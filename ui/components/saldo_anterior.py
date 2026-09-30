"""
DeltaBalance — ui/components/saldo_anterior.py

Piezas compartidas de la fila "SALDO ANTERIOR" (snapshots de cierre de mes,
services/snapshots_service.py) del Registro, Deudas y Gastos compartidos:
sus textos y el botón ↻ "RECALCULAR SALDOS HISTÓRICOS" de la barra
superior. La fila en sí es una FilaPie de ui/components/tabla_planilla.py:
cada pantalla la arma con sus columnas.

boton_recalcular(): corre SnapshotsService.recalcular_todo() (los tres
tipos de snapshot a la vez), avisa el resultado y llama a al_terminar()
para que la pantalla vuelva a pedir sus filas. El recálculo es síncrono (la
conexión SQLite de la app no se puede usar desde otro hilo): el handler es
async solo para que el SnackBar "RECALCULANDO…" llegue a la pantalla antes
de bloquear. Mientras corre, el botón queda deshabilitado (evita un doble
recálculo).

Reglas de arquitectura: solo SnapshotsService — nunca repositories/ ni db/
directo (CLAUDE.md §2/§3).
"""

import asyncio
import sqlite3
from typing import Callable

import flet as ft

from services.snapshots_service import SnapshotsError, SnapshotsService
from ui.components.tabla_planilla import mostrar_mensaje
from ui.theme.tabla_tokens import TEXT_SECONDARY

# --- Configuración de layout ---
# Pausa antes de bloquear con el recálculo: deja salir el SnackBar "RECALCULANDO…".
ESPERA_ANTES_DE_RECALCULAR_S = 0.05

TEXTO_SALDO_ANTERIOR = "SALDO ANTERIOR"
TOOLTIP_SALDO_ANTERIOR = "ACUMULADO DE MESES ANTERIORES — CLICK ↻ PARA RECALCULAR"


def boton_recalcular(
    page: ft.Page, snapshots_service: SnapshotsService, al_terminar: Callable[[], None],
) -> ft.IconButton:
    async def _on_recalcular(e=None) -> None:
        boton.disabled = True
        mostrar_mensaje(page, "RECALCULANDO SALDOS HISTÓRICOS…")  # page.update(): también deshabilita el botón
        await asyncio.sleep(ESPERA_ANTES_DE_RECALCULAR_S)
        try:
            resultado = snapshots_service.recalcular_todo()
        except (SnapshotsError, sqlite3.Error) as err:
            # recalcular_todo() es una sola transacción: si falló, ya hizo rollback.
            boton.disabled = False
            mostrar_mensaje(page, f"NO SE PUDO RECALCULAR: {err}", es_error=True)
            return
        boton.disabled = False
        mostrar_mensaje(
            page,
            f"✅ {resultado.meses_calculados} MESES RECALCULADOS EN {resultado.tiempo_segundos:.2f} SEGUNDOS",
        )
        al_terminar()

    boton = ft.IconButton(
        icon=ft.Icons.REFRESH, icon_color=TEXT_SECONDARY, tooltip="RECALCULAR SALDOS HISTÓRICOS",
        on_click=_on_recalcular,
    )
    return boton
