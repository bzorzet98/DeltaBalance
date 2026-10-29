"""
verify/schema/verify_user_version.py

Verifica el versionado del schema con PRAGMA user_version
(DatabaseManager.inicializar() + db/schema_migrations.py run_migrations(),
docs/DATA_MODEL_DECISIONS.md sección 37), contra DBs temporales — nunca
contra data/:

A. Base nueva (el archivo no existe): db/schema.sql + brokers + sync +
   seed, user_version = 1, las 37 tablas, el seed anotado en sync_cambios
   (los brokers no), los 66 triggers de sync y ningún backup.
B. Base existente en v0 (db/schema_v0.sql + seed + datos con la estructura
   vieja de presupuestos / ingresos_proyectados): backup ANTES de migrar,
   la cadena anterior convierte las tablas sin perder filas y queda
   user_version = 1.
C. La foto y la baseline dejan el MISMO schema: por cada tabla, mismas
   columnas en el mismo orden (nombre, tipo, NOT NULL, DEFAULT, PK) y
   mismas FKs; mismos índices, vistas y triggers. (Los CHECK no se
   comparan: PRAGMA no los expone.)
D. inicializar() otra vez sobre una base en v1: ningún backup nuevo, sigue
   en v1, run_migrations(conn, 1) no hace nada.
E. Archivo creado vacío (como legacy_migration/migrar_a_uuid_pk.py
   crear_destino()): schema completo SIN seed, user_version = 1.
F. Base con user_version mayor que VERSION_SCHEMA: inicializar() frena con
   RuntimeError y no toca la versión.
G. La marca va al final: una base v0 con ids enteros hace el backup, frena
   en la baseline (RuntimeError) y queda en user_version = 0.

Correlo con:
    python verify/schema/verify_user_version.py
"""

from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import SEED_PATH
from db.database import DatabaseManager
from db.schema_migrations import (
    BROKERS_INICIALES,
    SCHEMA_V0_PATH,
    TABLAS_SINCRONIZADAS,
    VERSION_SCHEMA,
    leer_version,
    run_migrations,
)

