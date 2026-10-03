"""
DeltaBalance — ui/screens/presupuestos.py

Presupuestos de cada mes (services/presupuestos_service.py) en dos
secciones, con el mismo formato visual que el Registro: barra de título (←
volver, búsqueda, mes, pills de moneda y COPIAR RECURRENTES) → sección
FIJOS → sección VARIABLES. Cada sección es su propia tabla (TablaPlanilla,
ui/components/tabla_planilla.py) con fila de alta, edición inline, barra
flotante y una fila TOTAL al final.

Reglas de arquitectura: solo PresupuestosService/CategoriasService/
AccountsService — nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).
dashboard_service lo sigue pasando ui/app.py, pero ya no se usa: el real de
los variables lo calcula PresupuestosService.

--- Estado propio ---

Período, moneda elegida y el BORRADOR de cada fila de alta viven en un
almacén por página (_ESTADOS_UI), igual que Deudas. Cada sección guarda
sus propios búsqueda, filtros, orden, selección y anchos de columna
(PREF_ANCHOS_COLUMNAS, una clave por sección). La búsqueda de la barra de
título filtra las dos (_BusquedaEnSecciones).

--- FIJOS ---

Concepto libre (ALQUILER, SEGURO…), Estimado y Real cargados a mano —
los tres editables inline (CLAUDE.md §10; montos con CampoMonto y fórmulas,
§9). Real en rojo si supera al estimado, "—" si todavía es 0.

--- VARIABLES ---

Categoría (CampoFiltrable con las categorías de egreso, más COMPARTIDOS),
Estimado editable y Real* CALCULADO (no editable): los egresos del Registro
de esa categoría en el mes — de una transacción compartida, solo la parte
del usuario —, o para COMPARTIDOS lo que el usuario debe por los gastos
compartidos que pagó el otro miembro del hogar. Para eso hace falta saber
quién es el usuario: usuario_local de las preferencias (lo escribe el
login, ui/components/usuario_local.py); sin él, el real de COMPARTIDOS se
muestra "—". COMPARTIDOS va en color de acento: no es una categoría del
Registro. Un mes no puede tener dos variables de la misma categoría (el
service lo rechaza con un mensaje).

--- Moneda ---

Igual que Ingresos: las pills eligen la moneda que se ve (las dos
secciones y sus TOTAL, que nunca mezclan monedas); son ARS y USD
(MONEDAS_TOGGLE) más cualquier otra moneda con presupuestos en el mes. Al
elegir una, las filas de alta pasan a esa moneda; un alta en otra moneda
cambia la pill sola. La columna Moneda es un botón ARS ↔ USD
(SelectorCiclico): en una fila ya cargada la cambia y guarda conservando
el importe mostrado (reescala si cambian los decimales).

--- Filas de alta ---

FIJOS: Concepto → Estimado → Real → ✓ (vacío = 0; hace falta al menos
uno). VARIABLES: Categoría → Estimado → ✓ (el real es automático). Moneda
y Recurrente en las dos, alcanzables con Tab. Enter nunca guarda la fila
salvo con el foco en el ✓. Están pegadas al encabezado de su sección, como
en toda tabla de la app (no al final, como "[+ Agregar …]" del boceto).

--- Barra flotante ---

Solo Eliminar (PresupuestosService.delete(): sin dependencias con estado
propio, CLAUDE.md §4). Cada sección tiene la suya, en el mismo lugar: con
filas seleccionadas en las dos a la vez, se superponen.

--- TOTAL ---

Al final de cada sección, TOTAL FIJOS / TOTAL VARIABLES (FilaPie, mismo
estilo que SALDO ANTERIOR) con el estimado y el real del mes en la moneda
elegida (PresupuestosService.totales_de()): fondo verde si el real no pasa
el estimado, rojo si lo pasa. Es el del mes entero, aunque haya una
búsqueda o un filtro activo.

--- COPIAR RECURRENTES ---

Copia los presupuestos recurrentes (fijos y variables) del mes anterior al
mes que se está viendo (PresupuestosService.copy_recurrentes(): real en 0,
sin duplicar) y avisa cuántos copió. En la barra es solo el ícono de
copiar, sin texto (con texto no entraba en el ancho disponible); el nombre
va en el tooltip.
"""

from datetime import date
from typing import Any, Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.dashboard_service import DashboardService
from services.presupuestos_service import (
    CATEGORIA_COMPARTIDOS,
    CONCEPTO_COMPARTIDOS,
    PresupuestoError,
    PresupuestosService,
)
from ui.components.campo_monto import CampoMonto
from ui.components.tabla_planilla import (
    ANCHO_BORDE,
    ESPACIO_DOT,
    PILL_ALTURA,
    PILL_PADDING_H,
    PILL_RADIO,
    Columna,
    FilaAlta,
    FilaPie,
    SelectorCiclico,
    TablaPlanilla,
    barra_titulo,
    estilo_campo,
    mostrar_mensaje,
    pantalla_planilla,
    sin_auto_update,
    sin_borde,
    texto_celda,
)
from ui.components.tipo_valor import numero
from ui.components.usuario_local import leer_usuario_local
from ui.theme.tabla_tokens import (
    BORDER_DEFAULT,
    BTN_COMPARTIR,
    PESO_HEADER,
    PESO_MONTO,
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_NEGATIVO,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_SOBRE_BOTON,
)
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---

