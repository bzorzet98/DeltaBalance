"""
DeltaBalance — ui/components/registro_transacciones.py

Registro de transacciones (tema oscuro): barra de título (búsqueda +
período) → barra de saldo por cuenta → tabla con encabezados filtrables/
ordenables/redimensionables, fila de alta pegada al encabezado y filas de
datos con edición inline → barra flotante de selección múltiple (eliminar /
compartir).

Reglas de arquitectura: solo AccountsService/CategoriasService/
TransactionService/SharedExpensesService/SavingsService/DebtsService —
nunca repositories/ ni db/ directo (CLAUDE.md §2/§3). La firma de build()
no cambió.

--- Estado de interfaz y refresco parcial ---

Tras un cambio de datos (alta, edición, borrado, compartir) el componente
se refresca solo (_datos_cambiaron()): recarga cuentas, transacciones y
compartidos, re-dibuja la barra de saldo y la tabla, y parchea solo esas
partes. Ya no llama a on_cambio() (queda en la firma para no romper al
dashboard): antes el dashboard reconstruía el Registro entero en cada
cambio, y con cientos de filas eso era lo que lo hacía lento.

Las filas se cachean por id de transacción (cache_filas) junto con una
"firma" de lo que muestran (_firma()). Al re-dibujar, una fila cuya firma
no cambió se reusa tal cual — solo se le ajustan fondo alternado,
selección, checkbox y anchos —, así que filtrar, ordenar, buscar o guardar
una celda reconstruye únicamente las filas que cambiaron de verdad.

El estado de interfaz vive en un almacén por página (_ESTADOS_UI, ver
_estado_ui()) y no en variables del build(): período, búsqueda, filtros por
columna, orden, selección, moneda de la barra de saldo, anchos de columna y
el borrador de la fila de alta. Sobrevive cuando ui/app.py reconstruye la
pantalla (otra pantalla cambió datos) y al navegar entre secciones.

--- Performance: nunca un page.update() completo ---

page.update() sin argumentos re-diffea la página entera — y con ui/app.py
manteniendo vivas todas las pantallas visitadas, eso las incluye a todas.
Este archivo parchea solo lo que cambió: _refrescar(*controles) →
page.update(*controles), únicamente con controles montados en la sesión
(_montado()). Dos detalles de Flet 0.86.5 que lo hacen necesario:

- Auto-update: un handler de evento que no llama a ningún update() termina
  en un page.update() completo automático (flet/messaging/session.py,
  after_event()). Los handlers que solo guardan estado (borrador de alta,
  tamaño de la tabla, checkbox del filtro) lo apagan con
  ft.context.disable_auto_update() (_sin_auto_update()).
- CampoFiltrable/CampoMonto llaman a page.update() por dentro, y sus
  archivos no se tocan. De `page` solo usan update() (verificado en su
  código), así que acá reciben una _ActualizacionLocal: un reemplazo de
  page cuyo update() parchea solo la celda/fila donde viven (cualquier otro
  atributo se delega a la página real).

Excepción consciente: los SnackBar siguen el patrón del proyecto
(page.overlay + page.update()). Es un update completo por mensaje, no por
evento de alta frecuencia; page.show_dialog() lo evitaría, pero dejaría el
SnackBar en la pila de diálogos y page.pop_dialog() podría cerrarlo a él en
vez del AlertDialog de routing que esté abierto.

Las celdas editables están en modo lectura por default: el campo
(TextField / CampoFiltrable / CampoMonto) se crea al hacer click en esa
celda y se destruye al salir — Enter, ✓ o perder el foco (CLAUDE.md §10).

--- Columnas: ancho completo, el vecino absorbe ---

La tabla ocupa siempre el 100% del ancho disponible: las 6 columnas de
datos usan `expand` (flex) proporcional a ui["anchos"], nunca un ancho fijo
en píxeles, así que la suma siempre llena la fila, sea cual sea el tamaño
de la ventana. Las columnas de checkbox y de acción sí son fijas.

Todas las columnas son redimensionables (mínimos en ANCHOS_MIN). Arrastrar
el borde derecho de una columna le pasa el diferencial a la columna
inmediatamente a la derecha (estilo Google Sheets), respetando el mínimo de
las dos: el ancho total no cambia. La última columna no tiene handle (su
borde derecho es el de la tabla). Durante el drag se actualizan solo el
encabezado y la fila de alta; las filas de datos se ajustan al soltar
(re-anchar cientos de filas en cada evento de drag era un cuello de
botella). "Ajustar al contenido"/"Ajustar todas" reparten el espacio entre
las demás columnas en proporción (_repartir()).

Para convertir píxeles (drag, ANCHOS_MIN, posición de las sugerencias) a
esas proporciones hace falta el ancho real de la tabla: lo informa
Container.on_size_change (ui["ancho_util"]). Mientras no llegó, se asume
1 unidad = 1 px.

Los anchos se guardan en .deltabalance_prefs.json (ui/utils/prefs.py,
clave "registro_anchos_columnas") como proporciones relativas.

--- Popups: un host fijo en page.overlay ---

El menú contextual del encabezado y el overlay de filtro se abren dentro
de un único host por página (ui["host_popups"], en page.overlay, creado una
sola vez): una capa transparente a pantalla completa + el popup
posicionado en el puntero (TapEvent.global_position). Abrir/cerrar parchea
solo el host.

La capa escucha click izquierdo y derecho (GestureDetector on_tap_down /
on_secondary_tap_down) sobre un fondo casi transparente (COLOR_CAPA, para
que sea hit-testeable sí o sí): cualquier click afuera cierra el popup. Si
ese mismo click cae sobre el encabezado de otra columna, además abre el
popup nuevo sin un segundo click — click derecho → su menú, click en su ▼ →
su filtro. Para saber qué columna hay debajo, al abrirse cada popup se anota
dónde está el encabezado en pantalla (ui["ancla_header"], sacado de
global_position − local_position del evento que lo abrió) y se mapea con
los anchos de columna. Mientras la capa tapa todo el dashboard no puede
scrollear, así que esa posición no queda vieja. (Flet no tiene
page.on_click ni stop_propagation: la capa cumple ese rol.)

La barra flotante de selección vive en page.overlay por el mismo motivo
(fija abajo de la ventana, no al final de la tabla), una por página, y se
esconde cuando la pantalla se oculta (hook data["al_ocultar"], ver
ui/app.py).

--- Fila de alta ---

Todas las celdas tienen el mismo alto fijo (ALTURA_FILA) con
clip_behavior=HARD_EDGE, y todos los TextField el mismo content_padding.
Las sugerencias de CampoFiltrable (Banco y Categoría, en el alta y en la
edición inline) no empujan el layout: _CampoFiltrableFlotante le pasa la
lista a capa_sugerencias, una capa posicionada dentro de un Stack que
envuelve la tabla, justo debajo de la celda. No va en page.overlay: ahí
haría falta la posición de la celda en pantalla, que Flet solo da en
eventos de puntero (no al llegar con Tab) y que queda vieja al scrollear el
dashboard; dentro de la tabla sale de los anchos de columna y del alto de
las filas. Si hay pocas filas debajo, relleno_tabla estira la tabla lo
justo para que la lista entre entera (fuera de los límites del Stack no
recibiría clicks).

Enter en Concepto, Monto o Fecha confirma y guarda; Tab avanza (nativo).
En Banco/Categoría Enter elige la sugerencia y pasa el foco al campo
siguiente, no guarda: ahí Enter hace falta para elegir. En Moneda (el
último campo) Tab o Enter confirman y guardan la fila directamente. Como
Dropdown no tiene on_submit, se detecta con page.on_keyboard_event (un
despachador por página, registrado al final de build()) mientras Moneda
tiene el foco (on_focus/on_blur). No se guarda en el on_blur de Moneda: un
click en otro campo de la fila, o en una opción del propio menú de Moneda,
también le saca el foco y guardaría una fila a medias. Con Enter se espera
ESPERA_CONFIRMAR_DESDE_MONEDA_S antes de guardar, por si ese Enter además
eligió una opción del menú. Al guardar, la fila de alta se reconstruye
vacía con el foco en Concepto. focus() es async en Flet 0.86.5, así que se
llama con page.run_task() (_enfocar()).

Signo del monto: negativo = egreso, positivo = ingreso. Categorías
especiales (_CATEGORIAS_ROUTING_ESPECIAL), sin cambios de comportamiento:
- "Autotransferencia": pide la cuenta destino y llama a
  TransactionService.create_transfer() (el concepto viaja como `notes`,
  create_transfer() no tiene `concept`). Signo ignorado (abs).
- "Ahorro/Inversión": modo simple (objetivo + "crear nuevo" →
  SavingsService.get_or_create_reserved_cash_asset() +
  register_purchase()) o "Elegir activo específico" (formulario de
  ui/components/dialogo_compra_ahorro.py en el mismo diálogo). Signo
  ignorado.
- "Deuda": crea la transacción normal primero y después pide persona/
  vencimiento para vincularle una deuda (DebtsService.create(origen_tipo=
  'transaccion')); debt_type sale del signo (egreso → 'a_favor').

--- Filas ---

Edición inline (CLAUDE.md §10): Concepto, Banco, Categoría, Monto (valor
absoluto — el signo no cambia desde acá) y Fecha. Moneda es de solo
lectura. Banco y Categoría excluyen tarjetas de crédito / usan las mismas
categorías que la fila de alta. Un ícono de personas al final de la fila
marca las transacciones ya compartidas.

Todas las celdas centran su contenido en los dos ejes; Monto va centrado
vertical y alineado a la derecha. En los encabezados el título se centra
en la celda entera (a la izquierda lleva el mismo ancho que ocupan los
íconos de la derecha). La fila de alta y los campos de edición inline
centran el texto igual, así no salta al entrar en edición.

Eliminar (barra flotante): confirmación inline en la misma barra, sin
diálogo. Si alguna fila es parte de una autotransferencia o el origen de
un movimiento de ahorro, el aviso lo dice ahí mismo
(TransactionService.get_delete_warnings()). Compartir: abre el flujo
existente de ui/components/compartir_gasto.py para UNA fila (el flujo es
un diálogo por transacción) — con más de una seleccionada el botón queda
deshabilitado.

--- Colores ---

Paleta propia de tema oscuro (constantes BG_*/TEXT_*/...). El tema de la
app (oscuro) lo fija ui/app.py; este archivo no toca page.theme_mode. El
dot de cada cuenta usa el color que el usuario le eligió en Cuentas
(cuentas.color_hex); si nunca eligió uno, el color de marca de
DOTS_CUENTAS por nombre (sin tildes, en mayúsculas); si tampoco, el
default. El nombre al lado del dot va siempre en TEXT_SECONDARY para no
competir con colores muy brillantes (ej. el amarillo de MP).

--- Sin confirmar corriendo la app (docs/FLET_API_NOTES.md, regla 2) ---

Confirmado por lectura del código instalado de Flet 0.86.5, pero sin
precedente en este proyecto: GestureDetector (on_horizontal_drag_update/
primary_delta, on_tap_down/on_secondary_tap_down con global_position/
local_position, on_enter/on_exit, mouse_cursor), controles posicionados
(left/top) dentro de page.overlay y de un ft.Stack, Container.on_size_change
(en particular, que se dispare en el primer layout y no solo al cambiar de
tamaño), `expand` entero (flex) sobre GestureDetector/Container dentro de
un Row, expand_loose, ft.context.disable_auto_update(),
page.on_keyboard_event (en particular, que llegue Tab/Enter aunque el foco
esté en el Dropdown de Moneda), ft.DatePicker vía page.show_dialog(), y
page.run_task() sobre focus() y sobre el on_click async del ícono de
compartir_gasto.py.
"""

import asyncio
import calendar
import unicodedata
from datetime import date, datetime
from typing import Any, Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.debts_service import DebtError, DebtsService
from services.savings_service import SavingsError, SavingsService
from services.shared_expenses_service import SharedExpensesService
from services.transaction_service import TransactionError, TransactionService
from ui.components import compartir_gasto, dialogo_compra_ahorro
from ui.components.campo_filtrable import ALTURA_ITEM_SUGERENCIA, ALTURA_MAX_LISTA, CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.theme.tokens import LayoutTokens, TypographyTokens
from ui.utils.prefs import escribir_pref, leer_pref
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---

# Paleta (tema oscuro)
BG_APP = "#1a1a1a"
BG_SUPERFICIE = "#242424"
BG_ENCABEZADO = "#2a2a2a"
BG_FILA_ALTA = "#2d2d2d"
BG_FILA_PAR = "#242424"
BG_FILA_IMPAR = "#262626"
BG_FILA_HOVER = "#2f2f2f"
BG_FILA_SEL = "#1a2a3a"
BG_BARRA_FLOT = "#1e1e1e"
BG_OVERLAY = "#2c2c2c"
BG_MENU_CTX = "#2c2c2c"
BG_ITEM_HOVER = "#383838"

BORDER_DEFAULT = "#3a3a3a"
BORDER_HEADER = "#333333"
BORDER_BARRA = "#3a3a3a"
BORDER_OVERLAY = "#404040"

TEXT_PRIMARY = "#e8e8e8"
TEXT_SECONDARY = "#9a9a9a"
TEXT_MUTED = "#666666"
TEXT_POSITIVO = "#4caf50"
TEXT_NEGATIVO = "#f44336"
TEXT_ACCENT = "#4a9eff"
TEXT_SOBRE_BOTON = "#ffffff"

BTN_ELIMINAR = "#e53935"
BTN_COMPARTIR = "#1976d2"

