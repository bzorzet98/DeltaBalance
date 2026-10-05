"""
DeltaBalance — ui/screens/resumen_mes.py

Pantalla DASHBOARD: el resumen del mes en UNA tarjeta, el BALANCE, con un
selector de mes "‹ OCTUBRE 2026 ›" en la barra superior.

BALANCE (pedido del usuario): colapsado por default — solo el DISPONIBLE en
grande —; ▼ / ▲ muestra / oculta el detalle. Switch [ESTIMADO] [REAL]: el
service ya devuelve los dos balances (get_resumen_mes()["balance"][modo]),
así que cambiar de modo o abrir el detalle solo redibuja esa tarjeta
(_pintar_balance()), sin volver a pedir datos. Las dos cosas se recuerdan en
el estado de la pantalla.
Es la pantalla que abre la app (ui/app.py). Se llama resumen_mes.py porque
ui/screens/dashboard.py es, por historia, la pantalla del Registro.

Dashboard unificado (pedido del usuario): no hay tarjetas aparte — el BALANCE
expandido ES el dashboard. Cada grupo muestra sus ítems (con checkbox, ver
"Escenarios") y debajo, en gris y sin checkbox, la información que no suma
al DISPONIBLE (_info_grupo()):
- INGRESOS: el otro número — COBRADOS en modo ESTIMADO, ESTIMADOS en REAL.
- EGRESOS FIJOS: PAGADOS (o ESTIMADOS) · FALTA, y lo gastado en el Registro
  en categorías de fijos (no cuenta como gasto variable: ya está en los
  fijos).
- GASTOS VARIABLES: cada categoría con la barra real / presupuesto (roja si
  se pasó) y el otro número — REAL en modo ESTIMADO, PRESUP. en REAL —. El
  monto de la fila es siempre el que suma en el modo elegido (en ESTIMADO, lo
  gastado + lo que falta del presupuesto; sin presupuesto, lo gastado —
  DashboardService._gasto_variable()).
- NETO DEUDAS: un solo checkbox (el neto) y debajo ME DEBEN / DEBO por
  persona, ACUMULADO hasta fin de mes (no solo lo del mes, pedido del
  usuario). Los compartidos del mes que pagó el otro se informan pero no
  suman aparte: lo que debés de eso ya está en DEBO.

Escenarios (calculadora del BALANCE, pedido del usuario): expandido, cada
grupo del DISPONIBLE (INGRESOS, EGRESOS FIJOS, CUOTAS, GASTOS VARIABLES,
NETO DEUDAS — get_resumen_mes()["balance"][modo]["grupos"]) tiene su
checkbox, y cada ítem el suyo (un ingreso, un fijo, una tarjeta, una
categoría). El del grupo es de tres estados: ☑ todos, ☐ ninguno, ▣ algunos;
clickearlo desmarca todos si estaban todos, si no los marca todos.
Desmarcar un ítem deja el grupo en ▣ y los demás como estaban. El grupo
suma solo lo marcado. "+ AJUSTE MANUAL" es un CampoMonto (fórmulas con =;
negativo resta; vacío no cambia nada: 0 o ↺ lo vuelven a cero). El
DISPONIBLE — también colapsado — se recalcula al instante con
DashboardService.calcular_escenario() y dice "(ESCENARIO)" si hay algo
desmarcado o ajuste; ↺ vuelve todo a marcado y ajuste 0. Lo desmarcado
(ids de ítem, iguales en ESTIMADO y REAL) y el ajuste se guardan en
.deltabalance_prefs.json (ui/utils/prefs.py, CLAUDE.md §12) bajo
"balance_escenario_{mes}_{anio}_{moneda}": por moneda, porque la misma
tarjeta puede tener cuotas en ARS y en USD. Se guarda lo EXCLUIDO, así un
ítem nuevo aparece marcado.

Todos los datos salen de UNA llamada: DashboardService.get_resumen_mes() —
las reglas de cada número (qué categorías no cuentan, la parte del usuario
en los compartidos, lo pendiente de los fijos, las deudas acumuladas) viven
ahí, ver su docstring y docs/DATA_MODEL_DECISIONS.md sección 34. Esta
pantalla solo dibuja.

Moneda: el resumen es de una sola moneda (nunca se suman pesos con
dólares). Por defecto ARS; si en el mes hay datos en otra
(monedas_disponibles), aparecen pills para cambiar.

Colores: la información de cada grupo en gris (pedido del usuario); ME DEBEN
en verde y DEBO en rojo; la barra de GASTOS VARIABLES en rojo si se pasó;
DISPONIBLE en verde si es positivo, rojo si es negativo.

Estado propio (mes, año, moneda) en un almacén por página: se conserva
cuando ui/app.py reconstruye la pantalla porque otra cambió datos.

Reglas de arquitectura: solo DashboardService (CLAUDE.md §2/§3). El
usuario local (para los compartidos) sale de ui/components/usuario_local.py
(CLAUDE.md §12), como en Presupuestos y Compartidos.
"""

