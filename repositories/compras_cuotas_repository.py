"""
DeltaBalance — repositories/compras_cuotas_repository.py

Acceso a datos para la tabla `compras_cuotas`. Sin lógica de negocio: no
genera las cuotas asociadas (eso es CuotasCreditoRepository.crear_lote(),
orquestado por services/fees_service.py, que sigue siendo la fuente de
verdad de esas reglas hoy), no valida existencia de cuenta/categoría/
moneda, no decide si una compra "se puede" cancelar.

`compras_cuotas` NO tiene columna `deleted_at` ni `activa` — no existe hoy
soft-delete genérico para esta tabla (el "borrado suave" real es la
transición de estado a 'cancelada' vía cancelar()). Por eso
obtener_por_id()/listar()/obtener_enriquecida()/listar_enriquecida() usan
QueryBuilder con include_deleted=True hardcodeado, igual que
DeudasRepository — mismo motivo (evitar que QueryBuilder inyecte
`deleted_at IS NULL` contra una columna que no existe).

crear()/actualizar() reciben moneda_id y montos ya resueltos en minor units
— resolver moneda_codigo→id y montos float→minor es responsabilidad de
quien llama (hoy FeesService.create_purchase() ya lo hace así).

crear() acepta un `conn` opcional desde el arranque (a diferencia de los
bloques anteriores, acá se sabe de entrada que hace falta): FeesService.
create_purchase() hoy inserta la compra Y llama en el mismo bloque
try/except/commit/rollback a lo que va a ser
CuotasCreditoRepository.crear_lote() para las N cuotas — ambas escrituras
tienen que ser atómicas. El paso 2 va a orquestar create_purchase() abriendo
una transacción externa (self._db.transaction()) y pasando ese mismo conn a
ComprasCuotasRepository.crear() y a CuotasCreditoRepository.crear_lote().

crear() NO setea monto_reintegro_minor ni modo_deuda (columnas agregadas
por db/schema_migrations.py para gastos compartidos) — create_purchase()
hoy tampoco las setea explícitamente en el INSERT, así que quedan en sus
defaults de columna (0 y 'prorrateado' respectivamente). Si en el futuro
hace falta setearlas al crear, se agregan como parámetros opcionales acá.

obtener_enriquecida() y listar_enriquecida() replican deliberadamente una
asimetría que ya existe en el service: get_purchase() trae currency_symbol
además de currency_code, pero list_purchases() NO trae currency_symbol (solo
currency_code). No es un bug que corrija este paso — paso 1 replica
exactamente el comportamiento actual; si se decide unificarlas, es decisión
explícita para el paso 2.

Fuera de alcance a propósito (no son CRUD de una sola tabla):
- `cancelar()` acá SOLO toca `compras_cuotas` (estado→'cancelada' + notas).
  cancel_purchase() en el service HOY TAMBIÉN marca en bloque las
  cuotas_credito 'pendiente' de esa compra como 'omitido' — pero eso vive en
  CuotasCreditoRepository.marcar_estado_por_compra(), no acá, porque un
  repositorio administra una sola tabla/agregado (ver CLAUDE.md, sección 3).
  El paso 2 va a orquestar ambas llamadas con un `conn` compartido, igual
  que create_transfer() orquesta dos TransaccionesRepository.crear().
- `fees_by_month()`/`fees_projection()`: reportes agregados, no CRUD de una
  tabla — se quedan en FeesService.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder
from repositories._sentinels import NO_CAMBIAR

# Ver mismo mecanismo/motivo en repositories/transacciones_repository.py
# (VENTANA_DUPLICADO_SEGUNDOS / TransaccionDuplicadaError) — chequeo de
# seguridad contra doble-click/doble-Enter, no una regla de negocio.
VENTANA_DUPLICADO_SEGUNDOS = 5


class CompraDuplicadaError(Exception):
    """
    Se lanza cuando crear() detecta una compra en cuotas con los mismos
    campos relevantes (cuenta, concepto/comercio, monto, fecha) insertada
    hace menos de VENTANA_DUPLICADO_SEGUNDOS. Ver
    TransaccionDuplicadaError en transacciones_repository.py — mismo
    criterio exacto.
    """


class ComprasCuotasRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    def _existe_duplicado_reciente(
        self,
        fecha_compra: str,
        concepto: str,
        cuenta_id: int,
        monto_total_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        sql = """
            SELECT 1 FROM compras_cuotas
            WHERE cuenta_id = ? AND monto_total_minor = ?
              AND fecha_compra = ? AND concepto = ?
              AND (strftime('%s', 'now') - strftime('%s', creada_en)) < ?
            LIMIT 1;
        """
        params = (cuenta_id, monto_total_minor, fecha_compra, concepto, VENTANA_DUPLICADO_SEGUNDOS)
        ejecutor = conn if conn is not None else self._db.conn
        return ejecutor.execute(sql, params).fetchone() is not None

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        fecha_compra: str,
        concepto: str,
        cuenta_id: int,
        categoria_id: int,
        moneda_id: int,
        monto_total_minor: int,
        total_cuotas: int,
        monto_por_cuota_minor: int,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta una compra en cuotas. estado arranca en 'activa' (default de
        columna, no se setea acá explícitamente — igual que hoy).

        Antes del INSERT, rechaza la operación con CompraDuplicadaError si
        ya existe una compra con la misma cuenta_id/monto_total_minor/
        fecha_compra/concepto creada hace menos de
        VENTANA_DUPLICADO_SEGUNDOS.

        Si se pasa `conn`, el INSERT se ejecuta ahí directamente sin
        comitear, para participar de la transacción externa que también
        inserta el lote de cuotas_credito (ver docstring del módulo).
        """
        if self._existe_duplicado_reciente(
            fecha_compra, concepto, cuenta_id, monto_total_minor, conn=conn,
        ):
            raise CompraDuplicadaError(
                f"Ya existe una compra idéntica (cuenta_id={cuenta_id}, "
                f"monto_total_minor={monto_total_minor}, fecha_compra={fecha_compra}) "
                f"creada hace menos de {VENTANA_DUPLICADO_SEGUNDOS} segundos."
            )

        sql = """
            INSERT INTO compras_cuotas
                (fecha_compra, concepto, cuenta_id, categoria_id, moneda_id,
                 monto_total_minor, total_cuotas, monto_por_cuota_minor, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            fecha_compra, concepto, cuenta_id, categoria_id, moneda_id,
            monto_total_minor, total_cuotas, monto_por_cuota_minor, notas,
        )
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, compra_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("compras_cuotas", include_deleted=True)
            .where("id", compra_id)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_enriquecida(self, compra_id: int) -> Optional[sqlite3.Row]:
        """
        Igual que obtener_por_id(), pero con el shape enriquecido que usa
        FeesService.get_purchase(): columnas propias más account_name,
        category_name, currency_code, currency_symbol y decimales vía JOINs
        a cuentas/categorias/monedas. Réplica exacta de esa query.
        """
        return (
            QueryBuilder("compras_cuotas pc", include_deleted=True)
            .select(
                "pc.*",
                "c.nombre AS account_name",
                "cat.subcategoria AS category_name",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
            )
            .join("cuentas c", "c.id = pc.cuenta_id")
            .join("categorias cat", "cat.id = pc.categoria_id")
            .join("monedas m", "m.id = pc.moneda_id")
            .where("pc.id", compra_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(
        self,
        cuenta_id: Optional[int] = None,
        estado: Optional[str] = None,
        moneda_id: Optional[int] = None,
        pagina: int = 1,
        por_pagina: int = 50,
    ) -> list[sqlite3.Row]:
        """
        Filtros AND-combinados, todos opcionales. Mismo set de filtros que
        FeesService.list_purchases(). Ordena por fecha_compra DESC.
        """
        return (
            QueryBuilder("compras_cuotas", include_deleted=True)
            .where("cuenta_id", cuenta_id)
            .where("estado", estado)
            .where("moneda_id", moneda_id)
            .order("fecha_compra", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    def listar_enriquecida(
        self,
        cuenta_id: Optional[int] = None,
        estado: Optional[str] = None,
        moneda_id: Optional[int] = None,
        pagina: int = 1,
        por_pagina: int = 50,
    ) -> list[sqlite3.Row]:
        """
        Misma firma de filtros y paginación que listar(), pero con el shape
        enriquecido que usa FeesService.list_purchases() — OJO: a
        diferencia de obtener_enriquecida(), acá NO se trae currency_symbol
        (list_purchases() tampoco lo trae hoy; ver docstring del módulo).
        """
        return (
            QueryBuilder("compras_cuotas pc", include_deleted=True)
            .select(
                "pc.*",
                "c.nombre AS account_name",
                "cat.subcategoria AS category_name",
                "m.codigo AS currency_code",
                "m.decimales",
            )
            .join("cuentas c", "c.id = pc.cuenta_id")
            .join("categorias cat", "cat.id = pc.categoria_id")
            .join("monedas m", "m.id = pc.moneda_id")
            .where("pc.cuenta_id", cuenta_id)
            .where("pc.estado", estado)
            .where("pc.moneda_id", moneda_id)
            .order("pc.fecha_compra", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        compra_id: int,
        concepto: Any = NO_CAMBIAR,
        cuenta_id: Any = NO_CAMBIAR,
        categoria_id: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
        monto_total_minor: Any = NO_CAMBIAR,
        total_cuotas: Any = NO_CAMBIAR,
        monto_por_cuota_minor: Any = NO_CAMBIAR,
        estado: Any = NO_CAMBIAR,
        notas: Any = NO_CAMBIAR,
        monto_reintegro_minor: Any = NO_CAMBIAR,
        modo_deuda: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        Update parcial. Default NO_CAMBIAR = no tocar ese campo. Pasar None
        explícito escribe NULL a propósito (solo tiene sentido hoy para
        `notas`, la única columna nullable de esta lista — el resto son
        NOT NULL en el schema). No hay un update() genérico en
        FeesService hoy que use esto; se agrega desde el arranque para el
        paso 2 (edición de compras, ventana de corrección temprana de
        CLAUDE.md sección 4), igual que el resto de los repositorios.
        """
        campos, valores = [], []
        if concepto               is not NO_CAMBIAR: campos.append("concepto = ?");               valores.append(concepto)
        if cuenta_id               is not NO_CAMBIAR: campos.append("cuenta_id = ?");               valores.append(cuenta_id)
        if categoria_id            is not NO_CAMBIAR: campos.append("categoria_id = ?");            valores.append(categoria_id)
        if moneda_id               is not NO_CAMBIAR: campos.append("moneda_id = ?");               valores.append(moneda_id)
        if monto_total_minor       is not NO_CAMBIAR: campos.append("monto_total_minor = ?");       valores.append(monto_total_minor)
        if total_cuotas            is not NO_CAMBIAR: campos.append("total_cuotas = ?");            valores.append(total_cuotas)
        if monto_por_cuota_minor   is not NO_CAMBIAR: campos.append("monto_por_cuota_minor = ?");   valores.append(monto_por_cuota_minor)
        if estado                  is not NO_CAMBIAR: campos.append("estado = ?");                  valores.append(estado)
        if notas                   is not NO_CAMBIAR: campos.append("notas = ?");                   valores.append(notas)
        if monto_reintegro_minor   is not NO_CAMBIAR: campos.append("monto_reintegro_minor = ?");   valores.append(monto_reintegro_minor)
        if modo_deuda              is not NO_CAMBIAR: campos.append("modo_deuda = ?");               valores.append(modo_deuda)
        if not campos:
            return False
        valores.append(compra_id)
        sql = f"UPDATE compras_cuotas SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.execute(sql, tuple(valores))
        return True

    # ----------------------------------------------------------
    # CANCELAR
    # ----------------------------------------------------------

    def cancelar(
        self,
        compra_id: int,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Soft-cancel: estado → 'cancelada', escribe `notas` (reemplaza el
        valor existente por completo, igual que cancel_purchase() hoy — no
        es NO_CAMBIAR condicional).

        Esto es SOLO el lado de compras_cuotas. cancel_purchase() en el
        service también marca en bloque las cuotas_credito 'pendiente' de
        esta compra como 'omitido' — eso es
        CuotasCreditoRepository.marcar_estado_por_compra(), no este método
        (ver docstring del módulo). Acepta `conn` para que el paso 2 pueda
        ejecutar ambas escrituras en la misma transacción externa.
        """
        sql = "UPDATE compras_cuotas SET estado = 'cancelada', notas = ? WHERE id = ?;"
        params = (notas, compra_id)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
