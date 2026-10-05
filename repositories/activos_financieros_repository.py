"""
DeltaBalance — repositories/activos_financieros_repository.py

Acceso a datos para la tabla `activos_financieros`. Sin lógica de negocio:
no decide qué activos "deberían" existir, no calcula ninguna cotización ni
rendimiento — eso vive en un futuro SavingsService (Fase 2, bloque
AHORROS, paso 2, todavía no existe).

Confirmado por grep en db/database.py (Fase 2, bloque AHORROS, paso 1):
NO existe ningún método relacionado con activos_financieros/
movimientos_activo/objetivos_ahorro/asignaciones en el código legacy — las
cuatro tablas se agregaron directo al schema en una fase de diseño previa
(ver docs/DATA_MODEL_DECISIONS.md sección 4) sin pasar nunca por
database.py. Por eso no hay comentario de deprecación que agregar ahí: no
hay nada que deprecar.

`activos_financieros` NO tiene columna `deleted_at` — por eso
obtener_por_id()/listar() usan QueryBuilder con include_deleted=True
hardcodeado, mismo motivo que en los repositorios anteriores sin esa
columna. SÍ tiene `activa` (como `cuentas`/`categorias`), así que el
soft-delete sigue el mismo patrón: desactivar()/activar() son métodos
dedicados, separados de actualizar() — actualizar() NUNCA toca `activa`.

Rediseño de Ahorros e Inversiones: crear()/actualizar() reciben broker_id
(brokers) y las comisiones por defecto del activo (comision_compra_minor /
comision_venta_minor, columnas de db/schema_migrations.py);
listar_por_tipo()/listar_por_broker() para las pestañas de la pantalla.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id
from db.query_builder import QueryBuilder
from repositories._sentinels import NO_CAMBIAR


class ActivosFinancierosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        nombre: str,
        tipo: str,
        moneda_id: int,
        cuenta_id: Optional[str] = None,
        broker_id: Optional[str] = None,
        comision_compra_minor: int = 0,
        comision_venta_minor: int = 0,
    ) -> str:
        """
        Inserta un activo financiero y devuelve su id (UUID,
        repositories/_ids.py). activa arranca en 1 (default de
        columna). cuenta_id (Tarea 6g, docs/PROXIMOS_PASOS.md) vincula el
        activo a una cuenta real — opcional, nullable en schema — usado
        por SavingsService.register_purchase()/register_sale() para
        resolver sola la cuenta a descontar/acreditar sin que el caller
        tenga que pasarla en cada movimiento. broker_id: dónde se opera
        (opcional); comisiones: las de compra/venta por defecto del activo.
        """
        activo_id = nuevo_id()
        self._db.execute(
            """
            INSERT INTO activos_financieros
                (id, nombre, tipo, moneda_id, cuenta_id, broker_id, comision_compra_minor, comision_venta_minor)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (activo_id, nombre, tipo, moneda_id, cuenta_id, broker_id, comision_compra_minor, comision_venta_minor),
        )
        return activo_id

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, activo_id: str) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("activos_financieros", include_deleted=True)
            .where("id", activo_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(self, tipo: Optional[str] = None, solo_activos: bool = True) -> list[sqlite3.Row]:
        """Filtros AND-combinados. solo_activos=True filtra activa=1 (default)."""
        builder = QueryBuilder("activos_financieros", include_deleted=True).where("tipo", tipo)
        if solo_activos:
            builder = builder.where("activa", 1)
        return builder.order("nombre").ejecutar(self._db.conn)

    def listar_por_tipo(self, tipo: str) -> list[sqlite3.Row]:
        """Los activos activos de ese tipo, por nombre."""
        return self.listar(tipo=tipo)

    def listar_por_broker(self, broker_id: str) -> list[sqlite3.Row]:
        """Los activos activos operados en ese broker, por nombre."""
        return (
            QueryBuilder("activos_financieros", include_deleted=True)
            .where("broker_id", broker_id)
            .where("activa", 1)
            .order("nombre")
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        activo_id: str,
        nombre: Any = NO_CAMBIAR,
        tipo: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
        broker_id: Any = NO_CAMBIAR,
        comision_compra_minor: Any = NO_CAMBIAR,
        comision_venta_minor: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
        cuenta_id: Any = NO_CAMBIAR,
    ) -> bool:
        """
        Update parcial de nombre/tipo/moneda_id/broker_id/comisiones/
        cuenta_id. Default NO_CAMBIAR = no tocar ese campo (None en
        broker_id / cuenta_id lo desvincula). NO incluye `activa` a
        propósito — eso es transición exclusiva de desactivar()/activar(),
        mismo patrón que CategoriasRepository.
        """
        campos, valores = [], []
        if nombre    is not NO_CAMBIAR: campos.append("nombre = ?");    valores.append(nombre)
        if tipo      is not NO_CAMBIAR: campos.append("tipo = ?");      valores.append(tipo)
        if moneda_id is not NO_CAMBIAR: campos.append("moneda_id = ?"); valores.append(moneda_id)
        if broker_id is not NO_CAMBIAR: campos.append("broker_id = ?"); valores.append(broker_id)
        if cuenta_id is not NO_CAMBIAR: campos.append("cuenta_id = ?"); valores.append(cuenta_id)
        if comision_compra_minor is not NO_CAMBIAR:
            campos.append("comision_compra_minor = ?")
            valores.append(comision_compra_minor)
        if comision_venta_minor is not NO_CAMBIAR:
            campos.append("comision_venta_minor = ?")
            valores.append(comision_venta_minor)
        if not campos:
            return False
        valores.append(activo_id)
        sql = f"UPDATE activos_financieros SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.execute(sql, tuple(valores))
        return True

    # ----------------------------------------------------------
    # SOFT-DELETE
    # ----------------------------------------------------------

    def desactivar(self, activo_id: str) -> None:
        """Soft-delete: activa = 0. No valida si el activo tiene movimientos asociados."""
        self._db.execute("UPDATE activos_financieros SET activa = 0 WHERE id = ?;", (activo_id,))

    def activar(self, activo_id: str) -> None:
        self._db.execute("UPDATE activos_financieros SET activa = 1 WHERE id = ?;", (activo_id,))

    # ----------------------------------------------------------
    # DELETE
    # ----------------------------------------------------------

    def eliminar(self, activo_id: str, conn: Optional[sqlite3.Connection] = None) -> None:
        """
        DELETE físico. Las FK de movimientos_activo / activo_objetivos lo
        rechazan si quedan filas que lo referencian: quién puede borrarse
        (y qué se borra antes, en la misma transacción `conn`) lo decide
        SavingsService.delete_activo().
        """
        sql = "DELETE FROM activos_financieros WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, (activo_id,))
        else:
            self._db.execute(sql, (activo_id,))
