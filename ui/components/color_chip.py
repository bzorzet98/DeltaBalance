"""
DeltaBalance — ui/components/color_chip.py

Círculo de color de solo lectura (Container circular) para representar
cuentas.color_hex — reusado en la lista de Cuentas (ui/screens/cuentas.py),
en la columna "Banco" del Registro de transacciones del dashboard, y en la
tabla de Compras en cuotas. Es la pieza realmente idéntica entre esas tres
pantallas; el resto de cada tabla difiere demasiado en columnas y lógica de
guardado como para forzar una abstracción común más grande (ver el resumen
de la tarea que agregó color_hex/el Registro para el razonamiento completo).
"""

import flet as ft


def color_chip(color_hex: str | None, size: int = 12) -> ft.Container:
    """Círculo relleno con `color_hex`. Si viene None/vacío, usa el gris default de columna."""
    return ft.Container(
        width=size,
        height=size,
        bgcolor=color_hex or "#5F5E5A",
        border_radius=size / 2,
    )
