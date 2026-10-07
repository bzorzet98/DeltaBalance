"""
DeltaBalance — services/dashboard_service.py

Purpose:
    Servicio de solo lectura / agregación para el dashboard: patrimonio
    total, gasto del mes por categoría, y comparación estimado vs. real
    contra los presupuestos cargados. No escribe nada, no tiene reglas de
    negocio propias — compone AccountsService/PresupuestosService (que ya
    tienen sus propias reglas) y agrega una query de agregación (GROUP BY)
    propia para el gasto por categoría.

    Revisión previa a escribir esto (paso 1 de la tarea):
    - AccountsService.get_total_balance() ya existe y hace exactamente lo
      que necesita get_patrimonio_total() — se delega directo, no se
      reinventa.
    - TransactionService.monthly_summary(mes, anio) existe pero agrupa por
      MONEDA, no por categoría — no sirve para get_gasto_por_categoria().
      No hay ningún método de TransaccionesRepository ni de
      TransactionService que agrupe por categoría tampoco.
    - Por eso get_gasto_por_categoria() escribe su propia query de
      agregación acá, igual que TransactionService.monthly_summary() y
      FeesService.fees_by_month()/DebtsService.summary_by_person() en
      bloques anteriores: un reporte agregado (GROUP BY) no es CRUD de una
      tabla, no le corresponde vivir en un repositorio.
    - PresupuestosService.list_budgets(mes, anio) ya expone
      PresupuestosRepository.listar_por_periodo() enriquecido con
      subcategoria/categoria_principal/moneda_codigo — se usa tal cual para
      get_comparacion_presupuesto(), no se instancia PresupuestosRepository
      directo.

    Ninguna de las tres columnas nunca mezcla monto de monedas distintas en
    un mismo total: get_patrimonio_total() ya viene separado por moneda_codigo
    (AccountsService), get_gasto_por_categoria() agrupa por
    (categoria_id, moneda_id) — no solo por categoria_id — y
    get_comparacion_presupuesto() solo suma como "real" el gasto que está en
    la MISMA moneda que el presupuesto de esa categoría (presupuestos tiene
    un único moneda_id por fila, UNIQUE(categoria_id, mes, anio) del schema).

    get_resumen_mes() (pantalla DASHBOARD, ui/screens/resumen_mes.py — el
    "dashboard.py" de ui/screens/ es la pantalla del Registro): todo lo del
    mes en una sola moneda — INGRESOS, EGRESOS, TARJETA y DEUDAS, cada uno
    con su detalle y subtotales — y el DISPONIBLE HOY / PROYECTADO. Compone
    los services de cada dominio — IngresosService (ingresos fijos),
    TransactionService (ingresos variables y pagos de tarjeta del
    Registro), CategoriasService (el tipo de cada categoría),
    PresupuestosService (fijos, y el real de cada categoría ya neto de
    compartidos), FeesService (lo que vence de cada tarjeta), DebtsService
    (deudas informales) — y agrega una query propia sobre
    gastos_compartidos (ver _gastos_compartidos_hasta()). Decisiones del
    usuario, ver docstring del método y docs/DATA_MODEL_DECISIONS.md
    sección 34.
"""

import sqlite3
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from db.database import DatabaseManager
from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.debts_service import DebtsService
from services.fees_service import FeesService
from services.ingresos_service import CLAVE_TOTAL, IngresosService
from services.presupuestos_service import MONEDA_SIN_ORIGEN_CODIGO, PresupuestosService
from services.shared_expenses_service import SharedExpensesService
from services.transaction_service import TransactionService
from utils.categorias import clave_categoria
from utils.personas import normalizar_persona

