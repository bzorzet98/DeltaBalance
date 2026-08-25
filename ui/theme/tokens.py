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
Este archivo es solo para lo genérico (tipografía por ahora; color/spacing
si hace falta más adelante).
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

    # Header de columna de tabla.
    TABLE_HEADER_SIZE = 12
    TABLE_HEADER_WEIGHT = ft.FontWeight.W_600

    # Contenido de celda de tabla. WEIGHT (W_500) para datos que conviene
    # destacar un poco (ej. montos); WEIGHT_REGULAR (W_400) para el resto.
    # Bajado un escalón (era 13) a pedido explícito — más densidad en el
    # Registro de transacciones.
    TABLE_CONTENT_SIZE = 12
    TABLE_CONTENT_WEIGHT = ft.FontWeight.W_500
    TABLE_CONTENT_WEIGHT_REGULAR = ft.FontWeight.W_400

    # Metadata / labels chicos (ej. código de moneda, texto auxiliar).
    METADATA_SIZE = 12
    LABEL_SIZE = 11
