"""
verify/schema/verify_user_version.py

Verifica el versionado del schema con PRAGMA user_version
(DatabaseManager.inicializar() + db/schema_migrations.py run_migrations(),
docs/DATA_MODEL_DECISIONS.md secciones 37, 38 y 39), contra DBs temporales —
nunca contra data/:

A. Base nueva (el archivo no existe): db/schema.sql + brokers + sync +
   seed, user_version = VERSION_SCHEMA (3), las 33 tablas, el seed anotado
   en sync_cambios (los brokers no), los triggers de sync (3 por tabla
   sincronizada) y ningún backup. Además: schema.sql solo (sin
   DatabaseManager) ya marca VERSION_SCHEMA.
B. Base existente en v0 (db/schema_v0.sql + db/seed_v0.sql + datos con la
   estructura vieja de presupuestos / ingresos_proyectados): UN backup ANTES
   de migrar, la cadena anterior convierte las tablas sin perder filas, la
   v2 borra las tablas sin uso, la v3 pasa a moneda_codigo y queda
   user_version = 3.
C. La foto y las migraciones (v0 → v1 → v2 → v3) dejan el MISMO schema: por cada
   tabla, mismas columnas en el mismo orden (nombre, tipo, NOT NULL,
   DEFAULT, PK) y mismas FKs; mismos índices, vistas y triggers. (Los CHECK
   no se comparan: PRAGMA no los expone.)
D. inicializar() otra vez sobre una base al día: ningún backup nuevo, sigue
   en la misma versión, run_migrations(conn, VERSION_SCHEMA) no hace nada.
E. Archivo creado vacío (como legacy_migration/migrar_a_uuid_pk.py
   crear_destino()): schema completo SIN seed, user_version = VERSION_SCHEMA.
F. Base con user_version mayor que VERSION_SCHEMA: inicializar() frena con
   RuntimeError y no toca la versión.
G. La marca va al final: una base v0 con ids enteros hace el backup, frena
   en la baseline (RuntimeError) y queda en user_version = 0.
H. v1 → v2: una base en v1 con una fila en cada tabla que se elimina (y un
   cambio de activo_objetivos pendiente en sync_cambios): backup ANTES de
   migrar (con las cuatro tablas y sus filas adentro), las cuatro tablas
   desaparecen, sync_cambios queda sin activo_objetivos, el resto de los
   datos sigue ahí, mismas tablas que una base nueva (sigue hasta v3).
I. v2 → v3: una base en v2 con datos en moneda_id (una transacción en USD,
   un saldo en USD, un movimiento sin moneda) y un cambio de cuentas_saldos
   pendiente en sync_cambios ('<cuenta>|2'): backup ANTES de migrar (en v2,
   con monedas.id), monedas con `codigo` de PK y sin id, ninguna tabla con
   moneda_id, cada fila con el código de su moneda (el NULL sigue NULL),
   la clave en sync_cambios pasa a '<cuenta>|USD', la vista
   vw_balance_cuentas da el saldo, el índice de transacciones está sobre
   moneda_codigo, el trigger de sync de cuentas_saldos anota la clave con
   el código, foreign_key_check vacío, mismas tablas que una base nueva.
J. v3 frena si una moneda tiene otro id que el que espera la sync
   (IDS_MONEDA_REMOTOS): RuntimeError, no se migra nada y queda en v2.
K. Sync (sync/sync_engine.py): la traducción codigo ↔ moneda_id en el
   borde — una fila sube con moneda_id, una bajada vuelve con
   moneda_codigo, y el uuid remoto de un saldo es el mismo que en v2.

Correlo con:
    python verify/schema/verify_user_version.py
"""

from pathlib import Path
import sqlite3
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import SCHEMA_PATH, SEED_V0_PATH
from db.database import DatabaseManager
from db.schema_migrations import (
    BROKERS_INICIALES,
    IDS_MONEDA_REMOTOS,
    MIGRACIONES_VERSIONADAS,
    SCHEMA_V0_PATH,
    TABLAS_ELIMINADAS_V2,
    TABLAS_SINCRONIZADAS,
    VERSION_SCHEMA,
    leer_version,
    marcar_version,
    run_migrations,
)

