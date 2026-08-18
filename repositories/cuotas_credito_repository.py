"""
DeltaBalance — repositories/cuotas_credito_repository.py

Acceso a datos para la tabla `cuotas_credito`. Sin lógica de negocio: no
decide si una cuota "puede" pasar de un estado a otro, no calcula
mes/año proyectado (eso lo hace FeesService.create_purchase() al armar el
lote), no valida existencia de la compra ni del resumen.

Transiciones de estado reales (relevadas en services/fees_service.py antes
de escribir este repositorio — Fase 2, COMPRAS_CUOTAS paso 1):
    pendiente → en_resumen   dispara FeesService.confirm_fee()
    en_resumen → pagado      dispara FeesService.pay_statement() (en BLOQUE,
                              todas las cuotas de un resumen a la vez, no
                              una por una)
    pendiente → omitido      dispara FeesService.cancel_purchase() (en
                              BLOQUE, todas las cuotas pendientes de una
                              compra a la vez)
'omitido' SÍ está en uso real (cancel_purchase()) — no es un estado muerto
del CHECK. No existe ninguna transición hacia 'completada' a nivel de
cuota (esa palabra no aparece en cuotas_credito, solo en compras_cuotas —
y ni siquiera compras_cuotas.estado='completada' se dispara desde ningún
método hoy; queda sin usar).

Por eso este repositorio expone TRES métodos de transición en vez de un
único marcar_estado() genérico:
    - marcar_estado(): una sola cuota por id — cubre confirm_fee(), que
      necesita además resumen_id/mes_real_pago/anio_real_pago/notas en la
      misma escritura (no solo el estado).
    - marcar_estado_por_resumen(): BLOQUE por resumen_id — cubre
      pay_statement(), que actualiza todas las cuotas 'en_resumen' de un
      resumen a 'pagado' de una sola vez (no fila por fila).
    - marcar_estado_por_compra(): BLOQUE por compra_id — cubre
      cancel_purchase(), que actualiza todas las cuotas 'pendiente' de una
      compra a 'omitido' de una sola vez, con una única `notas` para todas.
Los tres aceptan `conn` opcional porque las tres transiciones reales son la
mitad de una escritura atómica multi-tabla (la otra mitad toca
resumenes_tarjeta o compras_cuotas — ver ResumenesTarjetaRepository /
ComprasCuotasRepository).

No hay un actualizar() genérico con NO_CAMBIAR acá: no existe en
FeesService ningún método que edite libremente cuotas_credito más allá de
estas tres transiciones puntuales — agregar uno ahora sería anticiparse a
una necesidad que no está confirmada (CLAUDE.md: no inventar convenciones
que no estén respaldadas por el código real).
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager


class CuotasCreditoRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE (lote)
    # ----------------------------------------------------------

    def crear_lote(
        self,
        compra_id: int,
        cuotas: list[dict],
        conn: Optional[sqlite3.Connection] = None,
    ) -> list[int]:
        """
        Inserta las N cuotas de una compra de una sola vez. Cada elemento de
        `cuotas` es un dict con las claves: numero_cuota, mes_proyectado,
        anio_proyectado, monto_cuota_minor. Devuelve la lista de ids
        insertados, en el mismo orden que `cuotas`.

        Si se pasa `conn`, participa de la transacción externa que también
        inserta la compra en ComprasCuotasRepository.crear() (ver docstring
        de ese módulo) — ni comitea ni abre su propia transacción. Si no se
        pasa, cada INSERT comitea individualmente vía self._db.execute()
        (comportamiento standalone, sin garantía de atomicidad entre filas
        — para atomicidad real, pasar conn desde una
        self._db.transaction() externa).
        """
        sql = """
            INSERT INTO cuotas_credito
                (compra_id, numero_cuota, mes_proyectado, anio_proyectado, monto_cuota_minor)
            VALUES (?, ?, ?, ?, ?);
        """
        ids = []
        for cuota in cuotas:
            params = (
                compra_id,
                cuota["numero_cuota"],
                cuota["mes_proyectado"],
                cuota["anio_proyectado"],
                cuota["monto_cuota_minor"],
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
        return self._db.fetchone("SELECT * FROM cuotas_credito WHERE id = ?;", (cuota_id,))

    def listar_por_compra(self, compra_id: int) -> list[sqlite3.Row]:
        """
        Réplica exacta de FeesService.get_fees_for_purchase(): todas las
        cuotas de una compra, ordenadas por numero_cuota ASC. Sin JOINs.
        """
        return self._db.fetchall(
            """
            SELECT * FROM cuotas_credito
            WHERE compra_id = ?
            ORDER BY numero_cuota ASC;
            """,
            (compra_id,),
        )

    def listar_por_mes(
        self,
        mes: int,
        anio: int,
        estado: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        """
        Filas planas de cuotas_credito para un mes/año proyectado, sin JOINs
        ni agregación — el building block de listado que
        FeesService.fees_by_month() necesitaría si migrara. fees_by_month()
        en sí es un reporte agregado (GROUP BY cuenta+moneda, con JOINs a
        compras_cuotas/cuentas/monedas) y se queda fuera de este
        repositorio, mismo criterio que summary_by_person()/
        monthly_summary() en los bloques anteriores.
        """
        sql = "SELECT * FROM cuotas_credito WHERE mes_proyectado = ? AND anio_proyectado = ?"
        params: list = [mes, anio]
        if estado is not None:
            sql += " AND estado = ?"
            params.append(estado)
        sql += " ORDER BY compra_id, numero_cuota ASC;"
        return self._db.fetchall(sql, tuple(params))

    # ----------------------------------------------------------
    # TRANSICIONES DE ESTADO
    # ----------------------------------------------------------

    def marcar_estado(
        self,
        cuota_id: int,
        nuevo_estado: str,
        resumen_id: Optional[int] = None,
        mes_real_pago: Optional[int] = None,
        anio_real_pago: Optional[int] = None,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Actualiza una sola cuota por id. Cubre confirm_fee() (pendiente →
        en_resumen), que necesita escribir resumen_id/mes_real_pago/
        anio_real_pago/notas en la misma operación, no solo el estado.

        Los parámetros opcionales que se dejan en None simplemente no se
        incluyen en el UPDATE (se omiten, comportamiento "no tocar" — no es
        el sentinel NO_CAMBIAR de otros repositorios, porque acá None
        también es el valor real por default en el schema para estas
        columnas al crear una cuota, así que no hay ambigüedad que resolver
        con un sentinel: nunca hace falta "poner resumen_id en NULL a
        propósito" desde este método).
        """
        campos = ["estado = ?"]
        valores: list = [nuevo_estado]
        if resumen_id is not None:
            campos.append("resumen_id = ?")
            valores.append(resumen_id)
        if mes_real_pago is not None:
            campos.append("mes_real_pago = ?")
            valores.append(mes_real_pago)
        if anio_real_pago is not None:
            campos.append("anio_real_pago = ?")
            valores.append(anio_real_pago)
        if notas is not None:
            campos.append("notas = ?")
            valores.append(notas)
        valores.append(cuota_id)
        sql = f"UPDATE cuotas_credito SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.execute(sql, tuple(valores))

    def marcar_estado_por_resumen(
        self,
        resumen_id: int,
        estado_actual: str,
        nuevo_estado: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Transición en BLOQUE: todas las cuotas de `resumen_id` que estén en
        `estado_actual` pasan a `nuevo_estado`. Cubre pay_statement()
        (en_resumen → pagado para todo un resumen de una sola vez). Devuelve
        la cantidad de filas afectadas.

        No usa self._db.execute() para el caso standalone (conn=None): esa
        función devuelve `cur.lastrowid or cur.rowcount`, y en sqlite3
        cursor.lastrowid NO es None/0 después de un UPDATE — retiene el
        rowid de la última fila INSERTada en la conexión (stale, pero
        truthy), así que `or` nunca llega a devolver el rowcount real. Acá
        se ejecuta directo contra self._db.conn y se lee cur.rowcount, que
        sí es confiable para UPDATE/DELETE.
        """
        sql = "UPDATE cuotas_credito SET estado = ? WHERE resumen_id = ? AND estado = ?;"
        params = (nuevo_estado, resumen_id, estado_actual)
        if conn is not None:
            return conn.execute(sql, params).rowcount
        cur = self._db.conn.execute(sql, params)
        self._db.conn.commit()
        return cur.rowcount

    def marcar_estado_por_compra(
        self,
        compra_id: int,
        estado_actual: str,
        nuevo_estado: str,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Transición en BLOQUE: todas las cuotas de `compra_id` que estén en
        `estado_actual` pasan a `nuevo_estado`, con una única `notas` para
        todas las filas afectadas. Cubre cancel_purchase() (pendiente →
        omitido para toda una compra de una sola vez). Devuelve la cantidad
        de filas afectadas.

        Mismo motivo que marcar_estado_por_resumen() para no usar
        self._db.execute() en el caso standalone: cur.lastrowid después de
        un UPDATE no es None/0, así que `cur.lastrowid or cur.rowcount`
        nunca devuelve el rowcount real. Se ejecuta directo contra
        self._db.conn y se lee cur.rowcount.
        """
        sql = (
            "UPDATE cuotas_credito SET estado = ?, notas = ? "
            "WHERE compra_id = ? AND estado = ?;"
        )
        params = (nuevo_estado, notas, compra_id, estado_actual)
        if conn is not None:
            return conn.execute(sql, params).rowcount
        cur = self._db.conn.execute(sql, params)
        self._db.conn.commit()
        return cur.rowcount