from datetime import date
from typing import Optional

import flet as ft

from services.dashboard_service import DashboardService
from ui.components.campo_monto import CampoMonto
from ui.components.tabla_planilla import mostrar_mensaje, pantalla_planilla
from ui.components.usuario_local import leer_usuario_local
from ui.utils.prefs import escribir_pref, leer_pref
from ui.theme.tabla_tokens import (
    BG_SUPERFICIE,
    BORDER_DEFAULT,
    BTN_COMPARTIR,
    PESO_HEADER,
    PESO_MONTO,
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_NEGATIVO,
    TEXT_POSITIVO,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_SOBRE_BOTON,
)
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---

TITULO_PANTALLA = "DASHBOARD"
MESES = (
    "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
    "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE",
)
MONEDA_DEFAULT = "ARS"

ANCHO_CONTENIDO = 720          # la tarjeta no se estira más que esto
ESPACIADO_FILAS = 6
ESPACIADO_CABECERA = 12
PADDING_TARJETA = 16
RADIO_TARJETA = 8
ANCHO_BORDE = 1
SANGRIA_SUBLISTA = 16          # los ítems de cada grupo, bajo su checkbox
SANGRIA_INFO = 48              # las líneas de información de un grupo, más adentro que los checkbox de los ítems
SANGRIA_INFO_LISTA = 64        # las personas de ME DEBEN / DEBO
SEPARADOR_INFO = "   ·   "
ESPACIO_ENTRE_GRUPOS = 8       # aire entre un grupo (con su información) y el siguiente

ANCHO_MONTO = 150              # columna de montos, alineada a la derecha
ANCHO_BARRA = 120              # barra real / presupuesto de GASTOS VARIABLES
ALTO_BARRA = 8
RADIO_BARRA = 4
ANCHO_OTRO_VALOR = 150         # al lado de la barra: REAL (modo ESTIMADO) o PRESUP. (modo REAL)

ALTO_CABECERA = 56
ANCHO_TEXTO_PERIODO = 170
RADIO_CONTROL = 8
PADDING_PILL_H = 12
ALTO_PILL = 28
RADIO_PILL = 14

TAMANIO_DISPONIBLE = 28        # DISPONIBLE, "fuente grande" (pedido del usuario)
COLOR_FONDO_BARRA = BORDER_DEFAULT

TIPO_DEUDA_TEXTO = {"informal": "INFORMAL", "compartidos": "COMPARTIDOS"}
# Id de un ítem de GASTOS VARIABLES = este prefijo + categoria_id (igual que DashboardService._grupos_balance):
# con él cada ítem encuentra su fila de get_resumen_mes()["gastos_registro"] (barra y presupuesto).
PREFIJO_ID_VARIABLE = "variable:"

