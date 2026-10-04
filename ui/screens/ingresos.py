"""
DeltaBalance — ui/screens/ingresos.py

Ingresos esperados de cada mes (services/ingresos_service.py), con el
mismo formato visual que el Registro: barra de título (← volver, búsqueda,
mes, pills de moneda y COPIAR RECURRENTES) → tabla (TablaPlanilla,
ui/components/tabla_planilla.py) con fila de alta, edición inline, barra
flotante y una fila TOTAL al final.

Reglas de arquitectura: solo IngresosService/AccountsService — nunca
repositories/ ni db/ directo (CLAUDE.md §2/§3). ui/app.py pasa el service
con su nombre viejo (IngresosProyectadosService, reexport del mismo
IngresosService).

--- Estado propio ---

Período, moneda elegida y el BORRADOR de la fila de alta viven en un
almacén por página (_ESTADOS_UI), igual que Deudas: sobreviven a que
ui/app.py reconstruya la pantalla. Búsqueda, filtros, orden, selección y
anchos de columna (PREF_ANCHOS_COLUMNAS) los guarda TablaPlanilla.

--- Moneda ---

Las pills de la barra de título eligen la moneda que se ve: la tabla
muestra solo los ingresos de esa moneda y el TOTAL es el de esa moneda
(los totales nunca mezclan monedas). Pills: ARS y USD (MONEDAS_TOGGLE)
más cualquier otra moneda con ingresos en el mes. Al elegir una, la fila
de alta pasa a esa moneda; un alta en otra moneda cambia la pill sola, así
la fila nueva se ve.

La columna Moneda es un botón que alterna ARS ↔ USD (SelectorCiclico): en
la fila de alta elige la moneda; en una fila ya cargada la cambia y guarda,
conservando el importe mostrado (si la moneda nueva tiene otros decimales
se reescala, mismo criterio que Deudas) — la fila pasa a verse en la otra
pill. Una fila en otra moneda (ej. migrada) suma la suya a las opciones.

--- Tabla ---

Concepto, Estimado y Real se editan inline (CLAUDE.md §10; montos con
CampoMonto y fórmulas, §9) vía IngresosService.update(). Estimado en verde;
Real en verde si es > 0, "—" si todavía no se cobró. Recurrente: checkbox
que guarda al tocarlo.

--- Fila de alta ---

Concepto, Estimado y Real (CampoMonto: vacío = 0; hace falta al menos
uno), Moneda (SelectorCiclico) y Recurrente (checkbox). Enter nunca guarda
la fila salvo con el foco en el ✓: Concepto → Estimado → Real → ✓ (Moneda
y Recurrente se alcanzan con Tab; Enter en el botón de Moneda lo
cambiaría, así que la cadena lo saltea).

--- Barra flotante ---

Solo Eliminar (IngresosService.delete(): un ingreso no tiene dependencias
con estado propio, CLAUDE.md §4).

--- TOTAL ---

Al final de la tabla, una fila TOTAL (FilaPie, mismo estilo que SALDO
ANTERIOR) con el estimado y el real del mes en la moneda elegida: el total
que IngresosService.list_by_month() agrega al final del listado. Es el del
mes entero, aunque haya una búsqueda o un filtro activo.

--- COPIAR RECURRENTES ---

Copia los ingresos recurrentes del mes anterior al mes que se está viendo
(IngresosService.copy_recurrentes(): real en 0, sin duplicar conceptos que
ya estén) y avisa cuántos copió. En la barra es solo el ícono de copiar,
sin texto (con texto no entraba en el ancho disponible); el nombre va en el
tooltip.
"""

from datetime import date
from typing import Any, Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.ingresos_service import CLAVE_TOTAL, IngresoError, IngresosService
from ui.components.campo_monto import CampoMonto
from ui.components.tabla_planilla import (
    ANCHO_BORDE,
    ESPACIO_DOT,
    PILL_ALTURA,
    PILL_PADDING_H,
    PILL_RADIO,
    Columna,
    FilaAlta,
    FilaPie,
    SelectorCiclico,
    TablaPlanilla,
    barra_titulo,
    estilo_campo,
    mostrar_mensaje,
    pantalla_planilla,
    sin_auto_update,
    sin_borde,
)
from ui.components.tipo_valor import numero
from ui.theme.tabla_tokens import (
    BORDER_DEFAULT,
    BTN_COMPARTIR,
    PESO_HEADER,
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_POSITIVO,
    TEXT_SECONDARY,
    TEXT_SOBRE_BOTON,
)
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---

