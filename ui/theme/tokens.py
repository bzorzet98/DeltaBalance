"""
DeltaBalance — ui/theme/tokens.py

Tokens de tema genéricos y reusables por cualquier pantalla o componente.
Escala tipográfica única + tokens de layout de celda para que las celdas
editables no cambien de tamaño al entrar en modo edición.
"""

import flet as ft


class TypographyTokens:
    # Título de página
    PAGE_TITLE_SIZE = 22
    PAGE_TITLE_WEIGHT = ft.FontWeight.W_600

    # Título de sección/tarjeta
    SECTION_TITLE_SIZE = 14
    SECTION_TITLE_WEIGHT = ft.FontWeight.W_600

    # Header de columna de tabla
    TABLE_HEADER_SIZE = 11
    TABLE_HEADER_WEIGHT = ft.FontWeight.W_600

    # Contenido de celda de tabla
    TABLE_CONTENT_SIZE = 11
    TABLE_CONTENT_WEIGHT = ft.FontWeight.W_500
    TABLE_CONTENT_WEIGHT_REGULAR = ft.FontWeight.W_400

    # Metadata / labels chicos
    METADATA_SIZE = 12
    LABEL_SIZE = 11

    # Controles de filtro/herramientas
    FILTER_SIZE = 11

    # Navegación lateral
    NAV_LABEL_SIZE = 13
    NAV_LABEL_WEIGHT = ft.FontWeight.W_500


class LayoutTokens:
    """
    Tokens de layout de celda editable inline — garantizan que una celda
    no cambie de alto ni de ancho al pasar de modo lectura a modo edición.
    Usados por _celda_texto(), _celda_campo_filtrable(), _celda_dropdown()
    y _celda_monto() en registro_transacciones.py y por cualquier otra
    pantalla con edición inline.
    """
    # Alto fijo de cada fila de tabla — lectura y edición usan el mismo valor.
    ALTURA_FILA_TABLA = 36

    # Padding interno de celda en modo lectura (Container wrapping _texto_celda).
    PADDING_CELDA = 4

    # dense=True en todos los TextField/Dropdown de edición inline —
    # reduce el alto interno del control para que quepa en ALTURA_FILA_TABLA.
    CELDA_DENSE = True

    # Botón ✓ de confirmar en modo edición (ft.IconButton): con el ícono y
    # el padding default de Material (24 + 8*2 = 40px) no entra en
    # ALTURA_FILA_TABLA - 2*PADDING_CELDA = 28px. Con estos valores el
    # contenido del botón mide 16 + 4*2 = 24px.
    ICONO_BOTON_CELDA = 16
    PADDING_BOTON_CELDA = 4


class SharedFieldText:
    HINT_MONTO_CON_SIGNO = "± monto"