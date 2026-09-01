"""
DeltaBalance — ui/screens/cuentas.py

Pantalla de gestión de cuentas: listado (nombre, tipo, saldo por moneda,
badge si está archivada) con acciones editar/archivar/eliminar, y
formulario "Agregar cuenta" (nombre, tipo y color únicamente — sin ningún
campo de moneda, ni obligatorio ni opcional: desde la Tarea 6f cada
combinación cuenta+moneda se resuelve sola la primera vez que una
transacción real la usa, no hace falta declararla al crear). El diálogo de
"Editar cuenta" sí tiene una sección "Monedas" (una cuenta puede operar en
más de una, ver docs/DATA_MODEL_DECISIONS.md sección 14): lista las que la
cuenta ya tiene y permite agregar una nueva a mano vía
AccountsService.add_currency_to_account() — útil para declarar de antemano
una moneda que todavía no disparó ninguna transacción real.

Accesible desde dos lugares (armados por ui/app.py, no acá): un botón en el
dashboard y la sección "Configuración" de la sidebar. También se usa como
pantalla de onboarding (modo_onboarding=True) cuando todavía no existe
ninguna cuenta — ver ui/app.py.

Solo usa AccountsService (servicios/), nunca repositories/ ni db/ directo —
regla de arquitectura (CLAUDE.md §2/§3): la UI puede depender del motor de
datos, nunca al revés, y siempre a través de un service, no de un repositorio.

Diálogos: se abren/cierran con page.show_dialog()/page.pop_dialog() — API de
Flet 0.80+ ("1.0 Beta"), ver docs/FLET_API_NOTES.md. SnackBar usa el
mecanismo page.overlay + control.open — confirmado, sigue andando tal cual
en 0.86.5 (ver docs/FLET_API_NOTES.md).
"""

from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService, AccountsError
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.color_chip import color_chip

