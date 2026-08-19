"""
DeltaBalance — ui/screens/dashboard.py

Pantalla principal: patrimonio total → Registro de transacciones (tabla
estilo planilla con su propia barra de herramientas — mes/año, modo
Todos-del-mes/Últimos-N, filtro por banco — ver
ui/components/registro_transacciones.py) → gráfico de gasto del mes por
categoría con su detalle numérico.

No hay botón "+"/FloatingActionButton en esta pantalla: quedó redundante
una vez que el Registro tiene su propia fila de alta en línea arriba de la
tabla — cargar un movimiento ya no necesita abrir ningún diálogo.

Solo usa AccountsService/DashboardService/TransactionService/
CategoriasService — nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).
Compras en cuotas vive en su propia pantalla (ui/screens/compras_cuotas.py)
— este archivo no la toca.

SnackBar: page.overlay + control.open — confirmado para Flet 0.86.5, ver
docs/FLET_API_NOTES.md. Esta pantalla no abre AlertDialog (ya no hay FAB
con diálogo de alta rápida — el Registro cubre ese caso con su fila en
línea), así que page.show_dialog()/page.pop_dialog() no aplican acá.

Gráfico de gasto por categoría: flet_charts.PieChart — IMPLEMENTADO pero
SIN CONFIRMAR contra una instalación real (flet_charts no está disponible en
el entorno donde se escribió este archivo, ver docs/FLET_API_NOTES.md regla
2). Correr `flet run main.py` y, si algo no coincide (nombre de import,
parámetros de PieChart/PieChartSection), avisar para ajustarlo y recién ahí
agregar la confirmación a FLET_API_NOTES.md.
"""

from datetime import date
from typing import Callable

import flet as ft
import flet_charts as fc

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.dashboard_service import DashboardService
from services.transaction_service import TransactionService
from ui.components import registro_transacciones
from utils.money import amount_display

