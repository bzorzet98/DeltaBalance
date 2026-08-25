"""
DeltaBalance — ui/screens/estadisticas.py

Pantalla de Estadísticas: gráfico de "Gasto del mes por categoría" (PieChart
+ detalle numérico) que antes vivía en ui/screens/dashboard.py — se movió
acá completo, con su propio selector de período (ui/components/
selector_periodo.py) independiente del período del Registro de
transacciones del dashboard. Ambas pantallas mantienen su propio dict de
`estado` — cambiar el mes acá no afecta lo que ve el Registro, y viceversa.

DashboardService: se sigue usando tal cual (no se renombra ni se crea un
EstadisticasService nuevo). Motivo: DashboardService ya es un servicio de
solo lectura/agregación (patrimonio + gasto por categoría + comparación de
presupuesto) que no le pertenece a ninguna pantalla en particular — antes
lo consumía dashboard.py, ahora lo consumen dashboard.py (patrimonio) y
esta pantalla (gasto por categoría), pero sigue siendo el mismo tipo de
composición de solo lectura que ya era. Renombrarlo o partirlo en dos
services para que cada uno tenga una sola pantalla "dueña" habría tocado
services/dashboard_service.py, sus imports en ui/app.py, y su verify sin
ningún cambio de comportamiento real — fuera del alcance de esta tarea.

Solo usa AccountsService/DashboardService — nunca repositories/ ni db/
directo (CLAUDE.md §2/§3).

Gráfico de gasto por categoría: flet_charts.PieChart — mismo código que
tenía dashboard.py antes de moverse, IMPLEMENTADO pero SIN CONFIRMAR contra
una instalación real (flet_charts no está disponible en el entorno donde se
escribió este archivo, ver docs/FLET_API_NOTES.md regla 2). Correr
`flet run main.py` y, si algo no coincide, avisar para ajustarlo.
"""

from datetime import date
from typing import Callable

import flet as ft
import flet_charts as fc

from services.accounts_service import AccountsService
from services.dashboard_service import DashboardService
from ui.components import selector_periodo
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---
ALTURA_GRAFICO_TORTA = 240
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
    on_volver: Callable[[], None] = None,
) -> ft.Control:
    hoy = date.today()
    # Estado propio de esta pantalla — no comparte dict con el `estado` del
    # dashboard (ver docstring del módulo).
    estado = {"mes": hoy.month, "anio": hoy.year}

    contenedor = ft.Column(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO)

    def _monedas_por_id() -> dict:
        return {m["id"]: m for m in accounts_service.list_currencies()}

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
                    size=TypographyTokens.LABEL_SIZE,
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
                        ft.Text(
                            f"presupuestado: {estimado_texto}",
                            size=TypographyTokens.LABEL_SIZE,
                            color=ft.Colors.OUTLINE,
                        )
                    )

                filas.append(
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    ft.Column(
                                        [
                                            ft.Text(
                                                g["categoria_nombre"],
                                                size=TypographyTokens.TABLE_CONTENT_SIZE,
                                                weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                                            )
                                        ]
                                        + subtitulo_controles,
                                        spacing=2,
                                        expand=True,
                                    ),
                                    ft.Text(
                                        monto_texto,
                                        size=TypographyTokens.TABLE_CONTENT_SIZE,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                ],
                                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            ),
                            ft.Divider(height=1),
                        ],
                        spacing=4,
                    )
                )
            lista = ft.Column(filas, spacing=4)

        return ft.Container(
            padding=16,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Column(
                [
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

    def _refrescar() -> None:
        selector = selector_periodo.build(estado, _refrescar)
        contenedor.controls = [
            selector,
            _seccion_gasto(estado["mes"], estado["anio"]),
        ]
        page.update()

    _refrescar()

    fila_titulo = [
        ft.Text("Estadísticas", size=TypographyTokens.PAGE_TITLE_SIZE, weight=TypographyTokens.PAGE_TITLE_WEIGHT)
    ]
    if on_volver is not None:
        fila_titulo.insert(
            0,
            ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Volver", on_click=lambda e: on_volver()),
        )

    return ft.Column(
        [
            ft.Row(fila_titulo, alignment=ft.MainAxisAlignment.START),
            ft.Container(height=8),
            contenedor,
        ],
        spacing=8,
        expand=True,
    )