SECCIONES = ("fijo", "variable")
COLUMNAS = {
    "fijo": [
        Columna("concepto", "CONCEPTO", 200),
        Columna("estimado", "ESTIMADO", 130, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
        Columna("real", "REAL", 130, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
        Columna("moneda", "MONEDA", 70, redimensionable=False),
        Columna("recurrente", "RECURRENTE", 90, redimensionable=False),
    ],
    "variable": [
        Columna("categoria", "CATEGORÍA", 200),
        Columna("estimado", "ESTIMADO", 130, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
        Columna("real", "REAL*", 130, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
        Columna("moneda", "MONEDA", 70, redimensionable=False),
        Columna("recurrente", "RECURRENTE", 90, redimensionable=False),
    ],
}
# Una tabla por sección (ver docstring, "Estado propio").
CLAVE_TABLA = {"fijo": "presupuestos_fijos", "variable": "presupuestos_variables"}
PREF_ANCHOS_COLUMNAS = {"fijo": "presupuestos_fijos_anchos", "variable": "presupuestos_variables_anchos"}
# Clave de cada sección en PresupuestosService.list_by_month() / totales_de().
CLAVE_LISTADO = {"fijo": "fijos", "variable": "variables"}
PRIMERA_COLUMNA = {"fijo": "concepto", "variable": "categoria"}

MONEDA_DEFAULT = "ARS"
# Opciones del botón de Moneda y pills que se muestran siempre (ver docstring, "Moneda").
MONEDAS_TOGGLE = ("ARS", "USD")
DECIMALES_DEFAULT = 2

TITULO_SECCION = {"fijo": "FIJOS", "variable": "VARIABLES"}
TEXTO_TOTAL = {"fijo": "TOTAL FIJOS", "variable": "TOTAL VARIABLES"}
TOOLTIP_TOTAL = {
    "fijo": "TOTAL DE FIJOS DEL MES EN {moneda}",
    "variable": "TOTAL DE VARIABLES DEL MES EN {moneda} — EL REAL SALE DEL REGISTRO",
}
TEXTO_VACIO = {
    "fijo": "NO HAY FIJOS EN ESTA MONEDA PARA ESTE MES.",
    "variable": "NO HAY VARIABLES EN ESTA MONEDA PARA ESTE MES.",
}
NOTA_REAL_VARIABLES = "* REAL CALCULADO AUTOMÁTICAMENTE DEL REGISTRO"
TEXTO_SIN_REAL = "—"
TEXTO_REAL_AUTOMATICO = "AUTOMÁTICO"
TEXTO_RECURRENTE = {True: "SÍ", False: "NO"}
TOOLTIP_RECURRENTE = "SE COPIA AL MES SIGUIENTE CON COPIAR RECURRENTES"
TOOLTIP_COMPARTIDOS = "ÍTEM ESPECIAL: LO QUE DEBÉS POR LOS GASTOS COMPARTIDOS QUE PAGÓ EL OTRO MIEMBRO DEL HOGAR"
TOOLTIP_REAL_CATEGORIA = "EGRESOS DEL REGISTRO EN ESTA CATEGORÍA (DE UN GASTO COMPARTIDO, SOLO TU PARTE)"
TOOLTIP_REAL_COMPARTIDOS = "LO QUE DEBÉS POR LOS GASTOS COMPARTIDOS DEL MES QUE PAGÓ EL OTRO MIEMBRO"
TOOLTIP_SIN_USUARIO = "FALTA EL USUARIO LOCAL (INICIÁ SESIÓN): SIN ÉL NO SE SABE QUÉ GASTOS PAGÓ CADA UNO"
HINT_CONCEPTO_ALTA = "EJ: ALQUILER"
HINT_CATEGORIA_ALTA = "CATEGORÍA"
HINT_ESTIMADO_ALTA = "ESTIMADO"
HINT_REAL_ALTA = "REAL"

# Pills de moneda y botón COPIAR RECURRENTES: mismas medidas que las pills de barra_resumen().
TAMANIO_PILLS = TypographyTokens.REGISTRO_FONT_SALDO_BAR
ESPACIO_PILLS = ESPACIO_DOT
ICONO_PILL = 16

# Título de sección "─── FIJOS ─────": tramo de línea antes del texto.
ANCHO_LINEA_TITULO_SECCION = 24
ESPACIO_TITULO_SECCION = 8


# ============================================================
# ESTADO PROPIO POR PÁGINA (ver docstring del módulo)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia(seccion: str, moneda: Optional[str] = None) -> dict:
    if seccion == "fijo":
        return {"concepto": "", "estimado": "", "real": "", "moneda": moneda, "recurrente": False}
    return {"categoria": None, "estimado": "", "moneda": moneda, "recurrente": False}


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {
            "mes": hoy.month, "anio": hoy.year, "moneda": MONEDA_DEFAULT,
            "altas": {seccion: _alta_vacia(seccion) for seccion in SECCIONES},
        }
        _ESTADOS_UI[id(page)] = ui
    return ui


# ============================================================
# HELPERS
# ============================================================

def _reescalar(monto_minor: int, decimales_origen: int, decimales_destino: int) -> int:
    """Mismo importe mostrado en una moneda con otros decimales (mismo criterio que Deudas)."""
    diferencia = decimales_destino - decimales_origen
    return monto_minor * 10 ** diferencia if diferencia >= 0 else round(monto_minor / 10 ** -diferencia)


def _mes_anterior(mes: int, anio: int) -> tuple[int, int]:
    return (12, anio - 1) if mes == 1 else (mes - 1, anio)


def _orden_monedas(codigos: set[str]) -> list[str]:
    """ARS primero, después alfabético (mismo orden que las pills de barra_resumen())."""
    return sorted(codigos, key=lambda codigo: (codigo != MONEDA_DEFAULT, codigo))


def _valor_alta(campo: CampoMonto) -> Optional[float]:
    """Monto tipeado en el alta: vacío = 0; None si no es un número (o una fórmula) válido."""
    if not campo.confirmar():  # resuelve una fórmula pendiente (y marca el borde si no es válida)
        return None
    if not campo.texto.strip():
        return 0.0
    return numero(campo.texto)


def _pill(
    texto: Optional[str], activa: bool, on_click: Callable[[], None],
    tooltip: Optional[str] = None, icono: Optional[str] = None,
) -> ft.Control:
    """
    Pill de la barra de título (estilo selector de moneda de barra_resumen()):
    la activa, rellena. Sin texto, solo el ícono (el nombre va en el tooltip).
    """
    color = TEXT_SOBRE_BOTON if activa else TEXT_SECONDARY
    contenido: list[ft.Control] = []
    if icono is not None:
        contenido.append(ft.Icon(icono, size=ICONO_PILL, color=color))
    if texto:
        contenido.append(ft.Text(texto, size=TAMANIO_PILLS, weight=PESO_HEADER, color=color))
    return ft.Container(
        height=PILL_ALTURA,
        padding=ft.Padding.symmetric(horizontal=PILL_PADDING_H),
        border_radius=PILL_RADIO,
        bgcolor=BTN_COMPARTIR if activa else None,
        border=None if activa else ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
        alignment=ft.Alignment.CENTER,
        tooltip=tooltip,
        on_click=lambda e: on_click(),
        content=ft.Row(contenido, spacing=ESPACIO_PILLS, tight=True),
    )


def _con_volver(titulo: ft.Control, on_volver: Optional[Callable[[], None]]) -> ft.Control:
    """← a la izquierda de la barra de título (si la pantalla tiene adónde volver)."""
    if on_volver is None:
        return titulo
    titulo.expand = True
    return ft.Row(
        [
            ft.IconButton(
                icon=ft.Icons.ARROW_BACK, icon_color=TEXT_SECONDARY, tooltip="VOLVER", on_click=lambda e: on_volver(),
            ),
            titulo,
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _titulo_seccion(texto: str) -> ft.Control:
    """'─── FIJOS ─────────'."""
    return ft.Row(
        [
            ft.Container(width=ANCHO_LINEA_TITULO_SECCION, height=ANCHO_BORDE, bgcolor=BORDER_DEFAULT),
            ft.Text(
                texto, size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                color=TEXT_SECONDARY,
            ),
            ft.Container(expand=True, height=ANCHO_BORDE, bgcolor=BORDER_DEFAULT),
        ],
        spacing=ESPACIO_TITULO_SECCION,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


class _BusquedaEnSecciones:
    """
    barra_titulo() busca en UNA tabla (solo usa tabla.busqueda y
    tabla.buscar()): esto reparte el mismo texto entre las dos secciones.
    """

    def __init__(self, tablas: list[TablaPlanilla]):
        self._tablas = tablas

    @property
    def busqueda(self) -> str:
        return self._tablas[0].busqueda

    def buscar(self, texto: str) -> None:
        for tabla in self._tablas:
            tabla.buscar(texto)


# ============================================================
# PANTALLA
# ============================================================

def build(
    page: ft.Page,
    categorias_service: CategoriasService,
    presupuestos_service: PresupuestosService,
    dashboard_service: DashboardService,
    accounts_service: AccountsService,
    on_volver: Optional[Callable[[], None]] = None,
) -> ft.Control:
    # dashboard_service: ver docstring del módulo (ya no se usa).
    ui = _estado_ui(page)

    def _mostrar_error(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje, es_error=True)

    monedas_por_codigo = {m["codigo"]: dict(m) for m in accounts_service.list_currencies()}
    monedas_toggle = [codigo for codigo in MONEDAS_TOGGLE if codigo in monedas_por_codigo] or [MONEDA_DEFAULT]

    def _opciones_moneda(actual: Optional[str]) -> list[tuple[str, str]]:
        codigos = list(monedas_toggle)
        if actual and actual not in codigos:
            codigos.append(actual)
        return [(codigo, codigo) for codigo in codigos]

    def _decimales(codigo: Optional[str]) -> int:
        return monedas_por_codigo.get(codigo or "", {}).get("decimales", DECIMALES_DEFAULT)

    # COMPARTIDOS primero: es el ítem especial, no una categoría del Registro.
    opciones_categoria = [(CATEGORIA_COMPARTIDOS, CONCEPTO_COMPARTIDOS)] + [
        (c["id"], (c["subcategoria"] or "").upper()) for c in categorias_service.list_categories(tipo="egreso")
    ]

    tablas: dict[str, TablaPlanilla] = {}

    # ------------------------------------------------------------
    # DATOS
    # ------------------------------------------------------------

    # Lo que deja la última carga de cada sección: totales por moneda y en qué monedas hay filas este mes.
    datos: dict[str, dict] = {
        "totales": {seccion: {} for seccion in SECCIONES},
        "monedas_mes": {seccion: set() for seccion in SECCIONES},
    }

    def _cargar_filas(seccion: str) -> list[dict]:
        listado = presupuestos_service.list_by_month(ui["mes"], ui["anio"], leer_usuario_local())
        datos["totales"][seccion] = presupuestos_service.totales_de(listado)
        filas = listado[CLAVE_LISTADO[seccion]]
        datos["monedas_mes"][seccion] = {p["currency_code"] for p in filas}
        return [p for p in filas if p["currency_code"] == ui["moneda"]]

    def _texto_monto(monto_minor: int, p: dict) -> str:
        return amount_display(monto_minor, p["decimales"], p["currency_symbol"] or "")

    def _texto_real(p: dict) -> str:
        return _texto_monto(p["real_minor"], p) if p["real_minor"] else TEXTO_SIN_REAL

    def _color_real(p: dict) -> str:
        if not p["real_minor"]:
            return TEXT_MUTED
        return TEXT_NEGATIVO if p["real_minor"] > p["monto_estimado_minor"] else TEXT_PRIMARY

    def _valor_columna(p: dict, columna: str) -> str:
        """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
        if columna in ("concepto", "categoria"):
            return p["nombre"]
        if columna == "estimado":
            return _texto_monto(p["monto_estimado_minor"], p)
        if columna == "real":
            return _texto_real(p)
        if columna == "moneda":
            return p["currency_code"] or ""
        return TEXTO_RECURRENTE[bool(p["es_recurrente"])]

    def _clave_orden(p: dict, columna: str) -> Any:
        if columna == "estimado":
            return p["monto_estimado_minor"] / (10 ** p["decimales"])
        if columna == "real":
            return (p["real_minor"] or 0) / (10 ** p["decimales"])
        return _valor_columna(p, columna).lower()

    def _firma(p: dict) -> tuple:
        return (
            p["nombre"], p["categoria_id"], p["concepto"], p["monto_estimado_minor"], p["real_minor"], p["moneda_id"],
            p["currency_code"], p["currency_symbol"], p["decimales"], p["es_recurrente"],
        )

    def _recargar_todas(limpiar_seleccion: bool = False) -> None:
        for tabla in tablas.values():
            tabla.recargar(limpiar_seleccion=limpiar_seleccion)

    # ------------------------------------------------------------
    # PILLS DE MONEDA Y COPIAR RECURRENTES (barra de título)
    # ------------------------------------------------------------

    contenedor_pills = ft.Container()

    def _dibujar_pills() -> None:
        con_filas = set().union(*datos["monedas_mes"].values())
        monedas = _orden_monedas(set(monedas_toggle) | con_filas | {ui["moneda"]})
        contenedor_pills.content = ft.Row(
            [
                _pill(codigo, codigo == ui["moneda"], lambda c=codigo: _elegir_moneda(c), tooltip=f"VER LOS PRESUPUESTOS EN {codigo}")
                for codigo in monedas
            ],
            spacing=ESPACIO_PILLS,
        )

    def _elegir_moneda(codigo: str) -> None:
        if codigo == ui["moneda"]:
            sin_auto_update()
            return
        for seccion in SECCIONES:
            _guardar_borrador(seccion)
            ui["altas"][seccion]["moneda"] = codigo
        ui["moneda"] = codigo
        # Filas de alta en la moneda nueva + filas recargadas (al_recargar
        # re-dibuja las pills). FIJOS al final: se queda con el foco.
        tablas["variable"].alta_ok()
        tablas["fijo"].alta_ok()

    def _al_recargar() -> list[ft.Control]:
        _dibujar_pills()
        return [contenedor_pills]

    def _copiar_recurrentes() -> None:
        mes, anio = _mes_anterior(ui["mes"], ui["anio"])
        try:
            copiados = presupuestos_service.copy_recurrentes(mes, anio, ui["mes"], ui["anio"])
        except PresupuestoError as err:
            _mostrar_error(str(err))
            return
        _recargar_todas()
        if copiados == 0:
            mensaje = f"NO HABÍA PRESUPUESTOS RECURRENTES NUEVOS PARA COPIAR DE {mes:02d}/{anio}."
        elif copiados == 1:
            mensaje = "SE COPIÓ 1 PRESUPUESTO RECURRENTE."
        else:
            mensaje = f"SE COPIARON {copiados} PRESUPUESTOS RECURRENTES."
        mostrar_mensaje(page, mensaje)

    # ------------------------------------------------------------
    # FILAS TOTAL (FilaPie)
    # ------------------------------------------------------------

    def _filas_pie(seccion: str) -> list[FilaPie]:
        moneda = ui["moneda"]
        total = datos["totales"][seccion].get(moneda)
        if total is None or moneda not in datos["monedas_mes"][seccion]:
            return []
        clave = CLAVE_LISTADO[seccion]
        estimado, real = total[f"{clave}_estimado"], total[f"{clave}_real"]
        simbolo = total["currency_symbol"] or ""
        return [FilaPie(
            textos={
                PRIMERA_COLUMNA[seccion]: TEXTO_TOTAL[seccion],
                "estimado": amount_display(estimado, total["decimales"], simbolo),
                "real": amount_display(real, total["decimales"], simbolo) if real else TEXTO_SIN_REAL,
                "moneda": moneda,
            },
            positiva=real <= estimado,
            valores_filtro={"moneda": moneda},
            tooltip=TOOLTIP_TOTAL[seccion].format(moneda=moneda),
        )]

    # ------------------------------------------------------------
    # FILAS DE ALTA
    # ------------------------------------------------------------

    alta_refs: dict[str, dict[str, Any]] = {seccion: {} for seccion in SECCIONES}

    def _guardar_borrador(seccion: str) -> None:
        refs = alta_refs[seccion]
        if not refs:
            return
        borrador = {
            "estimado": refs["estimado"].texto,
            "moneda": refs["moneda"].valor,
            "recurrente": bool(refs["recurrente"].value),
        }
        if seccion == "fijo":
            borrador.update(concepto=refs["concepto"].value or "", real=refs["real"].texto)
        else:
            borrador.update(categoria=refs["categoria"].id_seleccionado)
        ui["altas"][seccion] = borrador

    def _on_cambio_borrador(seccion: str) -> None:
        # Solo guarda el borrador: el cambio ya está en pantalla.
        _guardar_borrador(seccion)
        sin_auto_update()

    def _campo_monto_alta(
        seccion: str, valor: str, decimales: int, hint: str, on_avanzar: Callable[[], None],
    ) -> CampoMonto:
        # persistir_formula=True: el campo recuerda la fórmula mientras la
        # fila no se guarde. on_confirmar solo guarda el borrador — la fila
        # entera confirma junta (✓).
        campo = CampoMonto(
            tablas[seccion].pagina_alta,
            on_confirmar=lambda monto_minor: _guardar_borrador(seccion),
            persistir_formula=True,
            decimales=decimales,
            hint_text=hint,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            on_avanzar=on_avanzar,
        )
        sin_borde(campo.control)
        campo.control.text_align = ft.TextAlign.RIGHT
        if valor:
            campo.control.value = valor
        return campo

    def _moneda_y_recurrente_alta(seccion: str, borrador: dict, moneda_inicial: str) -> tuple[Any, ft.Checkbox]:
        """Celdas comunes a las dos filas de alta: Moneda (SelectorCiclico) y Recurrente."""
        selector_moneda = tablas[seccion].selector_alta(
            _opciones_moneda(moneda_inicial), moneda_inicial, lambda codigo: _guardar_borrador(seccion), "MONEDA",
        )
        checkbox_recurrente = ft.Checkbox(
            value=borrador["recurrente"], active_color=TEXT_ACCENT, tooltip=TOOLTIP_RECURRENTE,
            on_change=lambda e: _on_cambio_borrador(seccion),
        )
        return selector_moneda, checkbox_recurrente

    def _construir_alta_fijo() -> FilaAlta:
        tabla = tablas["fijo"]
        borrador = ui["altas"]["fijo"]
        moneda_inicial = borrador["moneda"] or ui["moneda"]

        campo_concepto = ft.TextField(
            value=borrador["concepto"], hint_text=HINT_CONCEPTO_ALTA, autofocus=True,
            text_align=ft.TextAlign.CENTER, on_change=lambda e: _on_cambio_borrador("fijo"), **estilo_campo(),
        )
        # Real es el último campo de la cadena: Enter lleva el foco al ✓ sin guardar.
        campo_real = _campo_monto_alta(
            "fijo", borrador["real"], _decimales(moneda_inicial), HINT_REAL_ALTA, tabla.enfocar_confirmar,
        )
        campo_estimado = _campo_monto_alta(
            "fijo", borrador["estimado"], _decimales(moneda_inicial), HINT_ESTIMADO_ALTA,
            lambda: tabla.enfocar(campo_real.control),
        )
        selector_moneda, checkbox_recurrente = _moneda_y_recurrente_alta("fijo", borrador, moneda_inicial)
        boton_confirmar = tabla.boton_confirmar_alta("AGREGAR FIJO", lambda: _confirmar_alta("fijo"))
        alta_refs["fijo"].update(
            concepto=campo_concepto, estimado=campo_estimado, real=campo_real, moneda=selector_moneda,
            recurrente=checkbox_recurrente, boton=boton_confirmar,
        )
        campo_concepto.on_submit = lambda e: tabla.enfocar(campo_estimado.control)

        return FilaAlta(
            celdas={
                "concepto": campo_concepto,
                "estimado": campo_estimado.control,
                "real": campo_real.control,
                "moneda": selector_moneda.control,
                "recurrente": checkbox_recurrente,
            },
            boton=boton_confirmar,
            foco=campo_concepto,
        )

    def _construir_alta_variable() -> FilaAlta:
        tabla = tablas["variable"]
        borrador = ui["altas"]["variable"]
        moneda_inicial = borrador["moneda"] or ui["moneda"]

        # Estimado es el último campo de la cadena: Enter lleva el foco al ✓ sin guardar.
        campo_estimado = _campo_monto_alta(
            "variable", borrador["estimado"], _decimales(moneda_inicial), HINT_ESTIMADO_ALTA, tabla.enfocar_confirmar,
        )
        campo_categoria = tabla.campo_filtrable_alta(
            "categoria", opciones_categoria, lambda id_: _guardar_borrador("variable"),
            placeholder=HINT_CATEGORIA_ALTA, valor_inicial_id=borrador["categoria"],
            on_avanzar=lambda: tabla.enfocar(campo_estimado.control),
        )
        selector_moneda, checkbox_recurrente = _moneda_y_recurrente_alta("variable", borrador, moneda_inicial)
        boton_confirmar = tabla.boton_confirmar_alta("AGREGAR VARIABLE", lambda: _confirmar_alta("variable"))
        alta_refs["variable"].update(
            categoria=campo_categoria, estimado=campo_estimado, moneda=selector_moneda,
            recurrente=checkbox_recurrente, boton=boton_confirmar,
        )

        return FilaAlta(
            celdas={
                "categoria": campo_categoria.control,
                "estimado": campo_estimado.control,
                "real": texto_celda(TEXTO_REAL_AUTOMATICO, color=TEXT_MUTED, tooltip=TOOLTIP_REAL_CATEGORIA),
                "moneda": selector_moneda.control,
                "recurrente": checkbox_recurrente,
            },
            boton=boton_confirmar,
            foco=campo_categoria.campo_texto,
        )

    def _confirmar_alta(seccion: str) -> None:
        # Deshabilitar ANTES de procesar: un doble Enter/click no dispara dos altas.
        boton = alta_refs[seccion]["boton"]
        if boton.disabled:
            return
        boton.disabled = True
        tablas[seccion].refrescar(boton)
        try:
            if seccion == "fijo":
                _procesar_alta_fijo()
            else:
                _procesar_alta_variable()
        finally:
            boton.disabled = False
            tablas[seccion].refrescar(boton)

    def _moneda_alta(seccion: str) -> Optional[dict]:
        moneda = monedas_por_codigo.get(alta_refs[seccion]["moneda"].valor)
        if moneda is None:
            _mostrar_error("COMPLETÁ LA MONEDA.")
        return moneda

    def _procesar_alta_fijo() -> None:
        _guardar_borrador("fijo")
        refs = alta_refs["fijo"]
        concepto = (refs["concepto"].value or "").strip()
        if not concepto:
            _mostrar_error("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
            return
        estimado, real = _valor_alta(refs["estimado"]), _valor_alta(refs["real"])
        if estimado is None or real is None:
            _mostrar_error("EL MONTO NO ES UN NÚMERO VÁLIDO.")
            return
        if not estimado and not real:
            _mostrar_error("INGRESÁ EL ESTIMADO O EL REAL.")
            return
        moneda = _moneda_alta("fijo")
        if moneda is None:
            return

        escala = 10 ** moneda["decimales"]
        try:
            resultado = presupuestos_service.create_fijo(
                concepto=concepto,
                monto_estimado_minor=round(estimado * escala),
                moneda_id=moneda["id"],
                mes=ui["mes"],
                anio=ui["anio"],
                es_recurrente=bool(refs["recurrente"].value),
                monto_real_minor=round(real * escala),
            )
        except PresupuestoError as err:
            _mostrar_error(str(err))  # la fila queda como estaba para corregir
            return
        _alta_ok("fijo", moneda["codigo"], resultado.message)

    def _procesar_alta_variable() -> None:
        _guardar_borrador("variable")
        refs = alta_refs["variable"]
        categoria_id = refs["categoria"].id_seleccionado
        if not categoria_id:
            _mostrar_error("ELEGÍ UNA CATEGORÍA DE LA LISTA (O COMPARTIDOS).")
            return
        estimado = _valor_alta(refs["estimado"])
        if estimado is None:
            _mostrar_error("EL MONTO NO ES UN NÚMERO VÁLIDO.")
            return
        if not estimado:
            _mostrar_error("INGRESÁ EL ESTIMADO.")
            return
        moneda = _moneda_alta("variable")
        if moneda is None:
            return

        try:
            resultado = presupuestos_service.create_variable(
                categoria_id=categoria_id,
                monto_estimado_minor=round(estimado * 10 ** moneda["decimales"]),
                moneda_id=moneda["id"],
                mes=ui["mes"],
                anio=ui["anio"],
                es_recurrente=bool(refs["recurrente"].value),
            )
        except PresupuestoError as err:
            _mostrar_error(str(err))  # la fila queda como estaba para corregir
            return
        _alta_ok("variable", moneda["codigo"], resultado.message)

    def _alta_ok(seccion: str, codigo: str, mensaje: str) -> None:
        ui["altas"][seccion] = _alta_vacia(seccion, codigo)
        otra_moneda = codigo != ui["moneda"]
        ui["moneda"] = codigo  # la fila nueva se ve aunque sea de otra moneda
        tablas[seccion].alta_ok()
        if otra_moneda:
            for otra, tabla in tablas.items():
                if otra != seccion:
                    tabla.recargar(limpiar_seleccion=True)
        mostrar_mensaje(page, mensaje)

    # ------------------------------------------------------------
    # FILAS DE DATOS: celdas editables inline (CLAUDE.md §10)
    # ------------------------------------------------------------

    def _guardador(p: dict) -> Callable[..., str]:
        def _guardar(**kwargs: Any) -> str:
            # PresupuestoError (y ValueError) suben a la celda, que los muestra y vuelve al valor anterior.
            return presupuestos_service.update(p["id"], **kwargs).message
        return _guardar

    def _celda_moneda(seccion: str, p: dict) -> ft.Control:
        """Botón ARS ↔ USD que guarda al tocarlo (ver docstring, "Moneda")."""
        tabla = tablas[seccion]
        envoltorio = ft.Container()

        def _cambiar(codigo: str) -> None:
            moneda = monedas_por_codigo[codigo]
            cambios = {
                "moneda_id": moneda["id"],
                "monto_estimado_minor": _reescalar(p["monto_estimado_minor"], p["decimales"], moneda["decimales"]),
            }
            if seccion == "fijo":  # el real de un variable no se guarda: se calcula
                cambios["monto_real_minor"] = _reescalar(p["monto_real_minor"], p["decimales"], moneda["decimales"])
            try:
                presupuestos_service.update(p["id"], **cambios)
            except PresupuestoError as err:
                _dibujar_selector()  # vuelve a la moneda guardada
                tabla.refrescar(envoltorio)
                _mostrar_error(str(err))
                return
            tabla.recargar()
            tabla.mostrar_ok(f"'{p['nombre']}' PASÓ A {codigo}.")

        def _dibujar_selector() -> None:
            opciones = _opciones_moneda(p["currency_code"])
            # pagina_alta: parchea solo el botón (si la fila ya se reconstruyó, nada).
            selector = SelectorCiclico(
                tabla.pagina_alta, opciones, p["currency_code"], _cambiar, "MONEDA",
                colores={codigo: TEXT_SECONDARY for codigo, _ in opciones},
            )
            envoltorio.content = selector.control

        _dibujar_selector()
        return tabla.celda_lectura("moneda", envoltorio)

    def _celda_recurrente(seccion: str, p: dict) -> ft.Control:
        tabla = tablas[seccion]

        def _cambiar(e: ft.ControlEvent) -> None:
            try:
                presupuestos_service.update(p["id"], es_recurrente=bool(e.control.value))
            except PresupuestoError as err:
                e.control.value = not e.control.value
                tabla.refrescar(e.control)
                _mostrar_error(str(err))
                return
            tabla.recargar()

        return tabla.celda_lectura(
            "recurrente",
            ft.Checkbox(
                value=bool(p["es_recurrente"]), active_color=TEXT_ACCENT, tooltip=TOOLTIP_RECURRENTE,
                on_change=_cambiar,
            ),
        )

    def _celda_estimado(seccion: str, p: dict) -> ft.Control:
        return tablas[seccion].celda_monto(
            p, "estimado", _texto_monto(p["monto_estimado_minor"], p), TEXT_PRIMARY, p["monto_estimado_minor"],
            p["decimales"], lambda monto_minor: _guardador(p)(monto_estimado_minor=monto_minor),
        )

    def _celdas_fijo(p: dict) -> dict[str, ft.Control]:
        tabla = tablas["fijo"]
        guardar = _guardador(p)
        return {
            "concepto": tabla.celda_texto(p, "concepto", p["nombre"], lambda nuevo: guardar(concepto=nuevo)),
            "estimado": _celda_estimado("fijo", p),
            "real": tabla.celda_monto(
                p, "real", _texto_real(p), _color_real(p), p["monto_real_minor"], p["decimales"],
                lambda monto_minor: guardar(monto_real_minor=monto_minor),
            ),
            "moneda": _celda_moneda("fijo", p),
            "recurrente": _celda_recurrente("fijo", p),
        }

    def _celdas_variable(p: dict) -> dict[str, ft.Control]:
        tabla = tablas["variable"]
        guardar = _guardador(p)
        if p["es_compartidos"]:
            tooltip_real = TOOLTIP_REAL_COMPARTIDOS if p["real_minor"] is not None else TOOLTIP_SIN_USUARIO
        else:
            tooltip_real = TOOLTIP_REAL_CATEGORIA
        return {
            "categoria": tabla.celda_filtrable(
                p, "categoria",
                lambda: texto_celda(
                    p["nombre"], color=TEXT_ACCENT if p["es_compartidos"] else TEXT_PRIMARY,
                    tooltip=TOOLTIP_COMPARTIDOS if p["es_compartidos"] else None,
                ),
                opciones_categoria, p["categoria_id"] or CATEGORIA_COMPARTIDOS,
                lambda nuevo: guardar(categoria_id=nuevo),
            ),
            "estimado": _celda_estimado("variable", p),
            # Calculado del Registro: solo lectura.
            "real": tabla.celda_lectura(
                "real",
                texto_celda(
                    _texto_real(p), color=_color_real(p), size=TypographyTokens.REGISTRO_FONT_MONTO,
                    weight=PESO_MONTO, tooltip=tooltip_real,
                ),
            ),
            "moneda": _celda_moneda("variable", p),
            "recurrente": _celda_recurrente("variable", p),
        }

    # ------------------------------------------------------------
    # BARRA FLOTANTE: eliminar
    # ------------------------------------------------------------

    def _eliminar(filas: list[dict]) -> tuple[str, bool]:
        errores = []
        for p in filas:
            try:
                presupuestos_service.delete(p["id"])
            except PresupuestoError as err:
                errores.append(f"{p['nombre']}: {err}")
        eliminados = len(filas) - len(errores)
        if errores:
            return f"{eliminados} ELIMINADO(S), {len(errores)} CON ERROR: " + " | ".join(errores), True
        return f"{eliminados} PRESUPUESTO(S) ELIMINADO(S).", False

    # ------------------------------------------------------------
    # ARMADO
    # ------------------------------------------------------------

    def _seccion(seccion: str) -> ft.Control:
        tabla = TablaPlanilla(
            page,
            clave=CLAVE_TABLA[seccion],
            columnas=COLUMNAS[seccion],
            pref_anchos=PREF_ANCHOS_COLUMNAS[seccion],
            cargar_filas=lambda: _cargar_filas(seccion),
            construir_celdas=_celdas_fijo if seccion == "fijo" else _celdas_variable,
            construir_alta=_construir_alta_fijo if seccion == "fijo" else _construir_alta_variable,
            firma=_firma,
            valor_columna=_valor_columna,
            clave_orden=_clave_orden,
            texto_busqueda=lambda p: p["nombre"],
            on_eliminar=_eliminar,
            al_recargar=_al_recargar,
            filas_pie=lambda: _filas_pie(seccion),
            errores_esperados=(PresupuestoError,),
            texto_vacio=TEXTO_VACIO[seccion],
        )
        # Antes de construir(): los callbacks de la fila de alta la buscan acá.
        tablas[seccion] = tabla
        return tabla.construir()

    control_fijos = _seccion("fijo")
    control_variables = _seccion("variable")
    _dibujar_pills()

    # Solo ícono: con texto no entraba en la barra (ver docstring, "COPIAR RECURRENTES").
    boton_copiar = _pill(
        None, False, _copiar_recurrentes,
        tooltip="COPIAR RECURRENTES: COPIA LOS PRESUPUESTOS RECURRENTES DEL MES ANTERIOR A ESTE MES",
        icono=ft.Icons.CONTENT_COPY,
    )
    titulo = barra_titulo(
        page, "PRESUPUESTOS", _BusquedaEnSecciones([tablas["fijo"], tablas["variable"]]), ui,
        lambda: _recargar_todas(limpiar_seleccion=True), "BUSCAR EN PRESUPUESTOS…",
        acciones=[contenedor_pills, boton_copiar],
    )
    nota_variables = ft.Text(NOTA_REAL_VARIABLES, size=TypographyTokens.METADATA_SIZE, color=TEXT_MUTED, italic=True)
    # Scroll propio (el Registro lo tiene por la Column del dashboard).
    return ft.Column(
        [
            pantalla_planilla([
                _con_volver(titulo, on_volver),
                _titulo_seccion(TITULO_SECCION["fijo"]),
                control_fijos,
                _titulo_seccion(TITULO_SECCION["variable"]),
                control_variables,
                nota_variables,
            ]),
        ],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