# --- Configuración de layout ---
ALTURA_GRAFICO_TORTA = 220
# Paleta fija para las porciones del gráfico de torta — se repite por
# módulo si hay más categorías que colores.
PALETA_TORTA = [
    ft.Colors.BLUE, ft.Colors.RED, ft.Colors.GREEN, ft.Colors.ORANGE,
    ft.Colors.PURPLE, ft.Colors.TEAL, ft.Colors.PINK, ft.Colors.BROWN,
    ft.Colors.INDIGO, ft.Colors.AMBER,
]


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    dashboard_service: DashboardService,
    transaction_service: TransactionService,
    categorias_service: CategoriasService,
    on_ir_a_cuentas: Callable[[], None],
) -> ft.Control:
    hoy = date.today()
    # estado sobrevive entre refrescos (se define una sola vez acá, se muta
    # in place) — mes/anio los administra ahora la barra de herramientas
    # del Registro (ver ui/components/registro_transacciones.py), pero
    # _seccion_gasto() de acá abajo sigue leyéndolos del mismo dict para
    # mostrar el gráfico del mismo período. _refrescar_datos() reconstruye
    # TODA la pantalla después de cualquier alta, edición, o cambio en la
    # barra de herramientas del Registro — por eso todo ese estado vive acá
    # y no dentro del componente, que se reconstruye entero cada vez.
    estado = {"mes": hoy.month, "anio": hoy.year}

    contenedor_datos = ft.Column(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO)

    # ------------------------------------------------------------
    # HELPERS DE FORMATO
    # ------------------------------------------------------------

    def _monedas_por_codigo() -> dict:
        return {m["codigo"]: m for m in accounts_service.list_currencies()}

    def _monedas_por_id() -> dict:
        return {m["id"]: m for m in accounts_service.list_currencies()}

    # ------------------------------------------------------------
    # PATRIMONIO TOTAL
    # ------------------------------------------------------------

    def _tarjeta_patrimonio() -> ft.Control:
        patrimonio = dashboard_service.get_patrimonio_total()
        monedas = _monedas_por_codigo()

        if not patrimonio:
            contenido = ft.Text(
                "Todavía no hay saldo cargado en ninguna cuenta.",
                italic=True,
                color=ft.Colors.OUTLINE,
            )
        else:
            # Una "tile" por moneda, en una fila que envuelve — nunca se
            # fuerza una cantidad fija de columnas, se adapta a cuántas
            # monedas haya de verdad.
            tiles = []
            for codigo, total_minor in patrimonio.items():
                moneda = monedas.get(codigo)
                decimales = moneda["decimales"] if moneda else 2
                simbolo = (moneda["simbolo"] if moneda else "") or ""
                tiles.append(
                    ft.Container(
                        padding=16,
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                        border_radius=8,
                        content=ft.Column(
                            [
                                ft.Text(codigo, size=12, color=ft.Colors.OUTLINE),
                                ft.Text(
                                    amount_display(total_minor, decimales, simbolo),
                                    size=28,
                                    weight=ft.FontWeight.BOLD,
                                ),
                            ],
                            spacing=2,
                        ),
                    )
                )
            contenido = ft.Row(tiles, wrap=True, spacing=12, run_spacing=12)

        return ft.Container(
            padding=16,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text("Patrimonio total", size=16, weight=ft.FontWeight.BOLD),
                            ft.TextButton(
                                content=ft.Text("Ver cuentas"),
                                icon=ft.Icons.ACCOUNT_BALANCE,
                                on_click=lambda e: on_ir_a_cuentas(),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Container(height=8),
                    contenido,
                ],
                spacing=0,
            ),
        )

    # ------------------------------------------------------------
    # GASTO DEL MES POR CATEGORÍA (gráfico de torta + detalle)
    # ------------------------------------------------------------

    def _grafico_torta(gastos: list[dict]) -> ft.Control:
        """
        PieChart de flet_charts con el gasto del mes. Una torta solo puede
        representar una moneda a la vez (mezclar ARS y USD en una sola
        torta compararía montos no comparables) — si el mes tiene gasto en
        más de una moneda, se grafica la de mayor gasto total y se avisa
        que el resto queda solo en el detalle numérico de abajo.

        SIN CONFIRMAR contra una instalación real de flet_charts — ver
        docstring del módulo.
        """
        if not gastos:
            return ft.Text("Sin gastos para graficar este mes.", italic=True, color=ft.Colors.OUTLINE)

        monedas = _monedas_por_id()
        totales_por_moneda: dict[int, int] = {}
        for g in gastos:
            totales_por_moneda[g["moneda_id"]] = totales_por_moneda.get(g["moneda_id"], 0) + g["monto_total_minor"]

        moneda_id_elegida = max(totales_por_moneda, key=totales_por_moneda.get)
        moneda = monedas.get(moneda_id_elegida)
        codigo = moneda["codigo"] if moneda else ""

        gastos_moneda = [g for g in gastos if g["moneda_id"] == moneda_id_elegida]
        total = sum(g["monto_total_minor"] for g in gastos_moneda)

        secciones = [
            fc.PieChartSection(
                value=g["monto_total_minor"],
                title=f"{(g['monto_total_minor'] / total * 100) if total else 0:.0f}%",
                color=PALETA_TORTA[i % len(PALETA_TORTA)],
                radius=90,
            )
            for i, g in enumerate(gastos_moneda)
        ]

        grafico = ft.Container(
            height=ALTURA_GRAFICO_TORTA,
            content=fc.PieChart(
                sections=secciones,
                sections_space=2,
                center_space_radius=40,
                expand=True,
            ),
        )

        controles: list[ft.Control] = [grafico]
        if len(totales_por_moneda) > 1:
            controles.append(
                ft.Text(
                    f"Mostrando solo {codigo} (la de mayor gasto este mes) — "
                    f"el detalle de abajo incluye todas las monedas.",
                    size=11,
                    italic=True,
                    color=ft.Colors.OUTLINE,
                )
            )
        return ft.Column(controles, spacing=4)

    def _seccion_gasto(mes: int, anio: int) -> ft.Control:
        gastos = dashboard_service.get_gasto_por_categoria(mes, anio)
        comparacion = {
            c["categoria_id"]: c for c in dashboard_service.get_comparacion_presupuesto(mes, anio)
        }
        monedas = _monedas_por_id()

        if not gastos:
            lista = ft.Text(
                "No hay gastos registrados en este mes.",
                italic=True,
                color=ft.Colors.OUTLINE,
            )
        else:
            filas = []
            for g in gastos:
                moneda = monedas.get(g["moneda_id"])
                decimales = moneda["decimales"] if moneda else 2
                simbolo = (moneda["simbolo"] if moneda else "") or ""
                monto_texto = amount_display(g["monto_total_minor"], decimales, simbolo)

                subtitulo_controles = []
                presupuesto = comparacion.get(g["categoria_id"])
                if presupuesto is not None:
                    estimado_texto = amount_display(presupuesto["estimado_minor"], decimales, simbolo)
                    subtitulo_controles.append(
                        ft.Text(f"presupuestado: {estimado_texto}", size=11, color=ft.Colors.OUTLINE)
                    )

                filas.append(
                    ft.Container(
                        padding=ft.Padding.symmetric(vertical=8, horizontal=4),
                        content=ft.Row(
                            [
                                ft.Column(
                                    [ft.Text(g["categoria_nombre"], weight=ft.FontWeight.W_500)]
                                    + subtitulo_controles,
                                    spacing=2,
                                    expand=True,
                                ),
                                ft.Text(monto_texto, weight=ft.FontWeight.BOLD),
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                    )
                )
            lista = ft.Column(filas, spacing=0)

        return ft.Container(
            padding=16,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Column(
                [
                    ft.Text("Gasto del mes por categoría", size=16, weight=ft.FontWeight.BOLD),
                    ft.Container(height=8),
                    _grafico_torta(gastos),
                    ft.Container(height=8),
                    lista,
                ],
                spacing=0,
            ),
        )

    # ------------------------------------------------------------
    # REFRESCO
    # ------------------------------------------------------------

    def _refrescar_datos() -> None:
        contenedor_datos.controls = [
            _tarjeta_patrimonio(),
            registro_transacciones.build(
                page,
                accounts_service,
                categorias_service,
                transaction_service,
                estado,
                on_cambio=_refrescar_datos,
            ),
            _seccion_gasto(estado["mes"], estado["anio"]),
        ]
        page.update()

    _refrescar_datos()

    return ft.Column(
        [
            ft.Text("Dashboard", size=24, weight=ft.FontWeight.BOLD),
            ft.Container(height=8),
            contenedor_datos,
        ],
        spacing=8,
        expand=True,
    )
