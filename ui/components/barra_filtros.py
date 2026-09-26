"""
DeltaBalance — ui/components/barra_filtros.py

Barra de herramientas de filtro compartida entre las pantallas "estilo
planilla" (ui/components/registro_transacciones.py y
ui/screens/compras_cuotas.py): período navegable → filtro de Banco →
filtro de Categoría → (spacer) → búsqueda por texto.
"""

from typing import Callable, Optional

import flet as ft

from ui.components import selector_periodo

# --- Configuración de layout ---
ANCHO_FILTRO_BANCO_DEFAULT     = 150
ANCHO_FILTRO_CATEGORIA_DEFAULT = 160
ANCHO_BUSQUEDA_DEFAULT         = 200
ESPACIADO_DEFAULT              = 8


def build(
    estado: dict,
    on_cambio: Callable[[], None],
    cuentas_filtro: list[dict],
    categorias_filtro: list[dict],
    ancho_filtro_banco: int = ANCHO_FILTRO_BANCO_DEFAULT,
    ancho_filtro_categoria: int = ANCHO_FILTRO_CATEGORIA_DEFAULT,
    ancho_busqueda: int = ANCHO_BUSQUEDA_DEFAULT,
    espaciado: int = ESPACIADO_DEFAULT,
    text_size: Optional[int] = None,
    placeholder_busqueda: str = "Concepto... (Enter)",
) -> ft.Control:

    def _on_select_filtro_banco(e: ft.ControlEvent) -> None:
        valor = dropdown_filtro_banco.value
        estado["filtro_banco"] = int(valor) if valor else None
        on_cambio()

    def _on_select_filtro_categoria(e: ft.ControlEvent) -> None:
        valor = dropdown_filtro_categoria.value
        estado["filtro_categoria"] = int(valor) if valor else None
        on_cambio()

    def _on_submit_busqueda(e: ft.ControlEvent) -> None:
        estado["busqueda"] = campo_busqueda.value or ""
        on_cambio()

    control_periodo = selector_periodo.build(estado, on_cambio, text_size=text_size)

    dropdown_filtro_banco = ft.Dropdown(
        label="BANCO",
        width=ancho_filtro_banco,
        dense=True,
        text_size=text_size,
        options=[ft.dropdown.Option(key="", text="TODOS")] + [
            ft.dropdown.Option(key=str(c["id"]), text=c["nombre"].upper()) for c in cuentas_filtro
        ],
        value=str(estado["filtro_banco"]) if estado["filtro_banco"] is not None else "",
        on_select=_on_select_filtro_banco,
    )
    dropdown_filtro_categoria = ft.Dropdown(
        label="CATEGORÍA",
        width=ancho_filtro_categoria,
        dense=True,
        text_size=text_size,
        options=[ft.dropdown.Option(key="", text="TODAS")] + [
            ft.dropdown.Option(key=str(c["id"]), text=c["subcategoria"].upper()) for c in categorias_filtro
        ],
        value=str(estado["filtro_categoria"]) if estado["filtro_categoria"] is not None else "",
        on_select=_on_select_filtro_categoria,
    )
    campo_busqueda = ft.TextField(
        width=ancho_busqueda,
        label="BUSCAR",
        hint_text=placeholder_busqueda,
        dense=True,
        text_size=text_size,
        prefix_icon=ft.Icons.SEARCH,
        value=estado["busqueda"],
        on_submit=_on_submit_busqueda,
    )

    return ft.Row(
        [
            control_periodo,
            dropdown_filtro_banco,
            dropdown_filtro_categoria,
            ft.Container(expand=True),
            campo_busqueda,
        ],
        spacing=espaciado,
    )