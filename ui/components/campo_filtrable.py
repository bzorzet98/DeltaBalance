"""
DeltaBalance — ui/components/campo_filtrable.py

Componente propio de "campo con sugerencias filtrables" — reemplazo de
ft.Dropdown(enable_filter=True, editable=True) y ft.AutoComplete para
Banco/Categoría en ui/components/registro_transacciones.py y
ui/screens/compras_cuotas.py, por fricción real confirmada con AMBOS
controles nativos (ver docs/FLET_API_NOTES.md para el detalle completo):

- Dropdown(enable_filter=True, editable=True): el primer Tab enfocaba el
  selector cerrado en vez de caer directo en el campo de texto (hacía
  falta un segundo Tab/Enter).
- ft.AutoComplete: no mostraba ninguna lista de sugerencias al escribir,
  en ninguna pantalla, confirmado por el usuario con la consola del
  navegador sin errores — no se investigó la causa real a fondo (pedido
  explícito de no seguir depurando, reemplazar en su lugar). De paso, al
  construir este componente, se confirmó por lectura del código fuente
  real de Flet 0.86.5 instalado que AutoComplete en esa versión es un
  control muy desnudo (solo value/suggestions/on_select/on_change +
  width/height) — sin label/hint_text/dense/text_size/border/focus()/
  on_submit. Esto por sí solo ya explicaría una experiencia degradada,
  pero no alcanza para explicar sugerencias que nunca aparecen (eso
  apuntaría más a `suggestions` no llegando a poblarse del lado del
  cliente, o al popup recortado por algún contenedor padre con overflow
  — pero es una hipótesis sin confirmar, no la causa verificada).

Construido con controles básicos que ya se usan con éxito en el resto del
proyecto: ft.TextField (con on_change/on_focus/on_blur/on_submit, todos
reales y confirmados — a diferencia de Dropdown/AutoComplete) + un
ft.Container con ft.Column de opciones clickeables debajo, sin
ft.Dropdown ni ft.AutoComplete en absoluto.

Posicionamiento de la lista de sugerencias: un ft.Column normal (TextField
arriba, lista de sugerencias debajo), NO un ft.Stack superpuesto. Un Stack
habría dado un popup "flotante" más prolijo visualmente, pero acopla mal
con que esta fila vive dentro de un ft.Row de anchos fijos (cada celda ya
sabe su propio ancho — ver ANCHO_COL_* en los callers) y depende de una
API de overlay/z-index de Flet que no está confirmada para este caso. La
Column simplemente empuja el contenido de abajo mientras hay sugerencias
visibles (la fila se ve más alta un instante) — elegido a propósito por
ser lo más simple que funciona de verdad, no lo más elegante.

Navegación por teclado: Tab cae directo en el TextField (era el objetivo
original — TextField es un control real de foco, no un compuesto
menú+campo como Dropdown). Enter (on_submit) y perder el foco (on_blur)
comparten la misma lógica de confirmación (_confirmar_por_texto()): si el
texto tipeado coincide EXACTO (case-insensitive) con una opción, o si el
filtrado en curso ya dejó una sola opción visible, se selecciona sola; si
es ambiguo o no matchea nada, el campo queda con borde de error y SIN
ningún id seleccionado (nunca se deja un id viejo/inválido en silencio).

NAVEGACIÓN POR FLECHAS: con la lista de sugerencias abierta, ↓ resalta la
siguiente y ↑ la anterior (sin nada resaltado, ↓ va a la primera y ↑ a la
última; en los bordes se queda), con fondo BG_FILA_SEL; si la lista tiene
scroll, se corre para que la resaltada quede a la vista. Enter con una
resaltada la confirma (igual que un click) y avanza (on_avanzar); sin
resaltada, Enter sigue como antes (_confirmar_por_texto()). Cambiar el
texto o reabrir la lista borra el resaltado. Las teclas llegan por
page.on_keyboard_event — el mismo mecanismo que ya usa TablaPlanilla para
Tab/Enter en la fila de alta (tab_a_confirmar()), que recibe las teclas
con el foco en un TextField —, no por un ft.KeyboardListener alrededor del
campo (no confirmado que reciba teclas mientras el TextField interno tiene
el foco). Un despachador por página (_registrar_teclado()) que envuelve al
manejador que ya hubiera (y TablaPlanilla, a su vez, envuelve al que
encuentra): solo actúa el campo que tiene el foco (_TECLADO[...]["activo"],
lo marca _on_focus() y lo limpia _on_blur()). Se registra en la página
REAL (self._campo.page): dentro de TablaPlanilla, `page` es un
_ActualizacionLocal, y asignarle on_keyboard_event no llegaría a la
página. Sin confirmar corriendo la app: la etiqueta de las teclas
("Arrow Down"/"Arrow Up", KeyboardEvent.key de Flutter) y que el cursor del
TextField también se mueve al principio/fin con ↑/↓ (efecto de Flutter,
inofensivo en un campo de una línea).

"Blur vs. click" (problema clásico de cualquier UI, no específico de
Flet): clickear una sugerencia dispara blur en el TextField (Flutter
desenfoca el campo activo al tocar cualquier otro widget) ANTES de que el
click en sí termine de procesarse — si on_blur escondiera la lista de
inmediato, el click podría no llegar a resolverse nunca. _on_blur() espera
BLUR_DELAY_SEGUNDOS con asyncio.sleep() antes de validar/esconder, dándole
tiempo al click de la sugerencia (que corre primero y ya deja
id_seleccionado seteado) a completarse. Técnica estándar de "delayed
blur", no una funcionalidad de Flet.

ARREGLO DE TAB (reportado: tras confirmar un valor por click o por
auto-selección al perder foco, Tab no avanzaba al siguiente campo de la
fila). Causa más probable, sin poder confirmarla 100% sin correr la app
(la implementación real de `visible` vive en el lado Flutter/Dart
compilado, no en el paquete Python instalado que podemos leer): dejar
_contenedor_sugerencias siempre montado en el árbol con visible=False (en
vez de sacarlo del todo) probablemente lo mantenía alcanzable por el
recorrido de foco/tab de Flutter aunque no se viera — Tab desde el
TextField caía en esa subrama invisible en vez de saltar directo al
siguiente control del Row del caller. Arreglo aplicado, que no depende de
adivinar cómo Flutter maneja `visible=False`: _contenedor_sugerencias ya
NO vive en self.control.controls todo el tiempo — _mostrar_sugerencias()/
_ocultar_sugerencias() lo agregan/sacan de la lista, así que cuando está
oculto directamente no existe en el árbol, sin ambigüedad posible. Además,
_on_click_sugerencia() ahora restaura el foco al TextField explícitamente
después de aplicar la selección (el click deja el foco "parado" sobre el
ítem de sugerencia recién sacado del árbol) — _on_blur() NO hace lo mismo
a propósito, porque ahí el foco ya se está yendo adonde el usuario
realmente apuntaba (Tab real u otro click) y forzarlo de vuelta sería
pelearle esa navegación. Pendiente de confirmar corriendo la app — si Tab
sigue sin avanzar después de este cambio, el siguiente paso sería
interceptar la tecla Tab a mano vía page.on_keyboard_event() en el caller
y encadenar foco manualmente (permitido explícitamente para este caso),
pero no se implementó preventivamente para no apilar un tercer mecanismo
especulativo sin evidencia de que hiciera falta.

Reglas de arquitectura: sin dependencias de servicios/repositorios — es un
control de UI genérico, recibe sus opciones ya resueltas por el caller
(CLAUDE.md §2).
"""

