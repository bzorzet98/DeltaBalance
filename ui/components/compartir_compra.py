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

Reparto: [%] [0.XX] [$] — ver ui/components/reparto_compartido.py
(compartido con compartir_gasto.py y compartir_varios.py). La base es el
total de la compra (monto_base_compra()); con $ el monto fijo es sobre ese
total.

Identidad del usuario local y diálogo de "todavía no tenés hogar": ver
ui/components/usuario_local.py, compartido con compartir_gasto.py.

Reglas de arquitectura: SharedExpensesService (dueño de
gastos_compartidos) y FeesService (solo LECTURA, para leer las cuotas de
la compra al chequear estado y armar el detalle — nunca escribe
compras_cuotas/cuotas_credito desde acá) — nunca repositories/ ni db/
directo (CLAUDE.md §2/§3).
"""

from typing import Callable

import flet as ft

from services.fees_service import FeesService
from services.shared_expenses_service import SharedExpensesError, SharedExpensesService
from ui.components.reparto_compartido import RepartoCompartido
from ui.components.tipo_valor import TIPO_PORCENTAJE
from ui.components.usuario_local import abrir_dialogo_sin_hogar, obtener_usuario_local, ordenar_hogares

# --- Configuración de layout ---
ANCHO_DIALOGO = 360
ESPACIADO = 10
ETIQUETA_PREVIA_COMPRA = "ADEUDADO SOBRE EL TOTAL"


def monto_base_compra(compra: dict) -> int:
    """
    Monto base de la compra ENTERA (con signo: negativo en una devolución) —
    el mismo que usa add_shared_purchase() en modo 'total_unico'
    (monto_total_minor − monto_reintegro_minor). Es la base del reparto en
    los diálogos: con $, el monto fijo se pasa a % de este total y ese % se
    aplica igual a cada cuota en modo 'prorrateado' (si ya había cuotas
    compartidas, el adeudado nuevo es proporcionalmente menor).
    """
    return compra["monto_total_minor"] - (compra["monto_reintegro_minor"] or 0)


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
        base_total = monto_base_compra(compra)
        # [%] [0.XX] [$] (ui/components/reparto_compartido.py); arranca en %,
        # el formato que tenía este diálogo. Con $ el monto fijo es sobre el
        # TOTAL de la compra (ver monto_base_compra()).
        reparto = RepartoCompartido(
            page, shared_expenses_service, usuario_local, hogar_seleccionado["id"],
            decimales=compra["decimales"], tipo_inicial=TIPO_PORCENTAJE,
            base_minor=base_total, simbolo=compra["currency_symbol"] or "",
            etiqueta_previa=ETIQUETA_PREVIA_COMPRA, autofocus=(len(mis_hogares) == 1),
            # Enter no comparte: del valor pasa al botón (ahí Enter o click confirman).
            on_enter=lambda: page.run_task(boton_confirmar.focus),
        )

        def _on_select_hogar(e: ft.ControlEvent) -> None:
            hogar_seleccionado["id"] = dropdown_hogar.value
            reparto.cambiar_hogar(hogar_seleccionado["id"])

        controles: list[ft.Control] = [ft.Text(f"MODO DE DEUDA DE ESTA COMPRA: {compra['modo_deuda'].upper()}.")]
        if compra["modo_deuda"] == "prorrateado" and compartidas > 0:
            controles.append(
                ft.Text(
                    f"{compartidas} DE {total} CUOTA(S) YA TENÍAN UN GASTO COMPARTIDO Y SE VAN A SALTEAR — "
                    f"ESTO VA A COMPARTIR LAS {total - compartidas} RESTANTE(S).",
                    color=ft.Colors.OUTLINE,
                )
            )
        if len(mis_hogares) > 1:
            dropdown_hogar = ft.Dropdown(
                label="HOGAR",
                options=[
                    ft.dropdown.Option(key=str(h["hogar_id"]), text=(h["nombre"] or f"HOGAR #{h['hogar_id']}").upper())
                    for h in mis_hogares
                ],
                value=str(hogar_seleccionado["id"]),
                on_select=_on_select_hogar,
                autofocus=True,
            )
            controles.append(dropdown_hogar)
        controles.append(reparto.control)

        def _confirmar(e=None) -> None:
            try:
                coeficiente = reparto.porcentaje(base_total)
            except ValueError as err:
                _mostrar_mensaje(str(err), es_error=True)
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
            _mostrar_mensaje(f"{resultado.data['gastos_creados']} GASTO(S) COMPARTIDO(S) REGISTRADO(S).")
            on_cambio()

        boton_confirmar = ft.ElevatedButton(content=ft.Text("CONFIRMAR"), on_click=_confirmar)
        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("COMPARTIR ESTA COMPRA"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(controles, tight=True, spacing=ESPACIADO, scroll=ft.ScrollMode.AUTO),
            ),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                boton_confirmar,
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
        # El hogar por defecto (el primero) es el de más miembros — ver ordenar_hogares().
        mis_hogares = (
            ordenar_hogares(shared_expenses_service, shared_expenses_service.list_my_hogares(usuario_local))
            if usuario_local else []
        )
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