DOTS_CUENTAS = {
    # Bancos argentinos — colores de marca
    "NACION":               "#005F86",  # azul/teal BNA
    "CREDICOOP":            "#5E584C",  # tono Credicoop
    "BBVA":                 "#004481",  # azul marino oficial BBVA
    "GALICIA":              "#FF5000",  # naranja oficial Banco Galicia
    "SANTANDER":            "#EC0000",  # rojo oficial Santander
    "NARANJA X":            "#FF6200",  # naranja oficial Naranja X
    "BRUBANK":              "#5B2D8E",  # violeta oficial Brubank
    "MP":                   "#009EE3",  # azul/celeste oficial Mercado Pago
    "MERCADO PAGO CREDITO": "#009EE3",  # mismo azul/celeste Mercado Pago
    # Exchanges y crypto — colores de marca
    "COCOS":                "#0062DE",  # azul Cocos Capital
    "BULL MARKET":          "#1E27F3",  # azul Bull Market Brokers
    "BUENBIT":              "#787B7C",  # gris/slate Buenbit
    "BINANCE":              "#F3BA2F",  # amarillo dorado Binance
    "NEXO":                 "#787B7C",  # gris/slate (mismo que Buenbit)
    "FIWIND":               "#00B4D8",  # celeste Fiwind
    # Otros
    "BILLETERA ER":         "#549514",  # verde oficial Billetera Entre Ríos
    "MACRO":                "#002855",  # azul marino Banco Macro
    "JOY":                  "#00C853",  # verde Joy
    "CAJA EFECTIVO":        "#9E9E9E",  # gris neutro para efectivo
    "DEFAULT":              "#757575",  # gris para cualquier cuenta sin color asignado
}
# DEFAULT de la columna cuentas.color_hex (db/schema_migrations.py): una
# cuenta con este color nunca eligió uno propio → se usa DOTS_CUENTAS.
COLOR_CUENTA_SIN_ELEGIR = "#5F5E5A"

# Tipografía — tamaños en TypographyTokens.REGISTRO_FONT_* (ui/theme/tokens.py)
PESO_HEADER = ft.FontWeight.W_500
PESO_CELDA = ft.FontWeight.W_400
PESO_MONTO = ft.FontWeight.W_600

# Columnas: (clave, título del encabezado)
COLUMNAS = [
    ("concepto", "CONCEPTO"),
    ("banco", "BANCO"),
    ("categoria", "CATEGORÍA"),
    ("monto", "MONTO"),
    ("fecha", "FECHA"),
    ("moneda", "MONEDA"),
]
CLAVES_COLUMNAS = [clave for clave, _ in COLUMNAS]
# Proporción inicial de cada columna (px a un ancho de tabla de referencia;
# en pantalla son proporciones — ver docstring, "Columnas").
ANCHOS_DEFAULT = {"concepto": 220, "banco": 130, "categoria": 150, "monto": 120, "fecha": 110, "moneda": 80}
ANCHOS_MIN = {
    "concepto":  80,
    "banco":     80,
    "categoria": 80,
    "monto":     80,
    "fecha":     80,
    "moneda":    60,
}
# `expand` solo acepta enteros: proporción × FACTOR_FLEX (resolución 0.1).
FACTOR_FLEX = 10
ANCHO_COL_CHECK = 32
ANCHO_COL_ACCION = 48  # ✓ de la fila de alta / indicador de compartido en las filas
ANCHO_BORDE = 1
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
ALTURA_VACIO = 48  # fila "no hay movimientos"
ALTA_RADIO_CAMPO = 4
ICONO_HEADER = 16
ICONO_CALENDARIO = 16
ICONO_COMPARTIDO = 14
ANCHO_BOTON_CALENDARIO = 32

# Redimensionado
ANCHO_RESIZE_HANDLE = 8
ANCHO_LINEA_RESIZE = 3
RESIZE_DRAG_INTERVAL_MS = 30
SIZE_CHANGE_INTERVAL_MS = 100
PREF_ANCHOS_COLUMNAS = "registro_anchos_columnas"

# Sugerencias flotantes de CampoFiltrable
ANCHO_MINIMO_SUGERENCIAS = 160

# Cabecera (barra de título + barra de saldo): el buscador, el selector de
# mes y la barra de saldo miden lo mismo de alto y usan el mismo tamaño de
# texto (TypographyTokens.REGISTRO_FONT_SALDO_BAR) — un escalón entre el
# título (PAGE_TITLE_SIZE) y la tabla (REGISTRO_FONT_CELDA).
ALTURA_CABECERA = 56
ICONO_CABECERA = 20

# Barra de título
ANCHO_BUSQUEDA = 300
ALTURA_BARRA_TITULO = ALTURA_CABECERA
RADIO_CONTROL = 8
PADDING_SELECTOR_MES_H = 4

# Barra de saldo
ALTURA_BARRA_SALDO = ALTURA_CABECERA
PADDING_SALDO_H = 16
PADDING_SALDO_V = 8
# Aire arriba/abajo de los chips dentro de su Row scrolleable: la scrollbar
# horizontal se dibuja sobre el borde inferior del área scrolleable, así
# que queda sobre este aire y no sobre el texto.
ESPACIO_SCROLLBAR_SALDO = 8
DOT_SIZE = 8
DOT_SIZE_SALDO = 10
ESPACIO_DOT = 6
ESPACIO_CHIPS_SALDO = 20
PILL_ALTURA = 32
PILL_PADDING_H = 12
PILL_RADIO = 16

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
LIMITE_TRANSACCIONES_DEL_MES = 500
ANCHO_DIALOGO_ROUTING = 320
MONEDA_DEFAULT = "ARS"
HINT_MONTO_ALTA = "± MONTO"
# Teclas que, con el foco en Moneda (último campo del alta), confirman la
# fila — KeyboardEvent.key de Flet (etiqueta de la tecla lógica de Flutter).
TECLAS_CONFIRMAR_DESDE_MONEDA = ("Tab", "Enter", "Numpad Enter")
ESPERA_CONFIRMAR_DESDE_MONEDA_S = 0.15

# Categorías especiales de routing de la fila de alta — clave:
# (categoria_principal, subcategoria), mismo criterio que
# services/categorias_service.py CATEGORIAS_PROTEGIDAS (por nombre, el id
# varía entre bases).
_CATEGORIAS_ROUTING_ESPECIAL: dict[tuple[str, str], str] = {
    ("MOVIMIENTO CAPITAL", "Autotransferencia"): "autotransferencia",
    ("MOVIMIENTO CAPITAL", "Ahorro/Inversión"): "ahorro_inversion",
    ("MOVIMIENTO CAPITAL", "Deuda"): "deuda",
}
# Opción "+ Crear nuevo objetivo" del diálogo de Ahorro/Inversión — nunca
# colisiona con un id real (siempre numérico).
_ID_OBJETIVO_NUEVO = "__nuevo__"


# ============================================================
# ESTADO DE INTERFAZ POR PÁGINA (ver docstring del módulo)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia() -> dict:
    return {
        "concepto": "", "cuenta_id": None, "categoria_id": None,
        "monto": "", "fecha": date.today().isoformat(), "moneda": None,
    }


def _anchos_guardados() -> dict[str, float]:
    anchos: dict[str, float] = dict(ANCHOS_DEFAULT)
    guardados = leer_pref(PREF_ANCHOS_COLUMNAS, {})
    if isinstance(guardados, dict):
        for columna, ancho in guardados.items():
            if columna in anchos and isinstance(ancho, (int, float)) and ancho > 0:
                anchos[columna] = float(ancho)
    return anchos


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {
            "mes": hoy.month,
            "anio": hoy.year,
            "busqueda": "",
            "filtros": {},           # columna → set de valores visibles
            "orden": None,           # (columna, ascendente) o None
            "seleccion": set(),      # ids de transacción
            "moneda_saldo": MONEDA_DEFAULT,
            "anchos": _anchos_guardados(),
            "ancho_util": None,      # px para las 6 columnas de datos (on_size_change)
            "alta": _alta_vacia(),
            "confirmando_eliminar": False,
            "visible": True,
            "barra": None,           # barra flotante (page.overlay), una por página
            "host_popups": None,     # host de menú/filtro (page.overlay), uno por página
            "popup": None,           # {"tipo": "menu" | "filtro", "columna": str} abierto
            "ancla_header": None,    # (x, y) en pantalla del inicio de las columnas de datos del encabezado
            "teclado_registrado": False,  # despachador en page.on_keyboard_event, uno por página
            "al_tecla": None,        # _al_tecla() de la instancia vigente del Registro
        }
        _ESTADOS_UI[id(page)] = ui
    return ui


def _cuentas_no_credito(cuentas: list[dict]) -> list[dict]:
    """Las tarjetas de crédito solo aparecen en Compras en cuotas."""
    return [c for c in cuentas if c["tipo"] != "credito"]