# Switch del BALANCE (claves = DashboardService.MODOS_BALANCE) y sus líneas.
MODOS_BALANCE_TEXTO = (("estimado", "ESTIMADO"), ("real", "REAL"))
MODO_BALANCE_DEFAULT = "estimado"
ETIQUETAS_BALANCE = {  # por modo y clave de grupo (get_resumen_mes()["balance"][modo]["grupos"])
    "estimado": {
        "ingresos": "INGRESOS ESTIMADOS",
        "egresos_fijos": "EGRESOS FIJOS ESTIMADOS",
        "cuotas": "CUOTAS",
        "gastos_variables": "GASTOS VARIABLES ESTIMADOS",
        "neto_deudas": "NETO DEUDAS",
    },
    "real": {
        "ingresos": "INGRESOS COBRADOS",
        "egresos_fijos": "EGRESOS FIJOS PAGADOS",
        "cuotas": "CUOTAS",
        "gastos_variables": "GASTOS VARIABLES REALES",
        "neto_deudas": "NETO DEUDAS",
    },
}
# Calculadora de escenarios (ver docstring, "Escenarios").
PREFIJO_PREF_ESCENARIO = "balance_escenario"   # + _{mes}_{anio}_{moneda} en .deltabalance_prefs.json
GRUPOS_SIN_DETALLE = ("neto_deudas",)          # un solo ítem: solo el checkbox del grupo
TEXTO_ESCENARIO = " (ESCENARIO)"
ANCHO_AJUSTE = 200
HINT_AJUSTE = "= 500000 - 200000"


# ============================================================
# ESTADO PROPIO POR PÁGINA
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {
            "mes": hoy.month, "anio": hoy.year, "moneda": MONEDA_DEFAULT,
            "modo_balance": MODO_BALANCE_DEFAULT, "balance_expandido": False,  # colapsado por default
        }
        _ESTADOS_UI[id(page)] = ui
    return ui


# ============================================================
# PANTALLA
# ============================================================

