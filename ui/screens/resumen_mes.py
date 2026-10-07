"""
DeltaBalance — ui/screens/resumen_mes.py

Pantalla DASHBOARD: el resumen del mes en una moneda, con pills de moneda y
un selector de mes "‹ OCTUBRE 2026 ›" en la barra superior. Es la pantalla
que abre la app (ui/app.py). Se llama resumen_mes.py porque
ui/screens/dashboard.py es, por historia, la pantalla del Registro.

Layout (rediseño, pedido del usuario):
- DISPONIBLE arriba, siempre visible y en fuente grande; [HOY] [PROYECTADO]
  elige cuál. Verde si es positivo, rojo si es negativo. Ahí también el
  ajuste manual y ↺ de los escenarios.
- Debajo, en una tarjeta y separadas por ━━━:
  INGRESOS (ESTIMADO / REAL): FIJOS, VARIABLES, sus subtotales y TOTAL
  INGRESOS. Los variables no tienen estimado ("—").
  EGRESOS (ESTIMADO / REAL): FIJOS, VARIABLES con presupuesto y debajo
  ── SIN PRESUPUESTO ── (estimado = real), sus subtotales y TOTAL EGRESOS.
  TARJETA DE CRÉDITO (A PAGAR / PAGADO): cada tarjeta, TOTAL A PAGAR, los
  pagos realizados y FALTA PAGAR.
  DEUDAS (acumulado al último día del mes, no solo lo del mes): ME DEBEN y
  DEBO por persona con su subtotal, y NETO DEUDAS.
  Subsecciones separadas por ──; montos alineados a la derecha; "—" cuando
  el valor es 0 o no aplica; SUBTOTAL / TOTAL en negrita; en DEUDAS, verde
  lo positivo y rojo lo negativo.
- En gris, lo que no suma aparte: lo gastado en el Registro en categorías de
  fijos (ya está en los fijos), las categorías que se pasaron de su
  presupuesto (su REAL en rojo: el estimado del subtotal y el PROYECTADO
  usan lo gastado), qué resta la tarjeta y el aviso sin usuario local.

Escenarios (calculadora del DISPONIBLE, pedido del usuario; se mantuvo en
el rediseño): cada fila que suma al DISPONIBLE tiene su checkbox, y cada
subsección (FIJOS / VARIABLES de ingresos y de egresos, las tarjetas) uno
de tres estados: ☑ todas, ☐ ninguna, ▣ algunas; clickearlo desmarca todas
si estaban todas, si no las marca todas. NETO DEUDAS es un solo ítem, con
el checkbox en su fila. Lo desmarcado se ve en gris y no cuenta ni en los
subtotales ni en los DISPONIBLE: los dos salen de
DashboardService.calcular_escenario() sobre los grupos de cada modo
(get_resumen_mes()["disponible"]["grupos"]), y los subtotales, de lo que
aporta cada grupo (_subtotales()). Las filas sin checkbox — PAGOS
REALIZADOS, FALTA PAGAR y cada persona de DEUDAS — son información: no
cambian con lo marcado. "+ AJUSTE MANUAL" es un CampoMonto
(fórmulas con =; negativo resta; vacío no cambia nada) que suma a los dos
DISPONIBLE. Dice "(ESCENARIO)" si hay algo desmarcado o ajuste; ↺ vuelve
todo a marcado y ajuste 0. Lo desmarcado (ids de fila, los mismos en HOY y
PROYECTADO) y el ajuste se guardan en .deltabalance_prefs.json
(ui/utils/prefs.py, CLAUDE.md §12) bajo
"balance_escenario_{mes}_{anio}_{moneda}" — la misma clave y los mismos ids
que antes del rediseño, así lo ya guardado sigue valiendo. Se guarda lo
EXCLUIDO, así un ítem nuevo aparece marcado.

Todos los datos salen de UNA llamada: DashboardService.get_resumen_mes() —
las reglas de cada número (qué categorías no cuentan, la parte del usuario
en los compartidos, el proyectado de cada variable, qué resta la tarjeta,
las deudas acumuladas) viven ahí, ver su docstring y
docs/DATA_MODEL_DECISIONS.md sección 34. Esta pantalla solo dibuja; cambiar
de modo o tocar un checkbox redibuja sin volver a pedir datos.

Moneda: el resumen es de una sola moneda (nunca se suman pesos con
dólares). Por defecto ARS; si en el mes hay datos en otra
(monedas_disponibles), aparecen pills para cambiar.

Estado propio (mes, año, moneda, modo) en un almacén por página: se conserva
cuando ui/app.py reconstruye la pantalla porque otra cambió datos.

Reglas de arquitectura: solo DashboardService (CLAUDE.md §2/§3). El
usuario local (para los compartidos) sale de ui/components/usuario_local.py
(CLAUDE.md §12), como en Presupuestos y Compartidos.
"""

