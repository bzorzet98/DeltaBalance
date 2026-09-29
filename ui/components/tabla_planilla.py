"""
DeltaBalance — ui/components/tabla_planilla.py

Tabla estilo planilla (tema oscuro) compartida por el Registro de
transacciones (ui/components/registro_transacciones.py) y Compras en cuotas
(ui/screens/compras_cuotas.py): encabezados filtrables/ordenables/
redimensionables, fila de alta pegada al encabezado, filas con edición
inline, selección múltiple con barra flotante (eliminar / compartir). Más
las piezas de cabecera que usan las dos pantallas, para que se vean
idénticas: barra_titulo() (título + buscador + selector de mes),
barra_resumen() (la barra "saldo por cuenta") y pantalla_planilla() (el
fondo y márgenes de la pantalla). Paleta en ui/theme/tabla_tokens.py.

Cada pantalla aporta solo lo suyo — columnas (Columna), cómo se cargan las
filas, qué celda va en cada columna (con los helpers celda_texto()/
celda_filtrable()/celda_monto()/celda_lectura()), su fila de alta (FilaAlta)
y qué hacen eliminar/compartir — y toda la mecánica visual y de
interacción vive acá, una sola vez.

Uso típico:
    tabla = TablaPlanilla(page, clave="registro", columnas=[...], ...)
    control = tabla.construir()   # después de asignar `tabla`: los
                                  # callbacks de la pantalla la usan
    ...
    tabla.recargar()              # tras un cambio de datos
    tabla.alta_ok()               # tras un alta exitosa

--- Estado que sobrevive a las reconstrucciones ---

El estado de interfaz de cada tabla vive en un almacén por (página, clave)
(_ESTADOS, ver _estado_tabla()): búsqueda, filtros por columna, orden,
selección, anchos de columna, popup abierto. Sobrevive cuando ui/app.py
reconstruye la pantalla (otra pantalla cambió datos) y al navegar.

--- Refresco parcial ---

Tras un cambio de datos la pantalla llama a recargar(): vuelve a pedir las
filas (cargar_filas), re-dibuja la tabla y parchea solo esas partes, más
lo que devuelva al_recargar() (ej. la barra de saldo). Las filas se
cachean por id (fila["id"]) junto con una "firma" de lo que muestran
(firma(fila)): una fila cuya firma no cambió se reusa tal cual — solo se le
ajustan fondo alternado, selección, checkbox y anchos —, así que filtrar,
ordenar, buscar o guardar una celda reconstruye únicamente las filas que
cambiaron de verdad.

--- Performance: nunca un page.update() completo ---

page.update() sin argumentos re-diffea la página entera — y con ui/app.py
manteniendo vivas todas las pantallas visitadas, eso las incluye a todas.
Acá se parchea solo lo que cambió: refrescar(*controles) →
page.update(*controles), únicamente con controles montados en la sesión
(_montado()). Dos detalles de Flet 0.86.5 que lo hacen necesario:

- Auto-update: un handler de evento que no llama a ningún update() termina
  en un page.update() completo automático (flet/messaging/session.py,
  after_event()). Los handlers que solo guardan estado lo apagan con
  ft.context.disable_auto_update() (sin_auto_update()).
- CampoFiltrable/CampoMonto llaman a page.update() por dentro. De `page`
  solo usan update() (verificado en su código), así que reciben una
  _ActualizacionLocal: un reemplazo de page cuyo update() parchea solo la
  celda/fila donde viven (cualquier otro atributo se delega a la página
  real).

Excepción consciente: los SnackBar (mostrar_mensaje()) siguen el patrón del
proyecto (page.overlay + page.update()). Es un update completo por mensaje,
no por evento de alta frecuencia; page.show_dialog() lo evitaría, pero
dejaría el SnackBar en la pila de diálogos y page.pop_dialog() podría
cerrarlo a él en vez de un AlertDialog abierto.

--- Columnas: ancho completo, el vecino absorbe ---

La tabla ocupa siempre el 100% del ancho disponible: las columnas
redimensionables usan `expand` (flex) proporcional a ui["anchos"] y las
fijas un `width` en px; entre las redimensionables se reparten todo lo que
dejan libre las fijas, sea cual sea el tamaño de la ventana. Las columnas
de checkbox y de acción también son fijas.

Arrastrar el borde derecho de una columna redimensionable le pasa el
diferencial a la columna inmediatamente a la derecha (estilo Google
Sheets), respetando el mínimo de las dos (Columna.ancho_min): el ancho
total no cambia. Por eso solo tiene handle una columna redimensionable
cuya vecina de la derecha también lo es. Durante el drag se actualizan
solo el encabezado y la fila de alta; las filas de datos se ajustan al
soltar (re-anchar cientos de filas en cada evento de drag era un cuello de
botella). "Ajustar al contenido"/"Ajustar todas" reparten el espacio entre
las demás columnas redimensionables en proporción (_repartir()).

Para convertir píxeles (drag, mínimos, posición de las sugerencias) a esas
proporciones hace falta el ancho real de la tabla: lo informa
Container.on_size_change (ui["ancho_util"]). Mientras no llegó, se asume
1 unidad = 1 px.

Los anchos se guardan en .deltabalance_prefs.json (ui/utils/prefs.py,
clave `pref_anchos` de cada tabla) como proporciones relativas.

--- Popups: un host fijo en page.overlay ---

El menú contextual del encabezado y el overlay de filtro se abren dentro
de un único host por tabla (ui["host_popups"], en page.overlay, creado una
sola vez): una capa transparente a pantalla completa + el popup
posicionado en el puntero (TapEvent.global_position). Abrir/cerrar parchea
solo el host.

La capa escucha click izquierdo y derecho (GestureDetector on_tap_down /
on_secondary_tap_down) sobre un fondo casi transparente (COLOR_CAPA, para
que sea hit-testeable sí o sí): cualquier click afuera cierra el popup. Si
ese mismo click cae sobre el encabezado de otra columna, además abre el
popup nuevo sin un segundo click — click derecho → su menú, click en su ▼ →
su filtro. Para saber qué columna hay debajo, al abrirse cada popup se
anota dónde está el encabezado en pantalla (ui["ancla_header"], sacado de
global_position − local_position del evento que lo abrió) y se mapea con
los anchos de columna. Mientras la capa tapa todo, el dashboard no puede
scrollear, así que esa posición no queda vieja. (Flet no tiene
page.on_click ni stop_propagation: la capa cumple ese rol.)

La barra flotante de selección vive en page.overlay por el mismo motivo
(fija abajo de la ventana, no al final de la tabla), una por tabla, y se
esconde cuando la pantalla se oculta (hook data["al_ocultar"] del control
de la tabla, ver ui/app.py).

--- Fila de alta ---

La pantalla arma los campos (FilaAlta) y la tabla los pone en celdas de
alto fijo (ALTURA_FILA) con clip_behavior=HARD_EDGE, centradas; todos los
TextField usan estilo_campo() (mismo content_padding). Las sugerencias de
CampoFiltrable (campo_filtrable_alta() y la edición inline de celda_
filtrable()) no empujan el layout: _CampoFiltrableFlotante le pasa la
lista a capa_sugerencias, una capa posicionada dentro de un Stack que
envuelve la tabla, justo debajo de la celda. No va en page.overlay: ahí
haría falta la posición de la celda en pantalla, que Flet solo da en
eventos de puntero (no al llegar con Tab) y que queda vieja al scrollear;
dentro de la tabla sale de los anchos de columna y del alto de las filas.
Si hay pocas filas debajo, relleno_tabla estira la tabla lo justo para que
la lista entre entera (fuera de los límites del Stack no recibiría
clicks).

tab_a_confirmar(control): con el foco en ese control (el Dropdown de
Moneda, último campo del alta), Tab lleva el foco al botón ✓ de la fila, y
ahí Enter confirma (un IconButton enfocado se activa con Enter). El foco se
fuerza a mano, sin depender de que el recorrido nativo de Tab caiga justo
en el ✓ (el DropdownMenu tiene su propio ícono de flecha): Tab se detecta
con page.on_keyboard_event (un despachador por página, _registrar_tecla(),
que respeta un manejador previo) mientras el control tiene el foco
(on_focus/on_blur). Shift+Tab no se toca.

focus() es async en Flet 0.86.5: se llama con page.run_task() (enfocar()).

--- Filas ---

Todas las celdas centran su contenido en los dos ejes (cada Columna trae su
alineacion; Monto va centrado vertical y a la derecha). En los
encabezados el título se centra en la celda entera (a la izquierda lleva el
mismo ancho que ocupan los íconos de la derecha). La fila de alta y los
campos de edición inline centran el texto igual.

Edición inline (CLAUDE.md §10): modo lectura por default; el campo se crea
al hacer click y se destruye al salir (Enter, ✓ o blur; celda_dropdown():
al elegir una opción, ver su docstring). on_guardar(valor)
de cada celda guarda (y devuelve el mensaje de OK, o None); para rechazar
el valor lanza ValueError o una de errores_esperados — el mensaje se
muestra y la celda vuelve a modo lectura. Tras guardar, la fila se
reconstruye (recargar()). En celdas más angostas que ANCHO_MINIMO_CON_BOTON
el campo va sin ✓ (no entra): confirma con Enter o al salir.

fila_atenuada(fila) (ej. compras canceladas) dibuja la fila con
OPACIDAD_FILA_ATENUADA; qué celdas se pueden editar lo decide la pantalla.

--- Compartir ---

on_compartir recibe TODAS las filas seleccionadas (una o varias): cada
pantalla decide si abre su flujo de una fila o el de varias filas con el
mismo coeficiente (ui/components/compartir_varios.py).

--- Sin confirmar corriendo la app (docs/FLET_API_NOTES.md, regla 2) ---

Confirmado por lectura del código instalado de Flet 0.86.5, pero sin
precedente en este proyecto: GestureDetector (on_horizontal_drag_update/
primary_delta, on_tap_down/on_secondary_tap_down con global_position/
local_position, on_enter/on_exit, mouse_cursor), controles posicionados
(left/top) dentro de page.overlay y de un ft.Stack, Container.on_size_change
(en particular, que se dispare en el primer layout y no solo al cambiar de
tamaño), `expand` entero (flex) sobre GestureDetector/Container dentro de
un Row, expand_loose, ft.context.disable_auto_update(),
page.on_keyboard_event (que llegue Tab aunque el foco esté en un
Dropdown), que Enter active un IconButton enfocado, ft.DatePicker vía
page.show_dialog() y page.run_task() sobre focus().
"""

import calendar
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import flet as ft

from ui.components.campo_filtrable import ALTURA_ITEM_SUGERENCIA, ALTURA_MAX_LISTA, CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.theme.tabla_tokens import (
    BG_APP,
    BG_BARRA_FLOT,
    BG_ENCABEZADO,
    BG_FILA_ALTA,
    BG_FILA_HOVER,
    BG_FILA_IMPAR,
    BG_FILA_PAR,
    BG_FILA_SEL,
    BG_ITEM_HOVER,
    BG_MENU_CTX,
    BG_OVERLAY,
    BG_SUPERFICIE,
    BORDER_BARRA,
    BORDER_DEFAULT,
    BORDER_HEADER,
    BORDER_OVERLAY,
    BTN_COMPARTIR,
    BTN_ELIMINAR,
    COLOR_CUENTA_SIN_ELEGIR,
    DOTS_CUENTAS,
    PESO_CELDA,
    PESO_HEADER,
    PESO_MONTO,
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_SOBRE_BOTON,
)
from ui.theme.tokens import LayoutTokens, TypographyTokens
from ui.utils.prefs import escribir_pref, leer_pref

# --- Configuración de layout ---

ANCHO_COL_CHECK = 32
ANCHO_COL_ACCION = 48  # ✓ de la fila de alta / indicador de la fila (ej. compartido)
ANCHO_BORDE = 1
ANCHO_MIN_COLUMNA = 80
# `expand` solo acepta enteros: proporción × FACTOR_FLEX (resolución 0.1).
FACTOR_FLEX = 10
# "Ajustar al contenido": ancho aproximado de un carácter a REGISTRO_FONT_CELDA + margen.
ANCHO_POR_CARACTER = 7.5
PADDING_AJUSTE = 28

