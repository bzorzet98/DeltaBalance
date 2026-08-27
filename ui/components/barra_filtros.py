"""
DeltaBalance — ui/components/barra_filtros.py

Barra de herramientas de filtro compartida entre las pantallas "estilo
planilla" (ui/components/registro_transacciones.py y
ui/screens/compras_cuotas.py): período navegable (‹ Mes Año ›, ver
ui/components/selector_periodo.py) → filtro de Banco → filtro de
Categoría → (spacer) → búsqueda por texto, alineada al extremo derecho.

Extraído en la tarea de "filtro global en Compras en cuotas" para no
duplicar entre las dos pantallas la construcción de estos cuatro controles
(antes solo vivía inline dentro de registro_transacciones.py).

Reglas de negocio específicas de cada pantalla (ej. excluir tarjetas de
crédito del filtro de Banco en el Registro, o priorizarlas primero en
Compras en cuotas) NO viven acá — cada caller arma `cuentas_filtro`/
`categorias_filtro` ya filtradas/ordenadas como corresponda a su propio
caso de uso antes de pasarlas. Este componente es de UI genérica, sin
reglas de dominio propias (CLAUDE.md §2).

`estado` (dict mutable del caller) necesita las claves mes/anio/
filtro_banco/filtro_categoria/busqueda ya con setdefault() aplicado —
mismo contrato que selector_periodo.build(). on_cambio se llama tras
CUALQUIER cambio de esta barra (período, filtro banco, filtro categoría, o
Enter en la búsqueda) — el caller decide qué reconstruir con eso (puede
ser toda la pantalla, o solo la tabla, según cómo maneje su propio
refresco).

Sin wrap=True a propósito: un spacer con expand=True dentro de un Row con
wrap=True rompe en Flutter (ver docs/FLET_API_NOTES.md, sección "ft.Row/
ft.Column con wrap=True no soportan hijos expand=True").
"""

from typing import Callable, Optional

import flet as ft

from ui.components import selector_periodo

# --- Configuración de layout ---
# Anchos default — cada caller puede pasar los suyos propios si ya tiene
# constantes nombradas equivalentes (ej. ANCHO_TOOLBAR_FILTRO_BANCO en
# registro_transacciones.py) para no duplicar el número en dos archivos.
ANCHO_FILTRO_BANCO_DEFAULT = 150
ANCHO_FILTRO_CATEGORIA_DEFAULT = 160
ANCHO_BUSQUEDA_DEFAULT = 200
ESPACIADO_DEFAULT = 8


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
    """
    Args:
        estado:             Dict mutable del caller — mes/anio/
                             filtro_banco/filtro_categoria/busqueda ya con
                             setdefault() aplicado.
        on_cambio:           Llamado tras período/filtro banco/filtro
                             categoría/búsqueda confirmada (Enter).
        cuentas_filtro:      [{"id":.., "nombre":..}, ...] ya filtradas/
                             ordenadas por el caller (ver docstring del
                             módulo — acá no se excluye ni prioriza nada).
        categorias_filtro:   [{"id":.., "subcategoria":..}, ...].
        text_size:           Tamaño de fuente aplicado a los 4 controles
                             (período, filtro banco, filtro categoría,
                             búsqueda) — pensado para
                             TypographyTokens.FILTER_SIZE, para que se
                             vean todos del mismo tamaño (pedido
                             explícito). None = tamaños default de Flet.
    """

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
        label="Banco",
        width=ancho_filtro_banco,
        dense=True,
        text_size=text_size,
        options=[ft.dropdown.Option(key="", text="Todos")] + [
            ft.dropdown.Option(key=str(c["id"]), text=c["nombre"]) for c in cuentas_filtro
        ],
        value=str(estado["filtro_banco"]) if estado["filtro_banco"] is not None else "",
        on_select=_on_select_filtro_banco,
    )
    dropdown_filtro_categoria = ft.Dropdown(
        label="Categoría",
        width=ancho_filtro_categoria,
        dense=True,
        text_size=text_size,
        options=[ft.dropdown.Option(key="", text="Todas")] + [
            ft.dropdown.Option(key=str(c["id"]), text=c["subcategoria"]) for c in categorias_filtro
        ],
        value=str(estado["filtro_categoria"]) if estado["filtro_categoria"] is not None else "",
        on_select=_on_select_filtro_categoria,
    )
    # on_submit (Enter) confirma la búsqueda — nada se filtra "en vivo"
    # tecla por tecla, mismo criterio ya usado antes de extraer este
    # componente.
    campo_busqueda = ft.TextField(
        width=ancho_busqueda,
        label="Buscar",
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
