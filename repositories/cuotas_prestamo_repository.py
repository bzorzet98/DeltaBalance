"""
DeltaBalance — repositories/cuotas_prestamo_repository.py

Acceso a datos para la tabla `cuotas_prestamo`. Sin lógica de negocio: NO
calcula el cronograma de amortización (francesa/alemana) — el propio
comentario en db/schema.sql arriba de CREATE TABLE cuotas_prestamo lo dice
explícitamente: "el cálculo de amortización... es responsabilidad de la
capa de servicio (LoansService, fase futura) — el schema solo almacena el
resultado ya calculado, nunca calcula nada".

Confirmado por grep en db/database.py (Fase 2, bloque PRESTAMOS, paso 1):
no existe ningún método relacionado.

crear_lote() sigue el mismo patrón que
CuotasCreditoRepository.crear_lote() del bloque COMPRAS_CUOTAS: inserta N
cuotas de una sola vez, participando de la misma transacción externa que
PrestamosRepository.crear() cuando un futuro LoansService genere el
préstamo con todo su cronograma completo de una vez (ver
docs/DATA_MODEL_DECISIONS.md sección 5: "cuotas_prestamo se genera
completa en el alta").

actualizar() (Fase 2, bloque PRESTAMOS, paso 1b) SÍ existe para los tres
montos de una cuota individual — caso de uso confirmado: reconciliar la
cuota estimada por _generar_tabla_amortizacion() (LoansService, paso 2)
contra lo que el banco realmente cobra ese mes, sin tocar las demás
cuotas. Usa el sentinel NO_CAMBIAR, mismo patrón que el resto del
proyecto.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._sentinels import NO_CAMBIAR


class CuotasPrestamoRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE (lote)
    # ----------------------------------------------------------

    def crear_lote(
        self,
        prestamo_id: int,
        cuotas: list[dict],
        conn: Optional[sqlite3.Connection] = None,
    ) -> list[int]:
        """
        Inserta las N cuotas de un préstamo de una sola vez. Cada elemento
        de `cuotas` es un dict con las claves: numero_cuota,
        monto_capital_minor, monto_interes_minor, monto_total_minor, mes,
        anio. Devuelve la lista de ids insertados, en el mismo orden que
        `cuotas`.

        Si se pasa `conn`, participa de la transacción externa que también
        inserta el préstamo en PrestamosRepository.crear() (ver docstring
        del módulo) — ni comitea ni abre su propia transacción. Si no se
        pasa, cada INSERT comitea individualmente vía self._db.execute()
        (comportamiento standalone, sin garantía de atomicidad entre filas
        — para atomicidad real, pasar conn desde una self._db.transaction()
        externa).
        """
        sql = """
            INSERT INTO cuotas_prestamo
                (prestamo_id, numero_cuota, mes, anio,
                 monto_capital_minor, monto_interes_minor, monto_total_minor)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        ids = []
        for cuota in cuotas:
            params = (
                prestamo_id,
                cuota["numero_cuota"],
                cuota["mes"],
                cuota["anio"],
                cuota["monto_capital_minor"],
                cuota["monto_interes_minor"],
                cuota["monto_total_minor"],
            )
            if conn is not None:
                ids.append(conn.execute(sql, params).lastrowid)
            else:
                ids.append(self._db.execute(sql, params))
        return ids

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, cuota_id: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM cuotas_prestamo WHERE id = ?;", (cuota_id,))

    def listar_por_prestamo(self, prestamo_id: int) -> list[sqlite3.Row]:
        """Todas las cuotas de un préstamo, ordenadas por numero_cuota ASC."""
        return self._db.fetchall(
            "SELECT * FROM cuotas_prestamo WHERE prestamo_id = ? ORDER BY numero_cuota ASC;",
            (prestamo_id,),
        )

    def listar_por_mes(self, mes: int, anio: int, estado: Optional[str] = None) -> list[sqlite3.Row]:
        """
        Mismo criterio que CuotasCreditoRepository.listar_por_mes(): filas
        planas de cuotas_prestamo para un mes/año, sin JOINs ni agregación.
        """
        sql = "SELECT * FROM cuotas_prestamo WHERE mes = ? AND anio = ?"
        params: list = [mes, anio]
        if estado is not None:
            sql += " AND estado = ?"
            params.append(estado)
        sql += " ORDER BY prestamo_id, numero_cuota ASC;"
        return self._db.fetchall(sql, tuple(params))

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        cuota_id: int,
        monto_capital_minor: Any = NO_CAMBIAR,
        monto_interes_minor: Any = NO_CAMBIAR,
        monto_total_minor: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        Update parcial de los tres montos de una cuota individual.
        Default NO_CAMBIAR = no tocar ese campo. Pensado para reconciliar
        la cuota estimada contra lo que el banco realmente cobra, cuota a
        cuota — no fuerza que capital+interes sumen total, esa coherencia
        (si hace falta) es responsabilidad de quien llama.
        """
        campos, valores = [], []
        if monto_capital_minor is not NO_CAMBIAR: campos.append("monto_capital_minor = ?"); valores.append(monto_capital_minor)
        if monto_interes_minor is not NO_CAMBIAR: campos.append("monto_interes_minor = ?"); valores.append(monto_interes_minor)
        if monto_total_minor   is not NO_CAMBIAR: campos.append("monto_total_minor = ?");   valores.append(monto_total_minor)
        if not campos:
            return False
        valores.append(cuota_id)
        sql = f"UPDATE cuotas_prestamo SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.execute(sql, tuple(valores))
        return True

    # ----------------------------------------------------------
    # MARCAR PAGADA
    # ----------------------------------------------------------

    def marcar_pagada(
        self, cuota_id: int, fecha_pago: str, conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Transición: 'pendiente' -> 'pagado', escribe fecha_pago. Sin validar el estado previo (trabajo del futuro service)."""
        sql = "UPDATE cuotas_prestamo SET estado = 'pagado', fecha_pago = ? WHERE id = ?;"
        params = (fecha_pago, cuota_id)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