# get_resumen_mes(): categorías del Registro que NO son egresos variables
# (decisión del usuario): los movimientos de capital — autotransferencias,
# cambio de moneda, deudas, ahorro e inversiones — no son un gasto, y el
# pago de la tarjeta va en la sección TARJETA (PAGOS REALIZADOS). Pares
# (categoria_principal, subcategoria), comparados sin distinguir mayúsculas
# (utils/categorias.py clave_categoria()). Para dejar afuera otra
# categoría, agregarla acá.
CATEGORIAS_EXCLUIDAS_GASTOS: tuple[tuple[str, str], ...] = (
    ("MOVIMIENTO CAPITAL", "AUTOTRANSFERENCIA"),
    ("TARJETA DE CRÉDITO", "PAGO TARJETA"),
    ("MOVIMIENTO CAPITAL", "CAMBIO MONEDA"),
    ("MOVIMIENTO CAPITAL", "DEUDA"),
    ("MOVIMIENTO CAPITAL", "AHORRO/INVERSIÓN"),
    ("MOVIMIENTO CAPITAL", "INVERSIONES"),
)
# get_resumen_mes(): categorías del Registro donde se pagan los egresos FIJOS
# (decisión del usuario: los fijos también se pagan desde el Registro). No
# son egresos variables — ya cuentan como egresos fijos (estimados o
# pagados), si no se restarían dos veces —; se informan aparte
# (egresos.en_categorias_de_fijos). Mismo formato que
# CATEGORIAS_EXCLUIDAS_GASTOS; para sumar otra, agregarla acá.
CATEGORIAS_DE_FIJOS: tuple[tuple[str, str], ...] = (
    ("EGRESOS", "VIVIENDA"),
    ("EGRESOS", "SERVICIOS BÁSICOS"),
    ("EGRESOS", "SEGUROS"),
)
# get_resumen_mes(): categorías de ingreso del Registro que ya están en los
# INGRESOS FIJOS (IngresosService): no son ingresos variables, si no se
# sumarían dos veces. Mismo formato que CATEGORIAS_DE_FIJOS.
CATEGORIAS_INGRESOS_FIJOS: tuple[tuple[str, str], ...] = (
    ("INGRESOS", "SUELDO / BECA"),
)
# get_resumen_mes(): la categoría de los pagos de la tarjeta (sección TARJETA, PAGOS REALIZADOS).
CATEGORIA_PAGO_TARJETA: tuple[str, str] = ("TARJETA DE CRÉDITO", "PAGO TARJETA")
# Ingresos variables sin tag: uno por categoría, "<CATEGORÍA> (SIN TAG)".
TEXTO_SIN_TAG = "SIN TAG"
# Ids de las filas de get_resumen_mes() (calculadora de escenarios): prefijo + id, tag o categoría.
# Los de antes del rediseño (ingreso:, fijo:, variable:, cuota:) se mantienen: lo excluido ya guardado sigue valiendo.
PREFIJO_ID_INGRESO = "ingreso:"
PREFIJO_ID_INGRESO_VARIABLE = "ingreso_variable:"
PREFIJO_ID_INGRESO_SIN_TAG = "ingreso_sin_tag:"
PREFIJO_ID_FIJO = "fijo:"
PREFIJO_ID_VARIABLE = "variable:"
PREFIJO_ID_TARJETA = "cuota:"
# Id del único ítem del grupo neto_deudas.
ID_ITEM_NETO_DEUDAS = "neto_deudas"
# Filas por página al recorrer las transacciones del mes (TransactionService.list_transactions()).
LOTE_TRANSACCIONES = 500
# Los dos DISPONIBLE de get_resumen_mes() (switch HOY / PROYECTADO de la pantalla).
MODOS_DISPONIBLE = ("hoy", "proyectado")
# Moneda del resumen si no se pide otra.
MONEDA_RESUMEN_DEFAULT = "ARS"
# Origen de una línea de DEUDAS.
TIPO_DEUDA_INFORMAL = "informal"
TIPO_DEUDA_COMPARTIDOS = "compartidos"
# Persona de lo que te deben por un gasto compartido que pagaste vos, si el
# hogar no tiene otros miembros cargados en esta base.
PERSONA_HOGAR_SIN_MIEMBROS = "HOGAR"


