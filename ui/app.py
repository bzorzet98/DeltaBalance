"""
DeltaBalance — ui/app.py

Shell de la aplicación Flet: sidebar colapsable (nav de primer nivel +
sección "Configuración") y área de contenido donde se montan las pantallas
de ui/screens/.

Sidebar colapsable (estado solo en memoria de sesión, se pierde al
reiniciar la app — no hay animación de ancho: no se pudo confirmar la API
de animación exacta contra Flet 0.86.5 real, ver docs/FLET_API_NOTES.md
regla 2, así que el cambio de ancho es instantáneo; animarlo queda como
mejora futura si se confirma la API corriendo la app): un botón de flecha
al final de la sidebar alterna entre expandida (ancho SIDEBAR_ANCHO_EXPANDIDO,
ícono + texto, flecha apuntando a la izquierda) y colapsada (ancho
SIDEBAR_ANCHO_COLAPSADO, solo íconos con tooltip al hover, flecha apuntando
a la derecha). `_actualizar_sidebar()` reconstruye el contenido del
Container de la sidebar sin tocar content_area.

Onboarding condicional: si AccountsService.list_accounts() está vacío (primera
vez que se abre la app), se muestra Cuentas primero en vez del Dashboard.
No hay una bandera de estado separada para "está en onboarding" — se
consulta list_accounts() directo en cada momento en que hace falta decidir
qué pantalla mostrar (acá al armar el shell, y de nuevo dentro de
cuentas.py al guardar la primera cuenta), así nunca puede quedar desincronizada
de la base real.
"""

import flet as ft

from db.database import DatabaseManager
from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.dashboard_service import DashboardService
from services.fees_service import FeesService
from services.shared_expenses_service import SharedExpensesService
from services.transaction_service import TransactionService
from ui.screens import categorias as categorias_screen
from ui.screens import compras_cuotas as compras_cuotas_screen
from ui.screens import dashboard as dashboard_screen
from ui.screens import cuentas as cuentas_screen
from ui.screens import estadisticas as estadisticas_screen

# --- Configuración de layout ---
SIDEBAR_ANCHO_EXPANDIDO = 220
SIDEBAR_ANCHO_COLAPSADO = 64
SIDEBAR_PADDING_VERTICAL = 16
SIDEBAR_PADDING_HORIZONTAL = 8


def build_app(page: ft.Page, db: DatabaseManager) -> None:
    page.title = "DeltaBalance"
    page.padding = 0

    accounts_service = AccountsService(db)
    dashboard_service = DashboardService(db)
    transaction_service = TransactionService(db)
    fees_service = FeesService(db)
    categorias_service = CategoriasService(db)
    shared_expenses_service = SharedExpensesService(db)

    content_area = ft.Container(expand=True, padding=24)

    def mostrar_dashboard(e=None) -> None:
        content_area.content = dashboard_screen.build(
            page,
            accounts_service,
            dashboard_service,
            transaction_service,
            categorias_service,
            shared_expenses_service,
            on_ir_a_cuentas=mostrar_cuentas,
        )
        page.update()

    def mostrar_compras_cuotas(e=None) -> None:
        content_area.content = compras_cuotas_screen.build(
            page,
            accounts_service,
            categorias_service,
            fees_service,
            on_volver=mostrar_dashboard,
        )
        page.update()

    def mostrar_estadisticas(e=None) -> None:
        content_area.content = estadisticas_screen.build(
            page,
            accounts_service,
            dashboard_service,
            on_volver=mostrar_dashboard,
        )
        page.update()

    def mostrar_cuentas(e=None) -> None:
        content_area.content = cuentas_screen.build(
            page,
            accounts_service,
            on_volver=mostrar_dashboard,
            modo_onboarding=False,
        )
        page.update()

    def mostrar_categorias(e=None) -> None:
        content_area.content = categorias_screen.build(
            page,
            categorias_service,
            on_volver=mostrar_dashboard,
        )
        page.update()

    def mostrar_onboarding() -> None:
        content_area.content = cuentas_screen.build(
            page,
            accounts_service,
            on_volver=None,
            modo_onboarding=True,
            on_primera_cuenta_creada=mostrar_dashboard,
        )
        page.update()

    # ------------------------------------------------------------
    # SIDEBAR (colapsable — ver docstring del módulo)
    # ------------------------------------------------------------
    # "Cuentas"/"Categorías" no son ítems de primer nivel: viven dentro de
    # "Configuración". "Compras en cuotas"/"Estadísticas" sí son de primer
    # nivel (pantallas de feature, no de configuración).

    estado_sidebar = {"colapsado": False}

    def _item_nav(texto: str, icono: str, on_click) -> ft.Control:
        if estado_sidebar["colapsado"]:
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
            tooltip="Expandir" if colapsado else "Colapsar",
            on_click=_toggle_sidebar,
        )

    def _contenido_sidebar() -> ft.Control:
        colapsado = estado_sidebar["colapsado"]

        encabezado = (
            ft.Container(
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                content=ft.Text("DeltaBalance", size=18, weight=ft.FontWeight.BOLD),
            )
            if not colapsado
            else ft.Container(
                padding=ft.Padding.symmetric(vertical=8),
                alignment=ft.Alignment.CENTER,
                content=ft.Text("DB", size=18, weight=ft.FontWeight.BOLD),
            )
        )

        controles: list[ft.Control] = [
            encabezado,
            _item_nav("Dashboard", ft.Icons.DASHBOARD, mostrar_dashboard),
            _item_nav("Compras en cuotas", ft.Icons.CREDIT_CARD, mostrar_compras_cuotas),
            _item_nav("Estadísticas", ft.Icons.BAR_CHART, mostrar_estadisticas),
            ft.Container(expand=True),
            ft.Divider(),
        ]
        if not colapsado:
            controles.append(
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                    content=ft.Text(
                        "CONFIGURACIÓN", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.OUTLINE,
                    ),
                )
            )
        controles.append(_item_nav("Cuentas", ft.Icons.ACCOUNT_BALANCE, mostrar_cuentas))
        controles.append(_item_nav("Categorías", ft.Icons.CATEGORY, mostrar_categorias))
        controles.append(_boton_toggle())

        return ft.Column(controles, expand=True)

    def _actualizar_sidebar() -> None:
        sidebar.width = SIDEBAR_ANCHO_COLAPSADO if estado_sidebar["colapsado"] else SIDEBAR_ANCHO_EXPANDIDO
        sidebar.content = _contenido_sidebar()
        page.update()

    sidebar = ft.Container(
        width=SIDEBAR_ANCHO_EXPANDIDO,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        padding=ft.Padding.symmetric(
            vertical=SIDEBAR_PADDING_VERTICAL, horizontal=SIDEBAR_PADDING_HORIZONTAL,
        ),
        content=None,
    )
    sidebar.content = _contenido_sidebar()

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
