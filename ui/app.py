"""
DeltaBalance — ui/app.py

Shell de la aplicación Flet: sidebar colapsable (nav de primer nivel +
sección "Configuración") y área de contenido donde se montan las pantallas
de ui/screens/.

Sidebar colapsable (estado solo en memoria de sesión, se pierde al
reiniciar la app — no hay animación de ancho: no se pudo confirmar la API
de animación exacta contra Flet 0.86.5 real, ver docs/FLET_API_NOTES.md
regla 2, así que el cambio de ancho es instantáneo; animarlo queda como
mejora futura si se confirma la API corriendo la app): COLAPSADA por
default (ancho SIDEBAR_ANCHO_COLAPSADO, solo íconos con tooltip al hover,
flecha apuntando a la derecha). Solo el botón de flecha al final de la
sidebar la expande (ancho SIDEBAR_ANCHO_EXPANDIDO, ícono + texto, flecha
apuntando a la izquierda) o la contrae — pasar el mouse por encima ya no
la expande (se sacó el hover-to-peek). `_actualizar_sidebar()` reconstruye
el contenido del Container de la sidebar y parchea solo ese Container.

Tema: oscuro para toda la app (page.theme_mode + page.bgcolor, acá y en
ningún otro lado).

Onboarding condicional: si AccountsService.list_accounts() está vacío (primera
vez que se abre la app), se muestra Cuentas primero en vez del Dashboard.
No hay una bandera de estado separada para "está en onboarding" — se
consulta list_accounts() directo en cada momento en que hace falta decidir
qué pantalla mostrar (acá al armar el shell, y de nuevo dentro de
cuentas.py al guardar la primera cuenta), así nunca puede quedar desincronizada
de la base real.

--- Navegación: pantallas persistentes ---

Antes, cada mostrar_X() reemplazaba content_area.content por una pantalla
recién construida: al volver a una sección se perdía todo lo que tenía a
medias (fila de alta, selección, filtros, período, scroll). Ahora cada
pantalla se construye UNA vez, la primera vez que se visita (lazy), queda
apilada en content_stack (ft.Stack), y _navegar() solo alterna `visible`.
Los valores de los controles (texto tipeado, checkboxes, filtros) viven
del lado de Python, así que sobreviven a quedar ocultos.

Tres detalles que `visible` solo no resuelve:

1. Scroll. `visible=False` saca el control del árbol que dibuja Flutter
   (docstring de Control.visible en Flet 0.86.5) y con él la posición de
   scroll, que vive del lado del cliente. _preparar_scroll() le engancha
   un on_scroll a cada control scrolleable de la pantalla (si no tiene uno
   propio) que va guardando su posición, y _restaurar_scroll() la vuelve a
   aplicar con scroll_to() al mostrarla de nuevo. Ese on_scroll apaga el
   auto-update de Flet (ft.context.disable_auto_update()): un handler que
   no llama a update() termina en un page.update() completo automático, y
   con eventos de scroll cada 10 ms (scroll_interval default) eso
   re-diffeaba la página entera — todas las pantallas apiladas — mientras
   se scrolleaba. Además el intervalo se sube a SCROLL_INTERVALO_MS: solo
   hace falta la última posición.
2. Datos viejos. Una pantalla oculta no se entera de lo que se cargó en
   otra (una transacción nueva cambia los saldos de Cuentas y el "Real" de
   Presupuestos). _navegar() anota conn.total_changes (filas escritas en la
   conexión que comparten todos los services) al ocultar cada pantalla; si
   al volver cambió, esa pantalla se reconstruye con datos frescos. Si no
   cambió nada en el medio, se reusa tal cual, con todo su estado.
3. Hooks. Al ocultar/mostrar una pantalla, _notificar() recorre su árbol y
   llama a `control.data["al_ocultar"]` / `["al_mostrar"]` de cualquier
   control que los tenga (ej. el Registro esconde su barra flotante de
   selección, que vive en page.overlay y si no quedaría encima de las
   otras pantallas). Opt-in: una pantalla sin hooks no hace nada distinto.

"""
import asyncio
from typing import Callable, Iterator, Optional

import flet as ft

from db.database import DatabaseManager
from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.dashboard_service import DashboardService
from services.debts_service import DebtsService
from services.fees_service import FeesService
from services.presupuestos_service import PresupuestosService
from services.savings_service import SavingsService
from services.shared_expenses_service import SharedExpensesService
from services.transaction_service import TransactionService
from services.ingresos_proyectados_service import IngresosProyectadosService

