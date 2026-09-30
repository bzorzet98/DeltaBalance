"""
verify/deudas/verify_reestructura_deudas.py

Verifica la reestructuración de `deudas` a su estructura FINAL — tabs
'me_deben' / 'debo' + monto_minor con signo — (db/schema_migrations.py
reestructurar_deudas(), docs/DATA_MODEL_DECISIONS.md sección 22), contra
DBs temporales que arrancan con cada una de las dos estructuras anteriores
y datos cargados a mano (sqlite3 crudo, sin pasar por inicializar()).

Cubre:
  - Estructura ORIGINAL (monto_original_minor/estado + deuda_pagos):
      cada deuda va a su tab (a_favor → me_deben, en_contra → debo) con su
      monto ORIGINAL en positivo, su fecha_inicio y su mismo id; el
      vencimiento pasa a las notas; cada pago va al tab de su deuda en
      NEGATIVO (origen_tipo='pago_migrado', origen_id = id del pago; sin
      concepto → "PAGO"; fecha con hora → 'YYYY-MM-DD'); la persona queda
      normalizada ("juan pérez " → "JUAN PÉREZ"); saldos: pago parcial =
      su pendiente, saldada = 0, incobrable tal cual (su total).
  - Estructura LIBRO (tipo + monto_minor siempre positivo, con deudas_old):
      los pagos migrados van al tab de su deuda en negativo; las filas del
      Excel (notas "MIGRADO DESDE EXCEL — TABLA DEUDAS…") van a me_deben,
      en_contra en negativo; el resto por su tipo, en positivo; se
      conservan id y sincronizado_en. Los saldos de cada persona no cambian.
  - En las dos: desaparecen deuda_pagos, deudas_old (y deudas_old_2),
      deudas_final, la vista vw_deudas_activas, el índice idx_deudas_estado
      y el trigger trg_deudas_updated; backup automático antes (había
      datos); deudas_mensuales se recrea con `tab`, vacía.
  - Correr inicializar() otra vez no hace nada (ni otro backup).
  - Una base nueva (schema.sql) ya nace con la estructura final, sin backup.
  - DebtsService funciona sobre la tabla nueva.

Correlo con:
    python verify/deudas/verify_reestructura_deudas.py
"""

from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.debts_service import DebtsService

# --- Estructuras anteriores (copiadas de las versiones viejas de db/schema.sql y db/schema_migrations.py) ---

DDL_ORIGINAL = """
CREATE TABLE {tabla} (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    entidad_persona       TEXT NOT NULL,
    tipo                  TEXT NOT NULL CHECK(tipo IN ('a_favor', 'en_contra')),
    monto_original_minor  INTEGER NOT NULL,
    monto_pendiente_minor INTEGER NOT NULL,
    moneda_id             INTEGER NOT NULL,
    fecha_inicio          TEXT NOT NULL CHECK(fecha_inicio GLOB '????-??-??'),
    fecha_vencimiento     TEXT CHECK(fecha_vencimiento IS NULL OR fecha_vencimiento GLOB '????-??-??'),
    estado                TEXT NOT NULL DEFAULT 'activa' CHECK(estado IN ('activa', 'saldada', 'incobrable')),
    origen_tipo           TEXT DEFAULT 'manual',
    origen_id             INTEGER,
    notas                 TEXT,
    creada_en             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (moneda_id) REFERENCES monedas(id)
);
"""

DDL_DEUDA_PAGOS = """
CREATE TABLE deuda_pagos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    deuda_id            INTEGER NOT NULL,
    transaccion_id      INTEGER,
    concepto            TEXT,
    monto_applied_minor INTEGER NOT NULL,
    tipo_pago           TEXT NOT NULL DEFAULT 'transaccion',
    notas               TEXT,
    fecha               TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (deuda_id) REFERENCES {tabla}(id),
    FOREIGN KEY (transaccion_id) REFERENCES transacciones(id)
);
CREATE INDEX idx_deuda_pagos_deuda ON deuda_pagos(deuda_id);
"""

# Índice, trigger y vista viejos, colgados de la tabla original (en LIBRO,
# el RENAME a deudas_old se los había llevado a ella).
DDL_OBJETOS_VIEJOS = """
CREATE INDEX idx_deudas_estado ON {tabla}(estado);
CREATE TRIGGER trg_deudas_updated AFTER UPDATE ON {tabla}
BEGIN
    UPDATE {tabla} SET updated_en = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
CREATE VIEW vw_deudas_activas AS
SELECT d.id, d.entidad_persona, d.tipo, d.monto_pendiente_minor, m.codigo AS moneda, d.estado
FROM {tabla} d JOIN monedas m ON m.id = d.moneda_id
WHERE d.estado = 'activa';
"""

