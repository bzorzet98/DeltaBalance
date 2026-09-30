"""
DeltaBalance — db/schema_migrations.py

A partir de ahora, toda columna nueva sobre una tabla EXISTENTE se agrega acá,
nunca como `ALTER TABLE ... ADD COLUMN` suelto en db/schema.sql.

Por qué: SQLite no soporta `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (a
diferencia de CREATE TABLE/INDEX/TRIGGER/VIEW, que sí soportan IF NOT
EXISTS). db/schema.sql se reaplica completo en cada
DatabaseManager.inicializar(), no solo la primera vez — así que un ALTER
TABLE suelto ahí rompería con "duplicate column name" apenas se corriera una
segunda vez contra una base que ya tiene esa columna. Este módulo resuelve
eso: antes de alterar, consulta PRAGMA table_info y solo agrega la columna si
todavía no está.

Las tablas NUEVAS siguen yendo en db/schema.sql como siempre, con
`CREATE TABLE IF NOT EXISTS` — eso ya es idempotente de por sí y no necesita
pasar por acá. Excepción (pedido explícito, tarea "Snapshots mensuales de
saldos"): las tablas de snapshots de cierre de mes (saldos_mensuales,
deudas_mensuales, compartidos_mensuales) viven en MIGRACIONES_TABLA, más
abajo — son tablas derivadas (caché recalculable de datos que ya están en
otras tablas) que se agregan sobre bases existentes, y se aplican con
aplicar_migraciones_tabla() justo después de las migraciones de columna.
También son `CREATE TABLE IF NOT EXISTS`, así que reaplicarlas en cada
inicializar() no hace nada si ya existen. Diseño y cálculo: ver
services/snapshots_service.py y docs/DATA_MODEL_DECISIONS.md sección 21.
Mismo lugar, por el mismo pedido de no tocar db/schema.sql: tarjetas_config
(días de cierre y vencimiento por tarjeta — sección 23).

`MigracionColumna.sql_backfill` (opcional, agregado para la columna
`gastos_compartidos.monto_pendiente_minor`): un UPDATE que se ejecuta una
sola vez, inmediatamente después del ALTER TABLE que agrega la columna —
nunca en corridas donde la columna ya existía. Sirve para columnas nuevas
que no pueden arrancar con un valor por defecto constante (ej. "igual al
valor de otra columna ya existente en cada fila"), a diferencia de las
columnas anteriores de esta lista, que sí se conforman con el DEFAULT del
propio ALTER TABLE.

Reestructuración de `deudas` (más abajo, reestructurar_deudas()): convierte
una base con alguna estructura anterior de la tabla a la estructura final
de db/schema.sql. Corre al final de aplicar_migraciones_tabla() y no hace
nada si la tabla ya es la final. Ver docs/DATA_MODEL_DECISIONS.md sección 22.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from utils.personas import normalizar_persona


@dataclass(frozen=True)
class MigracionColumna:
    tabla: str
    columna: str
    ddl_columna: str
    sql_backfill: str | None = None


MIGRACIONES_COLUMNA: list[MigracionColumna] = [
    MigracionColumna(
        tabla="compras_cuotas",
        columna="monto_reintegro_minor",
        ddl_columna="monto_reintegro_minor INTEGER DEFAULT 0",
    ),
    MigracionColumna(
        tabla="compras_cuotas",
        columna="modo_deuda",
        ddl_columna=(
            "modo_deuda TEXT DEFAULT 'prorrateado' "
            "CHECK(modo_deuda IN ('total_unico', 'prorrateado'))"
        ),
    ),
    MigracionColumna(
        tabla="categorias",
        columna="activa",
        ddl_columna="activa INTEGER NOT NULL DEFAULT 1",
    ),
    MigracionColumna(
        # Solo para bases muy viejas (estructura original de `deudas` sin
        # concepto): la estructura final ya la trae, así que ahí se saltea.
        tabla="deudas",
        columna="concepto",
        ddl_columna="concepto TEXT",
    ),
    MigracionColumna(
        tabla="cuentas",
        columna="color_hex",
        ddl_columna="color_hex TEXT DEFAULT '#5F5E5A'",
    ),
    MigracionColumna(
        tabla="movimientos_activo",
        columna="transaccion_id",
        ddl_columna="transaccion_id INTEGER REFERENCES transacciones(id)",
    ),
    MigracionColumna(
        tabla="presupuestos",
        columna="formula_estimado",
        ddl_columna="formula_estimado TEXT",
    ),
    MigracionColumna(
        tabla="activos_financieros",
        columna="cuenta_id",
        ddl_columna="cuenta_id INTEGER REFERENCES cuentas(id)",
    ),
    MigracionColumna(
        tabla="gastos_compartidos",
        columna="monto_pendiente_minor",
        # DEFAULT 0 solo para que el ALTER TABLE sea válido con NOT NULL
        # (SQLite lo exige) — el valor real para cada fila lo pone
        # sql_backfill inmediatamente después, así que el default nunca
        # queda "pegado" en una fila existente.
        ddl_columna="monto_pendiente_minor INTEGER NOT NULL DEFAULT 0",
        # Al recién agregarse la columna, todo gasto compartido ya
        # existente arranca con su pendiente igual al adeudado completo
        # (mismo signo) — nada se había pagado todavía, porque el
        # mecanismo de pago parcial no existía antes de esta migración.
        sql_backfill="UPDATE gastos_compartidos SET monto_pendiente_minor = monto_adeudado_minor;",
    ),
]


# Snapshot de deudas: uno por (persona, tab, moneda) — el saldo de 'me_deben'
# y el de 'debo' con la misma persona van por separado (reestructuración
# final de deudas, docs/DATA_MODEL_DECISIONS.md sección 22). Una base que
# ya tenía la tabla sin `tab` la recrea _deudas_mensuales_con_tab().
DDL_DEUDAS_MENSUALES = """
    CREATE TABLE IF NOT EXISTS deudas_mensuales (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad_persona TEXT NOT NULL,
        tab             TEXT NOT NULL CHECK(tab IN ('me_deben', 'debo')),
        moneda_id       INTEGER NOT NULL REFERENCES monedas(id),
        mes             INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
        anio            INTEGER NOT NULL,
        monto_minor     INTEGER NOT NULL DEFAULT 0,
        calculado_en    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(entidad_persona, tab, moneda_id, mes, anio)
    );