COLUMNAS = [
    Columna("concepto", "CONCEPTO", 200),
    Columna("estimado", "ESTIMADO", 130, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
    Columna("real", "REAL", 130, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
    Columna("moneda", "MONEDA", 70, redimensionable=False),
    Columna("recurrente", "RECURRENTE", 90, redimensionable=False),
]
CLAVE_TABLA = "ingresos"
PREF_ANCHOS_COLUMNAS = "ingresos_anchos_columnas"
MONEDA_DEFAULT = "ARS"
# Opciones del botón de Moneda y pills que se muestran siempre (ver docstring, "Moneda").
MONEDAS_TOGGLE = ("ARS", "USD")
DECIMALES_DEFAULT = 2

TEXTO_SIN_REAL = "—"
TEXTO_TOTAL = "TOTAL"
TEXTO_RECURRENTE = {True: "SÍ", False: "NO"}
TOOLTIP_RECURRENTE = "SE COPIA AL MES SIGUIENTE CON COPIAR RECURRENTES"
HINT_CONCEPTO_ALTA = "EJ: BECA DOCTORAL"
HINT_ESTIMADO_ALTA = "ESTIMADO"
HINT_REAL_ALTA = "REAL"

# Pills de moneda y botón COPIAR RECURRENTES: mismas medidas que las pills de barra_resumen().
TAMANIO_PILLS = TypographyTokens.REGISTRO_FONT_SALDO_BAR
ESPACIO_PILLS = ESPACIO_DOT
ICONO_PILL = 16


# ============================================================
# ESTADO PROPIO POR PÁGINA (ver docstring del módulo)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia(moneda: Optional[str] = None) -> dict:
    return {"concepto": "", "estimado": "", "real": "", "moneda": moneda, "recurrente": False}


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {"mes": hoy.month, "anio": hoy.year, "moneda": MONEDA_DEFAULT, "alta": _alta_vacia()}
        _ESTADOS_UI[id(page)] = ui
    return ui


# ============================================================
# HELPERS
# ============================================================

def _texto_monto(monto_minor: int, decimales: int, simbolo: str) -> str:
    """'+ $1,000.00' — un ingreso siempre suma."""
    return f"+ {amount_display(monto_minor, decimales, simbolo)}"


def _reescalar(monto_minor: int, decimales_origen: int, decimales_destino: int) -> int:
    """Mismo importe mostrado en una moneda con otros decimales (mismo criterio que Deudas)."""
    diferencia = decimales_destino - decimales_origen
    return monto_minor * 10 ** diferencia if diferencia >= 0 else round(monto_minor / 10 ** -diferencia)


def _mes_anterior(mes: int, anio: int) -> tuple[int, int]:
    return (12, anio - 1) if mes == 1 else (mes - 1, anio)


def _orden_monedas(codigos: set[str]) -> list[str]:
    """ARS primero, después alfabético (mismo orden que las pills de barra_resumen())."""
    return sorted(codigos, key=lambda codigo: (codigo != MONEDA_DEFAULT, codigo))


def _valor_alta(campo: CampoMonto) -> Optional[float]:
    """Monto tipeado en el alta: vacío = 0; None si no es un número (o una fórmula) válido."""
    if not campo.confirmar():  # resuelve una fórmula pendiente (y marca el borde si no es válida)
        return None
    if not campo.texto.strip():
        return 0.0
    return numero(campo.texto)


def _pill(
    texto: Optional[str], activa: bool, on_click: Callable[[], None],
    tooltip: Optional[str] = None, icono: Optional[str] = None,
) -> ft.Control:
    """
    Pill de la barra de título (estilo selector de moneda de barra_resumen()):
    la activa, rellena. Sin texto, solo el ícono (el nombre va en el tooltip).
    """
    color = TEXT_SOBRE_BOTON if activa else TEXT_SECONDARY
    contenido: list[ft.Control] = []
    if icono is not None:
        contenido.append(ft.Icon(icono, size=ICONO_PILL, color=color))
    if texto:
        contenido.append(ft.Text(texto, size=TAMANIO_PILLS, weight=PESO_HEADER, color=color))
    return ft.Container(
        height=PILL_ALTURA,
        padding=ft.Padding.symmetric(horizontal=PILL_PADDING_H),
        border_radius=PILL_RADIO,
        bgcolor=BTN_COMPARTIR if activa else None,
        border=None if activa else ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
        alignment=ft.Alignment.CENTER,
        tooltip=tooltip,
        on_click=lambda e: on_click(),
        content=ft.Row(contenido, spacing=ESPACIO_PILLS, tight=True),
    )


def _con_volver(titulo: ft.Control, on_volver: Optional[Callable[[], None]]) -> ft.Control:
    """← a la izquierda de la barra de título (si la pantalla tiene adónde volver)."""
    if on_volver is None:
        return titulo
    titulo.expand = True
    return ft.Row(
        [
            ft.IconButton(
                icon=ft.Icons.ARROW_BACK, icon_color=TEXT_SECONDARY, tooltip="VOLVER", on_click=lambda e: on_volver(),
            ),
            titulo,
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


# ============================================================
# PANTALLA
# ============================================================

def build(
    page: ft.Page,
    ingresos_service: IngresosService,
    accounts_service: AccountsService,
    on_volver: Optional[Callable[[], None]] = None,
) -> ft.Control:
    ui = _estado_ui(page)

    def _mostrar_error(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje, es_error=True)

    monedas_por_codigo = {m["codigo"]: dict(m) for m in accounts_service.list_currencies()}
    monedas_toggle = [codigo for codigo in MONEDAS_TOGGLE if codigo in monedas_por_codigo] or [MONEDA_DEFAULT]

    def _opciones_moneda(actual: Optional[str]) -> list[tuple[str, str]]:
        codigos = list(monedas_toggle)
        if actual and actual not in codigos:
            codigos.append(actual)
        return [(codigo, codigo) for codigo in codigos]

    def _decimales(codigo: Optional[str]) -> int:
        return monedas_por_codigo.get(codigo or "", {}).get("decimales", DECIMALES_DEFAULT)

    # ------------------------------------------------------------
    # DATOS
    # ------------------------------------------------------------

    # Lo que deja cada carga: el total por moneda y en qué monedas hay ingresos este mes.
    datos: dict[str, Any] = {"totales": {}, "monedas_mes": set()}

    def _cargar_filas() -> list[dict]:
        listado = ingresos_service.list_by_month(ui["mes"], ui["anio"])
        filas = [i for i in listado if not i.get(CLAVE_TOTAL)]
        datos["totales"] = {t["currency_code"]: t for t in listado if t.get(CLAVE_TOTAL)}
        datos["monedas_mes"] = {i["currency_code"] for i in filas}
        return [i for i in filas if i["currency_code"] == ui["moneda"]]

    def _texto_estimado(i: dict) -> str:
        return _texto_monto(i["monto_estimado_minor"], i["decimales"], i["currency_symbol"] or "")

    def _texto_real(i: dict) -> str:
        if not i["monto_real_minor"]:
            return TEXTO_SIN_REAL
        return _texto_monto(i["monto_real_minor"], i["decimales"], i["currency_symbol"] or "")

    def _valor_columna(i: dict, columna: str) -> str:
        """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
        if columna == "concepto":
            return i["concepto"] or ""
        if columna == "estimado":
            return _texto_estimado(i)
        if columna == "real":
            return _texto_real(i)
        if columna == "moneda":
            return i["currency_code"] or ""
        return TEXTO_RECURRENTE[bool(i["es_recurrente"])]

    def _clave_orden(i: dict, columna: str) -> Any:
        if columna == "estimado":
            return i["monto_estimado_minor"] / (10 ** i["decimales"])
        if columna == "real":
            return i["monto_real_minor"] / (10 ** i["decimales"])
        return _valor_columna(i, columna).lower()

    def _firma(i: dict) -> tuple:
        return (
            i["concepto"], i["monto_estimado_minor"], i["monto_real_minor"], i["moneda_id"], i["currency_code"],
            i["currency_symbol"], i["decimales"], i["es_recurrente"], i["notas"],
        )

    # ------------------------------------------------------------
    # PILLS DE MONEDA Y COPIAR RECURRENTES (barra de título)
    # ------------------------------------------------------------

    contenedor_pills = ft.Container()

    def _dibujar_pills() -> None:
        monedas = _orden_monedas(set(monedas_toggle) | datos["monedas_mes"] | {ui["moneda"]})
        contenedor_pills.content = ft.Row(
            [
                _pill(codigo, codigo == ui["moneda"], lambda c=codigo: _elegir_moneda(c), tooltip=f"VER LOS INGRESOS EN {codigo}")
                for codigo in monedas
            ],
            spacing=ESPACIO_PILLS,
        )

    def _elegir_moneda(codigo: str) -> None:
        if codigo == ui["moneda"]:
            sin_auto_update()
            return
        _guardar_borrador_alta()
        ui["moneda"] = codigo
        ui["alta"]["moneda"] = codigo
        # Fila de alta en la moneda nueva + filas recargadas (al_recargar re-dibuja las pills).
        tabla.alta_ok()

    def _al_recargar() -> list[ft.Control]:
        _dibujar_pills()
        return [contenedor_pills]

    def _copiar_recurrentes() -> None:
        mes, anio = _mes_anterior(ui["mes"], ui["anio"])
        try:
            copiados = ingresos_service.copy_recurrentes(mes, anio, ui["mes"], ui["anio"])
        except IngresoError as err:
            _mostrar_error(str(err))
            return
        tabla.recargar()
        if copiados == 0:
            tabla.mostrar_ok(f"NO HABÍA INGRESOS RECURRENTES NUEVOS PARA COPIAR DE {mes:02d}/{anio}.")
        elif copiados == 1:
            tabla.mostrar_ok("SE COPIÓ 1 INGRESO RECURRENTE.")
        else:
            tabla.mostrar_ok(f"SE COPIARON {copiados} INGRESOS RECURRENTES.")

    # ------------------------------------------------------------
    # FILA TOTAL (FilaPie)
    # ------------------------------------------------------------

    def _filas_pie() -> list[FilaPie]:
        total = datos["totales"].get(ui["moneda"])
        if total is None:
            return []
        simbolo = total["currency_symbol"] or ""
        real = total["total_real_minor"]
        return [FilaPie(
            textos={
                "concepto": TEXTO_TOTAL,
                "estimado": _texto_monto(total["total_estimado_minor"], total["decimales"], simbolo),
                "real": _texto_monto(real, total["decimales"], simbolo) if real else TEXTO_SIN_REAL,
                "moneda": ui["moneda"],
            },
            positiva=True,
            valores_filtro={"moneda": ui["moneda"]},
            tooltip=f"TOTAL DEL MES EN {ui['moneda']}",
        )]

    # ------------------------------------------------------------
    # FILA DE ALTA
    # ------------------------------------------------------------

    alta_refs: dict[str, Any] = {}

    def _guardar_borrador_alta() -> None:
        if not alta_refs:
            return
        ui["alta"] = {
            "concepto": alta_refs["concepto"].value or "",
            "estimado": alta_refs["estimado"].texto,
            "real": alta_refs["real"].texto,
            "moneda": alta_refs["moneda"].valor,
            "recurrente": bool(alta_refs["recurrente"].value),
        }

    def _on_cambio_borrador(e=None) -> None:
        # Solo guarda el borrador: el cambio ya está en pantalla.
        _guardar_borrador_alta()
        sin_auto_update()

    def _campo_monto_alta(valor: str, decimales: int, hint: str, on_avanzar: Callable[[], None]) -> CampoMonto:
        # persistir_formula=True: el campo recuerda la fórmula mientras la
        # fila no se guarde. on_confirmar solo guarda el borrador — la fila
        # entera confirma junta (✓).
        campo = CampoMonto(
            tabla.pagina_alta,
            on_confirmar=lambda monto_minor: _guardar_borrador_alta(),
            persistir_formula=True,
            decimales=decimales,
            hint_text=hint,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            on_avanzar=on_avanzar,
        )
        sin_borde(campo.control)
        campo.control.text_align = ft.TextAlign.RIGHT
        # Borrador en cada tecla, no solo al confirmar: si la pantalla se
        # reconstruye antes del blur, el monto tipeado no se pierde.
        campo.control.on_change = _on_cambio_borrador
        if valor:
            campo.control.value = valor
        return campo

    def _construir_alta() -> FilaAlta:
        borrador = ui["alta"]
        moneda_inicial = borrador["moneda"] or ui["moneda"]

        campo_concepto = ft.TextField(
            value=borrador["concepto"], hint_text=HINT_CONCEPTO_ALTA, autofocus=True,
            text_align=ft.TextAlign.CENTER, on_change=_on_cambio_borrador, **estilo_campo(),
        )
        # Real es el último campo de la cadena: Enter lleva el foco al ✓ sin guardar.
        campo_real = _campo_monto_alta(
            borrador["real"], _decimales(moneda_inicial), HINT_REAL_ALTA, tabla.enfocar_confirmar,
        )
        campo_estimado = _campo_monto_alta(
            borrador["estimado"], _decimales(moneda_inicial), HINT_ESTIMADO_ALTA,
            lambda: tabla.enfocar(campo_real.control),
        )
        selector_moneda = tabla.selector_alta(
            _opciones_moneda(moneda_inicial), moneda_inicial, lambda codigo: _guardar_borrador_alta(), "MONEDA",
        )
        checkbox_recurrente = ft.Checkbox(
            value=borrador["recurrente"], active_color=TEXT_ACCENT, tooltip=TOOLTIP_RECURRENTE,
            on_change=_on_cambio_borrador,
        )
        boton_confirmar = tabla.boton_confirmar_alta("AGREGAR INGRESO", lambda: _confirmar_alta())
        alta_refs.update(
            concepto=campo_concepto, estimado=campo_estimado, real=campo_real, moneda=selector_moneda,
            recurrente=checkbox_recurrente, boton=boton_confirmar,
        )
        campo_concepto.on_submit = lambda e: tabla.enfocar(campo_estimado.control)

        return FilaAlta(
            celdas={
                "concepto": campo_concepto,
                "estimado": campo_estimado.control,
                "real": campo_real.control,
                "moneda": selector_moneda.control,
                "recurrente": checkbox_recurrente,
            },
            boton=boton_confirmar,
            foco=campo_concepto,
        )

    def _confirmar_alta() -> None:
        # Deshabilitar ANTES de procesar: un doble Enter/click no dispara dos altas.
        boton = alta_refs["boton"]
        if boton.disabled:
            return
        boton.disabled = True
        tabla.refrescar(boton)
        try:
            _procesar_alta()
        finally:
            boton.disabled = False
            tabla.refrescar(boton)

    def _procesar_alta() -> None:
        _guardar_borrador_alta()
        concepto = (alta_refs["concepto"].value or "").strip()
        if not concepto:
            _mostrar_error("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
            return
        estimado, real = _valor_alta(alta_refs["estimado"]), _valor_alta(alta_refs["real"])
        if estimado is None or real is None:
            _mostrar_error("EL MONTO NO ES UN NÚMERO VÁLIDO.")
            return
        if not estimado and not real:
            _mostrar_error("INGRESÁ EL ESTIMADO O EL REAL.")
            return
        codigo = alta_refs["moneda"].valor
        moneda = monedas_por_codigo.get(codigo)
        if moneda is None:
            _mostrar_error("COMPLETÁ LA MONEDA.")
            return

        escala = 10 ** moneda["decimales"]
        try:
            resultado = ingresos_service.create(
                concepto=concepto,
                monto_estimado_minor=round(estimado * escala),
                monto_real_minor=round(real * escala),
                moneda_id=moneda["id"],
                mes=ui["mes"],
                anio=ui["anio"],
                es_recurrente=bool(alta_refs["recurrente"].value),
            )
        except IngresoError as err:
            _mostrar_error(str(err))  # la fila queda como estaba para corregir
            return

        ui["alta"] = _alta_vacia(codigo)
        ui["moneda"] = codigo  # la fila nueva se ve aunque sea de otra moneda
        tabla.alta_ok()
        tabla.mostrar_ok(resultado.message)

    # ------------------------------------------------------------
    # FILAS DE DATOS: celdas editables inline (CLAUDE.md §10)
    # ------------------------------------------------------------

    def _celda_moneda(i: dict) -> ft.Control:
        """Botón ARS ↔ USD que guarda al tocarlo (ver docstring, "Moneda")."""
        envoltorio = ft.Container()

        def _cambiar(codigo: str) -> None:
            moneda = monedas_por_codigo[codigo]
            try:
                ingresos_service.update(
                    i["id"],
                    moneda_id=moneda["id"],
                    monto_estimado_minor=_reescalar(i["monto_estimado_minor"], i["decimales"], moneda["decimales"]),
                    monto_real_minor=_reescalar(i["monto_real_minor"], i["decimales"], moneda["decimales"]),
                )
            except IngresoError as err:
                _dibujar_selector()  # vuelve a la moneda guardada
                tabla.refrescar(envoltorio)
                _mostrar_error(str(err))
                return
            tabla.recargar()
            tabla.mostrar_ok(f"'{i['concepto']}' PASÓ A {codigo}.")

        def _dibujar_selector() -> None:
            opciones = _opciones_moneda(i["currency_code"])
            # pagina_alta: parchea solo el botón (si la fila ya se reconstruyó, nada).
            selector = SelectorCiclico(
                tabla.pagina_alta, opciones, i["currency_code"], _cambiar, "MONEDA",
                colores={codigo: TEXT_SECONDARY for codigo, _ in opciones},
            )
            envoltorio.content = selector.control

        _dibujar_selector()
        return tabla.celda_lectura("moneda", envoltorio)

    def _celda_recurrente(i: dict) -> ft.Control:
        def _cambiar(e: ft.ControlEvent) -> None:
            try:
                ingresos_service.update(i["id"], es_recurrente=bool(e.control.value))
            except IngresoError as err:
                e.control.value = not e.control.value
                tabla.refrescar(e.control)
                _mostrar_error(str(err))
                return
            tabla.recargar()

        return tabla.celda_lectura(
            "recurrente",
            ft.Checkbox(
                value=bool(i["es_recurrente"]), active_color=TEXT_ACCENT, tooltip=TOOLTIP_RECURRENTE,
                on_change=_cambiar,
            ),
        )

    def _construir_celdas(i: dict) -> dict[str, ft.Control]:
        def _guardar(**kwargs: Any) -> str:
            # IngresoError (y ValueError) suben a la celda, que los muestra y vuelve al valor anterior.
            return ingresos_service.update(i["id"], **kwargs).message

        return {
            "concepto": tabla.celda_texto(i, "concepto", i["concepto"] or "", lambda nuevo: _guardar(concepto=nuevo)),
            "estimado": tabla.celda_monto(
                i, "estimado", _texto_estimado(i), TEXT_POSITIVO, i["monto_estimado_minor"], i["decimales"],
                lambda monto_minor: _guardar(monto_estimado_minor=monto_minor),
            ),
            "real": tabla.celda_monto(
                i, "real", _texto_real(i), TEXT_POSITIVO if i["monto_real_minor"] > 0 else TEXT_MUTED,
                i["monto_real_minor"], i["decimales"], lambda monto_minor: _guardar(monto_real_minor=monto_minor),
            ),
            "moneda": _celda_moneda(i),
            "recurrente": _celda_recurrente(i),
        }

    # ------------------------------------------------------------
    # BARRA FLOTANTE: eliminar
    # ------------------------------------------------------------

    def _eliminar(filas: list[dict]) -> tuple[str, bool]:
        errores = []
        for i in filas:
            try:
                ingresos_service.delete(i["id"])
            except IngresoError as err:
                errores.append(f"{i['concepto']}: {err}")
        eliminados = len(filas) - len(errores)
        if errores:
            return f"{eliminados} ELIMINADO(S), {len(errores)} CON ERROR: " + " | ".join(errores), True
        return f"{eliminados} INGRESO(S) ELIMINADO(S).", False

    # ------------------------------------------------------------
    # ARMADO
    # ------------------------------------------------------------

    tabla = TablaPlanilla(
        page,
        clave=CLAVE_TABLA,
        columnas=COLUMNAS,
        pref_anchos=PREF_ANCHOS_COLUMNAS,
        cargar_filas=_cargar_filas,
        construir_celdas=_construir_celdas,
        construir_alta=_construir_alta,
        firma=_firma,
        valor_columna=_valor_columna,
        clave_orden=_clave_orden,
        texto_busqueda=lambda i: f"{i['concepto'] or ''} {i['notas'] or ''}",
        on_eliminar=_eliminar,
        al_recargar=_al_recargar,
        filas_pie=_filas_pie,
        errores_esperados=(IngresoError,),
        texto_vacio="NO HAY INGRESOS EN ESTA MONEDA PARA ESTE MES.",
    )
    control_tabla = tabla.construir()
    _dibujar_pills()

    # Solo ícono: con texto no entraba en la barra (ver docstring, "COPIAR RECURRENTES").
    boton_copiar = _pill(
        None, False, _copiar_recurrentes,
        tooltip="COPIAR RECURRENTES: COPIA LOS INGRESOS RECURRENTES DEL MES ANTERIOR A ESTE MES",
        icono=ft.Icons.CONTENT_COPY,
    )
    titulo = barra_titulo(
        page, "INGRESOS", tabla, ui, lambda: tabla.recargar(limpiar_seleccion=True), "BUSCAR EN INGRESOS…",
        acciones=[contenedor_pills, boton_copiar],
    )
    # Scroll propio (el Registro lo tiene por la Column del dashboard).
    return ft.Column(
        [pantalla_planilla([_con_volver(titulo, on_volver), control_tabla])],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