ALTURA_HEADER = 40
ALTURA_FILA = LayoutTokens.ALTURA_FILA_TABLA
PADDING_CELDA_H = 8
PADDING_CAMPO = 6  # content_padding de todos los TextField sin borde
ALTA_PADDING = 4  # alrededor de cada campo de la fila de alta (vertical de la fila, horizontal de la celda)
# Alto total de la fila de alta: celda + padding vertical + borde inferior de acento.
ALTURA_FILA_ALTA = ALTURA_FILA + 2 * ALTA_PADDING + ANCHO_BORDE
ALTURA_VACIO = 48  # fila "no hay filas para mostrar"
ALTA_RADIO_CAMPO = 4
ICONO_HEADER = 16
ICONO_CALENDARIO = 16
ICONO_ACCION_FILA = 14
ANCHO_BOTON_CALENDARIO = 32
# Por debajo de este ancho (px) una celda en edición va sin botón ✓ (no
# entra junto al campo): confirma con Enter o al salir.
ANCHO_MINIMO_CON_BOTON = 100
OPACIDAD_FILA_ATENUADA = 0.45
OPACIDAD_DESHABILITADO = 0.4

# Redimensionado
ANCHO_RESIZE_HANDLE = 8
ANCHO_LINEA_RESIZE = 3
RESIZE_DRAG_INTERVAL_MS = 30
SIZE_CHANGE_INTERVAL_MS = 100

# Sugerencias flotantes de CampoFiltrable
ANCHO_MINIMO_SUGERENCIAS = 160

# Cabecera (barra de título + barra de resumen): buscador, selector de mes
# y barra de resumen miden lo mismo de alto y usan el mismo tamaño de texto
# (TypographyTokens.REGISTRO_FONT_SALDO_BAR) — un escalón entre el título
# (PAGE_TITLE_SIZE) y la tabla (REGISTRO_FONT_CELDA).
ALTURA_CABECERA = 56
ICONO_CABECERA = 20
ANCHO_BUSQUEDA = 300
RADIO_CONTROL = 8
PADDING_SELECTOR_MES_H = 4
PADDING_RESUMEN_H = 16
PADDING_RESUMEN_V = 8
# Aire arriba/abajo de los chips dentro de su Row scrolleable: la scrollbar
# horizontal se dibuja sobre el borde inferior del área scrolleable, así
# que queda sobre este aire y no sobre el texto.
ESPACIO_SCROLLBAR_RESUMEN = 8
DOT_SIZE = 8
DOT_SIZE_RESUMEN = 10
ESPACIO_DOT = 6
# Columna.extra_ajuste de una columna que muestra banco_con_dot().
EXTRA_AJUSTE_DOT = DOT_SIZE + ESPACIO_DOT
ESPACIO_CHIPS = 20
PILL_ALTURA = 32
PILL_PADDING_H = 12
PILL_RADIO = 16
MONEDA_DEFAULT = "ARS"

# Overlay de filtro
ANCHO_OVERLAY_FILTRO = 240
ALTO_MAX_OVERLAY_FILTRO = 320
ALTURA_ITEM_FILTRO = 32
ALTURA_CONTROLES_FILTRO = 96  # búsqueda + botones, dentro del máximo
PADDING_OVERLAY = 8
RADIO_OVERLAY = 8

# Menú contextual
ANCHO_MENU_CTX = 220
ALTURA_ITEM_MENU = 32
ICONO_MENU = 14
PADDING_ITEM_MENU_H = 12
PADDING_MENU_V = 4
MARGEN_POPUP = 8
# Capa que cierra los popups: casi transparente, pero pintada (hit-testeable).
COLOR_CAPA = ft.Colors.with_opacity(0.01, ft.Colors.BLACK)
# Franja a la derecha de cada encabezado que cuenta como "click en ▼"
# cuando la capa re-despacha un click (ver docstring, "Popups").
ZONA_ICONO_FILTRO = 28

# Barra flotante
ALTURA_BARRA_FLOT = 52
DIAMETRO_BOTON_FLOT = 40
ICONO_BOTON_FLOT = 20
BARRA_MARGEN_IZQ = 88
BARRA_MARGEN_DER = 24
BARRA_MARGEN_INF = 24
PADDING_BARRA_H = 16
ESPACIO_BARRA = 12
PADDING_BOTON_TEXTO_H = 16
ALTURA_BOTON_TEXTO = 32

ESPACIADO = 8
RADIO_TABLA = 8
PADDING_PANTALLA = 16

# Tecla que, con el foco en el control de tab_a_confirmar(), lleva el foco
# al ✓ de la fila de alta — KeyboardEvent.key de Flet (etiqueta de la tecla
# lógica de Flutter).
TECLA_IR_A_CONFIRMAR = "Tab"


# ============================================================
# TIPOS
# ============================================================

@dataclass
class Columna:
    clave: str
    titulo: str                   # ya en MAYÚSCULAS
    ancho: int                    # px iniciales (redimensionable: proporción inicial)
    redimensionable: bool = True
    alineacion: ft.Alignment = field(default_factory=lambda: ft.Alignment.CENTER)
    ancho_min: int = ANCHO_MIN_COLUMNA
    extra_ajuste: int = 0         # px extra en "Ajustar al contenido" (ej. el dot de Banco)


@dataclass
class FilaAlta:
    """Lo que la pantalla le da a la tabla para su fila de alta."""
    celdas: dict[str, ft.Control]   # clave de columna → campo (sin celda: la pone la tabla)
    boton: ft.Control               # ✓ de la columna de acción
    foco: Optional[ft.Control] = None  # campo que recibe el foco tras un alta (el primero)


@dataclass
class ChipResumen:
    """Un chip de barra_resumen(): dot + nombre + monto + código de moneda."""
    color: str
    nombre: str
    monto: str
    color_monto: str
    moneda: str


# ============================================================
# HELPERS DE MÓDULO
# ============================================================

def _montado(page: ft.Page, control: Optional[ft.Control]) -> bool:
    """
    ¿El control está hoy en el árbol de la sesión? Flet no limpia `_parent`
    al sacar un control, así que control.page no alcanza: se consulta el
    índice de la sesión (page.get_control(control._i), mismo uso que el
    ejemplo de su docstring).
    """
    return control is not None and page.get_control(control._i) is control


def refrescar(page: ft.Page, *controles: Optional[ft.Control]) -> None:
    """Parchea solo estos controles (los que estén montados) — nunca la página entera."""
    montados = [c for c in controles if _montado(page, c)]
    if montados:
        page.update(*montados)
    else:
        ft.context.disable_auto_update()


def sin_auto_update() -> None:
    """Handlers que solo guardan estado: sin esto Flet hace un page.update() completo al terminar."""
    ft.context.disable_auto_update()


def mostrar_mensaje(page: ft.Page, mensaje: str, es_error: bool = False) -> None:
    # page.overlay + page.update(): ver docstring del módulo (excepción consciente).
    snack = ft.SnackBar(content=ft.Text(mensaje), bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None)
    page.overlay.append(snack)
    snack.open = True
    page.update()