# Swatches fijos (no color picker libre) — evita depender de una API de
# selección de color de Flet que no está confirmada para 0.86.5 (ver
# docs/FLET_API_NOTES.md regla 2). El primero coincide con el default de
# columna (cuentas.color_hex, ver db/schema_migrations.py).
_PALETA_COLORES = [
    "#5F5E5A", "#E53935", "#8E24AA", "#3949AB", "#1E88E5",
    "#00897B", "#43A047", "#FDD835", "#FB8C00", "#6D4C41",
]


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    on_volver: Optional[Callable[[], None]] = None,
    modo_onboarding: bool = False,
    on_primera_cuenta_creada: Optional[Callable[[], None]] = None,
) -> ft.Control:
    """
    Construye la pantalla de Cuentas.

    Args:
        page:                     Página Flet activa (para abrir diálogos y refrescar).
        accounts_service:         Instancia de AccountsService ya conectada a la DB.
        on_volver:                Callback para volver al dashboard. None en onboarding
                                   (todavía no hay a dónde volver).
        modo_onboarding:          Si True, muestra el mensaje de bienvenida en vez del
                                   botón "Volver".
        on_primera_cuenta_creada: Callback que dispara ui/app.py cuando se crea la
                                   primera cuenta desde onboarding, para pasar al
                                   dashboard sin esperar a un reinicio de la app.
    """

    lista_cuentas = ft.Column(spacing=8)

    def _tipo_display(tipo: str) -> str:
        return {
            "debito": "Débito",
            "credito": "Crédito",
            "efectivo": "Efectivo",
            "crypto": "Cripto",
            "inversion": "Inversión",
        }.get(tipo, tipo)

    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        # page.overlay + control.open: patrón confirmado para SnackBar en
        # Flet 0.86.5 (ver docs/FLET_API_NOTES.md) — a diferencia de
        # AlertDialog, acá el patrón clásico pre-1.0 sigue andando tal cual.
        snack = ft.SnackBar(
            content=ft.Text(mensaje),
            bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None,
        )
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _mostrar_error(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=False)

    def _cerrar_dialogo() -> None:
        page.pop_dialog()

    # ------------------------------------------------------------
    # FORMULARIO: agregar / editar cuenta
    # ------------------------------------------------------------

    def _abrir_formulario(cuenta: Optional[dict] = None) -> None:
        es_edicion = cuenta is not None

        # list_currencies() solo hace falta para la sección "Monedas" del
        # diálogo de edición (ver más abajo) — el alta ya no pide moneda.
        monedas_disponibles = accounts_service.list_currencies() if es_edicion else []
        cuentas_existentes = [
            c for c in accounts_service.list_accounts(solo_activas=True)
            if not es_edicion or c["id"] != cuenta["id"]
        ]

        campo_nombre = ft.TextField(
            label="Nombre",
            value=cuenta["nombre"] if es_edicion else "",
            autofocus=True,
        )
        campo_notas = ft.TextField(
            label="Notas (opcional)",
            value=(cuenta.get("notas") or "") if es_edicion else "",
            multiline=True,
            min_lines=1,
            max_lines=3,
        )

        dropdown_padre = ft.Dropdown(
            label="Cuenta de pago (origen de los fondos)",
            hint_text="Opcional",
            options=[
                ft.dropdown.Option(key=str(c["id"]), text=f"{c['nombre']} ({_tipo_display(c['tipo'])})")
                for c in cuentas_existentes
            ],
            value=(str(cuenta["cuenta_pago_id"]) if es_edicion and cuenta.get("cuenta_pago_id") else None),
            visible=(cuenta["tipo"] == "credito") if es_edicion else False,
        )

        def _on_change_tipo(e: ft.ControlEvent) -> None:
            dropdown_padre.visible = dropdown_tipo.value == "credito"
            dialogo.update()

        dropdown_tipo = ft.Dropdown(
            label="Tipo",
            options=[
                ft.dropdown.Option(key=t, text=_tipo_display(t))
                for t in AccountsService.TIPOS_VALIDOS
            ],
            value=cuenta["tipo"] if es_edicion else None,
            disabled=es_edicion,  # tipo es estructural, no se puede cambiar
            on_select=_on_change_tipo,
        )

        # Sección "Monedas": solo existe en el diálogo de EDICIÓN. El alta ya
        # no pide moneda en ningún momento (Tarea 6f: se resuelve sola con la
        # primera transacción real; y de todos modos no hay cuenta_id
        # todavía en el alta para poder llamar a add_currency_to_account()).
        # Muestra las monedas que la cuenta ya tiene (chips, vía
        # cuenta["saldos"]) y un "+ Agregar moneda" que abre inline (no un
        # segundo AlertDialog superpuesto — sin precedente confirmado de
        # diálogos anidados en este proyecto, ver docs/FLET_API_NOTES.md) un
        # CampoFiltrable con las monedas que todavía no tiene.
        seccion_moneda: Optional[ft.Control] = None
        if es_edicion:
            cuenta_actual = {"datos": cuenta}
            lista_chips_moneda = ft.Row(spacing=6, wrap=True)
            fila_agregar_moneda = ft.Row(spacing=8, visible=False)

            def _moneda_ids_actuales() -> set:
                return {s["moneda_id"] for s in cuenta_actual["datos"]["saldos"]}

            def _refrescar_chips_moneda() -> None:
                saldos = cuenta_actual["datos"]["saldos"]
                if saldos:
                    lista_chips_moneda.controls = [
                        ft.Container(
                            content=ft.Text(s["moneda_codigo"], size=12),
                            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
                            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                            border_radius=12,
                        )
                        for s in saldos
                    ]
                else:
                    lista_chips_moneda.controls = [ft.Text("—", color=ft.Colors.OUTLINE)]

            def _ocultar_fila_agregar_moneda() -> None:
                fila_agregar_moneda.visible = False
                fila_agregar_moneda.controls = []
                dialogo.update()

            def _mostrar_fila_agregar_moneda(e: ft.ControlEvent) -> None:
                disponibles = [m for m in monedas_disponibles if m["id"] not in _moneda_ids_actuales()]
                if not disponibles:
                    _mostrar_error("La cuenta ya opera en todas las monedas disponibles.")
                    return

                campo_moneda_nueva = CampoFiltrable(
                    page,
                    [(str(m["id"]), m["codigo"]) for m in disponibles],
                    on_seleccionar=lambda id_: None,
                    placeholder="Moneda",
                    width=180,
                    autofocus=True,
                )

                def _confirmar_moneda(e2: ft.ControlEvent) -> None:
                    if not campo_moneda_nueva.id_seleccionado:
                        _mostrar_error("Elegí una moneda de la lista de sugerencias.")
                        return
                    try:
                        resultado = accounts_service.add_currency_to_account(
                            cuenta["id"], int(campo_moneda_nueva.id_seleccionado)
                        )
                    except AccountsError as err:
                        _mostrar_error(str(err))
                        return
                    cuenta_actual["datos"] = resultado.data
                    _refrescar_chips_moneda()
                    _ocultar_fila_agregar_moneda()
                    _refrescar()  # el listado de atrás también muestra saldo por moneda
                    _mostrar_ok(f"Moneda agregada a '{cuenta['nombre']}'.")

                fila_agregar_moneda.controls = [
                    campo_moneda_nueva.control,
                    ft.IconButton(icon=ft.Icons.CHECK, tooltip="Agregar", on_click=_confirmar_moneda),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE, tooltip="Cancelar",
                        on_click=lambda e2: _ocultar_fila_agregar_moneda(),
                    ),
                ]
                fila_agregar_moneda.visible = True
                dialogo.update()
                campo_moneda_nueva.focus()

            _refrescar_chips_moneda()

            seccion_moneda = ft.Column(
                [
                    ft.Text("Monedas", size=12, color=ft.Colors.OUTLINE),
                    lista_chips_moneda,
                    fila_agregar_moneda,
                    ft.TextButton(content=ft.Text("+ Agregar moneda"), on_click=_mostrar_fila_agregar_moneda),
                ],
                spacing=6,
            )

        # Selector de color: grilla fija de swatches (no color picker libre,
        # ver comentario junto a _PALETA_COLORES). Tocar un swatch lo elige.
        color_actual = {
            "valor": (cuenta.get("color_hex") or _PALETA_COLORES[0]) if es_edicion else _PALETA_COLORES[0]
        }
        fila_colores = ft.Row(spacing=8, wrap=True)

        def _elegir_color(color: str) -> None:
            color_actual["valor"] = color
            _refrescar_swatches()
            dialogo.update()

        def _swatch(color: str) -> ft.Control:
            seleccionado = color == color_actual["valor"]
            return ft.Container(
                width=28,
                height=28,
                bgcolor=color,
                border_radius=14,
                border=(
                    ft.Border.all(3, ft.Colors.PRIMARY)
                    if seleccionado
                    else ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
                ),
                on_click=lambda e, c=color: _elegir_color(c),
            )

        def _refrescar_swatches() -> None:
            fila_colores.controls = [_swatch(c) for c in _PALETA_COLORES]

        _refrescar_swatches()
        selector_color = ft.Column(
            [ft.Text("Color", size=12, color=ft.Colors.OUTLINE), fila_colores],
            spacing=4,
        )

        def _guardar(e: ft.ControlEvent) -> None:
            try:
                if es_edicion:
                    # cuenta_pago_id solo se manda si realmente cambia: pasar
                    # None dispara el bloqueo de "unlink no soportado" de
                    # AccountsService.update_account() (ver su docstring), y
                    # la mayoría de las cuentas (todas menos tipo='credito')
                    # ya tienen cuenta_pago_id=None de por sí.
                    kwargs_update = {
                        "nombre": campo_nombre.value,
                        "notas": campo_notas.value,
                        "color_hex": color_actual["valor"],
                    }
                    if dropdown_padre.visible and dropdown_padre.value:
                        nuevo_padre = int(dropdown_padre.value)
                        if nuevo_padre != cuenta.get("cuenta_pago_id"):
                            kwargs_update["cuenta_pago_id"] = nuevo_padre
                    resultado = accounts_service.update_account(cuenta["id"], **kwargs_update)
                    mensaje = f"Cuenta '{resultado.data['nombre']}' actualizada."
                else:
                    if not dropdown_tipo.value:
                        raise AccountsError("Elegí un tipo de cuenta.")
                    resultado = accounts_service.create_account(
                        nombre=campo_nombre.value,
                        tipo=dropdown_tipo.value,
                        cuenta_pago_id=(
                            int(dropdown_padre.value)
                            if dropdown_tipo.value == "credito" and dropdown_padre.value
                            else None
                        ),
                        notas=campo_notas.value or None,
                        color_hex=color_actual["valor"],
                    )
                    mensaje = f"Cuenta '{resultado.data['nombre']}' creada."
            except (AccountsError, ValueError) as err:
                _mostrar_error(str(err))
                return

            _cerrar_dialogo()
            era_onboarding = modo_onboarding and not es_edicion
            _refrescar()
            _mostrar_ok(mensaje)
            if era_onboarding and on_primera_cuenta_creada is not None:
                on_primera_cuenta_creada()

        controles_dialogo = [campo_nombre, dropdown_tipo]
        if seccion_moneda is not None:
            controles_dialogo.append(seccion_moneda)
        controles_dialogo += [selector_color, dropdown_padre, campo_notas]

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Editar cuenta" if es_edicion else "Agregar cuenta"),
            content=ft.Container(
                width=380,
                content=ft.Column(
                    controles_dialogo,
                    tight=True,
                    spacing=12,
                    scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=lambda e: _cerrar_dialogo()),
                ft.ElevatedButton(content=ft.Text("Guardar"), on_click=_guardar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )

        page.show_dialog(dialogo)

    # ------------------------------------------------------------
    # ARCHIVAR
    # ------------------------------------------------------------

    def _confirmar_archivar(cuenta: dict) -> None:
        def _archivar(e: ft.ControlEvent) -> None:
            _cerrar_dialogo()
            try:
                accounts_service.archive_account(cuenta["id"])
            except AccountsError as err:
                _mostrar_error(str(err))
                return
            _refrescar()
            _mostrar_ok(f"Cuenta '{cuenta['nombre']}' archivada.")

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Archivar cuenta"),
            content=ft.Text(
                f"¿Archivar '{cuenta['nombre']}'? Esto solo se puede hacer si su saldo es 0 en todas sus monedas."
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=lambda e: _cerrar_dialogo()),
                ft.ElevatedButton(content=ft.Text("Archivar"), on_click=_archivar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    # ------------------------------------------------------------
    # ELIMINAR
    # ------------------------------------------------------------

    def _confirmar_eliminar(cuenta: dict) -> None:
        def _eliminar(e: ft.ControlEvent) -> None:
            _cerrar_dialogo()
            try:
                accounts_service.delete_account(cuenta["id"])
            except AccountsError as err:
                # El mensaje del service ya explica cuál de las tres
                # condiciones falló (tiene movimientos, tiene saldo, o es
                # cuenta_pago_id de otra) — se lo mostramos tal cual en vez
                # de dejar que la app falle en silencio.
                _mostrar_error(str(err))
                return
            _refrescar()
            _mostrar_ok(f"Cuenta '{cuenta['nombre']}' eliminada.")

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Eliminar cuenta"),
            content=ft.Text(
                f"¿Eliminar '{cuenta['nombre']}' definitivamente? Esta acción no se puede deshacer. "
                f"Solo es posible si la cuenta nunca tuvo movimientos ni saldo cargado."
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=lambda e: _cerrar_dialogo()),
                ft.ElevatedButton(
                    content=ft.Text("Eliminar"),
                    on_click=_eliminar,
                    style=ft.ButtonStyle(bgcolor=ft.Colors.ERROR, color=ft.Colors.ON_ERROR),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    # ------------------------------------------------------------
    # LISTADO
    # ------------------------------------------------------------

    def _saldos_texto(cuenta: dict) -> str:
        if not cuenta["saldos"]:
            return "—"
        partes = []
        for s in cuenta["saldos"]:
            simbolo = s["moneda_simbolo"] or ""
            partes.append(f"{simbolo} {s['saldo']:,.2f} {s['moneda_codigo']}".strip())
        return " · ".join(partes)

    def _fila_cuenta(cuenta: dict) -> ft.Control:
        controles_derecha = [
            ft.Text(_saldos_texto(cuenta), weight=ft.FontWeight.BOLD),
        ]
        if not cuenta["activa"]:
            controles_derecha.insert(
                0,
                ft.Container(
                    content=ft.Text("Archivada", size=11, color=ft.Colors.ON_ERROR_CONTAINER),
                    bgcolor=ft.Colors.ERROR_CONTAINER,
                    padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                    border_radius=12,
                ),
            )

        acciones = [
            ft.IconButton(
                icon=ft.Icons.EDIT,
                tooltip="Editar",
                on_click=lambda e, c=cuenta: _abrir_formulario(c),
            ),
        ]
        if cuenta["activa"]:
            acciones.append(
                ft.IconButton(
                    icon=ft.Icons.ARCHIVE,
                    tooltip="Archivar",
                    on_click=lambda e, c=cuenta: _confirmar_archivar(c),
                )
            )
        acciones.append(
            ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                tooltip="Eliminar",
                icon_color=ft.Colors.ERROR,
                on_click=lambda e, c=cuenta: _confirmar_eliminar(c),
            )
        )

        return ft.Container(
            padding=12,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Row(
                                [color_chip(cuenta.get("color_hex")), ft.Text(cuenta["nombre"], weight=ft.FontWeight.BOLD)],
                                spacing=8,
                            ),
                            ft.Text(_tipo_display(cuenta["tipo"]), size=12, color=ft.Colors.OUTLINE),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                    ft.Row(controles_derecha, spacing=8),
                    ft.Row(acciones, spacing=0),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _refrescar() -> None:
        cuentas = accounts_service.list_accounts(solo_activas=False)
        if cuentas:
            lista_cuentas.controls = [_fila_cuenta(c) for c in cuentas]
        else:
            lista_cuentas.controls = [
                ft.Text("Todavía no cargaste ninguna cuenta.", italic=True, color=ft.Colors.OUTLINE)
            ]
        page.update()

    # ------------------------------------------------------------
    # ENCABEZADO
    # ------------------------------------------------------------

    encabezado_controles = []
    if modo_onboarding:
        encabezado_controles.append(
            ft.Column(
                [
                    ft.Text("¡Bienvenido a DeltaBalance!", size=24, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Para empezar, cargá tu primer banco, tarjeta o efectivo.",
                        size=14,
                        color=ft.Colors.OUTLINE,
                    ),
                ],
                spacing=4,
            )
        )
    else:
        fila_titulo = [ft.Text("Cuentas", size=24, weight=ft.FontWeight.BOLD)]
        if on_volver is not None:
            fila_titulo.insert(
                0,
                ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Volver", on_click=lambda e: on_volver()),
            )
        encabezado_controles.append(ft.Row(fila_titulo, alignment=ft.MainAxisAlignment.START))

    encabezado_controles.append(
        ft.ElevatedButton(
            content=ft.Text("Agregar cuenta"),
            icon=ft.Icons.ADD,
            on_click=lambda e: _abrir_formulario(None),
        )
    )

    _refrescar()

    return ft.Column(
        [
            ft.Row(encabezado_controles, alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ft.Container(height=8),
            lista_cuentas,
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
