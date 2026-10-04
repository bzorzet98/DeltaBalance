"""
DeltaBalance — ui/screens/ahorros.py

Ahorros e inversiones (services/savings_service.py), rediseño por
pestañas: RESUMEN · FCI · ACCIONES · CEDEARS · PLAZO FIJO · PLAZO FLEX ·
OTROS. Mismo fondo y paleta que las pantallas estilo planilla
(pantalla_planilla(), ui/theme/tabla_tokens.py).

Las pestañas son pills (el mismo switch que ME DEBEN / DEBO de Deudas), no
ft.Tabs: la API de tabs cambió en Flet 0.80+ y no está confirmada en este
proyecto (docs/FLET_API_NOTES.md); las pills ya se usan y se ven igual que
el resto de la app.

Reglas de arquitectura: solo SavingsService/AccountsService — nunca
repositories/ ni db/ directo (CLAUDE.md §2/§3). Los formularios viven en
ui/components/dialogo_compra_ahorro.py (construir_nuevo_activo(),
construir_movimiento(), construir_objetivos_activo()); acá se arma el
AlertDialog alrededor de cada uno.

--- Estado propio ---

Pestaña y vista del RESUMEN en un almacén por página (_ESTADOS_UI): se
conservan cuando ui/app.py reconstruye la pantalla. Los datos se piden de
nuevo en cada redibujo (SavingsService.get_resumen_por_tipo()).

--- RESUMEN ---

[POR INSTRUMENTO]: una sección por tipo de activo, cada activo con su
broker, su saldo (o sus unidades, en acciones / CEDEARs) y debajo su
reparto entre objetivos. [POR OBJETIVO]: una sección por objetivo con la
parte que le toca de cada activo (SavingsService.get_resumen_por_objetivo(),
saldo / unidades × porcentaje); al final, SIN ASIGNAR con lo que ningún
objetivo tiene.

--- Pestañas por tipo ---

Una tarjeta por activo. FCI, PLAZO FIJO, PLAZO FLEX y OTROS se cuentan en
plata: SALDO (CAPITAL en los plazos) — el pedido decía "Cuotapartes" para
los FCI, pero sus movimientos se cargan en pesos, así que lo que se conoce
es el saldo. Botones: + RENDIMIENTO, + APORTE, − RETIRO. ACCIONES y
CEDEARS se cuentan en unidades: CANTIDAD y PRECIO PROMEDIO (de las
compras), botones + COMPRA, + VENTA, + RENDIMIENTO (dividendos). Todas
tienen además OBJETIVOS (editar el reparto) y VER MOVIMIENTOS (lista del
activo, con borrar). PLAZO FIJO sin vencimiento: decisión del usuario (el
schema no lo guarda).

"+ NUEVO …" de cada pestaña crea un activo de ese tipo; en ACCIONES,
CEDEARS y los plazos, enseguida abre su primer movimiento (la compra / el
aporte del capital: MOVIMIENTO_TRAS_ALTA).

OTROS (no estaba en el pedido): los activos 'cripto' y 'otro' de antes del
rediseño — entre ellos el "Efectivo reservado en <cuenta>" que crea el
Registro con la categoría Ahorro/Inversión —, que si no solo se verían en
el RESUMEN. No tiene "+ NUEVO": esos nacen en el Registro.

"+ NUEVO OBJETIVO" (barra de título) crea un objetivo de ahorro (no estaba
en el pedido, pero sin objetivos no hay a qué repartir).

--- Borrar un movimiento ---

SavingsService.delete_movement(): si el movimiento generó una transacción
real vinculada, pregunta si se borra también (mismo flujo que la pantalla
anterior).
"""

from datetime import datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.savings_service import SavingsError, SavingsResult, SavingsService
from ui.components import dialogo_compra_ahorro
from ui.components.campo_monto import CampoMonto
from ui.components.tabla_planilla import (
    ANCHO_BORDE,
    ESPACIO_DOT,
    PILL_ALTURA,
    PILL_PADDING_H,
    PILL_RADIO,
    mostrar_mensaje,
    pantalla_planilla,
    sin_auto_update,
)
from ui.components.tipo_valor import numero
from ui.theme.tabla_tokens import (
    BG_SUPERFICIE,
    BORDER_DEFAULT,
    BTN_COMPARTIR,
    PESO_HEADER,
    PESO_MONTO,
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_NEGATIVO,
    TEXT_POSITIVO,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_SOBRE_BOTON,
)
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---

