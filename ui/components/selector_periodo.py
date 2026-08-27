"""
DeltaBalance — ui/components/selector_periodo.py

Control "‹ Agosto 2026 ›" navegable: reemplaza los selectores separados de
Mes/Año que tenía el Registro de transacciones. Lo reusan tanto
ui/components/registro_transacciones.py (barra de herramientas del
Registro) como ui/screens/estadisticas.py (selector de período propio,
independiente del dashboard) — mismo patrón visual, cada caller mantiene su
propio dict de estado, no hay estado compartido entre pantallas acá.

Retrocede/avanza un mes con rollover de año. No se re-renderiza solo: como
en registro_transacciones.py, on_cambio() dispara la reconstrucción
completa de la pantalla que lo usa, así que basta con calcular el texto una
sola vez al construir.
"""

from typing import Callable, Optional

import flet as ft

from ui.theme.tokens import TypographyTokens

# --- Configuración de layout ---
ANCHO_TEXTO_PERIODO = 170

_MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def build(
    estado: dict,
    on_cambio: Callable[[], None],
    mes_key: str = "mes",
    anio_key: str = "anio",
    text_size: Optional[int] = None,
) -> ft.Control:
    """
    Args:
        estado:    Dict mutable del caller con al menos estado[mes_key] y
                   estado[anio_key] ya seteados (setdefault() antes de
                   llamar acá si hace falta).
        on_cambio: Callback tras avanzar/retroceder de mes — el caller
                   reconstruye su pantalla entera con esto.
        text_size: Tamaño de fuente del texto "Mes Año". None (default)
                   preserva el tamaño histórico (TypographyTokens.
                   SECTION_TITLE_SIZE, look de título) — usado por
                   ui/screens/estadisticas.py, que no pidió cambiarlo.
                   Los callers que lo usan como parte de una barra de
                   filtros chica (ver ui/components/barra_filtros.py) le
                   pasan TypographyTokens.FILTER_SIZE explícitamente acá.
    """

    def _avanzar(delta: int, e: ft.ControlEvent) -> None:
        mes = estado[mes_key] + delta
        anio = estado[anio_key]
        if mes < 1:
            mes = 12
            anio -= 1
        elif mes > 12:
            mes = 1
            anio += 1
        estado[mes_key] = mes
        estado[anio_key] = anio
        on_cambio()

    return ft.Row(
        [
            ft.IconButton(
                icon=ft.Icons.CHEVRON_LEFT,
                tooltip="Mes anterior",
                on_click=lambda e: _avanzar(-1, e),
            ),
            ft.Text(
                f"{_MESES[estado[mes_key] - 1]} {estado[anio_key]}",
                size=text_size if text_size is not None else TypographyTokens.SECTION_TITLE_SIZE,
                weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                width=ANCHO_TEXTO_PERIODO,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.IconButton(
                icon=ft.Icons.CHEVRON_RIGHT,
                tooltip="Mes siguiente",
                on_click=lambda e: _avanzar(1, e),
            ),
        ],
        spacing=0,
    )