def build(page: ft.Page, dashboard_service: DashboardService) -> ft.Control:
    ui = _estado_ui(page)
    raiz = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    # El último resumen pedido y la tarjeta BALANCE, que el switch y ▼ / ▲ redibujan solos.
    datos: dict = {"resumen": None}
    contenedor_balance = ft.Container()
    refs_balance: dict = {"campo_ajuste": None}  # el CampoMonto del ajuste manual (su fórmula, al confirmar)

    # ------------------------------------------------------------
    # Formato
    # ------------------------------------------------------------

    def _monto(resumen: dict, minor: int, signo: int) -> str:
        """'+$1,295,460.00' / '-$180,000.00' (signo +1 / -1); el cero, sin signo."""
        texto = amount_display(abs(minor), resumen["decimales"], resumen["moneda_simbolo"])
        if minor == 0:
            return texto
        return f"{'+' if signo * minor > 0 else '-'}{texto}"

    def _texto(texto: str, color: str = TEXT_PRIMARY, weight=None, size: int = TypographyTokens.METADATA_SIZE,
               expand: bool = False) -> ft.Text:
        return ft.Text(
            texto, color=color, weight=weight, size=size, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=texto or None, expand=expand,
        )

    def _fila(etiqueta: str, monto: str, color: str = TEXT_PRIMARY, sangria: int = 0) -> ft.Control:
        """Etiqueta a la izquierda y el monto a la derecha, sin checkbox (las personas de ME DEBEN / DEBO)."""
        return ft.Container(
            padding=ft.Padding.only(left=sangria),
            content=ft.Row(
                [
                    _texto(etiqueta, TEXT_SECONDARY, expand=True),
                    ft.Container(
                        width=ANCHO_MONTO, alignment=ft.Alignment.CENTER_RIGHT,
                        content=_texto(monto, color, PESO_MONTO),
                    ),
                ],
                spacing=ESPACIADO_FILAS,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _tarjeta(titulo: str, resumen: dict, filas: list[ft.Control],
                 acciones: Optional[list[ft.Control]] = None) -> ft.Control:
        """acciones: controles de la cabecera entre el título y la moneda (el switch del BALANCE)."""
        return ft.Container(
            bgcolor=BG_SUPERFICIE,
            border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
            border_radius=RADIO_TARJETA,
            padding=PADDING_TARJETA,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            _texto(
                                titulo, TEXT_PRIMARY, TypographyTokens.SECTION_TITLE_WEIGHT,
                                TypographyTokens.SECTION_TITLE_SIZE, expand=True,
                            ),
                            *(acciones or []),
                            _texto(resumen["moneda_codigo"], TEXT_SECONDARY),
                        ],
                        spacing=ESPACIADO_FILAS,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    *filas,
                ],
                spacing=ESPACIADO_FILAS,
            ),
        )

    def _pill(texto: str, activa: bool, on_click) -> ft.Control:
        """Pill de selección (monedas, ESTIMADO / REAL): la activa, rellena."""
        return ft.Container(
            height=ALTO_PILL,
            padding=ft.Padding.symmetric(horizontal=PADDING_PILL_H),
            border_radius=RADIO_PILL,
            bgcolor=BTN_COMPARTIR if activa else None,
            border=None if activa else ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
            alignment=ft.Alignment.CENTER,
            on_click=lambda e: on_click(),
            content=ft.Text(
                texto, size=TypographyTokens.LABEL_SIZE, weight=PESO_HEADER,
                color=TEXT_SOBRE_BOTON if activa else TEXT_SECONDARY,
            ),
        )

    def _barra(real: int, estimado: int) -> ft.Control:
        """Real / estimado; roja si se pasó del estimado."""
        proporcion = min(real / estimado, 1.0) if estimado > 0 else (1.0 if real > 0 else 0.0)
        color = TEXT_NEGATIVO if real > estimado else TEXT_ACCENT
        lleno = round(ANCHO_BARRA * proporcion)
        return ft.Container(
            width=ANCHO_BARRA, height=ALTO_BARRA, border_radius=RADIO_BARRA, bgcolor=COLOR_FONDO_BARRA,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Row([ft.Container(width=lleno, height=ALTO_BARRA, bgcolor=color)], spacing=0),
        )

    # ------------------------------------------------------------
    # Detalle de cada grupo del BALANCE (ver docstring, "Dashboard unificado")
    # ------------------------------------------------------------

    def _info(texto: str, sangria: int = SANGRIA_INFO) -> ft.Control:
        """Línea de información de un grupo: en gris y sin checkbox (no suma al DISPONIBLE)."""
        return ft.Container(
            padding=ft.Padding.only(left=sangria),
            content=_texto(texto, TEXT_SECONDARY, size=TypographyTokens.LABEL_SIZE),
        )

    def _lineas_deuda(r: dict, deudas: list[dict], signo: int) -> list[ft.Control]:
        color = TEXT_POSITIVO if signo > 0 else TEXT_NEGATIVO
        return [
            _fila(
                f"{d['persona']} ({TIPO_DEUDA_TEXTO.get(d['tipo'], d['tipo'].upper())})",
                _monto(r, d["monto_minor"], signo), color, sangria=SANGRIA_INFO_LISTA,
            )
            for d in deudas
        ] or [_info("NADA.", SANGRIA_INFO_LISTA)]

    def _info_ingresos(r: dict, modo: str) -> list[ft.Control]:
        """El otro número del grupo: lo cobrado en modo ESTIMADO, lo estimado en modo REAL."""
        ingresos = r["ingresos"]
        if modo == "estimado":
            return [_info(f"COBRADOS: {_monto(r, ingresos['real_minor'], 1)}")]
        return [_info(f"ESTIMADOS: {_monto(r, ingresos['estimado_minor'], 1)}")]

    def _info_fijos(r: dict, modo: str) -> list[ft.Control]:
        """PAGADOS (o ESTIMADOS) y lo que FALTA pagar; y lo pagado en el Registro en categorías de fijos."""
        fijos = r["egresos_fijos"]
        otro = (
            f"PAGADOS: {_monto(r, fijos['real_minor'], -1)}" if modo == "estimado"
            else f"ESTIMADOS: {_monto(r, fijos['estimado_minor'], -1)}"
        )
        lineas = [_info(f"{otro}{SEPARADOR_INFO}FALTA: {_monto(r, fijos['pendiente_minor'], -1)}")]
        en_registro = [g for g in r["gastos_registro"] if g["categoria_de_fijos"] and g["real_minor"]]
        if en_registro:
            lineas.append(_info("EN EL REGISTRO (CATEGORÍAS DE FIJOS): " + SEPARADOR_INFO.join(
                f"{g['categoria']} {_monto(r, g['real_minor'], -1)}" for g in en_registro
            )))
        return lineas

    def _info_deudas(r: dict) -> list[ft.Control]:
        """ME DEBEN / DEBO acumulados (sin checkbox: el grupo suma el neto) y los compartidos del mes."""
        deudas = r["deudas"]
        anio, mes, dia = r["fecha_corte"].split("-")
        lineas = [
            _info(f"ACUMULADO HASTA EL {dia}/{mes}/{anio} — NO SOLO LO DEL MES."),
            _info("ME DEBEN:"), *_lineas_deuda(r, deudas["me_deben"], 1),
            _info("DEBO:"), *_lineas_deuda(r, deudas["debo"], -1),
        ]
        if r["sin_usuario_local"]:
            lineas.append(_info("SIN USUARIO LOCAL: SOLO LAS DEUDAS INFORMALES (FALTAN LAS DE COMPARTIDOS)."))
        elif r["totales"]["gastos_compartidos_minor"]:
            lineas.append(_info(
                "COMPARTIDOS DEL MES QUE PAGÓ EL OTRO (TU PARTE): "
                f"{_monto(r, r['totales']['gastos_compartidos_minor'], -1)} — YA ESTÁ EN DEBO, NO SUMA APARTE."
            ))
        return lineas

    def _info_grupo(r: dict, grupo: dict, modo: str) -> list[ft.Control]:
        clave = grupo["clave"]
        if clave == "neto_deudas":
            return _info_deudas(r)
        lineas = [] if grupo["items"] else [_info("NADA ESTE MES.")]
        if clave == "ingresos":
            lineas += _info_ingresos(r, modo)
        elif clave == "egresos_fijos":
            lineas += _info_fijos(r, modo)
        elif clave == "gastos_variables" and grupo["items"]:
            if modo == "estimado":
                lineas.append(_info("CADA CATEGORÍA RESTA LO GASTADO + LO QUE FALTA DEL PRESUPUESTO (SIN PRESUPUESTO, LO GASTADO)."))
            lineas.append(_info("BARRA: LO GASTADO CONTRA EL PRESUPUESTO (ROJA SI SE PASÓ). SIN PRESUPUESTO, SIN BARRA."))
        return lineas

    def _medio_variable(r: dict, gasto: Optional[dict], modo: str) -> Optional[ft.Control]:
        """Barra real / presupuesto y el otro número (REAL en modo ESTIMADO, PRESUP. en modo REAL)."""
        if gasto is None:
            return None
        estimado = gasto["estimado_minor"]
        if modo == "estimado":
            otro = f"REAL {amount_display(gasto['real_minor'], r['decimales'], r['moneda_simbolo'])}"
        else:
            otro = f"PRESUP. {amount_display(estimado, r['decimales'], r['moneda_simbolo'])}" if estimado is not None else ""
        return ft.Row(
            [
                _barra(gasto["real_minor"], estimado) if estimado is not None else ft.Container(width=ANCHO_BARRA),
                ft.Container(width=ANCHO_OTRO_VALOR, alignment=ft.Alignment.CENTER_RIGHT, content=_texto(otro, TEXT_MUTED)),
            ],
            spacing=ESPACIADO_FILAS,
        )

    # --- Calculadora de escenarios del BALANCE (ver docstring, "Escenarios") ---

    def _clave_escenario() -> str:
        return f"{PREFIJO_PREF_ESCENARIO}_{ui['mes']}_{ui['anio']}_{ui['moneda']}"

    def _escenario() -> dict:
        """{excluidos: set de ids, ajuste_minor, ajuste_formula} de este mes y moneda (prefs; vacío si no hay o no se lee)."""
        guardado = leer_pref(_clave_escenario())
        if not isinstance(guardado, dict):
            guardado = {}
        excluidos = guardado.get("excluidos")
        ajuste = guardado.get("ajuste_minor")
        formula = guardado.get("ajuste_formula")
        return {
            "excluidos": {e for e in excluidos if isinstance(e, str)} if isinstance(excluidos, list) else set(),
            "ajuste_minor": ajuste if isinstance(ajuste, int) and not isinstance(ajuste, bool) else 0,
            "ajuste_formula": formula if isinstance(formula, str) else None,
        }

    def _guardar_escenario(escenario: dict) -> None:
        escribir_pref(_clave_escenario(), {
            "excluidos": sorted(escenario["excluidos"]),
            "ajuste_minor": escenario["ajuste_minor"],
            "ajuste_formula": escenario["ajuste_formula"],
        })

    def _repintar_balance() -> None:
        _pintar_balance()
        page.update(contenedor_balance)

    def _alternar_grupo(ids: list[str]) -> None:
        """Click en un padre: si estaban todos marcados, desmarca todos; si no, marca todos."""
        escenario = _escenario()
        if all(id_ not in escenario["excluidos"] for id_ in ids):
            escenario["excluidos"].update(ids)
        else:
            escenario["excluidos"].difference_update(ids)
        _guardar_escenario(escenario)
        _repintar_balance()

    def _alternar_item(id_: str) -> None:
        escenario = _escenario()
        escenario["excluidos"] ^= {id_}
        _guardar_escenario(escenario)
        _repintar_balance()

    def _al_confirmar_ajuste(monto_minor: int) -> None:
        escenario = _escenario()
        escenario["ajuste_minor"] = monto_minor
        escenario["ajuste_formula"] = refs_balance["campo_ajuste"].formula
        _guardar_escenario(escenario)
        _repintar_balance()

    def _resetear_escenario() -> None:
        escribir_pref(_clave_escenario(), None)  # todo marcado y ajuste 0
        _repintar_balance()

    def _fila_check(etiqueta: str, monto: str, valor: Optional[bool], on_cambio, incluida: bool,
                    tristate: bool = False, negrita: bool = False, sangria: int = 0, habilitada: bool = True,
                    medio: Optional[ft.Control] = None) -> ft.Control:
        """
        Checkbox + etiqueta + (opcional) algo en el medio + monto. El valor que trae el click se ignora:
        on_cambio decide (ver _alternar_grupo()).
        """
        color = (TEXT_PRIMARY if negrita else TEXT_SECONDARY) if incluida else TEXT_MUTED
        controles: list[ft.Control] = [
            ft.Checkbox(value=valor, tristate=tristate, disabled=not habilitada, on_change=lambda e: on_cambio()),
            _texto(etiqueta, color, PESO_MONTO if negrita else None, expand=True),
        ]
        if medio is not None:
            controles.append(medio)
        controles.append(ft.Container(
            width=ANCHO_MONTO, alignment=ft.Alignment.CENTER_RIGHT,
            content=_texto(monto, TEXT_PRIMARY if incluida else TEXT_MUTED, PESO_MONTO),
        ))
        return ft.Container(
            padding=ft.Padding.only(left=sangria),
            content=ft.Row(controles, spacing=ESPACIADO_FILAS, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        )

    def _filas_grupo(r: dict, grupo: dict, modo: str, excluidos: set[str], total_incluido: int) -> list[ft.Control]:
        """
        Padre de tres estados (☑ todos, ☐ ninguno, ▣ algunos) con lo incluido, un hijo por ítem (en GASTOS
        VARIABLES, con su barra) y debajo las líneas de información del grupo (_info_grupo()).
        """
        ids = [item["id"] for item in grupo["items"]]
        incluidos = [id_ for id_ in ids if id_ not in excluidos]
        estado = (True if len(incluidos) == len(ids) else (False if not incluidos else None)) if ids else False
        filas = [_fila_check(
            ETIQUETAS_BALANCE[modo][grupo["clave"]], _monto(r, total_incluido, 1), estado,
            lambda: _alternar_grupo(ids), incluida=bool(incluidos), tristate=True, negrita=True, habilitada=bool(ids),
        )]
        if grupo["clave"] not in GRUPOS_SIN_DETALLE:
            gastos = {f"{PREFIJO_ID_VARIABLE}{g['categoria_id']}": g for g in r["gastos_registro"]}
            for item in grupo["items"]:
                incluido = item["id"] not in excluidos
                filas.append(_fila_check(
                    item["nombre"], _monto(r, item["aporte_minor"], 1), incluido,
                    lambda id_=item["id"]: _alternar_item(id_), incluida=incluido, sangria=SANGRIA_SUBLISTA,
                    medio=_medio_variable(r, gastos.get(item["id"]), modo) if grupo["clave"] == "gastos_variables" else None,
                ))
        return filas + _info_grupo(r, grupo, modo)

    def _fila_ajuste(r: dict, escenario: dict) -> ft.Control:
        # Fórmulas con "=" y persistir_formula=True (CLAUDE.md §9): muestra un valor guardado.
        campo = CampoMonto(
            page, on_confirmar=_al_confirmar_ajuste, decimales=r["decimales"], persistir_formula=True,
            valor_inicial_minor=escenario["ajuste_minor"] or None, formula_inicial=escenario["ajuste_formula"],
            width=ANCHO_AJUSTE, hint_text=HINT_AJUSTE,
        )
        refs_balance["campo_ajuste"] = campo
        return ft.Row(
            [_texto("+ AJUSTE MANUAL (NEGATIVO RESTA)", TEXT_SECONDARY, expand=True), campo.control],
            spacing=ESPACIADO_FILAS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _tarjeta_balance(r: dict) -> ft.Control:
        """
        Colapsada: solo el DISPONIBLE del modo elegido, con el escenario aplicado. Expandida: el
        dashboard entero — cada grupo con su checkbox, el de cada ítem y su información —, el
        ajuste manual y el DISPONIBLE.
        """
        modo = ui["modo_balance"]
        expandido = ui["balance_expandido"]
        b = r["balance"][modo]
        escenario = _escenario()
        calculo = dashboard_service.calcular_escenario(b, escenario["excluidos"], escenario["ajuste_minor"])
        disponible = calculo["disponible_minor"]
        acciones = [
            ft.Row(
                [_pill(texto, clave == modo, lambda c=clave: _cambiar_modo(c)) for clave, texto in MODOS_BALANCE_TEXTO],
                spacing=ESPACIADO_FILAS,
            ),
            ft.IconButton(
                icon=ft.Icons.RESTART_ALT, icon_color=TEXT_SECONDARY, disabled=not calculo["es_escenario"],
                tooltip="VOLVER A TODO MARCADO Y AJUSTE EN 0", on_click=lambda e: _resetear_escenario(),
            ),
            ft.IconButton(
                icon=ft.Icons.EXPAND_LESS if expandido else ft.Icons.EXPAND_MORE, icon_color=TEXT_SECONDARY,
                tooltip="OCULTAR DETALLE" if expandido else "VER DETALLE", on_click=lambda e: _alternar_detalle(),
            ),
        ]
        filas: list[ft.Control] = []
        if expandido:
            for grupo in b["grupos"]:
                filas += _filas_grupo(r, grupo, modo, escenario["excluidos"], calculo["grupos"][grupo["clave"]])
                filas.append(ft.Container(height=ESPACIO_ENTRE_GRUPOS))
            filas += [_fila_ajuste(r, escenario), ft.Divider(height=1, color=BORDER_DEFAULT)]
        filas.append(ft.Row(
            [
                _texto(
                    "DISPONIBLE" + (TEXTO_ESCENARIO if calculo["es_escenario"] else ""),
                    TEXT_PRIMARY, PESO_MONTO, TAMANIO_DISPONIBLE, expand=True,
                ),
                _texto(
                    _monto(r, disponible, 1), TEXT_POSITIVO if disponible >= 0 else TEXT_NEGATIVO,
                    PESO_MONTO, TAMANIO_DISPONIBLE,
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ))
        return _tarjeta("BALANCE", r, filas, acciones=acciones)

    def _pintar_balance() -> None:
        contenedor_balance.content = _tarjeta_balance(datos["resumen"])

    def _alternar_detalle() -> None:
        ui["balance_expandido"] = not ui["balance_expandido"]
        _repintar_balance()

    def _cambiar_modo(modo: str) -> None:
        if modo == ui["modo_balance"]:
            return
        ui["modo_balance"] = modo
        _repintar_balance()

    # ------------------------------------------------------------
    # Cabecera: título, monedas y selector de mes
    # ------------------------------------------------------------

    def _cambiar_mes(delta: int) -> None:
        indice = ui["anio"] * 12 + (ui["mes"] - 1) + delta
        ui["anio"], ui["mes"] = indice // 12, indice % 12 + 1
        _redibujar()

    def _cambiar_moneda(codigo: str) -> None:
        if codigo == ui["moneda"]:
            return
        ui["moneda"] = codigo
        _redibujar()

    def _cabecera(monedas: list[str]) -> ft.Control:
        selector_mes = ft.Container(
            height=ALTO_CABECERA,
            border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
            border_radius=RADIO_CONTROL,
            bgcolor=BG_SUPERFICIE,
            content=ft.Row(
                [
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_LEFT, icon_color=TEXT_SECONDARY, tooltip="MES ANTERIOR",
                        on_click=lambda e: _cambiar_mes(-1),
                    ),
                    ft.Container(
                        width=ANCHO_TEXTO_PERIODO, alignment=ft.Alignment.CENTER,
                        content=ft.Text(
                            f"{MESES[ui['mes'] - 1]} {ui['anio']}",
                            size=TypographyTokens.SECTION_TITLE_SIZE, weight=PESO_HEADER, color=TEXT_PRIMARY,
                        ),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_RIGHT, icon_color=TEXT_SECONDARY, tooltip="MES SIGUIENTE",
                        on_click=lambda e: _cambiar_mes(1),
                    ),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
        controles: list[ft.Control] = [
            ft.Text(
                TITULO_PANTALLA, size=TypographyTokens.PAGE_TITLE_SIZE,
                weight=TypographyTokens.PAGE_TITLE_WEIGHT, color=TEXT_PRIMARY,
            ),
            ft.Container(expand=True),
        ]
        if len(monedas) > 1:
            controles.append(ft.Row(
                [_pill(codigo, codigo == ui["moneda"], lambda c=codigo: _cambiar_moneda(c)) for codigo in monedas],
                spacing=ESPACIADO_FILAS,
            ))
        controles.append(selector_mes)
        return ft.Row(controles, spacing=ESPACIADO_CABECERA, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ------------------------------------------------------------
    # Armado
    # ------------------------------------------------------------

    def _redibujar() -> None:
        try:
            resumen = dashboard_service.get_resumen_mes(
                ui["mes"], ui["anio"], usuario_local=leer_usuario_local(), moneda_codigo=ui["moneda"],
            )
        except ValueError as err:
            mostrar_mensaje(page, str(err).upper(), es_error=True)
            if ui["moneda"] == MONEDA_DEFAULT:
                return
            ui["moneda"] = MONEDA_DEFAULT  # una moneda que ya no existe: vuelve a la de siempre
            _redibujar()
            return
        datos["resumen"] = resumen
        _pintar_balance()
        # Una sola tarjeta: el BALANCE expandible ES el dashboard (ver docstring).
        contenido = ft.Container(width=ANCHO_CONTENIDO, content=contenedor_balance)
        raiz.controls = [pantalla_planilla([_cabecera(resumen["monedas_disponibles"]), contenido])]
        page.update()

    _redibujar()
    return raiz
