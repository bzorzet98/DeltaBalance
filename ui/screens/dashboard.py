"""
DeltaBalance — ui/screens/dashboard.py

Pantalla principal: patrimonio total (fila horizontal compacta) →
movimientos por banco (desglose del mes/año seleccionado, ver
_tarjeta_desglose_banco() — Tarea 4 de docs/PROXIMOS_PASOS.md, SOLO bancos
con actividad real ese período, detección dinámica vía
DashboardService.get_movimientos_por_cuenta()) → Registro de transacciones
(tabla estilo planilla con su propia barra de herramientas — período
navegable, filtros de banco/categoría, búsqueda — ver
ui/components/registro_transacciones.py). Siempre muestra todas las
transacciones del período, sin selector de cantidad.

El gráfico de "Gasto del mes por categoría" que antes vivía acá se movió a
ui/screens/estadisticas.py, con su propio selector de período independiente
del de este Registro — dashboard.py ya no importa flet_charts ni construye
ningún gráfico.

No hay botón "+"/FloatingActionButton en esta pantalla: quedó redundante
una vez que el Registro tiene su propia fila de alta en línea arriba de la
tabla — cargar un movimiento ya no necesita abrir ningún diálogo.

Solo usa AccountsService/DashboardService/TransactionService/
CategoriasService/SharedExpensesService/SavingsService — nunca
repositories/ ni db/ directo (CLAUDE.md §2/§3). SavingsService se agregó
en la Tarea 1b de docs/PROXIMOS_PASOS.md — este archivo solo lo recibe y
lo pasa a través a ui/components/registro_transacciones.py (categoría
especial "Ahorro/Inversión"), no lo usa directo acá.
Compras en cuotas vive en su propia pantalla (ui/screens/compras_cuotas.py)
— este archivo no la toca.

SnackBar: page.overlay + control.open — confirmado para Flet 0.86.5, ver
docs/FLET_API_NOTES.md. Esta pantalla no abre AlertDialog (ya no hay FAB
con diálogo de alta rápida — el Registro cubre ese caso con su fila en
línea), así que page.show_dialog()/page.pop_dialog() no aplican acá.
"""

