"""
DeltaBalance — ui/app.py

Shell de la aplicación Flet: sidebar (nav de primer nivel + sección
"Configuración") y área de contenido donde se montan las pantallas de
ui/screens/.

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
from services.transaction_service import TransactionService
from ui.screens import compras_cuotas as compras_cuotas_screen
from ui.screens import dashboard as dashboard_screen
from ui.screens import cuentas as cuentas_screen


def build_app(page: ft.Page, db: DatabaseManager) -> None:
    page.title = "DeltaBalance"
    page.padding = 0

    accounts_service = AccountsService(db)
    dashboard_service = DashboardService(db)
    transaction_service = TransactionService(db)
    fees_service = FeesService(db)
    categorias_service = CategoriasService(db)

    content_area = ft.Container(expand=True, padding=24)

    def mostrar_dashboard(e=None) -> None:
        content_area.content = dashboard_screen.build(
            page,
            accounts_service,
            dashboard_service,
            transaction_service,
            categorias_service,
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

    def mostrar_cuentas(e=None) -> None:
        content_area.content = cuentas_screen.build(
            page,
            accounts_service,
            on_volver=mostrar_dashboard,
            modo_onboarding=False,
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
    # SIDEBAR
    # ------------------------------------------------------------
    # "Cuentas" no es un ítem de primer nivel: vive dentro de "Configuración",
    # que por ahora solo la contiene a ella pero queda lista para crecer con
    # más ítems de config más adelante. "Compras en cuotas" sí es de primer
    # nivel (pantalla de feature, no de configuración).

    def _item_nav(texto: str, icono: str, on_click) -> ft.Control:
        return ft.TextButton(
            content=ft.Row(
                [ft.Icon(icono, size=18), ft.Text(texto)],
                spacing=10,
            ),
            style=ft.ButtonStyle(alignment=ft.Alignment.CENTER_LEFT, padding=12),
            on_click=on_click,
        )

    sidebar = ft.Container(
        width=220,
        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        padding=ft.Padding.symmetric(vertical=16, horizontal=8),
        content=ft.Column(
            [
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                    content=ft.Text("DeltaBalance", size=18, weight=ft.FontWeight.BOLD),
                ),
                _item_nav("Dashboard", ft.Icons.DASHBOARD, mostrar_dashboard),
                _item_nav("Compras en cuotas", ft.Icons.CREDIT_CARD, mostrar_compras_cuotas),
                ft.Container(expand=True),
                ft.Divider(),
                ft.Container(
                    padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                    content=ft.Text(
                        "CONFIGURACIÓN", size=11, weight=ft.FontWeight.BOLD, color=ft.Colors.OUTLINE,
                    ),
                ),
                _item_nav("Cuentas", ft.Icons.ACCOUNT_BALANCE, mostrar_cuentas),
            ],
            expand=True,
        ),
    )

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