import asyncio
from typing import Any, Callable, Optional

import flet as ft

from ui.theme.tabla_tokens import BG_FILA_SEL

# --- Configuración de layout ---
MAX_SUGERENCIAS = 50  # mostramos todas las que matcheen, el scroll las contiene
ALTURA_ITEM_SUGERENCIA = 36
ALTURA_MAX_LISTA = 220  # ~6 ítems visibles, el resto con scroll
BLUR_DELAY_SEGUNDOS = 0.2
# Navegación por flechas (KeyboardEvent.key de Flet: etiqueta de la tecla lógica de Flutter).
TECLA_ABAJO = "Arrow Down"
TECLA_ARRIBA = "Arrow Up"


# --- Teclado: un despachador de page.on_keyboard_event por página (ver docstring) ---

_TECLADO: dict[int, dict[str, Any]] = {}


def _registrar_teclado(page: ft.Page) -> dict[str, Any]:
    """
    El estado de teclado de la página ({"activo": campo con el foco o None}).
    La primera vez registra el despachador, envolviendo al manejador que ya
    hubiera; si no había ninguno, hace un update completo para que el
    cliente empiece a mandar las teclas.
    """
    estado = _TECLADO.get(id(page))
    if estado is not None:
        return estado
    estado = {"activo": None}
    _TECLADO[id(page)] = estado
    manejador_previo = page.on_keyboard_event

    def _despachar_tecla(e: ft.KeyboardEvent) -> None:
        campo = estado["activo"]
        if campo is not None:
            campo._al_tecla(e)
        if manejador_previo is not None:
            manejador_previo(e)
        else:
            # Cada tecla de la app pasa por acá: sin esto, cada una
            # terminaría en un page.update() completo automático.
            ft.context.disable_auto_update()

    page.on_keyboard_event = _despachar_tecla
    if manejador_previo is None:
        page.update()
    return estado