from datetime import date
from typing import Callable

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.dashboard_service import DashboardService
from services.debts_service import DebtsService
from services.savings_service import SavingsService
from services.shared_expenses_service import SharedExpensesService
from services.transaction_service import TransactionService
from ui.components import registro_transacciones
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    dashboard_service: DashboardService,
    transaction_service: TransactionService,
    categorias_service: CategoriasService,
    shared_expenses_service: SharedExpensesService,
    savings_service: SavingsService,
    debts_service: DebtsService,
    on_ir_a_cuentas: Callable[[], None],
) -> ft.Control:
    hoy = date.today()
    # estado sobrevive entre refrescos (se define una sola vez acá, se muta
    # in place) — mes/anio/modo/filtros/búsqueda los administra la barra de
    # herramientas del Registro (ver ui/components/registro_transacciones.py).
    # _refrescar_datos() reconstruye TODA la pantalla después de cualquier
    # alta, edición, o cambio en la barra de herramientas del Registro — por
    # eso todo ese estado vive acá y no dentro del componente, que se
    # reconstruye entero cada vez.
    estado = {"mes": hoy.month, "anio": hoy.year}

    contenedor_datos = ft.Column(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO)

    # ------------------------------------------------------------
    # PATRIMONIO TOTAL (fila horizontal compacta)
    # ------------------------------------------------------------

    def _tarjeta_patrimonio() -> ft.Control:
        patrimonio = dashboard_service.get_patrimonio_total()
        monedas = {m["codigo"]: m for m in accounts_service.list_currencies()}

        if not patrimonio:
            contenido = ft.Text(
                "Todavía no hay saldo cargado en ninguna cuenta.",
                italic=True,
                color=ft.Colors.OUTLINE,
                size=TypographyTokens.TABLE_CONTENT_SIZE,
            )
        else:
            # Una entrada por moneda, en una única fila corta — nunca se
            # fuerza una cantidad fija de columnas, se adapta a cuántas
            # monedas haya de verdad.
            partes: list[ft.Control] = []
            for codigo, total_minor in patrimonio.items():
                moneda = monedas.get(codigo)
                decimales = moneda["decimales"] if moneda else 2
                simbolo = (moneda["simbolo"] if moneda else "") or ""
                partes.append(
                    ft.Row(
                        [
                            ft.Text(codigo, size=TypographyTokens.LABEL_SIZE, color=ft.Colors.OUTLINE),
                            ft.Text(
                                amount_display(total_minor, decimales, simbolo),
                                size=TypographyTokens.SECTION_TITLE_SIZE,
                                weight=ft.FontWeight.BOLD,
                            ),
                        ],
                        spacing=6,
                    )
                )
            contenido = ft.Row(partes, spacing=20, wrap=True)

        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Text(
                        "Patrimonio total",
                        size=TypographyTokens.SECTION_TITLE_SIZE,
                        weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                    ),
                    contenido,
                    ft.TextButton(
                        content=ft.Text("Ver cuentas"),
                        icon=ft.Icons.ACCOUNT_BALANCE,
                        on_click=lambda e: on_ir_a_cuentas(),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # ------------------------------------------------------------
    # MOVIMIENTOS POR BANCO (desglose compacto — Tarea 4)
    # ------------------------------------------------------------

    def _tarjeta_desglose_banco() -> ft.Control:
        """
        Una fila por banco con actividad real en estado["mes"]/["anio"]
        (detección dinámica — ver DashboardService.get_movimientos_por_cuenta(),
        nunca lista fija de cuentas). Si el filtro de Banco de la barra de
        herramientas del Registro está activo, colapsa a mostrar solo esa
        cuenta (elegido en vez de ocultar la tarjeta entera, para que el
        layout no salte al tocar el filtro).
        """
        desglose = dashboard_service.get_movimientos_por_cuenta(estado["mes"], estado["anio"])
        filtro_banco = estado.get("filtro_banco")
        if filtro_banco is not None:
            desglose = [d for d in desglose if d["cuenta_id"] == filtro_banco]

        if not desglose:
            contenido = ft.Text(
                "Sin movimientos por banco este período.",
                italic=True,
                color=ft.Colors.OUTLINE,
                size=TypographyTokens.TABLE_CONTENT_SIZE,
            )
        else:
            filas = []
            for d in desglose:
                color_neto = ft.Colors.GREEN if d["net_minor"] >= 0 else ft.Colors.RED
                filas.append(
                    ft.Row(
                        [
                            ft.Text(
                                d["account_name"],
                                size=TypographyTokens.TABLE_CONTENT_SIZE,
                            ),
                            ft.Text(
                                amount_display(d["net_minor"], d["decimales"], d["currency_symbol"] or ""),
                                size=TypographyTokens.TABLE_CONTENT_SIZE,
                                weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                                color=color_neto,
                            ),
                        ],
                        spacing=10,
                    )
                )
            contenido = ft.Row(filas, spacing=20, wrap=True)

        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Text(
                        "Movimientos por banco",
                        size=TypographyTokens.SECTION_TITLE_SIZE,
                        weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                    ),
                    contenido,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    # ------------------------------------------------------------
    # REFRESCO
    # ------------------------------------------------------------

    def _refrescar_datos() -> None:
        contenedor_datos.controls = [
            _tarjeta_patrimonio(),
            _tarjeta_desglose_banco(),
            registro_transacciones.build(
                page,
                accounts_service,
                categorias_service,
                transaction_service,
                shared_expenses_service,
                savings_service,
                debts_service,
                estado,
                on_cambio=_refrescar_datos,
            ),
        ]
        page.update()

    _refrescar_datos()

    return ft.Column(
        [
            ft.Text("Dashboard", size=TypographyTokens.PAGE_TITLE_SIZE, weight=TypographyTokens.PAGE_TITLE_WEIGHT),
            ft.Container(height=8),
            contenedor_datos,
        ],
        spacing=8,
        expand=True,
    )
