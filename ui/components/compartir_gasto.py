"""
DeltaBalance — ui/components/compartir_gasto.py

Ícono + flujo de "Compartir" para una fila YA GUARDADA del Registro de
transacciones (nunca en la fila de carga vacía). build_icon() decide su
propio estado consultando SharedExpensesService.get_shared_expense_by_origin()
para esa transacción puntual:

- Sin gasto compartido asociado: ícono outline (ft.Icons.PEOPLE_OUTLINE),
  tocarlo abre el flujo para cargar uno.
- Con gasto compartido asociado: ícono relleno (ft.Icons.PEOPLE), tocarlo
  muestra el detalle de solo lectura en vez de ofrecer crear uno nuevo
  (evita duplicados — mismo criterio que GastoCompartidoDuplicadoError, que
  el propio service ya usa internamente).

Identidad del usuario local: la app todavía no tiene autenticación real
(hogar_miembros.usuario_local es un string simple — auth con Supabase llega
en una fase futura, ver docstring de services/shared_expenses_service.py).
Mientras tanto, el nombre que te identifica en los hogares compartidos se
guarda en page.client_storage bajo CLAVE_USUARIO_LOCAL, la primera vez que
creás o te unís a un hogar desde acá — no hay pantalla de configuración
separada para esto todavía. SIN CONFIRMAR contra una instalación real de
Flet 0.86.5 (page.client_storage no está en la lista de cambios de
docs/FLET_API_NOTES.md ni se usa en ningún otro lugar de este proyecto
todavía) — si falla al correr la app, avisar para ajustarlo; por eso
get/set están defensivamente envueltos en try/except (si client_storage no
está disponible, se trata como "usuario_local desconocido" en vez de
romper toda la pantalla).

Íconos: PEOPLE / PEOPLE_OUTLINE es el par outline/filled estándar de
Material Icons — no confirmados contra la versión instalada (mismo caveat
que flet_charts.PieChart en docs/FLET_API_NOTES.md, regla 2) — avisar si no
existen corriendo la app.

Reglas de arquitectura: solo SharedExpensesService — nunca repositories/ ni
db/ directo (CLAUDE.md §2/§3).
"""

from typing import Callable, Optional

import flet as ft

from services.shared_expenses_service import SharedExpensesError, SharedExpensesService
from utils.money import amount_display

# --- Configuración de layout ---
CLAVE_USUARIO_LOCAL = "usuario_local"
ANCHO_DIALOGO = 360


def _obtener_usuario_local(page: ft.Page) -> Optional[str]:
    try:
        return page.client_storage.get(CLAVE_USUARIO_LOCAL)
    except Exception:
        return None


def _guardar_usuario_local(page: ft.Page, nombre: str) -> None:
    try:
        page.client_storage.set(CLAVE_USUARIO_LOCAL, nombre)
    except Exception:
        pass


