"""
DeltaBalance — repositories/deudas_repository.py

Acceso a datos para las tablas `deudas` y `deuda_pagos`. Sin lógica de
negocio: no valida tipo, no decide si una deuda está "activa" antes de
tocarla, no calcula el nuevo saldo pendiente — todo eso vive en
services/debts_service.py (DebtsService), que sigue siendo la fuente de
verdad de esas reglas hoy y que en un paso posterior va a migrar para usar
este repositorio en vez de escribir SQL directo.

Por el mismo motivo, crear()/registrar_pago() reciben moneda_id y montos ya
resueltos en minor units (no moneda_codigo ni montos en float) — resolver
un código de moneda a id, convertir a minor units, y calcular el nuevo
monto_pendiente_minor/estado es responsabilidad de quien llama.

`deudas` NO tiene columna `deleted_at` (a diferencia de `transacciones`) ni
columna `activa` (a diferencia de `cuentas`/`categorias`) — no existe hoy
ningún soft-delete para deudas. Por eso obtener_por_id()/listar()/
obtener_enriquecida()/listar_enriquecida() usan QueryBuilder con
include_deleted=True hardcodeado (nunca parametrizado): pasar False haría
que QueryBuilder inyecte `deleted_at IS NULL` en el WHERE, y esa columna no
existe en esta tabla — explotaría con "no such column: deleted_at". Mismo
motivo por el que CuentasRepository/CategoriasRepository evitan el default
de QueryBuilder.

Lecciones aplicadas desde el arranque (post mortem del bloque TRANSACCIONES,
Fase 2 paso 3, para no repetir dejar cosas a medias):
- registrar_pago() y write_off() aceptan un `conn` opcional desde el
  arranque, para poder participar de una transacción externa cuando el
  service lo necesite.
- actualizar() usa el sentinel NO_CAMBIAR (repositories/_sentinels.py) desde
  el arranque, para distinguir "no tocar" de "escribir NULL a propósito".
- obtener_enriquecida()/listar_enriquecida() existen desde el arranque,
  replicando exactamente los JOINs que DebtsService.get()/list_debts() usan
  hoy (JOIN a monedas para currency_code/currency_symbol/decimales) — no se
  dejan para un paso 2.

Fuera de alcance a propósito (no son CRUD de una sola tabla/agregado):
- `summary_by_person`: reporte agregado (GROUP BY persona+tipo), no un
  listado de filas — se deja en DebtsService.
- `get_payments`: hace LEFT JOIN con `transacciones` para traer
  transaction_date; no se pidió en este paso — si se necesita, se agrega en
  un paso posterior con su propio método (ej. listar_pagos()).

Fix del bug concept/notes (Fase 2, DEUDAS paso 2): `deudas` no tenía columna
`concepto` — DebtsService.update() mapeaba tanto `concept` como `notes` a la
misma columna `notas`, así que pasar ambos en la misma llamada hacía que uno
pisara al otro. La migración en db/schema_migrations.py agrega la columna
`concepto` (ver MIGRACIONES_COLUMNA); crear()/actualizar() acá ya la tratan
como una columna independiente de `notas`. obtener_por_id()/listar() hacen
`SELECT *`/`d.*` así que ya la traen sin cambios de código.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder
from repositories._sentinels import NO_CAMBIAR


class DeudasRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        entidad_persona: str,
        tipo: str,
        monto_minor: int,
        moneda_id: int,
        fecha_inicio: str,
        fecha_vencimiento: Optional[str] = None,
        origen_tipo: str = "manual",
        origen_id: Optional[int] = None,
        notas: Optional[str] = None,
        concepto: Optional[str] = None,
    ) -> int:
        """
        Inserta una deuda. monto_original_minor y monto_pendiente_minor
        arrancan iguales (recién creada, nada pagado todavía).

        concepto y notas son columnas separadas (Fase 2, DEUDAS paso 2 — fix
        del bug donde antes se pisaban en actualizar()): concepto es la
        descripción corta de la deuda (equivalente a
        transacciones.concepto), notas es memo/motivo libre.
        """
        return self._db.execute(
            """
            INSERT INTO deudas
                (entidad_persona, tipo, monto_original_minor, monto_pendiente_minor,
                 moneda_id, fecha_inicio, fecha_vencimiento, origen_tipo, origen_id,
                 notas, concepto)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                entidad_persona, tipo, monto_minor, monto_minor,
                moneda_id, fecha_inicio, fecha_vencimiento, origen_tipo, origen_id,
                notas, concepto,
            ),
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, deuda_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("deudas", include_deleted=True)
            .where("id", deuda_id)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_enriquecida(self, deuda_id: int) -> Optional[sqlite3.Row]:
        """
        Igual que obtener_por_id(), pero con el shape enriquecido que usa
        DebtsService.get(): columnas propias de deudas más currency_code,
        currency_symbol y decimales vía JOIN a monedas. Réplica exacta de
        esa query.
        """
        return (
            QueryBuilder("deudas d", include_deleted=True)
            .select(
                "d.*",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
            )
            .join("monedas m", "m.id = d.moneda_id")
            .where("d.id", deuda_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(
        self,
        entidad_persona: Optional[str] = None,
        tipo: Optional[str] = None,
        estado: Optional[str] = None,
        moneda_id: Optional[int] = None,
        origen_tipo: Optional[str] = None,
        pagina: int = 1,
        por_pagina: int = 50,
    ) -> list[sqlite3.Row]:
        """
        Filtros AND-combinados, todos opcionales. Mismo set de filtros que
        DebtsService.list_debts() (la fuente de verdad actual). Ordena por
        fecha_inicio DESC — igual que hoy.
        """
        return (
            QueryBuilder("deudas", include_deleted=True)
            .where("entidad_persona", entidad_persona)
            .where("tipo", tipo)
            .where("estado", estado)
            .where("moneda_id", moneda_id)
            .where("origen_tipo", origen_tipo)
            .order("fecha_inicio", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    def listar_enriquecida(
        self,
        entidad_persona: Optional[str] = None,
        tipo: Optional[str] = None,
        estado: Optional[str] = None,
        moneda_id: Optional[int] = None,
        origen_tipo: Optional[str] = None,
        pagina: int = 1,
        por_pagina: int = 50,
    ) -> list[sqlite3.Row]:
        """
        Misma firma de filtros y paginación que listar(), pero con el shape
        enriquecido (JOIN a monedas) que usa DebtsService.list_debts()/
        list_active(). Réplica exacta de esa query.
        """
        return (
            QueryBuilder("deudas d", include_deleted=True)
            .select(
                "d.*",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
            )
            .join("monedas m", "m.id = d.moneda_id")
            .where("d.entidad_persona", entidad_persona)
            .where("d.tipo", tipo)
            .where("d.estado", estado)
            .where("d.moneda_id", moneda_id)
            .where("d.origen_tipo", origen_tipo)
            .order("d.fecha_inicio", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        deuda_id: int,
        entidad_persona: Any = NO_CAMBIAR,
        tipo: Any = NO_CAMBIAR,
        monto_original_minor: Any = NO_CAMBIAR,
        monto_pendiente_minor: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
        fecha_inicio: Any = NO_CAMBIAR,
        fecha_vencimiento: Any = NO_CAMBIAR,
        estado: Any = NO_CAMBIAR,
        origen_tipo: Any = NO_CAMBIAR,
        origen_id: Any = NO_CAMBIAR,
        notas: Any = NO_CAMBIAR,
        concepto: Any = NO_CAMBIAR,
    ) -> bool:
        """
        Update parcial. Default NO_CAMBIAR = no tocar ese campo (se omite
        del UPDATE). Pasar None explícito escribe NULL a propósito en ese
        campo (ej. fecha_vencimiento=None para quitar un vencimiento, o
        notas=None para borrarlas) — igual que
        TransaccionesRepository.actualizar().

        concepto y notas son parámetros separados que escriben a columnas
        separadas (Fase 2, DEUDAS paso 2): antes de este fix, DebtsService.
        update() mapeaba ambos a la columna `notas`, así que pasar los dos
        en la misma llamada hacía que uno pisara al otro. Acá cada uno tiene
        su propio NO_CAMBIAR independiente — no hay forma de que se pisen.
        """
        campos, valores = [], []
        if entidad_persona       is not NO_CAMBIAR: campos.append("entidad_persona = ?");       valores.append(entidad_persona)
        if tipo                  is not NO_CAMBIAR: campos.append("tipo = ?");                  valores.append(tipo)
        if monto_original_minor  is not NO_CAMBIAR: campos.append("monto_original_minor = ?");  valores.append(monto_original_minor)
        if monto_pendiente_minor is not NO_CAMBIAR: campos.append("monto_pendiente_minor = ?"); valores.append(monto_pendiente_minor)
        if moneda_id              is not NO_CAMBIAR: campos.append("moneda_id = ?");             valores.append(moneda_id)
        if fecha_inicio           is not NO_CAMBIAR: campos.append("fecha_inicio = ?");          valores.append(fecha_inicio)
        if fecha_vencimiento      is not NO_CAMBIAR: campos.append("fecha_vencimiento = ?");     valores.append(fecha_vencimiento)
        if estado                 is not NO_CAMBIAR: campos.append("estado = ?");                valores.append(estado)
        if origen_tipo            is not NO_CAMBIAR: campos.append("origen_tipo = ?");           valores.append(origen_tipo)
        if origen_id              is not NO_CAMBIAR: campos.append("origen_id = ?");             valores.append(origen_id)
        if notas                  is not NO_CAMBIAR: campos.append("notas = ?");                 valores.append(notas)
        if concepto               is not NO_CAMBIAR: campos.append("concepto = ?");              valores.append(concepto)
        if not campos:
            return False
        valores.append(deuda_id)
        self._db.execute(f"UPDATE deudas SET {', '.join(campos)} WHERE id = ?;", tuple(valores))
        return True

    # ----------------------------------------------------------
    # REGISTRAR PAGO
    # ----------------------------------------------------------

    def registrar_pago(
        self,
        deuda_id: int,
        monto_applied_minor: int,
        tipo_pago: str,
        fecha: str,
        nuevo_monto_pendiente_minor: int,
        nuevo_estado: str,
        transaccion_id: Optional[int] = None,
        concepto: Optional[str] = None,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un pago en deuda_pagos y actualiza monto_pendiente_minor +
        estado en deudas de forma atómica. Devuelve el id del pago
        insertado. nuevo_monto_pendiente_minor y nuevo_estado ya vienen
        calculados por quien llama (la resta y la decisión de si la deuda
        queda 'saldada' son regla de negocio, no de este repositorio).

        Si se pasa `conn`, participa de una transacción externa: ni el
        INSERT ni el UPDATE comitean acá, el caller es responsable de
        comitear/rollback (ver TransaccionesRepository.crear(conn=...)). Si
        no se pasa, este método abre su propia transacción vía
        self._db.transaction() para garantizar que el INSERT + UPDATE sean
        atómicos incluso en uso standalone — nunca se ejecutan como dos
        escrituras independientes, porque un fallo entre medio dejaría el
        pago insertado sin reflejarse en el saldo pendiente.
        """
        sql_insert = """
            INSERT INTO deuda_pagos
                (deuda_id, transaccion_id, concepto, monto_applied_minor,
                 tipo_pago, notas, fecha)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        params_insert = (
            deuda_id, transaccion_id, concepto, monto_applied_minor,
            tipo_pago, notas, fecha,
        )

        sql_update = """
            UPDATE deudas SET monto_pendiente_minor = ?, estado = ? WHERE id = ?;
        """
        params_update = (nuevo_monto_pendiente_minor, nuevo_estado, deuda_id)

        if conn is not None:
            pago_id = conn.execute(sql_insert, params_insert).lastrowid
            conn.execute(sql_update, params_update)
            return pago_id

        with self._db.transaction() as tx_conn:
            pago_id = tx_conn.execute(sql_insert, params_insert).lastrowid
            tx_conn.execute(sql_update, params_update)
        return pago_id

    # ----------------------------------------------------------
    # WRITE OFF
    # ----------------------------------------------------------

    def write_off(
        self,
        deuda_id: int,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Marca una deuda como 'incobrable' y escribe `notas` (reemplaza el
        valor existente por completo, igual que DebtsService.write_off()
        hoy — no es un NO_CAMBIAR condicional, siempre escribe lo que se le
        pasa, incluido None).

        Acepta `conn` opcional para participar de una transacción externa,
        igual que registrar_pago(). Si no se pasa, comitea normalmente vía
        self._db.execute().
        """
        sql = "UPDATE deudas SET estado = 'incobrable', notas = ? WHERE id = ?;"
        params = (notas, deuda_id)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
