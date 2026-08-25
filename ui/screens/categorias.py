"""
DeltaBalance — ui/screens/categorias.py

Pantalla de gestión de categorías: listado agrupado por categoria_principal
(nombre limpio de la subcategoría, sin prefijo de tipo — ver
ui/components/registro_transacciones.py y ui/screens/compras_cuotas.py para
el mismo criterio en los dropdowns) con badges "protegida"/"inactiva",
botón "Agregar categoría", y acciones editar/desactivar-reactivar por fila.
Las categorías protegidas (CategoriasService.CATEGORIAS_PROTEGIDAS) muestran
esas acciones deshabilitadas con un tooltip que explica el motivo — nunca se
ocultan, así el usuario entiende que existen pero no se pueden tocar.

Solo usa CategoriasService (services/), nunca repositories/ ni db/ directo
(CLAUDE.md §2/§3). Vive en la sidebar bajo "Configuración", junto a Cuentas
(agregado en ui/app.py).

Diálogos: page.show_dialog()/page.pop_dialog() — Flet 0.80+ ("1.0 Beta"),
ver docs/FLET_API_NOTES.md. SnackBar: page.overlay + control.open, mismo
patrón confirmado que el resto de ui/.
"""

from typing import Callable, Optional

import flet as ft

from services.categorias_service import (
    CategoriasError,
    CategoriasService,
    CategoryProtegidaError,
    TIPOS_VALIDOS,
)
from ui.theme.tokens import TypographyTokens

# --- Configuración de layout ---
ANCHO_DIALOGO_FORMULARIO = 380
ANCHO_BADGE_ESPACIO = 8

_TIPO_DISPLAY = {"ingreso": "Ingreso", "egreso": "Egreso", "movimiento": "Movimiento"}