class DashboardService:
    """
    Entry point para las agregaciones de solo lectura que alimentan el
    dashboard.

    Usage:
        db  = DatabaseManager()
        svc = DashboardService(db)

        svc.get_patrimonio_total()                  # {"ARS": 1234500, "USD": 20000}
        svc.get_gasto_por_categoria(5, 2026)         # [{"categoria_id": 3, ...}, ...]
        svc.get_movimientos_por_cuenta(5, 2026)      # [{"cuenta_id": 1, "net_minor": ..., ...}, ...]
        svc.get_comparacion_presupuesto(5, 2026)     # [{"categoria_id": 3, "estimado_minor": ..., "real_minor": ...}, ...]
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._accounts_service = AccountsService(db)
        self._presupuestos_service = PresupuestosService(db)
        # Solo lectura, para get_resumen_mes().
        self._ingresos_service = IngresosService(db)
        self._fees_service = FeesService(db)
        self._debts_service = DebtsService(db)
        self._shared_expenses_service = SharedExpensesService(db)
        self._transaction_service = TransactionService(db)
        self._categorias_service = CategoriasService(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    @staticmethod
    def _rango_mes(mes: int, anio: int) -> tuple[str, str]:
        """
        Calcula [inicio, fin) del mes como strings 'YYYY-MM-DD' — mismo
        patrón exacto que TransactionService.monthly_summary(): el límite
        superior es el día 1 del mes siguiente (exclusivo), para no
        depender de cuántos días tiene cada mes.
        """
        if not (1 <= mes <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {mes}.")

        if mes == 12:
            siguiente = date(anio + 1, 1, 1)
        else:
            siguiente = date(anio, mes + 1, 1)

        inicio = f"{anio:04d}-{mes:02d}-01"
        fin = siguiente.isoformat()
        return inicio, fin

    # ----------------------------------------------------------
    # PATRIMONIO TOTAL
    # ----------------------------------------------------------

    def get_patrimonio_total(self) -> dict:
        """
        Delega directo en AccountsService.get_total_balance(): saldo de
        todas las cuentas activas, agrupado por moneda_codigo, sin mezclar
        monedas distintas en una sola suma.

        Returns:
            Dict {moneda_codigo: total_minor}, ej. {"ARS": 1234500, "USD": 20000}.
        """
        return self._accounts_service.get_total_balance()

    # ----------------------------------------------------------
    # GASTO POR CATEGORÍA
    # ----------------------------------------------------------

    def get_gasto_por_categoria(self, mes: int, anio: int) -> list[dict]:
        """
        Suma todas las transacciones tipo='egreso' del mes/año dado,
        agrupadas por (categoria_id, moneda_id) — si una categoría tuvo
        gasto en más de una moneda en el mismo mes, aparece como una
        entrada por cada moneda, nunca mezcladas en un solo total. Excluye
        transacciones eliminadas lógicamente (deleted_at IS NOT NULL),
        mismo criterio que TransactionService.monthly_summary().

        Args:
            mes:  Mes 1–12.
            anio: Año de 4 dígitos.

        Returns:
            Lista de dicts {categoria_id, categoria_nombre,
            categoria_principal, monto_total_minor, moneda_id}, ordenada de
            mayor a menor monto_total_minor.

        Raises:
            ValueError si mes está fuera de rango.
        """
        inicio, fin = self._rango_mes(mes, anio)

        filas = self._db.fetchall(
            """
            SELECT
                t.categoria_id,
                cat.subcategoria AS categoria_nombre,
                cat.categoria_principal,
                t.moneda_id,
                SUM(t.monto_minor) AS monto_total_minor
            FROM transacciones t
            JOIN categorias cat ON cat.id = t.categoria_id
            WHERE t.tipo_movimiento = 'egreso'
              AND t.fecha >= ?
              AND t.fecha < ?
              AND t.deleted_at IS NULL
            GROUP BY t.categoria_id, t.moneda_id
            ORDER BY monto_total_minor DESC;
            """,
            (inicio, fin),
        )

        return [
            {
                "categoria_id": fila["categoria_id"],
                "categoria_nombre": fila["categoria_nombre"],
                "categoria_principal": fila["categoria_principal"],
                "monto_total_minor": fila["monto_total_minor"],
                "moneda_id": fila["moneda_id"],
            }
            for fila in filas
        ]

    # ----------------------------------------------------------
    # MOVIMIENTOS POR CUENTA (desglose por banco — Tarea 4)
    # ----------------------------------------------------------

    def get_movimientos_por_cuenta(self, mes: int, anio: int) -> list[dict]:
        """
        Desglose de movimientos del mes/año dado, agrupado por
        (cuenta_id, moneda_id) — SOLO cuentas con al menos una transacción
        real en ese período: el INNER JOIN contra `cuentas` hace la
        detección dinámica sola (nunca se parte de una lista fija de todas
        las cuentas existentes, ni se filtra por `activa` — una cuenta ya
        archivada que tuvo movimientos ese mes histórico igual debe
        aparecer). Mismo criterio de ingreso/egreso/neto que
        TransactionService.monthly_summary(), pero desagregado por cuenta
        además de por moneda. Nunca mezcla monedas distintas en un mismo
        total (agrupa por cuenta+moneda, no solo por cuenta) — mismo
        principio que get_gasto_por_categoria() de acá arriba.

        Args:
            mes:  Mes 1–12.
            anio: Año de 4 dígitos.

        Returns:
            Lista de dicts {cuenta_id, account_name, moneda_id,
            currency_code, currency_symbol, decimales,
            total_income_minor, total_expense_minor, net_minor}, ordenada
            por account_name.

        Raises:
            ValueError si mes está fuera de rango.
        """
        inicio, fin = self._rango_mes(mes, anio)

        filas = self._db.fetchall(
            """
            SELECT
                c.id            AS cuenta_id,
                c.nombre        AS account_name,
                m.id            AS moneda_id,
                m.codigo        AS currency_code,
                m.simbolo       AS currency_symbol,
                m.decimales,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN t.monto_minor ELSE 0 END) AS total_income_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'egreso'
                         THEN t.monto_minor ELSE 0 END) AS total_expense_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN  t.monto_minor
                         WHEN t.tipo_movimiento = 'egreso'
                         THEN -t.monto_minor
                         ELSE  0 END)                   AS net_minor
            FROM transacciones t
            JOIN cuentas c ON c.id = t.cuenta_id
            JOIN monedas m ON m.id = t.moneda_id
            WHERE t.fecha >= ?
              AND t.fecha < ?
              AND t.deleted_at IS NULL
            GROUP BY c.id, m.id
            ORDER BY c.nombre;
            """,
            (inicio, fin),
        )

        return [dict(fila) for fila in filas]
    def get_saldo_por_cuenta(self) -> list[dict]:
        """
        Saldo acumulado total por cuenta y moneda — usa la vista
        vw_balance_cuentas que ya suma saldo_inicial_minor + todas las
        transacciones históricas. Para el desglose de patrimonio por cuenta
        en el dashboard (no filtrado por mes).
        """
        filas = self._db.fetchall(
            """
            SELECT
                v.cuenta_id,
                v.nombre        AS account_name,
                v.moneda        AS currency_code,
                m.simbolo       AS currency_symbol,
                m.decimales,
                v.saldo_minor   AS net_minor
            FROM vw_balance_cuentas v
            JOIN monedas m ON m.codigo = v.moneda
            WHERE v.saldo_minor != 0
            ORDER BY v.nombre, v.moneda;
            """,
        )
        return [dict(fila) for fila in filas]
    # ----------------------------------------------------------
    # COMPARACIÓN ESTIMADO VS. REAL
    # ----------------------------------------------------------

    def get_comparacion_presupuesto(self, mes: int, anio: int) -> list[dict]:
        """
        Cruza los presupuestos cargados para mes/año contra el gasto real
        de get_gasto_por_categoria() del mismo período. Solo incluye
        categorías CON presupuesto cargado (una categoría con gasto real
        pero sin presupuesto no aparece acá — para verla completa está
        get_gasto_por_categoria()).

        El cruce es por (categoria_id, moneda_id): `presupuestos` tiene un
        único moneda_id por fila (UNIQUE(categoria_id, mes, anio) del
        schema), así que solo se suma como real_minor el gasto que está en
        esa misma moneda — nunca se mezcla con gasto de esa categoría en
        otra moneda.

        Args:
            mes:  Mes 1–12.
            anio: Año de 4 dígitos.

        Returns:
            Lista de dicts {categoria_id, categoria_nombre, estimado_minor,
            real_minor}, en el mismo orden que PresupuestosService.list_budgets()
            (categoria_principal, subcategoria). real_minor es 0 si la
            categoría tiene presupuesto pero ningún gasto real todavía en
            esa moneda.
        """
        presupuestos = self._presupuestos_service.list_budgets(mes, anio)
        gastos = self.get_gasto_por_categoria(mes, anio)

        real_por_clave = {
            (gasto["categoria_id"], gasto["moneda_id"]): gasto["monto_total_minor"]
            for gasto in gastos
        }

        return [
            {
                "categoria_id": presupuesto["categoria_id"],
                "categoria_nombre": presupuesto["subcategoria"],
                "estimado_minor": presupuesto["monto_estimado_minor"],
                "real_minor": real_por_clave.get(
                    (presupuesto["categoria_id"], presupuesto["moneda_id"]), 0
                ),
            }
            for presupuesto in presupuestos
        ]

    # ----------------------------------------------------------
    # RESUMEN DEL MES (pantalla DASHBOARD, ui/screens/resumen_mes.py)
    # ----------------------------------------------------------

    def get_resumen_mes(
        self,
        mes: int,
        anio: int,
        moneda: str = MONEDA_RESUMEN_DEFAULT,
        usuario_local: Optional[str] = None,
    ) -> dict:
        """
        Todo lo del mes para el dashboard, en UNA moneda (nunca se mezclan;
        monedas_disponibles dice en cuáles hay algo, para elegir otra):
        {
            moneda_codigo, moneda_simbolo, decimales, monedas_disponibles,
            fecha_corte (último día del mes: DEUDAS acumula hasta ahí),
            sin_usuario_local (True: sin él no se sabe qué compartidos pagó
                cada uno — las deudas de compartidos quedan afuera),
            ingresos: {
                fijos:     [{id, concepto, estimado_minor, real_minor}],
                variables: [{id, tag, concepto, estimado_minor (0), real_minor}],
                subtotal_fijos, subtotal_variables, total: {estimado_minor, real_minor},
            },
            egresos: {
                fijos: [{id, concepto, estimado_minor, real_minor}],
                variables_con_presupuesto: [{id, categoria_id, categoria,
                    estimado_minor (el presupuesto), proyectado_minor, real_minor}],
                variables_sin_presupuesto: [{id, categoria_id, categoria,
                    estimado_minor (= real), proyectado_minor (= real), real_minor}],
                en_categorias_de_fijos: [{categoria, real_minor}],
                subtotal_fijos, subtotal_variables, total: {estimado_minor, real_minor},
            },
            tarjeta: {
                por_cuenta: [{id, cuenta_id, cuenta, a_pagar_minor}],
                pagos_realizados_minor, total_a_pagar_minor, falta_pagar_minor,
            },
            deudas: {me_deben: [{persona, tipo, monto_minor, moneda}],
                     debo: [{persona, tipo, monto_minor, moneda}],
                     subtotal_me_deben_minor, subtotal_debo_minor, neto_minor},
            disponible: {hoy_minor, proyectado_minor,
                         grupos: {modo: [{clave, items: [{id, aporte_minor}]}]}
                         para cada modo de MODOS_DISPONIBLE (calcular_escenario())},
        }

        De dónde sale cada cosa (decisiones del usuario,
        docs/DATA_MODEL_DECISIONS.md sección 34):
        - ingresos.fijos: IngresosService.list_by_month() — estimado y cobrado.
        - ingresos.variables: los ingresos del Registro del mes en categorías
          de tipo 'ingreso', salvo CATEGORIAS_INGRESOS_FIJOS (ya están en los
          fijos). Un ítem por tag; los sin tag, uno por categoría (ver
          _ingresos_variables()). Solo real: estimado 0. El monto completo
          de cada movimiento: si se compartió, la parte del otro ya resta en
          las deudas.
        - egresos.fijos: los presupuestos 'fijo' del mes (estimado y el real
          cargado a mano).
        - egresos.variables_*: los egresos del Registro del mes por categoría
          de tipo 'egreso', salvo CATEGORIAS_EXCLUIDAS_GASTOS, con el real de
          PresupuestosService.get_real_variable() (un gasto compartido que
          pagaste vos cuenta solo tu parte), más las categorías con
          presupuesto variable sin gasto todavía (real 0). Con presupuesto:
          estimado = el presupuesto; proyectado = lo mayor entre presupuesto
          y gastado (decisión del usuario: si ya se pasó, el PROYECTADO resta
          lo gastado). Sin presupuesto: estimado = proyectado = real.
          subtotal_variables.estimado_minor suma los proyectados.
        - egresos.en_categorias_de_fijos: lo gastado en el Registro en
          CATEGORIAS_DE_FIJOS — informativo: ya está en los fijos (no es
          egreso variable, si no se restaría dos veces).
        - tarjeta: a pagar, FeesService.resumen_por_tarjeta() (lo que vence
          en el mes); pagos realizados, las transacciones del mes en
          CATEGORIA_PAGO_TARJETA; falta pagar, total − pagos (nunca negativo).
        - deudas, ACUMULADO hasta el último día del mes (sin filtro de mes):
          informales: DebtsService.summary_by_person() de cada tab;
          compartidos: el pendiente (monto_pendiente_minor, ya descuenta los
          pagos parciales) de los gastos 'pendiente' — los que pagaste vos
          te los deben (la persona: los otros miembros del hogar), los que
          pagó otro se los debés (la persona: quien pagó). Un saldo negativo
          pasa al otro lado (ej. alguien que te pagó de más: se lo debés).
        - disponible:
            hoy:        ingresos reales (fijos + variables) − egresos reales
                        (fijos + variables) − total a pagar de la tarjeta +
                        neto de deudas;
            proyectado: ingresos fijos estimados + ingresos variables
                        reales − egresos fijos estimados − egresos variables
                        proyectados − total a pagar de la tarjeta + neto de
                        deudas.
          La tarjeta resta su TOTAL en los dos (decisión del usuario): las
          compras con tarjeta no están en el Registro y PAGO TARJETA no es un
          egreso, así que pagar el resumen no mueve el disponible.

        Raises:
            ValueError si el mes está fuera de rango o la moneda no existe.
        """
        inicio, fin = self._rango_mes(mes, anio)
        fecha_corte = (date.fromisoformat(fin) - timedelta(days=1)).isoformat()
        monedas_por_codigo = {m["codigo"]: m for m in self._db.obtener_monedas()}
        codigos_por_id = {m["id"]: codigo for codigo, m in monedas_por_codigo.items()}
        datos_moneda = monedas_por_codigo.get(moneda)
        if datos_moneda is None:
            raise ValueError(f"MONEDA DESCONOCIDA: {moneda}.")
        moneda_id = datos_moneda["id"]
        yo = normalizar_persona(usuario_local)
        usadas: set[str] = set()  # monedas con algún dato este mes (monedas_disponibles)
        transacciones = self._transacciones_del_mes(inicio, fecha_corte)

        # --- Ingresos (fijos: filas + una de total por moneda, IngresosService.list_by_month()) ---
        filas_ingresos = self._ingresos_service.list_by_month(mes, anio)
        usadas.update(f["currency_code"] for f in filas_ingresos if f.get(CLAVE_TOTAL))
        ingresos_fijos = [
            {
                "id": f"{PREFIJO_ID_INGRESO}{f['id']}", "concepto": (f["concepto"] or "").upper(),
                "estimado_minor": f["monto_estimado_minor"], "real_minor": f["monto_real_minor"],
            }
            for f in filas_ingresos
            if not f.get(CLAVE_TOTAL) and f["moneda_id"] == moneda_id
        ]
        ingresos_variables = self._ingresos_variables(transacciones, moneda_id, usadas)
        ingresos = self._con_subtotales(
            {"fijos": ingresos_fijos, "variables": ingresos_variables},
            fijos=ingresos_fijos, variables=ingresos_variables,
        )

        # --- Egresos (fijos: presupuestos 'fijo'; variables: el Registro) ---
        presupuestos = self._presupuestos_service.list_by_month(mes, anio, usuario_local)
        usadas.update(p["currency_code"] for p in presupuestos["fijos"])
        egresos_fijos = [
            {
                "id": f"{PREFIJO_ID_FIJO}{p['id']}", "concepto": (p["nombre"] or "").upper(),
                "estimado_minor": p["monto_estimado_minor"], "real_minor": p["real_minor"] or 0,
            }
            for p in presupuestos["fijos"]
            if p["moneda_id"] == moneda_id
        ]
        con_presupuesto: list[dict] = []
        sin_presupuesto: list[dict] = []
        en_categorias_de_fijos: list[dict] = []
        for g in self._gastos_registro(mes, anio, moneda_id, presupuestos["variables"], usadas, codigos_por_id):
            if g["categoria_de_fijos"]:
                if g["real_minor"]:
                    en_categorias_de_fijos.append({"categoria": g["categoria"], "real_minor": g["real_minor"]})
                continue
            fila = {
                "id": f"{PREFIJO_ID_VARIABLE}{g['categoria_id']}", "categoria_id": g["categoria_id"],
                "categoria": g["categoria"], "real_minor": g["real_minor"],
            }
            if g["presupuesto_minor"] is None:
                sin_presupuesto.append({**fila, "estimado_minor": g["real_minor"], "proyectado_minor": g["real_minor"]})
            else:
                con_presupuesto.append({
                    **fila, "estimado_minor": g["presupuesto_minor"],
                    "proyectado_minor": max(g["presupuesto_minor"], g["real_minor"]),
                })
        egresos = self._con_subtotales(
            {
                "fijos": egresos_fijos,
                "variables_con_presupuesto": con_presupuesto,
                "variables_sin_presupuesto": sin_presupuesto,
                "en_categorias_de_fijos": en_categorias_de_fijos,
            },
            fijos=egresos_fijos, variables=con_presupuesto + sin_presupuesto, campo_estimado_variables="proyectado_minor",
        )

        # --- Tarjeta ---
        tarjetas = self._fees_service.resumen_por_tarjeta(mes, anio)
        usadas.update(t["currency_code"] for t in tarjetas)
        por_cuenta = [
            {
                "id": f"{PREFIJO_ID_TARJETA}{t['cuenta_id']}", "cuenta_id": t["cuenta_id"],
                "cuenta": (t["account_name"] or "").upper(), "a_pagar_minor": t["monto_total_minor"],
            }
            for t in tarjetas
            if t["moneda_id"] == moneda_id and t["monto_total_minor"] != 0
        ]
        total_a_pagar = sum(t["a_pagar_minor"] for t in por_cuenta)
        pagos_realizados = self._pagos_tarjeta(transacciones, moneda_id, usadas)
        tarjeta = {
            "por_cuenta": por_cuenta,
            "pagos_realizados_minor": pagos_realizados,
            "total_a_pagar_minor": total_a_pagar,
            "falta_pagar_minor": max(total_a_pagar - pagos_realizados, 0),
        }

        # --- Deudas (acumuladas al último día del mes) ---
        compartidos = self._gastos_compartidos_hasta(fin)
        usadas.update(codigos_por_id[g["moneda_id"]] for g in compartidos if g["moneda_id"] in codigos_por_id)
        me_deben: list[dict] = []
        debo: list[dict] = []
        for tab in ("me_deben", "debo"):
            for fila in self._debts_service.summary_by_person(tab, hasta_fecha=fecha_corte):
                usadas.add(fila["moneda_codigo"])
                if fila["moneda_id"] == moneda_id:
                    self._a_su_lado(
                        me_deben, debo, fila["entidad_persona"], TIPO_DEUDA_INFORMAL, fila["saldo_minor"],
                        a_favor=(tab == "me_deben"), moneda_codigo=moneda,
                    )
        if yo:
            pendientes: dict[tuple[str, bool, str], int] = defaultdict(int)
            for g in compartidos:
                if g["estado"] != "pendiente" or g["moneda_id"] != moneda_id:
                    continue
                pagaste_vos = normalizar_persona(g["pagador"]) == yo
                pendientes[(g["hogar_id"], pagaste_vos, "" if pagaste_vos else normalizar_persona(g["pagador"]))] += (
                    g["monto_pendiente_minor"] or 0
                )
            for (hogar_id, pagaste_vos, pagador), monto in pendientes.items():
                persona = self._otros_miembros(hogar_id, yo) if pagaste_vos else pagador
                self._a_su_lado(
                    me_deben, debo, persona, TIPO_DEUDA_COMPARTIDOS, monto, a_favor=pagaste_vos, moneda_codigo=moneda,
                )
        for lado in (me_deben, debo):
            lado.sort(key=lambda d: (d["persona"], d["tipo"]))
        subtotal_me_deben = sum(d["monto_minor"] for d in me_deben)
        subtotal_debo = sum(d["monto_minor"] for d in debo)
        deudas = {
            "me_deben": me_deben,
            "debo": debo,
            "subtotal_me_deben_minor": subtotal_me_deben,
            "subtotal_debo_minor": subtotal_debo,
            "neto_minor": subtotal_me_deben - subtotal_debo,
        }

        # --- Disponible (ver docstring) ---
        neto_deudas = deudas["neto_minor"]
        disponible = {
            "hoy_minor": (
                ingresos["total"]["real_minor"] - egresos["total"]["real_minor"] - total_a_pagar + neto_deudas
            ),
            "proyectado_minor": (
                ingresos["subtotal_fijos"]["estimado_minor"] + ingresos["subtotal_variables"]["real_minor"]
                - egresos["total"]["estimado_minor"] - total_a_pagar + neto_deudas
            ),
            "grupos": {
                modo: self._grupos_disponible(modo, ingresos, egresos, tarjeta, neto_deudas)
                for modo in MODOS_DISPONIBLE
            },
        }

        usadas.add(moneda)
        usadas.discard("")
        return {
            "moneda_codigo": moneda,
            "moneda_simbolo": datos_moneda["simbolo"] or "",
            "decimales": datos_moneda["decimales"],
            "monedas_disponibles": sorted(usadas, key=lambda codigo: (codigo != MONEDA_RESUMEN_DEFAULT, codigo)),
            "fecha_corte": fecha_corte,
            "sin_usuario_local": not yo,
            "ingresos": ingresos,
            "egresos": egresos,
            "tarjeta": tarjeta,
            "deudas": deudas,
            "disponible": disponible,
        }

    @staticmethod
    def _con_subtotales(
        seccion: dict, fijos: list[dict], variables: list[dict], campo_estimado_variables: str = "estimado_minor",
    ) -> dict:
        """
        Agrega a `seccion` (ingresos o egresos de get_resumen_mes())
        subtotal_fijos, subtotal_variables y total: {estimado_minor,
        real_minor}. El estimado de los variables suma
        `campo_estimado_variables` de cada ítem (en egresos, el proyectado).
        """
        subtotal_fijos = {
            "estimado_minor": sum(i["estimado_minor"] for i in fijos),
            "real_minor": sum(i["real_minor"] for i in fijos),
        }
        subtotal_variables = {
            "estimado_minor": sum(i[campo_estimado_variables] for i in variables),
            "real_minor": sum(i["real_minor"] for i in variables),
        }
        return {
            **seccion,
            "subtotal_fijos": subtotal_fijos,
            "subtotal_variables": subtotal_variables,
            "total": {
                campo: subtotal_fijos[campo] + subtotal_variables[campo] for campo in ("estimado_minor", "real_minor")
            },
        }

    @staticmethod
    def _grupos_disponible(modo: str, ingresos: dict, egresos: dict, tarjeta: dict, neto_deudas: int) -> list[dict]:
        """
        El DISPONIBLE de un modo desarmado en ítems, para la calculadora de
        escenarios: [{clave, items: [{id, aporte_minor}]}] en el orden de la
        pantalla. aporte_minor: lo que suma el ítem, con su signo (los
        egresos y la tarjeta en negativo). Los ids son los de cada fila de
        get_resumen_mes(), no dependen del modo (un ítem excluido sigue
        excluido al pasar de HOY a PROYECTADO) y no se repiten entre grupos.
        La suma de todos los aportes es el disponible del modo.
        """
        hoy = modo == "hoy"
        variables = egresos["variables_con_presupuesto"] + egresos["variables_sin_presupuesto"]
        return [
            {"clave": "ingresos_fijos", "items": [
                {"id": i["id"], "aporte_minor": i["real_minor"] if hoy else i["estimado_minor"]}
                for i in ingresos["fijos"]
            ]},
            {"clave": "ingresos_variables", "items": [
                {"id": i["id"], "aporte_minor": i["real_minor"]} for i in ingresos["variables"]
            ]},
            {"clave": "egresos_fijos", "items": [
                {"id": f["id"], "aporte_minor": -(f["real_minor"] if hoy else f["estimado_minor"])}
                for f in egresos["fijos"]
            ]},
            {"clave": "egresos_variables", "items": [
                {"id": v["id"], "aporte_minor": -(v["real_minor"] if hoy else v["proyectado_minor"])}
                for v in variables
            ]},
            {"clave": "tarjeta", "items": [
                {"id": t["id"], "aporte_minor": -t["a_pagar_minor"]} for t in tarjeta["por_cuenta"]
            ]},
            {"clave": "neto_deudas", "items": [{"id": ID_ITEM_NETO_DEUDAS, "aporte_minor": neto_deudas}]},
        ]

    @staticmethod
    def calcular_escenario(grupos: list[dict], excluidos, ajuste_minor: int = 0) -> dict:
        """
        Calculadora de escenarios del DISPONIBLE (pantalla DASHBOARD,
        docs/DATA_MODEL_DECISIONS.md sección 34): el disponible de un modo
        (get_resumen_mes()["disponible"]["grupos"][modo]) sin los ítems
        `excluidos` (ids; los que no son de estos grupos se ignoran) y con
        un ajuste manual (positivo suma, negativo resta). Solo calcula: qué
        está excluido lo guarda la pantalla.

        Returns:
            {disponible_minor, grupos: {clave: lo que aporta lo incluido de
            ese grupo}, es_escenario: True si algo de estos grupos está
            excluido o hay ajuste}.
        """
        excluidos = set(excluidos)
        totales: dict[str, int] = {}
        hay_excluidos = False
        for grupo in grupos:
            total = 0
            for item in grupo["items"]:
                if item["id"] in excluidos:
                    hay_excluidos = True
                else:
                    total += item["aporte_minor"]
            totales[grupo["clave"]] = total
        return {
            "disponible_minor": sum(totales.values()) + ajuste_minor,
            "grupos": totales,
            "es_escenario": hay_excluidos or ajuste_minor != 0,
        }

    def _transacciones_del_mes(self, inicio: str, fecha_corte: str) -> list[sqlite3.Row]:
        """Todas las transacciones del Registro entre `inicio` y `fecha_corte` (inclusive), de a LOTE_TRANSACCIONES."""
        filas: list[sqlite3.Row] = []
        pagina = 1
        while True:
            lote = self._transaction_service.list_transactions(
                date_from=inicio, date_to=fecha_corte, page=pagina, per_page=LOTE_TRANSACCIONES,
            )
            filas.extend(lote)
            if len(lote) < LOTE_TRANSACCIONES:
                return filas
            pagina += 1

    def _ingresos_variables(self, transacciones: list[sqlite3.Row], moneda_id: int, usadas: set[str]) -> list[dict]:
        """
        INGRESOS VARIABLES de get_resumen_mes(): de `transacciones` (las del
        mes), los ingresos en categorías de tipo 'ingreso' que no son
        CATEGORIAS_INGRESOS_FIJOS, en la moneda pedida, sumados por tag. El
        tag se compara sin espacios de más y en MAYÚSCULAS ("clases" y
        "CLASES " son el mismo ítem); los sin tag, uno por categoría
        ("REINTEGRO (SIN TAG)"). El mayor primero; anota en `usadas` las
        monedas con alguno.
        """
        de_ingreso = {c["id"] for c in self._categorias_service.list_categories(tipo="ingreso", incluir_inactivas=True)}
        fijas = {clave_categoria(principal, sub) for principal, sub in CATEGORIAS_INGRESOS_FIJOS}
        por_id: dict[str, dict] = {}
        for t in transacciones:
            if t["tipo_movimiento"] != "ingreso" or t["categoria_id"] not in de_ingreso:
                continue
            if clave_categoria(t["categoria_principal"], t["category_name"]) in fijas:
                continue
            usadas.add(t["currency_code"])
            if t["moneda_id"] != moneda_id:
                continue
            tag = " ".join((t["tag"] or "").split()).upper()
            if tag:
                id_, concepto = f"{PREFIJO_ID_INGRESO_VARIABLE}{tag}", tag
            else:
                categoria = " ".join((t["category_name"] or "").split()).upper()
                id_, concepto = f"{PREFIJO_ID_INGRESO_SIN_TAG}{t['categoria_id']}", f"{categoria} ({TEXTO_SIN_TAG})"
            item = por_id.setdefault(id_, {
                "id": id_, "tag": tag or None, "concepto": concepto, "estimado_minor": 0, "real_minor": 0,
            })
            item["real_minor"] += t["monto_minor"]
        return sorted(por_id.values(), key=lambda i: (-i["real_minor"], i["concepto"]))

    @staticmethod
    def _pagos_tarjeta(transacciones: list[sqlite3.Row], moneda_id: int, usadas: set[str]) -> int:
        """
        PAGOS REALIZADOS de la tarjeta: de `transacciones` (las del mes), la
        suma de las de CATEGORIA_PAGO_TARJETA en la moneda pedida (un
        ingreso en esa categoría — un pago devuelto — resta). Anota en
        `usadas` las monedas con alguno.
        """
        clave_pago = clave_categoria(*CATEGORIA_PAGO_TARJETA)
        total = 0
        for t in transacciones:
            if clave_categoria(t["categoria_principal"], t["category_name"]) != clave_pago:
                continue
            usadas.add(t["currency_code"])
            if t["moneda_id"] == moneda_id:
                total += -t["monto_minor"] if t["tipo_movimiento"] == "ingreso" else t["monto_minor"]
        return total

    def _gastos_registro(
        self, mes: int, anio: int, moneda_id: int, variables: list[dict],
        usadas: set[str], codigos_por_id: dict[int, str],
    ) -> list[dict]:
        """
        Los egresos del Registro del mes por categoría de tipo 'egreso'
        (get_gasto_por_categoria()) más las que tienen presupuesto variable,
        en la moneda pedida, sin CATEGORIAS_EXCLUIDAS_GASTOS. El real, de
        PresupuestosService.get_real_variable() (no se duplica su regla de
        compartidos); presupuesto_minor, el del presupuesto variable de la
        categoría en esa moneda, o None; categoria_de_fijos, si está en
        CATEGORIAS_DE_FIJOS. La mayor primero; anota en `usadas` las monedas
        con gasto.
        """
        excluidas = {clave_categoria(principal, sub) for principal, sub in CATEGORIAS_EXCLUIDAS_GASTOS}
        de_fijos = {clave_categoria(principal, sub) for principal, sub in CATEGORIAS_DE_FIJOS}
        de_egreso = {c["id"] for c in self._categorias_service.list_categories(tipo="egreso", incluir_inactivas=True)}
        categorias: dict[str, tuple[str, str]] = {}
        for gasto in self.get_gasto_por_categoria(mes, anio):
            if gasto["categoria_id"] not in de_egreso:
                continue
            if clave_categoria(gasto["categoria_principal"], gasto["categoria_nombre"]) in excluidas:
                continue
            usadas.add(codigos_por_id.get(gasto["moneda_id"], ""))
            if gasto["moneda_id"] == moneda_id:
                categorias[gasto["categoria_id"]] = (gasto["categoria_principal"], gasto["categoria_nombre"])
        presupuestos: dict[str, int] = {}
        for p in variables:
            if p["categoria_id"] is None or p["moneda_id"] != moneda_id:
                continue  # COMPARTIDOS (sin categoría) no es una categoría; otra moneda, en su resumen
            if clave_categoria(p["categoria_principal"], p["subcategoria"]) in excluidas:
                continue
            presupuestos[p["categoria_id"]] = p["monto_estimado_minor"]
            categorias.setdefault(p["categoria_id"], (p["categoria_principal"], p["subcategoria"]))

        filas = []
        for categoria_id, (principal, subcategoria) in categorias.items():
            real = self._presupuestos_service.get_real_variable(categoria_id, moneda_id, mes, anio)
            presupuesto = presupuestos.get(categoria_id)
            if real == 0 and presupuesto is None:
                continue  # todo su gasto era la parte del otro de un compartido
            filas.append({
                "categoria_id": categoria_id,
                "categoria": (subcategoria or "").upper(),
                "real_minor": real,
                "presupuesto_minor": presupuesto,
                "categoria_de_fijos": clave_categoria(principal, subcategoria) in de_fijos,
            })
        filas.sort(key=lambda f: (-f["real_minor"], f["categoria"]))
        return filas

    def _gastos_compartidos_hasta(self, fin: str) -> list[dict]:
        """
        Los gastos compartidos con fecha anterior a `fin` (el día 1 del mes
        siguiente), con su categoría y su moneda: la de su origen, o
        MONEDA_SIN_ORIGEN_CODIGO si el origen no está en esta base (una
        transacción del otro miembro, que no se sincroniza) — el mismo
        criterio que PresupuestosService.get_real_variable_compartidos(),
        que es una suma y no sirve para agrupar por categoría ni por quién
        pagó. (SnapshotsService, en cambio, saltea esos gastos: acá se
        perderían justo los que pagó el otro.) Agregación de reporte: vive
        acá, como get_gasto_por_categoria().
        """
        sin_origen = self._db.obtener_moneda_por_codigo(MONEDA_SIN_ORIGEN_CODIGO)
        filas = self._db.fetchall(
            """
            SELECT g.hogar_id, g.pagador, g.fecha, g.estado,
                   g.monto_adeudado_minor, g.monto_pendiente_minor,
                   cat.subcategoria AS categoria,
                   COALESCE(t.moneda_id, cc.moneda_id, ccq.moneda_id, ?) AS moneda_id
            FROM gastos_compartidos g
            JOIN categorias cat          ON cat.id = g.categoria_id
            LEFT JOIN transacciones t    ON g.origen_tipo = 'transaccion'   AND t.id = g.origen_id
            LEFT JOIN compras_cuotas cc  ON g.origen_tipo = 'compra_cuotas' AND cc.id = g.origen_id
            LEFT JOIN cuotas_credito q   ON g.origen_tipo = 'cuota_credito' AND q.id = g.origen_id
            LEFT JOIN compras_cuotas ccq ON ccq.id = q.compra_id
            WHERE g.fecha < ?;
            """,
            (sin_origen["id"] if sin_origen is not None else None, fin),
        )
        return [dict(fila) for fila in filas]

    def _otros_miembros(self, hogar_id: str, yo: str) -> str:
        """Quién te debe un gasto compartido que pagaste vos: los otros miembros del hogar ("A / B")."""
        otros = [
            normalizar_persona(m["usuario_local"]) for m in self._shared_expenses_service.list_miembros(hogar_id)
            if normalizar_persona(m["usuario_local"]) != yo
        ]
        return " / ".join(otros) or PERSONA_HOGAR_SIN_MIEMBROS

    @staticmethod
    def _a_su_lado(
        me_deben: list[dict], debo: list[dict], persona: str, tipo: str, monto: int, a_favor: bool,
        moneda_codigo: str,
    ) -> None:
        """
        Agrega una línea de DEUDAS: a ME DEBEN si es a favor, si no a DEBO; un
        monto negativo va al otro lado, en positivo (alguien que te pagó de
        más: se lo debés). Los ceros no se muestran.
        """
        if monto == 0:
            return
        lado = me_deben if (monto > 0) == a_favor else debo
        lado.append({
            "persona": normalizar_persona(persona), "tipo": tipo, "monto_minor": abs(monto), "moneda": moneda_codigo,
        })
