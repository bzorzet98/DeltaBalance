"""
DeltaBalance — repositories/gasto_compartido_pagos_repository.py

Acceso a datos para la tabla `gasto_compartido_pagos` (Tarea 9, Parte A —
docs/PROXIMOS_PASOS.md). Sin lógica de negocio: no calcula el nuevo
monto_pendiente_minor de gastos_compartidos ni decide cuándo pasa a
'saldado' — eso es responsabilidad de SharedExpensesService.aplicar_pago(),
que orquesta este repositorio junto con
GastosCompartidosRepository.actualizar_monto_pendiente() dentro de la misma
transacción (ver docstring de aplicar_pago() para el detalle de la
atomicidad entre los dos repositorios).

Decisión de diseño (archivo separado, a diferencia de deuda_pagos): en este
repo, `deuda_pagos` vive como métodos dentro de DeudasRepository (un solo
archivo cubre `deudas` + `deuda_pagos`), no como una clase separada —
confirmado por lectura de repositories/deudas_repository.py antes de
escribir esto. Acá se eligió un archivo/clase propio en su lugar, siguiendo
la regla por defecto de CLAUDE.md §3 ("un repositorio = una entidad de la
base de datos"): `gasto_compartido_pagos` es su propia tabla con su propio
ciclo de vida (se lista por separado en la futura UI de historial de
pagos), así que no comparte tanta cohesión con `gastos_compartidos` como
`cuotas_credito` comparte con `compras_cuotas` (el ejemplo de tablas que sí
van juntas, según ese mismo párrafo). La atomicidad INSERT+UPDATE entre
esta tabla y `gastos_compartidos` (que en DeudasRepository.registrar_pago()
vive dentro de un único método de repositorio) se resuelve acá un nivel
más arriba, en el service, pasando el mismo `conn` a ambos repositorios.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager


class GastoCompartidoPagosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        gasto_compartido_id: int,
        monto_aplicado_minor: int,
        tipo_pago: str,
        fecha: str,
        transaccion_id: Optional[int] = None,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un pago. No actualiza gastos_compartidos.monto_pendiente_minor
        ni estado — eso es GastosCompartidosRepository.actualizar_monto_pendiente(),
        llamado aparte por el service dentro de la misma transacción.

        Acepta `conn` opcional para participar de una transacción externa
        (mismo patrón que TransaccionesRepository.crear()/
        DeudasRepository.registrar_pago()). Si no se pasa, comitea solo vía
        self._db.execute().
        """
        sql = """
            INSERT INTO gasto_compartido_pagos
                (gasto_compartido_id, transaccion_id, monto_aplicado_minor,
                 tipo_pago, notas, fecha)
            VALUES (?, ?, ?, ?, ?, ?);
        """
        params = (
            gasto_compartido_id, transaccion_id, monto_aplicado_minor,
            tipo_pago, notas, fecha,
        )
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def listar_por_gasto(self, gasto_compartido_id: int) -> list[sqlite3.Row]:
        """Historial de pagos de un gasto compartido, ordenado por fecha ASC."""
        return self._db.fetchall(
            """
            SELECT * FROM gasto_compartido_pagos
            WHERE gasto_compartido_id = ?
            ORDER BY fecha ASC;
            """,
            (gasto_compartido_id,),
        )
