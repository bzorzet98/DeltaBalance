"""
DeltaBalance — ui/components/tabla_planilla.py

Tabla estilo planilla (tema oscuro) compartida por el Registro de
transacciones (ui/components/registro_transacciones.py), Compras en cuotas
(ui/screens/compras_cuotas.py), Gastos compartidos
(ui/screens/gastos_compartidos.py) y Deudas (ui/screens/deudas.py):
encabezados filtrables/ordenables/redimensionables, fila de alta pegada al
encabezado, filas con edición inline, selección múltiple con barra
flotante (eliminar / compartir / acciones propias de cada pantalla). Más
las piezas de cabecera que usan todas, para que se vean idénticas:
barra_titulo() (título + buscador + selector de mes), barra_resumen() (la
barra "saldo por cuenta") y pantalla_planilla() (el fondo y márgenes de la
pantalla). Paleta en ui/theme/tabla_tokens.py.

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

Arrastrar el borde derecho de cualquier columna (salvo la última) le pasa
el diferencial a la columna inmediatamente a la derecha (estilo Google
Sheets), respetando el mínimo de las dos: el ancho total no cambia. La
vecina puede ser redimensionable o fija: "fija" quiere decir que no se
estira con la ventana, no que no se pueda arrastrar — su ancho en px vive
en ui["fijos"] y se guarda en prefs junto a las proporciones. Si en el
borde hay una fija, se trabaja en px: la izquierda crece exactamente lo
que la vecina cede y el espacio flexible (ui["ancho_util"]) cambia lo
mismo en sentido contrario. Mínimo: Columna.ancho_min (default
ANCHO_MIN_COLUMNA, o ANCHO_MIN_COLUMNA_FIJA para una fija); una fija nunca
exige más que su ancho de diseño. Durante el drag se actualizan solo el
encabezado y la fila de alta (incluidas sus celdas unidas); las filas de
datos se ajustan al soltar (re-anchar cientos de filas en cada evento de
drag era un cuello de botella). "Ajustar al contenido" de una fija le da el
ancho del contenido (achicando las redimensionables, nunca por debajo de
sus mínimos); en una redimensionable, y "Ajustar todas", reparten el
espacio flexible en proporción (_repartir()).

Títulos del encabezado: centrados en la celda entera cuando entran; en
columnas angostas, en el espacio que dejan los íconos (con tooltip).

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

Filtro de columna (▼): la lista de valores con checkbox elige valores
exactos. Con texto en BUSCAR…, APLICAR deja solo los marcados que la lista
muestra (como en Google Sheets): así se filtra también por coincidencia
parcial (ej. todos los tags que contienen "viaje").

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

Enter en la fila de alta NUNCA guarda la fila, salvo con el foco en el ✓:
cada campo avanza al siguiente (TextField on_submit, CampoFiltrable/
CampoMonto on_avanzar — la pantalla arma la cadena) y el último lleva el
foco al ✓ sin activarlo (enfocar_confirmar()); ahí Enter (o un click)
confirma — un IconButton enfocado se activa con Enter.

tab_a_confirmar(control): con el foco en ese control (el último campo del
alta, ej. el Dropdown de Moneda), Tab o Enter llevan el foco al botón ✓ de
la fila, sin activarlo. El foco se fuerza a mano, sin depender de que el
recorrido nativo de Tab caiga justo en el ✓ (el DropdownMenu tiene su
propio ícono de flecha, y no tiene on_submit): la tecla se detecta con
page.on_keyboard_event (un despachador por página, _registrar_tecla(), que
respeta un manejador previo) mientras el control tiene el foco
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

TAG (etiqueta libre, Registro / Compras en cuotas / Deudas): celda_tag()
y campo_tag_alta() — mismo estilo en las tres. Vacía, la celda muestra
TEXTO_TAG_VACIO en TEXT_MUTED; la edición arranca vacía y guardar "" la
borra (la pantalla pasa None al service).

Filas de pie (filas_pie → FilaPie, ej. SALDO ANTERIOR): van al final, después
de un ft.Divider, con fondo verde/rojo muy sutil según el signo y texto
tenue; sin checkbox, no se editan ni se seleccionan (no están en _datos, así
que no entran en la selección, el orden ni la búsqueda). Siguen los anchos
de columna como cualquier fila y respetan los filtros por columna según
FilaPie.valores_filtro (ej. el filtro de Banco del Registro).

FilaAlta.unidas: una celda del alta puede ocupar varias columnas fijas
contiguas (ej. Deudas: selector $/0.XX/% + valor + "¿DE CUÁNTO?" sobre
Monto orig. + Pendiente, que en el alta no tienen nada propio que
mostrar). Su ancho es siempre la suma exacta de esas columnas (también
después de arrastrarlas), así que no tapa a las vecinas; lo que va adentro
lo recorta la celda.

selector_alta() (SelectorCiclico): para celdas angostas del alta donde un
ft.Dropdown no entra — un botón de texto que pasa a la opción siguiente.

Columna de acción: accion_fila(fila) devuelve hasta dos íconos de
icono_accion(). Un ícono con algo ya vinculado queda siempre visible en
color de acento; uno sin nada vinculado es gris y aparece solo con el
mouse encima de la fila, igual que el checkbox.

--- Barra flotante: compartir y acciones propias ---

on_compartir recibe TODAS las filas seleccionadas (una o varias): cada
pantalla decide si abre su flujo de una fila o el de varias filas con el
mismo coeficiente (ui/components/compartir_varios.py). acciones_barra
agrega botones circulares propios de la pantalla (AccionBarra: ej.
"Registrar pago"); motivo_deshabilitada(filas) los deja grises con el
motivo en el tooltip. aviso_barra(mensaje) muestra un error en la misma
barra (en lugar de un SnackBar) hasta que cambia la selección.

Σ (columna_suma): con una columna de monto en minor units, la barra suma
la de las filas seleccionadas y la muestra junto a la cantidad, con el
mismo formato que los montos de la tabla ("+ $45,320.00 ARS"). Una suma
por moneda si hay varias (ARS primero, separadas por SEPARADOR_SUMA); el
signo se respeta (neto). signo_suma(fila) → 1 / -1 da el signo cuando la
columna lo guarda en positivo (el Registro: tipo_movimiento). Las filas
traen currency_code, currency_symbol y decimales, como las devuelven los
services.

--- Sin confirmar corriendo la app (docs/FLET_API_NOTES.md, regla 2) ---

Confirmado por lectura del código instalado de Flet 0.86.5, pero sin
precedente en este proyecto: GestureDetector (on_horizontal_drag_update/
primary_delta, on_tap_down/on_secondary_tap_down con global_position/
local_position, on_enter/on_exit, mouse_cursor), controles posicionados
(left/top) dentro de page.overlay y de un ft.Stack, Container.on_size_change
(en particular, que se dispare en el primer layout y no solo al cambiar de
tamaño), `expand` entero (flex) sobre GestureDetector/Container dentro de
un Row, expand_loose, ft.context.disable_auto_update(),
page.on_keyboard_event (que lleguen Tab y Enter aunque el foco esté en un
Dropdown), que Enter active un IconButton enfocado, ft.DatePicker vía
page.show_dialog(), page.run_task() sobre focus() y que un TextButton de
SelectorCiclico con alto recortado por la celda se vea completo.
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
    BG_PIE_NEGATIVO,
    BG_PIE_POSITIVO,
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
    TEXT_NEGATIVO,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_SOBRE_BOTON,
)
from ui.theme.tokens import LayoutTokens, TypographyTokens
from ui.utils.prefs import escribir_pref, leer_pref
from utils.money import amount_display

# --- Configuración de layout ---

ANCHO_COL_CHECK = 32
ANCHO_COL_ACCION = 48  # ✓ de la fila de alta / íconos de la fila (icono_accion())
ANCHO_ICONO_ACCION = ANCHO_COL_ACCION // 2  # entran dos íconos por fila
ANCHO_BORDE = 1
ANCHO_MIN_COLUMNA = 80
# Mínimo default de una columna fija al arrastrarla (nunca más que su ancho de diseño).
ANCHO_MIN_COLUMNA_FIJA = 56
# `expand` solo acepta enteros: proporción × FACTOR_FLEX (resolución 0.1).
FACTOR_FLEX = 10
# "Ajustar al contenido": ancho aproximado de un carácter a REGISTRO_FONT_CELDA + margen.
ANCHO_POR_CARACTER = 7.5
PADDING_AJUSTE = 28
# Encabezado: ancho aproximado de un carácter del título (REGISTRO_FONT_HEADER,
# mayúsculas) — decide si el título entra centrado en la celda entera.
ANCHO_POR_CARACTER_HEADER = 7
PADDING_TITULO_ANGOSTO = 4

ALTURA_HEADER = 40
ALTURA_FILA = LayoutTokens.ALTURA_FILA_TABLA
PADDING_CELDA_H = 8
PADDING_CAMPO = 6  # content_padding de todos los TextField sin borde
ALTA_PADDING = 4  # alrededor de cada campo de la fila de alta (vertical de la fila, horizontal de la celda)
# Alto total de la fila de alta: celda + padding vertical + borde inferior de acento.
ALTURA_FILA_ALTA = ALTURA_FILA + 2 * ALTA_PADDING + ANCHO_BORDE
ALTURA_VACIO = 48  # fila "no hay filas para mostrar"
ALTURA_SEPARADOR_PIE = 8  # ft.Divider antes de las filas de pie (FilaPie)
TEXTO_SIN_VALOR = "—"  # celda de una FilaPie sin texto propio
TEXTO_TAG_VACIO = "ETIQUETA..."  # celda TAG vacía y placeholder del alta (celda_tag() / campo_tag_alta())
ALTA_RADIO_CAMPO = 4
ICONO_HEADER = 16
ICONO_CALENDARIO = 14
ICONO_ACCION_FILA = 14
ANCHO_BOTON_CALENDARIO = 24
# El texto de la fecha va casi sin relleno lateral: "AAAA-MM-DD" (~63 px) tiene
# que entrar junto al botón en una columna Fecha de 100 px.
PADDING_CAMPO_FECHA_H = 1
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

# SelectorCiclico (celdas angostas del alta)
PADDING_SELECTOR_CICLICO_H = 4

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
SEPARADOR_SUMA = " · "  # entre las sumas por moneda de la barra flotante (columna_suma)

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

# Teclas que, con el foco en el control de tab_a_confirmar(), llevan el foco
# al ✓ de la fila de alta (sin activarlo) — KeyboardEvent.key de Flet
# (etiqueta de la tecla lógica de Flutter).
TECLAS_IR_A_CONFIRMAR = ("Tab", "Enter", "Numpad Enter")

# Marca (Control.data) de los íconos de icono_accion() que solo se ven con
# el mouse encima de la fila.
_SOLO_HOVER = "solo_hover"


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
    # None: ANCHO_MIN_COLUMNA (redimensionable) o ANCHO_MIN_COLUMNA_FIJA (fija).
    ancho_min: Optional[int] = None
    extra_ajuste: int = 0         # px extra en "Ajustar al contenido" (ej. el dot de Banco)


@dataclass
class FilaAlta:
    """Lo que la pantalla le da a la tabla para su fila de alta."""
    celdas: dict[str, ft.Control]   # clave de columna → campo (sin celda: la pone la tabla)
    boton: ft.Control               # ✓ de la columna de acción
    foco: Optional[ft.Control] = None  # campo que recibe el foco tras un alta (el primero)
    # Celdas que ocupan varias columnas: clave de la primera → cuántas
    # (ella incluida). Solo columnas fijas (no redimensionables): su ancho
    # es la suma de los anchos. El campo va en celdas[clave de la primera].
    unidas: dict[str, int] = field(default_factory=dict)


@dataclass
class AccionBarra:
    """Botón circular extra de la barra flotante (además de Eliminar / Compartir)."""
    icono: str
    color: str
    texto: str                                   # ya en MAYÚSCULAS
    on_click: Callable[[list[dict]], None]       # recibe las filas seleccionadas
    # filas → motivo (ya en MAYÚSCULAS) para dejar el botón deshabilitado, o None.
    motivo_deshabilitada: Optional[Callable[[list[dict]], Optional[str]]] = None


@dataclass
class FilaPie:
    """
    Fila especial al final de la tabla (ej. SALDO ANTERIOR): sin checkbox, no
    editable ni seleccionable, texto tenue, fondo verde/rojo muy sutil.
    """
    textos: dict[str, str]          # clave de columna → texto (ya en MAYÚSCULAS); las que falten: TEXTO_SIN_VALOR
    positiva: bool                  # fondo BG_PIE_POSITIVO (True) o BG_PIE_NEGATIVO (False)
    # Valores para los filtros por columna (ej. {"banco": "BBVA"}): con un
    # filtro activo en esa columna, la fila se ve solo si su valor está
    # elegido. Columnas sin valor acá no la filtran.
    valores_filtro: dict[str, str] = field(default_factory=dict)
    tooltip: Optional[str] = None


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


def _solo_hover(control: Optional[ft.Control]) -> list[ft.Control]:
    """
    Íconos marcados por icono_accion() dentro de lo que devolvió accion_fila():
    el control, sus `controls` y el `content` de cada uno (alcanza para un
    ícono suelto o un Row de íconos).
    """
    if control is None:
        return []
    candidatos: list[Any] = []
    for hijo in [control, *(getattr(control, "controls", None) or [])]:
        candidatos += [hijo, getattr(hijo, "content", None)]
    return [c for c in candidatos if isinstance(c, ft.Control) and c.data == _SOLO_HOVER]


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
    acciones: Optional[list[ft.Control]] = None,
) -> ft.Control:
    """
    Título de la pantalla (tamaño de título de página) + buscador + selector
    de mes. `periodo` es un dict con "mes"/"anio" que se muta en el lugar
    al cambiar de mes; después se llama a on_cambio_periodo(). El buscador
    filtra la tabla (TablaPlanilla.buscar()).

    acciones (opcional): al extremo derecho, después del selector de mes (ej.
    el indicador de sync del Registro). Van en la MISMA fila: con la ventana
    angosta se achica el espacio libre primero y nada se superpone — en una
    fila aparte, esta se desbordaba por debajo de ellas.
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
                *(acciones or []),
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
    controles_titulo: Optional[list[ft.Control]] = None,
    acciones: Optional[list[ft.Control]] = None,
) -> ft.Control:
    """
    Barra "saldo por cuenta": ícono + título + chips (dot, nombre, monto,
    moneda; scroll horizontal si no entran) + pills de moneda a la derecha
    (la elegida, rellena). `monedas` se muestra ARS primero, después
    alfabético. controles_titulo (opcional) reemplaza al texto del título
    (ej. selector de hogar + botón de configuración en Gastos compartidos).
    acciones (opcional): al extremo derecho, después de las pills (ej. ↻
    recalcular saldos históricos).
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
                *(controles_titulo if controles_titulo is not None else [
                    ft.Text(titulo, size=tamanio, weight=TypographyTokens.SECTION_TITLE_WEIGHT, color=TEXT_PRIMARY),
                ]),
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
                *(acciones or []),
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
        fijos: dict[str, float] = {c.clave: float(c.ancho) for c in columnas if not c.redimensionable}
        guardados = leer_pref(pref_anchos, {})
        if isinstance(guardados, dict):
            for columna, ancho in guardados.items():
                if not isinstance(ancho, (int, float)) or ancho <= 0:
                    continue
                if columna in anchos:
                    anchos[columna] = float(ancho)
                elif columna in fijos:
                    fijos[columna] = float(ancho)
        ui = {
            "busqueda": "",
            "filtros": {},           # columna → set de valores visibles
            "orden": None,           # (columna, ascendente) o None
            "seleccion": set(),      # ids de fila
            "anchos": anchos,        # solo columnas redimensionables (proporciones)
            "fijos": fijos,          # columnas fijas: px (también se arrastran, ver docstring "Columnas")
            "ancho_tabla": None,     # px de la tabla entera (on_size_change)
            "ancho_util": None,      # px para las columnas redimensionables (ancho_tabla − fijas − checkbox − acción)
            "confirmando_eliminar": False,
            "aviso_barra": None,     # error inline de la barra flotante (aviso_barra())
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


class SelectorCiclico:
    """
    Botón de texto que al click (o Enter/Espacio con el foco) pasa a la
    opción siguiente — para celdas angostas del alta donde un ft.Dropdown no
    entra (su flecha sola ocupa ~40 px). Ej.: ME DEBEN ↔ DEBO, ARS → USD.
    `page` puede ser TablaPlanilla.pagina_alta: parchea solo el botón.
    """

    def __init__(
        self,
        page: Any,
        opciones: list[tuple[str, str]],
        valor: Optional[str],
        on_cambio: Callable[[str], None],
        nombre: str,
        colores: Optional[dict[str, str]] = None,
    ):
        """
        Args:
            opciones:  [(clave, texto visible en MAYÚSCULAS), ...], al menos una.
            valor:     Clave inicial (default: la primera).
            on_cambio: Recibe la clave nueva.
            nombre:    Qué se elige (tooltip), ya en MAYÚSCULAS.
            colores:   clave → color del texto (default TEXT_PRIMARY).
        """
        self._page = page
        self._opciones = opciones
        self._on_cambio = on_cambio
        self._colores = colores or {}
        claves = [clave for clave, _ in opciones]
        self._indice = claves.index(valor) if valor in claves else 0
        self._texto = ft.Text(size=TypographyTokens.REGISTRO_FONT_CELDA, weight=PESO_MONTO)
        textos = " / ".join(texto for _, texto in opciones)
        self.control = ft.TextButton(
            content=self._texto,
            tooltip=f"{nombre}: {textos} (CLICK PARA CAMBIAR)" if len(opciones) > 1 else nombre,
            style=ft.ButtonStyle(padding=ft.Padding.symmetric(horizontal=PADDING_SELECTOR_CICLICO_H)),
            on_click=self._siguiente,
        )
        self._pintar()

    @property
    def valor(self) -> str:
        return self._opciones[self._indice][0]

    def _pintar(self) -> None:
        clave, texto = self._opciones[self._indice]
        self._texto.value = texto
        self._texto.color = self._colores.get(clave, TEXT_PRIMARY)

    def _siguiente(self, e=None) -> None:
        if len(self._opciones) < 2:
            sin_auto_update()
            return
        self._indice = (self._indice + 1) % len(self._opciones)
        self._pintar()
        self._on_cambio(self.valor)
        self._page.update(self.control)


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
        accion_fila:      fila → contenido de la columna de acción (uno o
                          dos íconos de icono_accion(), en un Row), o None.
        fila_atenuada:    fila → True para dibujarla atenuada.
        avisos_eliminar:  filas → aviso extra para la confirmación de borrado.
        on_compartir:     filas seleccionadas (una o varias) → abre el flujo
                          de compartir. None = sin botón Compartir.
        acciones_barra:   Botones extra de la barra flotante (AccionBarra),
                          después de Eliminar/Compartir.
        al_recargar:      → controles extra a parchear en cada recargar()
                          (ej. la barra de resumen, ya re-armada).
        filas_pie:        → filas especiales al final (FilaPie, ej. SALDO
                          ANTERIOR). Se llama en cada redibujo: si calcularlas
                          cuesta, la pantalla las cachea.
        errores_esperados: excepciones de dominio que rechazan un valor
                          editado (además de ValueError).
        texto_vacio:      Fila que se muestra si no hay filas visibles.
        columna_suma:     Columna de las filas (monto en minor units) que la
                          barra flotante suma (Σ) sobre las seleccionadas. None
                          = sin suma. Ver docstring, "Barra flotante".
        signo_suma:       fila → 1 o -1, si el signo no está en columna_suma
                          (ej. el Registro). None = el valor tal cual.
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
        acciones_barra: Optional[list[AccionBarra]] = None,
        al_recargar: Optional[Callable[[], list[ft.Control]]] = None,
        filas_pie: Optional[Callable[[], list[FilaPie]]] = None,
        errores_esperados: tuple[type[Exception], ...] = (),
        texto_vacio: str = "NO HAY FILAS PARA MOSTRAR.",
        columna_suma: Optional[str] = None,
        signo_suma: Optional[Callable[[dict], int]] = None,
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
        self._acciones_barra = acciones_barra or []
        self._al_recargar = al_recargar
        self._filas_pie = filas_pie
        self._errores = (ValueError, *errores_esperados)
        self._texto_vacio = texto_vacio
        self._columna_suma = columna_suma
        self._signo_suma = signo_suma

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
        # Celdas unidas del alta: clave de la primera → (celda, claves que ocupa).
        self._unidas_alta: dict[str, tuple[ft.Container, list[str]]] = {}
        self._alta: Optional[FilaAlta] = None
        # Celdas de las filas de pie en pantalla (se re-anchan como las demás).
        self._celdas_pie: list[dict[str, ft.Control]] = []
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
            self._ui["aviso_barra"] = None
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

    def aviso_barra(self, mensaje: str) -> None:
        """Error inline en la barra flotante (ej. una selección que no se puede compartir junta); se borra al cambiar la selección."""
        self._ui["aviso_barra"] = mensaje
        self._ui["confirmando_eliminar"] = False
        self._actualizar_barra()
        self.refrescar(self._barra)

    def icono_accion(self, icono: str, tooltip: str, activo: bool, on_click: Callable[[], None]) -> ft.Control:
        """
        Ícono para accion_fila(). `activo` (ya hay algo vinculado): color de
        acento y siempre visible. Si no, gris y visible solo con el mouse
        encima de la fila (mismo patrón que el checkbox). El lugar del ícono
        queda reservado aunque no se vea, así los íconos no se corren.
        """
        icono_control = ft.Icon(
            icono, size=ICONO_ACCION_FILA, color=TEXT_ACCENT if activo else TEXT_MUTED,
            visible=activo, data=None if activo else _SOLO_HOVER,
        )
        return ft.Container(
            width=ANCHO_ICONO_ACCION, height=ALTURA_FILA, alignment=ft.Alignment.CENTER,
            tooltip=tooltip, on_click=lambda e: on_click(), content=icono_control,
        )

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
        campo.content_padding = ft.Padding.symmetric(horizontal=PADDING_CAMPO_FECHA_H, vertical=PADDING_CAMPO)

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
                    # Sin relleno propio: el ícono entra entero en ANCHO_BOTON_CALENDARIO.
                    style=ft.ButtonStyle(padding=ft.Padding.all(0)),
                    tooltip="ELEGIR FECHA", on_click=_abrir_calendario,
                ),
            ],
            spacing=0,
        )
        return control, campo

    def selector_alta(
        self, opciones: list[tuple[str, str]], valor: Optional[str], on_cambio: Callable[[str], None],
        nombre: str, colores: Optional[dict[str, str]] = None,
    ) -> SelectorCiclico:
        """SelectorCiclico para una celda angosta del alta (parchea solo su botón)."""
        return SelectorCiclico(self.pagina_alta, opciones, valor, on_cambio, nombre, colores)

    def boton_confirmar_alta(self, tooltip: str, on_click: Callable[[], None]) -> ft.IconButton:
        return ft.IconButton(icon=ft.Icons.CHECK, icon_color=TEXT_ACCENT, tooltip=tooltip, on_click=lambda e: on_click())

    def enfocar_confirmar(self) -> None:
        """Foco en el ✓ de la fila de alta, SIN activarlo (Enter en el último campo; ahí Enter o click confirma)."""
        self.enfocar(self._alta.boton if self._alta is not None else None)

    def tab_a_confirmar(self, control: ft.Control) -> None:
        """Con el foco en `control` (último campo del alta), Tab o Enter llevan el foco al ✓ sin activarlo (ahí Enter confirma)."""
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

    def celda_tag(
        self, fila: dict, clave: str, tag: Optional[str], on_guardar: Callable[[str], Optional[str]],
    ) -> ft.Container:
        """Celda TAG (texto libre): vacía muestra TEXTO_TAG_VACIO tenue; on_guardar recibe "" para borrarla."""
        return self.celda_texto(
            fila, clave, tag or TEXTO_TAG_VACIO, on_guardar, valor_inicial=tag or "",
            color=TEXT_PRIMARY if tag else TEXT_MUTED,
        )

    def campo_tag_alta(self, valor: str = "") -> ft.TextField:
        """Campo TAG de la fila de alta (opcional): la pantalla lo encadena con on_submit/on_change."""
        return ft.TextField(value=valor, hint_text=TEXTO_TAG_VACIO, text_align=ft.TextAlign.CENTER, **estilo_campo())

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
        if self._por_clave[clave].redimensionable:
            return self._ui["anchos"][clave] * self._escala()
        return self._ui["fijos"][clave]

    def _x_rel(self, clave: str) -> float:
        """px desde el inicio de las columnas de datos (después del checkbox) hasta el borde izquierdo de `clave`."""
        x = 0.0
        for c in self._claves:
            if c == clave:
                break
            x += self._px(c)
        return x

    def _minimo_px(self, clave: str) -> float:
        """Columna.ancho_min (o el default según el tipo); una fija nunca exige más que su ancho de diseño."""
        columna = self._por_clave[clave]
        if columna.redimensionable:
            return float(columna.ancho_min if columna.ancho_min is not None else ANCHO_MIN_COLUMNA)
        minimo = columna.ancho_min if columna.ancho_min is not None else ANCHO_MIN_COLUMNA_FIJA
        return float(min(minimo, columna.ancho))

    def _minimo(self, clave: str) -> float:
        """Mínimo de una columna redimensionable, en unidades de ui["anchos"]."""
        return self._minimo_px(clave) / self._escala()

    def _flex(self, clave: str) -> int:
        return max(1, round(self._ui["anchos"][clave] * FACTOR_FLEX))

    def _vecina(self, clave: str) -> Optional[str]:
        """La columna de la derecha (la que absorbe el drag), sea redimensionable o fija; la última no tiene."""
        indice = self._claves.index(clave)
        return self._claves[indice + 1] if indice + 1 < len(self._claves) else None

    def _recalcular_util(self) -> None:
        """ancho_util = lo que dejan las fijas (cambia cuando se arrastra una fija)."""
        ancho = self._ui.get("ancho_tabla")
        if ancho:
            fijas = sum(self._ui["fijos"].values())
            self._ui["ancho_util"] = max(1.0, ancho - 2 * ANCHO_BORDE - ANCHO_COL_CHECK - ANCHO_COL_ACCION - fijas)

    def _ancho_handle(self, clave: str) -> int:
        return ANCHO_RESIZE_HANDLE if self._vecina(clave) else 0

    def _con_boton(self, clave: str) -> bool:
        """¿La celda en edición lleva ✓? No en columnas angostas (ver ANCHO_MINIMO_CON_BOTON)."""
        return self._px(clave) >= ANCHO_MINIMO_CON_BOTON

    def _dimensionar(self, control: ft.Control, clave: str) -> None:
        """expand (redimensionable) o width en px (fija)."""
        if self._por_clave[clave].redimensionable:
            control.expand = self._flex(clave)
        else:
            control.width = self._ui["fijos"][clave]

    def _aplicar_anchos(self, celdas: dict[str, ft.Control]) -> None:
        for clave, celda in celdas.items():
            self._dimensionar(celda, clave)

    def _aplicar_unidas(self) -> None:
        """Celdas unidas del alta (FilaAlta.unidas): siempre la suma exacta de sus columnas."""
        for celda, claves in self._unidas_alta.values():
            celda.width = sum(self._ui["fijos"][c] for c in claves)

    def _guardar_anchos(self) -> None:
        # Una sola clave de prefs: proporciones de las redimensionables + px de las fijas.
        anchos = {c: round(v, 2) for c, v in self._ui["anchos"].items()}
        anchos.update({c: round(v, 1) for c, v in self._ui["fijos"].items()})
        escribir_pref(self._pref_anchos, anchos)

    def _anchos_cambiaron(self) -> None:
        """Aplica ui["anchos"]/ui["fijos"] a encabezado, alta y todas las filas; guarda y parchea."""
        self._aplicar_anchos(self._celdas_header)
        self._aplicar_anchos(self._celdas_alta)
        self._aplicar_unidas()
        for ref in self._cache.values():
            self._aplicar_anchos(ref["celdas"])
        for celdas in self._celdas_pie:
            self._aplicar_anchos(celdas)
        self._guardar_anchos()
        self.refrescar(self._contenedor_header, self._contenedor_alta, self._tabla_filas)

    def _redimensionar(self, clave: str, delta_px: float) -> None:
        """
        Drag del borde derecho de `clave`: la vecina de la derecha absorbe o
        cede el diferencial, sea redimensionable o fija (ver docstring,
        "Columnas").
        """
        vecina = self._vecina(clave)
        if vecina is None:
            return
        px_izquierda, px_vecina = self._px(clave), self._px(vecina)
        # Ninguna de las dos baja de su mínimo; si una ya está por debajo
        # (ventana muy angosta), solo puede crecer.
        delta = max(delta_px, min(0.0, self._minimo_px(clave) - px_izquierda))
        delta = min(delta, max(0.0, px_vecina - self._minimo_px(vecina)))
        if not delta:
            return
        if self._por_clave[clave].redimensionable and self._por_clave[vecina].redimensionable:
            self._ui["anchos"][clave] += delta / self._escala()
            self._ui["anchos"][vecina] -= delta / self._escala()
        else:
            # Alguna es fija: se trabaja en px. Las redimensionables pasan a
            # guardar su ancho en px (misma proporción entre ellas) y el
            # espacio flexible cambia lo mismo que las fijas, al revés: así
            # la izquierda crece exactamente `delta` y la vecina lo cede.
            flexibles = {c: self._px(c) for c in self._redimensionables()}
            for c, px in ((clave, px_izquierda + delta), (vecina, px_vecina - delta)):
                if self._por_clave[c].redimensionable:
                    flexibles[c] = px
                else:
                    self._ui["fijos"][c] = px
            self._ui["anchos"].update(flexibles)
            self._recalcular_util()
        self._aplicar_anchos(self._celdas_header)
        self._aplicar_anchos(self._celdas_alta)
        self._aplicar_unidas()
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
        if not self._por_clave[clave].redimensionable:
            # Fija: toma el ancho del contenido; las redimensionables se
            # achican lo que haga falta, nunca por debajo de sus mínimos.
            libre = 0.0
            if self._ui["ancho_util"]:
                libre = max(0.0, self._ui["ancho_util"] - sum(self._minimo_px(c) for c in self._redimensionables()))
            actual = self._ui["fijos"][clave]
            objetivo = max(self._minimo_px(clave), self._ancho_contenido_px(clave))
            self._ui["fijos"][clave] = min(objetivo, actual + libre) if self._ui["ancho_util"] else objetivo
            self._recalcular_util()
            self._anchos_cambiaron()
            return
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
        self._ui["ancho_tabla"] = e.width
        self._recalcular_util()

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
            # Recién ahora las filas de datos (y las de pie): ver docstring, "Columnas".
            for ref in self._cache.values():
                self._aplicar_anchos(ref["celdas"])
            for celdas in self._celdas_pie:
                self._aplicar_anchos(celdas)
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
            # Con texto en BUSCAR…, solo cuenta lo que la lista muestra (coincidencia parcial).
            texto = texto_busqueda["valor"].lower()
            elegidos = {v for v in marcados if texto in v.lower()} if texto else set(marcados)
            if elegidos >= set(valores):
                self._ui["filtros"].pop(clave, None)
            else:
                self._ui["filtros"][clave] = elegidos
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
        pie = ALTURA_SEPARADOR_PIE + len(self._celdas_pie) * ALTURA_FILA if self._celdas_pie else 0
        return ALTURA_HEADER + ALTURA_FILA_ALTA + (filas * ALTURA_FILA if filas else ALTURA_VACIO) + pie

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
        self._ui["aviso_barra"] = None
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
                    color=TEXT_SECONDARY, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=columna.titulo,
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
        # a la derecha (flecha de orden, ▼, handle y borde). En columnas
        # angostas (el título no entraría con ese relleno) se centra en el
        # espacio que queda, sin relleno.
        ancho_iconos = (ICONO_MENU if ordenada else 0) + ICONO_HEADER + self._ancho_handle(clave) + ANCHO_BORDE
        ancho_titulo = len(columna.titulo) * ANCHO_POR_CARACTER_HEADER
        centrar = self._px(clave) >= ancho_titulo + 2 * ancho_iconos

        celda = ft.GestureDetector(
            on_secondary_tap_down=_click_derecho,
            content=ft.Container(
                height=ALTURA_HEADER,
                padding=ft.Padding.only(left=ancho_iconos if centrar else PADDING_TITULO_ANGOSTO),
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

    def _celda_alta(self, clave: str, contenido: Optional[ft.Control], cantidad: int = 1) -> ft.Container:
        """
        Alto fijo + recorte: todas las celdas del alta miden lo mismo (el
        borde lo da la celda). `cantidad` > 1: la celda ocupa esa cantidad
        de columnas fijas a partir de `clave` (FilaAlta.unidas).
        """
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
        if cantidad == 1:
            self._dimensionar(celda, clave)
            self._celdas_alta[clave] = celda
            return celda
        inicio = self._claves.index(clave)
        claves = self._claves[inicio:inicio + cantidad]
        if len(claves) != cantidad or any(self._por_clave[c].redimensionable for c in claves):
            raise ValueError(f"FilaAlta.unidas['{clave}']: solo se pueden unir columnas fijas existentes.")
        # Ancho = suma exacta de las columnas que ocupa (se re-aplica al
        # arrastrar una de ellas): nunca tapa a las vecinas.
        self._unidas_alta[clave] = (celda, claves)
        self._aplicar_unidas()
        return celda

    def _celdas_fila_alta(self) -> list[ft.Control]:
        celdas: list[ft.Control] = []
        indice = 0
        while indice < len(self._claves):
            clave = self._claves[indice]
            cantidad = max(1, self._alta.unidas.get(clave, 1))
            celdas.append(self._celda_alta(clave, self._alta.celdas.get(clave), cantidad))
            indice += cantidad
        return celdas

    def _construir_fila_alta(self) -> ft.Control:
        self._celdas_alta.clear()
        self._unidas_alta.clear()
        self._foco_tab = None
        self._alta = self._construir_alta()
        return ft.Container(
            bgcolor=BG_FILA_ALTA,
            padding=ft.Padding.symmetric(vertical=ALTA_PADDING),
            border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=TEXT_ACCENT)),
            content=ft.Row(
                [
                    ft.Container(width=ANCHO_COL_CHECK),
                    *self._celdas_fila_alta(),
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
        if e.shift or e.ctrl or e.alt or e.meta or e.key not in TECLAS_IR_A_CONFIRMAR:
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
        self._ui["aviso_barra"] = None
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
        accion = self._accion_fila(fila) if self._accion_fila else None
        # Íconos de icono_accion() sin nada vinculado: solo con hover.
        iconos_hover = _solo_hover(accion)
        contenido = ft.Row(
            [
                ft.Container(width=ANCHO_COL_CHECK, height=ALTURA_FILA, alignment=ft.Alignment.CENTER, content=checkbox),
                *[celdas[clave] for clave in self._claves],
                ft.Container(
                    width=ANCHO_COL_ACCION, height=ALTURA_FILA, alignment=ft.Alignment.CENTER, content=accion,
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
            for icono in iconos_hover:
                icono.visible = activo
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
            self._ui["aviso_barra"] = None
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

        self._tabla_filas.controls = (controles or [
            ft.Container(
                height=ALTURA_VACIO,
                alignment=ft.Alignment.CENTER_LEFT,
                padding=ft.Padding.symmetric(horizontal=PADDING_RESUMEN_H),
                content=ft.Text(
                    self._texto_vacio, italic=True, color=TEXT_MUTED, size=TypographyTokens.REGISTRO_FONT_CELDA,
                ),
            )
        ]) + self._controles_pie()
        self._dibujar_header()
        self._actualizar_barra()

    # --- Filas de pie (FilaPie: ej. SALDO ANTERIOR) ---

    def _pie_visibles(self) -> list[FilaPie]:
        """Las filas de pie que pasan los filtros por columna activos (comparación sin mayúsculas/minúsculas)."""
        if self._filas_pie is None:
            return []
        filtros = {
            clave: {valor.upper() for valor in valores} for clave, valores in self._ui["filtros"].items()
        }
        return [
            fila for fila in self._filas_pie()
            if all(
                fila.valores_filtro[clave].upper() in valores
                for clave, valores in filtros.items() if clave in fila.valores_filtro
            )
        ]

    def _controles_pie(self) -> list[ft.Control]:
        """Separador + una fila por FilaPie visible. Se rearman en cada redibujo (son pocas)."""
        self._celdas_pie = []
        filas = self._pie_visibles()
        if not filas:
            return []
        controles: list[ft.Control] = [ft.Divider(height=ALTURA_SEPARADOR_PIE, thickness=ANCHO_BORDE, color=BORDER_DEFAULT)]
        for fila in filas:
            celdas: dict[str, ft.Control] = {}
            for clave in self._claves:
                celda = self._contenedor_celda(clave)
                celda.content = texto_celda(
                    fila.textos.get(clave, TEXTO_SIN_VALOR), color=TEXT_SECONDARY, tooltip=fila.tooltip,
                )
                celdas[clave] = celda
            self._celdas_pie.append(celdas)
            controles.append(ft.Container(
                height=ALTURA_FILA,
                bgcolor=BG_PIE_POSITIVO if fila.positiva else BG_PIE_NEGATIVO,
                border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
                content=ft.Row(
                    [
                        ft.Container(width=ANCHO_COL_CHECK),  # sin checkbox: no se selecciona
                        *[celdas[clave] for clave in self._claves],
                        ft.Container(width=ANCHO_COL_ACCION),
                    ],
                    spacing=0,
                ),
            ))
        return controles

    # ------------------------------------------------------------
    # BARRA FLOTANTE DE SELECCIÓN MÚLTIPLE (page.overlay)
    # ------------------------------------------------------------

    def _seleccionadas(self) -> list[dict]:
        return [f for f in self._datos if f["id"] in self._ui["seleccion"]]

    def _cancelar_seleccion(self, e=None) -> None:
        self._ui["seleccion"].clear()
        self._ui["confirmando_eliminar"] = False
        self._ui["aviso_barra"] = None
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
        self._ui["aviso_barra"] = None
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

    def _ejecutar_accion(self, accion: AccionBarra) -> None:
        seleccionadas = self._seleccionadas()
        if seleccionadas:
            accion.on_click(seleccionadas)

    def _texto_suma(self) -> str:
        """Σ de columna_suma sobre las filas seleccionadas, una por moneda (ver docstring, "Barra flotante")."""
        totales: dict[str, list] = {}  # codigo → [minor, decimales, símbolo]
        for fila in self._seleccionadas():
            signo = self._signo_suma(fila) if self._signo_suma else 1
            codigo = fila.get("currency_code") or ""
            total = totales.setdefault(codigo, [0, fila.get("decimales", 2), fila.get("currency_symbol") or ""])
            total[0] += signo * (fila.get(self._columna_suma) or 0)
        partes = [
            f"{'-' if minor < 0 else '+'} {amount_display(abs(minor), decimales, simbolo)} {codigo}"
            for codigo, (minor, decimales, simbolo) in sorted(
                totales.items(), key=lambda par: (par[0] != MONEDA_DEFAULT, par[0]),
            )
        ]
        return "Σ " + SEPARADOR_SUMA.join(partes) if partes else ""

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
        ]
        suma = self._texto_suma() if self._columna_suma is not None else ""
        if suma:
            controles.append(ft.Text(suma, size=tamanio, weight=PESO_MONTO, color=TEXT_PRIMARY))
        controles += [
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
        if self._acciones_barra:
            seleccionadas = self._seleccionadas()
            for accion in self._acciones_barra:
                motivo = accion.motivo_deshabilitada(seleccionadas) if accion.motivo_deshabilitada else None
                controles += [
                    self._boton_circular(
                        accion.icono, accion.color, motivo or accion.texto,
                        lambda e, a=accion: self._ejecutar_accion(a), habilitado=motivo is None,
                    ),
                    ft.Text(accion.texto, size=tamanio, color=TEXT_PRIMARY if motivo is None else TEXT_MUTED),
                ]
        aviso = self._ui.get("aviso_barra")
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
        elif aviso:
            controles += [
                ft.Container(width=ANCHO_BORDE, height=DIAMETRO_BOTON_FLOT, bgcolor=BORDER_BARRA),
                ft.Text(
                    aviso, size=tamanio, weight=PESO_MONTO, color=TEXT_NEGATIVO,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=aviso, expand=True,
                ),
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
