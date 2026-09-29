"""
DeltaBalance — ui/theme/tabla_tokens.py

Paleta de las pantallas estilo planilla (tema oscuro): el Registro de
transacciones y Compras en cuotas importan estos valores de acá — nunca
los redefinen —, así las dos pantallas se ven idénticas. Los tamaños de
fuente están en ui/theme/tokens.py (TypographyTokens.REGISTRO_FONT_*) y
el alto de fila en LayoutTokens.ALTURA_FILA_TABLA; el layout propio de la
tabla vive en ui/components/tabla_planilla.py.
"""

import flet as ft

# Fondos
BG_APP = "#1a1a1a"
BG_SUPERFICIE = "#242424"
BG_ENCABEZADO = "#2a2a2a"
BG_FILA_ALTA = "#2d2d2d"
BG_FILA_PAR = "#242424"
BG_FILA_IMPAR = "#262626"
BG_FILA_HOVER = "#2f2f2f"
BG_FILA_SEL = "#1a2a3a"
BG_BARRA_FLOT = "#1e1e1e"
BG_OVERLAY = "#2c2c2c"
BG_MENU_CTX = "#2c2c2c"
BG_ITEM_HOVER = "#383838"

# Bordes
BORDER_DEFAULT = "#3a3a3a"
BORDER_HEADER = "#333333"
BORDER_BARRA = "#3a3a3a"
BORDER_OVERLAY = "#404040"

# Texto
TEXT_PRIMARY = "#e8e8e8"
TEXT_SECONDARY = "#9a9a9a"
TEXT_MUTED = "#666666"
TEXT_POSITIVO = "#4caf50"
TEXT_NEGATIVO = "#f44336"
TEXT_ACCENT = "#4a9eff"
TEXT_SOBRE_BOTON = "#ffffff"

# Botones de la barra flotante
BTN_ELIMINAR = "#e53935"
BTN_COMPARTIR = "#1976d2"

# Pesos de fuente
PESO_HEADER = ft.FontWeight.W_500
PESO_CELDA = ft.FontWeight.W_400
PESO_MONTO = ft.FontWeight.W_600

# Dot de color por cuenta — clave: nombre de la cuenta sin tildes y en
# mayúsculas (ver color_cuenta() en ui/components/tabla_planilla.py).
DOTS_CUENTAS = {
    # Bancos argentinos — colores de marca
    "NACION":               "#005F86",  # azul/teal BNA
    "CREDICOOP":            "#5E584C",  # tono Credicoop
    "BBVA":                 "#004481",  # azul marino oficial BBVA
    "GALICIA":              "#FF5000",  # naranja oficial Banco Galicia
    "SANTANDER":            "#EC0000",  # rojo oficial Santander
    "NARANJA X":            "#FF6200",  # naranja oficial Naranja X
    "BRUBANK":              "#5B2D8E",  # violeta oficial Brubank
    "MP":                   "#009EE3",  # azul/celeste oficial Mercado Pago
    "MERCADO PAGO CREDITO": "#009EE3",  # mismo azul/celeste Mercado Pago
    # Exchanges y crypto — colores de marca
    "COCOS":                "#0062DE",  # azul Cocos Capital
    "BULL MARKET":          "#1E27F3",  # azul Bull Market Brokers
    "BUENBIT":              "#787B7C",  # gris/slate Buenbit
    "BINANCE":              "#F3BA2F",  # amarillo dorado Binance
    "NEXO":                 "#787B7C",  # gris/slate (mismo que Buenbit)
    "FIWIND":               "#00B4D8",  # celeste Fiwind
    # Otros
    "BILLETERA ER":         "#549514",  # verde oficial Billetera Entre Ríos
    "MACRO":                "#002855",  # azul marino Banco Macro
    "JOY":                  "#00C853",  # verde Joy
    "CAJA EFECTIVO":        "#9E9E9E",  # gris neutro para efectivo
    "DEFAULT":              "#757575",  # gris para cualquier cuenta sin color asignado
}
# DEFAULT de la columna cuentas.color_hex (db/schema_migrations.py): una
# cuenta con este color nunca eligió uno propio → se usa DOTS_CUENTAS.
COLOR_CUENTA_SIN_ELEGIR = "#5F5E5A"
