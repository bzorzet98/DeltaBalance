"""
DeltaBalance — ui/screens/login.py

Pantalla de inicio de sesión (Supabase Auth, sync/auth.py): una tarjeta
centrada con el título, email, contraseña, "INICIAR SESIÓN" y "TRABAJAR SIN
CONEXIÓN". Mismo tema oscuro que las planillas (ui/theme/tabla_tokens.py).

La arma ui/app.py solo cuando no hay una sesión guardada. Flujo:
- INICIAR SESIÓN (o Enter en la contraseña): AuthService.login() en otro
  hilo (hace red: asyncio.to_thread, la UI no se congela). Si sale bien, el
  estado pasa a "SINCRONIZANDO…" y se llama a on_sesion_iniciada() — ui/app.py
  corre ahí la primera sincronización y después abre el Registro. Si falla,
  el mensaje de AuthResult queda debajo del botón.
- TRABAJAR SIN CONEXIÓN: on_sin_conexion() abre la app sin sincronizar.

Reglas de arquitectura: solo AuthService (sync/) — nada de services/ ni
repositories/.
"""

import asyncio
from typing import Awaitable, Callable

import flet as ft

from sync.auth import AuthService
from ui.theme.tabla_tokens import (
    BG_APP,
    BG_SUPERFICIE,
    BORDER_DEFAULT,
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_NEGATIVO,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.theme.tokens import TypographyTokens

# --- Configuración de layout ---
ANCHO_TARJETA = 380
PADDING_TARJETA = 32
RADIO_TARJETA = 12
ANCHO_BORDE_TARJETA = 1
ESPACIADO_TARJETA = 16
ICONO_LOGO = 56
TAMANIO_TITULO = 28
TAMANIO_PUNTO_OFFLINE = 8


def build(
    page: ft.Page,
    auth: AuthService,
    on_sesion_iniciada: Callable[[], Awaitable[None]],
    on_sin_conexion: Callable[[], None],
) -> ft.Control:
    campo_email = ft.TextField(label="EMAIL", autofocus=True, keyboard_type=ft.KeyboardType.EMAIL)
    campo_password = ft.TextField(label="CONTRASEÑA", password=True, can_reveal_password=True)
    estado = ft.Text("", size=TypographyTokens.LABEL_SIZE, color=TEXT_SECONDARY, text_align=ft.TextAlign.CENTER)
    boton_ingresar = ft.ElevatedButton(content=ft.Text("INICIAR SESIÓN"), expand=True)

    def _mostrar_estado(texto: str, es_error: bool = False) -> None:
        estado.value = texto
        estado.color = TEXT_NEGATIVO if es_error else TEXT_SECONDARY

    async def _ingresar(e=None) -> None:
        if boton_ingresar.disabled:
            return  # doble Enter / doble click
        email = (campo_email.value or "").strip()
        password = campo_password.value or ""
        if not email or not password:
            _mostrar_estado("COMPLETÁ EL EMAIL Y LA CONTRASEÑA.", es_error=True)
            page.update()
            return
        boton_ingresar.disabled = True
        _mostrar_estado("INICIANDO SESIÓN…")
        page.update()
        resultado = await asyncio.to_thread(auth.login, email, password)
        if not resultado.success:
            boton_ingresar.disabled = False
            _mostrar_estado(resultado.mensaje, es_error=True)
            page.update()
            return
        _mostrar_estado("SINCRONIZANDO…")
        page.update()
        await on_sesion_iniciada()

    def _sin_conexion(e=None) -> None:
        on_sin_conexion()

    boton_ingresar.on_click = _ingresar
    campo_email.on_submit = lambda e: page.run_task(campo_password.focus)
    campo_password.on_submit = _ingresar

    tarjeta = ft.Container(
        width=ANCHO_TARJETA,
        padding=PADDING_TARJETA,
        bgcolor=BG_SUPERFICIE,
        border=ft.Border.all(ANCHO_BORDE_TARJETA, BORDER_DEFAULT),
        border_radius=RADIO_TARJETA,
        content=ft.Column(
            [
                ft.Text(
                    "DeltaBalance", size=TAMANIO_TITULO, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Icon(ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED, size=ICONO_LOGO, color=TEXT_ACCENT),
                campo_email,
                campo_password,
                ft.Row([boton_ingresar]),
                estado,
                ft.TextButton(
                    content=ft.Row(
                        [
                            ft.Container(
                                width=TAMANIO_PUNTO_OFFLINE, height=TAMANIO_PUNTO_OFFLINE,
                                border_radius=TAMANIO_PUNTO_OFFLINE / 2, bgcolor=TEXT_MUTED,
                            ),
                            ft.Text("TRABAJAR SIN CONEXIÓN", color=TEXT_SECONDARY),
                        ],
                        tight=True,
                        spacing=ESPACIADO_TARJETA / 2,
                    ),
                    on_click=_sin_conexion,
                ),
            ],
            tight=True,
            spacing=ESPACIADO_TARJETA,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )
    return ft.Container(expand=True, bgcolor=BG_APP, alignment=ft.Alignment.CENTER, content=tarjeta)