def _clave(texto: str) -> str:
    """Sin tildes, en mayúsculas — para buscar colores de DOTS_CUENTAS por nombre."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")
    return " ".join(sin_tildes.upper().split())


def _montado(page: ft.Page, control: Optional[ft.Control]) -> bool:
    """
    ¿El control está hoy en el árbol de la sesión? Flet no limpia `_parent`
    al sacar un control, así que control.page no alcanza: se consulta el
    índice de la sesión (page.get_control(control._i), mismo uso que el
    ejemplo de su docstring).
    """
    return control is not None and page.get_control(control._i) is control


class _ActualizacionLocal:
    """
    Reemplazo de `page` para CampoFiltrable/CampoMonto dentro del Registro
    (ver docstring del módulo, "Performance"). Esos componentes solo llaman
    a page.update(); acá eso parchea únicamente los controles que devuelve
    `controles()` (la celda o fila donde vive el campo), no la página
    entera. Cualquier otro atributo se delega a la página real.
    """

    def __init__(self, page: ft.Page, controles: Callable[[], list[ft.Control]]):
        self._pagina = page
        self._controles = controles

    def update(self, *controles: ft.Control) -> None:
        montados = [c for c in (controles or self._controles()) if _montado(self._pagina, c)]
        if montados:
            self._pagina.update(*montados)
        else:
            # Nada que parchear: igual se evita el page.update() automático.
            ft.context.disable_auto_update()

    def __getattr__(self, nombre: str) -> Any:
        return getattr(self._pagina, nombre)


class _CampoFiltrableFlotante(CampoFiltrable):
    """
    CampoFiltrable cuya lista de sugerencias NO vive en su propia Column
    (que empuja el layout): se la entrega a `al_mostrar_lista(campo, lista)`
    y el caller la pone en una capa posicionada (capa_sugerencias en
    build()). Sobreescribe solo los dos métodos que ubican la lista —
    filtrado, selección y teclado siguen siendo los de CampoFiltrable, sin
    tocar ui/components/campo_filtrable.py. `al_salir` (opcional) se llama
    después de que el blur terminó de resolverse (para que una celda inline
    vuelva a modo lectura).
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


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    transaction_service: TransactionService,
    shared_expenses_service: SharedExpensesService,
    savings_service: SavingsService,
    debts_service: DebtsService,
    estado: dict,
    on_cambio: Callable[[], None],
) -> ft.Control:
    """
    Args:
        estado:    Dict del dashboard. Se le copian mes/anio del período
                   elegido acá (el estado de interfaz propio vive en
                   _estado_ui(), ver docstring del módulo).
        on_cambio: Ya no se llama: el Registro se refresca solo tras cada
                   cambio de datos (ver docstring del módulo, "Estado de
                   interfaz y refresco parcial"). Queda para no cambiar la
                   firma.
    """
    ui = _estado_ui(page)
    estado["mes"], estado["anio"] = ui["mes"], ui["anio"]

    # ------------------------------------------------------------
    # ACTUALIZACIÓN (ver docstring del módulo, "Performance")
    # ------------------------------------------------------------

    def _refrescar(*controles: Optional[ft.Control]) -> None:
        """Parchea solo estos controles (los que estén montados) — nunca la página entera."""
        montados = [c for c in controles if _montado(page, c)]
        if montados:
            page.update(*montados)
        else:
            ft.context.disable_auto_update()

    def _sin_auto_update() -> None:
        """Handlers que solo guardan estado: sin esto Flet hace un page.update() completo al terminar."""
        ft.context.disable_auto_update()

    def _enfocar(control: Optional[ft.Control]) -> None:
        """focus() es async en Flet 0.86.5: llamarlo sin await no hace nada."""
        if _montado(page, control):
            page.run_task(control.focus)

    # ------------------------------------------------------------
    # MENSAJES
    # ------------------------------------------------------------

    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        # page.overlay + page.update(): ver docstring del módulo (excepción consciente).
        snack = ft.SnackBar(content=ft.Text(mensaje), bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None)
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _mostrar_error(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=False)

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    # ------------------------------------------------------------
    # DATOS
    # ------------------------------------------------------------

    datos: dict[str, Any] = {}

    def _cargar_cuentas() -> None:
        datos["cuentas_por_id"] = {c["id"]: c for c in accounts_service.list_accounts(solo_activas=False)}
        datos["cuentas_activas_todas"] = accounts_service.list_accounts(solo_activas=True)

    _cargar_cuentas()
    cuentas_activas = _cuentas_no_credito(datos["cuentas_activas_todas"])
    cuentas_todas_no_credito = _cuentas_no_credito(list(datos["cuentas_por_id"].values()))
    monedas_por_codigo = {m["codigo"]: dict(m) for m in accounts_service.list_currencies()}

    todas_las_categorias = [dict(c) for c in categorias_service.list_categories()]
    # Normales (ingreso/egreso) + las 3 especiales de routing al final, así
    # la primera opción por default sigue siendo una categoría normal.
    categorias_especiales = [
        c for c in todas_las_categorias
        if (c["categoria_principal"], c["subcategoria"]) in _CATEGORIAS_ROUTING_ESPECIAL
    ]
    categorias = [c for c in todas_las_categorias if c["tipo"] in ("ingreso", "egreso")] + categorias_especiales
    mapa_categoria_a_routing: dict[str, str] = {
        str(c["id"]): _CATEGORIAS_ROUTING_ESPECIAL[(c["categoria_principal"], c["subcategoria"])]
        for c in categorias_especiales
    }
    opciones_cuenta = [(str(c["id"]), c["nombre"]) for c in cuentas_activas]
    opciones_cuenta_edicion = [(str(c["id"]), c["nombre"]) for c in cuentas_todas_no_credito]
    opciones_categoria = [(str(c["id"]), c["subcategoria"]) for c in categorias]

    def _cargar_transacciones() -> None:
        ultimo_dia = calendar.monthrange(ui["anio"], ui["mes"])[1]
        filas = transaction_service.list_transactions(
            date_from=f"{ui['anio']:04d}-{ui['mes']:02d}-01",
            date_to=f"{ui['anio']:04d}-{ui['mes']:02d}-{ultimo_dia:02d}",
            per_page=LIMITE_TRANSACCIONES_DEL_MES,
        )
        datos["transacciones"] = [dict(t) for t in filas]  # CLAUDE.md §11
        datos["compartidos"] = {
            t["id"] for t in datos["transacciones"]
            if shared_expenses_service.get_shared_expense_by_origin("transaccion", t["id"]) is not None
        }

    _cargar_transacciones()

    def _color_cuenta(cuenta: Optional[dict], nombre: str) -> str:
        """Color elegido en Cuentas; si nunca se eligió uno, DOTS_CUENTAS por nombre; si no, el default."""
        color = (cuenta or {}).get("color_hex")
        if color and color.upper() != COLOR_CUENTA_SIN_ELEGIR.upper():
            return color
        return DOTS_CUENTAS.get(_clave(nombre), DOTS_CUENTAS["DEFAULT"])

    def _es_egreso(t: dict) -> bool:
        return t["tipo_movimiento"] == "egreso"

    def _monto_con_signo(t: dict) -> str:
        signo = "-" if _es_egreso(t) else "+"
        return f"{signo} {amount_display(t['monto_minor'], t['decimales'], t['currency_symbol'] or '')}"

    def _valor_columna(t: dict, columna: str) -> str:
        """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
        if columna == "concepto":
            return t["concepto"] or ""
        if columna == "banco":
            return t["account_name"] or ""
        if columna == "categoria":
            return t["category_name"] or ""
        if columna == "monto":
            return _monto_con_signo(t)
        if columna == "fecha":
            return t["fecha"] or ""
        return t["currency_code"] or ""

    def _clave_orden(t: dict, columna: str) -> Any:
        if columna == "monto":
            monto = t["monto_minor"] / (10 ** t["decimales"])
            return -monto if _es_egreso(t) else monto
        return _valor_columna(t, columna).lower()

    def _filas_visibles() -> list[dict]:
        texto = (ui["busqueda"] or "").strip().lower()
        filas = [t for t in datos["transacciones"] if not texto or texto in (t["concepto"] or "").lower()]
        for columna, valores in ui["filtros"].items():
            filas = [t for t in filas if _valor_columna(t, columna) in valores]
        if ui["orden"] is not None:
            columna, ascendente = ui["orden"]
            filas.sort(key=lambda t: _clave_orden(t, columna), reverse=not ascendente)
        return filas

    # ------------------------------------------------------------
    # CONTENEDORES QUE SE PARCHEAN (definidos antes que sus handlers)
    # ------------------------------------------------------------

    contenedor_saldos = ft.Container()
    contenedor_header = ft.Container()
    contenedor_alta = ft.Container()
    tabla_filas = ft.Column(spacing=0)
    # Sugerencias flotantes de CampoFiltrable (ver docstring, "Fila de alta").
    capa_sugerencias = ft.Container(visible=False)
    relleno_tabla = ft.Container(height=0)

    # Filas cacheadas por id de transacción (ver docstring, "refresco parcial").
    cache_filas: dict[int, dict] = {}
    mostradas: dict[str, list[int]] = {"ids": []}  # ids en pantalla, en orden

    # Celdas por columna del encabezado y de la fila de alta — el drag les
    # cambia el `expand` en vivo (las filas de datos, al soltar).
    celdas_header: dict[str, ft.Control] = {}
    celdas_alta: dict[str, ft.Control] = {}

    # Barra flotante y host de popups: uno por página, en page.overlay
    # (la barra primero, así los popups quedan encima de ella).
    if ui["barra"] is None:
        ui["barra"] = ft.Container(
            left=BARRA_MARGEN_IZQ, right=BARRA_MARGEN_DER, bottom=BARRA_MARGEN_INF,
            height=ALTURA_BARRA_FLOT,
            bgcolor=BG_BARRA_FLOT,
            border=ft.Border.only(top=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_BARRA)),
            border_radius=0,
            padding=ft.Padding.symmetric(horizontal=PADDING_BARRA_H),
            visible=False,
        )
        page.overlay.append(ui["barra"])
    if ui["host_popups"] is None:
        ui["host_popups"] = ft.Container(left=0, top=0, right=0, bottom=0, visible=False)
        page.overlay.append(ui["host_popups"])
    barra = ui["barra"]
    host_popups = ui["host_popups"]

    # ------------------------------------------------------------
    # CAMBIOS DE DATOS
    # ------------------------------------------------------------

    alta_refs: dict[str, Any] = {}

    def _guardar_borrador_alta() -> None:
        if not alta_refs:
            return
        ui["alta"] = {
            "concepto": alta_refs["concepto"].value or "",
            "cuenta_id": alta_refs["cuenta"].id_seleccionado,
            "categoria_id": alta_refs["categoria"].id_seleccionado,
            "monto": alta_refs["monto"].texto,
            "fecha": alta_refs["fecha"].value or "",
            "moneda": alta_refs["moneda"].value,
        }

    def _datos_cambiaron() -> None:
        """Hubo un cambio de datos: recarga y re-dibuja solo lo afectado (ver docstring, refresco parcial)."""
        _guardar_borrador_alta()
        _cerrar_popups()
        _ocultar_sugerencias()
        _cargar_cuentas()
        _cargar_transacciones()
        contenedor_saldos.content = _barra_saldos()
        _redibujar_tabla()
        _refrescar(contenedor_saldos, contenedor_header, tabla_filas, capa_sugerencias, relleno_tabla, barra)

    def _alta_ok() -> None:
        """Una alta terminó bien: la fila de alta vuelve a sus valores default, con el foco en Concepto."""
        ui["alta"] = _alta_vacia()
        contenedor_alta.content = _construir_fila_alta()
        _datos_cambiaron()
        _refrescar(contenedor_alta)
        _enfocar(alta_refs["concepto"])

    # ------------------------------------------------------------
    # ANCHOS DE COLUMNA (ver docstring, "Columnas")
    # ------------------------------------------------------------

    def _suma_anchos() -> float:
        return sum(ui["anchos"][c] for c in CLAVES_COLUMNAS)

    def _escala() -> float:
        """px por unidad de ui["anchos"] (1 hasta que on_size_change informe el ancho real)."""
        util, suma = ui["ancho_util"], _suma_anchos()
        return util / suma if util and suma else 1.0

    def _px(columna: str) -> float:
        return ui["anchos"][columna] * _escala()

    def _x_rel(columna: str) -> float:
        """px desde el inicio de las columnas de datos (después del checkbox) hasta el borde izquierdo de `columna`."""
        x = 0.0
        for c in CLAVES_COLUMNAS:
            if c == columna:
                break
            x += _px(c)
        return x

    def _minimo(columna: str) -> float:
        """ANCHOS_MIN pasado a unidades de ui["anchos"]."""
        return ANCHOS_MIN[columna] / _escala()

    def _flex(columna: str) -> int:
        return max(1, round(ui["anchos"][columna] * FACTOR_FLEX))

    def _ancho_handle(columna: str) -> int:
        """La última columna no tiene handle: su borde derecho es el de la tabla."""
        return 0 if columna == CLAVES_COLUMNAS[-1] else ANCHO_RESIZE_HANDLE

    def _aplicar_anchos(celdas: dict[str, ft.Control]) -> None:
        for columna, celda in celdas.items():
            celda.expand = _flex(columna)

    def _guardar_anchos() -> None:
        escribir_pref(PREF_ANCHOS_COLUMNAS, {c: round(ui["anchos"][c], 2) for c in CLAVES_COLUMNAS})

    def _anchos_cambiaron() -> None:
        """Aplica ui["anchos"] a encabezado, alta y todas las filas; guarda y parchea."""
        _aplicar_anchos(celdas_header)
        _aplicar_anchos(celdas_alta)
        for ref in cache_filas.values():
            _aplicar_anchos(ref["celdas"])
        _guardar_anchos()
        _refrescar(contenedor_header, contenedor_alta, tabla_filas)

    def _redimensionar(columna: str, delta_px: float) -> None:
        """Drag del borde derecho de `columna`: la vecina de la derecha absorbe o cede el diferencial."""
        indice = CLAVES_COLUMNAS.index(columna)
        if indice + 1 >= len(CLAVES_COLUMNAS):
            return
        vecina = CLAVES_COLUMNAS[indice + 1]
        anchos = ui["anchos"]
        delta = delta_px / _escala()
        # Ninguna de las dos baja de su mínimo; si una ya está por debajo
        # (ventana muy angosta), solo puede crecer.
        delta = max(delta, min(0.0, _minimo(columna) - anchos[columna]))
        delta = min(delta, max(0.0, anchos[vecina] - _minimo(vecina)))
        if not delta:
            return
        anchos[columna] += delta
        anchos[vecina] -= delta
        for celdas in (celdas_header, celdas_alta):
            for c in (columna, vecina):
                if c in celdas:
                    celdas[c].expand = _flex(c)
        _refrescar(contenedor_header, contenedor_alta)

    def _repartir(proporciones: dict[str, float], total: float) -> dict[str, float]:
        """Escala `proporciones` para que sumen `total` sin dejar ninguna columna por debajo de su mínimo."""
        resultado: dict[str, float] = {}
        libres = dict(proporciones)
        restante = total
        while libres:
            suma = sum(libres.values())
            if restante <= 0 or suma <= 0:
                resultado.update({c: _minimo(c) for c in libres})
                break
            escaladas = {c: v * restante / suma for c, v in libres.items()}
            debajo = [c for c, v in escaladas.items() if v < _minimo(c)]
            if not debajo:
                resultado.update(escaladas)
                break
            for c in debajo:
                resultado[c] = _minimo(c)
                restante -= _minimo(c)
                del libres[c]
        return resultado

    def _ancho_contenido_px(columna: str) -> float:
        titulo = dict(COLUMNAS)[columna]
        textos = [_valor_columna(t, columna) for t in _filas_visibles()] + [titulo]
        extra = DOT_SIZE + ESPACIO_DOT if columna == "banco" else 0
        return max(len(t) for t in textos) * ANCHO_POR_CARACTER + PADDING_AJUSTE + extra

    def _ajustar_al_contenido(columna: str) -> None:
        total = _suma_anchos()
        otras = [c for c in CLAVES_COLUMNAS if c != columna]
        maximo = total - sum(_minimo(c) for c in otras)
        objetivo = max(_minimo(columna), min(_ancho_contenido_px(columna) / _escala(), maximo))
        ui["anchos"].update(_repartir({c: ui["anchos"][c] for c in otras}, total - objetivo))
        ui["anchos"][columna] = objetivo
        _anchos_cambiaron()

    def _ajustar_todas() -> None:
        ui["anchos"].update(_repartir({c: _ancho_contenido_px(c) for c in CLAVES_COLUMNAS}, _suma_anchos()))
        _anchos_cambiaron()

    def _on_tamanio_tabla(e: ft.LayoutSizeChangeEvent) -> None:
        # Solo se anota (el flex ya llena el ancho solo) — sin auto-update.
        _sin_auto_update()
        ui["ancho_util"] = max(1.0, e.width - 2 * ANCHO_BORDE - ANCHO_COL_CHECK - ANCHO_COL_ACCION)

    def _handle_resize(columna: str) -> ft.Control:
        linea = ft.Container(width=ANCHO_LINEA_RESIZE, height=ALTURA_HEADER, bgcolor=TEXT_ACCENT, visible=False)
        arrastrando = {"activo": False}

        def _ver_linea(visible: bool) -> None:
            linea.visible = visible or arrastrando["activo"]
            _refrescar(linea)

        def _arrastrar(e: ft.DragUpdateEvent) -> None:
            _sin_auto_update()
            arrastrando["activo"] = True
            linea.visible = True
            delta = e.primary_delta
            if delta is None:
                delta = e.local_delta.x if e.local_delta else 0
            _redimensionar(columna, delta)

        def _soltar(e=None) -> None:
            arrastrando["activo"] = False
            linea.visible = False
            # Recién ahora las filas de datos: ver docstring, "Columnas".
            for ref in cache_filas.values():
                _aplicar_anchos(ref["celdas"])
            _guardar_anchos()
            _refrescar(linea, tabla_filas)

        return ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.RESIZE_COLUMN,
            drag_interval=RESIZE_DRAG_INTERVAL_MS,
            on_enter=lambda e: _ver_linea(True),
            on_exit=lambda e: _ver_linea(False),
            on_horizontal_drag_update=_arrastrar,
            on_horizontal_drag_end=_soltar,
            content=ft.Container(
                width=ANCHO_RESIZE_HANDLE, height=ALTURA_HEADER, alignment=ft.Alignment.CENTER, content=linea,
            ),
        )

    # ------------------------------------------------------------
    # POPUPS (host en page.overlay — ver docstring, "Popups")
    # ------------------------------------------------------------

    def _cerrar_popups() -> None:
        if ui["popup"] is None:
            return
        ui["popup"] = None
        host_popups.visible = False
        host_popups.content = None
        _refrescar(host_popups)

    def _mostrar_popup(popup: ft.Control, tipo: str, columna: str) -> None:
        capa = ft.GestureDetector(
            left=0, top=0, right=0, bottom=0,
            on_tap_down=_click_en_capa,
            on_secondary_tap_down=_click_derecho_en_capa,
            content=ft.Container(bgcolor=COLOR_CAPA),
        )
        ui["popup"] = {"tipo": tipo, "columna": columna}
        host_popups.content = ft.Stack([capa, popup])
        host_popups.visible = True
        _refrescar(host_popups)

    def _posicion_popup(e: ft.TapEvent, ancho_popup: int) -> tuple[float, float]:
        x = e.global_position.x if e.global_position else MARGEN_POPUP
        y = e.global_position.y if e.global_position else MARGEN_POPUP
        ancho_ventana = getattr(page, "width", None)
        if ancho_ventana and x + ancho_popup + MARGEN_POPUP > ancho_ventana:
            x = max(MARGEN_POPUP, ancho_ventana - ancho_popup - MARGEN_POPUP)
        return x, y + MARGEN_POPUP

    def _anclar_en_celda(columna: str, e: ft.TapEvent) -> None:
        """Click derecho en la celda del encabezado: local_position es relativa a la celda."""
        if e.global_position is None or e.local_position is None:
            return
        izquierda_celda = e.global_position.x - e.local_position.x
        ui["ancla_header"] = (izquierda_celda - _x_rel(columna), e.global_position.y - e.local_position.y)

    def _anclar_en_icono_filtro(columna: str, e: ft.TapEvent) -> None:
        """Click en ▼: local_position es relativa al ícono, pegado al borde derecho de la celda (antes del handle)."""
        if e.global_position is None or e.local_position is None:
            return
        fin_celda = e.global_position.x - e.local_position.x + ICONO_HEADER + _ancho_handle(columna) + ANCHO_BORDE
        arriba_icono = e.global_position.y - e.local_position.y
        ui["ancla_header"] = (
            fin_celda - _px(columna) - _x_rel(columna),
            arriba_icono - (ALTURA_HEADER - ICONO_HEADER) / 2,
        )

    def _columna_bajo_puntero(posicion: Optional[ft.Offset], zona_filtro: bool) -> Optional[str]:
        """Columna del encabezado bajo `posicion` (en pantalla); con zona_filtro, solo si cae sobre su ▼."""
        ancla = ui["ancla_header"]
        if posicion is None or ancla is None or ui["ancho_util"] is None:
            return None
        x_inicio, y_header = ancla
        if not (y_header <= posicion.y <= y_header + ALTURA_HEADER):
            return None
        for columna in CLAVES_COLUMNAS:
            x_fin = x_inicio + _px(columna)
            if x_inicio <= posicion.x < x_fin:
                if not zona_filtro:
                    return columna
                borde_icono = x_fin - ANCHO_BORDE - _ancho_handle(columna)
                return columna if borde_icono - ZONA_ICONO_FILTRO <= posicion.x <= borde_icono else None
            x_inicio = x_fin
        return None

    def _click_en_capa(e: ft.TapEvent) -> None:
        anterior = ui["popup"]
        _cerrar_popups()
        columna = _columna_bajo_puntero(e.global_position, zona_filtro=True)
        # Click en el ▼ del mismo filtro abierto = solo cerrarlo.
        if columna is None or (anterior and anterior["tipo"] == "filtro" and anterior["columna"] == columna):
            return
        x, y = _posicion_popup(e, ANCHO_OVERLAY_FILTRO)
        _abrir_filtro(columna, x, y)

    def _click_derecho_en_capa(e: ft.TapEvent) -> None:
        _cerrar_popups()
        columna = _columna_bajo_puntero(e.global_position, zona_filtro=False)
        if columna is not None:
            _abrir_menu_columna(columna, e)

    # ------------------------------------------------------------
    # MENÚ CONTEXTUAL DEL ENCABEZADO
    # ------------------------------------------------------------

    def _item_menu(icono: str, texto: str, on_click: Callable[[], None]) -> ft.Control:
        item = ft.Container(
            height=ALTURA_ITEM_MENU,
            padding=ft.Padding.symmetric(horizontal=PADDING_ITEM_MENU_H),
            alignment=ft.Alignment.CENTER_LEFT,
            content=ft.Row(
                [
                    ft.Icon(icono, size=ICONO_MENU, color=TEXT_SECONDARY),
                    ft.Text(texto, size=TypographyTokens.REGISTRO_FONT_OVERLAY, color=TEXT_PRIMARY),
                ],
                spacing=ESPACIADO,
            ),
        )

        def _hover(e: ft.ControlEvent) -> None:
            item.bgcolor = BG_ITEM_HOVER if str(e.data).lower() == "true" else None
            _refrescar(item)

        def _click(e=None) -> None:
            _cerrar_popups()
            on_click()

        item.on_hover = _hover
        item.on_click = _click
        return item

    def _separador_menu() -> ft.Control:
        return ft.Container(
            height=ANCHO_BORDE, bgcolor=BORDER_DEFAULT, margin=ft.Margin.symmetric(vertical=PADDING_MENU_V),
        )

    def _ordenar(columna: str, ascendente: bool) -> None:
        ui["orden"] = (columna, ascendente)
        _redibujar_tabla()
        _refrescar(contenedor_header, tabla_filas, barra)

    def _abrir_menu_columna(columna: str, e: ft.TapEvent) -> None:
        x, y = _posicion_popup(e, ANCHO_MENU_CTX)
        menu = ft.Container(
            left=x, top=y, width=ANCHO_MENU_CTX,
            bgcolor=BG_MENU_CTX,
            border=ft.Border.all(ANCHO_BORDE, BORDER_OVERLAY),
            border_radius=RADIO_OVERLAY,
            padding=ft.Padding.symmetric(vertical=PADDING_MENU_V),
            content=ft.Column(
                [
                    _item_menu(ft.Icons.WIDTH_NORMAL, "AJUSTAR AL CONTENIDO", lambda: _ajustar_al_contenido(columna)),
                    _item_menu(ft.Icons.VIEW_COLUMN_OUTLINED, "AJUSTAR TODAS LAS COLUMNAS", _ajustar_todas),
                    _separador_menu(),
                    _item_menu(ft.Icons.ARROW_UPWARD, "ORDENAR A → Z", lambda: _ordenar(columna, True)),
                    _item_menu(ft.Icons.ARROW_DOWNWARD, "ORDENAR Z → A", lambda: _ordenar(columna, False)),
                    _separador_menu(),
                    _item_menu(ft.Icons.FILTER_ALT_OUTLINED, "FILTRAR...", lambda: _abrir_filtro(columna, x, y)),
                ],
                spacing=0,
                tight=True,
            ),
        )
        _mostrar_popup(menu, "menu", columna)

    # ------------------------------------------------------------
    # OVERLAY DE FILTRO POR COLUMNA
    # ------------------------------------------------------------

    def _abrir_filtro(columna: str, x: float, y: float) -> None:
        valores = sorted({_valor_columna(t, columna) for t in datos["transacciones"]}, key=str.lower)
        activos = ui["filtros"].get(columna)
        marcados = set(activos) if activos is not None else set(valores)
        texto_busqueda = {"valor": ""}
        lista = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO)

        def _toggle(valor: str, marcado: bool) -> None:
            # El checkbox ya cambió del lado del cliente: nada que parchear.
            _sin_auto_update()
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
            _refrescar(lista)

        def _aplicar(e=None) -> None:
            if marcados >= set(valores):
                ui["filtros"].pop(columna, None)
            else:
                ui["filtros"][columna] = set(marcados)
            _cerrar_popups()
            _redibujar_tabla()
            _refrescar(contenedor_header, tabla_filas, barra)

        def _limpiar(e=None) -> None:
            ui["filtros"].pop(columna, None)
            _cerrar_popups()
            _redibujar_tabla()
            _refrescar(contenedor_header, tabla_filas, barra)

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
                    ft.TextField(
                        hint_text="BUSCAR…", autofocus=True, on_change=_buscar,
                        **_estilo_campo(),
                    ),
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
        _mostrar_popup(overlay, "filtro", columna)

    # ------------------------------------------------------------
    # SUGERENCIAS FLOTANTES DE CampoFiltrable (ver docstring, "Fila de alta")
    # ------------------------------------------------------------

    sugerencias: dict[str, Any] = {"duenio": None}

    def _alto_cuerpo() -> float:
        filas = len(mostradas["ids"])
        return ALTURA_HEADER + ALTURA_FILA_ALTA + (filas * ALTURA_FILA if filas else ALTURA_VACIO)

    def _mostrar_sugerencias(duenio: Any, lista: ft.Container, x: float, y: float, ancho: float) -> None:
        sugerencias["duenio"] = duenio
        capa_sugerencias.left = x
        capa_sugerencias.top = y
        capa_sugerencias.width = max(ancho, ANCHO_MINIMO_SUGERENCIAS)
        capa_sugerencias.content = lista
        capa_sugerencias.visible = True
        # La tabla se estira lo justo para que la lista quede entera adentro del Stack.
        relleno_tabla.height = max(0.0, y + (lista.height or ALTURA_MAX_LISTA) - _alto_cuerpo())

    def _ocultar_sugerencias(duenio: Any = None) -> None:
        """Sin `duenio` cierra la lista abierta, sea de quien sea."""
        if duenio is not None and sugerencias["duenio"] is not duenio:
            return
        sugerencias["duenio"] = None
        capa_sugerencias.visible = False
        capa_sugerencias.content = None
        relleno_tabla.height = 0

    # ------------------------------------------------------------
    # ESTILOS COMPARTIDOS
    # ------------------------------------------------------------

    def _estilo_campo(tamanio: int = TypographyTokens.REGISTRO_FONT_CELDA) -> dict:
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

    def _sin_borde(campo: ft.TextField) -> None:
        """Mismo estilo que _estilo_campo() sobre un TextField ya creado (el de CampoFiltrable/CampoMonto)."""
        for atributo, valor in _estilo_campo().items():
            if atributo not in ("dense", "text_size"):
                setattr(campo, atributo, valor)

    def _campo_filtrable_tabla(
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
            al_mostrar_lista=lambda duenio, lista: _mostrar_sugerencias(duenio, lista, *posicion()),
            al_ocultar_lista=_ocultar_sugerencias,
            al_salir=al_salir,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            **kwargs,
        )
        _sin_borde(campo.campo_texto)
        campo.campo_texto.text_align = ft.TextAlign.CENTER
        return campo

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

    def _texto_celda(texto: str, color: str = TEXT_PRIMARY, size: int = TypographyTokens.REGISTRO_FONT_CELDA,
                     weight=PESO_CELDA, align: Optional[ft.TextAlign] = None) -> ft.Text:
        """1 línea, ellipsis si no entra, tooltip con el texto completo."""
        texto_control = ft.Text(
            texto, color=color, size=size, weight=weight, max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS, tooltip=texto or None,
        )
        if align is not None:
            texto_control.text_align = align
        return texto_control

    def _dot(color: str, tamanio: int = DOT_SIZE) -> ft.Control:
        return ft.Container(width=tamanio, height=tamanio, border_radius=tamanio / 2, bgcolor=color)

    # ------------------------------------------------------------
    # A. BARRA DE TÍTULO: búsqueda + período
    # ------------------------------------------------------------
    # Buscador y selector de mes: mismo alto y mismo tamaño de texto que la
    # barra de saldo (ver ALTURA_CABECERA).

    texto_periodo = ft.Text(size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, color=TEXT_PRIMARY)

    def _actualizar_texto_periodo() -> None:
        ultimo_dia = calendar.monthrange(ui["anio"], ui["mes"])[1]
        texto_periodo.value = (
            f"{ui['anio']:04d}-{ui['mes']:02d}-01  →  {ui['anio']:04d}-{ui['mes']:02d}-{ultimo_dia:02d}"
        )

    def _cambiar_mes(delta: int) -> None:
        indice = ui["anio"] * 12 + (ui["mes"] - 1) + delta
        ui["anio"], ui["mes"] = indice // 12, indice % 12 + 1
        estado["mes"], estado["anio"] = ui["mes"], ui["anio"]
        # Una selección de otro mes quedaría invisible: se descarta.
        ui["seleccion"].clear()
        ui["confirmando_eliminar"] = False
        _cargar_transacciones()
        _actualizar_texto_periodo()
        _redibujar_tabla()
        _refrescar(texto_periodo, contenedor_header, tabla_filas, barra)

    def _on_busqueda(e: ft.ControlEvent) -> None:
        ui["busqueda"] = e.control.value or ""
        _redibujar_tabla()
        _refrescar(contenedor_header, tabla_filas, barra)

    _actualizar_texto_periodo()
    barra_titulo = ft.Container(
        height=ALTURA_BARRA_TITULO,
        content=ft.Row(
            [
                # Reemplaza al título de página que tenía el dashboard: mismo tamaño.
                ft.Text(
                    "REGISTRO DE TRANSACCIONES", size=TypographyTokens.PAGE_TITLE_SIZE,
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
                                value=ui["busqueda"], hint_text="BUSCAR EN EL REGISTRO…",
                                on_change=_on_busqueda, expand=True,
                                **_estilo_campo(TypographyTokens.REGISTRO_FONT_SALDO_BAR),
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

    # ------------------------------------------------------------
    # B. BARRA DE SALDO POR CUENTA
    # ------------------------------------------------------------

    def _elegir_moneda_saldo(codigo: str) -> None:
        ui["moneda_saldo"] = codigo
        contenedor_saldos.content = _barra_saldos()
        _refrescar(contenedor_saldos)

    def _barra_saldos() -> ft.Control:
        codigo_sel = ui["moneda_saldo"]
        moneda_sel = monedas_por_codigo.get(codigo_sel, {})
        cuentas = datos["cuentas_activas_todas"]
        chips: list[ft.Control] = []
        for cuenta in cuentas:
            saldo = next((s for s in cuenta["saldos"] if s["moneda_codigo"] == codigo_sel), None)
            if saldo is None or saldo["saldo_minor"] == 0:
                continue
            negativo = saldo["saldo_minor"] < 0
            monto = amount_display(abs(saldo["saldo_minor"]), moneda_sel.get("decimales", 2), saldo["moneda_simbolo"] or "")
            chips.append(
                ft.Row(
                    [
                        _dot(_color_cuenta(cuenta, cuenta["nombre"]), DOT_SIZE_SALDO),
                        ft.Text(cuenta["nombre"], size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, color=TEXT_SECONDARY),
                        ft.Text(
                            f"{'-' if negativo else ''}{monto}", size=TypographyTokens.REGISTRO_FONT_SALDO_BAR,
                            weight=PESO_MONTO, color=TEXT_NEGATIVO if negativo else TEXT_POSITIVO,
                        ),
                        ft.Text(codigo_sel, size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, color=TEXT_MUTED),
                    ],
                    spacing=ESPACIO_DOT,
                )
            )
        if not chips:
            chips = [
                ft.Text(
                    f"SIN SALDOS EN {codigo_sel}", size=TypographyTokens.REGISTRO_FONT_SALDO_BAR,
                    color=TEXT_MUTED, italic=True,
                )
            ]

        # Monedas con algún saldo distinto de cero (ARS primero) + la elegida.
        codigos = {s["moneda_codigo"] for c in cuentas for s in c["saldos"] if s["saldo_minor"] != 0} | {codigo_sel}
        pills = []
        for codigo in sorted(codigos, key=lambda c: (c != MONEDA_DEFAULT, c)):
            activa = codigo == codigo_sel
            pills.append(
                ft.Container(
                    height=PILL_ALTURA,
                    padding=ft.Padding.symmetric(horizontal=PILL_PADDING_H),
                    border_radius=PILL_RADIO,
                    bgcolor=BTN_COMPARTIR if activa else None,
                    border=None if activa else ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
                    alignment=ft.Alignment.CENTER,
                    on_click=lambda e, c=codigo: _elegir_moneda_saldo(c),
                    content=ft.Text(
                        codigo, size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, weight=PESO_HEADER,
                        color=TEXT_SOBRE_BOTON if activa else TEXT_SECONDARY,
                    ),
                )
            )

        return ft.Container(
            height=ALTURA_BARRA_SALDO,
            bgcolor=BG_SUPERFICIE,
            border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
            border_radius=RADIO_TABLA,
            padding=ft.Padding.symmetric(horizontal=PADDING_SALDO_H, vertical=PADDING_SALDO_V),
            clip_behavior=ft.ClipBehavior.NONE,
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED, size=ICONO_CABECERA, color=TEXT_SECONDARY),
                    ft.Text(
                        "SALDO POR CUENTA", size=TypographyTokens.REGISTRO_FONT_SALDO_BAR,
                        weight=TypographyTokens.SECTION_TITLE_WEIGHT, color=TEXT_PRIMARY,
                    ),
                    ft.Row(
                        [
                            ft.Container(
                                padding=ft.Padding.symmetric(vertical=ESPACIO_SCROLLBAR_SALDO),
                                content=ft.Row(chips, spacing=ESPACIO_CHIPS_SALDO),
                            ),
                        ],
                        scroll=ft.ScrollMode.AUTO,
                        expand=True,
                    ),
                    ft.Row(pills, spacing=ESPACIO_DOT),
                ],
                spacing=ESPACIO_CHIPS_SALDO,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # ------------------------------------------------------------
    # C. ENCABEZADOS DE COLUMNA
    # ------------------------------------------------------------

    checkbox_todas = ft.Checkbox(value=False, active_color=TEXT_ACCENT)

    def _todas_seleccionadas() -> bool:
        return bool(mostradas["ids"]) and all(i in ui["seleccion"] for i in mostradas["ids"])

    def _on_checkbox_todas(e: ft.ControlEvent) -> None:
        if e.control.value:
            ui["seleccion"] = set(mostradas["ids"])
        else:
            ui["seleccion"].clear()
        ui["confirmando_eliminar"] = False
        _redibujar_tabla()
        _refrescar(contenedor_header, tabla_filas, barra)

    checkbox_todas.on_change = _on_checkbox_todas

    def _celda_header(columna: str, titulo: str) -> ft.Control:
        filtro_activo = columna in ui["filtros"]
        icono_filtro = ft.Icon(
            ft.Icons.FILTER_ALT if filtro_activo else ft.Icons.ARROW_DROP_DOWN,
            size=ICONO_HEADER,
            color=TEXT_ACCENT if filtro_activo else TEXT_MUTED,
        )

        def _hover_filtro(activo: bool) -> None:
            icono_filtro.color = TEXT_ACCENT if (activo or filtro_activo) else TEXT_MUTED
            _refrescar(icono_filtro)

        def _click_filtro(e: ft.TapEvent) -> None:
            _anclar_en_icono_filtro(columna, e)
            x, y = _posicion_popup(e, ANCHO_OVERLAY_FILTRO)
            _abrir_filtro(columna, x, y)

        def _click_derecho(e: ft.TapEvent) -> None:
            _anclar_en_celda(columna, e)
            _abrir_menu_columna(columna, e)

        ordenada = ui["orden"] is not None and ui["orden"][0] == columna
        partes: list[ft.Control] = [
            ft.Container(
                expand=True,
                alignment=ft.Alignment.CENTER,
                content=ft.Text(
                    titulo, size=TypographyTokens.REGISTRO_FONT_HEADER, weight=PESO_HEADER, color=TEXT_SECONDARY,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                ),
            ),
        ]
        # El título se centra en la celda entera, no solo en el espacio que
        # le dejan los íconos: a la izquierda va el mismo ancho que ocupan
        # a la derecha (flecha de orden, ▼, handle y borde).
        ancho_iconos = (ICONO_MENU if ordenada else 0) + ICONO_HEADER + _ancho_handle(columna) + ANCHO_BORDE
        if ordenada:
            partes.append(
                ft.Icon(
                    ft.Icons.ARROW_UPWARD if ui["orden"][1] else ft.Icons.ARROW_DOWNWARD,
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
        if _ancho_handle(columna):
            partes.append(_handle_resize(columna))

        celda = ft.GestureDetector(
            expand=_flex(columna),
            on_secondary_tap_down=_click_derecho,
            content=ft.Container(
                height=ALTURA_HEADER,
                padding=ft.Padding.only(left=ancho_iconos),
                border=ft.Border.only(right=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
                content=ft.Row(partes, spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ),
        )
        celdas_header[columna] = celda
        return celda

    def _dibujar_header() -> None:
        checkbox_todas.value = _todas_seleccionadas()
        celdas_header.clear()
        contenedor_header.content = ft.Container(
            height=ALTURA_HEADER,
            bgcolor=BG_ENCABEZADO,
            border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
            content=ft.Row(
                [
                    ft.Container(
                        width=ANCHO_COL_CHECK, height=ALTURA_HEADER, alignment=ft.Alignment.CENTER,
                        border=ft.Border.only(right=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
                        content=checkbox_todas,
                    ),
                    *[_celda_header(columna, titulo) for columna, titulo in COLUMNAS],
                    ft.Container(width=ANCHO_COL_ACCION),
                ],
                spacing=0,
            ),
        )

    # ------------------------------------------------------------
    # D. FILA DE ALTA
    # ------------------------------------------------------------

    def _celda_alta(columna: str, contenido: ft.Control) -> ft.Container:
        """Alto fijo + recorte: todas las celdas del alta miden lo mismo (el borde lo da la celda)."""
        celda = ft.Container(
            expand=_flex(columna),
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
        celdas_alta[columna] = celda
        return celda

    def _posicion_lista_alta(columna: str) -> tuple[float, float, float]:
        """(x, y, ancho) de las sugerencias de una celda del alta, relativo a la tabla: justo debajo de la celda."""
        return (
            ANCHO_COL_CHECK + _x_rel(columna) + ALTA_PADDING,
            ALTURA_HEADER + ALTA_PADDING + ALTURA_FILA,
            _px(columna) - 2 * ALTA_PADDING,
        )

    def _construir_fila_alta() -> ft.Control:
        borrador = ui["alta"]
        cuenta_inicial = borrador["cuenta_id"] or (opciones_cuenta[0][0] if opciones_cuenta else None)
        categoria_inicial = borrador["categoria_id"] or (opciones_categoria[0][0] if opciones_categoria else None)
        pagina_alta = _ActualizacionLocal(page, lambda: [contenedor_alta, capa_sugerencias, relleno_tabla])
        celdas_alta.clear()

        campo_concepto = ft.TextField(
            value=borrador["concepto"], hint_text="EJ: SUPERMERCADO", autofocus=True,
            text_align=ft.TextAlign.CENTER, **_estilo_campo(),
        )
        dropdown_moneda = ft.Dropdown(
            options=[], dense=LayoutTokens.CELDA_DENSE, text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            border=ft.InputBorder.NONE, color=TEXT_PRIMARY, expand=True, text_align=ft.TextAlign.CENTER,
        )

        def _refrescar_monedas(cuenta_id: Optional[str], preferida: Optional[str] = None) -> None:
            cuentas_por_id = datos["cuentas_por_id"]
            saldos = cuentas_por_id[int(cuenta_id)]["saldos"] if cuenta_id and int(cuenta_id) in cuentas_por_id else []
            codigos = [s["moneda_codigo"] for s in saldos] or [MONEDA_DEFAULT]
            dropdown_moneda.options = [ft.dropdown.Option(key=c, text=c) for c in codigos]
            dropdown_moneda.value = preferida if preferida in codigos else codigos[0]

        def _on_cuenta(id_cuenta: Optional[str]) -> None:
            # CampoFiltrable parchea la fila (pagina_alta) justo después.
            if id_cuenta is not None:
                _refrescar_monedas(id_cuenta)
            _guardar_borrador_alta()

        campo_categoria = _campo_filtrable_tabla(
            pagina_alta, opciones_categoria, lambda id_: _guardar_borrador_alta(),
            posicion=lambda: _posicion_lista_alta("categoria"),
            placeholder="CATEGORÍA", valor_inicial_id=categoria_inicial,
            on_avanzar=lambda: _enfocar(campo_monto.control),
        )
        campo_cuenta = _campo_filtrable_tabla(
            pagina_alta, opciones_cuenta, _on_cuenta,
            posicion=lambda: _posicion_lista_alta("banco"),
            placeholder="BANCO", valor_inicial_id=cuenta_inicial,
            on_avanzar=lambda: _enfocar(campo_categoria.campo_texto),
        )

        # persistir_formula=True (pedido explícito): el campo recuerda la
        # fórmula mientras la fila no se guarde. on_confirmar solo guarda el
        # borrador — la fila entera confirma junta (Enter o ✓).
        campo_monto = CampoMonto(
            pagina_alta,
            on_confirmar=lambda monto_minor: _guardar_borrador_alta(),
            persistir_formula=True,
            hint_text=HINT_MONTO_ALTA,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            on_avanzar=lambda: _confirmar_alta(),
        )
        _sin_borde(campo_monto.control)
        campo_monto.control.text_align = ft.TextAlign.RIGHT
        if borrador["monto"]:
            campo_monto.control.value = borrador["monto"]

        campo_fecha = ft.TextField(
            value=borrador["fecha"] or date.today().isoformat(), hint_text="AAAA-MM-DD",
            expand=True, text_align=ft.TextAlign.CENTER, **_estilo_campo(),
        )
        _refrescar_monedas(cuenta_inicial, preferida=borrador["moneda"])

        def _abrir_calendario(e=None) -> None:
            try:
                actual = datetime.strptime((campo_fecha.value or "").strip(), "%Y-%m-%d")
            except ValueError:
                actual = datetime.now()

            def _elegida(ev: ft.ControlEvent) -> None:
                valor = selector.value
                if valor is None:
                    _sin_auto_update()
                    return
                campo_fecha.value = valor[:10] if isinstance(valor, str) else valor.strftime("%Y-%m-%d")
                _guardar_borrador_alta()
                _refrescar(campo_fecha)

            selector = ft.DatePicker(value=actual, on_change=_elegida)
            page.show_dialog(selector)

        boton_confirmar = ft.IconButton(
            icon=ft.Icons.CHECK, icon_color=TEXT_ACCENT, tooltip="AGREGAR MOVIMIENTO",
        )

        # Si el foco está en Moneda, Tab/Enter confirman la fila (ver _al_tecla()).
        foco_moneda = {"activo": False}

        alta_refs.update(
            concepto=campo_concepto, cuenta=campo_cuenta, categoria=campo_categoria,
            monto=campo_monto, fecha=campo_fecha, moneda=dropdown_moneda, boton=boton_confirmar,
            foco_moneda=foco_moneda,
        )

        def _on_cambio_borrador(e=None) -> None:
            # Solo guarda el borrador: el texto ya está en pantalla.
            _guardar_borrador_alta()
            _sin_auto_update()

        def _on_foco_moneda(activo: bool) -> None:
            foco_moneda["activo"] = activo
            _sin_auto_update()

        # Enter confirma (Concepto, Fecha; Monto vía on_avanzar; Moneda vía
        # _al_tecla(), igual que Tab). Tab en el resto de los campos es nativo.
        campo_concepto.on_submit = lambda e: _confirmar_alta()
        campo_concepto.on_change = _on_cambio_borrador
        campo_fecha.on_submit = lambda e: _confirmar_alta()
        campo_fecha.on_change = _on_cambio_borrador
        dropdown_moneda.on_select = _on_cambio_borrador
        dropdown_moneda.on_focus = lambda e: _on_foco_moneda(True)
        dropdown_moneda.on_blur = lambda e: _on_foco_moneda(False)
        boton_confirmar.on_click = lambda e: _confirmar_alta()

        return ft.Container(
            bgcolor=BG_FILA_ALTA,
            padding=ft.Padding.symmetric(vertical=ALTA_PADDING),
            border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=TEXT_ACCENT)),
            content=ft.Row(
                [
                    ft.Container(width=ANCHO_COL_CHECK),
                    _celda_alta("concepto", campo_concepto),
                    _celda_alta("banco", campo_cuenta.control),
                    _celda_alta("categoria", campo_categoria.control),
                    _celda_alta("monto", campo_monto.control),
                    _celda_alta(
                        "fecha",
                        ft.Row(
                            [
                                campo_fecha,
                                ft.IconButton(
                                    icon=ft.Icons.CALENDAR_MONTH_OUTLINED, icon_size=ICONO_CALENDARIO,
                                    icon_color=TEXT_SECONDARY, width=ANCHO_BOTON_CALENDARIO,
                                    tooltip="ELEGIR FECHA", on_click=_abrir_calendario,
                                ),
                            ],
                            spacing=0,
                        ),
                    ),
                    _celda_alta("moneda", ft.Row([dropdown_moneda], spacing=0)),
                    # height fijo: el IconButton (mínimo 40 px en Material 3)
                    # no puede estirar la fila más allá de ALTURA_FILA.
                    ft.Container(
                        width=ANCHO_COL_ACCION, height=ALTURA_FILA, alignment=ft.Alignment.CENTER,
                        content=boton_confirmar,
                    ),
                ],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # --- Guardado de la fila de alta ---

    def _confirmar_alta() -> None:
        # Deshabilitar ANTES de procesar: un doble Enter/click no dispara dos altas.
        boton = alta_refs["boton"]
        if boton.disabled:
            return
        boton.disabled = True
        _refrescar(boton)
        try:
            _procesar_alta()
        finally:
            # Tras un alta exitosa `boton` ya no está en pantalla (la fila se
            # reconstruyó): _refrescar() lo saltea.
            boton.disabled = False
            _refrescar(boton)

    # --- Tab/Enter en Moneda (último campo) confirma la fila ---

    async def _confirmar_alta_diferido() -> None:
        # Un Enter con el menú de Moneda abierto además elige la opción: se
        # deja pasar primero ese on_select para guardar la moneda elegida.
        await asyncio.sleep(ESPERA_CONFIRMAR_DESDE_MONEDA_S)
        _confirmar_alta()

    def _al_tecla(e: ft.KeyboardEvent) -> None:
        """Llamado por el despachador de page.on_keyboard_event (ver armado, al final de build())."""
        if not ui["visible"] or ui["popup"] is not None:
            return
        if e.shift or e.ctrl or e.alt or e.meta or e.key not in TECLAS_CONFIRMAR_DESDE_MONEDA:
            return
        if alta_refs.get("foco_moneda", {}).get("activo"):
            page.run_task(_confirmar_alta_diferido)

    def _procesar_alta() -> None:
        campo_concepto = alta_refs["concepto"]
        campo_cuenta = alta_refs["cuenta"]
        campo_categoria = alta_refs["categoria"]
        campo_monto = alta_refs["monto"]
        campo_fecha = alta_refs["fecha"]
        dropdown_moneda = alta_refs["moneda"]
        _guardar_borrador_alta()

        if not cuentas_activas:
            _mostrar_error("PRIMERO CARGÁ UNA CUENTA (NO TARJETA DE CRÉDITO) EN CONFIGURACIÓN → CUENTAS.")
            return
        if not categorias:
            _mostrar_error("NO HAY CATEGORÍAS CARGADAS.")
            return
        concepto = (campo_concepto.value or "").strip()
        if not concepto:
            _mostrar_error("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
            return
        try:
            monto_con_signo = float((campo_monto.texto or "").strip().replace(",", "."))
        except ValueError:
            _mostrar_error("EL MONTO NO ES UN NÚMERO VÁLIDO.")
            return
        if monto_con_signo == 0:
            _mostrar_error("EL MONTO NO PUEDE SER 0 — NEGATIVO ES GASTO, POSITIVO ES INGRESO.")
            return
        fecha_str = (campo_fecha.value or "").strip()
        try:
            datetime.strptime(fecha_str, "%Y-%m-%d")
        except ValueError:
            _mostrar_error("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.")
            return
        if not campo_cuenta.id_seleccionado:
            _mostrar_error("SELECCIONÁ UNA CUENTA DE LA LISTA DE SUGERENCIAS.")
            return
        if not campo_categoria.id_seleccionado:
            _mostrar_error("SELECCIONÁ UNA CATEGORÍA DE LA LISTA DE SUGERENCIAS.")
            return
        if not dropdown_moneda.value:
            _mostrar_error("COMPLETÁ LA MONEDA.")
            return

        cuenta_id = int(campo_cuenta.id_seleccionado)
        categoria_id = int(campo_categoria.id_seleccionado)
        moneda_codigo = dropdown_moneda.value

        # "autotransferencia"/"ahorro_inversion" REEMPLAZAN el guardado
        # normal (el diálogo guarda y llama a _alta_ok()); "deuda" lo
        # EXTIENDE (transacción normal primero, después el diálogo).
        routing = mapa_categoria_a_routing.get(campo_categoria.id_seleccionado)
        if routing == "autotransferencia":
            _abrir_dialogo_autotransferencia(cuenta_id, moneda_codigo, monto_con_signo, fecha_str, concepto, categoria_id)
            return
        if routing == "ahorro_inversion":
            _abrir_dialogo_ahorro_inversion(cuenta_id, moneda_codigo, monto_con_signo, fecha_str, concepto)
            return

        try:
            resultado = transaction_service.create(
                date_str=fecha_str,
                concept=concepto,
                account_id=cuenta_id,
                category_id=categoria_id,
                currency_code=moneda_codigo,
                amount=abs(monto_con_signo),
                movement_type="egreso" if monto_con_signo < 0 else "ingreso",
            )
        except (TransactionError, ValueError) as err:
            _mostrar_error(str(err))  # la fila queda como estaba para corregir
            return

        if routing == "deuda":
            _abrir_dialogo_deuda(resultado.transaction_id, moneda_codigo, monto_con_signo, fecha_str, concepto)
            return

        _alta_ok()
        _mostrar_ok(f"MOVIMIENTO #{resultado.transaction_id} REGISTRADO.")

    # --- Mini-diálogos de routing especial (misma lógica que antes) ---

    def _abrir_dialogo_autotransferencia(
        cuenta_origen_id: int, moneda_codigo: str, monto: float, fecha_str: str, concepto: str, categoria_id: int,
    ) -> None:
        opciones_destino = [(str(c["id"]), c["nombre"]) for c in cuentas_activas if c["id"] != cuenta_origen_id]
        if not opciones_destino:
            _mostrar_error("NO HAY OTRA CUENTA DISPONIBLE COMO DESTINO PARA LA AUTOTRANSFERENCIA.")
            return
        campo_destino = CampoFiltrable(
            page, opciones_destino, on_seleccionar=lambda id_: None,
            placeholder="CUENTA DESTINO", width=ANCHO_DIALOGO_ROUTING, autofocus=True,
        )

        def _confirmar(e=None) -> None:
            if not campo_destino.id_seleccionado:
                _mostrar_error("SELECCIONÁ LA CUENTA DESTINO DE LA LISTA DE SUGERENCIAS.")
                return
            try:
                resultado = transaction_service.create_transfer(
                    date_str=fecha_str,
                    origin_account_id=cuenta_origen_id,
                    dest_account_id=int(campo_destino.id_seleccionado),
                    currency_code=moneda_codigo,
                    amount=abs(monto),  # el signo no decide nada: siempre egreso + ingreso
                    category_id=categoria_id,
                    notes=concepto,  # create_transfer() no tiene `concept`
                )
            except (TransactionError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            _alta_ok()
            _mostrar_ok(
                f"AUTOTRANSFERENCIA REGISTRADA (MOVIMIENTOS #{resultado.data['out_transaction_id']} "
                f"→ #{resultado.data['in_transaction_id']})."
            )

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("AUTOTRANSFERENCIA — CUENTA DESTINO"),
            content=ft.Container(width=ANCHO_DIALOGO_ROUTING, content=campo_destino.control),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("CONFIRMAR"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _abrir_dialogo_deuda(transaction_id: int, moneda_codigo: str, monto: float, fecha_str: str, concepto: str) -> None:
        # La transacción YA está guardada: este diálogo solo decide si además
        # se le vincula una deuda. Cancelarlo no la deshace.
        campo_persona = ft.TextField(label="PERSONA / ENTIDAD", width=ANCHO_DIALOGO_ROUTING, autofocus=True)
        campo_vencimiento = ft.TextField(label="FECHA DE VENCIMIENTO (OPCIONAL, AAAA-MM-DD)", width=ANCHO_DIALOGO_ROUTING)
        # Egreso (le diste plata a alguien) → te debe; ingreso (te prestaron) → le debés.
        debt_type = "a_favor" if monto < 0 else "en_contra"
        texto_direccion = "TE DEBE" if debt_type == "a_favor" else "LE DEBÉS"

        def _cancelar(e=None) -> None:
            _cerrar_dialogo()
            _alta_ok()
            _mostrar_ok(f"MOVIMIENTO #{transaction_id} REGISTRADO (SIN DEUDA VINCULADA — SE CANCELÓ EL DIÁLOGO).")

        def _confirmar(e=None) -> None:
            persona = (campo_persona.value or "").strip()
            if not persona:
                _mostrar_error("INGRESÁ LA PERSONA/ENTIDAD.")
                return
            due_date = (campo_vencimiento.value or "").strip() or None
            if due_date is not None:
                try:
                    datetime.strptime(due_date, "%Y-%m-%d")
                except ValueError:
                    _mostrar_error("LA FECHA DE VENCIMIENTO DEBE TENER EL FORMATO AAAA-MM-DD.")
                    return
            try:
                resultado_deuda = debts_service.create(
                    person=persona,
                    debt_type=debt_type,
                    amount=abs(monto),  # create() lo exige positivo; el sentido lo da debt_type
                    currency_code=moneda_codigo,
                    date_str=fecha_str,
                    concept=concepto,
                    due_date=due_date,
                    origen_tipo="transaccion",
                    origen_id=transaction_id,
                )
            except (DebtError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            _alta_ok()
            _mostrar_ok(
                f"MOVIMIENTO #{transaction_id} REGISTRADO Y VINCULADO A UNA DEUDA CON "
                f"'{persona}' (#{resultado_deuda.debt_id}) — {texto_direccion}."
            )

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("DEUDA — PERSONA / VENCIMIENTO"),
            content=ft.Container(
                width=ANCHO_DIALOGO_ROUTING,
                content=ft.Column([campo_persona, campo_vencimiento], tight=True, spacing=ESPACIADO),
            ),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cancelar),
                ft.ElevatedButton(content=ft.Text("CONFIRMAR"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _abrir_dialogo_ahorro_inversion(
        cuenta_id: int, moneda_codigo: str, monto: float, fecha_str: str, concepto: str,
    ) -> None:
        # Dos modos en el MISMO AlertDialog: el link "Elegir activo
        # específico" reemplaza contenedor_dialogo.content por el formulario
        # de dialogo_compra_ahorro.py; estado_confirmar indirecciona qué
        # confirma el botón (las actions no se reconstruyen).
        objetivos = savings_service.list_objetivos()
        opciones_objetivo = [(str(o["id"]), o["nombre"]) for o in objetivos] + [
            (_ID_OBJETIVO_NUEVO, "+ CREAR NUEVO OBJETIVO")
        ]
        campo_nombre_nuevo = ft.TextField(
            label="NOMBRE DEL OBJETIVO NUEVO", visible=False, width=ANCHO_DIALOGO_ROUTING, dense=True,
        )

        def _on_objetivo(id_: Optional[str]) -> None:
            campo_nombre_nuevo.visible = (id_ == _ID_OBJETIVO_NUEVO)
            page.update()

        campo_objetivo = CampoFiltrable(
            page, opciones_objetivo, on_seleccionar=_on_objetivo,
            placeholder="OBJETIVO DE AHORRO", width=ANCHO_DIALOGO_ROUTING, autofocus=True,
        )

        def _on_exito(resultado) -> None:
            _cerrar_dialogo()
            _alta_ok()
            _mostrar_ok(f"APORTE A AHORRO REGISTRADO (MOVIMIENTO #{resultado.entity_id}).")

        def _confirmar_modo_simple(e=None) -> None:
            if not campo_objetivo.id_seleccionado:
                _mostrar_error("SELECCIONÁ UN OBJETIVO DE AHORRO DE LA LISTA DE SUGERENCIAS.")
                return
            if campo_objetivo.id_seleccionado == _ID_OBJETIVO_NUEVO:
                nombre_nuevo = (campo_nombre_nuevo.value or "").strip()
                if not nombre_nuevo:
                    _mostrar_error("EL NOMBRE DEL OBJETIVO NUEVO NO PUEDE ESTAR VACÍO.")
                    return
                objetivo_id = savings_service.create_objetivo(nombre=nombre_nuevo).entity_id
            else:
                objetivo_id = int(campo_objetivo.id_seleccionado)
            moneda = monedas_por_codigo.get(moneda_codigo)
            if moneda is None:
                _mostrar_error(f"MONEDA '{moneda_codigo}' NO ENCONTRADA.")
                return
            try:
                activo = savings_service.get_or_create_reserved_cash_asset(cuenta_id=cuenta_id, moneda_id=moneda["id"])
                resultado = savings_service.register_purchase(
                    activo_id=activo.entity_id,
                    fecha=fecha_str,
                    monto_total_minor=amount_to_minor(abs(monto), moneda["decimales"]),  # signo ignorado
                    asignaciones=[{"objetivo_id": objetivo_id, "porcentaje": 100.0}],
                    notas=concepto,
                )
            except (SavingsError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _on_exito(resultado)

        texto_titulo = ft.Text("AHORRO/INVERSIÓN — OBJETIVO")
        estado_confirmar = {"actual": _confirmar_modo_simple}

        def _ir_a_modo_completo(e=None) -> None:
            formulario = dialogo_compra_ahorro.construir(
                page, savings_service, accounts_service,
                on_exito=_on_exito,
                cuenta_id_inicial=cuenta_id, monto_inicial=abs(monto),
                fecha_inicial=fecha_str, notas_inicial=concepto,
            )
            texto_titulo.value = "AHORRO/INVERSIÓN — ACTIVO ESPECÍFICO"
            contenedor_dialogo.width = None  # el formulario trae su propio ancho
            contenedor_dialogo.content = formulario.contenido
            estado_confirmar["actual"] = formulario.confirmar
            page.update()

        contenedor_dialogo = ft.Container(
            width=ANCHO_DIALOGO_ROUTING,
            content=ft.Column(
                [
                    campo_objetivo.control,
                    campo_nombre_nuevo,
                    ft.TextButton(content=ft.Text("ELEGIR ACTIVO ESPECÍFICO"), on_click=_ir_a_modo_completo),
                ],
                tight=True,
                spacing=ESPACIADO,
            ),
        )
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=texto_titulo,
            content=contenedor_dialogo,
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("CONFIRMAR"), on_click=lambda e: estado_confirmar["actual"]()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # E. FILAS DE DATOS — celdas editables inline (CLAUDE.md §10)
    # ------------------------------------------------------------
    # Modo lectura por default; el campo se crea al hacer click y se
    # destruye al salir (Enter, ✓ o blur). Un guardado exitoso termina en
    # _guardar_campo()/_datos_cambiaron(), que reconstruye esa fila.

    def _contenedor_celda(columna: str, alineacion: ft.Alignment = ft.Alignment.CENTER) -> ft.Container:
        """Contenido centrado en los dos ejes (Monto: centrado vertical, a la derecha)."""
        return ft.Container(
            expand=_flex(columna), height=ALTURA_FILA, alignment=alineacion,
            padding=ft.Padding.symmetric(horizontal=LayoutTokens.PADDING_CELDA),
        )

    def _lectura(celda: ft.Container, contenido: ft.Control, on_editar: Callable[[], None],
                 alineacion: ft.Alignment = ft.Alignment.CENTER) -> None:
        celda.border = None
        celda.content = ft.Container(
            content=contenido, on_click=lambda e: on_editar(), alignment=alineacion,
            padding=ft.Padding.symmetric(horizontal=LayoutTokens.PADDING_CELDA),
        )

    def _celda_texto(columna: str, texto: str, valor_inicial: str,
                     on_confirmar: Callable[[str], None]) -> ft.Container:
        celda = _contenedor_celda(columna)
        edicion = {"activa": False}

        def _mostrar(actualizar: bool = True) -> None:
            edicion["activa"] = False
            _lectura(celda, _texto_celda(texto), _editar)
            if actualizar:
                _refrescar(celda)

        def _editar() -> None:
            campo = ft.TextField(
                value=valor_inicial, autofocus=True, expand=True, text_align=ft.TextAlign.CENTER,
                **_estilo_campo(),
            )
            boton = _boton_confirmar_celda()

            def _confirmar(e=None) -> None:
                # Enter, ✓ y blur pasan todos por acá: el primero gana.
                if not edicion["activa"]:
                    return
                nuevo = campo.value or ""
                if nuevo == valor_inicial:
                    _mostrar()
                    return
                edicion["activa"] = False
                campo.disabled = True
                boton.disabled = True
                _refrescar(celda)
                try:
                    on_confirmar(nuevo)
                except (TransactionError, ValueError) as err:
                    _mostrar_error(str(err))
                    _mostrar()

            campo.on_submit = _confirmar
            campo.on_blur = _confirmar
            boton.on_click = _confirmar
            edicion["activa"] = True
            celda.border = ft.Border.all(ANCHO_BORDE, TEXT_ACCENT)
            celda.content = ft.Row([campo, boton], spacing=0)
            _refrescar(celda)
            _enfocar(campo)

        _mostrar(actualizar=False)
        return celda

    def _celda_campo_filtrable(columna: str, t: dict, contenido_lectura: Callable[[], ft.Control],
                               opciones: list[tuple[str, str]], valor_inicial: str,
                               on_confirmar: Callable[[str], None]) -> ft.Container:
        celda = _contenedor_celda(columna)
        edicion = {"activa": False}

        def _mostrar(actualizar: bool = True) -> None:
            edicion["activa"] = False
            _lectura(celda, contenido_lectura(), _editar)
            if actualizar:
                _refrescar(celda)

        def _posicion() -> tuple[float, float, float]:
            """Sugerencias justo debajo de esta fila (índice actual en pantalla)."""
            indice = mostradas["ids"].index(t["id"]) if t["id"] in mostradas["ids"] else 0
            return (
                ANCHO_COL_CHECK + _x_rel(columna) + LayoutTokens.PADDING_CELDA,
                ALTURA_HEADER + ALTURA_FILA_ALTA + (indice + 1) * ALTURA_FILA,
                _px(columna) - 2 * LayoutTokens.PADDING_CELDA,
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
                _refrescar(celda)
                try:
                    on_confirmar(id_seleccionado)
                except (TransactionError, ValueError) as err:
                    _mostrar_error(str(err))
                    _mostrar()

            def _al_salir() -> None:
                # Blur sin elegir nada nuevo: vuelve a modo lectura.
                if edicion["activa"]:
                    _mostrar()

            pagina_celda = _ActualizacionLocal(page, lambda: [celda, capa_sugerencias, relleno_tabla])
            campo = _campo_filtrable_tabla(
                pagina_celda, opciones, _confirmar, _posicion, al_salir=_al_salir,
                valor_inicial_id=valor_inicial, autofocus=True,
            )
            edicion["activa"] = True
            celda.border = ft.Border.all(ANCHO_BORDE, TEXT_ACCENT)
            celda.content = campo.control
            _refrescar(celda)
            _enfocar(campo.campo_texto)

        _mostrar(actualizar=False)
        return celda

    def _celda_monto(t: dict, texto: str, color: str) -> ft.Container:
        celda = _contenedor_celda("monto", ft.Alignment.CENTER)
        decimales = t["decimales"]
        edicion = {"activa": False}

        def _mostrar(actualizar: bool = True) -> None:
            edicion["activa"] = False
            _lectura(
                celda,
                _texto_celda(
                    texto, color=color, size=TypographyTokens.REGISTRO_FONT_MONTO, weight=PESO_MONTO,
                    align=ft.TextAlign.RIGHT,
                ),
                _editar,
                alineacion=ft.Alignment.CENTER,
            )
            if actualizar:
                _refrescar(celda)

        def _editar() -> None:
            def _confirmar(monto_minor: int) -> None:
                if not edicion["activa"] or monto_minor == t["monto_minor"]:
                    return
                edicion["activa"] = False
                campo.control.disabled = True
                boton.disabled = True
                _refrescar(celda)
                try:
                    if monto_minor <= 0:
                        raise ValueError("EL MONTO DEBE SER MAYOR A 0 (EL SIGNO NO SE CAMBIA DESDE ACÁ).")
                    # update() recibe el monto como float y moneda junto con él.
                    transaction_service.update(
                        t["id"], amount=monto_minor / (10 ** decimales), currency_code=t["currency_code"],
                    )
                except (TransactionError, ValueError) as err:
                    _mostrar_error(str(err))
                    _mostrar()
                    raise  # CampoMonto revierte su texto
                cache_filas.pop(t["id"], None)
                _datos_cambiaron()
                _mostrar_ok(f"MOVIMIENTO #{t['id']} ACTUALIZADO.")

            def _enter_sin_cambios() -> None:
                # on_avanzar corre tras un Enter válido; si hubo guardado la
                # fila ya se reconstruyó (edicion["activa"] quedó en False).
                if edicion["activa"]:
                    _mostrar()

            pagina_celda = _ActualizacionLocal(page, lambda: [celda])
            campo = CampoMonto(
                pagina_celda, on_confirmar=_confirmar, decimales=decimales, persistir_formula=True,
                valor_inicial_minor=t["monto_minor"],
                dense=LayoutTokens.CELDA_DENSE, text_size=TypographyTokens.REGISTRO_FONT_MONTO, autofocus=True,
                on_avanzar=_enter_sin_cambios,
            )
            _sin_borde(campo.control)
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
            celda.content = ft.Row([campo.control, boton], spacing=0)
            _refrescar(celda)
            _enfocar(campo.control)

        _mostrar(actualizar=False)
        return celda

    # --- Filas (cacheadas) ---

    def _firma(t: dict) -> tuple:
        """Todo lo que la fila muestra: si no cambió, la fila cacheada se reusa tal cual."""
        return (
            t["concepto"], t["cuenta_id"], t["account_name"], t["categoria_id"], t["category_name"],
            t["monto_minor"], t["tipo_movimiento"], t["fecha"], t["currency_code"], t["decimales"],
            t["currency_symbol"], t["id"] in datos["compartidos"],
        )

    def _guardar_campo(t: dict, **kwargs) -> None:
        transaction_service.update(t["id"], **kwargs)
        cache_filas.pop(t["id"], None)
        _datos_cambiaron()
        _mostrar_ok(f"MOVIMIENTO #{t['id']} ACTUALIZADO.")

    def _toggle_seleccion(t: dict, seleccionada: bool) -> None:
        habia_seleccion = bool(ui["seleccion"])
        if seleccionada:
            ui["seleccion"].add(t["id"])
        else:
            ui["seleccion"].discard(t["id"])
        ui["confirmando_eliminar"] = False
        ref = cache_filas[t["id"]]
        ref["fila"].bgcolor = BG_FILA_SEL if seleccionada else ref["bg"]
        cambiados: list[ft.Control] = [ref["fila"]]
        if habia_seleccion != bool(ui["seleccion"]):
            # Se activó/desactivó el modo selección: checkboxes de todas las filas.
            for id_ in mostradas["ids"]:
                if id_ in cache_filas:
                    cache_filas[id_]["checkbox"].visible = bool(ui["seleccion"])
            ref["checkbox"].visible = True  # el mouse sigue encima de esta
            cambiados = [tabla_filas]
        checkbox_todas.value = _todas_seleccionadas()
        _actualizar_barra()
        _refrescar(*cambiados, checkbox_todas, barra)

    def _fila(t: dict) -> dict:
        """Construye la fila de `t` y la deja en cache_filas."""
        color_monto = TEXT_NEGATIVO if _es_egreso(t) else TEXT_POSITIVO
        cuenta = datos["cuentas_por_id"].get(t["cuenta_id"])
        ref: dict[str, Any] = {"id": t["id"], "firma": _firma(t), "bg": BG_FILA_PAR}

        checkbox = ft.Checkbox(
            value=False, visible=False, active_color=TEXT_ACCENT,
            on_change=lambda e: _toggle_seleccion(t, bool(e.control.value)),
        )

        def _confirmar_concepto(nuevo: str) -> None:
            if not nuevo.strip():
                raise ValueError("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
            _guardar_campo(t, concept=nuevo.strip())

        def _confirmar_fecha(nuevo: str) -> None:
            try:
                datetime.strptime(nuevo.strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.") from None
            _guardar_campo(t, date_str=nuevo.strip())

        def _banco_lectura() -> ft.Control:
            # Row ajustada al contenido (tight) para que la celda la centre;
            # el nombre es flexible "loose": ocupa lo que necesita y, si no
            # entra, se corta con ellipsis.
            nombre = _texto_celda(t["account_name"] or "", color=TEXT_SECONDARY)
            nombre.expand = True
            nombre.expand_loose = True
            return ft.Row(
                [_dot(_color_cuenta(cuenta, t["account_name"] or "")), nombre],
                spacing=ESPACIO_DOT,
                tight=True,
            )

        celdas: dict[str, ft.Container] = {
            "concepto": _celda_texto("concepto", t["concepto"], t["concepto"], _confirmar_concepto),
            "banco": _celda_campo_filtrable(
                "banco", t, _banco_lectura, opciones_cuenta_edicion, str(t["cuenta_id"]),
                lambda id_: _guardar_campo(t, account_id=int(id_)),
            ),
            "categoria": _celda_campo_filtrable(
                "categoria", t, lambda: _texto_celda(t["category_name"] or ""),
                opciones_categoria, str(t["categoria_id"]),
                lambda id_: _guardar_campo(t, category_id=int(id_)),
            ),
            "monto": _celda_monto(t, _monto_con_signo(t), color_monto),
            "fecha": _celda_texto("fecha", t["fecha"], t["fecha"], _confirmar_fecha),
            "moneda": ft.Container(
                expand=_flex("moneda"), height=ALTURA_FILA, alignment=ft.Alignment.CENTER,
                padding=ft.Padding.symmetric(horizontal=PADDING_CELDA_H),
                content=_texto_celda(t["currency_code"] or "", color=TEXT_SECONDARY),
            ),
        }

        fila = ft.Container(
            height=ALTURA_FILA,
            border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_HEADER)),
            content=ft.Row(
                [
                    ft.Container(
                        width=ANCHO_COL_CHECK, height=ALTURA_FILA, alignment=ft.Alignment.CENTER, content=checkbox,
                    ),
                    *[celdas[c] for c in CLAVES_COLUMNAS],
                    ft.Container(
                        width=ANCHO_COL_ACCION, height=ALTURA_FILA, alignment=ft.Alignment.CENTER,
                        content=(
                            ft.Icon(ft.Icons.PEOPLE, size=ICONO_COMPARTIDO, color=TEXT_ACCENT, tooltip="GASTO COMPARTIDO")
                            if t["id"] in datos["compartidos"] else None
                        ),
                    ),
                ],
                spacing=0,
            ),
        )

        def _hover(e: ft.ControlEvent) -> None:
            activo = str(e.data).lower() == "true"  # normalizado defensivamente
            sel = t["id"] in ui["seleccion"]
            fila.bgcolor = BG_FILA_SEL if sel else (BG_FILA_HOVER if activo else ref["bg"])
            checkbox.visible = activo or bool(ui["seleccion"])
            _refrescar(fila)

        fila.on_hover = _hover
        ref.update(fila=fila, checkbox=checkbox, celdas=celdas)
        cache_filas[t["id"]] = ref
        return ref

    def _aplicar_estado_fila(ref: dict, indice: int, hay_seleccion: bool) -> None:
        """Lo que cambia sin reconstruir la fila: fondo alternado, selección, checkbox, anchos."""
        seleccionada = ref["id"] in ui["seleccion"]
        ref["bg"] = BG_FILA_PAR if indice % 2 == 0 else BG_FILA_IMPAR
        ref["fila"].bgcolor = BG_FILA_SEL if seleccionada else ref["bg"]
        ref["checkbox"].value = seleccionada
        ref["checkbox"].visible = hay_seleccion
        _aplicar_anchos(ref["celdas"])

    def _redibujar_tabla() -> None:
        """Header + filas según filtros/orden/selección actuales (no recarga datos ni parchea: eso lo hace el caller)."""
        visibles = _filas_visibles()
        # Nunca queda seleccionada una fila que el usuario no ve.
        ids_visibles = {t["id"] for t in visibles}
        if not ui["seleccion"] <= ids_visibles:
            ui["seleccion"] &= ids_visibles
            ui["confirmando_eliminar"] = False
        # Transacciones que ya no están cargadas (borradas, otro mes): fuera del cache.
        ids_cargados = {t["id"] for t in datos["transacciones"]}
        for id_ in [i for i in cache_filas if i not in ids_cargados]:
            del cache_filas[id_]

        hay_seleccion = bool(ui["seleccion"])
        filas: list[ft.Control] = []
        for indice, t in enumerate(visibles):
            ref = cache_filas.get(t["id"])
            if ref is None or ref["firma"] != _firma(t):
                ref = _fila(t)
            _aplicar_estado_fila(ref, indice, hay_seleccion)
            filas.append(ref["fila"])
        mostradas["ids"] = [t["id"] for t in visibles]

        tabla_filas.controls = filas or [
            ft.Container(
                height=ALTURA_VACIO,
                alignment=ft.Alignment.CENTER_LEFT,
                padding=ft.Padding.symmetric(horizontal=PADDING_SALDO_H),
                content=ft.Text(
                    "NO HAY MOVIMIENTOS PARA MOSTRAR.", italic=True, color=TEXT_MUTED,
                    size=TypographyTokens.REGISTRO_FONT_CELDA,
                ),
            )
        ]
        _dibujar_header()
        _actualizar_barra()

    # ------------------------------------------------------------
    # F. BARRA FLOTANTE DE SELECCIÓN MÚLTIPLE (page.overlay)
    # ------------------------------------------------------------

    def _cancelar_seleccion(e=None) -> None:
        ui["seleccion"].clear()
        ui["confirmando_eliminar"] = False
        _redibujar_tabla()
        _refrescar(contenedor_header, tabla_filas, barra)

    def _boton_circular(icono: str, color: str, tooltip: str, on_click: Callable, habilitado: bool = True) -> ft.Control:
        return ft.Container(
            width=DIAMETRO_BOTON_FLOT, height=DIAMETRO_BOTON_FLOT, border_radius=DIAMETRO_BOTON_FLOT / 2,
            bgcolor=color, alignment=ft.Alignment.CENTER, tooltip=tooltip,
            opacity=1.0 if habilitado else 0.4,
            on_click=on_click if habilitado else None,
            content=ft.Icon(icono, size=ICONO_BOTON_FLOT, color=TEXT_SOBRE_BOTON),
        )

    def _avisos_eliminar(ids: list[int]) -> str:
        autotransferencias = origenes_ahorro = 0
        for transaccion_id in ids:
            avisos = transaction_service.get_delete_warnings(transaccion_id)
            autotransferencias += bool(avisos["es_autotransferencia"])
            origenes_ahorro += bool(avisos["es_origen_ahorro"])
        partes = []
        if autotransferencias:
            partes.append(f"{autotransferencias} ES PARTE DE UNA AUTOTRANSFERENCIA (LA OTRA PATA NO SE BORRA)")
        if origenes_ahorro:
            partes.append(f"{origenes_ahorro} ES ORIGEN DE UN MOVIMIENTO DE AHORRO (QUEDA SIN VÍNCULO)")
        return " · ".join(partes)

    def _pedir_confirmacion(e=None) -> None:
        ui["confirmando_eliminar"] = True
        _actualizar_barra()
        _refrescar(barra)

    def _cancelar_confirmacion(e=None) -> None:
        ui["confirmando_eliminar"] = False
        _actualizar_barra()
        _refrescar(barra)

    def _eliminar_seleccion(e=None) -> None:
        ids = sorted(ui["seleccion"])
        errores = []
        for transaccion_id in ids:
            try:
                resultado = transaction_service.delete(transaccion_id)
            except TransactionError as err:
                errores.append(f"#{transaccion_id}: {err}")
                continue
            if not resultado.success:
                errores.append(f"#{transaccion_id}: {resultado.message}")
        eliminadas = len(ids) - len(errores)
        ui["seleccion"].clear()
        ui["confirmando_eliminar"] = False
        _datos_cambiaron()
        if errores:
            _mostrar_error(f"{eliminadas} ELIMINADA(S), {len(errores)} CON ERROR: " + " | ".join(errores))
        else:
            _mostrar_ok(f"{eliminadas} MOVIMIENTO(S) ELIMINADO(S).")

    def _compartir(e=None) -> None:
        # El flujo existente (compartir_gasto.py) es un diálogo por
        # transacción: se dispara el on_click (async) de su propio ícono.
        transaccion = next((t for t in datos["transacciones"] if t["id"] in ui["seleccion"]), None)
        if transaccion is None or len(ui["seleccion"]) != 1:
            return
        icono, _ = compartir_gasto.build_icon(page, shared_expenses_service, transaccion, _datos_cambiaron)
        page.run_task(icono.on_click, None)

    def _actualizar_barra() -> None:
        cantidad = len(ui["seleccion"])
        barra.visible = cantidad > 0 and ui["visible"]
        if cantidad == 0:
            return
        texto_cantidad = f"{cantidad} FILA{'S' if cantidad != 1 else ''} SELECCIONADA{'S' if cantidad != 1 else ''}"
        tamanio = TypographyTokens.REGISTRO_FONT_BARRA_FLOT
        controles: list[ft.Control] = [
            ft.IconButton(icon=ft.Icons.CLOSE, icon_color=TEXT_SECONDARY, tooltip="CANCELAR SELECCIÓN",
                          on_click=_cancelar_seleccion),
            ft.Text(texto_cantidad, size=tamanio, weight=PESO_MONTO, color=TEXT_PRIMARY),
            ft.Container(width=ANCHO_BORDE, height=DIAMETRO_BOTON_FLOT, bgcolor=BORDER_BARRA),
            _boton_circular(ft.Icons.DELETE_OUTLINE, BTN_ELIMINAR, "ELIMINAR", _pedir_confirmacion),
            ft.Text("ELIMINAR", size=tamanio, color=TEXT_PRIMARY),
            _boton_circular(
                ft.Icons.PEOPLE, BTN_COMPARTIR,
                "COMPARTIR" if cantidad == 1 else "SE COMPARTE DE A UNA FILA",
                _compartir, habilitado=(cantidad == 1),
            ),
            ft.Text("COMPARTIR", size=tamanio, color=TEXT_PRIMARY if cantidad == 1 else TEXT_MUTED),
        ]
        if ui["confirmando_eliminar"]:
            avisos = _avisos_eliminar(sorted(ui["seleccion"]))
            controles += [
                ft.Container(width=ANCHO_BORDE, height=DIAMETRO_BOTON_FLOT, bgcolor=BORDER_BARRA),
                ft.Text(
                    f"¿ELIMINAR {cantidad} FILA{'S' if cantidad != 1 else ''}?" + (f"  ({avisos})" if avisos else ""),
                    size=tamanio, color=TEXT_PRIMARY, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                    tooltip=avisos or None, expand=True,
                ),
                _boton_texto("CONFIRMAR", _eliminar_seleccion, relleno=BTN_ELIMINAR),
                _boton_texto("CANCELAR", _cancelar_confirmacion, relleno=None),
            ]
        else:
            controles.append(ft.Container(expand=True))
        controles.append(
            ft.TextButton(
                content=ft.Text("CANCELAR SELECCIÓN", size=tamanio, color=TEXT_MUTED),
                on_click=_cancelar_seleccion,
            )
        )
        barra.content = ft.Row(controles, spacing=ESPACIO_BARRA, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # ------------------------------------------------------------
    # HOOKS DE NAVEGACIÓN (ui/app.py) + ARMADO
    # ------------------------------------------------------------
    # ui/app.py hace page.update() justo después de llamar a estos hooks.

    def _al_ocultar() -> None:
        ui["visible"] = False
        _cerrar_popups()
        barra.visible = False

    def _al_mostrar() -> None:
        ui["visible"] = True
        _actualizar_barra()

    # page.on_keyboard_event es uno solo por página: se registra una única
    # vez un despachador que llama al _al_tecla() de la instancia vigente
    # (el Registro se reconstruye) y respeta un manejador previo si lo hay.
    if not ui["teclado_registrado"]:
        manejador_previo = page.on_keyboard_event

        def _despachar_tecla(e: ft.KeyboardEvent) -> None:
            if ui["al_tecla"] is not None:
                ui["al_tecla"](e)
            if manejador_previo is not None:
                manejador_previo(e)
            else:
                # Cada tecla de la app pasa por acá: sin esto, cada una
                # terminaría en un page.update() completo automático.
                ft.context.disable_auto_update()

        page.on_keyboard_event = _despachar_tecla
        ui["teclado_registrado"] = True
    ui["al_tecla"] = _al_tecla

    ui["visible"] = True
    # Si ui/app.py reconstruye la pantalla con un popup abierto, el host
    # quedaría con controles de la instancia anterior.
    ui["popup"] = None
    host_popups.visible = False
    host_popups.content = None
    contenedor_saldos.content = _barra_saldos()
    contenedor_alta.content = _construir_fila_alta()
    _redibujar_tabla()

    cuerpo_tabla = ft.Column(
        [contenedor_header, contenedor_alta, tabla_filas, relleno_tabla], spacing=0, tight=True,
    )
    tabla = ft.Container(
        bgcolor=BG_SUPERFICIE,
        border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
        border_radius=RADIO_TABLA,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
        size_change_interval=SIZE_CHANGE_INTERVAL_MS,
        on_size_change=_on_tamanio_tabla,
        # Stack: capa_sugerencias flota sobre las filas (ver docstring, "Fila de alta").
        content=ft.Stack([cuerpo_tabla, capa_sugerencias]),
    )

    raiz = ft.Container(
        bgcolor=BG_APP,
        border_radius=RADIO_TABLA,
        padding=PADDING_SALDO_H,
        content=ft.Column(
            [barra_titulo, contenedor_saldos, tabla],
            spacing=ESPACIADO * 2,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        ),
    )
    raiz.data = {"al_mostrar": _al_mostrar, "al_ocultar": _al_ocultar}
    return raiz