def build(
    page: ft.Page,
    categorias_service: CategoriasService,
    on_volver: Optional[Callable[[], None]] = None,
) -> ft.Control:
    lista_categorias = ft.Column(spacing=8)

    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
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
    # FORMULARIO: agregar / editar categoría
    # ------------------------------------------------------------

    def _abrir_formulario(categoria: Optional[dict] = None) -> None:
        es_edicion = categoria is not None

        campo_principal = ft.TextField(
            label="Categoría principal",
            value=categoria["categoria_principal"] if es_edicion else "",
            autofocus=True,
        )
        campo_subcategoria = ft.TextField(
            label="Subcategoría",
            value=categoria["subcategoria"] if es_edicion else "",
        )
        dropdown_tipo = ft.Dropdown(
            label="Tipo",
            options=[ft.dropdown.Option(key=t, text=_TIPO_DISPLAY[t]) for t in TIPOS_VALIDOS],
            value=categoria["tipo"] if es_edicion else None,
        )

        def _guardar(e: ft.ControlEvent) -> None:
            try:
                if es_edicion:
                    resultado = categorias_service.update_category(
                        categoria["id"],
                        categoria_principal=campo_principal.value,
                        subcategoria=campo_subcategoria.value,
                        tipo=dropdown_tipo.value,
                    )
                    mensaje = f"Categoría '{resultado.data['subcategoria']}' actualizada."
                else:
                    if not dropdown_tipo.value:
                        raise CategoriasError("Elegí un tipo de categoría.")
                    resultado = categorias_service.create_category(
                        categoria_principal=campo_principal.value,
                        subcategoria=campo_subcategoria.value,
                        tipo=dropdown_tipo.value,
                    )
                    mensaje = f"Categoría '{resultado.data['subcategoria']}' creada."
            except (CategoriasError, ValueError) as err:
                _mostrar_error(str(err))
                return

            _cerrar_dialogo()
            _refrescar()
            _mostrar_ok(mensaje)

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Editar categoría" if es_edicion else "Agregar categoría"),
            content=ft.Container(
                width=ANCHO_DIALOGO_FORMULARIO,
                content=ft.Column(
                    [campo_principal, campo_subcategoria, dropdown_tipo],
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
    # DESACTIVAR / ACTIVAR
    # ------------------------------------------------------------

    def _confirmar_desactivar(categoria: dict) -> None:
        def _desactivar(e: ft.ControlEvent) -> None:
            _cerrar_dialogo()
            try:
                categorias_service.deactivate_category(categoria["id"])
            except (CategoriasError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _refrescar()
            _mostrar_ok(f"Categoría '{categoria['subcategoria']}' desactivada.")

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Desactivar categoría"),
            content=ft.Text(
                f"¿Desactivar '{categoria['subcategoria']}'? Deja de aparecer para cargar "
                f"movimientos nuevos, pero el historial existente no se toca."
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=lambda e: _cerrar_dialogo()),
                ft.ElevatedButton(content=ft.Text("Desactivar"), on_click=_desactivar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _activar(categoria: dict) -> None:
        try:
            categorias_service.activate_category(categoria["id"])
        except (CategoriasError, ValueError) as err:
            _mostrar_error(str(err))
            return
        _refrescar()
        _mostrar_ok(f"Categoría '{categoria['subcategoria']}' reactivada.")

    # ------------------------------------------------------------
    # LISTADO
    # ------------------------------------------------------------

    def _badge(texto: str, bgcolor: str, color: str) -> ft.Control:
        return ft.Container(
            content=ft.Text(texto, size=TypographyTokens.LABEL_SIZE, color=color),
            bgcolor=bgcolor,
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            border_radius=12,
        )

    def _fila_categoria(categoria: dict) -> ft.Control:
        protegida_motivo = categorias_service.protection_reason(categoria)
        es_protegida = protegida_motivo is not None
        esta_activa = categoria["activa"] == 1

        badges = []
        if es_protegida:
            badges.append(_badge("Protegida", ft.Colors.SECONDARY_CONTAINER, ft.Colors.ON_SECONDARY_CONTAINER))
        if not esta_activa:
            badges.append(_badge("Inactiva", ft.Colors.ERROR_CONTAINER, ft.Colors.ON_ERROR_CONTAINER))

        boton_editar = ft.IconButton(
            icon=ft.Icons.EDIT,
            tooltip=(
                f"No se puede editar: {protegida_motivo}" if es_protegida else "Editar"
            ),
            disabled=es_protegida,
            on_click=(lambda e, c=categoria: _abrir_formulario(c)) if not es_protegida else None,
        )

        if esta_activa:
            boton_estado = ft.IconButton(
                icon=ft.Icons.ARCHIVE,
                tooltip=(
                    f"No se puede desactivar: {protegida_motivo}" if es_protegida else "Desactivar"
                ),
                disabled=es_protegida,
                on_click=(lambda e, c=categoria: _confirmar_desactivar(c)) if not es_protegida else None,
            )
        else:
            boton_estado = ft.IconButton(
                icon=ft.Icons.UNARCHIVE,
                tooltip="Reactivar",
                on_click=lambda e, c=categoria: _activar(c),
            )

        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Row(
                            [
                                ft.Text(
                                    categoria["subcategoria"],
                                    size=TypographyTokens.TABLE_CONTENT_SIZE,
                                    weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                                ),
                                ft.Text(
                                    _TIPO_DISPLAY.get(categoria["tipo"], categoria["tipo"]),
                                    size=TypographyTokens.LABEL_SIZE,
                                    color=ft.Colors.OUTLINE,
                                ),
                                *badges,
                            ],
                            spacing=ANCHO_BADGE_ESPACIO,
                        ),
                        ft.Row([boton_editar, boton_estado], spacing=0),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Divider(height=1),
            ],
            spacing=4,
        )

    def _refrescar() -> None:
        categorias = categorias_service.list_categories(incluir_inactivas=True)
        if not categorias:
            lista_categorias.controls = [
                ft.Text("Todavía no cargaste ninguna categoría.", italic=True, color=ft.Colors.OUTLINE)
            ]
            page.update()
            return

        controles: list[ft.Control] = []
        principal_actual = None
        for c in categorias:
            if c["categoria_principal"] != principal_actual:
                principal_actual = c["categoria_principal"]
                controles.append(
                    ft.Text(
                        principal_actual,
                        size=TypographyTokens.LABEL_SIZE,
                        weight=ft.FontWeight.BOLD,
                        color=ft.Colors.OUTLINE,
                    )
                )
            controles.append(_fila_categoria(dict(c)))
        lista_categorias.controls = controles
        page.update()

    # ------------------------------------------------------------
    # ENCABEZADO
    # ------------------------------------------------------------

    fila_titulo = [
        ft.Text("Categorías", size=TypographyTokens.PAGE_TITLE_SIZE, weight=TypographyTokens.PAGE_TITLE_WEIGHT)
    ]
    if on_volver is not None:
        fila_titulo.insert(
            0,
            ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Volver", on_click=lambda e: on_volver()),
        )

    _refrescar()

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Row(fila_titulo, alignment=ft.MainAxisAlignment.START),
                    ft.ElevatedButton(
                        content=ft.Text("Agregar categoría"),
                        icon=ft.Icons.ADD,
                        on_click=lambda e: _abrir_formulario(None),
                    ),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            ft.Container(height=8),
            lista_categorias,
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
