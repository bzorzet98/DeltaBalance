"""
DeltaBalance — ui/theme/tokens.py

Tokens de tema genéricos y reusables por cualquier pantalla o componente de
ui/ (CLAUDE.md §8). Escala tipográfica única para toda la app — evita que
cada pantalla invente su propio tamaño/peso "a ojo" para el mismo tipo de
texto (título de página, header de tabla, contenido, metadata).

Valores específicos de UNA sola pantalla (anchos de columna en píxeles,
alturas fijas, cantidades por default, límites de paginación, etc.) NO van
acá — siguen viviendo como constantes nombradas al principio de cada
archivo de pantalla/componente, bajo "# --- Configuración de layout ---".
Este archivo es para lo genérico reusado literalmente igual por más de una
pantalla: tipografía (mayoría de los casos), y textos de campo compartidos
(SharedFieldText, más abajo) — mismo motivo que la tipografía: evitar que
cada pantalla redacte a ojo el mismo campo de forma distinta.
"""

import flet as ft


class TypographyTokens:
    # Título de página (ej. "Dashboard", "Cuentas", "Categorías").
    PAGE_TITLE_SIZE = 26
    PAGE_TITLE_WEIGHT = ft.FontWeight.W_600

    # Título de sección/tarjeta dentro de una pantalla (ej. "Registro de
    # transacciones", "Patrimonio total").
    SECTION_TITLE_SIZE = 16
    SECTION_TITLE_WEIGHT = ft.FontWeight.W_600

    # Header de columna de tabla. Bajado un escalón más (era 12) junto con
    # TABLE_CONTENT_SIZE, a pedido explícito — mantiene la misma relación
    # entre header y contenido que había antes del ajuste.
    TABLE_HEADER_SIZE = 11
    TABLE_HEADER_WEIGHT = ft.FontWeight.W_600

    # Contenido de celda de tabla. WEIGHT (W_500) para datos que conviene
    # destacar un poco (ej. montos); WEIGHT_REGULAR (W_400) para el resto.
    # Bajado un escalón más (era 13, después 12) a pedido explícito — más
    # densidad en el Registro de transacciones y en Compras en cuotas
    # (que ahora también usa estos tokens en vez de tamaños sueltos).
    TABLE_CONTENT_SIZE = 11
    TABLE_CONTENT_WEIGHT = ft.FontWeight.W_500
    TABLE_CONTENT_WEIGHT_REGULAR = ft.FontWeight.W_400

    # Metadata / labels chicos (ej. código de moneda, texto auxiliar).
    METADATA_SIZE = 12
    LABEL_SIZE = 11

    # Controles de filtro/herramientas de una pantalla "estilo planilla"
    # (selector de período, dropdowns de filtro Banco/Categoría, campo de
    # Búsqueda, y el TextField interno de CampoFiltrable — ver
    # ui/components/campo_filtrable.py) — todos deben verse del mismo
    # tamaño, chico, sin que ninguno resalte más que otro (pedido
    # explícito). Hoy coincide en valor con TABLE_CONTENT_SIZE, pero es un
    # token aparte a propósito: son conceptos distintos (chrome de
    # herramientas vs. contenido de fila de tabla) que hoy comparten
    # densidad visual, no necesariamente para siempre.
    FILTER_SIZE = 11


class SharedFieldText:
    """
    Textos de hint/placeholder de campos que se repiten IDÉNTICOS en más
    de una pantalla — evita que cada una redacte su propia versión y
    terminen desincronizadas (pasó con el campo Monto de la fila de alta
    entre ui/components/registro_transacciones.py y
    ui/screens/compras_cuotas.py, corregido a esta constante compartida).
    """
    # Convención de signo en un monto con TextField libre (positivo/
    # negativo cambia qué operación se ejecuta) — mismo hint_text en TODAS
    # las filas de alta que usan esta convención, cada una documenta en su
    # propio docstring qué significa + y - en su caso puntual.
    HINT_MONTO_CON_SIGNO = "± monto"