TABLAS_ESPERADAS = 37


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

    def tablas(conn) -> list[str]:
        return sorted(
            f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';")
        )

    def objetos(conn, tipo: str) -> list[str]:
        return sorted(
            f[0] for f in conn.execute("SELECT name FROM sqlite_master WHERE type = ? AND name NOT LIKE 'sqlite_%';", (tipo,))
        )

    def backups(db_path: Path) -> set[Path]:
        return set(db_path.parent.glob(f"{db_path.stem}_backup_antes_migracion_v*.db"))

    def contar(conn, sql: str, params: tuple = ()) -> int:
        return conn.execute(sql, params).fetchone()[0]

    carpeta = Path(tempfile.mkdtemp(prefix="verify_user_version_"))
    print(f"Carpeta temporal: {carpeta}\n")

    # ============================================================
    print("=== A. Base nueva ===\n")
    # ============================================================
    path_nueva = carpeta / "nueva.db"
    manager_nueva = DatabaseManager(db_path=path_nueva)
    manager_nueva.inicializar()
    nueva = manager_nueva.conn
    caso("user_version = VERSION_SCHEMA", VERSION_SCHEMA, leer_version(nueva))
    caso(f"las {TABLAS_ESPERADAS} tablas", TABLAS_ESPERADAS, len(tablas(nueva)))
    caso("presupuestos nace con la estructura final (tipo, monto_real_minor)", True,
         {"tipo", "monto_real_minor"} <= {f[1] for f in nueva.execute("PRAGMA table_info(presupuestos);")})
    caso("seed: 'Caja Efectivo' existe", 1, contar(nueva, "SELECT COUNT(*) FROM cuentas WHERE nombre = 'Caja Efectivo';"))
    caso("brokers iniciales sembrados", len(BROKERS_INICIALES), contar(nueva, "SELECT COUNT(*) FROM brokers;"))
    caso("el seed quedó anotado en sync_cambios (categorías)", True,
         contar(nueva, "SELECT COUNT(*) FROM sync_cambios WHERE tabla = 'categorias';") > 0)
    caso("los brokers NO quedaron anotados en sync_cambios (como antes del versionado)", 0,
         contar(nueva, "SELECT COUNT(*) FROM sync_cambios WHERE tabla = 'brokers';"))
    caso("los triggers de sync (3 por tabla sincronizada)", 3 * len(TABLAS_SINCRONIZADAS),
         sum(1 for nombre in objetos(nueva, "trigger") if nombre.startswith("trg_sync_")))
    caso("una base nueva no hace backup", 0, len(backups(path_nueva)))
    caso("foreign_key_check vacío", [], [tuple(f) for f in nueva.execute("PRAGMA foreign_key_check;")])

    # ============================================================
    print("\n=== B. Base existente en v0 (baseline) ===\n")
    # ============================================================
    path_vieja = carpeta / "vieja.db"
    crudo = sqlite3.connect(path_vieja)
    crudo.executescript(SCHEMA_V0_PATH.read_text(encoding="utf-8"))
    crudo.executescript(SEED_PATH.read_text(encoding="utf-8"))
    ars = crudo.execute("SELECT id FROM monedas WHERE codigo = 'ARS';").fetchone()[0]
    categoria = crudo.execute("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;").fetchone()[0]
    crudo.execute(
        "INSERT INTO presupuestos (categoria_id, moneda_id, mes, anio, monto_estimado_minor) VALUES (?, ?, 9, 2026, 50000);",
        (categoria, ars),
    )
    crudo.execute(
        "INSERT INTO ingresos_proyectados (concepto, mes, anio, monto_estimado_minor, monto_percibido_minor, moneda_id) "
        "VALUES ('SUELDO', 9, 2026, 100000, 90000, ?);",
        (ars,),
    )
    crudo.commit()
    caso("la base de prueba arranca en user_version = 0", 0, leer_version(crudo))
    crudo.close()

    manager_vieja = DatabaseManager(db_path=path_vieja)
    manager_vieja.inicializar()
    vieja = manager_vieja.conn
    caso("hizo UN backup antes de migrar", 1, len(backups(path_vieja)))
    caso("… con el nombre de la versión de origen (v0)", True,
         all("_backup_antes_migracion_v0_" in p.name for p in backups(path_vieja)))
    respaldo = next(iter(backups(path_vieja)), None)
    if respaldo is not None:
        copia = sqlite3.connect(respaldo)
        caso("el backup es la base SIN migrar (presupuestos sin `tipo`, user_version 0)", (False, 0),
             ("tipo" in {f[1] for f in copia.execute("PRAGMA table_info(presupuestos);")}, leer_version(copia)))
        copia.close()
    caso("quedó en user_version = VERSION_SCHEMA", VERSION_SCHEMA, leer_version(vieja))
    caso(f"las {TABLAS_ESPERADAS} tablas", TABLAS_ESPERADAS, len(tablas(vieja)))
    presupuesto = vieja.execute("SELECT tipo, monto_estimado_minor FROM presupuestos;").fetchall()
    caso("el presupuesto viejo pasó como 'variable' con su estimado", [("variable", 50000)], [tuple(f) for f in presupuesto])
    ingreso = vieja.execute("SELECT concepto, monto_real_minor FROM ingresos_proyectados;").fetchall()
    caso("el ingreso viejo conserva lo percibido como monto_real_minor", [("SUELDO", 90000)], [tuple(f) for f in ingreso])
    caso("brokers iniciales sembrados", len(BROKERS_INICIALES), contar(vieja, "SELECT COUNT(*) FROM brokers;"))
    caso("foreign_key_check vacío", [], [tuple(f) for f in vieja.execute("PRAGMA foreign_key_check;")])

    # ============================================================
    print("\n=== C. La foto (schema.sql) y la baseline dejan el mismo schema ===\n")
    # ============================================================
    caso("mismas tablas", tablas(nueva), tablas(vieja))
    distintas = []
    for tabla in tablas(nueva):
        columnas_foto = [tuple(f)[1:6] for f in nueva.execute(f"PRAGMA table_info({tabla});")]
        columnas_baseline = [tuple(f)[1:6] for f in vieja.execute(f"PRAGMA table_info({tabla});")]
        fks_foto = sorted(tuple(f)[2:5] for f in nueva.execute(f"PRAGMA foreign_key_list({tabla});"))
        fks_baseline = sorted(tuple(f)[2:5] for f in vieja.execute(f"PRAGMA foreign_key_list({tabla});"))
        if columnas_foto != columnas_baseline or fks_foto != fks_baseline:
            distintas.append(tabla)
            print(f"   {tabla}:\n     foto:     {columnas_foto} {fks_foto}\n     baseline: {columnas_baseline} {fks_baseline}")
    caso("cada tabla: mismas columnas, en el mismo orden, y mismas FKs", [], distintas)
    caso("mismos índices", objetos(nueva, "index"), objetos(vieja, "index"))
    caso("mismas vistas", objetos(nueva, "view"), objetos(vieja, "view"))
    caso("mismos triggers", objetos(nueva, "trigger"), objetos(vieja, "trigger"))

    # ============================================================
    print("\n=== D. inicializar() otra vez (ya en v1) ===\n")
    # ============================================================
    backups_antes = backups(path_vieja)
    manager_vieja.inicializar()
    caso("ningún backup nuevo", 0, len(backups(path_vieja) - backups_antes))
    caso("sigue en user_version = VERSION_SCHEMA", VERSION_SCHEMA, leer_version(vieja))
    caso("run_migrations(conn, VERSION_SCHEMA) no hace nada", VERSION_SCHEMA, run_migrations(vieja, VERSION_SCHEMA))
    caso("… y los datos siguen ahí", 1, contar(vieja, "SELECT COUNT(*) FROM presupuestos;"))
    manager_vieja.desconectar()
    manager_nueva.desconectar()

    # ============================================================
    print("\n=== E. Archivo creado vacío (sin seed) ===\n")
    # ============================================================
    path_vacia = carpeta / "vacia.db"
    path_vacia.touch()
    manager_vacia = DatabaseManager(db_path=path_vacia)
    manager_vacia.inicializar()
    vacia = manager_vacia.conn
    caso("schema completo", TABLAS_ESPERADAS, len(tablas(vacia)))
    caso("sin seed (ninguna categoría)", 0, contar(vacia, "SELECT COUNT(*) FROM categorias;"))
    caso("user_version = VERSION_SCHEMA", VERSION_SCHEMA, leer_version(vacia))
    caso("sin backup", 0, len(backups(path_vacia)))
    manager_vacia.desconectar()

    # ============================================================
    print("\n=== F. Base de una versión más nueva ===\n")
    # ============================================================
    path_futura = carpeta / "futura.db"
    manager_futura = DatabaseManager(db_path=path_futura)
    manager_futura.inicializar()
    manager_futura.conn.execute(f"PRAGMA user_version = {VERSION_SCHEMA + 1};")
    try:
        manager_futura.inicializar()
        caso("inicializar() frena con una versión más nueva", "RuntimeError", "no frenó")
    except RuntimeError as err:
        caso("inicializar() frena con una versión más nueva (RuntimeError)", True, "Actualizá la app" in str(err))
    caso("… y no toca la versión", VERSION_SCHEMA + 1, leer_version(manager_futura.conn))
    manager_futura.desconectar()

    # ============================================================
    print("\n=== G. La marca va al final: una baseline que falla deja v0 ===\n")
    # ============================================================
    path_enteros = carpeta / "enteros.db"
    crudo = sqlite3.connect(path_enteros)
    crudo.execute("CREATE TABLE transacciones (id INTEGER PRIMARY KEY AUTOINCREMENT, concepto TEXT);")
    crudo.commit()
    crudo.close()
    manager_enteros = DatabaseManager(db_path=path_enteros)
    try:
        manager_enteros.inicializar()
        caso("una base con ids enteros frena la baseline", "RuntimeError", "no frenó")
    except RuntimeError as err:
        caso("una base con ids enteros frena la baseline (y dice qué correr)", True, "migrar_a_uuid_pk.py" in str(err))
    caso("el backup se hizo ANTES de intentar migrar", 1, len(backups(path_enteros)))
    caso("queda en user_version = 0 (sin marca)", 0, leer_version(manager_enteros.conn))
    manager_enteros.desconectar()

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(Las bases de prueba y sus backups quedan en {carpeta} — no es data/.)")


if __name__ == "__main__":
    main()
