"""
DeltaBalance — repositories/ingresos_proyectados_repository.py

Acceso a datos para la tabla `ingresos_proyectados`. Sin lógica de negocio:
no decide cuándo un ingreso pasa de 'pendiente' a 'cobrado'/'parcial', no
valida que monto_percibido_minor sea coherente con monto_estimado_minor —
eso vive en un futuro service (Fase 2, paso 2, todavía no existe).

Hallazgo de la revisión previa a este repositorio (Fase 2, bloque
PRESUPUESTOS / INGRESOS PROYECTADOS / EMPLEOS, paso 1): `db/database.py` NO
tiene NINGÚN método para `ingresos_proyectados` — ni crear, ni leer, ni
actualizar. No hay comportamiento real que replicar 1:1 acá (a diferencia
de PresupuestosRepository/EmpleosRepository, que sí replican métodos
existentes) — el diseño de este repositorio se basa únicamente en el schema
(tabla + CHECK de estado) y en el mismo patrón ya usado en los demás
repositorios de esta fase (NO_CAMBIAR, transición de estado explícita
separada de actualizar()). Ningún service existente lo usa hoy (confirmado
por grep en transaction_service.py/debts_service.py/fees_service.py).

Por eso, a diferencia de otros repositorios de fases anteriores, NO se
agregan obtener_enriquecida()/listar_enriquecida(): no hay ningún JOIN real
que replicar (ninguna query existente los usa) y agregar uno ahora sería
anticiparse a una necesidad no confirmada por ningún caller — si un futuro
service necesita un shape enriquecido (ej. JOIN a monedas), se agrega en
ese momento con su propio criterio.

`ingresos_proyectados` NO tiene columna `deleted_at` — por eso
obtener_por_id()/listar_por_periodo() usan QueryBuilder con
include_deleted=True hardcodeado, mismo motivo que en los repositorios
anteriores sin esa columna.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder
from repositories._sentinels import NO_CAMBIAR


class IngresosProyectadosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        concepto: str,
        mes: int,
        anio: int,
        monto_estimado_minor: int,
        moneda_id: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un ingreso proyectado. estado arranca en 'pendiente' y
        monto_percibido_minor en 0 (defaults de columna, no se setean
        explícito acá).
        """
        sql = """
            INSERT INTO ingresos_proyectados (concepto, mes, anio, monto_estimado_minor, moneda_id)
            VALUES (?, ?, ?, ?, ?);
        """
        params = (concepto, mes, anio, monto_estimado_minor, moneda_id)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, ingreso_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("ingresos_proyectados", include_deleted=True)
            .where("id", ingreso_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar_por_periodo(self, mes: int, anio: int) -> list[sqlite3.Row]:
        return (
            QueryBuilder("ingresos_proyectados", include_deleted=True)
            .where("mes", mes)
            .where("anio", anio)
            .order("concepto")
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        ingreso_id: int,
        concepto: Any = NO_CAMBIAR,
        mes: Any = NO_CAMBIAR,
        anio: Any = NO_CAMBIAR,
        monto_estimado_minor: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        Update parcial de los campos "de definición" del ingreso proyectado.
        Default NO_CAMBIAR = no tocar ese campo. NO incluye estado ni
        monto_percibido_minor a propósito — esos dos son de transición
        exclusiva de marcar_estado(), igual que DeudasRepository separa
        actualizar() de registrar_pago()/write_off() para los campos de
        estado/saldo.
        """
        campos, valores = [], []
        if concepto              is not NO_CAMBIAR: campos.append("concepto = ?");              valores.append(concepto)
        if mes                   is not NO_CAMBIAR: campos.append("mes = ?");                   valores.append(mes)
        if anio                  is not NO_CAMBIAR: campos.append("anio = ?");                  valores.append(anio)
        if monto_estimado_minor  is not NO_CAMBIAR: campos.append("monto_estimado_minor = ?");  valores.append(monto_estimado_minor)
        if moneda_id             is not NO_CAMBIAR: campos.append("moneda_id = ?");             valores.append(moneda_id)
        if not campos:
            return False
        valores.append(ingreso_id)
        sql = f"UPDATE ingresos_proyectados SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.conn.execute(sql, tuple(valores))
            self._db.conn.commit()
        return True

    # ----------------------------------------------------------
    # TRANSICIÓN DE ESTADO
    # ----------------------------------------------------------

    def marcar_estado(
        self,
        ingreso_id: int,
        nuevo_estado: str,
        monto_percibido_minor: Optional[int] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Transición pendiente/cobrado/parcial. monto_percibido_minor es
        opcional: si se pasa, se escribe junto con el estado en el mismo
        UPDATE; si no, solo se toca `estado` (ej. para un 'cancelado'-style
        futuro sin monto asociado). No valida que nuevo_estado sea uno de
        los valores del CHECK — eso lo hace SQLite a nivel de schema; este
        repositorio no valida reglas de negocio.
        """
        if monto_percibido_minor is not None:
            sql = "UPDATE ingresos_proyectados SET estado = ?, monto_percibido_minor = ? WHERE id = ?;"
            params = (nuevo_estado, monto_percibido_minor, ingreso_id)
        else:
            sql = "UPDATE ingresos_proyectados SET estado = ? WHERE id = ?;"
            params = (nuevo_estado, ingreso_id)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.conn.execute(sql, params)
        self._db.conn.commit()