class CampoFiltrable:
    """
    Uso típico:
        campo = CampoFiltrable(
            page, opciones=[("1", "Visa"), ("2", "Efectivo")],
            on_seleccionar=lambda id_: print("elegiste", id_),
            placeholder="Banco", valor_inicial_id="1", width=160,
        )
        fila = ft.Row([campo.control, ...])
        ...
        campo.id_seleccionado  # id vigente o None si el campo quedó inválido/vacío
    """

    def __init__(
        self,
        page: ft.Page,
        opciones: list[tuple[str, str]],
        on_seleccionar: Callable[[Optional[str]], None],
        placeholder: str = "",
        valor_inicial_id: Optional[str] = None,
        width: Optional[int] = None,
        text_size: Optional[int] = None,
        dense: bool = True,
        autofocus: bool = False,
        on_avanzar: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            opciones:       [(id, texto_visible), ...] — id siempre str.
            on_seleccionar: Llamado con el id cada vez que una selección
                            queda confirmada (click, blur o Enter con
                            match único/exacto). NUNCA se llama con un id
                            inválido — si el campo queda ambiguo/sin
                            match, simplemente no se llama (consultar
                            campo.id_seleccionado, que va a ser None).
            valor_inicial_id: Precarga el campo con el texto de esa
                            opción y la deja seleccionada de entrada.
            on_avanzar:     Opcional — llamado tras un Enter que sí
                            resolvió una selección (no tras un Enter que
                            dejó el campo en error). Mismo rol que
                            "avanzar al siguiente campo" en el resto de
                            las filas de alta de este proyecto.
        """
        self._page = page
        self._opciones = opciones
        self._on_seleccionar = on_seleccionar
        self._on_avanzar = on_avanzar
        self._id_seleccionado: Optional[str] = None
        # True mientras un cambio de .value es programático (selección
        # propia) — evita que ese cambio dispare on_change como si el
        # usuario hubiera tipeado y así invalide la selección recién hecha.
        self._suprimir_on_change = False
        # Estado de teclado de la página (_registrar_teclado()), desde el primer foco.
        self._teclado: Optional[dict[str, Any]] = None

        texto_inicial = ""
        mapa_por_id = dict(opciones)
        if valor_inicial_id is not None and valor_inicial_id in mapa_por_id:
            self._id_seleccionado = valor_inicial_id
            texto_inicial = mapa_por_id[valor_inicial_id]

        self._campo = ft.TextField(
            value=texto_inicial,
            hint_text=placeholder,
            dense=dense,
            width=width,
            text_size=text_size,
            autofocus=autofocus,
            on_change=self._on_change,
            on_focus=self._on_focus,
            on_blur=self._on_blur,
            on_submit=self._on_submit,
        )
        self._lista_sugerencias = ft.Column(spacing=0)
        # Navegación por flechas (ver docstring del módulo): lo que se ve
        # ahora, sus ítems y cuál está resaltado (None = ninguno).
        self._filtradas: list[tuple[str, str]] = []
        self._items: list[ft.Container] = []
        self._indice_activo: Optional[int] = None
        self._columna_scroll = ft.Column(
            [self._lista_sugerencias],
            scroll=ft.ScrollMode.AUTO,
            spacing=0,
        )
        self._contenedor_sugerencias = ft.Container(
            width=width,
            height=ALTURA_MAX_LISTA,
            content=self._columna_scroll,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=4,
            bgcolor=ft.Colors.SURFACE,
            visible=False,
        )
        # self.control arranca con SOLO el TextField — _contenedor_sugerencias
        # se agrega/saca de .controls en _mostrar_sugerencias()/
        # _ocultar_sugerencias() en vez de quedar siempre presente con
        # visible=False. Ver "Arreglo de Tab" en el docstring del módulo:
        # dejarlo siempre montado (aunque invisible) es lo que rompía Tab.
        self.control: ft.Column = ft.Column(
            [self._campo],
            spacing=0,
            width=width,
            tight=True,
        )

    # ----------------------------------------------------------
    # API pública
    # ----------------------------------------------------------

    @property
    def id_seleccionado(self) -> Optional[str]:
        return self._id_seleccionado

    @property
    def texto(self) -> str:
        return self._campo.value or ""

    def focus(self) -> None:
        """Delegado al TextField real — mismo patrón que dropdown.focus()/textfield.focus() en el resto del proyecto."""
        self._campo.focus()

    # ----------------------------------------------------------
    # Filtrado + render de sugerencias
    # ----------------------------------------------------------

    def _filtrar(self, texto: str) -> list[tuple[str, str]]:
        texto_norm = texto.strip().lower()
        if not texto_norm:
            return self._opciones[:MAX_SUGERENCIAS]
        return [
            (id_, nombre) for id_, nombre in self._opciones
            if texto_norm in nombre.lower()
        ][:MAX_SUGERENCIAS]

    def _mostrar_sugerencias(self, filtradas: list[tuple[str, str]]) -> None:
        if not filtradas:
            self._ocultar_sugerencias()
            return
        self._filtradas = filtradas
        self._indice_activo = None  # lista nueva: nada resaltado
        self._items = [
            ft.Container(
                content=ft.Text(nombre, size=self._campo.text_size),
                height=ALTURA_ITEM_SUGERENCIA,
                padding=ft.Padding.symmetric(horizontal=10, vertical=8),
                ink=True,
                on_click=lambda e, id_=id_, nombre=nombre: self._on_click_sugerencia(id_, nombre),
            )
            for id_, nombre in filtradas
        ]
        self._lista_sugerencias.controls = list(self._items)
        self._contenedor_sugerencias.visible = True
        # Recién acá entra al árbol de foco/render — ver nota en __init__.
        self.control.controls = [self._campo, self._contenedor_sugerencias]

    def _ocultar_sugerencias(self) -> None:
        self._contenedor_sugerencias.visible = False
        self._lista_sugerencias.controls = []
        self._filtradas, self._items, self._indice_activo = [], [], None
        # Lo saca del árbol por completo (no solo visible=False) — así no
        # queda una subrama invisible-pero-montada entre el TextField y el
        # siguiente control del Row del caller que Tab pueda "pisar" en vez
        # de saltar directo al siguiente campo. Ver docstring del módulo.
        self.control.controls = [self._campo]

    # ----------------------------------------------------------
    # Navegación por flechas (ver docstring del módulo)
    # ----------------------------------------------------------

    def _al_tecla(self, e: ft.KeyboardEvent) -> None:
        """Lo llama el despachador de la página mientras este campo tiene el foco."""
        if not self._items or e.ctrl or e.alt or e.meta:
            return
        ultimo = len(self._items) - 1
        if e.key == TECLA_ABAJO:
            nuevo = 0 if self._indice_activo is None else min(self._indice_activo + 1, ultimo)
        elif e.key == TECLA_ARRIBA:
            nuevo = ultimo if self._indice_activo is None else max(self._indice_activo - 1, 0)
        else:
            return
        self._resaltar(nuevo)

    def _resaltar(self, indice: int) -> None:
        if self._indice_activo is not None and self._indice_activo < len(self._items):
            self._items[self._indice_activo].bgcolor = None
        self._indice_activo = indice
        self._items[indice].bgcolor = BG_FILA_SEL
        self._page.update()
        # Con scroll, que la resaltada quede a la vista (al final de lo visible, si hace falta).
        if len(self._items) * ALTURA_ITEM_SUGERENCIA > ALTURA_MAX_LISTA:
            offset = max(0, (indice + 1) * ALTURA_ITEM_SUGERENCIA - ALTURA_MAX_LISTA)
            self._page.run_task(self._columna_scroll.scroll_to, offset=offset, duration=0)

    # ----------------------------------------------------------
    # Selección
    # ----------------------------------------------------------

    def _aplicar_seleccion(self, id_: str, nombre: str, restaurar_foco: bool = False) -> None:
        self._id_seleccionado = id_
        self._suprimir_on_change = True
        self._campo.value = nombre
        self._campo.border_color = None
        self._ocultar_sugerencias()
        if restaurar_foco:
            # Solo desde el click de una sugerencia (ver _on_click_sugerencia):
            # el click deja el foco "parado" sobre el ítem que
            # _ocultar_sugerencias() acaba de sacar del árbol — sin este
            # focus() explícito, un Tab inmediato después de clickear
            # queda sin ancla real de foco desde donde avanzar (ver
            # "Arreglo de Tab" en el docstring del módulo). NO se pasa
            # restaurar_foco=True desde _confirmar_por_texto() (blur/
            # Enter): ahí el foco ya se está yendo a propósito a otro lado
            # (Tab real o click en otro campo) y forzarlo de vuelta sería
            # pelearle esa navegación al usuario.
            self._campo.focus()
        self._on_seleccionar(id_)

    def _confirmar_por_texto(self) -> bool:
        """
        Resuelve un id a partir del texto tipeado sin click (Enter o
        blur). Devuelve True si quedó una selección válida (ya sea
        porque ya había una vigente, un match exacto, o el filtrado
        vigente dejó una sola opción visible), False si quedó
        ambiguo/sin match — en ese caso deja borde de error y
        id_seleccionado en None, nunca algo inválido en silencio.
        """
        self._ocultar_sugerencias()

        if self._id_seleccionado is not None:
            return True

        texto = (self._campo.value or "").strip()
        if not texto:
            self._campo.border_color = None
            return False

        exactas = [(id_, nombre) for id_, nombre in self._opciones if nombre.lower() == texto.lower()]
        if len(exactas) == 1:
            self._aplicar_seleccion(*exactas[0])
            return True

        filtradas = self._filtrar(texto)
        if len(filtradas) == 1:
            self._aplicar_seleccion(*filtradas[0])
            return True

        self._campo.border_color = ft.Colors.ERROR
        return False

    # ----------------------------------------------------------
    # Handlers de eventos
    # ----------------------------------------------------------

    def _on_click_sugerencia(self, id_: str, nombre: str) -> None:
        self._aplicar_seleccion(id_, nombre, restaurar_foco=True)
        self._page.update()

    def _on_change(self, e: ft.ControlEvent) -> None:
        if self._suprimir_on_change:
            self._suprimir_on_change = False
            return
        # Cualquier tipeo invalida la selección vigente hasta la próxima
        # confirmación — evita guardar un id viejo que ya no corresponde
        # al texto en pantalla.
        self._id_seleccionado = None
        self._campo.border_color = None
        self._mostrar_sugerencias(self._filtrar(self._campo.value or ""))
        self._page.update()

    def _on_focus(self, e: ft.ControlEvent) -> None:
        # Flechas: este campo pasa a ser el que recibe las teclas. La página
        # REAL (self._campo.page, ya montado), no self._page: dentro de
        # TablaPlanilla es un _ActualizacionLocal (ver docstring del módulo).
        self._teclado = _registrar_teclado(self._campo.page)
        self._teclado["activo"] = self
        self._mostrar_sugerencias(self._filtrar(self._campo.value or ""))
        self._page.update()

    async def _on_blur(self, e: ft.ControlEvent) -> None:
        # Delay a propósito — ver docstring del módulo ("blur vs. click").
        await asyncio.sleep(BLUR_DELAY_SEGUNDOS)
        if self._teclado is not None and self._teclado["activo"] is self:
            self._teclado["activo"] = None  # si el foco ya pasó a otro campo filtrable, es de ese
        self._confirmar_por_texto()
        self._page.update()

    def _on_submit(self, e: ft.ControlEvent) -> None:
        if self._indice_activo is not None and self._indice_activo < len(self._filtradas):
            # Enter con una sugerencia resaltada (flechas): se confirma esa, como un click.
            self._aplicar_seleccion(*self._filtradas[self._indice_activo])
            exito = True
        else:
            exito = self._confirmar_por_texto()
        self._page.update()
        if exito and self._on_avanzar:
            self._on_avanzar()
