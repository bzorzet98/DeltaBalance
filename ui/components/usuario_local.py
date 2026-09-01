"""
DeltaBalance — ui/components/usuario_local.py

Identidad del usuario local en hogares compartidos — helper mínimo,
extraído de ui/components/compartir_gasto.py (donde vivía como funciones/
diálogo privados) para que ui/components/compartir_compra.py lo reuse sin
duplicarlo: ambos necesitan la misma lectura/escritura de persistencia
local y el mismo diálogo de onboarding ("todavía no pertenecés a ningún
hogar — crear uno o unirte por código"). La app todavía no tiene
autenticación real (ver docstring de services/shared_expenses_service.py)
— este nombre es un string simple guardado del lado del cliente, no un id
de usuario real.

BUG REAL CORREGIDO ACÁ (investigado a fondo por el usuario reportando que
"Crear hogar" no hacía nada visible, ni desde acá ni desde
ui/screens/deudas_y_compartidos.py): la versión anterior de este módulo
usaba `page.client_storage.get()/.set()`, una API que **NO EXISTE en Flet
0.86.5** — confirmado por lectura directa del código fuente real
instalado (grep de "client_storage" en todo el paquete `flet` instalado:
CERO coincidencias, en ningún archivo). Cada acceso a `page.client_storage`
lanzaba `AttributeError`, atrapado en silencio por los `except Exception`
de abajo — así que `guardar_usuario_local()` NUNCA guardaba nada de verdad
(en ningún lugar de la app, incluido ui/components/compartir_gasto.py, que
"parecía" funcionar solo porque su fallback ante "no tengo hogar todavía"
es reabrir el mismo diálogo — un cambio visible que se puede confundir con
progreso — mientras que ui/screens/deudas_y_compartidos.py simplemente
volvía a mostrar la misma pantalla "sin hogar" de antes, indistinguible de
"no pasó nada"). La API real de persistencia cliente en este ciclo de Flet
es el servicio `ft.SharedPreferences` (confirmado por lectura de
flet/controls/services/shared_preferences.py del paquete instalado):
`get`/`set`/`contains_key`/`remove`/`get_keys`/`clear`, TODOS `async def` —
se agrega una única instancia a `page.services` (mismo patrón que
FilePicker, ver docs/FLET_API_NOTES.md "FilePicker ahora es un servicio,
se agrega a page.services") y se reusa esa misma instancia en cada
llamada, en vez de instanciar una nueva cada vez (existe un
`page.shared_preferences` en el paquete instalado, pero está DEPRECADO —
"Use SharedPreferences() instead" — y devuelve una instancia nueva sin
registrar en cada acceso, así que no se usa acá).

Como get/set son ahora `async def`, obtener_usuario_local()/
guardar_usuario_local()/abrir_dialogo_sin_hogar() (los botones "Crear
hogar"/"Unirme" de adentro) también lo son — Flet soporta handlers de
evento async de forma nativa (mismo patrón ya confirmado y en uso en
ui/components/campo_filtrable.py, `_on_blur`). `on_listo` cambia de
`Callable[[], None]` a `Callable[[], Awaitable[None]]`: todo caller
(compartir_gasto.py, compartir_compra.py, ui/screens/deudas_y_compartidos.py)
tiene que pasar ahora una función async y este módulo la `await`ea antes
de devolver el control — si un caller le pasara una función sync, on_listo()
devolvería un coroutine sin ejecutar y quedaría exactamente en el mismo
bug de "no pasa nada" que se está corrigiendo acá.

`abrir_dialogo_sin_hogar()` en sí NO necesita ser async (arma y muestra el
diálogo de forma síncrona, como siempre) — el `await` vive adentro de los
handlers `_crear()`/`_unirse()`, que sí son event handlers async.

Registro del servicio en `page.services` + `page.update()` inmediato tras
agregarlo (antes de cualquier `get()`/`set()`): asegura que el cliente ya
conoce el control del servicio antes de invocarle un método RPC — mismo
principio ya usado en este proyecto para SnackBar (`page.overlay.append(...);
page.update()` antes de que quede "activo"). Pendiente de confirmar
corriendo la app: el mecanismo exacto de registro de un Service standalone
no está en la lista de cambios de docs/FLET_API_NOTES.md — si
`ft.SharedPreferences` sigue sin persistir corriendo la app de verdad, es
el siguiente punto a revisar.
"""

from typing import Awaitable, Callable, Optional

import flet as ft

from services.shared_expenses_service import SharedExpensesError, SharedExpensesService

# --- Configuración de layout ---
ANCHO_DIALOGO_HOGAR = 360

CLAVE_USUARIO_LOCAL = "usuario_local"


def _shared_preferences(page: ft.Page) -> ft.SharedPreferences:
    """
    Reusa la única instancia de ft.SharedPreferences ya registrada en
    page.services (agregada por una llamada anterior desde esta misma
    página) en vez de crear una nueva en cada get/set — ver docstring del
    módulo para el motivo.
    """
    for servicio in page.services:
        if isinstance(servicio, ft.SharedPreferences):
            return servicio
    prefs = ft.SharedPreferences()
    page.services.append(prefs)
    page.update()
    return prefs


async def obtener_usuario_local(page: ft.Page) -> Optional[str]:
    try:
        return await _shared_preferences(page).get(CLAVE_USUARIO_LOCAL)
    except Exception:
        return None


async def guardar_usuario_local(page: ft.Page, nombre: str) -> None:
    try:
        await _shared_preferences(page).set(CLAVE_USUARIO_LOCAL, nombre)
    except Exception:
        pass


def abrir_dialogo_sin_hogar(
    page: ft.Page,
    shared_expenses_service: SharedExpensesService,
    nombre_prellenado: str,
    on_listo: Callable[[], Awaitable[None]],
) -> None:
    """
    Diálogo de onboarding compartido por compartir_gasto.py,
    compartir_compra.py y ui/screens/deudas_y_compartidos.py: "todavía no
    pertenecés a ningún hogar compartido" — crear uno nuevo o unirse a uno
    existente por código.

    Al terminar cualquiera de los dos caminos con éxito, guarda
    usuario_local (ahora sí, de verdad — ver docstring del módulo), cierra
    el diálogo y hace `await on_listo()` — el caller reabre/reconstruye su
    propio flujo, ya con al menos un hogar disponible.
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

    async def _crear(e: ft.ControlEvent) -> None:
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
        await guardar_usuario_local(page, campo_nombre.value.strip())
        _cerrar_dialogo()
        await on_listo()

    async def _unirse(e: ft.ControlEvent) -> None:
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
        await guardar_usuario_local(page, campo_nombre.value.strip())
        _cerrar_dialogo()
        await on_listo()

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