def build_icon(
    page: ft.Page,
    shared_expenses_service: SharedExpensesService,
    transaccion: dict,
    on_cambio: Callable[[], None],
) -> tuple[ft.Control, bool]:
    """
    Returns:
        (control, ya_compartido) — el caller (registro_transacciones.py)
        usa ya_compartido para decidir si el ícono queda siempre visible
        (indicador persistente de "ya compartido") o solo aparece al hover
        de la fila (acción de descubrimiento cuando todavía no se compartió).
    """
    gasto_existente = shared_expenses_service.get_shared_expense_by_origin("transaccion", transaccion["id"])
    ya_compartido = gasto_existente is not None

    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        snack = ft.SnackBar(content=ft.Text(mensaje), bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None)
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    # ------------------------------------------------------------
    # PASO "sin hogar todavía": crear uno nuevo o unirse por código
    # ------------------------------------------------------------
    def _abrir_sin_hogar(nombre_prellenado: str) -> None:
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
            _guardar_usuario_local(page, campo_nombre.value.strip())
            _cerrar_dialogo()
            _abrir_flujo()

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
            _guardar_usuario_local(page, campo_nombre.value.strip())
            _cerrar_dialogo()
            _abrir_flujo()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Compartir gastos"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
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

    # ------------------------------------------------------------
    # PASO "cargar el gasto compartido" — popover compacto
    # ------------------------------------------------------------
    def _abrir_cargar_gasto(usuario_local: str, mis_hogares: list[dict]) -> None:
        hogar_seleccionado = {"id": mis_hogares[0]["hogar_id"]}

        def _sugerencia(hogar_id: int) -> tuple[Optional[float], str]:
            otros = [
                m for m in shared_expenses_service.list_miembros(hogar_id)
                if m["usuario_local"] != usuario_local
            ]
            if not otros:
                return None, "Sin otro miembro en este hogar todavía."
            otro = otros[0]
            sugerido = shared_expenses_service.get_suggested_coefficient(hogar_id, otro["usuario_local"])
            if sugerido is None:
                return None, f"'{otro['usuario_local']}' no tiene un coeficiente default configurado."
            return sugerido, f"Sugerido según el default de '{otro['usuario_local']}'."

        sugerido_inicial, ayuda_inicial = _sugerencia(hogar_seleccionado["id"])
        campo_coeficiente = ft.TextField(
            label="Coeficiente (%) del otro miembro",
            value=str(sugerido_inicial) if sugerido_inicial is not None else "",
            helper_text=ayuda_inicial,
            autofocus=(len(mis_hogares) == 1),
        )

        def _on_select_hogar(e: ft.ControlEvent) -> None:
            hogar_seleccionado["id"] = int(dropdown_hogar.value)
            sugerido, ayuda = _sugerencia(hogar_seleccionado["id"])
            campo_coeficiente.value = str(sugerido) if sugerido is not None else ""
            campo_coeficiente.helper_text = ayuda
            page.update()

        controles: list[ft.Control] = []
        if len(mis_hogares) > 1:
            dropdown_hogar = ft.Dropdown(
                label="Hogar",
                options=[
                    ft.dropdown.Option(key=str(h["hogar_id"]), text=h["nombre"] or f"Hogar #{h['hogar_id']}")
                    for h in mis_hogares
                ],
                value=str(hogar_seleccionado["id"]),
                on_select=_on_select_hogar,
                autofocus=True,
            )
            controles.append(dropdown_hogar)
        controles.append(campo_coeficiente)

        def _confirmar(e: ft.ControlEvent) -> None:
            try:
                coeficiente = float((campo_coeficiente.value or "").strip().replace(",", "."))
            except ValueError:
                _mostrar_mensaje("El coeficiente no es un número válido.", es_error=True)
                return
            try:
                resultado = shared_expenses_service.add_shared_expense(
                    hogar_id=hogar_seleccionado["id"],
                    pagador=usuario_local,
                    origen_tipo="transaccion",
                    origen_id=transaccion["id"],
                    categoria_id=transaccion["categoria_id"],
                    monto_base_minor=transaccion["monto_minor"],
                    coeficiente_deuda=coeficiente,
                    fecha=transaccion["fecha"],
                )
            except (SharedExpensesError, ValueError) as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_mensaje(f"Gasto compartido #{resultado.entity_id} registrado.")
            on_cambio()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Compartir este movimiento"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(controles, tight=True, spacing=10, scroll=ft.ScrollMode.AUTO),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Confirmar"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    # ------------------------------------------------------------
    # PASO "ya compartido" — detalle de solo lectura
    # ------------------------------------------------------------
    def _abrir_detalle(gasto) -> None:
        monto_texto = amount_display(
            gasto["monto_adeudado_minor"], transaccion["decimales"], transaccion["currency_symbol"] or "",
        )
        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Gasto compartido"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [
                        ft.Text(f"Pagador: {gasto['pagador']}"),
                        ft.Text(f"Coeficiente: {gasto['coeficiente_deuda']}%"),
                        ft.Text(f"Monto adeudado: {monto_texto}"),
                        ft.Text(f"Estado: {gasto['estado']}"),
                    ],
                    tight=True,
                    spacing=6,
                ),
            ),
            actions=[ft.TextButton(content=ft.Text("Cerrar"), on_click=_cerrar_dialogo)],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    # ------------------------------------------------------------
    # ENTRY POINT
    # ------------------------------------------------------------
    def _abrir_flujo() -> None:
        usuario_local = _obtener_usuario_local(page)
        mis_hogares = shared_expenses_service.list_my_hogares(usuario_local) if usuario_local else []
        if not mis_hogares:
            _abrir_sin_hogar(nombre_prellenado=usuario_local or "")
            return
        _abrir_cargar_gasto(usuario_local, mis_hogares)

    def _on_click(e: ft.ControlEvent) -> None:
        if ya_compartido:
            _abrir_detalle(gasto_existente)
        else:
            _abrir_flujo()

    icono = ft.IconButton(
        icon=ft.Icons.PEOPLE if ya_compartido else ft.Icons.PEOPLE_OUTLINE,
        icon_color=ft.Colors.PRIMARY if ya_compartido else ft.Colors.OUTLINE,
        tooltip="Gasto compartido (ya cargado)" if ya_compartido else "Compartir con hogar",
        on_click=_on_click,
    )
    return icono, ya_compartido
