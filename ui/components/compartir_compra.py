"""
DeltaBalance — ui/components/compartir_compra.py

Ícono + flujo de "Compartir" para una fila YA GUARDADA de Compras en
cuotas (ui/screens/compras_cuotas.py) — mismo patrón visual/de
interacción que ui/components/compartir_gasto.py para el Registro de
transacciones (ícono que aparece al hover, popover compacto con hogar +
coeficiente), pero apuntando a
SharedExpensesService.add_shared_purchase() en vez de
add_shared_expense(): acá el origen es una compra_cuotas/cuotas_credito
completa, no una transacción suelta — ver docstring de
add_shared_purchase() para el detalle de por qué esa orquestación no
existía antes de esta ronda.

Detección de "ya compartido": una compra puede compartirse de dos formas
según su modo_deuda — 'total_unico' (un solo gasto_compartido,
origen_tipo='compra_cuotas') o 'prorrateado' (uno por cada
cuotas_credito, origen_tipo='cuota_credito'). _estado_compartido()
resuelve cuál corresponde y devuelve (algo_compartido, todo_compartido,
compartidas, total):
- El ÍCONO usa "algo_compartido" (relleno si hay AL MENOS una unidad
  compartida, aunque sea parcial) — mismo criterio binario simple que
  compartir_gasto.py para decidir si queda siempre visible o solo aparece
  al hover.
- El CLICK, a diferencia de compartir_gasto.py, sí distingue parcial de
  total: si "todo_compartido" abre el detalle de solo lectura (no hay
  nada más que compartir); si es parcial o nada, abre el flujo de carga
  — add_shared_purchase() ya sabe saltear las cuotas que ya tienen un
  gasto compartido asociado, así que "compartir de nuevo" una compra
  parcialmente compartida simplemente completa lo que falta, en vez de
  duplicar o bloquear.

No se puede elegir modo_deuda desde acá (pedido explícito): usa el que ya
tiene la compra guardada (compras_cuotas.modo_deuda, default
'prorrateado' si nunca se seteó explícito al crearla).

Identidad del usuario local y diálogo de "todavía no tenés hogar": ver
ui/components/usuario_local.py, compartido con compartir_gasto.py.

Reglas de arquitectura: SharedExpensesService (dueño de
gastos_compartidos) y FeesService (solo LECTURA, para leer las cuotas de
la compra al chequear estado y armar el detalle — nunca escribe
compras_cuotas/cuotas_credito desde acá) — nunca repositories/ ni db/
directo (CLAUDE.md §2/§3).
"""

from typing import Callable, Optional

import flet as ft

from services.fees_service import FeesService
from services.shared_expenses_service import SharedExpensesError, SharedExpensesService
from ui.components.usuario_local import abrir_dialogo_sin_hogar, obtener_usuario_local

# --- Configuración de layout ---
ANCHO_DIALOGO = 360


def _estado_compartido(
    shared_expenses_service: SharedExpensesService,
    fees_service: FeesService,
    compra: dict,
) -> tuple[bool, bool, int, int]:
    """
    Returns:
        (algo_compartido, todo_compartido, compartidas, total).
        `total` es 1 para modo_deuda='total_unico' (una sola unidad
        compartible), o la cantidad de cuotas_credito de la compra para
        'prorrateado'. Una compra 'prorrateado' sin ninguna cuota
        generada devuelve (False, False, 0, 0) — no debería pasar en la
        práctica (create_purchase() siempre genera al menos 1), pero no
        se asume.
    """
    if compra["modo_deuda"] == "total_unico":
        existe = shared_expenses_service.get_shared_expense_by_origin("compra_cuotas", compra["id"]) is not None
        return existe, existe, (1 if existe else 0), 1

    cuotas = fees_service.get_fees_for_purchase(compra["id"])
    if not cuotas:
        return False, False, 0, 0
    compartidas = sum(
        1 for c in cuotas
        if shared_expenses_service.get_shared_expense_by_origin("cuota_credito", c["id"]) is not None
    )
    return compartidas > 0, compartidas == len(cuotas), compartidas, len(cuotas)


