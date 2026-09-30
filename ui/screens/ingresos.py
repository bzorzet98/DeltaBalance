"""
DeltaBalance — ui/screens/ingresos.py

Pantalla de Ingresos proyectados: una fila por cada ingreso esperado del
período (SUELDO, BECA, etc.), con estado pendiente/parcial/cobrado.
Usa IngresosProyectadosService — tabla propia `ingresos_proyectados`,
separada de `presupuestos`. En el futuro se vinculará con recibos de
sueldo y obra social.

ESPERADO es editable inline (click para editar, CLAUDE.md §10) con
CampoMonto — mismo patrón que _celda_monto() de
ui/components/registro_transacciones.py. Al confirmar llama a
IngresosProyectadosService.update() con monto_estimado_minor solo. El
diálogo de editar completo (_abrir_editar) se mantiene para concepto y
moneda. persistir_formula=True (CLAUDE.md §9), pero ingresos_proyectados
no tiene columna de fórmula (a diferencia de presupuestos.formula_estimado),
así que la fórmula tipeada no sobrevive a _refrescar(): se guarda solo el
número resuelto.
"""

from datetime import date
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.ingresos_proyectados_service import (
    IngresosProyectadosService,
    IngresoProyectadoError,
)
from ui.components import selector_periodo
from ui.components.campo_monto import CampoMonto
from ui.theme.tokens import LayoutTokens, TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---
ANCHO_CONCEPTO        = 200
ANCHO_ESTIMADO        = 130
ANCHO_PERCIBIDO       = 130
ANCHO_ESTADO          = 110
ANCHO_ACCIONES        = 140
ESPACIADO_FILA        = 8
MONEDA_DEFAULT_CODIGO = "ARS"
ANCHO_DIALOGO         = 340
# Celda editable inline de ESPERADO — mismos valores que
# ui/components/registro_transacciones.py (_celda_monto()).
ANCHO_BOTON_CONFIRMAR = 48
ANCHO_MINIMO_CAMPO_CELDA = 40

_COLORES_ESTADO = {
    "pendiente": ft.Colors.OUTLINE,
    "parcial":   ft.Colors.ORANGE,
    "cobrado":   ft.Colors.GREEN,
}