def _clave_nombre(texto: str) -> str:
    """Sin tildes, en mayúsculas — para buscar colores de DOTS_CUENTAS por nombre."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")
    return " ".join(sin_tildes.upper().split())


def color_cuenta(cuenta: Optional[dict], nombre: str) -> str:
    """Color elegido en Cuentas; si nunca se eligió uno, DOTS_CUENTAS por nombre; si no, el default."""
    color = (cuenta or {}).get("color_hex")
    if color and color.upper() != COLOR_CUENTA_SIN_ELEGIR.upper():
        return color
    return DOTS_CUENTAS.get(_clave_nombre(nombre), DOTS_CUENTAS["DEFAULT"])


def _dot(color: str, tamanio: int = DOT_SIZE) -> ft.Control:
    return ft.Container(width=tamanio, height=tamanio, border_radius=tamanio / 2, bgcolor=color)


def estilo_campo(tamanio: int = TypographyTokens.REGISTRO_FONT_CELDA) -> dict:
    """TextField sin borde propio — el borde lo da la celda (o el contenedor del buscador)."""
    return {
        "border": ft.InputBorder.NONE,
        "dense": LayoutTokens.CELDA_DENSE,
        "text_size": tamanio,
        "color": TEXT_PRIMARY,
        "cursor_color": TEXT_ACCENT,
        "content_padding": ft.Padding.symmetric(horizontal=PADDING_CAMPO, vertical=PADDING_CAMPO),
        "hint_style": ft.TextStyle(size=tamanio, color=TEXT_MUTED),
    }


def sin_borde(campo: ft.TextField) -> None:
    """Mismo estilo que estilo_campo() sobre un TextField ya creado (el de CampoFiltrable/CampoMonto)."""
    for atributo, valor in estilo_campo().items():
        if atributo not in ("dense", "text_size"):
            setattr(campo, atributo, valor)


def _boton_texto(texto: str, on_click: Callable, relleno: Optional[str]) -> ft.Control:
    """Botón rectangular: relleno sólido (acción principal) o solo borde gris."""
    return ft.Container(
        height=ALTURA_BOTON_TEXTO,
        padding=ft.Padding.symmetric(horizontal=PADDING_BOTON_TEXTO_H),
        border_radius=RADIO_CONTROL,
        bgcolor=relleno,
        border=None if relleno else ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
        alignment=ft.Alignment.CENTER,
        on_click=on_click,
        content=ft.Text(
            texto, size=TypographyTokens.REGISTRO_FONT_BARRA_FLOT, weight=PESO_MONTO,
            color=TEXT_SOBRE_BOTON if relleno else TEXT_SECONDARY,
        ),
    )


def _boton_confirmar_celda(on_click=None) -> ft.IconButton:
    return ft.IconButton(
        icon=ft.Icons.CHECK,
        icon_color=TEXT_ACCENT,
        icon_size=LayoutTokens.ICONO_BOTON_CELDA,
        style=ft.ButtonStyle(padding=ft.Padding.all(LayoutTokens.PADDING_BOTON_CELDA)),
        on_click=on_click,
    )


def texto_celda(texto: str, color: str = TEXT_PRIMARY, size: int = TypographyTokens.REGISTRO_FONT_CELDA,
                weight=PESO_CELDA, tooltip: Optional[str] = None) -> ft.Text:
    """1 línea, ellipsis si no entra, tooltip con el texto completo (o `tooltip`, si se pasa)."""
    return ft.Text(
        texto, color=color, size=size, weight=weight, max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS, tooltip=tooltip or texto or None,
    )


def banco_con_dot(color: str, nombre: str) -> ft.Control:
    """Dot de color + nombre de la cuenta (en TEXT_SECONDARY: no compite con colores brillantes)."""
    # Row ajustada al contenido (tight) para que la celda la centre; el
    # nombre es flexible "loose": ocupa lo que necesita y, si no entra, se
    # corta con ellipsis.
    texto = texto_celda(nombre, color=TEXT_SECONDARY)
    texto.expand = True
    texto.expand_loose = True
    return ft.Row([_dot(color), texto], spacing=ESPACIO_DOT, tight=True)


# --- Teclado: un despachador de page.on_keyboard_event por página ---

_TECLADO: dict[int, dict[str, Callable[[ft.KeyboardEvent], None]]] = {}


def _registrar_tecla(page: ft.Page, clave: str, manejador: Callable[[ft.KeyboardEvent], None]) -> None:
    """
    page.on_keyboard_event es uno solo por página: la primera tabla registra
    un despachador (respetando un manejador previo, si lo hay) y cada tabla
    anota su manejador bajo su clave — la instancia vigente reemplaza a la
    anterior cuando ui/app.py reconstruye la pantalla.
    """
    manejadores = _TECLADO.get(id(page))
    if manejadores is None:
        manejadores = {}
        _TECLADO[id(page)] = manejadores
        manejador_previo = page.on_keyboard_event

        def _despachar_tecla(e: ft.KeyboardEvent) -> None:
            for manejar in list(manejadores.values()):
                manejar(e)
            if manejador_previo is not None:
                manejador_previo(e)
            else:
                # Cada tecla de la app pasa por acá: sin esto, cada una
                # terminaría en un page.update() completo automático.
                ft.context.disable_auto_update()

        page.on_keyboard_event = _despachar_tecla
    manejadores[clave] = manejador


# ============================================================
# PIEZAS DE CABECERA COMPARTIDAS
# ============================================================

def pantalla_planilla(controles: list[ft.Control]) -> ft.Control:
    """Fondo y márgenes de una pantalla estilo planilla (título, resumen, tabla…)."""
    return ft.Container(
        bgcolor=BG_APP,
        border_radius=RADIO_TABLA,
        padding=PADDING_PANTALLA,
        content=ft.Column(controles, spacing=ESPACIADO * 2, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
    )


def barra_titulo(
    page: ft.Page,
    titulo: str,
    tabla: "TablaPlanilla",
    periodo: dict,
    on_cambio_periodo: Callable[[], None],
    hint_busqueda: str,
) -> ft.Control:
    """
    Título de la pantalla (tamaño de título de página) + buscador + selector
    de mes. `periodo` es un dict con "mes"/"anio" que se muta en el lugar
    al cambiar de mes; después se llama a on_cambio_periodo(). El buscador
    filtra la tabla (TablaPlanilla.buscar()).
    """
    texto_periodo = ft.Text(size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, color=TEXT_PRIMARY)

    def _actualizar_texto_periodo() -> None:
        ultimo_dia = calendar.monthrange(periodo["anio"], periodo["mes"])[1]
        texto_periodo.value = (
            f"{periodo['anio']:04d}-{periodo['mes']:02d}-01  →  "
            f"{periodo['anio']:04d}-{periodo['mes']:02d}-{ultimo_dia:02d}"
        )

    def _cambiar_mes(delta: int) -> None:
        indice = periodo["anio"] * 12 + (periodo["mes"] - 1) + delta
        periodo["anio"], periodo["mes"] = indice // 12, indice % 12 + 1
        _actualizar_texto_periodo()
        on_cambio_periodo()
        refrescar(page, texto_periodo)

    _actualizar_texto_periodo()
    return ft.Container(
        height=ALTURA_CABECERA,
        content=ft.Row(
            [
                ft.Text(
                    titulo, size=TypographyTokens.PAGE_TITLE_SIZE,
                    weight=TypographyTokens.PAGE_TITLE_WEIGHT, color=TEXT_PRIMARY,
                ),
                ft.Container(expand=True),
                ft.Container(
                    width=ANCHO_BUSQUEDA,
                    height=ALTURA_CABECERA,
                    border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
                    border_radius=RADIO_CONTROL,
                    bgcolor=BG_SUPERFICIE,
                    padding=ft.Padding.only(left=PADDING_CELDA_H),
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.SEARCH, size=ICONO_CABECERA, color=TEXT_SECONDARY),
                            ft.TextField(
                                value=tabla.busqueda, hint_text=hint_busqueda,
                                on_change=lambda e: tabla.buscar(e.control.value or ""), expand=True,
                                **estilo_campo(TypographyTokens.REGISTRO_FONT_SALDO_BAR),
                            ),
                        ],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
                ft.Container(
                    height=ALTURA_CABECERA,
                    border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
                    border_radius=RADIO_CONTROL,
                    bgcolor=BG_SUPERFICIE,
                    padding=ft.Padding.symmetric(horizontal=PADDING_SELECTOR_MES_H),
                    content=ft.Row(
                        [
                            ft.IconButton(
                                icon=ft.Icons.CHEVRON_LEFT, icon_color=TEXT_SECONDARY, tooltip="MES ANTERIOR",
                                on_click=lambda e: _cambiar_mes(-1),
                            ),
                            ft.Icon(ft.Icons.CALENDAR_MONTH_OUTLINED, size=ICONO_CABECERA, color=TEXT_SECONDARY),
                            texto_periodo,
                            ft.IconButton(
                                icon=ft.Icons.CHEVRON_RIGHT, icon_color=TEXT_SECONDARY, tooltip="MES SIGUIENTE",
                                on_click=lambda e: _cambiar_mes(1),
                            ),
                        ],
                        spacing=ESPACIADO,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                ),
            ],
            spacing=ESPACIADO * 2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


def barra_resumen(
    titulo: str,
    icono: str,
    chips: list[ChipResumen],
    monedas: list[str],
    moneda_sel: str,
    on_moneda: Callable[[str], None],
    texto_vacio: str,
) -> ft.Control:
    """
    Barra "saldo por cuenta": ícono + título + chips (dot, nombre, monto,
    moneda; scroll horizontal si no entran) + pills de moneda a la derecha
    (la elegida, rellena). `monedas` se muestra ARS primero, después
    alfabético.
    """
    tamanio = TypographyTokens.REGISTRO_FONT_SALDO_BAR
    controles_chips: list[ft.Control] = [
        ft.Row(
            [
                _dot(chip.color, DOT_SIZE_RESUMEN),
                ft.Text(chip.nombre, size=tamanio, color=TEXT_SECONDARY),
                ft.Text(chip.monto, size=tamanio, weight=PESO_MONTO, color=chip.color_monto),
                ft.Text(chip.moneda, size=tamanio, color=TEXT_MUTED),
            ],
            spacing=ESPACIO_DOT,
        )
        for chip in chips
    ] or [ft.Text(texto_vacio, size=tamanio, color=TEXT_MUTED, italic=True)]

    pills: list[ft.Control] = []
    for codigo in sorted(set(monedas) | {moneda_sel}, key=lambda c: (c != MONEDA_DEFAULT, c)):
        activa = codigo == moneda_sel
        pills.append(
            ft.Container(
                height=PILL_ALTURA,
                padding=ft.Padding.symmetric(horizontal=PILL_PADDING_H),
                border_radius=PILL_RADIO,
                bgcolor=BTN_COMPARTIR if activa else None,
                border=None if activa else ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
                alignment=ft.Alignment.CENTER,
                on_click=lambda e, c=codigo: on_moneda(c),
                content=ft.Text(
                    codigo, size=tamanio, weight=PESO_HEADER,
                    color=TEXT_SOBRE_BOTON if activa else TEXT_SECONDARY,
                ),
            )
        )

    return ft.Container(
        height=ALTURA_CABECERA,
        bgcolor=BG_SUPERFICIE,
        border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
        border_radius=RADIO_TABLA,
        padding=ft.Padding.symmetric(horizontal=PADDING_RESUMEN_H, vertical=PADDING_RESUMEN_V),
        clip_behavior=ft.ClipBehavior.NONE,
        content=ft.Row(
            [
                ft.Icon(icono, size=ICONO_CABECERA, color=TEXT_SECONDARY),
                ft.Text(titulo, size=tamanio, weight=TypographyTokens.SECTION_TITLE_WEIGHT, color=TEXT_PRIMARY),
                ft.Row(
                    [
                        ft.Container(
                            padding=ft.Padding.symmetric(vertical=ESPACIO_SCROLLBAR_RESUMEN),
                            content=ft.Row(controles_chips, spacing=ESPACIO_CHIPS),
                        ),
                    ],
                    scroll=ft.ScrollMode.AUTO,
                    expand=True,
                ),
                ft.Row(pills, spacing=ESPACIO_DOT),
            ],
            spacing=ESPACIO_CHIPS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )


# ============================================================
# ESTADO POR (PÁGINA, TABLA) — ver docstring del módulo
# ============================================================

_ESTADOS: dict[tuple[int, str], dict] = {}


def _estado_tabla(page: ft.Page, clave: str, columnas: list[Columna], pref_anchos: str) -> dict:
    ui = _ESTADOS.get((id(page), clave))
    if ui is None:
        anchos: dict[str, float] = {c.clave: float(c.ancho) for c in columnas if c.redimensionable}
        guardados = leer_pref(pref_anchos, {})
        if isinstance(guardados, dict):
            for columna, ancho in guardados.items():
                if columna in anchos and isinstance(ancho, (int, float)) and ancho > 0:
                    anchos[columna] = float(ancho)
        ui = {
            "busqueda": "",
            "filtros": {},           # columna → set de valores visibles
            "orden": None,           # (columna, ascendente) o None
            "seleccion": set(),      # ids de fila
            "anchos": anchos,        # solo columnas redimensionables (proporciones)
            "ancho_util": None,      # px para las columnas redimensionables (on_size_change)
            "confirmando_eliminar": False,
            "visible": True,
            "barra": None,           # barra flotante (page.overlay), una por tabla
            "host_popups": None,     # host de menú/filtro (page.overlay), uno por tabla
            "popup": None,           # {"tipo": "menu" | "filtro", "columna": str} abierto
            "ancla_header": None,    # (x, y) en pantalla del inicio de las columnas de datos del encabezado
        }
        _ESTADOS[(id(page), clave)] = ui
    return ui


# ============================================================
# ACTUALIZACIÓN LOCAL Y SUGERENCIAS FLOTANTES
# ============================================================

class _ActualizacionLocal:
    """
    Reemplazo de `page` para CampoFiltrable/CampoMonto dentro de la tabla
    (ver docstring del módulo, "Performance"). Esos componentes solo llaman
    a page.update(); acá eso parchea únicamente los controles que devuelve
    `controles()` (la celda o fila donde vive el campo), no la página
    entera. Cualquier otro atributo se delega a la página real.
    """

    def __init__(self, page: ft.Page, controles: Callable[[], list[ft.Control]]):
        self._pagina = page
        self._controles = controles

    def update(self, *controles: ft.Control) -> None:
        refrescar(self._pagina, *(controles or self._controles()))

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._pagina, nombre)


class _CampoFiltrableFlotante(CampoFiltrable):
    """
    CampoFiltrable cuya lista de sugerencias NO vive en su propia Column
    (que empuja el layout): se la entrega a `al_mostrar_lista(campo, lista)`
    y la tabla la pone en capa_sugerencias. Sobreescribe solo los dos
    métodos que ubican la lista — filtrado, selección y teclado siguen
    siendo los de CampoFiltrable, sin tocar ui/components/campo_filtrable.py.
    `al_salir` (opcional) se llama después de que el blur terminó de
    resolverse (para que una celda inline vuelva a modo lectura).
    """

    def __init__(
        self,
        page: Any,
        opciones: list[tuple[str, str]],
        on_seleccionar: Callable[[Optional[str]], None],
        *,
        al_mostrar_lista: Callable[["_CampoFiltrableFlotante", ft.Container], None],
        al_ocultar_lista: Callable[["_CampoFiltrableFlotante"], None],
        al_salir: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ):
        self._al_mostrar_lista = al_mostrar_lista
        self._al_ocultar_lista = al_ocultar_lista
        self._al_salir = al_salir
        super().__init__(page, opciones, on_seleccionar, **kwargs)

    @property
    def campo_texto(self) -> ft.TextField:
        """El TextField interno (estilo sin borde y foco — CampoFiltrable no los expone)."""
        return self._campo

    def _mostrar_sugerencias(self, filtradas: list[tuple[str, str]]) -> None:
        super()._mostrar_sugerencias(filtradas)
        if not filtradas:
            return  # super() ya llamó a _ocultar_sugerencias()
        # super() metió la lista debajo del TextField: se la saca de ahí.
        self.control.controls = [self._campo]
        self._contenedor_sugerencias.height = min(
            ALTURA_MAX_LISTA, len(filtradas) * ALTURA_ITEM_SUGERENCIA + 2 * ANCHO_BORDE,
        )
        self._al_mostrar_lista(self, self._contenedor_sugerencias)

    def _ocultar_sugerencias(self) -> None:
        super()._ocultar_sugerencias()
        self._al_ocultar_lista(self)

    async def _on_blur(self, e: ft.ControlEvent) -> None:
        await super()._on_blur(e)
        if self._al_salir is not None:
            self._al_salir()


# ============================================================
# TABLA
# ============================================================

class TablaPlanilla:
    """
    Args (todos por nombre):
        clave:            Identifica la tabla ("registro", "compras"): separa
                          su estado y su despachador de teclado.
        columnas:         Columnas de datos, en orden (sin checkbox ni acción).
        pref_anchos:      Clave en .deltabalance_prefs.json para los anchos.
        cargar_filas:     Filas actuales (dicts con "id") — se llama al
                          construir y en cada recargar().
        construir_celdas: fila → {clave de columna: celda}, armadas con
                          celda_texto()/celda_filtrable()/celda_monto()/
                          celda_lectura().
        construir_alta:   → FilaAlta. Se llama al construir y tras alta_ok().
        firma:            fila → tupla de todo lo que la fila muestra.
        valor_columna:    (fila, clave) → texto tal como se muestra (filtros,
                          orden y "Ajustar al contenido").
        on_eliminar:      filas seleccionadas → (mensaje, es_error). Borra/
                          cancela; la tabla después limpia la selección,
                          recarga y muestra el mensaje.
        texto_busqueda:   fila → texto donde busca el buscador.
        clave_orden:      (fila, clave) → clave de orden (default: el texto
                          de valor_columna en minúsculas).
        accion_fila:      fila → ícono de la columna de acción, o None.
        fila_atenuada:    fila → True para dibujarla atenuada.
        avisos_eliminar:  filas → aviso extra para la confirmación de borrado.
        on_compartir:     filas seleccionadas (una o varias) → abre el flujo
                          de compartir. None = sin botón Compartir.
        al_recargar:      → controles extra a parchear en cada recargar()
                          (ej. la barra de resumen, ya re-armada).
        errores_esperados: excepciones de dominio que rechazan un valor
                          editado (además de ValueError).
        texto_vacio:      Fila que se muestra si no hay filas visibles.
    """

    def __init__(
        self,
        page: ft.Page,
        *,
        clave: str,
        columnas: list[Columna],
        pref_anchos: str,
        cargar_filas: Callable[[], list[dict]],
        construir_celdas: Callable[[dict], dict[str, ft.Control]],
        construir_alta: Callable[[], FilaAlta],
        firma: Callable[[dict], tuple],
        valor_columna: Callable[[dict, str], str],
        on_eliminar: Callable[[list[dict]], tuple[str, bool]],
        texto_busqueda: Callable[[dict], str] = lambda fila: "",
        clave_orden: Optional[Callable[[dict, str], Any]] = None,
        accion_fila: Optional[Callable[[dict], Optional[ft.Control]]] = None,
        fila_atenuada: Optional[Callable[[dict], bool]] = None,
        avisos_eliminar: Optional[Callable[[list[dict]], str]] = None,
        on_compartir: Optional[Callable[[list[dict]], None]] = None,
        al_recargar: Optional[Callable[[], list[ft.Control]]] = None,
        errores_esperados: tuple[type[Exception], ...] = (),
        texto_vacio: str = "NO HAY FILAS PARA MOSTRAR.",
    ):
        self._page = page
        self._clave = clave
        self._columnas = columnas
        self._por_clave = {c.clave: c for c in columnas}
        self._claves = [c.clave for c in columnas]
        self._pref_anchos = pref_anchos
        self._cargar_filas = cargar_filas
        self._construir_celdas = construir_celdas
        self._construir_alta = construir_alta
        self._firma = firma
        self._valor_columna = valor_columna
        self._on_eliminar = on_eliminar
        self._texto_busqueda = texto_busqueda
        self._clave_orden = clave_orden or (lambda fila, c: self._valor_columna(fila, c).lower())
        self._accion_fila = accion_fila
        self._fila_atenuada = fila_atenuada
        self._avisos_eliminar = avisos_eliminar
        self._on_compartir = on_compartir
        self._al_recargar = al_recargar
        self._errores = (ValueError, *errores_esperados)
        self._texto_vacio = texto_vacio

        self._ui = _estado_tabla(page, clave, columnas, pref_anchos)
        self._datos: list[dict] = []
        # Filas cacheadas por id (ver docstring, "Refresco parcial").
        self._cache: dict[int, dict] = {}
        self._mostradas: list[int] = []  # ids en pantalla, en orden

        self._contenedor_header = ft.Container()
        self._contenedor_alta = ft.Container()
        self._tabla_filas = ft.Column(spacing=0)
        self._checkbox_todas = ft.Checkbox(value=False, active_color=TEXT_ACCENT, on_change=self._on_checkbox_todas)
        # Sugerencias flotantes de CampoFiltrable (ver docstring, "Fila de alta").
        self._capa_sugerencias = ft.Container(visible=False)
        self._relleno = ft.Container(height=0)
        self._sugerencias_duenio: Any = None
        # Celdas por columna del encabezado y de la fila de alta — el drag
        # les cambia el `expand` en vivo (las filas de datos, al soltar).
        self._celdas_header: dict[str, ft.Control] = {}
        self._celdas_alta: dict[str, ft.Control] = {}
        self._alta: Optional[FilaAlta] = None
        # Foco del control de tab_a_confirmar() (None = la fila no usa uno).
        self._foco_tab: Optional[dict] = None

        # Reemplazo de page para los campos del alta (ver _ActualizacionLocal).
        self.pagina_alta = _ActualizacionLocal(
            page, lambda: [self._contenedor_alta, self._capa_sugerencias, self._relleno],
        )

        # Barra flotante y host de popups: uno por tabla, en page.overlay
        # (la barra primero, así los popups quedan encima de ella).
        if self._ui["barra"] is None:
            self._ui["barra"] = ft.Container(
                left=BARRA_MARGEN_IZQ, right=BARRA_MARGEN_DER, bottom=BARRA_MARGEN_INF,
                height=ALTURA_BARRA_FLOT,
                bgcolor=BG_BARRA_FLOT,
                border=ft.Border.only(top=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_BARRA)),
                border_radius=0,
                padding=ft.Padding.symmetric(horizontal=PADDING_BARRA_H),
                visible=False,
            )
            page.overlay.append(self._ui["barra"])
        if self._ui["host_popups"] is None:
            self._ui["host_popups"] = ft.Container(left=0, top=0, right=0, bottom=0, visible=False)
            page.overlay.append(self._ui["host_popups"])
        self._barra: ft.Container = self._ui["barra"]
        self._host_popups: ft.Container = self._ui["host_popups"]

    # ------------------------------------------------------------
    # API PÚBLICA
    # ------------------------------------------------------------

    def construir(self) -> ft.Control:
        """Carga las filas y arma la tabla. Llamar después de asignar la instancia (los callbacks la usan)."""
        self._datos = [dict(f) for f in self._cargar_filas()]
        self._ui["visible"] = True
        # Si ui/app.py reconstruye la pantalla con un popup abierto, el host
        # quedaría con controles de la instancia anterior.
        self._ui["popup"] = None
        self._host_popups.visible = False
        self._host_popups.content = None
        self._contenedor_alta.content = self._construir_fila_alta()
        self._redibujar()
        _registrar_tecla(self._page, self._clave, self._al_tecla)

        cuerpo = ft.Column(
            [self._contenedor_header, self._contenedor_alta, self._tabla_filas, self._relleno],
            spacing=0, tight=True,
        )
        tabla = ft.Container(
            bgcolor=BG_SUPERFICIE,
            border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
            border_radius=RADIO_TABLA,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            size_change_interval=SIZE_CHANGE_INTERVAL_MS,
            on_size_change=self._on_tamanio_tabla,
            # Stack: capa_sugerencias flota sobre las filas (ver docstring, "Fila de alta").
            content=ft.Stack([cuerpo, self._capa_sugerencias]),
        )
        # Hooks de navegación de ui/app.py (hace page.update() justo después).
        tabla.data = {"al_mostrar": self._al_mostrar, "al_ocultar": self._al_ocultar}
        return tabla

    def recargar(self, limpiar_seleccion: bool = False) -> None:
        """Hubo un cambio de datos (o de período): recarga y re-dibuja solo lo afectado."""
        self._cerrar_popups()
        self._ocultar_sugerencias()
        if limpiar_seleccion:
            self._ui["seleccion"].clear()
            self._ui["confirmando_eliminar"] = False
        self._datos = [dict(f) for f in self._cargar_filas()]
        extras = self._al_recargar() if self._al_recargar else []
        self._redibujar()
        self.refrescar(
            *extras, self._contenedor_header, self._tabla_filas, self._capa_sugerencias, self._relleno, self._barra,
        )

    def alta_ok(self) -> None:
        """Un alta terminó bien: fila de alta nueva (vacía), datos recargados, foco en el primer campo."""
        self._contenedor_alta.content = self._construir_fila_alta()
        self.recargar()
        self.refrescar(self._contenedor_alta)
        self.enfocar(self._alta.foco if self._alta else None)

    @property
    def busqueda(self) -> str:
        return self._ui["busqueda"]

    def buscar(self, texto: str) -> None:
        self._ui["busqueda"] = texto
        self._redibujar()
        self.refrescar(self._contenedor_header, self._tabla_filas, self._barra)

    def refrescar(self, *controles: Optional[ft.Control]) -> None:
        refrescar(self._page, *controles)

    def enfocar(self, control: Optional[ft.Control]) -> None:
        """focus() es async en Flet 0.86.5: llamarlo sin await no hace nada."""
        if _montado(self._page, control):
            self._page.run_task(control.focus)

    def mostrar_ok(self, mensaje: str) -> None:
        mostrar_mensaje(self._page, mensaje)

    def mostrar_error(self, mensaje: str) -> None:
        mostrar_mensaje(self._page, mensaje, es_error=True)

    # --- Fila de alta: helpers para la pantalla ---

    def campo_filtrable_alta(
        self, clave: str, opciones: list[tuple[str, str]], on_seleccionar: Callable[[Optional[str]], None],
        **kwargs: Any,
    ) -> _CampoFiltrableFlotante:
        """CampoFiltrable para la celda `clave` del alta: sugerencias flotando debajo de la celda."""
        return self._campo_filtrable(
            self.pagina_alta, opciones, on_seleccionar, lambda: self._posicion_lista_alta(clave), **kwargs,
        )

    def campo_fecha_alta(
        self, valor: str, on_cambio: Callable[[], None], on_submit: Callable[[], None],
    ) -> tuple[ft.Control, ft.TextField]:
        """TextField AAAA-MM-DD + botón de calendario (DatePicker). Devuelve (control de la celda, TextField)."""
        campo = ft.TextField(
            value=valor, hint_text="AAAA-MM-DD", expand=True, text_align=ft.TextAlign.CENTER, **estilo_campo(),
        )

        def _cambio(e=None) -> None:
            on_cambio()
            sin_auto_update()  # el texto ya está en pantalla

        def _abrir_calendario(e=None) -> None:
            try:
                actual = datetime.strptime((campo.value or "").strip(), "%Y-%m-%d")
            except ValueError:
                actual = datetime.now()

            def _elegida(ev: ft.ControlEvent) -> None:
                elegida = selector.value
                if elegida is None:
                    sin_auto_update()
                    return
                campo.value = elegida[:10] if isinstance(elegida, str) else elegida.strftime("%Y-%m-%d")
                on_cambio()
                self.refrescar(campo)

            selector = ft.DatePicker(value=actual, on_change=_elegida)
            self._page.show_dialog(selector)

        campo.on_change = _cambio
        campo.on_submit = lambda e: on_submit()
        control = ft.Row(
            [
                campo,
                ft.IconButton(
                    icon=ft.Icons.CALENDAR_MONTH_OUTLINED, icon_size=ICONO_CALENDARIO,
                    icon_color=TEXT_SECONDARY, width=ANCHO_BOTON_CALENDARIO,
                    tooltip="ELEGIR FECHA", on_click=_abrir_calendario,
                ),
            ],
            spacing=0,
        )
        return control, campo

    def boton_confirmar_alta(self, tooltip: str, on_click: Callable[[], None]) -> ft.IconButton:
        return ft.IconButton(icon=ft.Icons.CHECK, icon_color=TEXT_ACCENT, tooltip=tooltip, on_click=lambda e: on_click())

    def tab_a_confirmar(self, control: ft.Control) -> None:
        """Con el foco en `control` (último campo del alta), Tab lleva el foco al ✓ de la fila (ahí Enter confirma)."""
        foco = {"activo": False}

        def _foco(activo: bool) -> None:
            foco["activo"] = activo
            sin_auto_update()

        control.on_focus = lambda e: _foco(True)
        control.on_blur = lambda e: _foco(False)
        self._foco_tab = foco

    # --- Filas: helpers de celda para construir_celdas() ---

    def celda_lectura(self, clave: str, contenido: ft.Control) -> ft.Container:
        """Celda de solo lectura (mismo alto y alineación que las editables)."""
        celda = self._contenedor_celda(clave)
        celda.content = ft.Container(
            content=contenido, alignment=self._por_clave[clave].alineacion,
            padding=ft.Padding.symmetric(horizontal=LayoutTokens.PADDING_CELDA),
        )
        return celda

    def celda_texto(
        self, fila: dict, clave: str, texto: str, on_guardar: Callable[[str], Optional[str]],
        valor_inicial: Optional[str] = None, color: str = TEXT_PRIMARY,
    ) -> ft.Container:
        celda = self._contenedor_celda(clave)
        inicial = texto if valor_inicial is None else valor_inicial
        edicion = {"activa": False}

        def _mostrar(actualizar: bool = True) -> None:
            edicion["activa"] = False
            self._lectura(celda, clave, texto_celda(texto, color=color), _editar)
            if actualizar:
                self.refrescar(celda)

        def _editar() -> None:
            campo = ft.TextField(
                value=inicial, autofocus=True, expand=True, text_align=ft.TextAlign.CENTER, **estilo_campo(),
            )
            boton = _boton_confirmar_celda()

            def _confirmar(e=None) -> None:
                # Enter, ✓ y blur pasan todos por acá: el primero gana.
                if not edicion["activa"]:
                    return
                nuevo = campo.value or ""
                if nuevo == inicial:
                    _mostrar()
                    return
                edicion["activa"] = False
                campo.disabled = True
                boton.disabled = True
                self.refrescar(celda)
                try:
                    mensaje = on_guardar(nuevo)
                except self._errores as err:
                    self.mostrar_error(str(err))
                    _mostrar()
                    return
                self._guardado_ok(fila, mensaje)

            campo.on_submit = _confirmar
            campo.on_blur = _confirmar
            boton.on_click = _confirmar
            edicion["activa"] = True
            celda.border = ft.Border.all(ANCHO_BORDE, TEXT_ACCENT)
            celda.content = ft.Row([campo, boton] if self._con_boton(clave) else [campo], spacing=0)
            self.refrescar(celda)
            self.enfocar(campo)

        _mostrar(actualizar=False)
        return celda

    def celda_filtrable(
        self, fila: dict, clave: str, contenido_lectura: Callable[[], ft.Control],
        opciones: list[tuple[str, str]], valor_inicial: str, on_guardar: Callable[[str], Optional[str]],
    ) -> ft.Container:
        celda = self._contenedor_celda(clave)
        edicion = {"activa": False}

        def _mostrar(actualizar: bool = True) -> None:
            edicion["activa"] = False
            self._lectura(celda, clave, contenido_lectura(), _editar)
            if actualizar:
                self.refrescar(celda)

        def _posicion() -> tuple[float, float, float]:
            """Sugerencias justo debajo de esta fila (índice actual en pantalla)."""
            indice = self._mostradas.index(fila["id"]) if fila["id"] in self._mostradas else 0
            return (
                ANCHO_COL_CHECK + self._x_rel(clave) + LayoutTokens.PADDING_CELDA,
                ALTURA_HEADER + ALTURA_FILA_ALTA + (indice + 1) * ALTURA_FILA,
                self._px(clave) - 2 * LayoutTokens.PADDING_CELDA,
            )

        def _editar() -> None:
            def _confirmar(id_seleccionado: Optional[str]) -> None:
                if id_seleccionado is None or not edicion["activa"]:
                    return
                if id_seleccionado == valor_inicial:
                    _mostrar()
                    return
                edicion["activa"] = False
                campo.campo_texto.disabled = True
                self.refrescar(celda)
                try:
                    mensaje = on_guardar(id_seleccionado)
                except self._errores as err:
                    self.mostrar_error(str(err))
                    _mostrar()
                    return
                self._guardado_ok(fila, mensaje)

            def _al_salir() -> None:
                # Blur sin elegir nada nuevo: vuelve a modo lectura.
                if edicion["activa"]:
                    _mostrar()

            pagina_celda = _ActualizacionLocal(self._page, lambda: [celda, self._capa_sugerencias, self._relleno])
            campo = self._campo_filtrable(
                pagina_celda, opciones, _confirmar, _posicion, al_salir=_al_salir,
                valor_inicial_id=valor_inicial, autofocus=True,
            )
            edicion["activa"] = True
            celda.border = ft.Border.all(ANCHO_BORDE, TEXT_ACCENT)
            celda.content = campo.control
            self.refrescar(celda)
            self.enfocar(campo.campo_texto)

        _mostrar(actualizar=False)
        return celda

    def celda_monto(
        self, fila: dict, clave: str, texto: str, color: str, valor_minor: int, decimales: int,
        on_guardar: Callable[[int], Optional[str]],
    ) -> ft.Container:
        """CampoMonto con persistir_formula=True (CLAUDE.md §9). on_guardar recibe minor units."""
        celda = self._contenedor_celda(clave)
        edicion = {"activa": False}

        def _mostrar(actualizar: bool = True) -> None:
            edicion["activa"] = False
            self._lectura(
                celda, clave,
                texto_celda(texto, color=color, size=TypographyTokens.REGISTRO_FONT_MONTO, weight=PESO_MONTO),
                _editar,
            )
            if actualizar:
                self.refrescar(celda)

        def _editar() -> None:
            def _confirmar(monto_minor: int) -> None:
                if not edicion["activa"] or monto_minor == valor_minor:
                    return
                edicion["activa"] = False
                campo.control.disabled = True
                boton.disabled = True
                self.refrescar(celda)
                try:
                    mensaje = on_guardar(monto_minor)
                except self._errores as err:
                    self.mostrar_error(str(err))
                    _mostrar()
                    raise  # CampoMonto revierte su texto
                self._guardado_ok(fila, mensaje)

            def _enter_sin_cambios() -> None:
                # on_avanzar corre tras un Enter válido; si hubo guardado la
                # fila ya se reconstruyó (edicion["activa"] quedó en False).
                if edicion["activa"]:
                    _mostrar()

            pagina_celda = _ActualizacionLocal(self._page, lambda: [celda])
            campo = CampoMonto(
                pagina_celda, on_confirmar=_confirmar, decimales=decimales, persistir_formula=True,
                valor_inicial_minor=valor_minor,
                dense=LayoutTokens.CELDA_DENSE, text_size=TypographyTokens.REGISTRO_FONT_MONTO, autofocus=True,
                on_avanzar=_enter_sin_cambios,
            )
            sin_borde(campo.control)
            campo.control.expand = True
            campo.control.text_align = ft.TextAlign.RIGHT
            boton = _boton_confirmar_celda(on_click=lambda e: campo.confirmar())

            # Blur: CampoMonto confirma primero (si cambió); si no hubo nada
            # que guardar, la celda vuelve a modo lectura.
            blur_de_campo_monto = campo.control.on_blur

            def _blur(e: ft.ControlEvent) -> None:
                blur_de_campo_monto(e)
                if edicion["activa"]:
                    _mostrar()

            campo.control.on_blur = _blur
            edicion["activa"] = True
            celda.border = ft.Border.all(ANCHO_BORDE, TEXT_ACCENT)
            celda.content = ft.Row([campo.control, boton] if self._con_boton(clave) else [campo.control], spacing=0)
            self.refrescar(celda)
            self.enfocar(campo.control)

        _mostrar(actualizar=False)
        return celda

    def celda_dropdown(
        self, fila: dict, clave: str, texto: str, opciones: list[tuple[str, str]], valor_inicial: str,
        on_guardar: Callable[[str], Optional[str]], color: str = TEXT_PRIMARY,
    ) -> ft.Container:
        """
        ft.Dropdown simple (CLAUDE.md §10, "dropdowns simples (moneda)"):
        guarda al elegir una opción distinta; elegir la misma vuelve a modo
        lectura. opciones: [(clave, texto), ...]. No sale de edición en
        on_blur (a diferencia de las otras celdas): abrir el menú del
        Dropdown y pasar el mouse por sus opciones puede sacarle el foco,
        y cerrarlo ahí haría imposible elegir — mismo criterio que ya tenía
        la edición de Moneda de Compras en cuotas.
        """
        celda = self._contenedor_celda(clave)
        edicion = {"activa": False}

        def _mostrar(actualizar: bool = True) -> None:
            edicion["activa"] = False
            self._lectura(celda, clave, texto_celda(texto, color=color), _editar)
            if actualizar:
                self.refrescar(celda)

        def _editar() -> None:
            desplegable = ft.Dropdown(
                value=valor_inicial,
                options=[ft.dropdown.Option(key=k, text=t) for k, t in opciones],
                dense=LayoutTokens.CELDA_DENSE, text_size=TypographyTokens.REGISTRO_FONT_CELDA,
                border=ft.InputBorder.NONE, text_align=ft.TextAlign.CENTER, expand=True, autofocus=True,
            )

            def _confirmar(e=None) -> None:
                if not edicion["activa"]:
                    return
                if not desplegable.value or desplegable.value == valor_inicial:
                    _mostrar()
                    return
                edicion["activa"] = False
                desplegable.disabled = True
                self.refrescar(celda)
                try:
                    mensaje = on_guardar(desplegable.value)
                except self._errores as err:
                    self.mostrar_error(str(err))
                    _mostrar()
                    return
                self._guardado_ok(fila, mensaje)

            desplegable.on_select = _confirmar
            edicion["activa"] = True
            celda.border = ft.Border.all(ANCHO_BORDE, TEXT_ACCENT)
            celda.content = ft.Row([desplegable], spacing=0)
            self.refrescar(celda)
            self.enfocar(desplegable)

        _mostrar(actualizar=False)
        return celda

    # ------------------------------------------------------------
    # ANCHOS DE COLUMNA (ver docstring, "Columnas")
    # ------------------------------------------------------------

    def _redimensionables(self) -> list[str]:
        return [c.clave for c in self._columnas if c.redimensionable]

    def _suma_anchos(self) -> float:
        return sum(self._ui["anchos"][c] for c in self._redimensionables())

    def _escala(self) -> float:
        """px por unidad de ui["anchos"] (1 hasta que on_size_change informe el ancho real)."""
        util, suma = self._ui["ancho_util"], self._suma_anchos()
        return util / suma if util and suma else 1.0

    def _px(self, clave: str) -> float:
        columna = self._por_clave[clave]
        return self._ui["anchos"][clave] * self._escala() if columna.redimensionable else float(columna.ancho)

    def _x_rel(self, clave: str) -> float:
        """px desde el inicio de las columnas de datos (después del checkbox) hasta el borde izquierdo de `clave`."""
        x = 0.0
        for c in self._claves:
            if c == clave:
                break
            x += self._px(c)
        return x

    def _minimo(self, clave: str) -> float:
        """Columna.ancho_min pasado a unidades de ui["anchos"]."""
        return self._por_clave[clave].ancho_min / self._escala()

    def _flex(self, clave: str) -> int:
        return max(1, round(self._ui["anchos"][clave] * FACTOR_FLEX))

    def _vecina(self, clave: str) -> Optional[str]:
        """La columna de la derecha, si ambas son redimensionables (la que absorbe el drag)."""
        indice = self._claves.index(clave)
        if not self._por_clave[clave].redimensionable or indice + 1 >= len(self._claves):
            return None
        siguiente = self._claves[indice + 1]
        return siguiente if self._por_clave[siguiente].redimensionable else None

    def _ancho_handle(self, clave: str) -> int:
        return ANCHO_RESIZE_HANDLE if self._vecina(clave) else 0

    def _con_boton(self, clave: str) -> bool:
        """¿La celda en edición lleva ✓? No en columnas angostas (ver ANCHO_MINIMO_CON_BOTON)."""
        return self._px(clave) >= ANCHO_MINIMO_CON_BOTON

    def _dimensionar(self, control: ft.Control, clave: str) -> None:
        """expand (redimensionable) o width fijo (resto)."""
        if self._por_clave[clave].redimensionable:
            control.expand = self._flex(clave)
        else:
            control.width = self._por_clave[clave].ancho

    def _aplicar_anchos(self, celdas: dict[str, ft.Control]) -> None:
        for clave, celda in celdas.items():
            if self._por_clave[clave].redimensionable:
                celda.expand = self._flex(clave)

    def _guardar_anchos(self) -> None:
        escribir_pref(self._pref_anchos, {c: round(v, 2) for c, v in self._ui["anchos"].items()})

    def _anchos_cambiaron(self) -> None:
        """Aplica ui["anchos"] a encabezado, alta y todas las filas; guarda y parchea."""
        self._aplicar_anchos(self._celdas_header)
        self._aplicar_anchos(self._celdas_alta)
        for ref in self._cache.values():
            self._aplicar_anchos(ref["celdas"])
        self._guardar_anchos()
        self.refrescar(self._contenedor_header, self._contenedor_alta, self._tabla_filas)

    def _redimensionar(self, clave: str, delta_px: float) -> None:
        """Drag del borde derecho de `clave`: la vecina de la derecha absorbe o cede el diferencial."""
        vecina = self._vecina(clave)
        if vecina is None:
            return
        anchos = self._ui["anchos"]
        delta = delta_px / self._escala()
        # Ninguna de las dos baja de su mínimo; si una ya está por debajo
        # (ventana muy angosta), solo puede crecer.
        delta = max(delta, min(0.0, self._minimo(clave) - anchos[clave]))
        delta = min(delta, max(0.0, anchos[vecina] - self._minimo(vecina)))
        if not delta:
            return
        anchos[clave] += delta
        anchos[vecina] -= delta
        for celdas in (self._celdas_header, self._celdas_alta):
            for c in (clave, vecina):
                if c in celdas:
                    celdas[c].expand = self._flex(c)
        self.refrescar(self._contenedor_header, self._contenedor_alta)

    def _repartir(self, proporciones: dict[str, float], total: float) -> dict[str, float]:
        """Escala `proporciones` para que sumen `total` sin dejar ninguna columna por debajo de su mínimo."""
        resultado: dict[str, float] = {}
        libres = dict(proporciones)
        restante = total
        while libres:
            suma = sum(libres.values())
            if restante <= 0 or suma <= 0:
                resultado.update({c: self._minimo(c) for c in libres})
                break
            escaladas = {c: v * restante / suma for c, v in libres.items()}
            debajo = [c for c, v in escaladas.items() if v < self._minimo(c)]
            if not debajo:
                resultado.update(escaladas)
                break
            for c in debajo:
                resultado[c] = self._minimo(c)
                restante -= self._minimo(c)
                del libres[c]
        return resultado

    def _ancho_contenido_px(self, clave: str) -> float:
        columna = self._por_clave[clave]
        textos = [self._valor_columna(f, clave) for f in self._filas_visibles()] + [columna.titulo]
        return max(len(t) for t in textos) * ANCHO_POR_CARACTER + PADDING_AJUSTE + columna.extra_ajuste

    def _ajustar_al_contenido(self, clave: str) -> None:
        total = self._suma_anchos()
        otras = [c for c in self._redimensionables() if c != clave]
        maximo = total - sum(self._minimo(c) for c in otras)
        objetivo = max(self._minimo(clave), min(self._ancho_contenido_px(clave) / self._escala(), maximo))
        self._ui["anchos"].update(self._repartir({c: self._ui["anchos"][c] for c in otras}, total - objetivo))
        self._ui["anchos"][clave] = objetivo
        self._anchos_cambiaron()

    def _ajustar_todas(self) -> None:
        self._ui["anchos"].update(
            self._repartir({c: self._ancho_contenido_px(c) for c in self._redimensionables()}, self._suma_anchos())
        )
        self._anchos_cambiaron()

    def _on_tamanio_tabla(self, e: ft.LayoutSizeChangeEvent) -> None:
        # Solo se anota (el flex ya llena el ancho solo) — sin auto-update.
        sin_auto_update()
        fijas = sum(c.ancho for c in self._columnas if not c.redimensionable)
        self._ui["ancho_util"] = max(1.0, e.width - 2 * ANCHO_BORDE - ANCHO_COL_CHECK - ANCHO_COL_ACCION - fijas)

    def _handle_resize(self, clave: str) -> ft.Control:
        linea = ft.Container(width=ANCHO_LINEA_RESIZE, height=ALTURA_HEADER, bgcolor=TEXT_ACCENT, visible=False)
        arrastrando = {"activo": False}

        def _ver_linea(visible: bool) -> None:
            linea.visible = visible or arrastrando["activo"]
            self.refrescar(linea)

        def _arrastrar(e: ft.DragUpdateEvent) -> None:
            sin_auto_update()
            arrastrando["activo"] = True
            linea.visible = True
            delta = e.primary_delta
            if delta is None:
                delta = e.local_delta.x if e.local_delta else 0
            self._redimensionar(clave, delta)

        def _soltar(e=None) -> None:
            arrastrando["activo"] = False
            linea.visible = False
            # Recién ahora las filas de datos: ver docstring, "Columnas".
            for ref in self._cache.values():
                self._aplicar_anchos(ref["celdas"])
            self._guardar_anchos()
            self.refrescar(linea, self._tabla_filas)

        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.RESIZE_COLUMN,
            drag_interval=RESIZE_DRAG_INTERVAL_MS,
            on_enter=lambda e: _ver_linea(True),
            on_exit=lambda e: _ver_linea(False),
            on_horizontal_drag_update=_arrastrar,
            on_horizontal_drag_end=_soltar,
            content=ft.Container(
                width=ANCHO_RESIZE_HANDLE, height=ALTURA_HEADER, alignment=ft.Alignment.CENTER_RIGHT, content=linea,
            ),
        )

    # ------------------------------------------------------------
    # POPUPS (host en page.overlay — ver docstring, "Popups")
    # ------------------------------------------------------------

    def _cerrar_popups(self) -> None:
        if self._ui["popup"] is None:
            return
        self._ui["popup"] = None
        self._host_popups.visible = False
        self._host_popups.content = None
        self.refrescar(self._host_popups)

    def _mostrar_popup(self, popup: ft.Control, tipo: str, clave: str) -> None:
        capa = ft.GestureDetector(
            left=0, top=0, right=0, bottom=0,
            on_tap_down=self._click_en_capa,
            on_secondary_tap_down=self._click_derecho_en_capa,
            content=ft.Container(bgcolor=COLOR_CAPA),
        )
        self._ui["popup"] = {"tipo": tipo, "columna": clave}
        self._host_popups.content = ft.Stack([capa, popup])
        self._host_popups.visible = True
        self.refrescar(self._host_popups)

    def _posicion_popup(self, e: ft.TapEvent, ancho_popup: int) -> tuple[float, float]:
        x = e.global_position.x if e.global_position else MARGEN_POPUP
        y = e.global_position.y if e.global_position else MARGEN_POPUP
        ancho_ventana = getattr(self._page, "width", None)
        if ancho_ventana and x + ancho_popup + MARGEN_POPUP > ancho_ventana:
            x = max(MARGEN_POPUP, ancho_ventana - ancho_popup - MARGEN_POPUP)
        return x, y + MARGEN_POPUP

    def _anclar_en_celda(self, clave: str, e: ft.TapEvent) -> None:
        """Click derecho en la celda del encabezado: local_position es relativa a la celda."""
        if e.global_position is None or e.local_position is None:
            return
        izquierda_celda = e.global_position.x - e.local_position.x
        self._ui["ancla_header"] = (izquierda_celda - self._x_rel(clave), e.global_position.y - e.local_position.y)

    def _anclar_en_icono_filtro(self, clave: str, e: ft.TapEvent) -> None:
        """Click en ▼: local_position es relativa al ícono, pegado al borde derecho de la celda (antes del handle)."""
        if e.global_position is None or e.local_position is None:
            return
        fin_celda = e.global_position.x - e.local_position.x + ICONO_HEADER + self._ancho_handle(clave) + ANCHO_BORDE
        arriba_icono = e.global_position.y - e.local_position.y
        self._ui["ancla_header"] = (
            fin_celda - self._px(clave) - self._x_rel(clave),
            arriba_icono - (ALTURA_HEADER - ICONO_HEADER) / 2,
        )

    def _columna_bajo_puntero(self, posicion: Optional[ft.Offset], zona_filtro: bool) -> Optional[str]:
        """Columna del encabezado bajo `posicion` (en pantalla); con zona_filtro, solo si cae sobre su ▼."""
        ancla = self._ui["ancla_header"]
        if posicion is None or ancla is None or self._ui["ancho_util"] is None:
            return None
        x_inicio, y_header = ancla
        if not (y_header <= posicion.y <= y_header + ALTURA_HEADER):
            return None
        for clave in self._claves:
            x_fin = x_inicio + self._px(clave)
            if x_inicio <= posicion.x < x_fin:
                if not zona_filtro:
                    return clave
                borde_icono = x_fin - ANCHO_BORDE - self._ancho_handle(clave)
                return clave if borde_icono - ZONA_ICONO_FILTRO <= posicion.x <= borde_icono else None
            x_inicio = x_fin
        return None

    def _click_en_capa(self, e: ft.TapEvent) -> None:
        anterior = self._ui["popup"]
        self._cerrar_popups()
        clave = self._columna_bajo_puntero(e.global_position, zona_filtro=True)
        # Click en el ▼ del mismo filtro abierto = solo cerrarlo.
        if clave is None or (anterior and anterior["tipo"] == "filtro" and anterior["columna"] == clave):
            return
        x, y = self._posicion_popup(e, ANCHO_OVERLAY_FILTRO)
        self._abrir_filtro(clave, x, y)

    def _click_derecho_en_capa(self, e: ft.TapEvent) -> None:
        self._cerrar_popups()
        clave = self._columna_bajo_puntero(e.global_position, zona_filtro=False)
        if clave is not None:
            self._abrir_menu_columna(clave, e)

    # --- Menú contextual del encabezado ---

    def _item_menu(self, icono: str, texto: str, on_click: Callable[[], None], habilitado: bool = True) -> ft.Control:
        item = ft.Container(
            height=ALTURA_ITEM_MENU,
            padding=ft.Padding.symmetric(horizontal=PADDING_ITEM_MENU_H),
            alignment=ft.Alignment.CENTER_LEFT,
            opacity=1.0 if habilitado else OPACIDAD_DESHABILITADO,
            content=ft.Row(
                [
                    ft.Icon(icono, size=ICONO_MENU, color=TEXT_SECONDARY),
                    ft.Text(texto, size=TypographyTokens.REGISTRO_FONT_OVERLAY, color=TEXT_PRIMARY),
                ],
                spacing=ESPACIADO,
            ),
        )
        if habilitado:
            def _hover(e: ft.ControlEvent) -> None:
                item.bgcolor = BG_ITEM_HOVER if str(e.data).lower() == "true" else None
                self.refrescar(item)

            def _click(e=None) -> None:
                self._cerrar_popups()
                on_click()

            item.on_hover = _hover
            item.on_click = _click
        return item

    def _separador_menu(self) -> ft.Control:
        return ft.Container(
            height=ANCHO_BORDE, bgcolor=BORDER_DEFAULT, margin=ft.Margin.symmetric(vertical=PADDING_MENU_V),
        )

    def _ordenar(self, clave: str, ascendente: bool) -> None:
        self._ui["orden"] = (clave, ascendente)
        self._redibujar()
        self.refrescar(self._contenedor_header, self._tabla_filas, self._barra)

    def _abrir_menu_columna(self, clave: str, e: ft.TapEvent) -> None:
        x, y = self._posicion_popup(e, ANCHO_MENU_CTX)
        menu = ft.Container(
            left=x, top=y, width=ANCHO_MENU_CTX,
            bgcolor=BG_MENU_CTX,
            border=ft.Border.all(ANCHO_BORDE, BORDER_OVERLAY),
            border_radius=RADIO_OVERLAY,
            padding=ft.Padding.symmetric(vertical=PADDING_MENU_V),
            content=ft.Column(
                [
                    self._item_menu(
                        ft.Icons.WIDTH_NORMAL, "AJUSTAR AL CONTENIDO", lambda: self._ajustar_al_contenido(clave),
                        habilitado=self._por_clave[clave].redimensionable,
                    ),
                    self._item_menu(ft.Icons.VIEW_COLUMN_OUTLINED, "AJUSTAR TODAS LAS COLUMNAS", self._ajustar_todas),
                    self._separador_menu(),
                    self._item_menu(ft.Icons.ARROW_UPWARD, "ORDENAR A → Z", lambda: self._ordenar(clave, True)),
                    self._item_menu(ft.Icons.ARROW_DOWNWARD, "ORDENAR Z → A", lambda: self._ordenar(clave, False)),
                    self._separador_menu(),
                    self._item_menu(ft.Icons.FILTER_ALT_OUTLINED, "FILTRAR...", lambda: self._abrir_filtro(clave, x, y)),
                ],
                spacing=0,
                tight=True,
            ),
        )
        self._mostrar_popup(menu, "menu", clave)

    # --- Overlay de filtro por columna ---

    def _abrir_filtro(self, clave: str, x: float, y: float) -> None:
        valores = sorted({self._valor_columna(f, clave) for f in self._datos}, key=str.lower)
        activos = self._ui["filtros"].get(clave)
        marcados = set(activos) if activos is not None else set(valores)
        texto_busqueda = {"valor": ""}
        lista = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO)

        def _toggle(valor: str, marcado: bool) -> None:
            # El checkbox ya cambió del lado del cliente: nada que parchear.
            sin_auto_update()
            if marcado:
                marcados.add(valor)
            else:
                marcados.discard(valor)

        def _dibujar_lista() -> None:
            texto = texto_busqueda["valor"].lower()
            lista.controls = [
                ft.Container(
                    height=ALTURA_ITEM_FILTRO,
                    content=ft.Checkbox(
                        label=valor or "(VACÍO)",
                        value=valor in marcados,
                        label_style=ft.TextStyle(size=TypographyTokens.REGISTRO_FONT_OVERLAY, color=TEXT_PRIMARY),
                        active_color=TEXT_ACCENT,
                        on_change=lambda e, v=valor: _toggle(v, bool(e.control.value)),
                    ),
                )
                for valor in valores if texto in valor.lower()
            ]

        def _buscar(e: ft.ControlEvent) -> None:
            texto_busqueda["valor"] = e.control.value or ""
            _dibujar_lista()
            self.refrescar(lista)

        def _aplicar(e=None) -> None:
            if marcados >= set(valores):
                self._ui["filtros"].pop(clave, None)
            else:
                self._ui["filtros"][clave] = set(marcados)
            self._cerrar_popups()
            self._redibujar()
            self.refrescar(self._contenedor_header, self._tabla_filas, self._barra)

        def _limpiar(e=None) -> None:
            self._ui["filtros"].pop(clave, None)
            self._cerrar_popups()
            self._redibujar()
            self.refrescar(self._contenedor_header, self._tabla_filas, self._barra)

        _dibujar_lista()
        alto_lista = min(len(valores) * ALTURA_ITEM_FILTRO, ALTO_MAX_OVERLAY_FILTRO - ALTURA_CONTROLES_FILTRO)
        overlay = ft.Container(
            left=x, top=y, width=ANCHO_OVERLAY_FILTRO,
            bgcolor=BG_OVERLAY,
            border=ft.Border.all(ANCHO_BORDE, BORDER_OVERLAY),
            border_radius=RADIO_OVERLAY,
            padding=PADDING_OVERLAY,
            content=ft.Column(
                [
                    ft.TextField(hint_text="BUSCAR…", autofocus=True, on_change=_buscar, **estilo_campo()),
                    ft.Container(height=alto_lista, content=lista),
                    ft.Row(
                        [
                            _boton_texto("LIMPIAR", _limpiar, relleno=None),
                            _boton_texto("APLICAR", _aplicar, relleno=BTN_COMPARTIR),
                        ],
                        alignment=ft.MainAxisAlignment.END,
                        spacing=ESPACIADO,
                    ),
                ],
                spacing=ESPACIADO,
                tight=True,
            ),
        )
        self._mostrar_popup(overlay, "filtro", clave)

    # ------------------------------------------------------------
    # SUGERENCIAS FLOTANTES DE CampoFiltrable (ver docstring, "Fila de alta")
    # ------------------------------------------------------------

    def _alto_cuerpo(self) -> float:
        filas = len(self._mostradas)
        return ALTURA_HEADER + ALTURA_FILA_ALTA + (filas * ALTURA_FILA if filas else ALTURA_VACIO)

    def _mostrar_sugerencias(self, duenio: Any, lista: ft.Container, x: float, y: float, ancho: float) -> None:
        self._sugerencias_duenio = duenio
        self._capa_sugerencias.left = x
        self._capa_sugerencias.top = y
        self._capa_sugerencias.width = max(ancho, ANCHO_MINIMO_SUGERENCIAS)
        self._capa_sugerencias.content = lista
        self._capa_sugerencias.visible = True
        # La tabla se estira lo justo para que la lista quede entera adentro del Stack.
        self._relleno.height = max(0.0, y + (lista.height or ALTURA_MAX_LISTA) - self._alto_cuerpo())

    def _ocultar_sugerencias(self, duenio: Any = None) -> None:
        """Sin `duenio` cierra la lista abierta, sea de quien sea."""
        if duenio is not None and self._sugerencias_duenio is not duenio:
            return
        self._sugerencias_duenio = None
        self._capa_sugerencias.visible = False
        self._capa_sugerencias.content = None
        self._relleno.height = 0

    def _campo_filtrable(
        self,
        pagina: _ActualizacionLocal,
        opciones: list[tuple[str, str]],
        on_seleccionar: Callable[[Optional[str]], None],
        posicion: Callable[[], tuple[float, float, float]],
        al_salir: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ) -> _CampoFiltrableFlotante:
        """CampoFiltrable con sugerencias en capa_sugerencias, en (x, y, ancho) = posicion()."""
        campo = _CampoFiltrableFlotante(
            pagina, opciones, on_seleccionar,
            al_mostrar_lista=lambda duenio, lista: self._mostrar_sugerencias(duenio, lista, *posicion()),
            al_ocultar_lista=self._ocultar_sugerencias,
            al_salir=al_salir,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            **kwargs,
        )
        sin_borde(campo.campo_texto)
        campo.campo_texto.text_align = ft.TextAlign.CENTER
        return campo

    def _posicion_lista_alta(self, clave: str) -> tuple[float, float, float]:
        """(x, y, ancho) de las sugerencias de una celda del alta, relativo a la tabla: justo debajo de la celda."""
        return (
            ANCHO_COL_CHECK + self._x_rel(clave) + ALTA_PADDING,
            ALTURA_HEADER + ALTA_PADDING + ALTURA_FILA,
            self._px(clave) - 2 * ALTA_PADDING,
        )

    # ------------------------------------------------------------
    # ENCABEZADOS
    # ------------------------------------------------------------

    def _on_checkbox_todas(self, e: ft.ControlEvent) -> None:
        if e.control.value:
            self._ui["seleccion"] = set(self._mostradas)
        else:
            self._ui["seleccion"].clear()
        self._ui["confirmando_eliminar"] = False
        self._redibujar()
        self.refrescar(self._contenedor_header, self._tabla_filas, self._barra)

    def _todas_seleccionadas(self) -> bool:
        return bool(self._mostradas) and all(i in self._ui["seleccion"] for i in self._mostradas)

    def _celda_header(self, columna: Columna) -> ft.Control:
        clave = columna.clave
        filtro_activo = clave in self._ui["filtros"]
        ordenada = self._ui["orden"] is not None and self._ui["orden"][0] == clave
        icono_filtro = ft.Icon(
            ft.Icons.FILTER_ALT if filtro_activo else ft.Icons.ARROW_DROP_DOWN,
            size=ICONO_HEADER,
            color=TEXT_ACCENT if filtro_activo else TEXT_MUTED,
        )

        def _hover_filtro(activo: bool) -> None:
            icono_filtro.color = TEXT_ACCENT if (activo or filtro_activo) else TEXT_MUTED
            self.refrescar(icono_filtro)

        def _click_filtro(e: ft.TapEvent) -> None:
            self._anclar_en_icono_filtro(clave, e)
            x, y = self._posicion_popup(e, ANCHO_OVERLAY_FILTRO)
            self._abrir_filtro(clave, x, y)

        def _click_derecho(e: ft.TapEvent) -> None:
            self._anclar_en_celda(clave, e)
            self._abrir_menu_columna(clave, e)

        partes: list[ft.Control] = [
            ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    columna.titulo, size=TypographyTokens.REGISTRO_FONT_HEADER, weight=PESO_HEADER,
                    color=TEXT_SECONDARY, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ),
        ]
        if ordenada:
            partes.append(
                ft.Icon(
                    ft.Icons.ARROW_UPWARD if self._ui["orden"][1] else ft.Icons.ARROW_DOWNWARD,
                    size=ICONO_MENU, color=TEXT_ACCENT,
                )
            )
        partes.append(
            ft.GestureDetector(
                mouse_cursor=ft.MouseCursor.CLICK,
                on_tap_down=_click_filtro,
                on_enter=lambda e: _hover_filtro(True),
                on_exit=lambda e: _hover_filtro(False),
                content=ft.Container(content=icono_filtro, tooltip="FILTRAR"),
            )
        )
        if self._vecina(clave):
            partes.append(self._handle_resize(clave))
        # El título se centra en la celda entera, no solo en el espacio que
        # le dejan los íconos: a la izquierda va el mismo ancho que ocupan
        # a la derecha (flecha de orden, ▼, handle y borde).
        ancho_iconos = (ICONO_MENU if ordenada else 0) + ICONO_HEADER + self._ancho_handle(clave) + ANCHO_BORDE

        celda = ft.GestureDetector(
            on_secondary_tap_down=_click_derecho,
            content=ft.Container(
                height=ALTURA_HEADER,
                padding=ft.Padding.only(left=ancho_iconos),
                border=ft.Border.only(right=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
                content=ft.Row(partes, spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ),
        )
        self._dimensionar(celda, clave)
        self._celdas_header[clave] = celda
        return celda

    def _dibujar_header(self) -> None:
        self._checkbox_todas.value = self._todas_seleccionadas()
        self._celdas_header.clear()
        self._contenedor_header.content = ft.Container(
            height=ALTURA_HEADER,
            bgcolor=BG_ENCABEZADO,
            border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
            content=ft.Row(
                [
                    ft.Container(
                        width=ANCHO_COL_CHECK, height=ALTURA_HEADER, alignment=ft.Alignment.CENTER,
                        border=ft.Border.only(right=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
                        content=self._checkbox_todas,
                    ),
                    *[self._celda_header(columna) for columna in self._columnas],
                    ft.Container(width=ANCHO_COL_ACCION),
                ],
                spacing=0,
            ),
        )

    # ------------------------------------------------------------
    # FILA DE ALTA
    # ------------------------------------------------------------

    def _celda_alta(self, clave: str, contenido: Optional[ft.Control]) -> ft.Container:
        """Alto fijo + recorte: todas las celdas del alta miden lo mismo (el borde lo da la celda)."""
        celda = ft.Container(
            height=ALTURA_FILA,
            padding=ft.Padding.symmetric(horizontal=ALTA_PADDING),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            content=ft.Container(
                border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
                border_radius=ALTA_RADIO_CAMPO,
                bgcolor=BG_SUPERFICIE,
                alignment=ft.Alignment.CENTER,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                content=contenido,
            ),
        )
        self._dimensionar(celda, clave)
        self._celdas_alta[clave] = celda
        return celda

    def _construir_fila_alta(self) -> ft.Control:
        self._celdas_alta.clear()
        self._foco_tab = None
        self._alta = self._construir_alta()
        return ft.Container(
            bgcolor=BG_FILA_ALTA,
            padding=ft.Padding.symmetric(vertical=ALTA_PADDING),
            border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=TEXT_ACCENT)),
            content=ft.Row(
                [
                    ft.Container(width=ANCHO_COL_CHECK),
                    *[self._celda_alta(clave, self._alta.celdas.get(clave)) for clave in self._claves],
                    # height fijo: el IconButton (mínimo 40 px en Material 3)
                    # no puede estirar la fila más allá de ALTURA_FILA.
                    ft.Container(
                        width=ANCHO_COL_ACCION, height=ALTURA_FILA, alignment=ft.Alignment.CENTER,
                        content=self._alta.boton,
                    ),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # --- Tab en el último campo del alta (tab_a_confirmar()) ---

    def _al_tecla(self, e: ft.KeyboardEvent) -> None:
        if not self._ui["visible"] or self._ui["popup"] is not None or self._foco_tab is None:
            return
        if e.shift or e.ctrl or e.alt or e.meta or e.key != TECLA_IR_A_CONFIRMAR:
            return
        if self._foco_tab["activo"] and self._alta is not None:
            self.enfocar(self._alta.boton)

    # ------------------------------------------------------------
    # FILAS
    # ------------------------------------------------------------

    def _contenedor_celda(self, clave: str) -> ft.Container:
        """Contenido alineado según la columna (centrado en los dos ejes; Monto, centrado vertical a la derecha)."""
        celda = ft.Container(
            height=ALTURA_FILA, alignment=self._por_clave[clave].alineacion,
            padding=ft.Padding.symmetric(horizontal=LayoutTokens.PADDING_CELDA),
        )
        self._dimensionar(celda, clave)
        return celda

    def _lectura(self, celda: ft.Container, clave: str, contenido: ft.Control, on_editar: Callable[[], None]) -> None:
        celda.border = None
        celda.content = ft.Container(
            content=contenido, on_click=lambda e: on_editar(), alignment=self._por_clave[clave].alineacion,
            padding=ft.Padding.symmetric(horizontal=LayoutTokens.PADDING_CELDA),
        )

    def _guardado_ok(self, fila: dict, mensaje: Optional[str]) -> None:
        self._cache.pop(fila["id"], None)
        self.recargar()
        if mensaje:
            self.mostrar_ok(mensaje)

    def _filas_visibles(self) -> list[dict]:
        texto = (self._ui["busqueda"] or "").strip().lower()
        filas = [f for f in self._datos if not texto or texto in (self._texto_busqueda(f) or "").lower()]
        for clave, valores in self._ui["filtros"].items():
            if clave in self._por_clave:
                filas = [f for f in filas if self._valor_columna(f, clave) in valores]
        if self._ui["orden"] is not None and self._ui["orden"][0] in self._por_clave:
            clave, ascendente = self._ui["orden"]
            filas.sort(key=lambda f: self._clave_orden(f, clave), reverse=not ascendente)
        return filas

    def _toggle_seleccion(self, fila: dict, seleccionada: bool) -> None:
        habia_seleccion = bool(self._ui["seleccion"])
        if seleccionada:
            self._ui["seleccion"].add(fila["id"])
        else:
            self._ui["seleccion"].discard(fila["id"])
        self._ui["confirmando_eliminar"] = False
        ref = self._cache[fila["id"]]
        ref["fila"].bgcolor = BG_FILA_SEL if seleccionada else ref["bg"]
        cambiados: list[ft.Control] = [ref["fila"]]
        if habia_seleccion != bool(self._ui["seleccion"]):
            # Se activó/desactivó el modo selección: checkboxes de todas las filas.
            for id_ in self._mostradas:
                if id_ in self._cache:
                    self._cache[id_]["checkbox"].visible = bool(self._ui["seleccion"])
            ref["checkbox"].visible = True  # el mouse sigue encima de esta
            cambiados = [self._tabla_filas]
        self._checkbox_todas.value = self._todas_seleccionadas()
        self._actualizar_barra()
        self.refrescar(*cambiados, self._checkbox_todas, self._barra)

    def _fila(self, fila: dict) -> dict:
        """Construye la fila y la deja en el cache."""
        ref: dict[str, Any] = {"id": fila["id"], "firma": self._firma(fila), "bg": BG_FILA_PAR}
        checkbox = ft.Checkbox(
            value=False, visible=False, active_color=TEXT_ACCENT,
            on_change=lambda e: self._toggle_seleccion(fila, bool(e.control.value)),
        )
        celdas = self._construir_celdas(fila)
        contenido = ft.Row(
            [
                ft.Container(width=ANCHO_COL_CHECK, height=ALTURA_FILA, alignment=ft.Alignment.CENTER, content=checkbox),
                *[celdas[clave] for clave in self._claves],
                ft.Container(
                    width=ANCHO_COL_ACCION, height=ALTURA_FILA, alignment=ft.Alignment.CENTER,
                    content=self._accion_fila(fila) if self._accion_fila else None,
                ),
            ],
            spacing=0,
        )
        if self._fila_atenuada and self._fila_atenuada(fila):
            contenido.opacity = OPACIDAD_FILA_ATENUADA
        control = ft.Container(
            height=ALTURA_FILA,
            border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
            content=contenido,
        )

        def _hover(e: ft.ControlEvent) -> None:
            activo = str(e.data).lower() == "true"  # normalizado defensivamente
            seleccionada = fila["id"] in self._ui["seleccion"]
            control.bgcolor = BG_FILA_SEL if seleccionada else (BG_FILA_HOVER if activo else ref["bg"])
            checkbox.visible = activo or bool(self._ui["seleccion"])
            self.refrescar(control)

        control.on_hover = _hover
        ref.update(fila=control, checkbox=checkbox, celdas=celdas)
        self._cache[fila["id"]] = ref
        return ref

    def _aplicar_estado_fila(self, ref: dict, indice: int, hay_seleccion: bool) -> None:
        """Lo que cambia sin reconstruir la fila: fondo alternado, selección, checkbox, anchos."""
        seleccionada = ref["id"] in self._ui["seleccion"]
        ref["bg"] = BG_FILA_PAR if indice % 2 == 0 else BG_FILA_IMPAR
        ref["fila"].bgcolor = BG_FILA_SEL if seleccionada else ref["bg"]
        ref["checkbox"].value = seleccionada
        ref["checkbox"].visible = hay_seleccion
        self._aplicar_anchos(ref["celdas"])

    def _redibujar(self) -> None:
        """Header + filas según filtros/orden/selección actuales (no recarga datos ni parchea)."""
        visibles = self._filas_visibles()
        # Nunca queda seleccionada una fila que el usuario no ve.
        ids_visibles = {f["id"] for f in visibles}
        if not self._ui["seleccion"] <= ids_visibles:
            self._ui["seleccion"] &= ids_visibles
            self._ui["confirmando_eliminar"] = False
        # Filas que ya no están cargadas (borradas, otro período): fuera del cache.
        ids_cargados = {f["id"] for f in self._datos}
        for id_ in [i for i in self._cache if i not in ids_cargados]:
            del self._cache[id_]

        hay_seleccion = bool(self._ui["seleccion"])
        controles: list[ft.Control] = []
        for indice, fila in enumerate(visibles):
            ref = self._cache.get(fila["id"])
            if ref is None or ref["firma"] != self._firma(fila):
                ref = self._fila(fila)
            self._aplicar_estado_fila(ref, indice, hay_seleccion)
            controles.append(ref["fila"])
        self._mostradas = [f["id"] for f in visibles]

        self._tabla_filas.controls = controles or [
            ft.Container(
                height=ALTURA_VACIO,
                alignment=ft.Alignment.CENTER_LEFT,
                padding=ft.Padding.symmetric(horizontal=PADDING_RESUMEN_H),
                content=ft.Text(
                    self._texto_vacio, italic=True, color=TEXT_MUTED, size=TypographyTokens.REGISTRO_FONT_CELDA,
                ),
            )
        ]
        self._dibujar_header()
        self._actualizar_barra()

    # ------------------------------------------------------------
    # BARRA FLOTANTE DE SELECCIÓN MÚLTIPLE (page.overlay)
    # ------------------------------------------------------------

    def _seleccionadas(self) -> list[dict]:
        return [f for f in self._datos if f["id"] in self._ui["seleccion"]]

    def _cancelar_seleccion(self, e=None) -> None:
        self._ui["seleccion"].clear()
        self._ui["confirmando_eliminar"] = False
        self._redibujar()
        self.refrescar(self._contenedor_header, self._tabla_filas, self._barra)

    def _boton_circular(self, icono: str, color: str, tooltip: str, on_click: Callable,
                        habilitado: bool = True) -> ft.Control:
        return ft.Container(
            width=DIAMETRO_BOTON_FLOT, height=DIAMETRO_BOTON_FLOT, border_radius=DIAMETRO_BOTON_FLOT / 2,
            bgcolor=color, alignment=ft.Alignment.CENTER, tooltip=tooltip,
            opacity=1.0 if habilitado else OPACIDAD_DESHABILITADO,
            on_click=on_click if habilitado else None,
            content=ft.Icon(icono, size=ICONO_BOTON_FLOT, color=TEXT_SOBRE_BOTON),
        )

    def _pedir_confirmacion(self, e=None) -> None:
        self._ui["confirmando_eliminar"] = True
        self._actualizar_barra()
        self.refrescar(self._barra)

    def _cancelar_confirmacion(self, e=None) -> None:
        self._ui["confirmando_eliminar"] = False
        self._actualizar_barra()
        self.refrescar(self._barra)

    def _eliminar_seleccion(self, e=None) -> None:
        mensaje, es_error = self._on_eliminar(self._seleccionadas())
        self.recargar(limpiar_seleccion=True)
        mostrar_mensaje(self._page, mensaje, es_error=es_error)

    def _compartir(self, e=None) -> None:
        seleccionadas = self._seleccionadas()
        if self._on_compartir is None or not seleccionadas:
            return
        self._on_compartir(seleccionadas)

    def _actualizar_barra(self) -> None:
        cantidad = len(self._ui["seleccion"])
        self._barra.visible = cantidad > 0 and self._ui["visible"]
        if cantidad == 0:
            return
        plural = "S" if cantidad != 1 else ""
        tamanio = TypographyTokens.REGISTRO_FONT_BARRA_FLOT
        controles: list[ft.Control] = [
            ft.IconButton(icon=ft.Icons.CLOSE, icon_color=TEXT_SECONDARY, tooltip="CANCELAR SELECCIÓN",
                          on_click=self._cancelar_seleccion),
            ft.Text(f"{cantidad} FILA{plural} SELECCIONADA{plural}", size=tamanio, weight=PESO_MONTO, color=TEXT_PRIMARY),
            ft.Container(width=ANCHO_BORDE, height=DIAMETRO_BOTON_FLOT, bgcolor=BORDER_BARRA),
            self._boton_circular(ft.Icons.DELETE_OUTLINE, BTN_ELIMINAR, "ELIMINAR", self._pedir_confirmacion),
            ft.Text("ELIMINAR", size=tamanio, color=TEXT_PRIMARY),
        ]
        if self._on_compartir is not None:
            controles += [
                self._boton_circular(
                    ft.Icons.PEOPLE, BTN_COMPARTIR,
                    "COMPARTIR" if cantidad == 1 else f"COMPARTIR LAS {cantidad} FILAS CON EL MISMO COEFICIENTE",
                    self._compartir,
                ),
                ft.Text("COMPARTIR", size=tamanio, color=TEXT_PRIMARY),
            ]
        if self._ui["confirmando_eliminar"]:
            avisos = self._avisos_eliminar(self._seleccionadas()) if self._avisos_eliminar else ""
            controles += [
                ft.Container(width=ANCHO_BORDE, height=DIAMETRO_BOTON_FLOT, bgcolor=BORDER_BARRA),
                ft.Text(
                    f"¿ELIMINAR {cantidad} FILA{plural}?" + (f"  ({avisos})" if avisos else ""),
                    size=tamanio, color=TEXT_PRIMARY, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                    tooltip=avisos or None, expand=True,
                ),
                _boton_texto("CONFIRMAR", self._eliminar_seleccion, relleno=BTN_ELIMINAR),
                _boton_texto("CANCELAR", self._cancelar_confirmacion, relleno=None),
            ]
        else:
            controles.append(ft.Container(expand=True))
        controles.append(
            ft.TextButton(
                content=ft.Text("CANCELAR SELECCIÓN", size=tamanio, color=TEXT_MUTED),
                on_click=self._cancelar_seleccion,
            )
        )
        self._barra.content = ft.Row(controles, spacing=ESPACIO_BARRA, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ------------------------------------------------------------
    # HOOKS DE NAVEGACIÓN (ui/app.py)
    # ------------------------------------------------------------

    def _al_ocultar(self) -> None:
        self._ui["visible"] = False
        self._cerrar_popups()
        self._barra.visible = False

    def _al_mostrar(self) -> None:
        self._ui["visible"] = True
        self._actualizar_barra()