from ui.screens import ingresos as ingresos_screen
from ui.screens import ahorros as ahorros_screen
from ui.screens import categorias as categorias_screen
from ui.screens import compras_cuotas as compras_cuotas_screen
from ui.screens import dashboard as dashboard_screen
from ui.screens import cuentas as cuentas_screen
from ui.screens import deudas_y_compartidos as deudas_y_compartidos_screen
from ui.screens import estadisticas as estadisticas_screen
from ui.screens import presupuestos as presupuestos_screen

# --- Configuración de layout ---
SIDEBAR_ANCHO_EXPANDIDO = 220
SIDEBAR_ANCHO_COLAPSADO = 64
SIDEBAR_PADDING_VERTICAL = 16
SIDEBAR_PADDING_HORIZONTAL = 8
CONTENIDO_PADDING = 24
COLOR_FONDO_APP = "#1a1a1a"
# Espera antes de restaurar el scroll de una pantalla recién mostrada.
SCROLL_RESTAURAR_DELAY_S = 0.05
# Throttling de on_scroll (default de Flet: 10 ms) — ver docstring, punto 1.
SCROLL_INTERVALO_MS = 200


def _recorrer_controles(control: ft.Control) -> Iterator[ft.Control]:
    """El control y todos sus descendientes vía `content` / `controls` (alcanza para Container/Row/Column/Stack)."""
    yield control
    contenido = getattr(control, "content", None)
    if isinstance(contenido, ft.Control):
        yield from _recorrer_controles(contenido)
    for hijo in getattr(control, "controls", None) or []:
        if isinstance(hijo, ft.Control):
            yield from _recorrer_controles(hijo)


