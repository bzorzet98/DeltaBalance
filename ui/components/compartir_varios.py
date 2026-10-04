"""
DeltaBalance — ui/components/compartir_varios.py

Compartir VARIAS filas seleccionadas de una vez, con el mismo hogar y el
mismo reparto — botón "Compartir" de la barra flotante de
ui/components/tabla_planilla.py cuando hay más de una fila seleccionada
(Registro de transacciones y Compras en cuotas; con una sola fila cada
pantalla sigue abriendo su flujo de siempre, compartir_gasto.py /
compartir_compra.py).

Un solo diálogo con los mismos campos que el flujo de una fila de cada
pantalla: hogar (si el usuario tiene más de uno) + reparto [%] [0.XX] [$]
con la misma sugerencia (ui/components/reparto_compartido.py, compartido
con compartir_gasto.py y compartir_compra.py). `tipo_inicial` es la pill
con la que arranca: la del flujo de una fila de esa pantalla (0.XX en el
Registro, % en Compras en cuotas).

El reparto se pasa a % POR FILA, sobre su monto base (`base_de(fila)`, con
signo): % y 0.XX dan el mismo % para todas; $ es el mismo monto fijo PARA
CADA fila (no se reparte entre ellas) y solo se ofrece si todas las filas
son de la misma moneda. Un $ que supera la base de alguna fila es un error
de esa fila. El coeficiente se pide siempre, también si hay ingresos
(pedido explícito: no se presupone que un ingreso se comparte al 100%).

Qué se crea por cada fila lo decide el caller en `compartir_una(fila,
hogar_id, pagador, coeficiente %)` (add_shared_expense() para una
transacción, add_shared_purchase() para una compra): acá solo se arma el
diálogo, se recorre la lista y se resume el resultado. Una fila que falla
no frena a las demás: su error queda en el mensaje final.

Las filas que no se pueden compartir (ya compartidas, canceladas…) las
saca el caller antes y las cuenta en `avisos`, que se muestran en el
diálogo.

Identidad del usuario local y diálogo de "todavía no tenés hogar": ver
ui/components/usuario_local.py, igual que compartir_gasto.py/
compartir_compra.py.

Reglas de arquitectura: solo SharedExpensesService (lectura de hogares/
miembros/sugerencia) — la escritura la hace el caller en compartir_una;
nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).
"""

from typing import Callable

import flet as ft

from services.shared_expenses_service import SharedExpensesError, SharedExpensesService
from ui.components.reparto_compartido import ORDEN_TIPOS, RepartoCompartido
from ui.components.tabla_planilla import mostrar_mensaje
from ui.components.tipo_valor import TIPO_MONTO, TIPO_PORCENTAJE
from ui.components.usuario_local import abrir_dialogo_sin_hogar, obtener_usuario_local, ordenar_hogares

# --- Configuración de layout ---
ANCHO_DIALOGO = 380
ESPACIADO = 10

AVISO_MONTO_FIJO = "CON $, EL MONTO FIJO VA PARA CADA FILA (NO SE REPARTE ENTRE ELLAS)."
AVISO_SIN_MONTO_FIJO = "SIN $: LAS FILAS SON DE MONEDAS DISTINTAS."


