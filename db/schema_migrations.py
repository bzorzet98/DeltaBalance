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

Reestructuración de `presupuestos` e `ingresos_proyectados` (más abajo,
reestructurar_presupuestos_ingresos(), pedido explícito, tarea
"Reestructuración de pantallas Ingresos y Presupuestos" — sin tocar
db/schema.sql, que sigue creando las tablas con su estructura anterior en
una base nueva: esta migración las convierte enseguida). Mismo mecanismo
que deudas: tabla nueva al lado, copia, borrado de la vieja y rename, todo
en una transacción y con backup previo si hay datos.

Rediseño de Ahorros e Inversiones (más abajo): ampliar_tipos_ahorro()
reconstruye activos_financieros y movimientos_activo cuando su CHECK de
`tipo` todavía es el anterior (sin 'cedear'/'plazo_flex' y sin 'aporte'), y
_sembrar_brokers() carga los brokers iniciales (BROKERS_INICIALES) en toda
base — las tablas nuevas brokers / activo_objetivos van en db/schema.sql.

Cargos extra de tarjeta (más abajo, migrar_cargos_extra_a_compras(),
docs/DATA_MODEL_DECISIONS.md sección 28): mueve una sola vez las filas de
resumen_cargos_extra a compras_cuotas (es_cargo_extra = 1) + cuotas_credito,
con backup previo. tarjetas_resumenes (fechas reales de cierre/vencimiento
de un resumen, sección 29) va en MIGRACIONES_TABLA, junto a tarjetas_config.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from utils.personas import normalizar_persona

# El mismo DEFAULT de los `id` de db/schema.sql: un UUID v4 en texto,
# generado por SQLite si un INSERT no trae id (ver el comentario de cabecera
# de schema.sql). `random() & 3` y no `abs(random()) % 4`: abs() del mínimo
# entero de 64 bits da overflow.
UUID_V4_SQL = (
    "(lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || "
    "substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || "
    "substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6))))"
)


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
        ddl_columna="transaccion_id TEXT REFERENCES transacciones(id)",
    ),
    # presupuestos.formula_estimado ya no se agrega: la estructura final de
    # presupuestos (reestructurar_presupuestos_ingresos()) no la tiene, y
    # estas migraciones corren ANTES que esa — la volverían a agregar en
    # cada arranque.
    MigracionColumna(
        tabla="activos_financieros",
        columna="cuenta_id",
        ddl_columna="cuenta_id TEXT REFERENCES cuentas(id)",
    ),
    # Rediseño de Ahorros e Inversiones: broker y comisiones por defecto
    # del activo, y comisión de cada movimiento. (El dólar del momento ya
    # existía como movimientos_activo.dolar_oficial_momento_minor.)
    MigracionColumna(
        tabla="activos_financieros",
        columna="broker_id",
        ddl_columna="broker_id TEXT REFERENCES brokers(id)",
    ),
    MigracionColumna(
        tabla="activos_financieros",
        columna="comision_compra_minor",
        ddl_columna="comision_compra_minor INTEGER DEFAULT 0",
    ),
    MigracionColumna(
        tabla="activos_financieros",
        columna="comision_venta_minor",
        ddl_columna="comision_venta_minor INTEGER DEFAULT 0",
    ),
    MigracionColumna(
        tabla="movimientos_activo",
        columna="comision_minor",
        ddl_columna="comision_minor INTEGER DEFAULT 0",
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
    MigracionColumna(
        # Etiqueta libre, igual que transacciones.tag (docs/DATA_MODEL_DECISIONS.md sección 26).
        tabla="compras_cuotas",
        columna="tag",
        ddl_columna="tag TEXT",
    ),
    # Moneda y fecha de cada cargo extra (docs/DATA_MODEL_DECISIONS.md
    # sección 28): las pide la fila de alta de Compras en cuotas y antes se
    # descartaban. NULL en los cargos anteriores — FeesService deduce su
    # moneda como antes (sección 15) y no tienen fecha propia.
    MigracionColumna(
        tabla="resumen_cargos_extra",
        columna="moneda_id",
        ddl_columna="moneda_id INTEGER REFERENCES monedas(id)",
    ),
    MigracionColumna(
        tabla="resumen_cargos_extra",
        columna="fecha",
        ddl_columna="fecha TEXT CHECK(fecha IS NULL OR fecha GLOB '????-??-??')",
    ),
    MigracionColumna(
        # 1 = cargo/reintegro del resumen de la tarjeta (impuesto, recargo,
        # ajuste), no una compra real. Los cargos extra viven acá y no en
        # resumen_cargos_extra (migrar_cargos_extra_a_compras(),
        # docs/DATA_MODEL_DECISIONS.md sección 28).
        tabla="compras_cuotas",
        columna="es_cargo_extra",
        ddl_columna="es_cargo_extra INTEGER NOT NULL DEFAULT 0",
    ),
]