TABS = (
    ("resumen", "RESUMEN"), ("fci", "FCI"), ("accion", "ACCIONES"), ("cedear", "CEDEARS"),
    ("plazo_fijo", "PLAZO FIJO"), ("plazo_flex", "PLAZO FLEX"), ("otros", "OTROS"),
)
# Tipos de activo de cada pestaña (ver docstring, "OTROS").
TIPOS_POR_TAB = {
    "fci": ("fci",), "accion": ("accion",), "cedear": ("cedear",),
    "plazo_fijo": ("plazo_fijo",), "plazo_flex": ("plazo_flex",), "otros": ("cripto", "otro"),
}
VISTAS_RESUMEN = (("instrumento", "POR INSTRUMENTO"), ("objetivo", "POR OBJETIVO"))
TAB_DEFAULT = "resumen"
VISTA_DEFAULT = "instrumento"

NOMBRE_TIPO = {
    "fci": "FCI", "accion": "ACCIÓN", "cedear": "CEDEAR", "plazo_fijo": "PLAZO FIJO",
    "plazo_flex": "PLAZO FLEX", "cripto": "CRIPTO", "otro": "OTRO",
}
TITULO_TIPO = {
    "fci": "FCI", "accion": "ACCIONES", "cedear": "CEDEARS", "plazo_fijo": "PLAZO FIJO",
    "plazo_flex": "PLAZO FLEX", "cripto": "CRIPTO", "otro": "OTROS",
}
UNIDADES_TIPO = {"accion": "ACCIONES", "cedear": "CEDEARS"}
UNIDADES_DEFAULT = "UNIDADES"
ETIQUETA_SALDO = {"plazo_fijo": "CAPITAL", "plazo_flex": "CAPITAL"}
ETIQUETA_SALDO_DEFAULT = "SALDO"
BOTON_NUEVO = {
    "fci": "+ NUEVO FCI", "accion": "+ NUEVA COMPRA", "cedear": "+ NUEVA COMPRA",
    "plazo_fijo": "+ NUEVO PLAZO FIJO", "plazo_flex": "+ NUEVO PLAZO FLEX",
}
# Movimiento que se abre enseguida al crear un activo desde su pestaña.
MOVIMIENTO_TRAS_ALTA = {"accion": "compra", "cedear": "compra", "plazo_fijo": "aporte", "plazo_flex": "aporte"}
TEXTO_MOVIMIENTO = {"compra": "COMPRA", "venta": "VENTA", "rendimiento": "RENDIMIENTO", "aporte": "APORTE"}
TEXTO_RETIRO = "RETIRO"  # una venta sin cantidad (FCI, plazos)
COLOR_MOVIMIENTO = {"venta": TEXT_NEGATIVO, "rendimiento": TEXT_POSITIVO}
TEXTO_SIN_OBJETIVOS = "SIN OBJETIVOS"
SEPARADOR_OBJETIVOS = " · "
PREFIJO_SUBLINEA = "└ "

TAMANIO_PILLS = TypographyTokens.REGISTRO_FONT_SALDO_BAR
ESPACIO_PILLS = ESPACIO_DOT
ESPACIADO = 12
ESPACIADO_LINEAS = 4
PADDING_TARJETA = 12
RADIO_TARJETA = 8
SANGRIA_RESUMEN = 16
ANCHO_LINEA_TITULO_SECCION = 24
ANCHO_DIALOGO = 380
ANCHO_DIALOGO_MOVIMIENTOS = 680
ALTO_LISTA_MOVIMIENTOS = 360
ANCHO_COL_FECHA = 90
ANCHO_COL_TIPO = 100
ANCHO_COL_CANTIDAD = 70
ANCHO_COL_MONTO = 130
ANCHO_COL_COMISION = 100
ANCHO_COL_OBJETIVOS = 140
ICONO_BORRAR = 16
# objetivos_ahorro no tiene moneda: la meta se carga con estos decimales (como antes del rediseño).
DECIMALES_META = 2