from datetime import date
from typing import Callable, Optional

import flet as ft

from services.dashboard_service import ID_ITEM_NETO_DEUDAS, MODOS_DISPONIBLE, DashboardService
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

ANCHO_CONTENIDO = 760          # las tarjetas no se estiran más que esto
ESPACIO_ENTRE_TARJETAS = 12
PADDING_TARJETA = 16
RADIO_TARJETA = 8
ANCHO_BORDE = 1
ESPACIADO_FILAS = 4
ESPACIADO_CABECERA = 12

ANCHO_CHECK = 40               # columna del checkbox (o su lugar vacío, para que todo quede alineado)
ANCHO_MONTO = 140              # cada una de las dos columnas de montos, alineadas a la derecha
SANGRIA_ITEM = 16              # filas de una subsección, más adentro que su separador ──
SANGRIA_INFO = SANGRIA_ITEM + ANCHO_CHECK + ESPACIADO_FILAS  # las líneas grises, alineadas con las etiquetas
ALTO_SEPARADOR = 1             # ── entre subsecciones y antes de un TOTAL
ALTO_SEPARADOR_SECCION = 2     # ━━━ entre secciones principales
COLOR_SEPARADOR = BORDER_DEFAULT
COLOR_SEPARADOR_SECCION = TEXT_MUTED
AIRE_SEPARADOR_SECCION = 8     # arriba y abajo de ━━━

TAMANIO_FILA = TypographyTokens.METADATA_SIZE
TAMANIO_INFO = TypographyTokens.LABEL_SIZE
TAMANIO_DISPONIBLE = 32        # DISPONIBLE, "grande y prominente" (pedido del usuario)

ALTO_CABECERA = 56
ANCHO_TEXTO_PERIODO = 170
RADIO_CONTROL = 8
PADDING_PILL_H = 12
ALTO_PILL = 28
RADIO_PILL = 14

SIN_VALOR = "—"                # un monto 0 o que no aplica
TEXTO_NADA = "NADA ESTE MES."
SEPARADOR_INFO = "   ·   "     # entre los ítems de una línea de información
TIPO_DEUDA_TEXTO = {"informal": "INFORMAL", "compartidos": "COMPARTIDOS"}

# Switch del DISPONIBLE (claves = DashboardService.MODOS_DISPONIBLE).
MODOS_DISPONIBLE_TEXTO = (("hoy", "HOY"), ("proyectado", "PROYECTADO"))
MODO_DISPONIBLE_DEFAULT = "hoy"
# Signo con el que cada grupo de get_resumen_mes()["disponible"]["grupos"] aporta
# al DISPONIBLE (los egresos y la tarjeta restan): para mostrar sus subtotales en positivo.
SIGNO_GRUPO = {
    "ingresos_fijos": 1, "ingresos_variables": 1, "egresos_fijos": -1, "egresos_variables": -1,
    "tarjeta": -1, "neto_deudas": 1,
}

# Calculadora de escenarios (ver docstring, "Escenarios").
PREFIJO_PREF_ESCENARIO = "balance_escenario"   # + _{mes}_{anio}_{moneda} en .deltabalance_prefs.json
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
        ui = {"mes": hoy.month, "anio": hoy.year, "moneda": MONEDA_DEFAULT, "modo": MODO_DISPONIBLE_DEFAULT}
        _ESTADOS_UI[id(page)] = ui
    return ui


# ============================================================
# PANTALLA
# ============================================================