"""

# Tablas nuevas sobre bases existentes (ver docstring del módulo). Snapshots
# de cierre de mes: una fila por (clave, mes, anio); el mes en curso nunca
# se guarda (siempre se calcula en vivo). UNIQUE sobre la clave + período:
# los repositorios hacen el upsert con INSERT OR REPLACE.
MIGRACIONES_TABLA: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS saldos_mensuales (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        cuenta_id    INTEGER NOT NULL REFERENCES cuentas(id),
        moneda_id    INTEGER NOT NULL REFERENCES monedas(id),
        mes          INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
        anio         INTEGER NOT NULL,
        saldo_minor  INTEGER NOT NULL DEFAULT 0,
        calculado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(cuenta_id, moneda_id, mes, anio)
    );
    """,
    DDL_DEUDAS_MENSUALES,
    """
    CREATE TABLE IF NOT EXISTS compartidos_mensuales (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        hogar_id     INTEGER NOT NULL REFERENCES hogares(id),
        pagador      TEXT NOT NULL,
        moneda_id    INTEGER NOT NULL REFERENCES monedas(id),
        mes          INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
        anio         INTEGER NOT NULL,
        monto_minor  INTEGER NOT NULL DEFAULT 0,
        calculado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(hogar_id, pagador, moneda_id, mes, anio)
    );
    """,
    # Días de cierre y vencimiento de cada tarjeta de crédito (pedido
    # explícito, tarea "Mejoras en Compras y Movimientos en Cuotas"): con
    # ellos FeesService calcula las fechas de los resúmenes y sugiere el
    # mes de la 1ª cuota. Una fila por tarjeta (UNIQUE cuenta_id);
    # updated_en lo escribe el repositorio en cada upsert (sin trigger).
    # Ver docs/DATA_MODEL_DECISIONS.md sección 23.
    """
    CREATE TABLE IF NOT EXISTS tarjetas_config (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        cuenta_id           INTEGER NOT NULL REFERENCES cuentas(id) UNIQUE,
        dia_cierre          INTEGER NOT NULL CHECK(dia_cierre BETWEEN 1 AND 31),
        dia_vencimiento     INTEGER NOT NULL CHECK(dia_vencimiento BETWEEN 1 AND 31),
        creada_en           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_en          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
]


def aplicar_migraciones_tabla(conn: sqlite3.Connection) -> None:
    """
    Crea cada tabla de MIGRACIONES_TABLA que todavía no exista. Idempotente
    (CREATE TABLE IF NOT EXISTS): si la tabla ya está, no hace nada. Después
    corre la reestructuración de `deudas` (reestructurar_deudas(), con su
    backup — por eso va primero: el backup queda con la base tal como
    estaba) y recrea deudas_mensuales si todavía no tiene `tab`
    (_deudas_mensuales_con_tab()); ninguna de las dos hace nada si ya se hizo.
    Por último, preparar_sync(): columna sincronizado_en, tablas de control
    y triggers de la sincronización con Supabase (idempotente).
    """
    for ddl in MIGRACIONES_TABLA:
        conn.execute(ddl)
    reestructurar_deudas(conn)
    _deudas_mensuales_con_tab(conn)
    # Al final: necesita que todas las tablas sincronizadas ya existan con
    # su estructura definitiva (deudas recién reestructurada, tarjetas_config).
    preparar_sync(conn)


def _columnas(conn: sqlite3.Connection, tabla: str) -> set[str]:
    return {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla});")}


def _existe_tabla(conn: sqlite3.Connection, nombre: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?;", (nombre,),
    ).fetchone() is not None


def _deudas_mensuales_con_tab(conn: sqlite3.Connection) -> None:
    """
    deudas_mensuales guardaba un neto por (persona, moneda); ahora guarda uno
    por (persona, tab, moneda). Es un caché recalculable
    (services/snapshots_service.py): si la tabla existente no tiene `tab`,
    se borra y se crea de nuevo, vacía — el SALDO ANTERIOR de Deudas se
    calcula en vivo hasta el próximo ↻.
    """
    if "tab" in _columnas(conn, "deudas_mensuales"):
        return
    conn.execute("DROP TABLE deudas_mensuales;")
    conn.execute(DDL_DEUDAS_MENSUALES)
    conn.commit()


# =============================================================
# REESTRUCTURACIÓN DE DEUDAS (estructura final: tabs + monto con signo)
# =============================================================
# `deudas` pasa a la estructura final de db/schema.sql: cada fila es un
# movimiento con una persona dentro de un tab — 'me_deben' (lo que te
# deben) o 'debo' (lo que debés) — y monto_minor CON SIGNO: positivo =
# entrada (la deuda crece), negativo = salida (un pago que la baja). Ver
# docs/DATA_MODEL_DECISIONS.md sección 22.
#
# Una base existente puede venir con una de dos estructuras anteriores:
#   ORIGINAL — monto_original_minor / monto_pendiente_minor / estado, con los
#     pagos en deuda_pagos (la app no se abrió desde antes de la primera
#     reestructuración).
#   LIBRO — el libro de movimientos intermedio: tipo ('a_favor' /
#     'en_contra') + monto_minor SIEMPRE positivo (el sentido lo daba
#     `tipo`), con la tabla original renombrada a deudas_old.
# Las dos se convierten directo a la final conservando el SENTIDO de cada
# fila, así el saldo de cada persona no cambia:
#   ORIGINAL: cada deuda → su tab (a_favor → me_deben, en_contra → debo),
#     con su monto ORIGINAL en positivo; su vencimiento, si tenía, pasa a
#     las notas ("VENCE: AAAA-MM-DD", mismo formato que el Registro). Cada
#     pago de deuda_pagos → el tab de su deuda, en NEGATIVO. Las
#     'incobrable' se migran tal cual (decisión explícita): vuelven a sumar
#     su monto completo.
#   LIBRO: los pagos que migró la reestructuración anterior (origen_tipo
#     'pago_migrado', de tipo OPUESTO al de su deuda) → el tab de su deuda,
#     en negativo. Las filas importadas del Excel (notas que empiezan con
#     PREFIJO_NOTA_EXCEL: siempre de alguien que te debe) → me_deben,
#     a_favor en positivo y en_contra — un pago que te hicieron — en
#     negativo. El resto (cargadas a mano o desde el Registro) → por su
#     tipo, en positivo.
# No se copia monto_minor tal cual (como proponía el pedido): en LIBRO nunca
# tuvo signo, así que un pago recibido hubiera quedado como una deuda tuya
# en 'debo' (decisión confirmada con el usuario). Límite: una fila de LIBRO
# cargada a mano como 'en_contra' para anotar un pago RECIBIDO no se puede
# distinguir de una deuda tuya — queda en 'debo'.
#
# Después se eliminan deuda_pagos, deudas_old (y deudas_old_N, si hubiera),
# la vista vw_deudas_activas y la tabla anterior; sus índices
# (idx_deudas_estado, idx_deuda_pagos_deuda) y el trigger trg_deudas_updated
# se van con sus tablas. La vista se borra PRIMERO: usa columnas que ya no
# existen, y si quedara, el ALTER TABLE … RENAME final fallaría al
# revalidar el schema. deuda_pagos se borra antes que las deudas: tiene la
# FK hacia ellas, así el DELETE implícito de DROP TABLE (PRAGMA
# foreign_keys = ON) no viola ninguna.
#
# Todo en UNA transacción: si algo falla, rollback completo y la base queda
# como estaba (la excepción sube: la app no arranca con una base a medio
# migrar). Si hay datos, antes se hace un backup del archivo.

TABLA_NUEVA = "deudas_final"

# Mismo prefijo que escribe migration/migrar_deudas.py en las notas de cada
# fila importada del Excel (lo importa de acá).
PREFIJO_NOTA_EXCEL = "MIGRADO DESDE EXCEL — TABLA DEUDAS"

# Idéntica a la de db/schema.sql, salvo el nombre (se crea al lado y se
# renombra al final).
DDL_DEUDAS_FINAL = f"""
CREATE TABLE {TABLA_NUEVA} (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entidad_persona TEXT NOT NULL,
    concepto        TEXT,
    tab             TEXT NOT NULL CHECK(tab IN ('me_deben', 'debo')),
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

# ORIGINAL — cada deuda, con su mismo id (las referencias origen_tipo/
# origen_id de otras pantallas siguen valiendo).
SQL_DESDE_ORIGINAL = f"""
INSERT INTO {TABLA_NUEVA} (
    id, entidad_persona, concepto, tab,
    monto_minor, moneda_id, fecha, notas,
    origen_tipo, origen_id, creada_en
)
SELECT
    id, entidad_persona, concepto,
    CASE tipo WHEN 'a_favor' THEN 'me_deben' ELSE 'debo' END,
    ABS(monto_original_minor), moneda_id, fecha_inicio,
    CASE
        WHEN fecha_vencimiento IS NULL THEN notas
        WHEN TRIM(COALESCE(notas, '')) = '' THEN 'VENCE: ' || fecha_vencimiento
        ELSE notas || ' — VENCE: ' || fecha_vencimiento
    END,
    COALESCE(origen_tipo, 'manual'), origen_id, creada_en
FROM deudas;
"""

# ORIGINAL — cada pago, en el tab de su deuda y en negativo. deuda_pagos.fecha
# tiene DEFAULT CURRENT_TIMESTAMP: substr(…, 1, 10) para que un valor con
# hora no rompa el CHECK de fecha y con él toda la migración.
SQL_PAGOS_DESDE_ORIGINAL = f"""
INSERT INTO {TABLA_NUEVA} (
    entidad_persona, concepto, tab,
    monto_minor, moneda_id, fecha,
    notas, origen_tipo, origen_id, creada_en
)
SELECT
    d.entidad_persona,
    COALESCE(dp.concepto, 'PAGO'),
    CASE d.tipo WHEN 'a_favor' THEN 'me_deben' ELSE 'debo' END,
    -ABS(dp.monto_applied_minor),
    d.moneda_id,
    substr(dp.fecha, 1, 10),
    dp.notas,
    'pago_migrado',
    dp.id,
    dp.fecha
FROM deuda_pagos dp
JOIN deudas d ON d.id = dp.deuda_id;
"""

# LIBRO — cada fila, con su mismo id (ver el bloque de arriba para las tres reglas).
SQL_DESDE_LIBRO = f"""
INSERT INTO {TABLA_NUEVA} (
    id, entidad_persona, concepto, tab,
    monto_minor, moneda_id, fecha, notas,
    origen_tipo, origen_id, sincronizado_en, creada_en
)
SELECT
    id, entidad_persona, concepto,
    CASE
        WHEN origen_tipo = 'pago_migrado' THEN
            CASE tipo WHEN 'en_contra' THEN 'me_deben' ELSE 'debo' END
        WHEN notas LIKE :prefijo_excel THEN 'me_deben'
        ELSE CASE tipo WHEN 'a_favor' THEN 'me_deben' ELSE 'debo' END
    END,
    CASE
        WHEN origen_tipo = 'pago_migrado' THEN -ABS(monto_minor)
        WHEN notas LIKE :prefijo_excel AND tipo = 'en_contra' THEN -ABS(monto_minor)
        ELSE ABS(monto_minor)
    END,
    moneda_id, fecha, notas,
    COALESCE(origen_tipo, 'manual'), origen_id, sincronizado_en, creada_en
FROM deudas;
"""

# deudas_old (reestructuración anterior) y deudas_old_2, _3… si hubo más de una.
PATRON_TABLAS_VIEJAS = re.compile(r"^deudas_old(_\d+)?$")


def _tablas_viejas(conn: sqlite3.Connection) -> list[str]:
    return [
        fila[0]
        for fila in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';")
        if PATRON_TABLAS_VIEJAS.match(fila[0])
    ]


def _estructura_deudas(conn: sqlite3.Connection) -> str:
    """'final', 'original' o 'libro' (ver el bloque de arriba)."""
    columnas = _columnas(conn, "deudas")
    if "tab" in columnas:
        return "final"
    if "monto_original_minor" in columnas:
        return "original"
    if {"tipo", "monto_minor", "fecha"} <= columnas:
        return "libro"
    raise RuntimeError(
        "La tabla `deudas` tiene una estructura desconocida (columnas: "
        f"{', '.join(sorted(columnas))}): no se reestructura. Revisá la base antes de abrir la app."
    )


def _backup_antes_de_reestructurar(conn: sqlite3.Connection) -> None:
    """
    Copia completa de la base (API de backup de sqlite3: incluye lo que esté
    en el -wal) al lado del archivo, antes de tocar `deudas`:
    <nombre de la base>_backup_antes_deudas_<fecha>_<hora>.db. Sin archivo
    (base en memoria), no hace nada.
    """
    archivo = next((fila[2] for fila in conn.execute("PRAGMA database_list;") if fila[1] == "main"), "")
    if not archivo:
        return
    origen = Path(archivo)
    destino = origen.parent / f"{origen.stem}_backup_antes_deudas_{datetime.now():%Y%m%d_%H%M%S}.db"
    copia = sqlite3.connect(destino)
    try:
        conn.backup(copia)
    finally:
        copia.close()
    print(f"[DeltaBalance] Backup antes de reestructurar deudas: {destino}")


def _normalizar_personas(conn: sqlite3.Connection, tabla: str) -> None:
    """entidad_persona sin espacios de más y en mayúsculas (utils/personas.py: en Python, UPPER() de SQLite no pasa la ñ ni los acentos)."""
    for fila_id, persona in conn.execute(f"SELECT id, entidad_persona FROM {tabla};").fetchall():
        normalizada = normalizar_persona(persona)
        if normalizada != persona:
            conn.execute(f"UPDATE {tabla} SET entidad_persona = ? WHERE id = ?;", (normalizada, fila_id))


def reestructurar_deudas(conn: sqlite3.Connection) -> None:
    """
    Convierte `deudas` a la estructura final (ver el bloque de arriba): crea
    deudas_final, copia las filas según la estructura de origen, normaliza
    el nombre de la persona, borra deuda_pagos, la vista vw_deudas_activas,
    la tabla anterior y deudas_old, y renombra deudas_final → deudas. Todo
    en UNA transacción (rollback completo si algo falla; la excepción sube).

    No hace nada si `deudas` ya tiene la estructura final. Si hay datos en
    alguna de las tablas que se tocan, antes hace un backup del archivo
    (_backup_antes_de_reestructurar()).

    Raises:
        RuntimeError si `deudas` no tiene ninguna estructura conocida.
    """
    if not _existe_tabla(conn, "deudas"):
        return
    estructura = _estructura_deudas(conn)
    if estructura == "final":
        return

    # Un backfill de las migraciones de columna puede haber dejado abierta
    # una transacción implícita: se cierra antes del backup y del BEGIN.
    conn.commit()
    viejas = _tablas_viejas(conn)
    hay_pagos = _existe_tabla(conn, "deuda_pagos")
    tablas = ["deudas", *viejas, *(["deuda_pagos"] if hay_pagos else [])]
    if any(conn.execute(f"SELECT 1 FROM {tabla} LIMIT 1;").fetchone() for tabla in tablas):
        _backup_antes_de_reestructurar(conn)

    conn.execute("BEGIN;")
    try:
        conn.execute("DROP VIEW IF EXISTS vw_deudas_activas;")
        conn.execute(f"DROP TABLE IF EXISTS {TABLA_NUEVA};")  # un resto de un intento anterior, si lo hubiera
        conn.execute(DDL_DEUDAS_FINAL)
        if estructura == "original":
            conn.execute(SQL_DESDE_ORIGINAL)
            if hay_pagos:
                conn.execute(SQL_PAGOS_DESDE_ORIGINAL)
        else:
            conn.execute(SQL_DESDE_LIBRO, {"prefijo_excel": f"{PREFIJO_NOTA_EXCEL}%"})
        _normalizar_personas(conn, TABLA_NUEVA)
        if hay_pagos:
            conn.execute("DROP TABLE deuda_pagos;")
        conn.execute("DROP TABLE deudas;")
        for tabla in viejas:
            conn.execute(f"DROP TABLE {tabla};")
        conn.execute(f"ALTER TABLE {TABLA_NUEVA} RENAME TO deudas;")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _aplicar_columnas(conn: sqlite3.Connection, migraciones: list[MigracionColumna]) -> None:
    """Agrega cada columna que todavía no exista en su tabla (idempotente)."""
    for migracion in migraciones:
        columnas_actuales = {
            fila[1]  # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
            for fila in conn.execute(f"PRAGMA table_info({migracion.tabla});")
        }
        if migracion.columna in columnas_actuales:
            continue
        conn.execute(
            f"ALTER TABLE {migracion.tabla} ADD COLUMN {migracion.ddl_columna};"
        )
        if migracion.sql_backfill:
            conn.execute(migracion.sql_backfill)


def aplicar_migraciones_columna(conn: sqlite3.Connection) -> None:
    """
    Recorre MIGRACIONES_COLUMNA y agrega cada columna que todavía no exista
    en su tabla. Idempotente: si la columna ya está, la salta sin error.
    """
    _aplicar_columnas(conn, MIGRACIONES_COLUMNA)


# =============================================================
# SINCRONIZACIÓN CON SUPABASE (sync/, docs/DATA_MODEL_DECISIONS.md sección 24)
# =============================================================
# Qué se prepara acá, en cada inicializar() (todo idempotente):
#
# 1. La columna sincronizado_en (cuándo se subió o bajó la fila por última
#    vez) en cada tabla de TABLAS_SINCRONIZADAS — las 10 del pedido más las
#    que dependen de ellas (cuentas_saldos, cuotas_credito,
#    resumenes_tarjeta, tarjetas_config, gasto_compartido_pagos: sin ellas,
#    en Supabase una compra quedaría sin sus cuotas y una cuenta sin sus
#    saldos). Va en MIGRACIONES_COLUMNA_SYNC y no en MIGRACIONES_COLUMNA
#    porque tarjetas_config la crea MIGRACIONES_TABLA y deudas la rearma
#    reestructurar_deudas(): la columna se agrega después de las dos.
# 2. sync_cambios: qué filas cambiaron desde la última sincronización —
#    (tabla, clave, 'guardado' | 'borrado', modificado_en). La llenan
#    triggers AFTER INSERT / UPDATE / DELETE sobre cada tabla sincronizada,
#    así ningún service ni repositorio tiene que acordarse de avisar, y un
#    borrado también viaja (a Supabase como fila marcada borrada).
#    modificado_en (UTC, con milésimas) es el reloj de last-write-wins.
# 3. sync_estado: una marca (MARCA_ESCRITURA_SYNC) que la propia
#    sincronización pone DENTRO de su transacción mientras escribe (marcar
#    filas como subidas, aplicar filas bajadas). Los triggers no registran
#    nada mientras la marca existe: si no, cada escritura de la sync —
#    incluido el UPDATE de updated_en que disparan los triggers
#    trg_*_updated de schema.sql — volvería a marcar la fila como
#    pendiente. Como la marca nunca se comitea (se borra antes del COMMIT),
#    las escrituras de la app en otra conexión nunca la ven.
#
# La clave de una fila es su clave primaria como texto: el id ('12'), o
# las columnas de CLAVES_SYNC unidas con '|' ('3|1' en cuentas_saldos).

TABLAS_SINCRONIZADAS: list[str] = [
    # En orden de dependencias (padres primero): así se aplican las filas
    # bajadas sin romper las FK; los borrados, al revés.
    "categorias",
    "cuentas",
    "cuentas_saldos",
    "tarjetas_config",
    "transacciones",
    "deudas",
    "compras_cuotas",
    "resumenes_tarjeta",
    "cuotas_credito",
    "presupuestos",
    "ingresos_proyectados",
    "hogares",
    "hogar_miembros",
    "gastos_compartidos",
    "gasto_compartido_pagos",
]

# Tablas cuya clave primaria no es `id`.
CLAVES_SYNC: dict[str, tuple[str, ...]] = {
    "cuentas_saldos": ("cuenta_id", "moneda_id"),
    "hogar_miembros": ("hogar_id", "usuario_local"),
}
SEPARADOR_CLAVE = "|"
MARCA_ESCRITURA_SYNC = "escribiendo_sync"

MIGRACIONES_COLUMNA_SYNC: list[MigracionColumna] = [
    MigracionColumna(tabla=tabla, columna="sincronizado_en", ddl_columna="sincronizado_en TEXT DEFAULT NULL")
    for tabla in TABLAS_SINCRONIZADAS
]

DDL_TABLAS_SYNC: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS sync_cambios (
        tabla         TEXT NOT NULL,
        clave         TEXT NOT NULL,
        operacion     TEXT NOT NULL CHECK(operacion IN ('guardado', 'borrado')),
        modificado_en TEXT NOT NULL,
        PRIMARY KEY (tabla, clave)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_estado (
        clave TEXT PRIMARY KEY,
        valor TEXT
    );
    """,
]


def claves_primarias(tabla: str) -> tuple[str, ...]:
    return CLAVES_SYNC.get(tabla, ("id",))


def _expresion_clave(tabla: str, fila: str) -> str:
    """SQL que arma la clave de texto de NEW/OLD en un trigger: CAST(NEW.id AS TEXT) o 'a' || '|' || 'b'."""
    return f" || '{SEPARADOR_CLAVE}' || ".join(f"CAST({fila}.{columna} AS TEXT)" for columna in claves_primarias(tabla))


def _ddl_triggers_sync(tabla: str) -> list[str]:
    cuando = f"WHEN NOT EXISTS (SELECT 1 FROM sync_estado WHERE clave = '{MARCA_ESCRITURA_SYNC}')"
    marca_tiempo = "strftime('%Y-%m-%d %H:%M:%f', 'now')"

    def _registrar(fila: str, operacion: str) -> str:
        return (
            "INSERT OR REPLACE INTO sync_cambios (tabla, clave, operacion, modificado_en) "
            f"VALUES ('{tabla}', {_expresion_clave(tabla, fila)}, '{operacion}', {marca_tiempo});"
        )

    return [
        f"CREATE TRIGGER IF NOT EXISTS trg_sync_{tabla}_insert AFTER INSERT ON {tabla} {cuando} "
        f"BEGIN {_registrar('NEW', 'guardado')} END;",
        f"CREATE TRIGGER IF NOT EXISTS trg_sync_{tabla}_update AFTER UPDATE ON {tabla} {cuando} "
        f"BEGIN {_registrar('NEW', 'guardado')} END;",
        f"CREATE TRIGGER IF NOT EXISTS trg_sync_{tabla}_delete AFTER DELETE ON {tabla} {cuando} "
        f"BEGIN {_registrar('OLD', 'borrado')} END;",
    ]


def preparar_sync(conn: sqlite3.Connection) -> None:
    """Columna sincronizado_en, sync_cambios / sync_estado y los triggers (ver el bloque de arriba)."""
    _aplicar_columnas(conn, MIGRACIONES_COLUMNA_SYNC)
    for ddl in DDL_TABLAS_SYNC:
        conn.execute(ddl)
    for tabla in TABLAS_SINCRONIZADAS:
        for ddl in _ddl_triggers_sync(tabla):
            conn.execute(ddl)
    conn.commit()
