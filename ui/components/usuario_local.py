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
"no pasó nada").

Persistencia: archivo JSON local vía ui/utils/prefs.py, clave
"usuario_local" (CLAUDE.md §12). La versión intermedia usaba el servicio
`ft.SharedPreferences`, que en Flet 0.86.5 desktop no persiste entre
sesiones — se reemplazó.

obtener_usuario_local()/guardar_usuario_local() siguen siendo `async def`
aunque la lectura/escritura del archivo es síncrona: así no cambia ningún
caller (compartir_gasto.py, compartir_compra.py y compartir_varios.py los
`await`ean). leer_usuario_local() es la misma lectura, síncrona, para el
build() de ui/screens/gastos_compartidos.py. `on_listo` es
`Callable[[], Awaitable[None]]`: todo caller pasa una función async y este
módulo la `await`ea antes de devolver el control — si un caller le pasara
una función sync, on_listo() devolvería un coroutine sin ejecutar y
quedaría en el mismo bug de "no pasa nada" de arriba.

`abrir_dialogo_sin_hogar()` en sí NO necesita ser async (arma y muestra el
diálogo de forma síncrona, como siempre) — el `await` vive adentro de los
handlers `_crear()`/`_unirse()`, que sí son event handlers async.
"""

from typing import Awaitable, Callable, Optional

import flet as ft

from services.shared_expenses_service import SharedExpensesError, SharedExpensesService
from ui.utils.prefs import escribir_prefs, leer_prefs

# --- Configuración de layout ---
ANCHO_DIALOGO_HOGAR = 360

CLAVE_USUARIO_LOCAL = "usuario_local"


def leer_usuario_local() -> Optional[str]:
    """Versión síncrona (la lectura del archivo lo es): para un build() de pantalla, que no puede `await`."""
    nombre = leer_prefs().get(CLAVE_USUARIO_LOCAL)
    return nombre if isinstance(nombre, str) and nombre else None


async def obtener_usuario_local(page: ft.Page) -> Optional[str]:
    return leer_usuario_local()


def ordenar_hogares(shared_expenses_service: SharedExpensesService, hogares: list[dict]) -> list[dict]:
    """
    Los hogares de list_my_hogares() con el que se usa por defecto PRIMERO:
    el que tiene más miembros (fix: con dos hogares, list_my_hogares()
    ordena por id y el primero podía ser uno donde el usuario está solo).
    Lo usan la pantalla de Gastos compartidos y los diálogos de compartir
    (compartir_gasto.py, compartir_compra.py, compartir_varios.py), así
    todos eligen el mismo.

    TODO: en un empate el pedido elegía el hogar más reciente, pero
    list_my_hogares() no devuelve creada_en (y services/ no se tocó en ese
    fix): por ahora, en empate queda el orden de list_my_hogares() (por id).
    """
    miembros = {h["hogar_id"]: len(shared_expenses_service.list_miembros(h["hogar_id"])) for h in hogares}
    return sorted(hogares, key=lambda h: -miembros[h["hogar_id"]])  # sorted() es estable: el empate conserva el orden


async def guardar_usuario_local(page: ft.Page, nombre: str) -> None:
    prefs = leer_prefs()
    prefs[CLAVE_USUARIO_LOCAL] = nombre
    escribir_prefs(prefs)


def abrir_dialogo_sin_hogar(
    page: ft.Page,
    shared_expenses_service: SharedExpensesService,
    nombre_prellenado: str,
    on_listo: Callable[[], Awaitable[None]],
) -> None:
    """
    Diálogo de onboarding compartido por compartir_gasto.py,
    compartir_compra.py, compartir_varios.py y
    ui/screens/gastos_compartidos.py: "todavía no
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
