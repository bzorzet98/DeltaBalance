"""
verify/deudas/verify_reestructura_deudas.py

Verifica la reestructuración de `deudas` al libro de movimientos
(db/schema_migrations.py reestructurar_deudas(), docs/DATA_MODEL_DECISIONS.md
sección 22), contra una DB temporal que arranca con la estructura VIEJA
(la de db/schema.sql) y datos cargados a mano.

Cubre:
  - La tabla nueva: monto_minor/fecha, sin monto_original_minor.
  - 1b: cada deuda vieja pasa con su monto ORIGINAL, su fecha_inicio y su
    mismo id; la vieja queda como deudas_old.
  - 1c: cada pago de deuda_pagos pasa como fila de tipo OPUESTO
    (origen_tipo='pago_migrado', origen_id = id del pago); un pago sin
    concepto queda "PAGO"; una fecha con hora se recorta a 'YYYY-MM-DD'.
  - La persona queda normalizada ("juan pérez " → "JUAN PÉREZ").
  - Saldos: una deuda con pago parcial da su pendiente, una saldada da 0,
    una incobrable se migra tal cual (vuelve a sumar su total — decisión
    explícita).
  - deuda_pagos sigue apuntando a sus deudas, ahora en deudas_old.
  - Backup automático del archivo antes de migrar (hay datos).
  - Correr inicializar() otra vez no hace nada (ni otro backup).
  - Una base nueva (sin datos) también se reestructura, sin backup.
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

PATRON_BACKUP = "deltabalance_backup_antes_deudas_*.db"


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

    def backups(carpeta: Path) -> set[Path]:
        return set(carpeta.glob(PATRON_BACKUP))

    # ============================================================
    # Base con la estructura VIEJA y datos (sin pasar por inicializar())
    # ============================================================
    db_path = crear_dummy_db()  # schema.sql solo: `deudas` con la estructura vieja
    print(f"Dummy DB creada en: {db_path}\n")
    crudo = sqlite3.connect(db_path)
    ars = crudo.execute("SELECT id FROM monedas WHERE codigo = 'ARS';").fetchone()[0]

    def deuda_vieja(persona, tipo, original, pendiente, fecha, estado) -> int:
        return crudo.execute(
            """
            INSERT INTO deudas (entidad_persona, tipo, monto_original_minor, monto_pendiente_minor,
                                moneda_id, fecha_inicio, estado, origen_tipo, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?);
            """,
            (persona, tipo, original, pendiente, ars, fecha, estado, f"nota de {persona}"),
        ).lastrowid

    def pago_viejo(deuda_id, monto, fecha, concepto) -> int:
        return crudo.execute(
            "INSERT INTO deuda_pagos (deuda_id, concepto, monto_applied_minor, tipo_pago, fecha) VALUES (?, ?, ?, 'transaccion', ?);",
            (deuda_id, concepto, monto, fecha),
        ).lastrowid

    noe = deuda_vieja("Noe", "a_favor", 40000, 30000, "2026-01-10", "activa")
    pago_noe = pago_viejo(noe, 10000, "2026-02-05 10:30:00", "Devolvió una parte")  # fecha CON hora
    kevin = deuda_vieja("Kevin", "en_contra", 5000, 0, "2026-01-20", "saldada")
    pago_kevin = pago_viejo(kevin, 5000, "2026-03-01", None)  # sin concepto
    juan = deuda_vieja("juan pérez ", "a_favor", 7000, 7000, "2026-02-01", "incobrable")
    crudo.commit()
    crudo.close()

    carpeta = Path(db_path).parent
    backups_antes = backups(carpeta)

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()  # columnas → tablas → reestructurar_deudas()

    # ============================================================
    print("--- Estructura ---")
    # ============================================================
    columnas = {f["name"] for f in manager.fetchall("PRAGMA table_info(deudas);")}
    caso("deudas tiene monto_minor y fecha", True, {"monto_minor", "fecha"} <= columnas)
    caso("deudas ya no tiene monto_original_minor ni estado", False, bool({"monto_original_minor", "estado"} & columnas))
    caso("la tabla vieja quedó como deudas_old, con sus 3 deudas", 3,
         manager.fetchone("SELECT COUNT(*) AS n FROM deudas_old;")["n"])
    caso("no queda deudas_v2", None,
         manager.fetchone("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'deudas_v2';"))
    destino_fk = {f["table"] for f in manager.fetchall("PRAGMA foreign_key_list(deuda_pagos);") if f["from"] == "deuda_id"}
    caso("deuda_pagos apunta a deudas_old", {"deudas_old"}, destino_fk)
    caso("se hizo UN backup antes de migrar (había datos)", 1, len(backups(carpeta) - backups_antes))

    # ============================================================
    print("\n--- 1b: deudas (monto original, fecha, mismo id) ---")
    # ============================================================
    fila_noe = manager.fetchone("SELECT * FROM deudas WHERE id = ?;", (noe,))
    caso("Noe conserva su id y su monto ORIGINAL (400.00)", 40000, fila_noe["monto_minor"] if fila_noe else None)
    caso("Noe: fecha = fecha_inicio", "2026-01-10", fila_noe["fecha"] if fila_noe else None)
    caso("Noe: notas conservadas", "nota de Noe", fila_noe["notas"] if fila_noe else None)
    caso("persona normalizada: 'Noe' → 'NOE'", "NOE", fila_noe["entidad_persona"] if fila_noe else None)
    fila_juan = manager.fetchone("SELECT * FROM deudas WHERE id = ?;", (juan,))
    caso("'juan pérez ' → 'JUAN PÉREZ' (con la É)", "JUAN PÉREZ", fila_juan["entidad_persona"] if fila_juan else None)

    # ============================================================
    print("\n--- 1c: pagos como filas de tipo opuesto ---")
    # ============================================================
    def fila_pago(pago_id: int):
        return manager.fetchone(
            "SELECT * FROM deudas WHERE origen_tipo = 'pago_migrado' AND origen_id = ?;", (pago_id,),
        )

    p_noe = fila_pago(pago_noe)
    caso("pago de Noe: tipo opuesto (en_contra)", "en_contra", p_noe["tipo"] if p_noe else None)
    caso("pago de Noe: monto 100.00", 10000, p_noe["monto_minor"] if p_noe else None)
    caso("pago de Noe: fecha recortada a AAAA-MM-DD", "2026-02-05", p_noe["fecha"] if p_noe else None)
    caso("pago de Noe: su concepto", "Devolvió una parte", p_noe["concepto"] if p_noe else None)
    p_kevin = fila_pago(pago_kevin)
    caso("pago de Kevin: tipo opuesto (a_favor)", "a_favor", p_kevin["tipo"] if p_kevin else None)
    caso("pago sin concepto → 'PAGO'", "PAGO", p_kevin["concepto"] if p_kevin else None)
    caso("total de filas: 3 deudas + 2 pagos", 5, manager.fetchone("SELECT COUNT(*) AS n FROM deudas;")["n"])

    # ============================================================
    print("\n--- Saldos (DebtsService.get_saldo_neto()) ---")
    # ============================================================
    svc = DebtsService(manager)
    saldos = {e["entidad_persona"]: e["saldo_minor"] for e in svc.get_saldo_neto()}
    caso("NOE: 400.00 − 100.00 pagados = su pendiente de antes", 30000, saldos.get("NOE"))
    caso("KEVIN: saldada → 0", 0, saldos.get("KEVIN"))
    caso("JUAN PÉREZ: incobrable migrada tal cual (vuelve a sumar su total)", 7000, saldos.get("JUAN PÉREZ"))

    # ============================================================
    print("\n--- Idempotencia ---")
    # ============================================================
    backups_antes_2 = backups(carpeta)
    manager.inicializar()
    caso("inicializar() otra vez: mismas 5 filas", 5, manager.fetchone("SELECT COUNT(*) AS n FROM deudas;")["n"])
    caso("… y ningún backup nuevo", 0, len(backups(carpeta) - backups_antes_2))

    nueva = svc.create("Noe", "Otra cena", "a_favor", 2500, ars, "2026-04-01")
    caso("DebtsService.create() sobre la tabla nueva", True, nueva.success)
    manager.desconectar()

    # ============================================================
    print("\n--- Base nueva (sin datos) ---")
    # ============================================================
    db_vacia = crear_dummy_db()
    backups_antes_3 = backups(Path(db_vacia).parent)
    manager_vacio = DatabaseManager(db_path=db_vacia)
    manager_vacio.inicializar()
    columnas_vacia = {f["name"] for f in manager_vacio.fetchall("PRAGMA table_info(deudas);")}
    caso("una base nueva también queda con la estructura nueva", True, "monto_minor" in columnas_vacia)
    caso("… sin backup (no había datos)", 0, len(backups(Path(db_vacia).parent) - backups_antes_3))
    manager_vacio.desconectar()

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print("(Los backups de prueba quedan en la carpeta temporal del sistema, junto a las dummy DB.)")


if __name__ == "__main__":
    main()
