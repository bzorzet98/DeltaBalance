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
    mes en una sola moneda, más un balance DISPONIBLE. Compone los services
    de cada dominio — IngresosService, PresupuestosService (fijos, y el real
    de cada categoría ya neto de compartidos), FeesService (cuotas),
    DebtsService (deudas informales) — y agrega una query propia sobre
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
from services.debts_service import DebtsService
from services.fees_service import FeesService
from services.ingresos_service import CLAVE_TOTAL, IngresosService
from services.presupuestos_service import MONEDA_SIN_ORIGEN_CODIGO, PresupuestosService
from services.shared_expenses_service import SharedExpensesService
from utils.categorias import clave_categoria
from utils.personas import normalizar_persona

# get_resumen_mes(): categorías del Registro que NO entran en GASTOS DEL MES
# ni en el balance (decisión del usuario: las autotransferencias no son un
# gasto, y el pago de la tarjeta ya está en CUOTAS A PAGAR). Pares
# (categoria_principal, subcategoria), comparados sin distinguir mayúsculas
# (utils/categorias.py clave_categoria()). Para dejar afuera otra
# categoría, agregarla acá.
CATEGORIAS_EXCLUIDAS_GASTOS: tuple[tuple[str, str], ...] = (
    ("MOVIMIENTO CAPITAL", "AUTOTRANSFERENCIA"),
    ("TARJETA DE CRÉDITO", "PAGO TARJETA"),
)
# get_resumen_mes(): categorías del Registro donde se pagan los gastos FIJOS
# (decisión del usuario: los fijos también se pagan desde el Registro). Se
# ven en GASTOS DEL MES, pero no entran en los "gastos variables" del
# balance: ahí ya cuentan como egresos fijos (estimados o pagados), y si no
# se restarían dos veces. Mismo formato que CATEGORIAS_EXCLUIDAS_GASTOS;
# para sumar otra, agregarla acá.
CATEGORIAS_DE_FIJOS: tuple[tuple[str, str], ...] = (
    ("EGRESOS", "VIVIENDA"),
    ("EGRESOS", "SERVICIOS BÁSICOS"),
    ("EGRESOS", "SEGUROS"),
)
# Los dos modos del balance de get_resumen_mes() (switch ESTIMADO / REAL de la pantalla).
MODOS_BALANCE = ("estimado", "real")
# Id del único ítem del grupo neto_deudas (calcular_escenario()).
ID_ITEM_NETO_DEUDAS = "neto_deudas"
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
        usuario_local: Optional[str] = None,
        moneda_codigo: str = MONEDA_RESUMEN_DEFAULT,
    ) -> dict:
        """
        Todo lo del mes para el dashboard, en UNA moneda (nunca se mezclan;
        monedas_disponibles dice en cuáles hay algo, para elegir otra):
        {
            moneda_codigo, moneda_simbolo, decimales, monedas_disponibles,
            fecha_corte (último día del mes: DEUDAS acumula hasta ahí),
            sin_usuario_local (True: sin él no se sabe qué compartidos pagó
                cada uno — GASTOS COMPARTIDOS y sus deudas quedan vacíos),
            ingresos:        {estimado_minor, real_minor, moneda,
                              items: [{id, concepto, estimado_minor, real_minor}]},
            egresos_fijos:   {estimado_minor, real_minor, pendiente_minor, moneda,
                              items: [{id, concepto, estimado_minor, real_minor, pendiente_minor}]},
            cuotas:          [{cuenta_id, cuenta, monto_minor, moneda}],
            gastos_registro: [{categoria_id, categoria, real_minor, estimado_minor,
                               categoria_de_fijos, moneda}],
            gastos_compartidos: [{categoria, monto_minor, moneda}],
            totales: {cuotas_minor, gastos_registro_minor, gastos_compartidos_minor},
            deudas: {me_deben: [{persona, tipo, monto_minor, moneda}],
                     debo: [{persona, tipo, monto_minor, moneda}],
                     subtotal_me_deben_minor, subtotal_debo_minor, neto_minor},
            balance: {modo: {ingresos_minor, egresos_fijos_minor, cuotas_minor,
                             gastos_variables_minor, neto_deudas_minor,
                             disponible_minor, grupos}} para cada modo de MODOS_BALANCE
                     (grupos: el detalle por ítem, ver _grupos_balance() y
                     calcular_escenario()),
        }

        De dónde sale cada cosa (decisiones del usuario,
        docs/DATA_MODEL_DECISIONS.md sección 34):
        - ingresos: IngresosService.get_totales() — estimado y cobrado.
        - egresos_fijos: los presupuestos 'fijo' del mes (estimado y el real
          cargado a mano). pendiente_minor: lo que falta pagar de cada fijo
          (estimado − real, nunca negativo), informativo.
        - cuotas: FeesService.resumen_por_tarjeta(), el total de cada tarjeta.
        - gastos_registro: egresos del Registro del mes por categoría, salvo
          CATEGORIAS_EXCLUIDAS_GASTOS, con el real de
          PresupuestosService.get_real_variable() (un gasto compartido que
          pagaste vos cuenta solo tu parte) y el estimado de su presupuesto
          variable (None si no tiene). También las categorías con
          presupuesto variable sin gasto todavía (real 0). categoria_de_fijos:
          está en CATEGORIAS_DE_FIJOS (se muestra, pero el balance no la
          cuenta como gasto variable).
        - gastos_compartidos: tu parte de lo que pagó el OTRO miembro en el
          mes (monto_adeudado_minor con pagador ≠ usuario_local), por
          categoría — la misma regla que el ítem COMPARTIDOS de Presupuestos.
          Informativo: no entra en el balance (lo que debés de eso ya está
          en las deudas).
        - totales: lo que suma cada tarjeta (cuotas, gastos del mes y
          compartidos), para mostrarlo.
        - deudas, ACUMULADO hasta el último día del mes (sin filtro de mes):
          informales: DebtsService.summary_by_person() de cada tab;
          compartidos: el pendiente (monto_pendiente_minor, ya descuenta los
          pagos parciales) de los gastos 'pendiente' — los que pagaste vos
          te los deben (la persona: los otros miembros del hogar), los que
          pagó otro se los debés (la persona: quien pagó). Un saldo negativo
          pasa al otro lado (ej. alguien que te pagó de más: se lo debés).
        - balance, en los dos modos (switch ESTIMADO / REAL de la pantalla,
          que solo elige cuál mostrar):
            estimado: ingresos estimados − fijos estimados − cuotas − gastos
                      variables estimados + neto de deudas;
            real:     ingresos cobrados − fijos pagados − cuotas − gastos
                      variables reales + neto de deudas.
          Gastos variables: las filas de gastos_registro que NO son de
          CATEGORIAS_DE_FIJOS — en REAL, lo gastado; en ESTIMADO, lo gastado
          más lo que falta de su presupuesto (ver _gasto_variable()).

        Raises:
            ValueError si el mes está fuera de rango o la moneda no existe.
        """
        inicio, fin = self._rango_mes(mes, anio)
        fecha_corte = (date.fromisoformat(fin) - timedelta(days=1)).isoformat()
        monedas_por_codigo = {m["codigo"]: m for m in self._db.obtener_monedas()}
        codigos_por_id = {m["id"]: codigo for codigo, m in monedas_por_codigo.items()}
        moneda = monedas_por_codigo.get(moneda_codigo)
        if moneda is None:
            raise ValueError(f"MONEDA DESCONOCIDA: {moneda_codigo}.")
        moneda_id = moneda["id"]
        yo = normalizar_persona(usuario_local)
        usadas: set[str] = set()  # monedas con algún dato este mes (monedas_disponibles)

        # --- Ingresos (filas + una de total por moneda, IngresosService.list_by_month()) ---
        filas_ingresos = self._ingresos_service.list_by_month(mes, anio)
        totales_ingresos = {f["currency_code"]: f for f in filas_ingresos if f.get(CLAVE_TOTAL)}
        usadas.update(totales_ingresos)
        total_ingresos = totales_ingresos.get(moneda_codigo, {})
        ingresos = {
            "estimado_minor": total_ingresos.get("total_estimado_minor", 0),
            "real_minor": total_ingresos.get("total_real_minor", 0),
            "items": [
                {
                    "id": f["id"], "concepto": (f["concepto"] or "").upper(),
                    "estimado_minor": f["monto_estimado_minor"], "real_minor": f["monto_real_minor"],
                }
                for f in filas_ingresos
                if not f.get(CLAVE_TOTAL) and f["moneda_id"] == moneda_id
            ],
            "moneda": moneda_codigo,
        }

        # --- Egresos fijos ---
        presupuestos = self._presupuestos_service.list_by_month(mes, anio, usuario_local)
        usadas.update(p["currency_code"] for p in presupuestos["fijos"])
        fijos = [
            {
                "id": p["id"], "concepto": (p["nombre"] or "").upper(),
                "estimado_minor": p["monto_estimado_minor"], "real_minor": p["real_minor"] or 0,
                "pendiente_minor": max(p["monto_estimado_minor"] - (p["real_minor"] or 0), 0),
            }
            for p in presupuestos["fijos"]
            if p["moneda_id"] == moneda_id
        ]
        egresos_fijos = {
            "estimado_minor": sum(f["estimado_minor"] for f in fijos),
            "real_minor": sum(f["real_minor"] for f in fijos),
            "pendiente_minor": sum(f["pendiente_minor"] for f in fijos),
            "items": fijos,
            "moneda": moneda_codigo,
        }

        # --- Cuotas ---
        tarjetas = self._fees_service.resumen_por_tarjeta(mes, anio)
        usadas.update(t["currency_code"] for t in tarjetas)
        cuotas = [
            {
                "cuenta_id": t["cuenta_id"], "cuenta": (t["account_name"] or "").upper(),
                "monto_minor": t["monto_total_minor"], "moneda": moneda_codigo,
            }
            for t in tarjetas
            if t["moneda_id"] == moneda_id and t["monto_total_minor"] != 0
        ]

        # --- Gastos del Registro ---
        gastos_registro = self._gastos_registro(mes, anio, moneda_id, moneda_codigo, presupuestos["variables"], usadas, codigos_por_id)

        # --- Gastos compartidos (del mes) y sus deudas (acumuladas) ---
        compartidos = self._gastos_compartidos_hasta(fin)
        usadas.update(codigos_por_id[g["moneda_id"]] for g in compartidos if g["moneda_id"] in codigos_por_id)
        gastos_compartidos: list[dict] = []
        if yo:
            por_categoria: dict[str, int] = defaultdict(int)
            for g in compartidos:
                if g["fecha"] >= inicio and g["moneda_id"] == moneda_id and normalizar_persona(g["pagador"]) != yo:
                    por_categoria[(g["categoria"] or "").upper()] += g["monto_adeudado_minor"]
            gastos_compartidos = [
                {"categoria": categoria, "monto_minor": monto, "moneda": moneda_codigo}
                for categoria, monto in sorted(por_categoria.items(), key=lambda item: (-item[1], item[0]))
                if monto != 0
            ]

        # --- Deudas ---
        me_deben: list[dict] = []
        debo: list[dict] = []
        for tab in ("me_deben", "debo"):
            for fila in self._debts_service.summary_by_person(tab, hasta_fecha=fecha_corte):
                usadas.add(fila["moneda_codigo"])
                if fila["moneda_id"] == moneda_id:
                    self._a_su_lado(
                        me_deben, debo, fila["entidad_persona"], TIPO_DEUDA_INFORMAL, fila["saldo_minor"],
                        a_favor=(tab == "me_deben"), moneda_codigo=moneda_codigo,
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
                    me_deben, debo, persona, TIPO_DEUDA_COMPARTIDOS, monto, a_favor=pagaste_vos, moneda_codigo=moneda_codigo,
                )
        for lado in (me_deben, debo):
            lado.sort(key=lambda d: (d["persona"], d["tipo"]))
        subtotal_me_deben = sum(d["monto_minor"] for d in me_deben)
        subtotal_debo = sum(d["monto_minor"] for d in debo)
        neto_deudas = subtotal_me_deben - subtotal_debo

        # --- Balance, en los dos modos (ver docstring) ---
        totales = {
            "cuotas_minor": sum(c["monto_minor"] for c in cuotas),
            "gastos_registro_minor": sum(g["real_minor"] for g in gastos_registro),
            "gastos_compartidos_minor": sum(g["monto_minor"] for g in gastos_compartidos),
        }
        variables = [g for g in gastos_registro if not g["categoria_de_fijos"]]
        balance = {
            modo: self._balance(
                ingresos=ingresos[f"{modo}_minor"],
                egresos_fijos=egresos_fijos[f"{modo}_minor"],
                cuotas=totales["cuotas_minor"],
                gastos_variables=sum(self._gasto_variable(g, modo) for g in variables),
                neto_deudas=neto_deudas,
                grupos=self._grupos_balance(modo, ingresos, egresos_fijos, cuotas, variables, neto_deudas),
            )
            for modo in MODOS_BALANCE
        }

        usadas.add(moneda_codigo)
        usadas.discard("")
        return {
            "moneda_codigo": moneda_codigo,
            "moneda_simbolo": moneda["simbolo"] or "",
            "decimales": moneda["decimales"],
            "monedas_disponibles": sorted(usadas, key=lambda codigo: (codigo != MONEDA_RESUMEN_DEFAULT, codigo)),
            "fecha_corte": fecha_corte,
            "sin_usuario_local": not yo,
            "ingresos": ingresos,
            "egresos_fijos": egresos_fijos,
            "cuotas": cuotas,
            "gastos_registro": gastos_registro,
            "gastos_compartidos": gastos_compartidos,
            "totales": totales,
            "deudas": {
                "me_deben": me_deben,
                "debo": debo,
                "subtotal_me_deben_minor": subtotal_me_deben,
                "subtotal_debo_minor": subtotal_debo,
                "neto_minor": neto_deudas,
            },
            "balance": balance,
        }

    @staticmethod
    def _gasto_variable(gasto: dict, modo: str) -> int:
        """
        Lo que resta una categoría de gastos variables (fila de gastos_registro)
        en el balance de `modo` (decisión del usuario):
            real:     lo gastado.
            estimado: lo gastado + lo que falta de su presupuesto (presupuesto −
                      gastado, nunca negativo) — así se ven lo real y lo
                      proyectado juntos: en la práctica, el mayor de los dos.
                      Sin presupuesto, lo gastado (no 0).
        """
        real = gasto["real_minor"]
        if modo == "real" or gasto["estimado_minor"] is None:
            return real
        return real + max(gasto["estimado_minor"] - real, 0)

    @staticmethod
    def _balance(
        ingresos: int, egresos_fijos: int, cuotas: int, gastos_variables: int, neto_deudas: int, grupos: list[dict],
    ) -> dict:
        """Un modo del balance de get_resumen_mes(): sus componentes, el DISPONIBLE y sus grupos (calcular_escenario())."""
        return {
            "ingresos_minor": ingresos,
            "egresos_fijos_minor": egresos_fijos,
            "cuotas_minor": cuotas,
            "gastos_variables_minor": gastos_variables,
            "neto_deudas_minor": neto_deudas,
            "disponible_minor": ingresos - egresos_fijos - cuotas - gastos_variables + neto_deudas,
            "grupos": grupos,
        }

    @staticmethod
    def _grupos_balance(
        modo: str, ingresos: dict, egresos_fijos: dict, cuotas: list[dict], variables: list[dict], neto_deudas: int,
    ) -> list[dict]:
        """
        El balance de un modo desarmado en ítems, para la calculadora de
        escenarios: [{clave, items: [{id, nombre, aporte_minor}]}] en el
        orden del DISPONIBLE. aporte_minor: lo que suma el ítem, con su
        signo (los egresos en negativo). Los ids no dependen del modo (un
        ítem excluido sigue excluido al pasar de ESTIMADO a REAL) y no se
        repiten entre grupos. La suma de todos los aportes es el
        disponible_minor del modo.
        """
        return [
            {"clave": "ingresos", "items": [
                {"id": f"ingreso:{i['id']}", "nombre": i["concepto"], "aporte_minor": i[f"{modo}_minor"]}
                for i in ingresos["items"]
            ]},
            {"clave": "egresos_fijos", "items": [
                {"id": f"fijo:{f['id']}", "nombre": f["concepto"], "aporte_minor": -f[f"{modo}_minor"]}
                for f in egresos_fijos["items"]
            ]},
            {"clave": "cuotas", "items": [
                {"id": f"cuota:{c['cuenta_id']}", "nombre": c["cuenta"], "aporte_minor": -c["monto_minor"]}
                for c in cuotas
            ]},
            {"clave": "gastos_variables", "items": [
                {
                    "id": f"variable:{g['categoria_id']}", "nombre": g["categoria"],
                    "aporte_minor": -DashboardService._gasto_variable(g, modo),
                }
                for g in variables
            ]},
            {"clave": "neto_deudas", "items": [
                {"id": ID_ITEM_NETO_DEUDAS, "nombre": "NETO DEUDAS", "aporte_minor": neto_deudas},
            ]},
        ]

    @staticmethod
    def calcular_escenario(balance_modo: dict, excluidos, ajuste_minor: int = 0) -> dict:
        """
        Calculadora de escenarios del BALANCE (pantalla DASHBOARD,
        docs/DATA_MODEL_DECISIONS.md sección 34): el DISPONIBLE de un modo
        (get_resumen_mes()["balance"][modo]) sin los ítems `excluidos` (ids
        de sus grupos[].items[]; los que no son de este balance se ignoran)
        y con un ajuste manual (positivo suma, negativo resta). Solo
        calcula: qué está excluido lo guarda la pantalla.

        Returns:
            {disponible_minor, grupos: {clave: lo que aporta lo incluido de
            ese grupo}, es_escenario: True si algo de este balance está
            excluido o hay ajuste}.
        """
        excluidos = set(excluidos)
        grupos: dict[str, int] = {}
        hay_excluidos = False
        for grupo in balance_modo["grupos"]:
            total = 0
            for item in grupo["items"]:
                if item["id"] in excluidos:
                    hay_excluidos = True
                else:
                    total += item["aporte_minor"]
            grupos[grupo["clave"]] = total
        return {
            "disponible_minor": sum(grupos.values()) + ajuste_minor,
            "grupos": grupos,
            "es_escenario": hay_excluidos or ajuste_minor != 0,
        }

    def _gastos_registro(
        self, mes: int, anio: int, moneda_id: int, moneda_codigo: str, variables: list[dict],
        usadas: set[str], codigos_por_id: dict[int, str],
    ) -> list[dict]:
        """
        GASTOS DEL MES de get_resumen_mes(): las categorías con egresos en el
        Registro (get_gasto_por_categoria()) más las que tienen presupuesto
        variable, en la moneda pedida, sin CATEGORIAS_EXCLUIDAS_GASTOS. El
        real, de PresupuestosService.get_real_variable() (no se duplica su
        regla de compartidos); el estimado, el del presupuesto variable de la
        categoría en esa moneda, o None; categoria_de_fijos, si está en
        CATEGORIAS_DE_FIJOS. La mayor primero; anota en `usadas` las monedas
        con gasto.
        """
        excluidas = {clave_categoria(principal, sub) for principal, sub in CATEGORIAS_EXCLUIDAS_GASTOS}
        de_fijos = {clave_categoria(principal, sub) for principal, sub in CATEGORIAS_DE_FIJOS}
        categorias: dict[str, tuple[str, str]] = {}
        for gasto in self.get_gasto_por_categoria(mes, anio):
            if clave_categoria(gasto["categoria_principal"], gasto["categoria_nombre"]) in excluidas:
                continue
            usadas.add(codigos_por_id.get(gasto["moneda_id"], ""))
            if gasto["moneda_id"] == moneda_id:
                categorias[gasto["categoria_id"]] = (gasto["categoria_principal"], gasto["categoria_nombre"])
        estimados: dict[str, int] = {}
        for p in variables:
            if p["categoria_id"] is None or p["moneda_id"] != moneda_id:
                continue  # COMPARTIDOS va en su tarjeta; otra moneda, en su resumen
            if clave_categoria(p["categoria_principal"], p["subcategoria"]) in excluidas:
                continue
            estimados[p["categoria_id"]] = p["monto_estimado_minor"]
            categorias.setdefault(p["categoria_id"], (p["categoria_principal"], p["subcategoria"]))

        filas = []
        for categoria_id, (principal, subcategoria) in categorias.items():
            real = self._presupuestos_service.get_real_variable(categoria_id, moneda_id, mes, anio)
            estimado = estimados.get(categoria_id)
            if real == 0 and estimado is None:
                continue  # todo su gasto era la parte del otro de un compartido
            filas.append({
                "categoria_id": categoria_id,
                "categoria": (subcategoria or "").upper(),
                "real_minor": real,
                "estimado_minor": estimado,
                "categoria_de_fijos": clave_categoria(principal, subcategoria) in de_fijos,
                "moneda": moneda_codigo,
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