def build(page: ft.Page, dashboard_service: DashboardService) -> ft.Control:
    ui = _estado_ui(page)
    raiz = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    # El último resumen pedido. El DISPONIBLE y las secciones se redibujan juntos: los subtotales
    # dependen de lo marcado.
    datos: dict = {"resumen": None}
    contenedor = ft.Container()
    refs: dict = {"campo_ajuste": None}  # el CampoMonto del ajuste manual (su fórmula, al confirmar)

    # ------------------------------------------------------------
    # Formato
    # ------------------------------------------------------------

    def _monto(r: dict, minor: int) -> str:
        """'$1,217,393.00' sin signo; el 0, "—"."""
        return amount_display(abs(minor), r["decimales"], r["moneda_simbolo"]) if minor else SIN_VALOR

    def _monto_con_signo(r: dict, minor: int) -> str:
        """'+$524,517.00' / '-$470,600.00'; el 0, "—"."""
        if not minor:
            return SIN_VALOR
        return f"{'+' if minor > 0 else '-'}{amount_display(abs(minor), r['decimales'], r['moneda_simbolo'])}"

    def _color_signo(minor: int) -> str:
        if minor > 0:
            return TEXT_POSITIVO
        return TEXT_NEGATIVO if minor < 0 else TEXT_SECONDARY

    def _texto(texto: str, color: str = TEXT_PRIMARY, weight=None, size: int = TAMANIO_FILA,
               expand: bool = False) -> ft.Text:
        return ft.Text(
            texto, color=color, weight=weight, size=size, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=texto or None, expand=expand,
        )

    def _tarjeta(controles: list[ft.Control]) -> ft.Control:
        return ft.Container(
            bgcolor=BG_SUPERFICIE,
            border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
            border_radius=RADIO_TARJETA,
            padding=PADDING_TARJETA,
            content=ft.Column(controles, spacing=ESPACIADO_FILAS),
        )

    def _pill(texto: str, activa: bool, on_click) -> ft.Control:
        """Pill de selección (monedas, HOY / PROYECTADO): la activa, rellena."""
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

    # ------------------------------------------------------------
    # Piezas de las secciones: filas, separadores, información
    # ------------------------------------------------------------

    def _lugar_check(check: Optional[ft.Control]) -> ft.Control:
        """La columna del checkbox: el checkbox, o su lugar vacío (así todo queda alineado)."""
        return ft.Container(width=ANCHO_CHECK, content=check)

    def _celda_monto(texto: str, color: str, negrita: bool) -> ft.Control:
        return ft.Container(
            width=ANCHO_MONTO, alignment=ft.Alignment.CENTER_RIGHT,
            content=_texto(texto, color, PESO_MONTO if negrita else None),
        )

    def _fila(etiqueta: str, valores: tuple[str, str], colores: tuple[str, str] = (TEXT_PRIMARY, TEXT_PRIMARY),
              check: Optional[ft.Control] = None, negrita: bool = False, apagada: bool = False,
              sangria: int = SANGRIA_ITEM) -> ft.Control:
        """[checkbox] etiqueta ··· valor 1 | valor 2. Apagada (desmarcada en el escenario): todo en gris."""
        color_etiqueta = TEXT_MUTED if apagada else TEXT_PRIMARY
        colores = (TEXT_MUTED, TEXT_MUTED) if apagada else colores
        return ft.Container(
            padding=ft.Padding.only(left=sangria),
            content=ft.Row(
                [
                    _lugar_check(check),
                    _texto(etiqueta, color_etiqueta, PESO_MONTO if negrita else None, expand=True),
                    _celda_monto(valores[0], colores[0], negrita),
                    _celda_monto(valores[1], colores[1], negrita),
                ],
                spacing=ESPACIADO_FILAS,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _cabecera_seccion(titulo: str, columnas: tuple[str, str], check: Optional[ft.Control] = None) -> ft.Control:
        def _columna(texto: str) -> ft.Control:
            return ft.Container(
                width=ANCHO_MONTO, alignment=ft.Alignment.CENTER_RIGHT,
                content=_texto(texto, TEXT_SECONDARY, TypographyTokens.TABLE_HEADER_WEIGHT,
                               TypographyTokens.TABLE_HEADER_SIZE),
            )

        return ft.Row(
            [
                _lugar_check(check),
                _texto(titulo, TEXT_PRIMARY, TypographyTokens.SECTION_TITLE_WEIGHT,
                       TypographyTokens.SECTION_TITLE_SIZE, expand=True),
                _columna(columnas[0]),
                _columna(columnas[1]),
            ],
            spacing=ESPACIADO_FILAS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _separador(titulo: str, check: Optional[ft.Control] = None, sangria: int = 0) -> ft.Control:
        """── TÍTULO ───────── entre subsecciones, con el checkbox de tres estados del grupo."""
        return ft.Container(
            padding=ft.Padding.only(left=sangria),
            content=ft.Row(
                [
                    _lugar_check(check),
                    _texto(titulo, TEXT_SECONDARY, PESO_HEADER, TAMANIO_INFO),
                    ft.Container(expand=True, height=ALTO_SEPARADOR, bgcolor=COLOR_SEPARADOR),
                ],
                spacing=ESPACIADO_FILAS,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _linea() -> ft.Control:
        """── antes de un TOTAL."""
        return ft.Container(height=ALTO_SEPARADOR, bgcolor=COLOR_SEPARADOR)

    def _linea_seccion() -> ft.Control:
        """━━━ entre secciones principales."""
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=AIRE_SEPARADOR_SECCION),
            content=ft.Container(height=ALTO_SEPARADOR_SECCION, bgcolor=COLOR_SEPARADOR_SECCION),
        )

    def _info(texto: str) -> ft.Control:
        """Línea de información: en gris y sin checkbox (no suma aparte al DISPONIBLE)."""
        return ft.Container(
            padding=ft.Padding.only(left=SANGRIA_INFO),
            content=_texto(texto, TEXT_SECONDARY, size=TAMANIO_INFO),
        )

    # ------------------------------------------------------------
    # Calculadora de escenarios (ver docstring, "Escenarios")
    # ------------------------------------------------------------

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

    def _alternar_grupo(ids: list[str]) -> None:
        """Click en un padre: si estaban todos marcados, desmarca todos; si no, marca todos."""
        escenario = _escenario()
        if all(id_ not in escenario["excluidos"] for id_ in ids):
            escenario["excluidos"].update(ids)
        else:
            escenario["excluidos"].difference_update(ids)
        _guardar_escenario(escenario)
        _repintar()

    def _alternar_item(id_: str) -> None:
        escenario = _escenario()
        escenario["excluidos"] ^= {id_}
        _guardar_escenario(escenario)
        _repintar()

    def _al_confirmar_ajuste(monto_minor: int) -> None:
        escenario = _escenario()
        escenario["ajuste_minor"] = monto_minor
        escenario["ajuste_formula"] = refs["campo_ajuste"].formula
        _guardar_escenario(escenario)
        _repintar()

    def _resetear_escenario() -> None:
        escribir_pref(_clave_escenario(), None)  # todo marcado y ajuste 0
        _repintar()

    def _check_item(id_: str, excluidos: set[str]) -> ft.Control:
        # El valor que trae el click se ignora: _alternar_item() decide.
        return ft.Checkbox(value=id_ not in excluidos, on_change=lambda e: _alternar_item(id_))

    def _check_grupo(items: list[dict], excluidos: set[str]) -> ft.Control:
        """Tres estados: ☑ todos, ☐ ninguno, ▣ algunos (sin ítems: deshabilitado)."""
        ids = [item["id"] for item in items]
        incluidos = [id_ for id_ in ids if id_ not in excluidos]
        estado = (True if len(incluidos) == len(ids) else (False if not incluidos else None)) if ids else False
        return ft.Checkbox(
            value=estado, tristate=True, disabled=not ids, on_change=lambda e: _alternar_grupo(ids),
        )

    def _subtotales(calculos: dict) -> dict[str, tuple[int, int]]:
        """
        (estimado, real) de lo MARCADO de cada grupo, en positivo: lo que aporta el grupo según
        calcular_escenario() en PROYECTADO y en HOY, por su signo (SIGNO_GRUPO). Los ingresos
        variables no tienen estimado (get_resumen_mes()): el suyo es 0, aunque el PROYECTADO sume
        lo cobrado.
        """
        proyectado, hoy = calculos["proyectado"]["grupos"], calculos["hoy"]["grupos"]
        subtotales = {clave: (signo * proyectado[clave], signo * hoy[clave]) for clave, signo in SIGNO_GRUPO.items()}
        subtotales["ingresos_variables"] = (0, subtotales["ingresos_variables"][1])
        return subtotales

    # ------------------------------------------------------------
    # DISPONIBLE
    # ------------------------------------------------------------

    def _fila_ajuste(r: dict, escenario: dict) -> ft.Control:
        # Fórmulas con "=" y persistir_formula=True (CLAUDE.md §9): muestra un valor guardado.
        campo = CampoMonto(
            page, on_confirmar=_al_confirmar_ajuste, decimales=r["decimales"], persistir_formula=True,
            valor_inicial_minor=escenario["ajuste_minor"] or None, formula_inicial=escenario["ajuste_formula"],
            width=ANCHO_AJUSTE, hint_text=HINT_AJUSTE,
        )
        refs["campo_ajuste"] = campo
        return ft.Row(
            [_texto("+ AJUSTE MANUAL (NEGATIVO RESTA)", TEXT_SECONDARY, expand=True), campo.control],
            spacing=ESPACIADO_FILAS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _tarjeta_disponible(r: dict, calculos: dict, escenario: dict) -> ft.Control:
        modo = ui["modo"]
        calculo = calculos[modo]
        disponible = calculo["disponible_minor"]
        signo = "+" if disponible > 0 else ("-" if disponible < 0 else "")
        cabecera = ft.Row(
            [
                _texto(
                    "DISPONIBLE" + (TEXTO_ESCENARIO if calculo["es_escenario"] else ""), TEXT_PRIMARY,
                    TypographyTokens.SECTION_TITLE_WEIGHT, TypographyTokens.SECTION_TITLE_SIZE, expand=True,
                ),
                ft.Row(
                    [_pill(texto, clave == modo, lambda c=clave: _cambiar_modo(c)) for clave, texto in MODOS_DISPONIBLE_TEXTO],
                    spacing=ESPACIADO_FILAS,
                ),
                ft.IconButton(
                    icon=ft.Icons.RESTART_ALT, icon_color=TEXT_SECONDARY, disabled=not calculo["es_escenario"],
                    tooltip="VOLVER A TODO MARCADO Y AJUSTE EN 0", on_click=lambda e: _resetear_escenario(),
                ),
                _texto(r["moneda_codigo"], TEXT_SECONDARY),
            ],
            spacing=ESPACIADO_FILAS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        monto = _texto(
            f"{signo}{amount_display(abs(disponible), r['decimales'], r['moneda_simbolo'])}",
            TEXT_POSITIVO if disponible >= 0 else TEXT_NEGATIVO, PESO_MONTO, TAMANIO_DISPONIBLE,
        )
        return _tarjeta([cabecera, monto, _fila_ajuste(r, escenario)])

    # ------------------------------------------------------------
    # Secciones
    # ------------------------------------------------------------

    def _filas_items(r: dict, items: list[dict], etiqueta: Callable[[dict], str], excluidos: set[str],
                     color_real: Callable[[dict], str] = lambda item: TEXT_PRIMARY) -> list[ft.Control]:
        """Una fila con checkbox por ítem (ESTIMADO | REAL)."""
        return [
            _fila(
                etiqueta(item), (_monto(r, item["estimado_minor"]), _monto(r, item["real_minor"])),
                colores=(TEXT_PRIMARY, color_real(item)), check=_check_item(item["id"], excluidos),
                apagada=item["id"] in excluidos,
            )
            for item in items
        ]

    def _fila_subtotal(r: dict, etiqueta: str, valores: tuple[int, int], sangria: int = SANGRIA_ITEM) -> ft.Control:
        return _fila(etiqueta, (_monto(r, valores[0]), _monto(r, valores[1])), negrita=True, sangria=sangria)

    def _seccion_ingresos(r: dict, subtotales: dict, excluidos: set[str]) -> list[ft.Control]:
        ingresos = r["ingresos"]
        fijos, variables = subtotales["ingresos_fijos"], subtotales["ingresos_variables"]
        return [
            _cabecera_seccion("INGRESOS", ("ESTIMADO", "REAL")),
            _separador("FIJOS", _check_grupo(ingresos["fijos"], excluidos)),
            *(_filas_items(r, ingresos["fijos"], lambda i: i["concepto"], excluidos) or [_info(TEXTO_NADA)]),
            _fila_subtotal(r, "SUBTOTAL FIJOS", fijos),
            _separador("VARIABLES", _check_grupo(ingresos["variables"], excluidos)),
            *(_filas_items(r, ingresos["variables"], lambda i: i["concepto"], excluidos) or [_info(TEXTO_NADA)]),
            _fila_subtotal(r, "SUBTOTAL VARIABLES", variables),
            _linea(),
            _fila_subtotal(r, "TOTAL INGRESOS", (fijos[0] + variables[0], fijos[1] + variables[1]), sangria=0),
        ]

    def _seccion_egresos(r: dict, subtotales: dict, excluidos: set[str]) -> list[ft.Control]:
        egresos = r["egresos"]
        fijos, variables = subtotales["egresos_fijos"], subtotales["egresos_variables"]
        con_presupuesto = egresos["variables_con_presupuesto"]
        sin_presupuesto = egresos["variables_sin_presupuesto"]
        pasadas = [v for v in con_presupuesto if v["real_minor"] > v["estimado_minor"]]

        filas = [
            _cabecera_seccion("EGRESOS", ("ESTIMADO", "REAL")),
            _separador("FIJOS", _check_grupo(egresos["fijos"], excluidos)),
            *(_filas_items(r, egresos["fijos"], lambda f: f["concepto"], excluidos) or [_info(TEXTO_NADA)]),
            _fila_subtotal(r, "SUBTOTAL FIJOS", fijos),
        ]
        if egresos["en_categorias_de_fijos"]:
            filas.append(_info("EN EL REGISTRO, EN CATEGORÍAS DE FIJOS (YA ESTÁ EN LOS FIJOS): " + SEPARADOR_INFO.join(
                f"{g['categoria']} {_monto(r, g['real_minor'])}" for g in egresos["en_categorias_de_fijos"]
            )))
        filas += [
            _separador("VARIABLES", _check_grupo(con_presupuesto + sin_presupuesto, excluidos)),
            # Pasada de su presupuesto: el REAL en rojo.
            *_filas_items(
                r, con_presupuesto, lambda v: v["categoria"], excluidos,
                color_real=lambda v: TEXT_NEGATIVO if v["real_minor"] > v["estimado_minor"] else TEXT_PRIMARY,
            ),
        ]
        if sin_presupuesto:
            filas += [
                _separador("SIN PRESUPUESTO", sangria=SANGRIA_ITEM),
                *_filas_items(r, sin_presupuesto, lambda v: v["categoria"], excluidos),
            ]
        if not con_presupuesto and not sin_presupuesto:
            filas.append(_info(TEXTO_NADA))
        filas.append(_fila_subtotal(r, "SUBTOTAL VARIABLES", variables))
        if pasadas:
            filas.append(_info(
                "SE PASARON DEL PRESUPUESTO: " + ", ".join(v["categoria"] for v in pasadas)
                + " — EL ESTIMADO DEL SUBTOTAL Y EL PROYECTADO USAN LO GASTADO."
            ))
        filas += [
            _linea(),
            _fila_subtotal(r, "TOTAL EGRESOS", (fijos[0] + variables[0], fijos[1] + variables[1]), sangria=0),
        ]
        return filas

    def _seccion_tarjeta(r: dict, subtotales: dict, excluidos: set[str]) -> list[ft.Control]:
        tarjeta = r["tarjeta"]
        filas = [_cabecera_seccion("TARJETA DE CRÉDITO", ("A PAGAR", "PAGADO"), _check_grupo(tarjeta["por_cuenta"], excluidos))]
        filas += [
            _fila(
                t["cuenta"], (_monto(r, t["a_pagar_minor"]), SIN_VALOR), check=_check_item(t["id"], excluidos),
                apagada=t["id"] in excluidos,
            )
            for t in tarjeta["por_cuenta"]
        ] or [_info(TEXTO_NADA)]
        filas += [
            _fila_subtotal(r, "TOTAL A PAGAR", (subtotales["tarjeta"][1], 0)),
            _fila("PAGOS REALIZADOS", (SIN_VALOR, _monto(r, tarjeta["pagos_realizados_minor"]))),
            _linea(),
            _fila_subtotal(r, "FALTA PAGAR", (0, tarjeta["falta_pagar_minor"]), sangria=0),
            _info("EL DISPONIBLE RESTA EL TOTAL A PAGAR (LO PAGADO Y LO QUE FALTA): LAS COMPRAS CON TARJETA NO ESTÁN EN LOS EGRESOS."),
        ]
        return filas

    def _seccion_deudas(r: dict, excluidos: set[str]) -> list[ft.Control]:
        deudas = r["deudas"]
        anio, mes, dia = r["fecha_corte"].split("-")

        def _personas(lista: list[dict], signo: int) -> list[ft.Control]:
            return [
                _fila(
                    f"{d['persona']} ({TIPO_DEUDA_TEXTO.get(d['tipo'], d['tipo'].upper())})",
                    ("", _monto_con_signo(r, signo * d["monto_minor"])), colores=(TEXT_PRIMARY, _color_signo(signo)),
                )
                for d in lista
            ] or [_info("NADA.")]

        def _subtotal(minor: int) -> ft.Control:
            return _fila("SUBTOTAL", ("", _monto_con_signo(r, minor)), colores=(TEXT_PRIMARY, _color_signo(minor)), negrita=True)

        neto = deudas["neto_minor"]
        filas = [
            _cabecera_seccion(f"DEUDAS (ACUMULADO AL {dia}/{mes}/{anio})", ("", "TOTAL")),
            _separador("ME DEBEN"),
            *_personas(deudas["me_deben"], 1),
            _subtotal(deudas["subtotal_me_deben_minor"]),
            _separador("DEBO"),
            *_personas(deudas["debo"], -1),
            _subtotal(-deudas["subtotal_debo_minor"]),
            _linea(),
            # Un solo ítem: el checkbox va en su fila.
            _fila(
                "NETO DEUDAS", ("", _monto_con_signo(r, neto)), colores=(TEXT_PRIMARY, _color_signo(neto)),
                check=_check_item(ID_ITEM_NETO_DEUDAS, excluidos), negrita=True,
                apagada=ID_ITEM_NETO_DEUDAS in excluidos, sangria=0,
            ),
        ]
        if r["sin_usuario_local"]:
            filas.append(_info("SIN USUARIO LOCAL: SOLO LAS DEUDAS INFORMALES (FALTAN LAS DE COMPARTIDOS)."))
        return filas

    # ------------------------------------------------------------
    # Dibujo
    # ------------------------------------------------------------

    def _pintar() -> None:
        r = datos["resumen"]
        escenario = _escenario()
        excluidos = escenario["excluidos"]
        calculos = {
            modo: dashboard_service.calcular_escenario(
                r["disponible"]["grupos"][modo], excluidos, escenario["ajuste_minor"],
            )
            for modo in MODOS_DISPONIBLE
        }
        subtotales = _subtotales(calculos)
        secciones = [
            *_seccion_ingresos(r, subtotales, excluidos),
            _linea_seccion(),
            *_seccion_egresos(r, subtotales, excluidos),
            _linea_seccion(),
            *_seccion_tarjeta(r, subtotales, excluidos),
            _linea_seccion(),
            *_seccion_deudas(r, excluidos),
        ]
        contenedor.content = ft.Column(
            [_tarjeta_disponible(r, calculos, escenario), _tarjeta(secciones)],
            spacing=ESPACIO_ENTRE_TARJETAS,
        )

    def _repintar() -> None:
        _pintar()
        page.update(contenedor)

    def _cambiar_modo(modo: str) -> None:
        if modo == ui["modo"]:
            return
        ui["modo"] = modo
        _repintar()

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
                ui["mes"], ui["anio"], moneda=ui["moneda"], usuario_local=leer_usuario_local(),
            )
        except ValueError as err:
            mostrar_mensaje(page, str(err).upper(), es_error=True)
            if ui["moneda"] == MONEDA_DEFAULT:
                return
            ui["moneda"] = MONEDA_DEFAULT  # una moneda que ya no existe: vuelve a la de siempre
            _redibujar()
            return
        datos["resumen"] = resumen
        _pintar()
        contenido = ft.Container(width=ANCHO_CONTENIDO, content=contenedor)
        raiz.controls = [pantalla_planilla([_cabecera(resumen["monedas_disponibles"]), contenido])]
        page.update()

    _redibujar()
    return raiz