async def abrir_compartir_varios(
    page: ft.Page,
    shared_expenses_service: SharedExpensesService,
    titulo: str,
    filas: list[dict],
    compartir_una: Callable[[dict, int, str, float], None],
    avisos: list[str],
    on_cambio: Callable[[], None],
    base_de: Callable[[dict], int],
    errores_esperados: tuple[type[Exception], ...] = (),
    tipo_inicial: str = TIPO_PORCENTAJE,
) -> None:
    """
    Args:
        titulo:        Título del diálogo, ya en MAYÚSCULAS.
        filas:         Filas a compartir (cada una con "id", "currency_code"
                       y "decimales").
        compartir_una: (fila, hogar_id, pagador, coeficiente %) → crea el
                       gasto compartido de esa fila; lanza para fallar.
        avisos:        Líneas informativas para el diálogo (ya en MAYÚSCULAS).
        on_cambio:     Llamado al terminar si se compartió al menos una fila.
        base_de:       fila → su monto base en minor units, con signo (sobre
                       él se calcula el % de esa fila).
        errores_esperados: excepciones de dominio de compartir_una, además
                       de SharedExpensesError/ValueError.
        tipo_inicial:  Pill con la que arranca el reparto (tipo_valor.py).
    """
    usuario_local = await obtener_usuario_local(page)
    # El hogar por defecto (el primero) es el de más miembros — ver ordenar_hogares().
    mis_hogares = (
        ordenar_hogares(shared_expenses_service, shared_expenses_service.list_my_hogares(usuario_local))
        if usuario_local else []
    )
    if not mis_hogares:
        async def _reintentar() -> None:
            await abrir_compartir_varios(
                page, shared_expenses_service, titulo, filas, compartir_una, avisos, on_cambio, base_de,
                errores_esperados, tipo_inicial,
            )

        abrir_dialogo_sin_hogar(page, shared_expenses_service, usuario_local or "", on_listo=_reintentar)
        return

    errores_capturados = (SharedExpensesError, ValueError, *errores_esperados)
    hogar_seleccionado = {"id": mis_hogares[0]["hogar_id"]}
    # Un monto fijo en dos monedas no tiene sentido: $ solo con una sola moneda.
    misma_moneda = len({f["currency_code"] for f in filas}) == 1
    reparto = RepartoCompartido(
        page, shared_expenses_service, usuario_local, hogar_seleccionado["id"],
        decimales=filas[0]["decimales"], tipo_inicial=tipo_inicial,
        orden=ORDEN_TIPOS if misma_moneda else tuple(t for t in ORDEN_TIPOS if t != TIPO_MONTO),
        autofocus=(len(mis_hogares) == 1),
        # Enter no comparte: del valor pasa al botón (ahí Enter o click confirman).
        on_enter=lambda: page.run_task(boton_compartir.focus),
    )

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    def _on_select_hogar(e: ft.ControlEvent) -> None:
        hogar_seleccionado["id"] = dropdown_hogar.value
        reparto.cambiar_hogar(hogar_seleccionado["id"])

    controles: list[ft.Control] = [ft.Text(aviso, color=ft.Colors.OUTLINE) for aviso in avisos]
    controles.append(ft.Text(AVISO_MONTO_FIJO if misma_moneda else AVISO_SIN_MONTO_FIJO, color=ft.Colors.OUTLINE))
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
        # El valor se valida una vez contra la base más grande: si ahí no es
        # válido, no lo es para ninguna fila — error y el diálogo queda abierto.
        try:
            porcentaje = reparto.porcentaje(max((base_de(f) for f in filas), key=abs))
        except ValueError as err:
            mostrar_mensaje(page, str(err), es_error=True)
            return

        compartidas = 0
        errores: list[str] = []
        for fila in filas:
            try:
                compartir_una(fila, hogar_seleccionado["id"], usuario_local, reparto.porcentaje(base_de(fila)))
                compartidas += 1
            except errores_capturados as err:
                errores.append(f"#{fila['id']}: {err}")

        _cerrar_dialogo()
        # Con $ el % cambia por fila: solo se informa con % / 0.XX.
        detalle = "" if reparto.tipo == TIPO_MONTO else f" AL {porcentaje:g}%"
        mensaje = f"{compartidas} FILA(S) COMPARTIDA(S){detalle}."
        if errores:
            mensaje += f" {len(errores)} CON ERROR: " + " | ".join(errores)
        mostrar_mensaje(page, mensaje, es_error=bool(errores))
        if compartidas:
            on_cambio()

    boton_compartir = ft.ElevatedButton(content=ft.Text("COMPARTIR"), on_click=_confirmar)
    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text(titulo),
        content=ft.Container(
            width=ANCHO_DIALOGO,
            content=ft.Column(controles, tight=True, spacing=ESPACIADO, scroll=ft.ScrollMode.AUTO),
        ),
        actions=[
            ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
            boton_compartir,
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))