# Columnas sobre `deudas` con su estructura FINAL: van después de
# reestructurar_deudas() (aplicar_migraciones_tabla()), que en una base vieja
# rearma la tabla desde DDL_DEUDAS_FINAL — agregadas antes, se perderían en
# esa misma corrida.
MIGRACIONES_COLUMNA_DEUDAS: list[MigracionColumna] = [
    MigracionColumna(
        # Etiqueta libre, igual que transacciones.tag (docs/DATA_MODEL_DECISIONS.md sección 26).
        tabla="deudas",
        columna="tag",
        ddl_columna="tag TEXT",
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
        cuenta_id    TEXT NOT NULL REFERENCES cuentas(id),
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
        hogar_id     TEXT NOT NULL REFERENCES hogares(id),
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
    f"""
    CREATE TABLE IF NOT EXISTS tarjetas_config (
        id                  TEXT PRIMARY KEY NOT NULL DEFAULT {UUID_V4_SQL},
        cuenta_id           TEXT NOT NULL REFERENCES cuentas(id) UNIQUE,
        dia_cierre          INTEGER NOT NULL CHECK(dia_cierre BETWEEN 1 AND 31),
        dia_vencimiento     INTEGER NOT NULL CHECK(dia_vencimiento BETWEEN 1 AND 31),
        creada_en           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_en          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # Fecha real de cierre / vencimiento de UN resumen, cuando no es la que
    # dan los días de tarjetas_config (feriados, cambios del banco). Una
    # fila por (tarjeta, mes, anio) — el mes del cierre calculado con el día
    # default, el mismo que identifica cada resumen en
    # FeesService.card_cycle_dates(). Cada fecha es opcional: NULL = la
    # calculada. Se sincroniza (TABLAS_SINCRONIZADAS, más abajo: su
    # sincronizado_en y sus triggers los agrega preparar_sync()). Ver
    # docs/DATA_MODEL_DECISIONS.md sección 29.
    f"""
    CREATE TABLE IF NOT EXISTS tarjetas_resumenes (
        id                  TEXT PRIMARY KEY NOT NULL DEFAULT {UUID_V4_SQL},
        cuenta_id           TEXT NOT NULL REFERENCES cuentas(id),
        mes                 INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
        anio                INTEGER NOT NULL,
        fecha_cierre        TEXT CHECK(fecha_cierre IS NULL OR fecha_cierre GLOB '????-??-??'),
        fecha_vence         TEXT CHECK(fecha_vence IS NULL OR fecha_vence GLOB '????-??-??'),
        creada_en           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_en          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(cuenta_id, mes, anio)
    );
    """,
]


def aplicar_migraciones_tabla(conn: sqlite3.Connection) -> None:
    """
    Crea cada tabla de MIGRACIONES_TABLA que todavía no exista. Idempotente
    (CREATE TABLE IF NOT EXISTS): si la tabla ya está, no hace nada. Después
    corre la reestructuración de `deudas` (reestructurar_deudas(), con su
    backup — por eso va primero: el backup queda con la base tal como
    estaba), las columnas de MIGRACIONES_COLUMNA_DEUDAS y recrea
    deudas_mensuales si todavía no tiene `tab` (_deudas_mensuales_con_tab())
    y convierte presupuestos / ingresos_proyectados a su estructura final
    (reestructurar_presupuestos_ingresos()) y los CHECK de tipo de ahorros
    (ampliar_tipos_ahorro()); ninguna hace nada si ya se hizo. Siembra los
    brokers iniciales (_sembrar_brokers(), INSERT OR IGNORE) y mueve los
    cargos extra de resumen_cargos_extra a compras_cuotas
    (migrar_cargos_extra_a_compras(), no hace nada si ya no quedan). Por
    último, preparar_sync(): columna sincronizado_en, tablas de control y
    triggers de la sincronización con Supabase (idempotente).
    """
    for ddl in MIGRACIONES_TABLA:
        conn.execute(ddl)
    reestructurar_deudas(conn)
    _aplicar_columnas(conn, MIGRACIONES_COLUMNA_DEUDAS)
    _deudas_mensuales_con_tab(conn)
    reestructurar_presupuestos_ingresos(conn)
    ampliar_tipos_ahorro(conn)
    _sembrar_brokers(conn)
    migrar_cargos_extra_a_compras(conn)
    # Al final: necesita que todas las tablas sincronizadas ya existan con
    # su estructura definitiva (deudas, presupuestos e ingresos_proyectados
    # recién reestructuradas, tarjetas_config). Sus triggers se fueron con
    # las tablas viejas: acá se crean sobre las nuevas.
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
    id              TEXT PRIMARY KEY NOT NULL DEFAULT {UUID_V4_SQL},
    entidad_persona TEXT NOT NULL,
    concepto        TEXT,
    tab             TEXT NOT NULL CHECK(tab IN ('me_deben', 'debo')),
    monto_minor     INTEGER NOT NULL,
    moneda_id       INTEGER NOT NULL REFERENCES monedas(id),
    fecha           TEXT NOT NULL CHECK(fecha GLOB '????-??-??'),
    notas           TEXT,
    origen_tipo     TEXT DEFAULT 'manual',
    origen_id       TEXT,
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


def _backup_antes_de_reestructurar(conn: sqlite3.Connection, motivo: str = "deudas") -> None:
    """
    Copia completa de la base (API de backup de sqlite3: incluye lo que esté
    en el -wal) al lado del archivo, antes de reestructurar `motivo`:
    <nombre de la base>_backup_antes_<motivo>_<fecha>_<hora>.db. Sin archivo
    (base en memoria), no hace nada.
    """
    archivo = next((fila[2] for fila in conn.execute("PRAGMA database_list;") if fila[1] == "main"), "")
    if not archivo:
        return
    origen = Path(archivo)
    destino = origen.parent / f"{origen.stem}_backup_antes_{motivo}_{datetime.now():%Y%m%d_%H%M%S}.db"
    copia = sqlite3.connect(destino)
    try:
        conn.backup(copia)
    finally:
        copia.close()
    print(f"[DeltaBalance] Backup antes de reestructurar {motivo}: {destino}")


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


# =============================================================
# REESTRUCTURACIÓN DE PRESUPUESTOS E INGRESOS PROYECTADOS
# =============================================================
# Pedido explícito (tarea "Reestructuración de pantallas Ingresos y
# Presupuestos"):
#
# ingresos_proyectados — se van `estado` y `monto_percibido_minor`; entran
#   monto_real_minor (lo cobrado), es_recurrente, notas y creada_en. Cada
#   fila vieja conserva su id, concepto, mes/año, moneda y estimado;
#   monto_percibido_minor pasa a monto_real_minor (el estado ya no hace
#   falta: "cobrado" es real > 0).
#
# presupuestos — dos tipos: 'fijo' (concepto libre: ALQUILER, SEGURO…, con
#   su real cargado a mano en monto_real_minor) y 'variable' (una
#   categoría del Registro, con el real calculado de las transacciones, o
#   el ítem especial COMPARTIDOS: categoria_id NULL y concepto
#   'COMPARTIDOS', ver services/presupuestos_service.py). monto_real_minor
#   no estaba en el pedido: se agregó con el OK del usuario, porque la
#   pantalla pide el Real de los fijos editable. Cada fila vieja era el
#   presupuesto de una categoría: pasa como 'variable' con su id,
#   categoría, estimado, moneda, mes/año y recurrente. Se descartan
#   monto_ejecutado_minor (nunca se calculó), notas (la pantalla nunca las
#   guardó) y formula_estimado (metadata del campo) — decisión del usuario;
#   quedan en el backup. Desaparece el UNIQUE(categoria_id, mes, anio): un
#   variable por categoría y mes lo controla ahora el service.
#
# Las dos con `id TEXT PRIMARY KEY NOT NULL DEFAULT <uuid>`, como toda tabla
# de db/schema.sql (sección 25), en vez del `id TEXT PRIMARY KEY` pelado del
# pedido.
#
# Sincronización: los triggers trg_sync_* se van con la tabla vieja y
# preparar_sync() los crea sobre la nueva. Cada fila migrada se anota en
# sync_cambios como 'guardado' (ahora): así sube con su forma nueva y le
# gana a la versión vieja que está en Supabase (con sincronizado_en NULL
# solamente, la remota — editada más tarde que creada_en — ganaría el
# last-write-wins y la forma vieja volvería a bajar).
#
# Todo en UNA transacción (rollback completo si algo falla; la excepción
# sube), con backup previo del archivo si alguna de las dos tiene datos.

TABLA_INGRESOS_NUEVA = "ingresos_proyectados_final"
TABLA_PRESUPUESTOS_NUEVA = "presupuestos_final"

DDL_INGRESOS_FINAL = f"""
CREATE TABLE {TABLA_INGRESOS_NUEVA} (
    id                   TEXT PRIMARY KEY NOT NULL DEFAULT {UUID_V4_SQL},
    concepto             TEXT NOT NULL,
    monto_estimado_minor INTEGER NOT NULL DEFAULT 0,
    monto_real_minor     INTEGER NOT NULL DEFAULT 0,
    moneda_id            INTEGER NOT NULL REFERENCES monedas(id),
    mes                  INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio                 INTEGER NOT NULL,
    es_recurrente        INTEGER NOT NULL DEFAULT 0,
    notas                TEXT,
    sincronizado_en      TEXT DEFAULT NULL,
    creada_en            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

DDL_PRESUPUESTOS_FINAL = f"""
CREATE TABLE {TABLA_PRESUPUESTOS_NUEVA} (
    id                   TEXT PRIMARY KEY NOT NULL DEFAULT {UUID_V4_SQL},
    tipo                 TEXT NOT NULL CHECK(tipo IN ('fijo', 'variable')),
    concepto             TEXT,
    categoria_id         TEXT REFERENCES categorias(id),
    monto_estimado_minor INTEGER NOT NULL DEFAULT 0,
    monto_real_minor     INTEGER NOT NULL DEFAULT 0,
    moneda_id            INTEGER NOT NULL REFERENCES monedas(id),
    mes                  INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio                 INTEGER NOT NULL,
    es_recurrente        INTEGER NOT NULL DEFAULT 0,
    sincronizado_en      TEXT DEFAULT NULL,
    creada_en            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# sincronizado_en y creada_en no se copian: NULL (pendiente de subir) y ahora.
SQL_INGRESOS_DESDE_ORIGINAL = f"""
INSERT INTO {TABLA_INGRESOS_NUEVA} (id, concepto, monto_estimado_minor, monto_real_minor, moneda_id, mes, anio)
SELECT id, concepto, monto_estimado_minor, COALESCE(monto_percibido_minor, 0), moneda_id, mes, anio
FROM ingresos_proyectados;
"""

SQL_PRESUPUESTOS_DESDE_ORIGINAL = f"""
INSERT INTO {TABLA_PRESUPUESTOS_NUEVA} (id, tipo, categoria_id, monto_estimado_minor, moneda_id, mes, anio, es_recurrente)
SELECT id, 'variable', categoria_id, monto_estimado_minor, moneda_id, mes, anio, COALESCE(es_recurrente, 0)
FROM presupuestos;
"""

# Misma marca de tiempo que los triggers de la sync (_ddl_triggers_sync()).
SQL_REGISTRAR_MIGRADAS = (
    "INSERT OR REPLACE INTO sync_cambios (tabla, clave, operacion, modificado_en) "
    "SELECT ?, CAST(id AS TEXT), 'guardado', strftime('%Y-%m-%d %H:%M:%f', 'now') FROM {tabla};"
)


@dataclass(frozen=True)
class _Reestructuracion:
    tabla: str
    tabla_nueva: str
    columna_final: str      # si la tabla ya la tiene, ya es la final
    columna_original: str   # la que identifica la estructura anterior
    ddl: str
    sql_copia: str


REESTRUCTURACIONES_PRESUPUESTOS_INGRESOS: list[_Reestructuracion] = [
    _Reestructuracion(
        tabla="ingresos_proyectados", tabla_nueva=TABLA_INGRESOS_NUEVA,
        columna_final="monto_real_minor", columna_original="monto_percibido_minor",
        ddl=DDL_INGRESOS_FINAL, sql_copia=SQL_INGRESOS_DESDE_ORIGINAL,
    ),
    _Reestructuracion(
        tabla="presupuestos", tabla_nueva=TABLA_PRESUPUESTOS_NUEVA,
        columna_final="tipo", columna_original="categoria_id",
        ddl=DDL_PRESUPUESTOS_FINAL, sql_copia=SQL_PRESUPUESTOS_DESDE_ORIGINAL,
    ),
]


def _necesita_reestructurar(conn: sqlite3.Connection, r: _Reestructuracion) -> bool:
    """False si la tabla no existe o ya es la final; True si tiene la estructura anterior."""
    if not _existe_tabla(conn, r.tabla):
        return False
    columnas = _columnas(conn, r.tabla)
    if r.columna_final in columnas:
        return False
    if r.columna_original in columnas:
        return True
    raise RuntimeError(
        f"La tabla `{r.tabla}` tiene una estructura desconocida (columnas: "
        f"{', '.join(sorted(columnas))}): no se reestructura. Revisá la base antes de abrir la app."
    )


def reestructurar_presupuestos_ingresos(conn: sqlite3.Connection) -> None:
    """
    Convierte presupuestos e ingresos_proyectados a su estructura final (ver
    el bloque de arriba): por cada una que todavía tenga la anterior, crea
    la tabla nueva, copia las filas, borra la vieja, renombra y anota las
    filas en sync_cambios. Todo en UNA transacción (rollback completo si
    algo falla; la excepción sube).

    No hace nada si las dos ya son las finales. Si alguna de las que se
    convierten tiene datos, antes hace un backup del archivo
    (_backup_antes_de_reestructurar()).

    Raises:
        RuntimeError si alguna de las dos no tiene ninguna estructura conocida.
    """
    pendientes = [r for r in REESTRUCTURACIONES_PRESUPUESTOS_INGRESOS if _necesita_reestructurar(conn, r)]
    if not pendientes:
        return

    # Mismo motivo que en reestructurar_deudas(): se cierra una transacción
    # implícita que pudiera haber quedado abierta antes del backup y del BEGIN.
    conn.commit()
    if any(conn.execute(f"SELECT 1 FROM {r.tabla} LIMIT 1;").fetchone() for r in pendientes):
        _backup_antes_de_reestructurar(conn, "presupuestos_ingresos")
    hay_sync = _existe_tabla(conn, "sync_cambios")

    conn.execute("BEGIN;")
    try:
        for r in pendientes:
            conn.execute(f"DROP TABLE IF EXISTS {r.tabla_nueva};")  # un resto de un intento anterior, si lo hubiera
            conn.execute(r.ddl)
            conn.execute(r.sql_copia)
            # Los triggers de la tabla vieja se borran con ella, antes del
            # DELETE implícito de DROP TABLE: no anotan ningún 'borrado'.
            conn.execute(f"DROP TABLE {r.tabla};")
            conn.execute(f"ALTER TABLE {r.tabla_nueva} RENAME TO {r.tabla};")
            if hay_sync:
                conn.execute(SQL_REGISTRAR_MIGRADAS.format(tabla=r.tabla), (r.tabla,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# =============================================================
# AHORROS E INVERSIONES: CHECK de tipo ampliados + brokers iniciales
# =============================================================
# Pedido explícito (tarea "Rediseño de Ahorros e Inversiones"):
#
# activos_financieros.tipo acepta además 'cedear' y 'plazo_flex' (se
#   conservan 'cripto' y 'otro': 'otro' es el "Efectivo reservado en
#   <cuenta>" que crea el Registro — SavingsService.get_or_create_reserved_
#   cash_asset()). 'cedear' y no el 'cedeard' del pedido: decisión del
#   usuario.
# movimientos_activo.tipo acepta además 'aporte'. El pedido agregaba una
#   columna tipo_movimiento con esos valores al lado de `tipo`; se amplió
#   `tipo` en su lugar (decisión del usuario: una sola columna).
#
# SQLite no altera un CHECK: se reconstruye la tabla (nueva al lado, copia,
# borrado, rename), en UNA transacción y con backup previo si hay datos.
# Se copian las columnas que tengan las dos tablas, así las que agregaron
# antes las migraciones de columna (cuenta_id, broker_id, comisiones,
# transaccion_id) viajan solas. Los índices y triggers de la tabla vieja
# (trg_activos_financieros_updated, idx_movimientos_activo_activo) se
# leen de sqlite_master y se vuelven a crear sobre la nueva.
#
# Las dos tablas tienen hijas con FK (movimientos_activo → activos;
# asignaciones / activo_objetivos → movimientos / activos): con
# foreign_keys = ON, el DROP de la vieja fallaría por esas filas. Se apagan
# las FK mientras dura (procedimiento de 12 pasos de la documentación de
# SQLite, "Making Other Kinds Of Table Schema Changes"): PRAGMA
# foreign_keys = OFF fuera de la transacción, PRAGMA foreign_key_check
# antes del COMMIT, y se vuelven a prender pase lo que pase.

TIPOS_ACTIVO_FINANCIERO = ("accion", "fci", "plazo_fijo", "cripto", "otro", "cedear", "plazo_flex")
TIPOS_MOVIMIENTO_ACTIVO = ("compra", "venta", "rendimiento", "aporte")

BROKERS_INICIALES = ("COCOS", "BULL MARKET", "IOL", "MERCADO PAGO", "NACION", "BALANZ")


def _valores_sql(valores: tuple[str, ...]) -> str:
    return ", ".join(f"'{valor}'" for valor in valores)


DDL_ACTIVOS_FINAL = f"""
CREATE TABLE activos_financieros_final (
    id                    TEXT PRIMARY KEY NOT NULL DEFAULT {UUID_V4_SQL},
    nombre                TEXT NOT NULL,
    tipo                  TEXT NOT NULL CHECK(tipo IN ({_valores_sql(TIPOS_ACTIVO_FINANCIERO)})),
    moneda_id             INTEGER NOT NULL REFERENCES monedas(id),
    activa                INTEGER NOT NULL DEFAULT 1,
    creada_en             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cuenta_id             TEXT REFERENCES cuentas(id),
    broker_id             TEXT REFERENCES brokers(id),
    comision_compra_minor INTEGER DEFAULT 0,
    comision_venta_minor  INTEGER DEFAULT 0
);
"""

DDL_MOVIMIENTOS_FINAL = f"""
CREATE TABLE movimientos_activo_final (
    id                          TEXT PRIMARY KEY NOT NULL DEFAULT {UUID_V4_SQL},
    activo_id                   TEXT NOT NULL REFERENCES activos_financieros(id),
    tipo                        TEXT NOT NULL CHECK(tipo IN ({_valores_sql(TIPOS_MOVIMIENTO_ACTIVO)})),
    fecha                       TEXT NOT NULL CHECK(fecha GLOB '????-??-??'),
    cantidad                    REAL,
    precio_unitario_minor       INTEGER,
    monto_total_minor           INTEGER NOT NULL,
    dolar_oficial_momento_minor INTEGER,
    notas                       TEXT,
    creada_en                   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    transaccion_id              TEXT REFERENCES transacciones(id),
    comision_minor              INTEGER DEFAULT 0
);
"""


@dataclass(frozen=True)
class _AmpliacionCheck:
    tabla: str
    tabla_nueva: str
    marca: str  # valor del CHECK nuevo: si el CREATE TABLE ya lo tiene, no hay nada que hacer
    ddl: str


AMPLIACIONES_AHORRO: list[_AmpliacionCheck] = [
    _AmpliacionCheck("activos_financieros", "activos_financieros_final", "'plazo_flex'", DDL_ACTIVOS_FINAL),
    _AmpliacionCheck("movimientos_activo", "movimientos_activo_final", "'aporte'", DDL_MOVIMIENTOS_FINAL),
]


def _sql_tabla(conn: sqlite3.Connection, tabla: str) -> str:
    fila = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?;", (tabla,)).fetchone()
    return (fila[0] or "") if fila else ""


def _columnas_en_orden(conn: sqlite3.Connection, tabla: str) -> list[str]:
    return [fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla});")]


def ampliar_tipos_ahorro(conn: sqlite3.Connection) -> None:
    """
    Reconstruye activos_financieros / movimientos_activo si su CHECK de
    `tipo` todavía es el anterior (ver el bloque de arriba). No hace nada si
    ya están ampliados (siempre, en una base nueva: db/schema.sql ya los
    crea así). Todo en UNA transacción; si algo falla, rollback completo y
    la excepción sube.

    Raises:
        RuntimeError si al terminar PRAGMA foreign_key_check encuentra
        alguna fila huérfana (no debería: se copian los mismos ids).
    """
    pendientes = [
        r for r in AMPLIACIONES_AHORRO if _existe_tabla(conn, r.tabla) and r.marca not in _sql_tabla(conn, r.tabla)
    ]
    if not pendientes:
        return

    # Mismo motivo que en reestructurar_deudas(): se cierra una transacción
    # implícita antes del backup, del PRAGMA (no tiene efecto dentro de una
    # transacción) y del BEGIN.
    conn.commit()
    if any(conn.execute(f"SELECT 1 FROM {r.tabla} LIMIT 1;").fetchone() for r in pendientes):
        _backup_antes_de_reestructurar(conn, "ahorros")

    conn.execute("PRAGMA foreign_keys = OFF;")
    try:
        conn.execute("BEGIN;")
        try:
            for r in pendientes:
                # Índices y triggers propios (sqlite_master ya los guarda sin IF NOT EXISTS).
                anexos = [
                    fila[0] for fila in conn.execute(
                        "SELECT sql FROM sqlite_master WHERE tbl_name = ? AND type IN ('index', 'trigger') AND sql IS NOT NULL;",
                        (r.tabla,),
                    )
                ]
                conn.execute(f"DROP TABLE IF EXISTS {r.tabla_nueva};")  # un resto de un intento anterior, si lo hubiera
                conn.execute(r.ddl)
                viejas = set(_columnas_en_orden(conn, r.tabla))
                columnas = ", ".join(c for c in _columnas_en_orden(conn, r.tabla_nueva) if c in viejas)
                conn.execute(f"INSERT INTO {r.tabla_nueva} ({columnas}) SELECT {columnas} FROM {r.tabla};")
                conn.execute(f"DROP TABLE {r.tabla};")
                conn.execute(f"ALTER TABLE {r.tabla_nueva} RENAME TO {r.tabla};")
                for ddl in anexos:
                    conn.execute(ddl)
            huerfana = conn.execute("PRAGMA foreign_key_check;").fetchone()
            if huerfana is not None:
                raise RuntimeError(
                    f"Ampliación de tipos de ahorro: fila huérfana en {huerfana[0]} (rowid {huerfana[1]}) "
                    f"hacia {huerfana[2]}. No se aplicó nada."
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")


def _sembrar_brokers(conn: sqlite3.Connection) -> None:
    """Brokers iniciales (INSERT OR IGNORE por el UNIQUE de nombre: no duplica ni reactiva nada)."""
    if not _existe_tabla(conn, "brokers"):
        return
    conn.executemany("INSERT OR IGNORE INTO brokers (nombre) VALUES (?);", [(nombre,) for nombre in BROKERS_INICIALES])
    conn.commit()


# =============================================================
# CARGOS EXTRA → compras_cuotas (docs/DATA_MODEL_DECISIONS.md sección 28)
# =============================================================
# Los cargos extra de un resumen (impuestos, recargos, ajustes/reintegros)
# pasan de resumen_cargos_extra — que no se sincroniza — a compras_cuotas
# con es_cargo_extra = 1: una compra de 1 cuota con la categoría especial de
# su tipo, y su cuota en cuotas_credito en el mes del resumen, ya incluida
# en él. compras_cuotas y cuotas_credito sí se sincronizan.
#
# Copia CONGELADA de services/fees_service.py CATEGORIAS_CARGO_EXTRA (tipo →
# categoría especial): db/ no importa services/ (es al revés), y una
# migración de datos no tiene que cambiar si después cambia el código vivo.
CATEGORIA_DE_CARGO_MIGRACION: dict[str, tuple[str, str]] = {
    "impuesto": ("TARJETA DE CRÉDITO", "Impuesto tarjeta"),
    "recargo": ("TARJETA DE CRÉDITO", "Recargo tarjeta"),
    "ajuste": ("TARJETA DE CRÉDITO", "Ajuste/Reintegro tarjeta"),
}
# compras_cuotas.moneda_id es NOT NULL: la moneda de un cargo viejo que no
# se puede deducir (ver _moneda_cargo_viejo()).
MONEDA_CARGO_POR_DEFECTO = "ARS"


def _moneda_cargo_viejo(conn: sqlite3.Connection, cuenta_id: str, mes: int, anio: int) -> int:
    """
    Moneda de un cargo guardado sin moneda (los anteriores a la columna
    moneda_id): la única de las compras de esa tarjeta con cuotas ese mes
    (la misma regla con que se sumaban, sección 15); sin cuotas ese mes, la
    única en que opera la tarjeta (cuentas_saldos); si no,
    MONEDA_CARGO_POR_DEFECTO — los impuestos de una tarjeta se cobran en pesos.
    """
    de_cuotas = conn.execute(
        """
        SELECT DISTINCT pc.moneda_id
        FROM cuotas_credito qc
        JOIN compras_cuotas pc ON pc.id = qc.compra_id
        WHERE pc.cuenta_id = ? AND qc.mes_proyectado = ? AND qc.anio_proyectado = ?
          AND qc.estado != 'omitido' AND pc.es_cargo_extra = 0;
        """,
        (cuenta_id, mes, anio),
    ).fetchall()
    if len(de_cuotas) == 1:
        return de_cuotas[0][0]
    if not de_cuotas:
        de_cuenta = conn.execute("SELECT moneda_id FROM cuentas_saldos WHERE cuenta_id = ?;", (cuenta_id,)).fetchall()
        if len(de_cuenta) == 1:
            return de_cuenta[0][0]
    return conn.execute("SELECT id FROM monedas WHERE codigo = ?;", (MONEDA_CARGO_POR_DEFECTO,)).fetchone()[0]


def migrar_cargos_extra_a_compras(conn: sqlite3.Connection) -> None:
    """
    Mueve cada fila de resumen_cargos_extra a compras_cuotas (es_cargo_extra
    = 1, total_cuotas = 1) más su cuota en cuotas_credito, todo en UNA
    transacción (rollback completo si algo falla; la excepción sube) y con
    backup previo del archivo. Idempotente: lo movido se borra de
    resumen_cargos_extra, así que la corrida siguiente no encuentra nada.

    - id de la compra = id del cargo (no cambia su identidad); la cuota
      toma el DEFAULT (UUID nuevo).
    - Categoría: la especial de su tipo (CATEGORIA_DE_CARGO_MIGRACION). Un
      cargo de tipo 'otro' (sin categoría especial — la pantalla nunca los
      creó) o cuya categoría especial no está en la base NO se mueve: queda
      en resumen_cargos_extra y se avisa por consola.
    - Moneda: la del cargo; sin ella, _moneda_cargo_viejo().
    - fecha_compra: la del cargo; sin ella, el día 1 del mes del resumen.
    - Cuota: mes/año del resumen, monto = el del cargo (con su signo),
      resumen_id = su resumen, y estado 'pagado' si el resumen ya se pagó o
      'en_resumen' si no (el cargo ya es parte de ese resumen).
    - Sin columna sincronizado_en en el INSERT: queda NULL, pendiente de
      subir (y los triggers de sync lo anotan en sync_cambios).

    No hace nada con ids enteros (migration/migrar_a_uuid_pk.py corre las
    migraciones sobre una copia así antes de convertirla): el id es UUID.
    """
    if not _existe_tabla(conn, "resumen_cargos_extra") or "es_cargo_extra" not in _columnas(conn, "compras_cuotas"):
        return
    if not usa_ids_uuid(conn):
        return
    cargos = conn.execute(
        """
        SELECT ce.id, ce.concepto, ce.tipo, ce.monto_minor, ce.moneda_id, ce.fecha, ce.creada_en,
               r.id, r.cuenta_id, r.mes, r.anio, r.estado
        FROM resumen_cargos_extra ce
        JOIN resumenes_tarjeta r ON r.id = ce.resumen_id
        ORDER BY ce.rowid;
        """
    ).fetchall()
    if not cargos:
        return
    categorias: dict[str, str] = {}
    for tipo, (principal, subcategoria) in CATEGORIA_DE_CARGO_MIGRACION.items():
        fila = conn.execute(
            "SELECT id FROM categorias WHERE categoria_principal = ? AND subcategoria = ?;", (principal, subcategoria),
        ).fetchone()
        if fila is not None:
            categorias[tipo] = fila[0]
    movibles = [cargo for cargo in cargos if cargo[2] in categorias]
    if len(movibles) < len(cargos):
        print(
            f"[DeltaBalance] {len(cargos) - len(movibles)} cargo(s) extra sin categoría especial (tipo 'otro' o "
            f"categoría borrada): quedan en resumen_cargos_extra, sin migrar."
        )
    if not movibles:
        return

    # Mismo motivo que en reestructurar_deudas(): se cierra una transacción
    # implícita que pudiera haber quedado abierta antes del backup y del BEGIN.
    conn.commit()
    _backup_antes_de_reestructurar(conn, "cargos_extra")
    conn.execute("BEGIN;")
    try:
        for (cargo_id, concepto, tipo, monto_minor, moneda_id, fecha, creada_en,
             resumen_id, cuenta_id, mes, anio, estado_resumen) in movibles:
            if moneda_id is None:
                moneda_id = _moneda_cargo_viejo(conn, cuenta_id, mes, anio)
            conn.execute(
                """
                INSERT INTO compras_cuotas
                    (id, fecha_compra, concepto, cuenta_id, categoria_id, moneda_id, monto_total_minor,
                     total_cuotas, monto_por_cuota_minor, es_cargo_extra, creada_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 1, ?);
                """,
                (cargo_id, fecha or f"{anio:04d}-{mes:02d}-01", concepto, cuenta_id, categorias[tipo], moneda_id,
                 monto_minor, monto_minor, creada_en),
            )
            conn.execute(
                """
                INSERT INTO cuotas_credito
                    (compra_id, resumen_id, numero_cuota, mes_proyectado, anio_proyectado, monto_cuota_minor, estado)
                VALUES (?, ?, 1, ?, ?, ?, ?);
                """,
                (cargo_id, resumen_id, mes, anio, monto_minor, "pagado" if estado_resumen == "pagado" else "en_resumen"),
            )
            conn.execute("DELETE FROM resumen_cargos_extra WHERE id = ?;", (cargo_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"[DeltaBalance] {len(movibles)} cargo(s) extra movido(s) de resumen_cargos_extra a compras_cuotas.")


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


def usa_ids_uuid(conn: sqlite3.Connection) -> bool:
    """True si la base ya tiene ids UUID: transacciones.id es TEXT (una base sin la tabla, también)."""
    for fila in conn.execute("PRAGMA table_info(transacciones);"):
        if fila[1] == "id":
            return (fila[2] or "").upper() == "TEXT"
    return True


def exigir_ids_uuid(conn: sqlite3.Connection) -> None:
    """
    Frena antes de tocar nada si la base todavía tiene ids enteros: con el
    código actual (los repositorios insertan UUIDs) se rompería en el primer
    alta ("datatype mismatch" en una INTEGER PRIMARY KEY).
    """
    if not usa_ids_uuid(conn):
        raise RuntimeError(
            "La base de datos todavía usa ids enteros. Cerrá la app y corré, parado en la raíz del proyecto: "
            "python migration/migrar_a_uuid_pk.py (prueba) y después python migration/migrar_a_uuid_pk.py "
            "--confirmar. Ver docs/DATA_MODEL_DECISIONS.md sección 25."
        )


def aplicar_migraciones_columna(conn: sqlite3.Connection, exigir_uuid: bool = True) -> None:
    """
    Recorre MIGRACIONES_COLUMNA y agrega cada columna que todavía no exista
    en su tabla. Idempotente: si la columna ya está, la salta sin error.

    Antes, exigir_ids_uuid() (salvo exigir_uuid=False: solo
    migration/migrar_a_uuid_pk.py, que pone al día una COPIA de la base
    vieja antes de convertirla).
    """
    if exigir_uuid:
        exigir_ids_uuid(conn)
    _aplicar_columnas(conn, MIGRACIONES_COLUMNA)


# =============================================================
# SINCRONIZACIÓN CON SUPABASE (sync/, docs/DATA_MODEL_DECISIONS.md sección 24)
# =============================================================
# Qué se prepara acá, en cada inicializar() (todo idempotente):
#
# 1. La columna sincronizado_en (cuándo se subió o bajó la fila por última
#    vez) en cada tabla de TABLAS_SINCRONIZADAS — las 10 del pedido más las
#    que dependen de ellas (cuentas_saldos, cuotas_credito,
#    resumenes_tarjeta, tarjetas_config, tarjetas_resumenes,
#    gasto_compartido_pagos: sin ellas, en Supabase una compra quedaría sin
#    sus cuotas y una cuenta sin sus saldos). Va en MIGRACIONES_COLUMNA_SYNC
#    y no en MIGRACIONES_COLUMNA porque tarjetas_config / tarjetas_resumenes
#    las crea MIGRACIONES_TABLA y deudas la rearma reestructurar_deudas(): la
#    columna se agrega después.
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
# La clave de una fila es su clave primaria como texto: el id (un UUID), o
# las columnas de CLAVES_SYNC unidas con '|' ('<uuid de la cuenta>|1' en cuentas_saldos).

TABLAS_SINCRONIZADAS: list[str] = [
    # En orden de dependencias (padres primero): así se aplican las filas
    # bajadas sin romper las FK; los borrados, al revés.
    "categorias",
    "cuentas",
    "cuentas_saldos",
    "tarjetas_config",
    "tarjetas_resumenes",  # fechas reales de un resumen (sección 29)
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