def build_app(page: ft.Page, db: DatabaseManager) -> None:
    page.title = "DeltaBalance"
    page.padding = 0
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = COLOR_FONDO_APP

    accounts_service = AccountsService(db)
    dashboard_service = DashboardService(db)
    transaction_service = TransactionService(db)
    fees_service = FeesService(db)
    categorias_service = CategoriasService(db)
    shared_expenses_service = SharedExpensesService(db)
    presupuestos_service = PresupuestosService(db)
    savings_service = SavingsService(db)
    debts_service = DebtsService(db)
    ingresos_service = IngresosProyectadosService(db)

    # ------------------------------------------------------------
    # PANTALLAS PERSISTENTES (ver docstring del módulo, "Navegación")
    # ------------------------------------------------------------
    # Cada pantalla se construye una sola vez (lazy, la primera vez que se
    # visita) y queda apilada en content_stack; navegar solo cambia cuál
    # está visible. Cada una va envuelta en un Container posicionado en los
    # cuatro bordes para que ocupe el Stack entero, igual que ocupaba
    # content_area antes.
    content_stack = ft.Stack(expand=True)
    content_area = ft.Container(expand=True, padding=CONTENIDO_PADDING, content=content_stack)

    pantallas: dict[str, ft.Control] = {}
    # conn.total_changes al ocultar cada pantalla — ver _navegar().
    cambios_al_ocultar: dict[str, int] = {}
    # id(control scrolleable) → última posición, ver _preparar_scroll().
    posiciones_scroll: dict[int, float] = {}
    pantalla_actual: dict[str, Optional[str]] = {"nombre": None}

    builders: dict[str, Callable[[], ft.Control]] = {
        "registro": lambda: dashboard_screen.build(
            page,
            accounts_service,
            dashboard_service,
            transaction_service,
            categorias_service,
            shared_expenses_service,
            savings_service,
            debts_service,
            on_ir_a_cuentas=mostrar_cuentas,
        ),
        "ingresos": lambda: ingresos_screen.build(
            page, ingresos_service, accounts_service, on_volver=mostrar_dashboard,
        ),
        "compras_cuotas": lambda: compras_cuotas_screen.build(
            page, accounts_service, categorias_service, fees_service, shared_expenses_service,
            on_volver=mostrar_dashboard,
        ),
        "deudas_y_compartidos": lambda: deudas_y_compartidos_screen.build(
            page, debts_service, shared_expenses_service, accounts_service, on_volver=mostrar_dashboard,
        ),
        "estadisticas": lambda: estadisticas_screen.build(
            page, accounts_service, dashboard_service, on_volver=mostrar_dashboard,
        ),
        "presupuestos": lambda: presupuestos_screen.build(
            page, categorias_service, presupuestos_service, dashboard_service, accounts_service,
            on_volver=mostrar_dashboard,
        ),
        "ahorros": lambda: ahorros_screen.build(
            page, savings_service, accounts_service, on_volver=mostrar_dashboard,
        ),
        "cuentas": lambda: cuentas_screen.build(
            page, accounts_service, on_volver=mostrar_dashboard, modo_onboarding=False,
        ),
        "categorias": lambda: categorias_screen.build(
            page, categorias_service, on_volver=mostrar_dashboard,
        ),
        "onboarding": lambda: cuentas_screen.build(
            page, accounts_service, on_volver=None, modo_onboarding=True,
            on_primera_cuenta_creada=mostrar_dashboard,
        ),
    }

    def _cambios_en_db() -> int:
        # Filas escritas en la conexión desde que se abrió — lo usan todos
        # los services (un único DatabaseManager), así que cualquier alta/
        # edición/borrado de cualquier pantalla lo mueve.
        return db.conn.total_changes

    def _notificar(raiz: ft.Control, evento: str) -> None:
        for control in _recorrer_controles(raiz):
            hooks = control.data
            if isinstance(hooks, dict) and callable(hooks.get(evento)):
                hooks[evento]()

    def _registrar_scroll(e: ft.OnScrollEvent, clave: int) -> None:
        # Solo anota la posición: sin esto, Flet haría un page.update()
        # completo al terminar cada evento de scroll (ver docstring, punto 1).
        ft.context.disable_auto_update()
        posiciones_scroll[clave] = e.pixels

    def _preparar_scroll(raiz: ft.Control) -> None:
        for control in _recorrer_controles(raiz):
            if getattr(control, "scroll", None) is None or not hasattr(control, "scroll_to"):
                continue
            if getattr(control, "on_scroll", False) is None:
                control.on_scroll = lambda e, clave=id(control): _registrar_scroll(e, clave)
                control.scroll_interval = SCROLL_INTERVALO_MS

    async def _scroll_diferido(control: ft.Control, offset: float) -> None:
        # Le da tiempo al cliente a volver a construir el widget recién
        # mostrado antes de moverle el scroll.
        await asyncio.sleep(SCROLL_RESTAURAR_DELAY_S)
        await control.scroll_to(offset=offset, duration=0)

    def _restaurar_scroll(raiz: ft.Control) -> None:
        for control in _recorrer_controles(raiz):
            offset = posiciones_scroll.get(id(control))
            if offset and hasattr(control, "scroll_to"):
                page.run_task(_scroll_diferido, control, offset)

    def _navegar(nombre: str) -> None:
        anterior = pantalla_actual["nombre"]
        if anterior == nombre and nombre in pantallas:
            return

        if anterior in pantallas:
            _notificar(pantallas[anterior], "al_ocultar")
            cambios_al_ocultar[anterior] = _cambios_en_db()
            if anterior == "onboarding":
                # Solo se usa una vez: no tiene sentido dejarla apilada.
                content_stack.controls.remove(pantallas.pop(anterior))

        # Si la base cambió desde que esta pantalla se ocultó (se cargó algo
        # en OTRA pantalla), se reconstruye: sus datos quedaron viejos. Lo
        # que la propia pantalla escribió antes de ocultarse ya está
        # contado en cambios_al_ocultar, así que no la invalida.
        if nombre in pantallas and cambios_al_ocultar.get(nombre) != _cambios_en_db():
            content_stack.controls.remove(pantallas.pop(nombre))

        mostrada_de_nuevo = nombre in pantallas
        if not mostrada_de_nuevo:
            pantallas[nombre] = ft.Container(left=0, top=0, right=0, bottom=0, content=builders[nombre]())
            content_stack.controls.append(pantallas[nombre])

        for n, pantalla in pantallas.items():
            pantalla.visible = (n == nombre)
        pantalla_actual["nombre"] = nombre
        _preparar_scroll(pantallas[nombre])
        page.update()

        _notificar(pantallas[nombre], "al_mostrar")
        if mostrada_de_nuevo:
            _restaurar_scroll(pantallas[nombre])
        page.update()

    def mostrar_dashboard(e=None) -> None:
        _navegar("registro")

    def mostrar_ingresos(e=None) -> None:
        _navegar("ingresos")

    def mostrar_compras_cuotas(e=None) -> None:
        _navegar("compras_cuotas")

    def mostrar_deudas_y_compartidos(e=None) -> None:
        _navegar("deudas_y_compartidos")

    def mostrar_estadisticas(e=None) -> None:
        _navegar("estadisticas")

    def mostrar_presupuestos(e=None) -> None:
        _navegar("presupuestos")

    def mostrar_ahorros(e=None) -> None:
        _navegar("ahorros")

    def mostrar_cuentas(e=None) -> None:
        _navegar("cuentas")

    def mostrar_categorias(e=None) -> None:
        _navegar("categorias")

    def mostrar_onboarding() -> None:
        _navegar("onboarding")

    # ------------------------------------------------------------
    # SIDEBAR (colapsable — ver docstring del módulo)
    # ------------------------------------------------------------
    # "Cuentas"/"Categorías" no son ítems de primer nivel: viven dentro de
    # "Configuración". "Compras en cuotas"/"Presupuestos"/"Estadísticas" sí
    # son de primer nivel (pantallas de feature, no de configuración).

    # Colapsada por default (pedido explícito) — solo el botón de flecha la
    # expande/contrae.
    estado_sidebar = {"colapsado": True}

    def _item_nav(texto: str, icono: str, on_click, expandido: bool) -> ft.Control:
        if not expandido:
            return ft.IconButton(icon=icono, tooltip=texto, on_click=on_click)
        return ft.TextButton(
            content=ft.Row(
                [ft.Icon(icono, size=18), ft.Text(texto)],
                spacing=10,
            ),
            style=ft.ButtonStyle(alignment=ft.Alignment.CENTER_LEFT, padding=12),
            on_click=on_click,
        )

    def _toggle_sidebar(e=None) -> None:
        estado_sidebar["colapsado"] = not estado_sidebar["colapsado"]
        _actualizar_sidebar()

    def _boton_toggle() -> ft.Control:
        colapsado = estado_sidebar["colapsado"]
        return ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT if colapsado else ft.Icons.CHEVRON_LEFT,
            tooltip="EXPANDIR" if colapsado else "CONTRAER",
            on_click=_toggle_sidebar,
        )

    def _contenido_sidebar(expandido: bool) -> ft.Control:
        encabezado = (
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                content=ft.Text("DeltaBalance", size=18, weight=ft.FontWeight.BOLD),
            )
            if expandido
            else ft.Container(
                padding=ft.Padding.symmetric(vertical=8),
                alignment=ft.Alignment.CENTER,
                content=ft.Text("DB", size=18, weight=ft.FontWeight.BOLD),
            )
        )

        controles: list[ft.Control] = [
            encabezado,
            _item_nav("REGISTRO", ft.Icons.RECEIPT_LONG, mostrar_dashboard, expandido),
            _item_nav("INGRESOS", ft.Icons.TRENDING_UP, mostrar_ingresos, expandido),
            _item_nav("PRESUPUESTOS", ft.Icons.SAVINGS, mostrar_presupuestos, expandido),
            _item_nav("COMPRAS EN CUOTAS", ft.Icons.CREDIT_CARD, mostrar_compras_cuotas, expandido),
            _item_nav("DEUDAS Y COMPARTIDOS", ft.Icons.HANDSHAKE, mostrar_deudas_y_compartidos, expandido),
            _item_nav("ESTADÍSTICAS", ft.Icons.BAR_CHART, mostrar_estadisticas, expandido),
            ft.Container(expand=True),
            ft.Divider(),
        ]
        if expandido:
            controles.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                    content=ft.Text(
                        "CONFIGURACIÓN", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.OUTLINE,
                    ),
                )
            )
        controles.append(_item_nav("CUENTAS", ft.Icons.ACCOUNT_BALANCE, mostrar_cuentas, expandido))
        controles.append(_item_nav("CATEGORÍAS", ft.Icons.CATEGORY, mostrar_categorias, expandido))
        controles.append(_boton_toggle())

        return ft.Column(controles, expand=True)

    def _actualizar_sidebar() -> None:
        colapsado = estado_sidebar["colapsado"]
        sidebar.width = SIDEBAR_ANCHO_COLAPSADO if colapsado else SIDEBAR_ANCHO_EXPANDIDO
        sidebar.content = _contenido_sidebar(expandido=not colapsado)
        sidebar.update()

    sidebar = ft.Container(
        width=SIDEBAR_ANCHO_COLAPSADO,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        padding=ft.Padding.symmetric(
            vertical=SIDEBAR_PADDING_VERTICAL, horizontal=SIDEBAR_PADDING_HORIZONTAL,
        ),
        content=None,
    )
    sidebar.content = _contenido_sidebar(expandido=not estado_sidebar["colapsado"])

    page.controls.clear()
    page.add(
        ft.Row(
            [sidebar, ft.VerticalDivider(width=1), content_area],
            expand=True,
            spacing=0,
        )
    )

    hay_cuentas = len(accounts_service.list_accounts()) > 0
    if hay_cuentas:
        mostrar_dashboard()
    else:
        mostrar_onboarding()