# ============================================================
# ESTADO PROPIO POR PÁGINA
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        ui = {"tab": TAB_DEFAULT, "vista": VISTA_DEFAULT}
        _ESTADOS_UI[id(page)] = ui
    return ui


# ============================================================
# HELPERS
# ============================================================

def _fmt_unidades(unidades: Optional[float]) -> str:
    """10 → '10'; 7.5 → '7.50' (las acciones / CEDEARs son enteras; la parte de un objetivo puede no serlo)."""
    valor = unidades or 0
    return f"{int(valor):,}" if float(valor).is_integer() else f"{valor:,.2f}"


def _texto_tenencia(entrada: dict, unidades: str = UNIDADES_DEFAULT) -> str:
    """'285,432.50 ARS' en plata, '10 UNIDADES' en acciones / CEDEARs."""
    if entrada["por_unidades"]:
        return f"{_fmt_unidades(entrada['unidades'])} {unidades}"
    return f"{amount_display(entrada['saldo_minor'], entrada['decimales'], '')} {entrada['moneda']}"


def _texto_objetivos(objetivos: list[dict]) -> str:
    return SEPARADOR_OBJETIVOS.join(f"{o['nombre'].upper()} {o['porcentaje']:g}%" for o in objetivos) or TEXTO_SIN_OBJETIVOS


def _pill(texto: str, activa: bool, on_click: Callable[[], None], tooltip: Optional[str] = None) -> ft.Control:
    """Pill de selección (mismo estilo que el switch de Deudas): la activa, rellena."""
    return ft.Container(
        height=PILL_ALTURA,
        padding=ft.Padding.symmetric(horizontal=PILL_PADDING_H),
        border_radius=PILL_RADIO,
        bgcolor=BTN_COMPARTIR if activa else None,
        border=None if activa else ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
        alignment=ft.Alignment.CENTER,
        tooltip=tooltip,
        on_click=lambda e: on_click(),
        content=ft.Text(texto, size=TAMANIO_PILLS, weight=PESO_HEADER, color=TEXT_SOBRE_BOTON if activa else TEXT_SECONDARY),
    )


