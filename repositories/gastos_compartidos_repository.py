"""
DeltaBalance — repositories/gastos_compartidos_repository.py

Acceso a datos para la tabla `gastos_compartidos`. Sin lógica de negocio:
no calcula `coeficiente_deuda` ni `monto_adeudado_minor` — ambos ya vienen
resueltos por quien llama (futuro service). No valida `origen_tipo`/
`origen_id` contra la tabla real que corresponda (transacciones/
compras_cuotas/cuotas_credito): el propio schema documenta que esa FK
polimórfica no se puede declarar en SQL y que la integridad referencial
queda a cargo de la capa de servicio.

Confirmado por grep en db/database.py (Fase 2, bloque HOGARES / GASTOS
COMPARTIDOS, paso 1): no existe ningún método relacionado.

`gastos_compartidos` NO tiene columna `deleted_at` — por eso los métodos
de lectura usan QueryBuilder con include_deleted=True hardcodeado, mismo
motivo que en los repositorios anteriores sin esa columna. SÍ tiene
`updated_en` con su propio trigger (trg_gastos_compartidos_updated) —  no
hace falta tocarlo desde acá, SQLite lo actualiza solo en cada UPDATE.

crear() acepta `conn` desde el arranque: un gasto compartido casi siempre
se genera junto con la transacción/cuota de origen (origen_tipo/
origen_id), en la misma operación atómica que el futuro service orqueste.

IMPORTANTE — monto_adeudado_minor PUEDE ser NEGATIVO (ver comentario en
db/schema.sql arriba de esa columna y docs/DATA_MODEL_DECISIONS.md sección
2): si el reintegro de una cuota supera su monto, la deuda se invierte y
es el propio pagador quien termina debiendo. Este repositorio NO valida
signo en ningún método — persiste y devuelve el valor tal cual.

monto_pendiente_minor (Tarea 9, Parte A — docs/PROXIMOS_PASOS.md): columna
agregada vía db/schema_migrations.py, no acá en el CREATE TABLE (misma
razón que el resto de las columnas de esa lista). crear() la inicializa
igual a monto_adeudado_minor; actualizar_monto_pendiente() es el único
punto que la modifica después, y lo hace ya con el valor calculado por
SharedExpensesService.aplicar_pago() — este repositorio nunca resta ni
decide el nuevo estado.
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


class GastoCompartidoRecienDuplicadoError(Exception):
    """
    Se lanza cuando crear() detecta un gasto compartido con los mismos
    campos relevantes (hogar_id, pagador, monto_base_minor, fecha,
    origen_tipo) insertado hace menos de VENTANA_DUPLICADO_SEGUNDOS. Ver
    TransaccionDuplicadaError en transacciones_repository.py — mismo
    criterio exacto (Tarea 9, Parte B).

    Nombre deliberadamente DISTINTO de GastoCompartidoDuplicadoError (esa
    vive en services/shared_expenses_service.py): esa otra excepción
    bloquea por origen_tipo+origen_id EXACTO repetido (regla de negocio —
    "esta transacción/cuota ya se compartió"), sin importar cuándo. Esta de
    acá es una ventana de tiempo corta contra doble-click/doble-Enter en la
    UI, comparando otros campos (nunca origen_id, que si se repitiera ya lo
    atraparía la otra excepción de todos modos).
    """


class GastosCompartidosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    def _existe_duplicado_reciente(
        self,
        hogar_id: int,
        pagador: str,
        monto_base_minor: int,
        fecha: str,
        origen_tipo: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        sql = """
            SELECT 1 FROM gastos_compartidos
            WHERE hogar_id = ? AND pagador = ? AND monto_base_minor = ?
              AND fecha = ? AND origen_tipo = ?
              AND (strftime('%s', 'now') - strftime('%s', creada_en)) < ?
            LIMIT 1;
        """
        params = (hogar_id, pagador, monto_base_minor, fecha, origen_tipo, VENTANA_DUPLICADO_SEGUNDOS)
        ejecutor = conn if conn is not None else self._db.conn
        return ejecutor.execute(sql, params).fetchone() is not None

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        hogar_id: int,
        pagador: str,
        origen_tipo: str,
        origen_id: int,
        categoria_id: int,
        monto_base_minor: int,
        coeficiente_deuda: float,
        monto_adeudado_minor: int,
        fecha: str,
        descripcion: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un gasto compartido. estado arranca en 'pendiente'
        (default de columna). monto_pendiente_minor arranca igual a
        monto_adeudado_minor (mismo signo) — nada se pagó todavía (Tarea 9,
        Parte A: mecanismo de pago parcial, espejo de
        deudas.monto_pendiente_minor).

        monto_adeudado_minor PUEDE ser NEGATIVO (ver docstring del
        módulo) — no se valida signo acá, se persiste tal cual lo que
        recibe. monto_pendiente_minor hereda el mismo signo por la misma
        razón.

        Antes del INSERT, rechaza la operación con
        GastoCompartidoRecienDuplicadoError si ya existe un gasto con el
        mismo hogar_id/pagador/monto_base_minor/fecha/origen_tipo creado
        hace menos de VENTANA_DUPLICADO_SEGUNDOS (Tarea 9, Parte B) — ver
        docstring de esa excepción para la distinción con
        GastoCompartidoDuplicadoError (bloqueo por origen exacto, en el
        service). Este chequeo respeta `conn`: add_shared_purchase() en
        modo 'prorrateado' llama a crear() varias veces dentro de la MISMA
        transacción externa para distintas cuotas — cada cuota tiene su
        propio origen_id pero podría compartir fecha/monto con otra si dos
        cuotas cayeran el mismo día por el mismo monto exacto, así que la
        consulta de duplicado necesita ver el conn en curso (no una
        conexión aparte que todavía no ve los INSERTs sin comitear de esa
        misma transacción).
        """
        if self._existe_duplicado_reciente(
            hogar_id, pagador, monto_base_minor, fecha, origen_tipo, conn=conn,
        ):
            raise GastoCompartidoRecienDuplicadoError(
                f"Ya existe un gasto compartido idéntico (hogar_id={hogar_id}, "
                f"pagador='{pagador}', monto_base_minor={monto_base_minor}, "
                f"fecha={fecha}, origen_tipo='{origen_tipo}') creado hace menos de "
                f"{VENTANA_DUPLICADO_SEGUNDOS} segundos."
            )

        sql = """
            INSERT INTO gastos_compartidos
                (hogar_id, pagador, origen_tipo, origen_id, categoria_id,
                 monto_base_minor, coeficiente_deuda, monto_adeudado_minor,
                 monto_pendiente_minor, fecha, descripcion)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            hogar_id, pagador, origen_tipo, origen_id, categoria_id,
            monto_base_minor, coeficiente_deuda, monto_adeudado_minor,
            monto_adeudado_minor, fecha, descripcion,
        )
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, gasto_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("gastos_compartidos", include_deleted=True)
            .where("id", gasto_id)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_enriquecida(self, gasto_id: int) -> Optional[sqlite3.Row]:
        """
        Igual que obtener_por_id(), pero con el shape enriquecido:
        columnas propias más category_name/categoria_principal vía JOIN a
        categorias — mismo criterio que TransaccionesRepository/
        ComprasCuotasRepository. Sin JOIN a monedas/cuentas: esta tabla no
        tiene moneda_id ni cuenta_id (ver schema).
        """
        return (
            QueryBuilder("gastos_compartidos gc", include_deleted=True)
            .select(
                "gc.*",
                "cat.subcategoria AS category_name",
                "cat.categoria_principal",
            )
            .join("categorias cat", "cat.id = gc.categoria_id")
            .where("gc.id", gasto_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(
        self,
        hogar_id: int,
        estado: Optional[str] = None,
        origen_tipo: Optional[str] = None,
        pagador: Optional[str] = None,
        pagina: int = 1,
        por_pagina: int = 50,
    ) -> list[sqlite3.Row]:
        """Filtros AND-combinados sobre hogar_id (obligatorio) + opcionales. Ordena por fecha DESC."""
        return (
            QueryBuilder("gastos_compartidos", include_deleted=True)
            .where("hogar_id", hogar_id)
            .where("estado", estado)
            .where("origen_tipo", origen_tipo)
            .where("pagador", pagador)
            .order("fecha", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    def listar_enriquecida(
        self,
        hogar_id: int,
        estado: Optional[str] = None,
        origen_tipo: Optional[str] = None,
        pagador: Optional[str] = None,
        pagina: int = 1,
        por_pagina: int = 50,
    ) -> list[sqlite3.Row]:
        """Misma firma de filtros y paginación que listar(), con el shape enriquecido de obtener_enriquecida()."""
        return (
            QueryBuilder("gastos_compartidos gc", include_deleted=True)
            .select(
                "gc.*",
                "cat.subcategoria AS category_name",
                "cat.categoria_principal",
            )
            .join("categorias cat", "cat.id = gc.categoria_id")
            .where("gc.hogar_id", hogar_id)
            .where("gc.estado", estado)
            .where("gc.origen_tipo", origen_tipo)
            .where("gc.pagador", pagador)
            .order("gc.fecha", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    def listar_por_origen(self, origen_tipo: str, origen_id: int) -> list[sqlite3.Row]:
        """
        Para que el futuro service pueda chequear si una transacción/cuota
        específica ya tiene un gasto compartido asociado, antes de crear
        uno duplicado.
        """
        return (
            QueryBuilder("gastos_compartidos", include_deleted=True)
            .where("origen_tipo", origen_tipo)
            .where("origen_id", origen_id)
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        gasto_id: int,
        descripcion: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        Update parcial. Único campo editable hoy: descripcion — el resto
        de los campos de un gasto compartido no deberían editarse
        libremente una vez creado (origen/monto/coeficiente son el
        resultado de un cálculo, no datos sueltos); el estado tiene su
        propia transición dedicada en marcar_saldado().
        """
        if descripcion is NO_CAMBIAR:
            return False
        sql = "UPDATE gastos_compartidos SET descripcion = ? WHERE id = ?;"
        params = (descripcion, gasto_id)
        if conn is not None:
            conn.execute(sql, params)
        else:
            self._db.execute(sql, params)
        return True

    def marcar_saldado(self, gasto_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
        """Transición estado: 'pendiente' -> 'saldado'. Sin validar el estado previo (trabajo del futuro service)."""
        sql = "UPDATE gastos_compartidos SET estado = 'saldado' WHERE id = ?;"
        params = (gasto_id,)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)

    def actualizar_monto_pendiente(
        self,
        gasto_id: int,
        nuevo_monto_pendiente_minor: int,
        nuevo_estado: str,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Actualiza monto_pendiente_minor y estado en un solo UPDATE (Tarea 9,
        Parte A). El valor nuevo y la decisión de si el estado pasa a
        'saldado' ya vienen calculados por quien llama
        (SharedExpensesService.aplicar_pago()) — este repositorio no resta
        ni decide nada, igual que DeudasRepository.registrar_pago() con sus
        parámetros nuevo_monto_pendiente_minor/nuevo_estado.

        Acepta `conn` opcional para participar de una transacción externa
        junto con GastoCompartidoPagosRepository.crear() — ver docstring de
        aplicar_pago() para el detalle de la atomicidad entre ambos
        repositorios.
        """
        sql = "UPDATE gastos_compartidos SET monto_pendiente_minor = ?, estado = ? WHERE id = ?;"
        params = (nuevo_monto_pendiente_minor, nuevo_estado, gasto_id)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)

    # ----------------------------------------------------------
    # ELIMINAR
    # ----------------------------------------------------------

    def eliminar(self, gasto_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
        """
        DELETE físico del gasto compartido (Tarea 9, Parte B — ventana de
        corrección temprana, CLAUDE.md §4). No valida si tiene pagos
        asociados en gasto_compartido_pagos — esa es responsabilidad de
        SharedExpensesService.delete_shared_expense() (regla de negocio, no
        de este repositorio).
        """
        sql = "DELETE FROM gastos_compartidos WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, (gasto_id,))
            return
        self._db.execute(sql, (gasto_id,))

    # ----------------------------------------------------------
    # SALDO NETO (vista)
    # ----------------------------------------------------------

    def obtener_saldo_neto(self, hogar_id: int) -> int:
        """
        Consulta vw_saldo_neto_hogar (SUM(monto_pendiente_minor) — no
        monto_adeudado_minor, cambiado en Tarea 9 Parte A corrección
        posterior, ver comentario de la vista en db/schema.sql — de los
        gastos 'pendiente' de ese hogar). Devuelve 0 si el hogar no tiene
        ningún gasto compartido pendiente (la vista no genera fila para un
        hogar_id sin filas que agrupar).
        """
        fila = self._db.fetchone(
            "SELECT saldo_neto_minor FROM vw_saldo_neto_hogar WHERE hogar_id = ?;",
            (hogar_id,),
        )
        return fila["saldo_neto_minor"] if fila is not None else 0
