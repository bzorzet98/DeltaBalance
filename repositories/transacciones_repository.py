"""
DeltaBalance — repositories/transacciones_repository.py

Acceso a datos para la tabla `transacciones`. Sin lógica de negocio: no
valida formato de fecha, no valida tipo_movimiento, no valida que el monto
sea positivo, no verifica que la cuenta/categoría exista — todo eso vive en
services/transaction_service.py (TransactionService), que sigue siendo la
fuente de verdad de esas reglas hoy y que en un paso posterior va a migrar
para usar este repositorio en vez de escribir SQL directo.

Por el mismo motivo, crear()/actualizar() reciben moneda_id y monto_minor ya
resueltos (no moneda_codigo ni montos en float) — resolver un código de
moneda a id y convertir un monto a minor units es responsabilidad de quien
llama (hoy TransactionService.create()/update() ya lo hacen así antes de
tocar la tabla), no de este repositorio.

`transacciones` ya tiene soft-delete propio vía `deleted_at` (a diferencia
de `cuentas`/`categorias`, que usan `activa`) — crear() nunca lo toca;
eliminar()/restaurar() son los únicos métodos que lo modifican.

Fuera de alcance a propósito (no son CRUD de una sola tabla):
- `resumen_mensual` / `monthly_summary`: es un reporte agregado (GROUP BY
  por moneda), no un listado de filas — se deja en TransactionService.

crear_autotransferencia() (hueco cerrado — ver docs/DATA_MODEL_DECISIONS.md
sección 13): SÍ vive acá, a diferencia de lo que decía una versión anterior
de este docstring. Crear las DOS filas de `transacciones` de una
transferencia sigue siendo orquestación de TransactionService.create_transfer()
(dos llamadas a este mismo crear()) — pero una vez que esas dos filas ya
existen, escribir el vínculo en `autotransferencias` es un INSERT de una
sola tabla sin ninguna regla de negocio, exactamente el mismo criterio que
cualquier otro crear() simple del proyecto (ej. AsignacionesRepository.crear()).
No existía la tabla `autotransferencias` en el schema hasta ahora — se
agregó recién al cerrar este hueco (nunca se había creado, aunque el código
legacy ya insertaba contra ella asumiendo que sí).

Sentinel NO_CAMBIAR (Fase 2, TRANSACCIONES paso 3): actualizar() distingue
"no tocar este campo" (default, se omite del UPDATE) de "escribir NULL a
propósito" (pasar None explícito) — algo que la convención anterior
(Optional[...] = None significando "no tocar") no podía expresar. Ver
CuentasRepository.actualizar(), que todavía usa la convención vieja porque
hoy no existe ningún caso real que necesite escribir NULL a propósito ahí;
la inconsistencia entre los dos repositorios es deliberada por ahora, no un
descuido. El sentinel en sí vive en repositories/_sentinels.py (Fase 2,
DEUDAS paso 1) porque DeudasRepository también lo necesita — centralizado
para que no existan dos objetos NO_CAMBIAR con identidad distinta.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder
from repositories._sentinels import NO_CAMBIAR


class TransaccionesRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    def crear(
        self,
        fecha: str,
        concepto: str,
        cuenta_id: int,
        categoria_id: int,
        moneda_id: int,
        tipo_movimiento: str,
        monto_minor: int,
        tag: Optional[str] = None,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta una transacción. Nunca toca deleted_at.

        Si se pasa `conn` (por ejemplo el conn que entrega
        `self._db.transaction()` dentro de un `with`), el INSERT se ejecuta
        ahí directamente, sin pasar por self._db.execute() ni comitear —
        para poder participar de una transacción externa (ver
        TransactionService.create_transfer(), que hace dos crear() atómicos
        dentro de una sola transacción). Si no se pasa, comportamiento
        actual sin cambios: abre/comitea a través de self._db.execute().
        """
        sql = """
            INSERT INTO transacciones
                (fecha, concepto, cuenta_id, categoria_id, moneda_id,
                 tipo_movimiento, monto_minor, tag, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            fecha, concepto, cuenta_id, categoria_id, moneda_id,
            tipo_movimiento, monto_minor, tag, notas,
        )
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    def crear_autotransferencia(
        self,
        transaccion_salida_id: int,
        transaccion_entrada_id: int,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta el vínculo formal en `autotransferencias` entre las dos
        filas de transacciones que ya existen (egreso en origen, ingreso en
        destino). No crea esas dos filas — eso es orquestación de
        TransactionService.create_transfer(), que llama a crear() dos veces
        y a este método una tercera, todo dentro de la misma transacción
        externa (ver docstring del módulo).

        Si se pasa `conn`, participa de esa transacción externa igual que
        crear() — no comitea acá.
        """
        sql = """
            INSERT INTO autotransferencias
                (transaccion_salida_id, transaccion_entrada_id, notas)
            VALUES (?, ?, ?);
        """
        params = (transaccion_salida_id, transaccion_entrada_id, notas)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    def obtener_por_id(
        self, transaccion_id: int, incluir_eliminadas: bool = False
    ) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("transacciones", include_deleted=incluir_eliminadas)
            .where("id", transaccion_id)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_enriquecida(
        self, transaccion_id: int, incluir_eliminadas: bool = False
    ) -> Optional[sqlite3.Row]:
        """
        Igual que obtener_por_id(), pero con el shape enriquecido que usa
        TransactionService.get(): columnas propias de transacciones más
        account_name, category_name, categoria_principal, currency_code,
        currency_symbol y decimales, vía JOINs a cuentas/categorias/monedas.
        Réplica exacta de la query que TransactionService.get() armaba a
        mano con QueryBuilder antes de migrar (Fase 2, TRANSACCIONES paso 3).
        """
        return (
            QueryBuilder("transacciones t", include_deleted=incluir_eliminadas)
            .select(
                "t.*",
                "c.nombre AS account_name",
                "cat.subcategoria AS category_name",
                "cat.categoria_principal",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
            )
            .join("cuentas c", "c.id = t.cuenta_id")
            .join("categorias cat", "cat.id = t.categoria_id")
            .join("monedas m", "m.id = t.moneda_id")
            .where("t.id", transaccion_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(
        self,
        cuenta_id: Optional[int] = None,
        categoria_id: Optional[int] = None,
        moneda_id: Optional[int] = None,
        tipo_movimiento: Optional[str] = None,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        tag: Optional[str] = None,
        incluir_eliminadas: bool = False,
        pagina: int = 1,
        por_pagina: int = 50,
    ) -> list[sqlite3.Row]:
        """
        Filtros AND-combinados, todos opcionales. Mismo set de filtros que
        TransactionService.list_transactions() (la fuente de verdad actual),
        con nombres en español. Por default excluye eliminadas (deleted_at
        no nulo). Ordena por fecha DESC, id DESC — igual que hoy.
        """
        return (
            QueryBuilder("transacciones", include_deleted=incluir_eliminadas)
            .where("cuenta_id", cuenta_id)
            .where("categoria_id", categoria_id)
            .where("moneda_id", moneda_id)
            .where("tipo_movimiento", tipo_movimiento)
            .where("fecha", fecha_desde, ">=")
            .where("fecha", fecha_hasta, "<=")
            .where("tag", tag)
            .order("fecha", "DESC")
            .order("id", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    def listar_enriquecida(
        self,
        cuenta_id: Optional[int] = None,
        categoria_id: Optional[int] = None,
        moneda_id: Optional[int] = None,
        tipo_movimiento: Optional[str] = None,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        tag: Optional[str] = None,
        incluir_eliminadas: bool = False,
        pagina: int = 1,
        por_pagina: int = 50,
    ) -> list[sqlite3.Row]:
        """
        Misma firma de filtros y paginación que listar(), pero con el shape
        enriquecido (JOINs a cuentas/categorias/monedas) y el subconjunto
        curado de columnas que usa TransactionService.list_transactions().
        Réplica exacta de esa query (Fase 2, TRANSACCIONES paso 3) — con un
        agregado: t.cuenta_id/t.categoria_id/t.moneda_id ahora también van
        seleccionados (antes solo se usaban como filtro WHERE, no viajaban
        en la fila). Hacía falta para el Registro de transacciones del
        dashboard (edición inline por celda, Fase 5): sin el id crudo, la
        única forma de saber a qué categoría/cuenta pertenece una fila para
        precargar el dropdown de edición era matchear por
        category_name/account_name, y `subcategoria` NO es única por sí
        sola en el schema (solo UNIQUE(categoria_principal, subcategoria)
        combinados) — dos categorías de grupos distintos podrían compartir
        nombre de subcategoría y confundir la edición. Con el id crudo no
        hay ambigüedad posible.
        """
        return (
            QueryBuilder("transacciones t", include_deleted=incluir_eliminadas)
            .select(
                "t.id", "t.fecha", "t.concepto", "t.tipo_movimiento",
                "t.monto_minor", "t.tag", "t.notas", "t.creada_en",
                "t.cuenta_id", "t.categoria_id", "t.moneda_id",
                "c.nombre AS account_name",
                "cat.subcategoria AS category_name",
                "cat.categoria_principal",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
            )
            .join("cuentas c", "c.id = t.cuenta_id")
            .join("categorias cat", "cat.id = t.categoria_id")
            .join("monedas m", "m.id = t.moneda_id")
            .where("t.cuenta_id", cuenta_id)
            .where("t.categoria_id", categoria_id)
            .where("t.moneda_id", moneda_id)
            .where("t.tipo_movimiento", tipo_movimiento)
            .where("t.fecha", fecha_desde, ">=")
            .where("t.fecha", fecha_hasta, "<=")
            .where("t.tag", tag)
            .order("t.fecha", "DESC")
            .order("t.id", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    def actualizar(
        self,
        transaccion_id: int,
        fecha: Any = NO_CAMBIAR,
        concepto: Any = NO_CAMBIAR,
        cuenta_id: Any = NO_CAMBIAR,
        categoria_id: Any = NO_CAMBIAR,
        moneda_id: Any = NO_CAMBIAR,
        tipo_movimiento: Any = NO_CAMBIAR,
        monto_minor: Any = NO_CAMBIAR,
        tag: Any = NO_CAMBIAR,
        notas: Any = NO_CAMBIAR,
    ) -> bool:
        """
        Update parcial. Default NO_CAMBIAR = no tocar ese campo (se omite
        del UPDATE). Pasar None explícito escribe NULL a propósito en ese
        campo — a diferencia de la convención vieja (Optional[...] = None
        significando "no tocar"), acá None es un valor real y NO_CAMBIAR es
        el sentinel de "sin cambios".
        """
        campos, valores = [], []
        if fecha           is not NO_CAMBIAR: campos.append("fecha = ?");           valores.append(fecha)
        if concepto        is not NO_CAMBIAR: campos.append("concepto = ?");        valores.append(concepto)
        if cuenta_id       is not NO_CAMBIAR: campos.append("cuenta_id = ?");       valores.append(cuenta_id)
        if categoria_id    is not NO_CAMBIAR: campos.append("categoria_id = ?");    valores.append(categoria_id)
        if moneda_id       is not NO_CAMBIAR: campos.append("moneda_id = ?");       valores.append(moneda_id)
        if tipo_movimiento is not NO_CAMBIAR: campos.append("tipo_movimiento = ?"); valores.append(tipo_movimiento)
        if monto_minor     is not NO_CAMBIAR: campos.append("monto_minor = ?");     valores.append(monto_minor)
        if tag             is not NO_CAMBIAR: campos.append("tag = ?");             valores.append(tag)
        if notas           is not NO_CAMBIAR: campos.append("notas = ?");           valores.append(notas)
        if not campos:
            return False
        valores.append(transaccion_id)
        self._db.execute(
            f"UPDATE transacciones SET {', '.join(campos)} WHERE id = ?;", tuple(valores)
        )
        return True

    def eliminar(self, transaccion_id: int) -> None:
        """Soft-delete: deleted_at = CURRENT_TIMESTAMP. Nunca un DELETE físico."""
        self._db.execute(
            "UPDATE transacciones SET deleted_at = CURRENT_TIMESTAMP WHERE id = ?;",
            (transaccion_id,),
        )

    def restaurar(self, transaccion_id: int) -> None:
        """Revierte eliminar(): deja deleted_at en NULL de nuevo."""
        self._db.execute(
            "UPDATE transacciones SET deleted_at = NULL WHERE id = ?;",
            (transaccion_id,),
        )
