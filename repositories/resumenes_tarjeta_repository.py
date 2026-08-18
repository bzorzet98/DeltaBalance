"""
DeltaBalance — repositories/resumenes_tarjeta_repository.py

Acceso a datos para la tabla `resumenes_tarjeta`. Sin lógica de negocio: no
decide si un resumen "se puede" cerrar o pagar, no calcula el nuevo total
consolidado — eso vive en services/fees_service.py (FeesService), que sigue
siendo la fuente de verdad de esas reglas hoy.

`resumenes_tarjeta` NO tiene columna `deleted_at` ni `activa` — no existe
soft-delete para esta tabla. Por eso obtener_por_id()/obtener_por_periodo()/
listar()/obtener_enriquecida()/listar_enriquecida() usan QueryBuilder con
include_deleted=True hardcodeado, mismo motivo que en los repositorios
anteriores sin esa columna.

Hallazgo de la revisión de FeesService (Fase 2, COMPRAS_CUOTAS paso 1) que
determina la forma de este repositorio — importante para el paso 2:
- close_statement() (el método REAL en services/fees_service.py, todavía
  sin migrar) HOY SOLO cambia `estado` a 'cerrado'. NO toca
  monto_consumos_minor/monto_impuestos_minor/porcentaje_impuesto_bp/
  monto_total_pagado_minor — a pesar de que la consigna original de esta
  tarea asumía que "close_statement() consolida los totales". El único
  método que efectivamente escribe esos totales hoy es confirm_fee(), vía
  un UPDATE con aritmética inline (`monto_consumos_minor = monto_consumos_minor + ?`).
  actualizar_totales() de acá está pensado para lo que confirm_fee()
  necesita en la práctica: recibe los valores YA CALCULADOS (no hace la
  suma ella misma — esa aritmética es responsabilidad de quien llama, igual
  que el resto de los repositorios de esta fase no hacen matemática de
  negocio).
- monto_impuestos_minor se inicializa en 0 en open_statement() y NINGÚN
  método existente lo vuelve a tocar después — queda sin usar en la
  práctica hoy. Se deja como parámetro opcional en actualizar_totales() por
  si el paso 2 lo necesita, pero no hay evidencia de que haga falta.

CARGOS EXTRA DE RESUMEN (extensión posterior, ver
docs/DATA_MODEL_DECISIONS.md sección 12) — esto SÍ cambia marcar_cerrado()
y marcar_pagado() de este repositorio, adelantándose al rediseño de
close_statement()/pay_statement() que el paso 2 todavía tiene pendiente
portar al service real:
- marcar_cerrado() dejó de ser un cambio de estado puro. Ahora calcula y
  escribe en el mismo UPDATE: monto_consumos_minor (SUM de
  cuotas_credito.monto_cuota_minor de ese resumen — consulta directa, no
  existe hoy un método en CuotasCreditoRepository que agregue por
  resumen_id, y agregar uno queda fuera de alcance de esta tarea),
  monto_impuestos_minor (vía ResumenCargosExtraRepository.suma_por_resumen())
  y porcentaje_impuesto_bp (derivado: monto_impuestos_minor*10000 //
  monto_consumos_minor, 0 si no hay consumos). Devuelve los tres valores en
  un dict para que quien orqueste el cierre arme su resultado sin releer.
- marcar_pagado() ahora SÍ escribe monto_total_pagado_minor (antes no lo
  tocaba, ver nota vieja de pay_statement() arriba) — recibe
  monto_pagado_minor como parámetro obligatorio, cambio de firma respecto a
  la versión anterior.
- El service real (`FeesService.close_statement()`/`pay_statement()`)
  TODAVÍA no llama a esta versión nueva — eso es explícitamente el paso 2,
  no esta tarea.
- marcar_cerrado() respeta `conn` de punta a punta: si se pasa, las dos
  lecturas internas (suma de cuotas_credito, suma de resumen_cargos_extra)
  y el UPDATE final se ejecutan TODOS sobre esa conexión, nunca sobre
  self._db.conn. La primera versión de este método leía siempre contra
  self._db.conn asumiendo que coincidía con el `conn` externo recibido —
  cierto en este codebase hoy, pero un supuesto frágil, no una garantía del
  método. Corregido — ver docstring de marcar_cerrado().

Fuera de alcance a propósito:
- No hay un "abrir o crear" combinado: open_statement() hace su propio
  chequeo de idempotencia con obtener_por_periodo() + crear() por separado
  — esa orquestación (y el hecho de que hoy NO está envuelta en una
  transacción explícita, ver open_statement() en el service) se queda en
  FeesService.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder
from repositories.resumen_cargos_extra_repository import ResumenCargosExtraRepository


class ResumenesTarjetaRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db
        self._cargos_repo = ResumenCargosExtraRepository(db)

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        cuenta_id: int,
        mes: int,
        anio: int,
        porcentaje_impuesto_bp: int = 0,
        monto_consumos_minor: int = 0,
        monto_impuestos_minor: int = 0,
        monto_total_pagado_minor: int = 0,
    ) -> int:
        """
        Inserta un resumen de tarjeta nuevo. estado arranca en 'abierto'
        (default de columna). Réplica exacta del INSERT de
        open_statement(), que siempre arranca los tres montos en 0 y solo
        recibe cuenta_id/mes/anio/porcentaje_impuesto_bp como variables.
        """
        return self._db.execute(
            """
            INSERT INTO resumenes_tarjeta
                (cuenta_id, mes, anio, monto_consumos_minor,
                 monto_impuestos_minor, porcentaje_impuesto_bp, monto_total_pagado_minor)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                cuenta_id, mes, anio, monto_consumos_minor,
                monto_impuestos_minor, porcentaje_impuesto_bp, monto_total_pagado_minor,
            ),
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, resumen_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("resumenes_tarjeta", include_deleted=True)
            .where("id", resumen_id)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_por_periodo(self, cuenta_id: int, mes: int, anio: int) -> Optional[sqlite3.Row]:
        """
        Réplica exacta del chequeo de idempotencia de open_statement():
        busca un resumen existente para esa cuenta/mes/año (coincide con el
        UNIQUE(cuenta_id, mes, anio) del schema).
        """
        return (
            QueryBuilder("resumenes_tarjeta", include_deleted=True)
            .where("cuenta_id", cuenta_id)
            .where("mes", mes)
            .where("anio", anio)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_enriquecida(self, resumen_id: int) -> Optional[sqlite3.Row]:
        """
        Igual que obtener_por_id(), pero con el shape enriquecido que usa
        FeesService.get_statement(): columnas propias más account_name vía
        JOIN a cuentas. Réplica exacta de esa query.
        """
        return (
            QueryBuilder("resumenes_tarjeta rt", include_deleted=True)
            .select("rt.*", "c.nombre AS account_name")
            .join("cuentas c", "c.id = rt.cuenta_id")
            .where("rt.id", resumen_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(
        self,
        cuenta_id: Optional[int] = None,
        estado: Optional[str] = None,
        anio: Optional[int] = None,
        pagina: int = 1,
        por_pagina: int = 24,
    ) -> list[sqlite3.Row]:
        """
        Filtros AND-combinados, todos opcionales. Mismo set de filtros que
        FeesService.list_statements(). Ordena por anio DESC, mes DESC.
        """
        return (
            QueryBuilder("resumenes_tarjeta", include_deleted=True)
            .where("cuenta_id", cuenta_id)
            .where("estado", estado)
            .where("anio", anio)
            .order("anio", "DESC")
            .order("mes", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    def listar_enriquecida(
        self,
        cuenta_id: Optional[int] = None,
        estado: Optional[str] = None,
        anio: Optional[int] = None,
        pagina: int = 1,
        por_pagina: int = 24,
    ) -> list[sqlite3.Row]:
        """
        Misma firma de filtros y paginación que listar(), pero con el shape
        enriquecido (JOIN a cuentas) que usa FeesService.list_statements().
        Réplica exacta de esa query.
        """
        return (
            QueryBuilder("resumenes_tarjeta rt", include_deleted=True)
            .select("rt.*", "c.nombre AS account_name")
            .join("cuentas c", "c.id = rt.cuenta_id")
            .where("rt.cuenta_id", cuenta_id)
            .where("rt.estado", estado)
            .where("rt.anio", anio)
            .order("rt.anio", "DESC")
            .order("rt.mes", "DESC")
            .paginar(pagina, por_pagina)
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar_totales(
        self,
        resumen_id: int,
        monto_consumos_minor: int,
        monto_total_pagado_minor: int,
        monto_impuestos_minor: Optional[int] = None,
        porcentaje_impuesto_bp: Optional[int] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Escribe los totales YA CALCULADOS por quien llama (la suma
        monto_consumos_minor + fee y la aplicación de porcentaje_impuesto_bp
        para obtener monto_total_pagado_minor son aritmética de negocio,
        responsabilidad de FeesService — ver docstring del módulo). Cubre lo
        que confirm_fee() necesita hoy: monto_consumos_minor y
        monto_total_pagado_minor son obligatorios porque confirm_fee()
        siempre los reescribe juntos; monto_impuestos_minor/
        porcentaje_impuesto_bp son opcionales porque en la práctica actual
        nadie los vuelve a tocar después de la creación.

        Acepta `conn` para participar de la transacción externa que también
        actualiza la cuota en CuotasCreditoRepository.marcar_estado()
        (confirm_fee() hace ambas escrituras atómicamente).
        """
        campos = ["monto_consumos_minor = ?", "monto_total_pagado_minor = ?"]
        valores: list = [monto_consumos_minor, monto_total_pagado_minor]
        if monto_impuestos_minor is not None:
            campos.append("monto_impuestos_minor = ?")
            valores.append(monto_impuestos_minor)
        if porcentaje_impuesto_bp is not None:
            campos.append("porcentaje_impuesto_bp = ?")
            valores.append(porcentaje_impuesto_bp)
        valores.append(resumen_id)
        sql = f"UPDATE resumenes_tarjeta SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.execute(sql, tuple(valores))

    def marcar_cerrado(
        self,
        resumen_id: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> dict:
        """
        Cierra un resumen: calcula y escribe monto_consumos_minor,
        monto_impuestos_minor y porcentaje_impuesto_bp junto con
        estado='cerrado', en un único UPDATE (ver docs/DATA_MODEL_DECISIONS.md
        sección 12 y el docstring del módulo).

        Si se pasa `conn`, TODAS las operaciones (las dos lecturas y el
        UPDATE final) se ejecutan sobre esa conexión — no solo la
        escritura. Es lo correcto para que el método vea el estado real de
        una transacción externa, incluyendo INSERTs/UPDATEs no comiteados
        todavía sobre esa misma conexión; asumir que `conn` externo
        siempre coincide con self._db.conn sería un supuesto frágil (en
        este codebase suele coincidir, pero no es una garantía del
        contrato del método). Si no se pasa `conn`, todo se ejecuta contra
        self._db.conn como siempre.

        Devuelve {"monto_consumos_minor", "monto_impuestos_minor",
        "porcentaje_impuesto_bp"} con los valores calculados, para que quien
        orqueste el cierre (FeesService, paso 2) arme su resultado sin
        releer el resumen.
        """
        conexion = conn if conn is not None else self._db.conn
        fila_consumos = conexion.execute(
            "SELECT COALESCE(SUM(monto_cuota_minor), 0) AS total FROM cuotas_credito WHERE resumen_id = ?;",
            (resumen_id,),
        ).fetchone()
        monto_consumos_minor = fila_consumos["total"]
        monto_impuestos_minor = self._cargos_repo.suma_por_resumen(resumen_id, conn=conn)
        if monto_consumos_minor > 0:
            porcentaje_impuesto_bp = (monto_impuestos_minor * 10000) // monto_consumos_minor
        else:
            porcentaje_impuesto_bp = 0

        sql = """
            UPDATE resumenes_tarjeta
            SET estado = 'cerrado',
                monto_consumos_minor = ?,
                monto_impuestos_minor = ?,
                porcentaje_impuesto_bp = ?
            WHERE id = ?;
        """
        params = (monto_consumos_minor, monto_impuestos_minor, porcentaje_impuesto_bp, resumen_id)
        if conn is not None:
            conn.execute(sql, params)
        else:
            self._db.execute(sql, params)

        return {
            "monto_consumos_minor": monto_consumos_minor,
            "monto_impuestos_minor": monto_impuestos_minor,
            "porcentaje_impuesto_bp": porcentaje_impuesto_bp,
        }

    def marcar_pagado(
        self,
        resumen_id: int,
        fecha_pago: str,
        monto_pagado_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Transición → 'pagado', con fecha_pago y monto_total_pagado_minor =
        monto_pagado_minor. Cambio de firma respecto a la versión anterior
        (ver docstring del módulo): antes NO reescribía
        monto_total_pagado_minor, replicando fielmente a pay_statement()
        como estaba hoy; ahora sí, como parte del rediseño de cargos extra.

        Acepta `conn` para participar de la transacción externa que también
        marca en bloque las cuotas del resumen como 'pagado' vía
        CuotasCreditoRepository.marcar_estado_por_resumen() (pay_statement()
        hace ambas escrituras atómicamente).
        """
        sql = (
            "UPDATE resumenes_tarjeta "
            "SET estado = 'pagado', fecha_pago = ?, monto_total_pagado_minor = ? "
            "WHERE id = ?;"
        )
        params = (fecha_pago, monto_pagado_minor, resumen_id)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
