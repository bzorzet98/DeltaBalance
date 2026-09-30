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

`MigracionColumna.sql_backfill` (opcional, agregado para la columna
`gastos_compartidos.monto_pendiente_minor`): un UPDATE que se ejecuta una
sola vez, inmediatamente después del ALTER TABLE que agrega la columna —
nunca en corridas donde la columna ya existía. Sirve para columnas nuevas
que no pueden arrancar con un valor por defecto constante (ej. "igual al
valor de otra columna ya existente en cada fila"), a diferencia de las
columnas anteriores de esta lista, que sí se conforman con el DEFAULT del
propio ALTER TABLE.
"""

from __future__ import annotations

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
    """
    CREATE TABLE IF NOT EXISTS deudas_mensuales (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        entidad_persona TEXT NOT NULL,
        moneda_id       INTEGER NOT NULL REFERENCES monedas(id),
        mes             INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
        anio            INTEGER NOT NULL,
        monto_minor     INTEGER NOT NULL DEFAULT 0,
        calculado_en    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(entidad_persona, moneda_id, mes, anio)
    );
    """,
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
]


def aplicar_migraciones_tabla(conn: sqlite3.Connection) -> None:
    """
    Crea cada tabla de MIGRACIONES_TABLA que todavía no exista. Idempotente
    (CREATE TABLE IF NOT EXISTS): si la tabla ya está, no hace nada. Después
    corre la reestructuración de `deudas` (reestructurar_deudas()), que
    tampoco hace nada si ya se hizo.
    """
    for ddl in MIGRACIONES_TABLA:
        conn.execute(ddl)
    reestructurar_deudas(conn)


# =============================================================
# REESTRUCTURACIÓN DE DEUDAS (libro de movimientos)
# =============================================================
# `deudas` deja de ser "una deuda con su pendiente, su estado y sus pagos en
# deuda_pagos" y pasa a ser un libro de movimientos: cada fila es un monto
# con dirección (a_favor = te deben más, en_contra = debés más / te pagaron),
# y el saldo con una persona es la suma con signo de sus filas. Un pago es
# una fila más, de tipo opuesto. Ver docs/DATA_MODEL_DECISIONS.md sección 22.
#
# db/schema.sql sigue teniendo la definición VIEJA de `deudas` (no se tocó
# en esta tarea): en una base nueva, schema.sql la crea con la estructura
# vieja y esta reestructuración la convierte en la primera inicializar().
# Por eso la condición para correr es "la tabla todavía tiene la estructura
# vieja" (columna monto_original_minor), no "hay datos": una base vacía
# también se reestructura. En las corridas siguientes, los CREATE ... IF NOT
# EXISTS de schema.sql sobre `deudas` (tabla, índice idx_deudas_estado,
# trigger trg_deudas_updated, vista vw_deudas_activas) no hacen nada: la
# tabla nueva ya existe con ese nombre, y el índice, el trigger y la vista
# existen con el suyo — el ALTER TABLE ... RENAME se los llevó a
# deudas_old junto con la FK de deuda_pagos (SQLite >= 3.26 reescribe esas
# referencias al renombrar).

DDL_DEUDAS_V2 = """
CREATE TABLE IF NOT EXISTS deudas_v2 (
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

# 1b — cada deuda vieja, con su monto ORIGINAL (conserva el id: las
# referencias origen_tipo/origen_id de otras pantallas siguen valiendo). Las
# 'incobrable' se migran tal cual (decisión explícita): vuelven a sumar su
# monto completo.
SQL_MIGRAR_DEUDAS = """
INSERT INTO deudas_v2 (
    id, entidad_persona, concepto, tipo,
    monto_minor, moneda_id, fecha, notas,
    origen_tipo, origen_id, creada_en
)
SELECT
    id, entidad_persona, concepto, tipo,
    monto_original_minor, moneda_id, fecha_inicio, notas,
    COALESCE(origen_tipo, 'manual'), origen_id, creada_en
FROM deudas
WHERE NOT EXISTS (SELECT 1 FROM deudas_v2 WHERE deudas_v2.id = deudas.id);
"""

# 1c — cada pago de deuda_pagos, como una fila de tipo OPUESTO a su deuda
# (un pago que te hicieron baja lo que te deben). deuda_pagos.fecha tiene
# DEFAULT CURRENT_TIMESTAMP: substr(…, 1, 10) para que un valor con hora no
# rompa el CHECK de fecha y con él toda la migración.
SQL_MIGRAR_PAGOS = """
INSERT INTO deudas_v2 (
    entidad_persona, concepto, tipo,
    monto_minor, moneda_id, fecha,
    notas, origen_tipo, origen_id, creada_en
)
SELECT
    d.entidad_persona,
    COALESCE(dp.concepto, 'PAGO'),
    CASE d.tipo WHEN 'a_favor' THEN 'en_contra' ELSE 'a_favor' END,
    dp.monto_applied_minor,
    d.moneda_id,
    substr(dp.fecha, 1, 10),
    dp.notas,
    'pago_migrado',
    dp.id,
    dp.fecha
FROM deuda_pagos dp
JOIN deudas d ON d.id = dp.deuda_id
WHERE NOT EXISTS (
    SELECT 1 FROM deudas_v2
    WHERE origen_tipo = 'pago_migrado' AND origen_id = dp.id
);
"""

NOMBRE_TABLA_VIEJA = "deudas_old"


def _columnas(conn: sqlite3.Connection, tabla: str) -> set[str]:
    return {fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla});")}


def _existe_tabla(conn: sqlite3.Connection, nombre: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?;", (nombre,),
    ).fetchone() is not None


def _nombre_libre(conn: sqlite3.Connection, base: str) -> str:
    """deudas_old, o deudas_old_2, _3… si ya existe (no pisar una copia vieja)."""
    nombre, n = base, 1
    while _existe_tabla(conn, nombre):
        n += 1
        nombre = f"{base}_{n}"
    return nombre


def _backup_antes_de_reestructurar(conn: sqlite3.Connection) -> None:
    """
    Copia completa de la base (API de backup de sqlite3: incluye lo que esté
    en el -wal) al lado del archivo, antes de tocar `deudas`. Sin archivo
    (base en memoria), no hace nada.
    """
    archivo = next((fila[2] for fila in conn.execute("PRAGMA database_list;") if fila[1] == "main"), "")
    if not archivo:
        return
    origen = Path(archivo)
    destino = origen.parent / f"deltabalance_backup_antes_deudas_{datetime.now():%Y%m%d_%H%M%S}.db"
    copia = sqlite3.connect(destino)
    try:
        conn.backup(copia)
    finally:
        copia.close()
    print(f"[DeltaBalance] Backup antes de reestructurar deudas: {destino}")


def reestructurar_deudas(conn: sqlite3.Connection) -> None:
    """
    Convierte `deudas` al libro de movimientos (ver bloque de arriba):
    1a crea deudas_v2, 1b copia las deudas, 1c copia los pagos como filas
    de tipo opuesto, normaliza el nombre de la persona (utils/personas.py) y
    1d renombra deudas → deudas_old y deudas_v2 → deudas. Todo en UNA
    transacción: si algo falla, rollback completo y `deudas` queda intacta
    (la excepción sube: la app no arranca con una base a medio migrar).

    Solo corre si `deudas` todavía tiene la estructura vieja. Si además
    tiene datos (deudas o pagos), antes hace un backup del archivo
    (_backup_antes_de_reestructurar()).
    """
    if not _existe_tabla(conn, "deudas") or "monto_original_minor" not in _columnas(conn, "deudas"):
        return  # ya reestructurada

    # Un backfill de las migraciones de columna puede haber dejado abierta
    # una transacción implícita: se cierra antes del backup y del BEGIN.
    conn.commit()
    hay_datos = conn.execute(
        "SELECT (SELECT COUNT(*) FROM deudas) + (SELECT COUNT(*) FROM deuda_pagos);"
    ).fetchone()[0] > 0
    if hay_datos:
        _backup_antes_de_reestructurar(conn)

    conn.execute("BEGIN;")
    try:
        conn.execute(DDL_DEUDAS_V2)
        conn.execute(SQL_MIGRAR_DEUDAS)
        conn.execute(SQL_MIGRAR_PAGOS)
        for fila_id, persona in conn.execute("SELECT id, entidad_persona FROM deudas_v2;").fetchall():
            normalizada = normalizar_persona(persona)
            if normalizada != persona:
                conn.execute("UPDATE deudas_v2 SET entidad_persona = ? WHERE id = ?;", (normalizada, fila_id))
        conn.execute(f"ALTER TABLE deudas RENAME TO {_nombre_libre(conn, NOMBRE_TABLA_VIEJA)};")
        conn.execute("ALTER TABLE deudas_v2 RENAME TO deudas;")
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def aplicar_migraciones_columna(conn: sqlite3.Connection) -> None:
    """
    Recorre MIGRACIONES_COLUMNA y agrega cada columna que todavía no exista
    en su tabla. Idempotente: si la columna ya está, la salta sin error.
    """
    for migracion in MIGRACIONES_COLUMNA:
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
