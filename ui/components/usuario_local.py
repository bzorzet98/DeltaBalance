"""
DeltaBalance — ui/components/usuario_local.py

Identidad del usuario local en hogares compartidos — helper mínimo,
extraído de ui/components/compartir_gasto.py (donde vivía como funciones/
diálogo privados) para que ui/components/compartir_compra.py lo reuse sin
duplicarlo: ambos necesitan la misma lectura/escritura de
page.client_storage y el mismo diálogo de onboarding ("todavía no
pertenecés a ningún hogar — crear uno o unirte por código"). La app
todavía no tiene autenticación real (ver docstring de
services/shared_expenses_service.py) — este nombre es un string simple
guardado del lado del cliente, no un id de usuario real.

SIN CONFIRMAR contra una instalación real de Flet 0.86.5
(page.client_storage no está en la lista de cambios de
docs/FLET_API_NOTES.md) — por eso get/set están envueltos en try/except:
si client_storage no está disponible, se trata como "usuario_local
desconocido" en vez de romper la pantalla que lo use.
"""

from typing import Callable, Optional

import flet as ft

from services.shared_expenses_service import SharedExpensesError, SharedExpensesService

# --- Configuración de layout ---
ANCHO_DIALOGO_HOGAR = 360

CLAVE_USUARIO_LOCAL = "usuario_local"


def obtener_usuario_local(page: ft.Page) -> Optional[str]:
    try:
        return page.client_storage.get(CLAVE_USUARIO_LOCAL)
    except Exception:
        return None


def guardar_usuario_local(page: ft.Page, nombre: str) -> None:
    try:
        page.client_storage.set(CLAVE_USUARIO_LOCAL, nombre)
    except Exception:
        pass


def abrir_dialogo_sin_hogar(
    page: ft.Page,
    shared_expenses_service: SharedExpensesService,
    nombre_prellenado: str,
    on_listo: Callable[[], None],
) -> None:
    """
    Diálogo de onboarding compartido por compartir_gasto.py y
    compartir_compra.py: "todavía no pertenecés a ningún hogar
    compartido" — crear uno nuevo o unirse a uno existente por código.

    Al terminar cualquiera de los dos caminos con éxito, guarda
    usuario_local en client_storage, cierra el diálogo y llama on_listo()
    — el caller reabre su propio flujo de "compartir" (transacción o
    compra), ya con al menos un hogar disponible.
    """
    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        snack = ft.SnackBar(content=ft.Text(mensaje), bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None)
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    campo_nombre = ft.TextField(
        label="Tu nombre en hogares compartidos", value=nombre_prellenado, autofocus=True,
    )
    campo_hogar_nombre = ft.TextField(label="Nombre del hogar (opcional)")
    campo_codigo = ft.TextField(label="Código de invitación")

    def _crear(e: ft.ControlEvent) -> None:
        if not campo_nombre.value or not campo_nombre.value.strip():
            _mostrar_mensaje("Ingresá tu nombre.", es_error=True)
            return
        try:
            shared_expenses_service.create_hogar(
                nombre_creador_local=campo_nombre.value.strip(),
                nombre_hogar=(campo_hogar_nombre.value or "").strip() or None,
            )
        except (SharedExpensesError, ValueError) as err:
            _mostrar_mensaje(str(err), es_error=True)
            return
        guardar_usuario_local(page, campo_nombre.value.strip())
        _cerrar_dialogo()
        on_listo()

    def _unirse(e: ft.ControlEvent) -> None:
        if not campo_nombre.value or not campo_nombre.value.strip():
            _mostrar_mensaje("Ingresá tu nombre.", es_error=True)
            return
        if not campo_codigo.value or not campo_codigo.value.strip():
            _mostrar_mensaje("Ingresá el código de invitación.", es_error=True)
            return
        try:
            shared_expenses_service.join_hogar(
                codigo_invitacion=campo_codigo.value.strip(),
                nombre_local=campo_nombre.value.strip(),
            )
        except (SharedExpensesError, ValueError) as err:
            _mostrar_mensaje(str(err), es_error=True)
            return
        guardar_usuario_local(page, campo_nombre.value.strip())
        _cerrar_dialogo()
        on_listo()

    dialogo = ft.AlertDialog(
        modal=True,
        title=ft.Text("Compartir gastos"),
        content=ft.Container(
            width=ANCHO_DIALOGO_HOGAR,
            content=ft.Column(
                [
                    ft.Text("Todavía no pertenecés a ningún hogar compartido."),
                    campo_nombre,
                    ft.Divider(),
                    ft.Text("Crear un hogar nuevo", weight=ft.FontWeight.BOLD),
                    campo_hogar_nombre,
                    ft.ElevatedButton(content=ft.Text("Crear hogar"), on_click=_crear),
                    ft.Divider(),
                    ft.Text("Unirme a uno existente", weight=ft.FontWeight.BOLD),
                    campo_codigo,
                    ft.ElevatedButton(content=ft.Text("Unirme"), on_click=_unirse),
                ],
                tight=True,
                spacing=10,
                scroll=ft.ScrollMode.AUTO,
            ),
        ),
        actions=[ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo)],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dialogo)