TABLAS_ESPERADAS = 33


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
    caso("ninguna de las tablas eliminadas en v2", [], [t for t in TABLAS_ELIMINADAS_V2 if t in tablas(nueva)])
    tablas_foto = tablas(nueva)
    solo_foto = sqlite3.connect(carpeta / "solo_schema.db")
    solo_foto.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    caso("schema.sql solo (sin DatabaseManager) ya marca user_version = VERSION_SCHEMA", VERSION_SCHEMA,
         leer_version(solo_foto))
    solo_foto.close()

    # ============================================================
    print("\n=== B. Base existente en v0 (baseline) ===\n")
    # ============================================================
    path_vieja = carpeta / "vieja.db"
    crudo = sqlite3.connect(path_vieja)
    crudo.executescript(SCHEMA_V0_PATH.read_text(encoding="utf-8"))
    crudo.executescript(SEED_V0_PATH.read_text(encoding="utf-8"))
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
    print("\n=== C. La foto (schema.sql) y las migraciones desde v0 dejan el mismo schema ===\n")
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
    print("\n=== D. inicializar() otra vez (ya al día) ===\n")
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

    # ============================================================
    print("\n=== H. v1 → v2: se eliminan las tablas sin uso ===\n")
    # ============================================================
    # Una base en v1: schema_v0.sql + seed_v0.sql + la baseline (la
    # migración 1 de verdad), marcada a mano en 1 — sin pasar por
    # inicializar(), que la llevaría directo a la última versión.
    path_v1 = carpeta / "v1.db"
    crudo = sqlite3.connect(path_v1)
    crudo.executescript(SCHEMA_V0_PATH.read_text(encoding="utf-8"))
    crudo.executescript(SEED_V0_PATH.read_text(encoding="utf-8"))
    MIGRACIONES_VERSIONADAS[1](crudo)
    marcar_version(crudo, 1)
    caso("la base de prueba arranca en user_version = 1", 1, leer_version(crudo))
    caso("… con las cuatro tablas que elimina la v2", list(TABLAS_ELIMINADAS_V2),
         [t for t in TABLAS_ELIMINADAS_V2 if t in tablas(crudo)])

    ars = crudo.execute("SELECT id FROM monedas WHERE codigo = 'ARS';").fetchone()[0]
    usd = crudo.execute("SELECT id FROM monedas WHERE codigo = 'USD';").fetchone()[0]
    caja = crudo.execute("SELECT id FROM cuentas WHERE nombre = 'Caja Efectivo';").fetchone()[0]
    resumen, activo, objetivo = (str(uuid.uuid4()) for _ in range(3))
    crudo.execute(
        "INSERT INTO tipos_cambio (fecha, moneda_origen_id, moneda_destino_id, tasa_minor) VALUES ('2026-09-01', ?, ?, 14000000);",
        (usd, ars),
    )
    crudo.execute("INSERT INTO indices_inflacion (mes, anio, valor_indice) VALUES (9, 2026, 100.0);")
    crudo.execute(
        "INSERT INTO resumenes_tarjeta (id, cuenta_id, mes, anio, monto_total_pagado_minor) VALUES (?, ?, 9, 2026, 0);",
        (resumen, caja),
    )
    crudo.execute(
        "INSERT INTO resumen_cargos_extra (resumen_id, concepto, tipo, monto_minor) VALUES (?, 'CARGO OTRO', 'otro', 100);",
        (resumen,),
    )
    crudo.execute("INSERT INTO activos_financieros (id, nombre, tipo, moneda_id) VALUES (?, 'FCI', 'fci', ?);", (activo, ars))
    crudo.execute("INSERT INTO objetivos_ahorro (id, nombre) VALUES (?, 'MOTO');", (objetivo,))
    crudo.execute(
        "INSERT INTO activo_objetivos (activo_id, objetivo_id, porcentaje) VALUES (?, ?, 50.0);", (activo, objetivo),
    )
    # Un cambio pendiente de subir, como lo dejaba el trigger de sync de v1.
    crudo.execute(
        "INSERT OR REPLACE INTO sync_cambios (tabla, clave, operacion, modificado_en) "
        "VALUES ('activo_objetivos', 'pendiente', 'guardado', '2026-09-01 10:00:00.000');",
    )
    crudo.commit()
    crudo.close()

    manager_v1 = DatabaseManager(db_path=path_v1)
    manager_v1.inicializar()
    v2 = manager_v1.conn
    caso("hizo UN backup antes de migrar", 1, len(backups(path_v1)))
    caso("… con el nombre de la versión de origen (v1)", True,
         all("_backup_antes_migracion_v1_" in p.name for p in backups(path_v1)))
    respaldo = next(iter(backups(path_v1)), None)
    if respaldo is not None:
        copia = sqlite3.connect(respaldo)
        caso("el backup tiene las cuatro tablas con su fila (v1, sin migrar)", ([1, 1, 1, 1], 1), (
            [contar(copia, f"SELECT COUNT(*) FROM {t};") for t in TABLAS_ELIMINADAS_V2], leer_version(copia),
        ))
        copia.close()
    caso("quedó en user_version = VERSION_SCHEMA (pasó por v2 y siguió)", VERSION_SCHEMA, leer_version(v2))
    caso("las cuatro tablas ya no existen", [], [t for t in TABLAS_ELIMINADAS_V2 if t in tablas(v2)])
    caso("… ni sus índices", [], [i for i in objetos(v2, "index") if any(t in i for t in TABLAS_ELIMINADAS_V2)])
    caso("… ni triggers de sync de activo_objetivos", [], [t for t in objetos(v2, "trigger") if "activo_objetivos" in t])
    caso("sync_cambios sin anotaciones de activo_objetivos", 0,
         contar(v2, "SELECT COUNT(*) FROM sync_cambios WHERE tabla = 'activo_objetivos';"))
    caso("el resto de los datos sigue ahí (resumen, activo, objetivo)", (1, 1, 1), (
        contar(v2, "SELECT COUNT(*) FROM resumenes_tarjeta WHERE id = ?;", (resumen,)),
        contar(v2, "SELECT COUNT(*) FROM activos_financieros WHERE id = ?;", (activo,)),
        contar(v2, "SELECT COUNT(*) FROM objetivos_ahorro WHERE id = ?;", (objetivo,)),
    ))
    caso("mismas tablas que una base nueva", tablas_foto, tablas(v2))
    caso("foreign_key_check vacío", [], [tuple(f) for f in v2.execute("PRAGMA foreign_key_check;")])
    manager_v1.desconectar()

    def base_en_v2(nombre: str) -> sqlite3.Connection:
        """schema_v0.sql + seed_v0.sql + las migraciones 1 y 2 de verdad, marcada en 2 — sin pasar por inicializar()."""
        conn = sqlite3.connect(carpeta / nombre)
        conn.executescript(SCHEMA_V0_PATH.read_text(encoding="utf-8"))
        conn.executescript(SEED_V0_PATH.read_text(encoding="utf-8"))
        MIGRACIONES_VERSIONADAS[1](conn)
        MIGRACIONES_VERSIONADAS[2](conn)
        marcar_version(conn, 2)
        return conn

    def columnas(conn, tabla: str) -> list[str]:
        return [f[1] for f in conn.execute(f"PRAGMA table_info({tabla});")]

    # ============================================================
    print("\n=== I. v2 → v3: monedas por código ===\n")
    # ============================================================
    path_v2 = carpeta / "v2.db"
    crudo = base_en_v2(path_v2.name)
    caso("la base de prueba arranca en user_version = 2, con monedas.id", (2, True),
         (leer_version(crudo), "id" in columnas(crudo, "monedas")))
    ids = {codigo: moneda_id for moneda_id, codigo in crudo.execute("SELECT id, codigo FROM monedas;")}
    caja = crudo.execute("SELECT id FROM cuentas WHERE nombre = 'Caja Efectivo';").fetchone()[0]
    categoria = crudo.execute("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;").fetchone()[0]
    transaccion, activo, movimiento = (str(uuid.uuid4()) for _ in range(3))
    # El trigger de sync (creado por la baseline) anota la clave con el id: '<caja>|2'.
    crudo.execute(
        "INSERT INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES (?, ?, 50000);", (caja, ids["USD"]),
    )
    crudo.execute(
        "INSERT INTO transacciones (id, fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor) "
        "VALUES (?, '2026-09-10', 'COMPRA EN USD', ?, ?, ?, 'egreso', 10000);",
        (transaccion, caja, categoria, ids["USD"]),
    )
    crudo.execute("INSERT INTO activos_financieros (id, nombre, tipo, moneda_id) VALUES (?, 'CEDEAR', 'cedear', ?);", (activo, ids["ARS"]))
    crudo.execute(
        "INSERT INTO movimientos_activo (id, activo_id, tipo, fecha, monto_total_minor, moneda_id) "
        "VALUES (?, ?, 'compra', '2026-09-11', 7000, NULL);",
        (movimiento, activo),
    )
    crudo.commit()
    clave_vieja, clave_nueva = f"{caja}|{ids['USD']}", f"{caja}|USD"
    caso("sync_cambios tiene el saldo con la clave vieja ('<caja>|2')", 1,
         contar(crudo, "SELECT COUNT(*) FROM sync_cambios WHERE tabla = 'cuentas_saldos' AND clave = ?;", (clave_vieja,)))
    crudo.close()

    manager_v2 = DatabaseManager(db_path=path_v2)
    manager_v2.inicializar()
    v3 = manager_v2.conn
    caso("hizo UN backup antes de migrar", 1, len(backups(path_v2)))
    caso("… con el nombre de la versión de origen (v2)", True,
         all("_backup_antes_migracion_v2_" in p.name for p in backups(path_v2)))
    respaldo = next(iter(backups(path_v2)), None)
    if respaldo is not None:
        copia = sqlite3.connect(respaldo)
        caso("el backup es la base en v2 (monedas con id, transacciones con moneda_id)", (2, True, True), (
            leer_version(copia), "id" in columnas(copia, "monedas"), "moneda_id" in columnas(copia, "transacciones"),
        ))
        copia.close()
    caso("quedó en user_version = 3", 3, leer_version(v3))
    caso("monedas: codigo, simbolo, decimales — sin id", ["codigo", "simbolo", "decimales"], columnas(v3, "monedas"))
    caso("… con `codigo` de PRIMARY KEY", ["codigo"],
         [f[1] for f in v3.execute("PRAGMA table_info(monedas);") if f[5]])
    caso("las 7 monedas siguen ahí", sorted(IDS_MONEDA_REMOTOS), sorted(f[0] for f in v3.execute("SELECT codigo FROM monedas;")))
    caso("ninguna tabla con moneda_id", [], [t for t in tablas(v3) if "moneda_id" in columnas(v3, t)])
    caso("la transacción quedó en USD (moneda_codigo), monto intacto", ("USD", 10000),
         tuple(v3.execute("SELECT moneda_codigo, monto_minor FROM transacciones WHERE id = ?;", (transaccion,)).fetchone()))
    caso("los saldos de Caja: ARS (seed) y USD (500.00)", [("ARS", 0), ("USD", 50000)], [
        tuple(f) for f in v3.execute(
            "SELECT moneda_codigo, saldo_inicial_minor FROM cuentas_saldos WHERE cuenta_id = ? ORDER BY moneda_codigo;", (caja,),
        )
    ])
    caso("el activo quedó en ARS y el movimiento sin moneda sigue sin moneda", ("ARS", None), (
        v3.execute("SELECT moneda_codigo FROM activos_financieros WHERE id = ?;", (activo,)).fetchone()[0],
        v3.execute("SELECT moneda_codigo FROM movimientos_activo WHERE id = ?;", (movimiento,)).fetchone()[0],
    ))
    caso("FK de transacciones.moneda_codigo → monedas(codigo)", [("monedas", "moneda_codigo", "codigo")],
         [tuple(f)[2:5] for f in v3.execute("PRAGMA foreign_key_list(transacciones);") if tuple(f)[2] == "monedas"])
    caso("sync_cambios: la clave del saldo pasó a '<caja>|USD'", (0, 1), (
        contar(v3, "SELECT COUNT(*) FROM sync_cambios WHERE tabla = 'cuentas_saldos' AND clave = ?;", (clave_vieja,)),
        contar(v3, "SELECT COUNT(*) FROM sync_cambios WHERE tabla = 'cuentas_saldos' AND clave = ?;", (clave_nueva,)),
    ))
    caso("vw_balance_cuentas: Caja en USD = 500.00 − 100.00", 40000, v3.execute(
        "SELECT saldo_minor FROM vw_balance_cuentas WHERE cuenta_id = ? AND moneda = 'USD';", (caja,),
    ).fetchone()[0])
    caso("idx_transacciones_moneda está sobre moneda_codigo", ["moneda_codigo"],
         [f[2] for f in v3.execute("PRAGMA index_info(idx_transacciones_moneda);")])
    v3.execute("DELETE FROM sync_cambios WHERE tabla = 'cuentas_saldos';")
    v3.execute("UPDATE cuentas_saldos SET saldo_inicial_minor = 60000 WHERE cuenta_id = ? AND moneda_codigo = 'USD';", (caja,))
    v3.commit()
    caso("el trigger de sync de cuentas_saldos anota la clave con el código", [clave_nueva],
         [f[0] for f in v3.execute("SELECT clave FROM sync_cambios WHERE tabla = 'cuentas_saldos';")])
    caso("mismas tablas que una base nueva", tablas_foto, tablas(v3))
    caso("foreign_key_check vacío", [], [tuple(f) for f in v3.execute("PRAGMA foreign_key_check;")])
    caso("PRAGMA foreign_keys sigue ON después de la migración", 1, v3.execute("PRAGMA foreign_keys;").fetchone()[0])
    manager_v2.desconectar()

    # ============================================================
    print("\n=== J. v3 frena si una moneda tiene otro id que el de la sync ===\n")
    # ============================================================
    path_mal = carpeta / "ids_distintos.db"
    crudo = base_en_v2(path_mal.name)
    crudo.execute("UPDATE monedas SET id = 99 WHERE codigo = 'BRL';")
    crudo.commit()
    crudo.close()
    manager_mal = DatabaseManager(db_path=path_mal)
    try:
        manager_mal.inicializar()
        caso("inicializar() frena", "RuntimeError", "no frenó")
    except RuntimeError as err:
        caso("inicializar() frena (RuntimeError que nombra la moneda)", True, "BRL" in str(err))
    caso("… queda en user_version = 2", 2, leer_version(manager_mal.conn))
    caso("… sin migrar nada (monedas con id, transacciones con moneda_id)", (True, True), (
        "id" in columnas(manager_mal.conn, "monedas"), "moneda_id" in columnas(manager_mal.conn, "transacciones"),
    ))
    manager_mal.desconectar()

    # ============================================================
    print("\n=== K. Sync: codigo ↔ moneda_id en el borde ===\n")
    # ============================================================
    try:
        from sync import sync_engine
    except ImportError as err:  # dependencias de sync/ (postgrest, keyring…) no instaladas
        print(f"(Se saltea: no se pudo importar sync/sync_engine.py — {err})")
    else:
        cuenta = str(uuid.uuid4())
        caso("uuid remoto de un saldo: el mismo que en v2 ('<cuenta>|2')",
             sync_engine.uuid_compuesto("cuentas_saldos", f"{cuenta}|2"),
             sync_engine.uuid_remoto("cuentas_saldos", f"{cuenta}|USD"))
        caso("al subir: moneda_codigo → moneda_id",
             {"id": "x", "moneda_id": 2},
             sync_engine._monedas_a_remoto({"id": "x", "moneda_codigo": "USD"}))
        caso("al subir: moneda_codigo NULL → moneda_id NULL",
             {"moneda_id": None}, sync_engine._monedas_a_remoto({"moneda_codigo": None}))
        bajada = sync_engine._normalizar_remota(
            "cuentas_saldos", {"datos": '{"cuenta_id": "%s", "moneda_id": 2, "saldo_inicial_minor": 5}' % cuenta},
        )
        caso("al bajar: moneda_id → moneda_codigo, y la clave local con el código",
             ({"cuenta_id": cuenta, "moneda_codigo": "USD", "saldo_inicial_minor": 5}, f"{cuenta}|USD"),
             (bajada["datos"], bajada["clave_local"]))
        caso("al bajar: un id desconocido queda sin traducir (guardar_fila() lo rechaza)",
             {"moneda_id": 42}, sync_engine._monedas_a_local({"moneda_id": 42}))

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(Las bases de prueba y sus backups quedan en {carpeta} — no es data/.)")


if __name__ == "__main__":
    main()
