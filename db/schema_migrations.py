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
pasar por acá.

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