def build_icon(
    page: ft.Page,
    shared_expenses_service: SharedExpensesService,
    fees_service: FeesService,
    compra: dict,
    on_cambio: Callable[[], None],
) -> tuple[ft.Control, bool]:
    """
    Returns:
        (control, ya_compartido) — mismo contrato que
        compartir_gasto.build_icon(): el caller (ui/screens/compras_cuotas.py)
        usa ya_compartido para decidir si el ícono queda siempre visible
        (indicador persistente) o solo aparece al hover de la fila.
    """
    algo_compartido, todo_compartido, compartidas, total = _estado_compartido(
        shared_expenses_service, fees_service, compra,
    )
    ya_compartido = algo_compartido

    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        snack = ft.SnackBar(content=ft.Text(mensaje), bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None)
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    def _abrir_sin_hogar(nombre_prellenado: str) -> None:
        abrir_dialogo_sin_hogar(page, shared_expenses_service, nombre_prellenado, on_listo=_abrir_flujo)

    # ------------------------------------------------------------
    # PASO "cargar/completar la compra compartida" — popover compacto
    # ------------------------------------------------------------
    def _abrir_cargar_compra(usuario_local: str, mis_hogares: list[dict]) -> None:
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

        controles: list[ft.Control] = [ft.Text(f"Modo de deuda de esta compra: {compra['modo_deuda']}.")]
        if compra["modo_deuda"] == "prorrateado" and compartidas > 0:
            controles.append(
                ft.Text(
                    f"{compartidas} de {total} cuota(s) ya tenían un gasto compartido y se van a saltear — "
                    f"esto va a compartir las {total - compartidas} restante(s).",
                    color=ft.Colors.OUTLINE,
                )
            )
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
                resultado = shared_expenses_service.add_shared_purchase(
                    compra_id=compra["id"],
                    hogar_id=hogar_seleccionado["id"],
                    pagador=usuario_local,
                    coeficiente_deuda=coeficiente,
                )
            except (SharedExpensesError, ValueError) as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_mensaje(f"{resultado.data['gastos_creados']} gasto(s) compartido(s) registrado(s).")
            on_cambio()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Compartir esta compra"),
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
    # PASO "ya compartido por completo" — detalle de solo lectura
    # ------------------------------------------------------------
    def _abrir_detalle() -> None:
        resumen = (
            "Compartida como gasto único."
            if compra["modo_deuda"] == "total_unico"
            else f"{compartidas} de {total} cuota(s) compartida(s)."
        )
        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Gasto compartido de esta compra"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [
                        ft.Text(f"Modo de deuda: {compra['modo_deuda']}."),
                        ft.Text(resumen),
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
    # async: obtener_usuario_local() ahora usa ft.SharedPreferences (async
    # de verdad) en vez de la inexistente page.client_storage — ver
    # docstring de ui/components/usuario_local.py para el detalle completo
    # del bug corregido.
    async def _abrir_flujo() -> None:
        usuario_local = await obtener_usuario_local(page)
        mis_hogares = shared_expenses_service.list_my_hogares(usuario_local) if usuario_local else []
        if not mis_hogares:
            _abrir_sin_hogar(nombre_prellenado=usuario_local or "")
            return
        _abrir_cargar_compra(usuario_local, mis_hogares)

    async def _on_click(e: ft.ControlEvent) -> None:
        if todo_compartido:
            _abrir_detalle()
        else:
            await _abrir_flujo()

    icono = ft.IconButton(
        icon=ft.Icons.PEOPLE if ya_compartido else ft.Icons.PEOPLE_OUTLINE,
        icon_color=ft.Colors.PRIMARY if ya_compartido else ft.Colors.OUTLINE,
        tooltip=(
            "Gasto compartido (ya cargado)" if todo_compartido
            else f"Compartido parcialmente ({compartidas}/{total}) — click para compartir el resto"
            if algo_compartido else "Compartir con hogar"
        ),
        on_click=_on_click,
    )
    return icono, ya_compartido
