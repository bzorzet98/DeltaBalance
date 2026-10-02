"""
DeltaBalance — ui/components/compartir_varios.py

Compartir VARIAS filas seleccionadas de una vez, con el mismo hogar y el
mismo coeficiente — botón "Compartir" de la barra flotante de
ui/components/tabla_planilla.py cuando hay más de una fila seleccionada
(Registro de transacciones y Compras en cuotas; con una sola fila cada
pantalla sigue abriendo su flujo de siempre, compartir_gasto.py /
compartir_compra.py).

Un solo diálogo con los mismos campos que el flujo de una fila de cada
pantalla: hogar (si el usuario tiene más de uno) + coeficiente del otro
miembro, con la misma sugerencia (SharedExpensesService.
get_suggested_coefficient()). El coeficiente se tipea en el mismo formato
que ese flujo: de 0 a 1 en el Registro (compartir_gasto.py), en % en
Compras en cuotas (compartir_compra.py) — `formato_coeficiente`. Si todas
las filas son ingresos (pago recibido, siempre al 100%) no se pide
coeficiente (`pide_coeficiente=False`), igual que compartir_gasto.py con
un ingreso. compartir_una recibe siempre el coeficiente en %. Qué se crea por cada
fila lo decide el caller en `compartir_una(fila, hogar_id, pagador,
coeficiente)` (add_shared_expense() para una transacción,
add_shared_purchase() para una compra): acá solo se arma el diálogo, se
recorre la lista y se resume el resultado. Una fila que falla no frena a
las demás: su error queda en el mensaje final.

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

from typing import Callable, Optional

import flet as ft

from services.shared_expenses_service import SharedExpensesError, SharedExpensesService
from ui.components.tabla_planilla import mostrar_mensaje
from ui.components.usuario_local import abrir_dialogo_sin_hogar, obtener_usuario_local

# --- Configuración de layout ---
ANCHO_DIALOGO = 380
ESPACIADO = 10
COEFICIENTE_MAXIMO = 100.0  # coeficiente_deuda va en %, 0 < c ≤ 100
COEFICIENTE_INGRESO = 100.0  # pago recibido (pide_coeficiente=False)
# 0.3 * 100 = 30.000000000000004: se redondea para no guardar ruido de float.
DECIMALES_COEFICIENTE = 4

FORMATO_PORCENTAJE = "%"
FORMATO_UNIDAD = "0-1"
# Formato → (label del campo, factor para pasarlo a %, mensaje de rango).
_FORMATOS = {
    FORMATO_PORCENTAJE: ("COEFICIENTE (%) DEL OTRO MIEMBRO", 1.0,
                         "EL COEFICIENTE DEBE SER MAYOR A 0 Y COMO MÁXIMO 100 (EJ: 50 PARA 50%)."),
    FORMATO_UNIDAD: ("COEFICIENTE DEL OTRO MIEMBRO (0 A 1)", 100.0,
                     "EL COEFICIENTE DEBE ESTAR ENTRE 0 Y 1 (EJ: 0.5 PARA 50%)."),
}


async def abrir_compartir_varios(
    page: ft.Page,
    shared_expenses_service: SharedExpensesService,
    titulo: str,
    filas: list[dict],
    compartir_una: Callable[[dict, int, str, float], None],
    avisos: list[str],
    on_cambio: Callable[[], None],
    errores_esperados: tuple[type[Exception], ...] = (),
    formato_coeficiente: str = FORMATO_PORCENTAJE,
    pide_coeficiente: bool = True,
) -> None:
    """
    Args:
        titulo:        Título del diálogo, ya en MAYÚSCULAS.
        filas:         Filas a compartir (cada una con "id").
        compartir_una: (fila, hogar_id, pagador, coeficiente %) → crea el
                       gasto compartido de esa fila; lanza para fallar.
        avisos:        Líneas informativas para el diálogo (ya en MAYÚSCULAS).
        on_cambio:     Llamado al terminar si se compartió al menos una fila.
        errores_esperados: excepciones de dominio de compartir_una, además
                       de SharedExpensesError/ValueError.
        formato_coeficiente: FORMATO_PORCENTAJE (50) o FORMATO_UNIDAD (0.5),
                       el mismo que el flujo de una fila de la pantalla.
        pide_coeficiente: False = sin campo de coeficiente (todas las filas
                       son ingresos: van al 100%).
    """
    usuario_local = await obtener_usuario_local(page)
    mis_hogares = shared_expenses_service.list_my_hogares(usuario_local) if usuario_local else []
    if not mis_hogares:
        async def _reintentar() -> None:
            await abrir_compartir_varios(
                page, shared_expenses_service, titulo, filas, compartir_una, avisos, on_cambio, errores_esperados,
                formato_coeficiente, pide_coeficiente,
            )

        abrir_dialogo_sin_hogar(page, shared_expenses_service, usuario_local or "", on_listo=_reintentar)
        return

    errores_capturados = (SharedExpensesError, ValueError, *errores_esperados)
    hogar_seleccionado = {"id": mis_hogares[0]["hogar_id"]}
    label_coeficiente, factor, error_rango = _FORMATOS[formato_coeficiente]

    def _texto_sugerido(sugerido: Optional[float]) -> str:
        # get_suggested_coefficient() devuelve % (50.0): se muestra en el formato del campo.
        return f"{sugerido / factor:g}" if sugerido is not None else ""

    def _sugerencia(hogar_id: str) -> tuple[Optional[float], str]:
        otros = [m for m in shared_expenses_service.list_miembros(hogar_id) if m["usuario_local"] != usuario_local]
        if not otros:
            return None, "SIN OTRO MIEMBRO EN ESTE HOGAR TODAVÍA."
        otro = otros[0]
        sugerido = shared_expenses_service.get_suggested_coefficient(hogar_id, otro["usuario_local"])
        if sugerido is None:
            return None, f"'{otro['usuario_local'].upper()}' NO TIENE UN COEFICIENTE DEFAULT CONFIGURADO."
        return sugerido, f"SUGERIDO SEGÚN EL DEFAULT DE '{otro['usuario_local'].upper()}'."

    sugerido_inicial, ayuda_inicial = _sugerencia(hogar_seleccionado["id"])
    campo_coeficiente = ft.TextField(
        label=label_coeficiente,
        value=_texto_sugerido(sugerido_inicial),
        helper_text=ayuda_inicial,
        autofocus=(len(mis_hogares) == 1),
        visible=pide_coeficiente,
    )

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    def _on_select_hogar(e: ft.ControlEvent) -> None:
        hogar_seleccionado["id"] = dropdown_hogar.value
        sugerido, ayuda = _sugerencia(hogar_seleccionado["id"])
        campo_coeficiente.value = _texto_sugerido(sugerido)
        campo_coeficiente.helper_text = ayuda
        campo_coeficiente.update()

    controles: list[ft.Control] = [ft.Text(aviso, color=ft.Colors.OUTLINE) for aviso in avisos]
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
    controles.append(campo_coeficiente)

    def _confirmar(e=None) -> None:
        if pide_coeficiente:
            try:
                coeficiente = round(float((campo_coeficiente.value or "").strip().replace(",", ".")) * factor, DECIMALES_COEFICIENTE)
            except ValueError:
                mostrar_mensaje(page, "EL COEFICIENTE NO ES UN NÚMERO VÁLIDO.", es_error=True)
                return
            if not 0 < coeficiente <= COEFICIENTE_MAXIMO:
                mostrar_mensaje(page, error_rango, es_error=True)
                return
        else:
            coeficiente = COEFICIENTE_INGRESO

        compartidas = 0
        errores: list[str] = []
        for fila in filas:
            try:
                compartir_una(fila, hogar_seleccionado["id"], usuario_local, coeficiente)
                compartidas += 1
            except errores_capturados as err:
                errores.append(f"#{fila['id']}: {err}")

        _cerrar_dialogo()
        mensaje = f"{compartidas} FILA(S) COMPARTIDA(S) AL {coeficiente:g}%."
        if errores:
            mensaje += f" {len(errores)} CON ERROR: " + " | ".join(errores)
        mostrar_mensaje(page, mensaje, es_error=bool(errores))
        if compartidas:
            on_cambio()

    page.show_dialog(ft.AlertDialog(
        modal=True,
        title=ft.Text(titulo),
        content=ft.Container(
            width=ANCHO_DIALOGO,
            content=ft.Column(controles, tight=True, spacing=ESPACIADO, scroll=ft.ScrollMode.AUTO),
        ),
        actions=[
            ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
            ft.ElevatedButton(content=ft.Text("COMPARTIR"), on_click=_confirmar),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    ))