def _titulo_seccion(texto: str) -> ft.Control:
    """'── FCI ─────────'."""
    return ft.Row(
        [
            ft.Container(width=ANCHO_LINEA_TITULO_SECCION, height=ANCHO_BORDE, bgcolor=BORDER_DEFAULT),
            ft.Text(
                texto, size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                color=TEXT_SECONDARY,
            ),
            ft.Container(expand=True, height=ANCHO_BORDE, bgcolor=BORDER_DEFAULT),
        ],
        spacing=ESPACIADO,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _boton(texto: str, on_click: Callable[[], None], color: str = TEXT_ACCENT) -> ft.Control:
    return ft.TextButton(
        content=ft.Text(texto, size=TypographyTokens.LABEL_SIZE, weight=PESO_HEADER, color=color),
        on_click=lambda e: on_click(),
    )


def _texto(texto: str, color: str = TEXT_PRIMARY, weight=None, size: int = TypographyTokens.METADATA_SIZE,
           width: Optional[int] = None, expand: bool = False) -> ft.Control:
    control = ft.Text(
        texto, color=color, weight=weight, size=size, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
        tooltip=texto or None, expand=expand,
    )
    return ft.Container(width=width, content=control) if width is not None else control


# ============================================================
# PANTALLA
# ============================================================

def build(
    page: ft.Page,
    savings_service: SavingsService,
    accounts_service: AccountsService,
    on_volver: Optional[Callable[[], None]] = None,
) -> ft.Control:
    ui = _estado_ui(page)
    raiz = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    datos: dict = {"resumen": {}}

    def _ok(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje)

    def _error(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje.upper(), es_error=True)

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    def _abrir_formulario(titulo: str, formulario, texto_confirmar: str = "CONFIRMAR") -> None:
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(titulo),
            content=formulario.contenido,
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text(texto_confirmar), on_click=lambda e: formulario.confirmar()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _tras_guardar(mensaje: str) -> None:
        _cerrar_dialogo()
        _ok(mensaje)
        _redibujar()

    def _entrada(activo_id: str) -> Optional[dict]:
        return next((e for entradas in datos["resumen"].values() for e in entradas if e["activo_id"] == activo_id), None)

    # ------------------------------------------------------------
    # ACCIONES (formularios de dialogo_compra_ahorro.py)
    # ------------------------------------------------------------

    def _nuevo_activo(tipo: str) -> None:
        def _on_exito(resultado: SavingsResult) -> None:
            _tras_guardar(resultado.message)
            movimiento = MOVIMIENTO_TRAS_ALTA.get(tipo)
            entrada = _entrada(resultado.entity_id)
            if movimiento and entrada is not None:
                _movimiento(entrada, movimiento)

        formulario = dialogo_compra_ahorro.construir_nuevo_activo(
            page, savings_service, accounts_service, _on_exito, tipo_inicial=tipo,
        )
        _abrir_formulario(f"NUEVO ACTIVO — {NOMBRE_TIPO[tipo]}", formulario, "CREAR")

    def _movimiento(entrada: dict, tipo: Optional[str] = None) -> None:
        formulario = dialogo_compra_ahorro.construir_movimiento(
            page, savings_service, accounts_service, entrada, lambda resultado: _tras_guardar(resultado.message),
            tipo_inicial=tipo,
        )
        _abrir_formulario(f"{entrada['activo'].upper()} — MOVIMIENTO", formulario)

    def _objetivos(entrada: dict) -> None:
        formulario = dialogo_compra_ahorro.construir_objetivos_activo(page, savings_service, entrada, _tras_guardar)
        _abrir_formulario(f"{entrada['activo'].upper()} — OBJETIVOS", formulario, "GUARDAR")

    def _nuevo_objetivo() -> None:
        campo_nombre = ft.TextField(label="NOMBRE", autofocus=True)
        campo_meta = CampoMonto(
            page, on_confirmar=lambda m: None, decimales=DECIMALES_META, dense=False, label="META (OPCIONAL)",
        )
        campo_fecha_meta = ft.TextField(label="FECHA META AAAA-MM-DD (OPCIONAL)")
        texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

        def _confirmar() -> None:
            def _falla(mensaje: str) -> None:
                texto_error.value = mensaje
                page.update()

            nombre = (campo_nombre.value or "").strip().upper()
            if not nombre:
                _falla("EL NOMBRE NO PUEDE ESTAR VACÍO.")
                return
            monto_meta_minor = None
            if campo_meta.confirmar() and (campo_meta.texto or "").strip():
                meta = numero(campo_meta.texto)
                if meta is None or meta <= 0:
                    _falla("LA META TIENE QUE SER UN NÚMERO MAYOR A 0.")
                    return
                monto_meta_minor = amount_to_minor(meta, DECIMALES_META)
            fecha_meta = (campo_fecha_meta.value or "").strip() or None
            if fecha_meta is not None:
                try:
                    datetime.strptime(fecha_meta, "%Y-%m-%d")
                except ValueError:
                    _falla("LA FECHA META DEBE TENER EL FORMATO AAAA-MM-DD.")
                    return
            try:
                savings_service.create_objetivo(nombre=nombre, monto_meta_minor=monto_meta_minor, fecha_meta=fecha_meta)
            except SavingsError as err:
                _falla(str(err).upper())
                return
            _tras_guardar(f"OBJETIVO '{nombre}' CREADO.")

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("NUEVO OBJETIVO DE AHORRO"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [campo_nombre, campo_meta.control, campo_fecha_meta, texto_error], tight=True, spacing=ESPACIADO,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("CREAR"), on_click=lambda e: _confirmar()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # --- VER MOVIMIENTOS / borrar ---

    def _eliminar_movimiento(movimiento: dict) -> None:
        """Ver docstring del módulo, "Borrar un movimiento"."""
        _cerrar_dialogo()  # la lista de movimientos

        def _borrar(eliminar_transaccion: bool) -> None:
            _cerrar_dialogo()
            try:
                resultado = savings_service.delete_movement(
                    movimiento["id"], eliminar_transaccion_vinculada=eliminar_transaccion,
                )
            except SavingsError as err:
                _error(str(err))
                return
            _ok(resultado.message.upper())
            _redibujar()

        if movimiento["transaccion_id"] is not None:
            contenido = ft.Text(
                "ESTE MOVIMIENTO GENERÓ (O ESTÁ VINCULADO A) UNA TRANSACCIÓN DEL REGISTRO. ¿BORRAR TAMBIÉN ESA TRANSACCIÓN?"
            )
            acciones = [
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.TextButton(content=ft.Text("NO, SOLO EL MOVIMIENTO"), on_click=lambda e: _borrar(False)),
                ft.ElevatedButton(content=ft.Text("SÍ, BORRAR LOS DOS"), on_click=lambda e: _borrar(True)),
            ]
        else:
            contenido = ft.Text("¿ELIMINAR ESTE MOVIMIENTO? NO SE PUEDE DESHACER.")
            acciones = [
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("ELIMINAR"), on_click=lambda e: _borrar(False)),
            ]
        page.show_dialog(ft.AlertDialog(
            modal=True, title=ft.Text("ELIMINAR MOVIMIENTO"), content=contenido, actions=acciones,
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _fila_movimiento(movimiento: dict, entrada: dict) -> ft.Control:
        tipo = movimiento["tipo"]
        texto_tipo = TEXTO_RETIRO if tipo == "venta" and movimiento["cantidad"] is None else TEXTO_MOVIMIENTO.get(tipo, tipo.upper())
        monto = amount_display(movimiento["monto_total_minor"], entrada["decimales"], entrada["simbolo"])
        comision = movimiento["comision_minor"] or 0
        objetivos = SEPARADOR_OBJETIVOS.join(
            f"{a['objetivo_nombre'].upper()} {a['porcentaje']:g}%" for a in movimiento["asignaciones"]
        )
        return ft.Row(
            [
                _texto(movimiento["fecha"], width=ANCHO_COL_FECHA),
                _texto(texto_tipo, color=COLOR_MOVIMIENTO.get(tipo, TEXT_PRIMARY), weight=PESO_MONTO, width=ANCHO_COL_TIPO),
                _texto(_fmt_unidades(movimiento["cantidad"]) if movimiento["cantidad"] is not None else "—", width=ANCHO_COL_CANTIDAD),
                _texto(monto, weight=PESO_MONTO, width=ANCHO_COL_MONTO),
                _texto(amount_display(comision, entrada["decimales"], entrada["simbolo"]) if comision else "—", width=ANCHO_COL_COMISION),
                _texto(objetivos or TEXTO_SIN_OBJETIVOS, color=TEXT_SECONDARY, width=ANCHO_COL_OBJETIVOS),
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE, icon_size=ICONO_BORRAR, icon_color=TEXT_NEGATIVO, tooltip="ELIMINAR",
                    on_click=lambda e: _eliminar_movimiento(movimiento),
                ),
            ],
            spacing=ESPACIADO_LINEAS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _ver_movimientos(entrada: dict) -> None:
        movimientos = savings_service.list_movimientos(activo_id=entrada["activo_id"])
        encabezado = ft.Row(
            [
                _texto("FECHA", TEXT_SECONDARY, PESO_HEADER, width=ANCHO_COL_FECHA),
                _texto("TIPO", TEXT_SECONDARY, PESO_HEADER, width=ANCHO_COL_TIPO),
                _texto("CANT.", TEXT_SECONDARY, PESO_HEADER, width=ANCHO_COL_CANTIDAD),
                _texto("MONTO", TEXT_SECONDARY, PESO_HEADER, width=ANCHO_COL_MONTO),
                _texto("COMISIÓN", TEXT_SECONDARY, PESO_HEADER, width=ANCHO_COL_COMISION),
                _texto("OBJETIVOS", TEXT_SECONDARY, PESO_HEADER, width=ANCHO_COL_OBJETIVOS),
            ],
            spacing=ESPACIADO_LINEAS,
        )
        filas = [_fila_movimiento(m, entrada) for m in movimientos] or [
            ft.Text("TODAVÍA NO HAY MOVIMIENTOS.", italic=True, color=TEXT_MUTED)
        ]
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(f"{entrada['activo'].upper()} — MOVIMIENTOS"),
            content=ft.Container(
                width=ANCHO_DIALOGO_MOVIMIENTOS, height=ALTO_LISTA_MOVIMIENTOS,
                content=ft.Column([encabezado, ft.Divider(height=1), *filas], spacing=ESPACIADO_LINEAS, scroll=ft.ScrollMode.AUTO),
            ),
            actions=[ft.TextButton(content=ft.Text("CERRAR"), on_click=_cerrar_dialogo)],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # RESUMEN
    # ------------------------------------------------------------

    def _fila_resumen(texto: str, valor: str) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.only(left=SANGRIA_RESUMEN),
            content=ft.Row(
                [_texto(texto, expand=True), _texto(valor, weight=PESO_MONTO)],
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _sublinea(texto: str) -> ft.Control:
        return ft.Container(
            padding=ft.Padding.only(left=2 * SANGRIA_RESUMEN),
            content=_texto(PREFIJO_SUBLINEA + texto, color=TEXT_SECONDARY, size=TypographyTokens.LABEL_SIZE),
        )

    def _contenido_resumen() -> list[ft.Control]:
        pills_vista = ft.Row(
            [
                ft.Text("VISTA:", size=TAMANIO_PILLS, color=TEXT_SECONDARY),
                *[_pill(texto, clave == ui["vista"], lambda c=clave: _cambiar_vista(c)) for clave, texto in VISTAS_RESUMEN],
            ],
            spacing=ESPACIO_PILLS,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        controles: list[ft.Control] = [pills_vista]
        if ui["vista"] == "instrumento":
            for tipo, entradas in datos["resumen"].items():
                controles.append(_titulo_seccion(TITULO_TIPO.get(tipo, tipo.upper())))
                for e in entradas:
                    nombre = f"{e['broker'].upper()} · {e['activo'].upper()}" if e["broker"] else e["activo"].upper()
                    controles += [_fila_resumen(nombre, _texto_tenencia(e)), _sublinea(_texto_objetivos(e["objetivos"]))]
            if not datos["resumen"]:
                controles.append(ft.Text("TODAVÍA NO HAY ACTIVOS — CREALOS DESDE SU PESTAÑA.", italic=True, color=TEXT_MUTED))
        else:
            por_objetivo = savings_service.get_resumen_por_objetivo()
            for objetivo, partes in por_objetivo.items():
                controles.append(_titulo_seccion(objetivo.upper()))
                for p in partes:
                    controles.append(_fila_resumen(f"{NOMBRE_TIPO.get(p['tipo'], '')} {p['activo'].upper()}", _texto_tenencia(p)))
            if not por_objetivo:
                controles.append(ft.Text("NINGÚN ACTIVO TIENE OBJETIVOS NI SALDO TODAVÍA.", italic=True, color=TEXT_MUTED))
        return controles

    # ------------------------------------------------------------
    # PESTAÑAS POR TIPO
    # ------------------------------------------------------------

    def _linea(etiqueta: str, valor: str) -> ft.Control:
        return ft.Row(
            [_texto(f"{etiqueta}:", color=TEXT_SECONDARY), _texto(valor, weight=PESO_MONTO, expand=True)],
            spacing=ESPACIADO_LINEAS,
        )

    def _tarjeta(entrada: dict) -> ft.Control:
        tipo = entrada["tipo"]
        titulo = entrada["activo"].upper() + (f" ({entrada['broker'].upper()})" if entrada["broker"] else "")
        if entrada["por_unidades"]:
            lineas = [_linea("CANTIDAD", _texto_tenencia(entrada, UNIDADES_TIPO.get(tipo, UNIDADES_DEFAULT)))]
            if entrada["precio_promedio_minor"] is not None:
                lineas.append(_linea(
                    "PRECIO PROMEDIO", amount_display(entrada["precio_promedio_minor"], entrada["decimales"], entrada["simbolo"]),
                ))
            botones = [
                _boton("+ COMPRA", lambda: _movimiento(entrada, "compra")),
                _boton("+ VENTA", lambda: _movimiento(entrada, "venta")),
                _boton("+ RENDIMIENTO", lambda: _movimiento(entrada, "rendimiento")),
            ]
        else:
            saldo = amount_display(entrada["saldo_minor"], entrada["decimales"], entrada["simbolo"])
            lineas = [_linea(ETIQUETA_SALDO.get(tipo, ETIQUETA_SALDO_DEFAULT), saldo)]
            botones = [
                _boton("+ RENDIMIENTO", lambda: _movimiento(entrada, "rendimiento")),
                _boton("+ APORTE", lambda: _movimiento(entrada, "aporte")),
                _boton("− RETIRO", lambda: _movimiento(entrada, "retiro")),
            ]
        lineas.append(_linea("OBJETIVOS", _texto_objetivos(entrada["objetivos"])))
        botones += [
            _boton("OBJETIVOS", lambda: _objetivos(entrada), TEXT_SECONDARY),
            _boton("VER MOVIMIENTOS", lambda: _ver_movimientos(entrada), TEXT_SECONDARY),
        ]
        return ft.Container(
            bgcolor=BG_SUPERFICIE,
            border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
            border_radius=RADIO_TARJETA,
            padding=PADDING_TARJETA,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            _texto(titulo, weight=TypographyTokens.SECTION_TITLE_WEIGHT, size=TypographyTokens.SECTION_TITLE_SIZE, expand=True),
                            _texto(entrada["moneda"], color=TEXT_SECONDARY),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    *lineas,
                    ft.Row(botones, spacing=0, wrap=True),
                ],
                spacing=ESPACIADO_LINEAS,
            ),
        )

    def _contenido_tipo(tab: str) -> list[ft.Control]:
        tipos = TIPOS_POR_TAB[tab]
        controles: list[ft.Control] = []
        if tab in BOTON_NUEVO:
            controles.append(ft.Row([_boton(BOTON_NUEVO[tab], lambda: _nuevo_activo(tipos[0]))]))
        entradas = [e for tipo in tipos for e in datos["resumen"].get(tipo, [])]
        controles += [_tarjeta(e) for e in entradas] or [
            ft.Text(f"NO HAY ACTIVOS EN {dict(TABS)[tab]} TODAVÍA.", italic=True, color=TEXT_MUTED)
        ]
        return controles

    # ------------------------------------------------------------
    # ARMADO
    # ------------------------------------------------------------

    def _cambiar_tab(tab: str) -> None:
        if tab == ui["tab"]:
            sin_auto_update()
            return
        ui["tab"] = tab
        _redibujar()

    def _cambiar_vista(vista: str) -> None:
        if vista == ui["vista"]:
            sin_auto_update()
            return
        ui["vista"] = vista
        _redibujar()

    def _cabecera() -> ft.Control:
        controles: list[ft.Control] = []
        if on_volver is not None:
            controles.append(ft.IconButton(
                icon=ft.Icons.ARROW_BACK, icon_color=TEXT_SECONDARY, tooltip="VOLVER", on_click=lambda e: on_volver(),
            ))
        controles += [
            ft.Text(
                "AHORROS E INVERSIONES", size=TypographyTokens.PAGE_TITLE_SIZE,
                weight=TypographyTokens.PAGE_TITLE_WEIGHT, color=TEXT_PRIMARY,
            ),
            ft.Container(expand=True),
            _boton("+ NUEVO OBJETIVO", _nuevo_objetivo),
        ]
        return ft.Row(controles, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _redibujar() -> None:
        try:
            datos["resumen"] = savings_service.get_resumen_por_tipo()
        except SavingsError as err:
            _error(str(err))
        tabs = ft.Row(
            [_pill(texto, clave == ui["tab"], lambda c=clave: _cambiar_tab(c)) for clave, texto in TABS],
            spacing=ESPACIO_PILLS,
            wrap=True,
        )
        contenido = _contenido_resumen() if ui["tab"] == "resumen" else _contenido_tipo(ui["tab"])
        raiz.controls = [pantalla_planilla([_cabecera(), tabs, *contenido])]
        page.update()

    _redibujar()
    return raiz