DDL_LIBRO = """
CREATE TABLE deudas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entidad_persona TEXT NOT NULL,
    concepto        TEXT,
    tipo            TEXT NOT NULL CHECK(tipo IN ('a_favor', 'en_contra')),
    monto_minor     INTEGER NOT NULL,
    moneda_id       INTEGER NOT NULL REFERENCES monedas(id),
    fecha           TEXT NOT NULL CHECK(fecha GLOB '????-??-??'),
    notas           TEXT,
    origen_tipo     TEXT DEFAULT 'manual',
    origen_id       INTEGER,
    sincronizado_en TEXT DEFAULT NULL,
    creada_en       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# deudas_mensuales antes de tener `tab`.
DDL_DEUDAS_MENSUALES_SIN_TAB = """
CREATE TABLE deudas_mensuales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entidad_persona TEXT NOT NULL,
    moneda_id       INTEGER NOT NULL REFERENCES monedas(id),
    mes             INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio            INTEGER NOT NULL,
    monto_minor     INTEGER NOT NULL DEFAULT 0,
    calculado_en    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entidad_persona, moneda_id, mes, anio)
);
"""

NOTA_EXCEL = "MIGRADO DESDE EXCEL — TABLA DEUDAS, FILA {n}"


def main() -> None:
    casos_ok = 0
    casos_total = 0

    def caso(descripcion: str, esperado, obtenido) -> None:
        nonlocal casos_ok, casos_total
        casos_total += 1
        if esperado == obtenido:
            casos_ok += 1
            print(f"✅ {descripcion} — esperado: {esperado!r}, obtenido: {obtenido!r}")
        else:
            print(f"❌ {descripcion} — esperado: {esperado!r}, obtenido: {obtenido!r}")

    def backups(db_path: Path) -> set[Path]:
        return set(db_path.parent.glob(f"{db_path.stem}_backup_antes_deudas_*.db"))

    def objeto(manager: DatabaseManager, tipo: str, nombre: str) -> bool:
        return manager.fetchone("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ?;", (tipo, nombre)) is not None

    def saldos(svc: DebtsService, tab: str) -> dict[str, int]:
        return {e["entidad_persona"]: e["saldo_minor"] for e in svc.summary_by_person(tab, "2026-12-31")}

    def chequear_limpieza(manager: DatabaseManager, db_path: Path, backups_antes: set[Path]) -> None:
        columnas = {f["name"] for f in manager.fetchall("PRAGMA table_info(deudas);")}
        caso("deudas tiene tab, monto_minor y fecha", True, {"tab", "monto_minor", "fecha"} <= columnas)
        caso("… y ya no tiene tipo, monto_original_minor, estado ni updated_en", set(),
             {"tipo", "monto_original_minor", "estado", "updated_en"} & columnas)
        for tipo, nombre in (
            ("table", "deuda_pagos"), ("table", "deudas_old"), ("table", "deudas_old_2"), ("table", "deudas_final"),
            ("view", "vw_deudas_activas"), ("index", "idx_deudas_estado"), ("trigger", "trg_deudas_updated"),
        ):
            caso(f"ya no existe {nombre} ({tipo})", False, objeto(manager, tipo, nombre))
        caso("se hizo UN backup antes de migrar (había datos)", 1, len(backups(db_path) - backups_antes))
        columnas_snap = {f["name"] for f in manager.fetchall("PRAGMA table_info(deudas_mensuales);")}
        caso("deudas_mensuales se recreó con `tab`", True, "tab" in columnas_snap)
        caso("… vacía (es un caché: se recalcula con ↻)", 0,
             manager.fetchone("SELECT COUNT(*) AS n FROM deudas_mensuales;")["n"])

    # ============================================================
    # 1) Estructura ORIGINAL
    # ============================================================
    print("=== Estructura ORIGINAL (monto_original_minor + deuda_pagos) ===\n")
    db_path = Path(crear_dummy_db())  # schema.sql + seed (ya con la estructura final): se la pisa a mano
    print(f"Dummy DB creada en: {db_path}\n")
    crudo = sqlite3.connect(db_path)
    ars = crudo.execute("SELECT id FROM monedas WHERE codigo = 'ARS';").fetchone()[0]
    crudo.execute("DROP TABLE deudas;")
    crudo.execute(DDL_ORIGINAL.format(tabla="deudas"))
    crudo.executescript(DDL_DEUDA_PAGOS.format(tabla="deudas") + DDL_OBJETOS_VIEJOS.format(tabla="deudas")
                        + DDL_DEUDAS_MENSUALES_SIN_TAB)
    crudo.execute(
        "INSERT INTO deudas_mensuales (entidad_persona, moneda_id, mes, anio, monto_minor) VALUES ('NOE', ?, 1, 2026, 40000);",
        (ars,),
    )

    def deuda_vieja(persona, tipo, original, pendiente, fecha, estado, vencimiento=None) -> int:
        return crudo.execute(
            """
            INSERT INTO deudas (entidad_persona, tipo, monto_original_minor, monto_pendiente_minor,
                                moneda_id, fecha_inicio, fecha_vencimiento, estado, origen_tipo, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?);
            """,
            (persona, tipo, original, pendiente, ars, fecha, vencimiento, estado, f"nota de {persona}"),
        ).lastrowid

    def pago_viejo(deuda_id, monto, fecha, concepto) -> int:
        return crudo.execute(
            "INSERT INTO deuda_pagos (deuda_id, concepto, monto_applied_minor, tipo_pago, fecha) VALUES (?, ?, ?, 'transaccion', ?);",
            (deuda_id, concepto, monto, fecha),
        ).lastrowid

    noe = deuda_vieja("Noe", "a_favor", 40000, 30000, "2026-01-10", "activa", vencimiento="2026-06-30")
    pago_noe = pago_viejo(noe, 10000, "2026-02-05 10:30:00", "Devolvió una parte")  # fecha CON hora
    kevin = deuda_vieja("Kevin", "en_contra", 5000, 0, "2026-01-20", "saldada")
    pago_kevin = pago_viejo(kevin, 5000, "2026-03-01", None)  # sin concepto
    juan = deuda_vieja("juan pérez ", "a_favor", 7000, 7000, "2026-02-01", "incobrable")
    crudo.commit()
    crudo.close()

    backups_antes = backups(db_path)
    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()  # columnas → tablas → reestructurar_deudas()

    print("--- Estructura y limpieza ---")
    chequear_limpieza(manager, db_path, backups_antes)

    print("\n--- Deudas: su tab, monto original en positivo, mismo id ---")
    fila_noe = manager.fetchone("SELECT * FROM deudas WHERE id = ?;", (noe,))
    caso("Noe conserva su id → tab me_deben (era a_favor)", "me_deben", fila_noe["tab"] if fila_noe else None)
    caso("Noe: monto ORIGINAL, positivo (400.00)", 40000, fila_noe["monto_minor"] if fila_noe else None)
    caso("Noe: fecha = fecha_inicio", "2026-01-10", fila_noe["fecha"] if fila_noe else None)
    caso("Noe: el vencimiento pasa a las notas", "nota de Noe — VENCE: 2026-06-30", fila_noe["notas"] if fila_noe else None)
    caso("persona normalizada: 'Noe' → 'NOE'", "NOE", fila_noe["entidad_persona"] if fila_noe else None)
    fila_kevin = manager.fetchone("SELECT * FROM deudas WHERE id = ?;", (kevin,))
    caso("Kevin → tab debo (era en_contra), en positivo", ("debo", 5000),
         (fila_kevin["tab"], fila_kevin["monto_minor"]) if fila_kevin else None)
    fila_juan = manager.fetchone("SELECT * FROM deudas WHERE id = ?;", (juan,))
    caso("'juan pérez ' → 'JUAN PÉREZ' (con la É)", "JUAN PÉREZ", fila_juan["entidad_persona"] if fila_juan else None)

    print("\n--- Pagos: el tab de su deuda, en negativo ---")

    def fila_pago(pago_id: int):
        return manager.fetchone("SELECT * FROM deudas WHERE origen_tipo = 'pago_migrado' AND origen_id = ?;", (pago_id,))

    p_noe = fila_pago(pago_noe)
    caso("pago de Noe: tab me_deben, −100.00", ("me_deben", -10000), (p_noe["tab"], p_noe["monto_minor"]) if p_noe else None)
    caso("pago de Noe: fecha recortada a AAAA-MM-DD", "2026-02-05", p_noe["fecha"] if p_noe else None)
    caso("pago de Noe: su concepto", "Devolvió una parte", p_noe["concepto"] if p_noe else None)
    p_kevin = fila_pago(pago_kevin)
    caso("pago a Kevin: tab debo, −50.00", ("debo", -5000), (p_kevin["tab"], p_kevin["monto_minor"]) if p_kevin else None)
    caso("pago sin concepto → 'PAGO'", "PAGO", p_kevin["concepto"] if p_kevin else None)
    caso("total de filas: 3 deudas + 2 pagos", 5, manager.fetchone("SELECT COUNT(*) AS n FROM deudas;")["n"])

    print("\n--- Saldos (DebtsService.summary_by_person()) ---")
    svc = DebtsService(manager)
    me_deben, debo = saldos(svc, "me_deben"), saldos(svc, "debo")
    caso("NOE: 400.00 − 100.00 pagados = su pendiente de antes", 30000, me_deben.get("NOE"))
    caso("JUAN PÉREZ: incobrable migrada tal cual (vuelve a sumar su total)", 7000, me_deben.get("JUAN PÉREZ"))
    caso("KEVIN (DEBO): saldada → 0", 0, debo.get("KEVIN"))

    print("\n--- Idempotencia ---")
    backups_antes_2 = backups(db_path)
    manager.inicializar()
    caso("inicializar() otra vez: mismas 5 filas", 5, manager.fetchone("SELECT COUNT(*) AS n FROM deudas;")["n"])
    caso("… y ningún backup nuevo", 0, len(backups(db_path) - backups_antes_2))
    nueva = svc.create("Noe", "Otra cena", "me_deben", 2500, ars, "2026-04-01")
    caso("DebtsService.create() sobre la tabla nueva", True, nueva.success)
    manager.desconectar()

    # ============================================================
    # 2) Estructura LIBRO (reestructuración anterior)
    # ============================================================
    print("\n=== Estructura LIBRO (tipo + monto_minor positivo, con deudas_old) ===\n")
    db_libro = Path(crear_dummy_db())
    print(f"Dummy DB creada en: {db_libro}\n")
    crudo = sqlite3.connect(db_libro)
    ars = crudo.execute("SELECT id FROM monedas WHERE codigo = 'ARS';").fetchone()[0]
    crudo.execute("DROP TABLE deudas;")
    crudo.execute(DDL_LIBRO)
    # Lo que dejó la reestructuración anterior: la tabla original como
    # deudas_old (con su índice, trigger y vista) y deuda_pagos apuntándole.
    crudo.execute(DDL_ORIGINAL.format(tabla="deudas_old"))
    crudo.execute(DDL_ORIGINAL.format(tabla="deudas_old_2"))
    crudo.executescript(DDL_DEUDA_PAGOS.format(tabla="deudas_old") + DDL_OBJETOS_VIEJOS.format(tabla="deudas_old")
                        + DDL_DEUDAS_MENSUALES_SIN_TAB)
    vieja = crudo.execute(
        """
        INSERT INTO deudas_old (entidad_persona, tipo, monto_original_minor, monto_pendiente_minor, moneda_id, fecha_inicio)
        VALUES ('Noe', 'a_favor', 40000, 30000, ?, '2026-01-10');
        """,
        (ars,),
    ).lastrowid
    crudo.execute(
        "INSERT INTO deuda_pagos (deuda_id, monto_applied_minor, fecha) VALUES (?, 10000, '2026-02-05');", (vieja,),
    )

    def fila_libro(persona, tipo, monto, fecha, origen_tipo="manual", origen_id=None, notas=None, sincronizado=None) -> int:
        return crudo.execute(
            """
            INSERT INTO deudas (entidad_persona, concepto, tipo, monto_minor, moneda_id, fecha, notas,
                                origen_tipo, origen_id, sincronizado_en)
            VALUES (?, 'x', ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (persona, tipo, monto, ars, fecha, notas, origen_tipo, origen_id, sincronizado),
        ).lastrowid

    l_noe = fila_libro("NOE", "a_favor", 40000, "2026-01-10", sincronizado="2026-05-01 10:00:00")
    l_pago_noe = fila_libro("NOE", "en_contra", 10000, "2026-02-05", origen_tipo="pago_migrado", origen_id=1)
    l_kevin = fila_libro("KEVIN", "en_contra", 5000, "2026-01-20")
    l_pago_kevin = fila_libro("KEVIN", "a_favor", 5000, "2026-03-01", origen_tipo="pago_migrado", origen_id=2)
    l_maria = fila_libro("MARIA", "a_favor", 8000, "2026-02-01", notas=NOTA_EXCEL.format(n=2))
    l_maria_pago = fila_libro("MARIA", "en_contra", 3000, "2026-02-10", notas=NOTA_EXCEL.format(n=3))
    l_pedro = fila_libro("PEDRO", "en_contra", 2000, "2026-02-15", origen_tipo="transaccion", origen_id=77)
    crudo.commit()
    crudo.close()

    backups_antes = backups(db_libro)
    manager = DatabaseManager(db_path=db_libro)
    manager.inicializar()

    print("--- Estructura y limpieza ---")
    chequear_limpieza(manager, db_libro, backups_antes)

    print("\n--- Filas: tab y signo según su origen ---")

    def tab_y_monto(fila_id: int):
        fila = manager.fetchone("SELECT tab, monto_minor FROM deudas WHERE id = ?;", (fila_id,))
        return (fila["tab"], fila["monto_minor"]) if fila else None

    caso("NOE a_favor (manual) → me_deben +400.00, mismo id", ("me_deben", 40000), tab_y_monto(l_noe))
    caso("… conserva sincronizado_en", "2026-05-01 10:00:00",
         manager.fetchone("SELECT sincronizado_en FROM deudas WHERE id = ?;", (l_noe,))["sincronizado_en"])
    caso("pago migrado de NOE (en_contra) → me_deben −100.00", ("me_deben", -10000), tab_y_monto(l_pago_noe))
    caso("KEVIN en_contra (manual) → debo +50.00", ("debo", 5000), tab_y_monto(l_kevin))
    caso("pago migrado a KEVIN (a_favor) → debo −50.00", ("debo", -5000), tab_y_monto(l_pago_kevin))
    caso("MARIA del Excel, a_favor → me_deben +80.00", ("me_deben", 8000), tab_y_monto(l_maria))
    caso("MARIA del Excel, en_contra (un pago que te hizo) → me_deben −30.00", ("me_deben", -3000), tab_y_monto(l_maria_pago))
    caso("PEDRO en_contra desde el Registro → debo +20.00", ("debo", 2000), tab_y_monto(l_pedro))
    caso("total de filas: las 7 (deudas_old y sus pagos no se vuelven a migrar)", 7,
         manager.fetchone("SELECT COUNT(*) AS n FROM deudas;")["n"])

    print("\n--- Saldos: los mismos que antes de migrar ---")
    svc = DebtsService(manager)
    me_deben, debo = saldos(svc, "me_deben"), saldos(svc, "debo")
    caso("ME DEBEN — NOE: 400.00 − 100.00", 30000, me_deben.get("NOE"))
    caso("ME DEBEN — MARIA: 80.00 − 30.00", 5000, me_deben.get("MARIA"))
    caso("DEBO — KEVIN: 50.00 − 50.00", 0, debo.get("KEVIN"))
    caso("DEBO — PEDRO: 20.00", 2000, debo.get("PEDRO"))

    backups_antes_2 = backups(db_libro)
    manager.inicializar()
    caso("inicializar() otra vez: mismas 7 filas y ningún backup nuevo", (7, 0),
         (manager.fetchone("SELECT COUNT(*) AS n FROM deudas;")["n"], len(backups(db_libro) - backups_antes_2)))
    manager.desconectar()

    # ============================================================
    # 3) Base nueva
    # ============================================================
    print("\n=== Base nueva (schema.sql) ===\n")
    db_vacia = Path(crear_dummy_db())
    backups_antes_3 = backups(db_vacia)
    manager_vacio = DatabaseManager(db_path=db_vacia)
    manager_vacio.inicializar()
    columnas_vacia = {f["name"] for f in manager_vacio.fetchall("PRAGMA table_info(deudas);")}
    caso("una base nueva nace con la estructura final", True, {"tab", "monto_minor"} <= columnas_vacia)
    caso("… sin deuda_pagos", False, objeto(manager_vacio, "table", "deuda_pagos"))
    caso("… y sin backup (no había nada que migrar)", 0, len(backups(db_vacia) - backups_antes_3))
    manager_vacio.desconectar()

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print("(Los backups de prueba quedan en la carpeta temporal del sistema, junto a las dummy DB.)")


if __name__ == "__main__":
    main()