def build(
    page: ft.Page,
    ingresos_service: IngresosProyectadosService,
    accounts_service: AccountsService,
    on_volver: Optional[Callable[[], None]] = None,
) -> ft.Control:
    hoy = date.today()
    estado = {"mes": hoy.month, "anio": hoy.year}

    contenedor = ft.Column(spacing=ESPACIADO_FILA, expand=True, scroll=ft.ScrollMode.AUTO)

    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        snack = ft.SnackBar(
            content=ft.Text(mensaje),
            bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None,
        )
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    # ------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------

    def _listar_monedas() -> dict:
        return {dict(m)["id"]: dict(m) for m in accounts_service.list_currencies()}

    def _moneda_default(monedas: dict) -> dict:
        return next(
            (m for m in monedas.values() if m["codigo"] == MONEDA_DEFAULT_CODIGO),
            list(monedas.values())[0],
        )

    def _monto_desde_texto(texto: str, decimales: int) -> Optional[int]:
        try:
            monto = float(texto.strip().replace(",", "."))
            return int(round(monto * (10 ** decimales)))
        except ValueError:
            return None

    # ------------------------------------------------------------
    # DIÁLOGO — AGREGAR INGRESO
    # ------------------------------------------------------------

    def _abrir_agregar(e=None) -> None:
        monedas = _listar_monedas()
        moneda_sel = {"moneda": _moneda_default(monedas)}

        dropdown_moneda = ft.Dropdown(
            label="MONEDA",
            width=ANCHO_DIALOGO,
            dense=True,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
            options=[ft.dropdown.Option(key=str(m["id"]), text=m["codigo"]) for m in monedas.values()],
            value=str(moneda_sel["moneda"]["id"]),
        )

        def _on_moneda(e: ft.ControlEvent) -> None:
            moneda_sel["moneda"] = monedas[int(dropdown_moneda.value)]

        dropdown_moneda.on_change = _on_moneda

        campo_concepto = ft.TextField(
            label="CONCEPTO (SUELDO, BECA, etc.)",
            width=ANCHO_DIALOGO,
            autofocus=True,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
            # focus() es async en Flet 0.86.5: sin run_task no hacía nada.
            on_submit=lambda e: page.run_task(campo_monto.control.focus),
        )
        campo_monto = CampoMonto(
            page,
            on_confirmar=lambda m: None,
            width=ANCHO_DIALOGO,
            hint_text="Monto esperado (podés usar =950000+50000)",
            decimales=moneda_sel["moneda"]["decimales"],
            persistir_formula=True,
            # Enter resuelve la fórmula y pasa al botón, sin guardar (ahí Enter o click guardan).
            on_avanzar=lambda: page.run_task(boton_agregar.focus),
        )

        def _confirmar(e=None) -> None:
            concepto = (campo_concepto.value or "").strip().upper()
            if not concepto:
                _mostrar_mensaje("El concepto no puede estar vacío.", es_error=True)
                return
            monto_texto = (campo_monto.texto or "").strip()
            if not monto_texto:
                _mostrar_mensaje("Ingresá el monto esperado.", es_error=True)
                return
            moneda = moneda_sel["moneda"]
            monto_minor = _monto_desde_texto(monto_texto, moneda["decimales"])
            if monto_minor is None:
                _mostrar_mensaje("El monto no es válido.", es_error=True)
                return
            if monto_minor <= 0:
                _mostrar_mensaje("El monto debe ser mayor a cero.", es_error=True)
                return
            try:
                ingresos_service.create(
                    concepto=concepto,
                    mes=estado["mes"],
                    anio=estado["anio"],
                    monto_estimado_minor=monto_minor,
                    moneda_id=moneda["id"],
                )
            except (IngresoProyectadoError, ValueError) as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_mensaje("Ingreso agregado.")
            _refrescar()

        boton_agregar = ft.ElevatedButton(content=ft.Text("Agregar"), on_click=_confirmar)
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("AGREGAR INGRESO ESPERADO"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [campo_concepto, dropdown_moneda, campo_monto.control],
                    tight=True, spacing=12,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                boton_agregar,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # DIÁLOGO — EDITAR INGRESO
    # ------------------------------------------------------------

    def _abrir_editar(ingreso: dict) -> None:
        monedas = _listar_monedas()
        moneda_sel = {"moneda": monedas.get(ingreso["moneda_id"], _moneda_default(monedas))}

        dropdown_moneda = ft.Dropdown(
            label="MONEDA",
            width=ANCHO_DIALOGO,
            dense=True,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
            options=[ft.dropdown.Option(key=str(m["id"]), text=m["codigo"]) for m in monedas.values()],
            value=str(moneda_sel["moneda"]["id"]),
        )

        def _on_moneda(e: ft.ControlEvent) -> None:
            moneda_sel["moneda"] = monedas[int(dropdown_moneda.value)]

        dropdown_moneda.on_change = _on_moneda

        campo_concepto = ft.TextField(
            label="CONCEPTO",
            value=ingreso["concepto"],
            width=ANCHO_DIALOGO,
            autofocus=True,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
            # focus() es async en Flet 0.86.5: sin run_task no hacía nada.
            on_submit=lambda e: page.run_task(campo_monto.control.focus),
        )
        campo_monto = CampoMonto(
            page,
            on_confirmar=lambda m: None,
            width=ANCHO_DIALOGO,
            hint_text="Monto esperado (podés usar =950000+50000)",
            decimales=moneda_sel["moneda"]["decimales"],
            valor_inicial_minor=ingreso["monto_estimado_minor"],
            persistir_formula=True,
            # Enter resuelve la fórmula y pasa al botón, sin guardar (ahí Enter o click guardan).
            on_avanzar=lambda: page.run_task(boton_guardar.focus),
        )

        def _confirmar(e=None) -> None:
            concepto = (campo_concepto.value or "").strip().upper()
            if not concepto:
                _mostrar_mensaje("El concepto no puede estar vacío.", es_error=True)
                return
            monto_texto = (campo_monto.texto or "").strip()
            if not monto_texto:
                _mostrar_mensaje("Ingresá el monto esperado.", es_error=True)
                return
            moneda = moneda_sel["moneda"]
            monto_minor = _monto_desde_texto(monto_texto, moneda["decimales"])
            if monto_minor is None:
                _mostrar_mensaje("El monto no es válido.", es_error=True)
                return
            if monto_minor <= 0:
                _mostrar_mensaje("El monto debe ser mayor a cero.", es_error=True)
                return
            try:
                ingresos_service.update(
                    ingreso["id"],
                    concepto=concepto,
                    monto_estimado_minor=monto_minor,
                    moneda_id=moneda["id"],
                )
            except (IngresoProyectadoError, ValueError) as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_mensaje("Ingreso actualizado.")
            _refrescar()

        boton_guardar = ft.ElevatedButton(content=ft.Text("Guardar"), on_click=_confirmar)
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("EDITAR INGRESO"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [campo_concepto, dropdown_moneda, campo_monto.control],
                    tight=True, spacing=12,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                boton_guardar,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # DIÁLOGO — MARCAR COBRADO / PARCIAL
    # ------------------------------------------------------------

    def _abrir_cobro(ingreso: dict) -> None:
        monedas = _listar_monedas()
        moneda  = monedas.get(ingreso["moneda_id"], _moneda_default(monedas))
        simbolo = moneda.get("simbolo", "") or ""
        estimado_texto = amount_display(ingreso["monto_estimado_minor"], moneda["decimales"], simbolo)

        campo_monto = CampoMonto(
            page,
            on_confirmar=lambda m: None,
            width=ANCHO_DIALOGO,
            hint_text=f"Monto recibido (estimado: {estimado_texto})",
            decimales=moneda["decimales"],
            persistir_formula=True,
        )

        def _marcar_cobrado(e=None) -> None:
            monto_texto = (campo_monto.texto or "").strip()
            monto_minor = None
            if monto_texto:
                monto_minor = _monto_desde_texto(monto_texto, moneda["decimales"])
                if monto_minor is None:
                    _mostrar_mensaje("El monto no es válido.", es_error=True)
                    return
            try:
                ingresos_service.mark_collected(ingreso["id"], monto_minor)
            except (IngresoProyectadoError, ValueError) as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_mensaje("Ingreso marcado como cobrado.")
            _refrescar()

        def _marcar_parcial(e=None) -> None:
            monto_texto = (campo_monto.texto or "").strip()
            if not monto_texto:
                _mostrar_mensaje("Ingresá el monto recibido parcialmente.", es_error=True)
                return
            monto_minor = _monto_desde_texto(monto_texto, moneda["decimales"])
            if monto_minor is None:
                _mostrar_mensaje("El monto no es válido.", es_error=True)
                return
            try:
                ingresos_service.mark_partial(ingreso["id"], monto_minor)
            except (IngresoProyectadoError, ValueError) as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_mensaje("Ingreso marcado como cobro parcial.")
            _refrescar()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(f"REGISTRAR COBRO — {ingreso['concepto']}"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [
                        ft.Text(
                            f"Estimado: {estimado_texto}",
                            size=TypographyTokens.METADATA_SIZE,
                            color=ft.Colors.OUTLINE,
                        ),
                        campo_monto.control,
                        ft.Text(
                            "Dejá vacío para cobrar el monto estimado exacto.",
                            size=11,
                            color=ft.Colors.OUTLINE,
                        ),
                    ],
                    tight=True, spacing=10,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.TextButton(content=ft.Text("COBRO PARCIAL"), on_click=_marcar_parcial),
                ft.ElevatedButton(content=ft.Text("COBRADO"), on_click=_marcar_cobrado),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # CELDA EDITABLE INLINE (monto) — mismo patrón que _celda_monto() de
    # ui/components/registro_transacciones.py (CLAUDE.md §10). No se
    # re-renderiza sola tras un guardado exitoso: on_confirmar() dispara
    # _refrescar(), que reconstruye la grilla ya con el valor nuevo. Solo
    # vuelve a modo lectura acá si on_confirmar() lanza
    # IngresoProyectadoError/ValueError.
    # ------------------------------------------------------------

    def _celda_monto(
        texto_mostrado: str,
        monto_inicial_minor: int,
        decimales: int,
        on_confirmar: Callable[[int], None],
        width: int,
    ) -> ft.Control:
        contenedor = ft.Container(
            width=width,
            height=LayoutTokens.ALTURA_FILA_TABLA,
            padding=LayoutTokens.PADDING_CELDA,
        )

        def _mostrar() -> None:
            contenedor.content = ft.Container(
                content=ft.Text(
                    texto_mostrado,
                    size=TypographyTokens.TABLE_CONTENT_SIZE,
                    weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                    max_lines=1,
                    overflow=ft.TextOverflow.ELLIPSIS,
                    tooltip=texto_mostrado,
                ),
                on_click=lambda e: _editar(),
                ink=True,
                padding=LayoutTokens.PADDING_CELDA,
                alignment=ft.Alignment.CENTER_LEFT,
            )
            page.update()

        def _editar() -> None:
            def _confirmar(monto_minor: int) -> None:
                # Deshabilita campo y botón ANTES de llamar al service —
                # evita que un doble Enter/click dispare dos guardados
                # (mismo mecanismo que el Registro). Enter y el botón ✓
                # pasan los dos por campo.confirmar(), así que alcanza con
                # hacerlo acá.
                campo.control.disabled = True
                boton_confirmar_monto.disabled = True
                page.update()
                try:
                    on_confirmar(monto_minor)
                except (IngresoProyectadoError, ValueError) as err:
                    _mostrar_mensaje(str(err), es_error=True)
                    _mostrar()
                    raise

            campo = CampoMonto(
                page,
                on_confirmar=_confirmar,
                decimales=decimales,
                persistir_formula=True,
                valor_inicial_minor=monto_inicial_minor,
                width=max(width - ANCHO_BOTON_CONFIRMAR, ANCHO_MINIMO_CAMPO_CELDA),
                dense=LayoutTokens.CELDA_DENSE,
                text_size=TypographyTokens.TABLE_CONTENT_SIZE,
                autofocus=True,
            )

            def _on_click_confirmar(e: ft.ControlEvent) -> None:
                campo.confirmar()

            boton_confirmar_monto = ft.IconButton(
                icon=ft.Icons.CHECK, icon_color=ft.Colors.PRIMARY, on_click=_on_click_confirmar,
            )
            contenedor.content = ft.Row(
                [campo.control, boton_confirmar_monto],
                spacing=0,
                tight=True,
            )
            page.update()

        _mostrar()
        return contenedor

    # ------------------------------------------------------------
    # FILA
    # ------------------------------------------------------------

    def _fila_ingreso(ingreso: dict, moneda: dict) -> ft.Control:
        decimales  = moneda["decimales"]
        simbolo    = moneda.get("simbolo", "") or ""
        estado_str = ingreso["estado"]
        cobrado    = estado_str == "cobrado"

        texto_estimado  = amount_display(ingreso["monto_estimado_minor"], decimales, simbolo)
        texto_percibido = amount_display(ingreso["monto_percibido_minor"], decimales, simbolo)
        color_estado    = _COLORES_ESTADO.get(estado_str, ft.Colors.OUTLINE)

        def _confirmar_esperado(monto_minor: int) -> None:
            if monto_minor <= 0:
                raise ValueError("EL MONTO ESPERADO DEBE SER MAYOR A CERO.")
            ingresos_service.update(ingreso["id"], monto_estimado_minor=monto_minor)
            _mostrar_mensaje("INGRESO ACTUALIZADO.")
            _refrescar()

        celda_esperado = _celda_monto(
            texto_mostrado=texto_estimado,
            monto_inicial_minor=ingreso["monto_estimado_minor"],
            decimales=decimales,
            on_confirmar=_confirmar_esperado,
            width=ANCHO_ESTIMADO,
        )

        botones = ft.Row(
            [
                ft.IconButton(
                    icon=ft.Icons.EDIT_OUTLINED,
                    icon_size=16,
                    tooltip="Editar",
                    on_click=lambda e, ing=ingreso: _abrir_editar(ing),
                ),
                ft.IconButton(
                    icon=ft.Icons.CHECK_CIRCLE_OUTLINE,
                    icon_size=16,
                    tooltip="Registrar cobro",
                    disabled=cobrado,
                    on_click=lambda e, ing=ingreso: _abrir_cobro(ing),
                ),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE,
                    icon_size=16,
                    icon_color=ft.Colors.ERROR,
                    tooltip="Eliminar",
                    on_click=lambda e, ing=ingreso: _confirmar_eliminar(ing),
                ),
            ],
            spacing=0,
        )

        return ft.Row(
            [
                ft.Container(width=ANCHO_CONCEPTO,  content=ft.Text(ingreso["concepto"], size=TypographyTokens.TABLE_CONTENT_SIZE)),
                celda_esperado,
                ft.Container(width=ANCHO_PERCIBIDO, content=ft.Text(texto_percibido, size=TypographyTokens.TABLE_CONTENT_SIZE, weight=TypographyTokens.TABLE_CONTENT_WEIGHT, color=ft.Colors.GREEN if cobrado else None)),
                ft.Container(width=ANCHO_ESTADO,    content=ft.Text(estado_str.upper(), size=TypographyTokens.TABLE_CONTENT_SIZE, color=color_estado, weight=TypographyTokens.TABLE_CONTENT_WEIGHT)),
                ft.Container(width=ANCHO_ACCIONES,  content=botones),
            ],
            spacing=ESPACIADO_FILA,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ------------------------------------------------------------
    # ELIMINAR
    # ------------------------------------------------------------

    def _confirmar_eliminar(ingreso: dict) -> None:
        def _eliminar(e=None) -> None:
            try:
                ingresos_service._db.conn.execute(
                    "DELETE FROM ingresos_proyectados WHERE id = ?;",
                    (ingreso["id"],)
                )
                ingresos_service._db.conn.commit()
            except Exception as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_mensaje("Ingreso eliminado.")
            _refrescar()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("ELIMINAR INGRESO"),
            content=ft.Text(f"¿Eliminar '{ingreso['concepto']}'? Esta acción no se puede deshacer."),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Eliminar"), on_click=_eliminar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # REFRESCO
    # ------------------------------------------------------------

    def _refrescar() -> None:
        monedas_cache = _listar_monedas()
        ingresos      = ingresos_service.list_for_period(estado["mes"], estado["anio"])

        barra = ft.Row(
            [
                selector_periodo.build(estado, _refrescar),
                ft.Container(expand=True),
                ft.ElevatedButton(
                    content=ft.Text("+ AGREGAR INGRESO"),
                    icon=ft.Icons.ADD,
                    on_click=_abrir_agregar,
                ),
            ],
            spacing=ESPACIADO_FILA,
        )

        encabezado = ft.Row(
            [
                ft.Container(width=ANCHO_CONCEPTO,  content=ft.Text("CONCEPTO",  size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
                ft.Container(width=ANCHO_ESTIMADO,  content=ft.Text("ESPERADO",  size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
                ft.Container(width=ANCHO_PERCIBIDO, content=ft.Text("RECIBIDO",  size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
                ft.Container(width=ANCHO_ESTADO,    content=ft.Text("ESTADO",    size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
                ft.Container(width=ANCHO_ACCIONES,  content=ft.Text("",          size=TypographyTokens.TABLE_HEADER_SIZE)),
            ],
            spacing=ESPACIADO_FILA,
        )

        if ingresos:
            filas_grilla: list[ft.Control] = []
            for i, ing in enumerate(ingresos):
                if i > 0:
                    filas_grilla.append(ft.Divider(height=1))
                ing_dict = dict(ing)
                moneda   = monedas_cache.get(ing_dict["moneda_id"], _moneda_default(monedas_cache))
                filas_grilla.append(_fila_ingreso(ing_dict, moneda))
        else:
            filas_grilla = [
                ft.Text(
                    "No hay ingresos proyectados para este período — usá \"+ AGREGAR INGRESO\".",
                    italic=True,
                    color=ft.Colors.OUTLINE,
                )
            ]

        contenedor.controls = [
            barra,
            ft.Divider(height=1),
            encabezado,
            ft.Divider(height=1),
            ft.Column(filas_grilla, spacing=ESPACIADO_FILA),
        ]
        page.update()

    _refrescar()

    fila_titulo: list[ft.Control] = [
        ft.Text("INGRESOS", size=TypographyTokens.PAGE_TITLE_SIZE, weight=TypographyTokens.PAGE_TITLE_WEIGHT)
    ]
    if on_volver is not None:
        fila_titulo.insert(
            0,
            ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Volver", on_click=lambda e: on_volver()),
        )

    return ft.Column(
        [
            ft.Row(fila_titulo, alignment=ft.MainAxisAlignment.START),
            ft.Container(height=8),
            contenedor,
        ],
        spacing=8,
        expand=True,
    )