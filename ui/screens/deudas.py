"""
DeltaBalance — ui/screens/deudas.py

Deudas informales como LIBRO DE MOVIMIENTOS (services/debts_service.py,
docs/DATA_MODEL_DECISIONS.md sección 22): barra de título (búsqueda +
período) → barra "saldo neto por persona" → tabla (TablaPlanilla,
ui/components/tabla_planilla.py) con fila de alta, edición inline y barra
flotante. Mismo formato visual que el Registro.

Cada fila es un monto con dirección: ME DEBE (a_favor: le prestaste, pagaste
algo por esa persona) o DEBO (en_contra: te prestaron, o te devolvieron /
pagaron). El saldo con una persona es la suma con signo de sus filas. No
hay pendiente, estado ni pagos aparte: un pago es una fila más, de tipo
opuesto — por eso no hay "Registrar pago" ni "Marcar incobrable".

Reglas de arquitectura: solo DebtsService/AccountsService/SnapshotsService —
nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).

--- Estado propio ---

Período, moneda de la barra de saldo y el BORRADOR de la fila de alta viven
en un almacén por página (_ESTADOS_UI), igual que el Registro. El estado de
la tabla (búsqueda, filtros, orden, selección, anchos — clave
"deudas_anchos_columnas") lo guarda TablaPlanilla.

--- Barra de saldo ---

DebtsService.summary_by_person(hasta_fecha=último día del mes elegido): un
chip por persona con saldo distinto de cero en la moneda elegida — TE DEBE
en verde (positivo), DEBÉS en rojo (negativo). ↻ recalcula los snapshots
(ui/components/saldo_anterior.py).

--- Tabla ---

Las filas cuya fecha cae en el mes elegido (DebtsService.list_by_period()).
Edición inline (CLAUDE.md §10) de todas las celdas vía DebtsService.update():
Persona, Concepto, Tipo, Monto, Moneda y Fecha. Monto se edita con el signo
que se ve — + ME DEBE, − DEBO —: un negativo es válido y da vuelta el tipo
(el service guarda el monto en positivo y la dirección en el tipo). Cambiar
la moneda conserva el importe mostrado (si la moneda nueva tiene otros
decimales, se reescala el monto, mismo criterio que el Registro). Las
notas no tienen columna: entran en la búsqueda.

--- Fila de alta ---

Persona, Concepto, Tipo (ME DEBE / DEBO), Monto (CampoMonto, con fórmulas;
negativo = el tipo contrario), Moneda y Fecha. Tipo y Moneda son
SelectorCiclico (TablaPlanilla.selector_alta()): en 90 / 70 px un Dropdown
no entra (su flecha sola ocupa ~40 px). Enter nunca guarda la fila salvo con
el foco en el ✓: Persona → Concepto → Monto → Fecha (Tipo y Moneda son
botones: Enter los cambiaría, así que la cadena los saltea; con Tab se llega
igual); en Fecha, el último campo, Enter o Tab llevan el foco al ✓ sin
activarlo, y ahí Enter o un click confirman.

--- Barra flotante ---

Solo Eliminar (DebtsService.delete(): una fila no tiene dependencias con
estado propio, CLAUDE.md §4).

--- Saldo anterior ---

Al final de la tabla, una fila "SALDO ANTERIOR" (FilaPie) por (persona,
moneda) con saldo al cierre del mes anterior distinto de cero
(SnapshotsService.get_saldos_anteriores_deudas(): snapshot, o en vivo si
falta). Respeta los filtros de Persona y Moneda. Se cachea por período.
"""

import calendar
from datetime import date, datetime
from typing import Any, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.debts_service import DebtError, DebtsService
from services.snapshots_service import SnapshotsService
from ui.components.campo_monto import CampoMonto
from ui.components.saldo_anterior import TEXTO_SALDO_ANTERIOR, TOOLTIP_SALDO_ANTERIOR, boton_recalcular
from ui.components.tabla_planilla import (
    ChipResumen,
    Columna,
    FilaAlta,
    FilaPie,
    TablaPlanilla,
    barra_resumen,
    barra_titulo,
    estilo_campo,
    mostrar_mensaje,
    pantalla_planilla,
    sin_auto_update,
    sin_borde,
)
from ui.components.tipo_valor import numero
from ui.theme.tabla_tokens import TEXT_NEGATIVO, TEXT_POSITIVO, TEXT_SECONDARY
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---

COLUMNAS = [
    Columna("persona", "PERSONA", 150),
    Columna("concepto", "CONCEPTO", 200),
    Columna("tipo", "TIPO", 90, redimensionable=False),
    Columna("monto", "MONTO", 120, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
    Columna("moneda", "MONEDA", 70, redimensionable=False),
    Columna("fecha", "FECHA", 100, redimensionable=False),
]
PREF_ANCHOS_COLUMNAS = "deudas_anchos_columnas"
MONEDA_DEFAULT = "ARS"
DECIMALES_DEFAULT = 2
HINT_MONTO_ALTA = "± MONTO"

ETIQUETA_TIPO = {"a_favor": "ME DEBE", "en_contra": "DEBO"}
COLORES_TIPO = {"a_favor": TEXT_POSITIVO, "en_contra": TEXT_NEGATIVO}


# ============================================================
# ESTADO PROPIO POR PÁGINA (ver docstring del módulo)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia(moneda: Optional[str] = None) -> dict:
    return {
        "persona": "", "concepto": "", "tipo": "a_favor", "monto": "",
        "fecha": date.today().isoformat(), "moneda": moneda,
    }


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {"mes": hoy.month, "anio": hoy.year, "moneda_saldo": MONEDA_DEFAULT, "alta": _alta_vacia()}
        _ESTADOS_UI[id(page)] = ui
    return ui


def build(
    page: ft.Page,
    debts_service: DebtsService,
    accounts_service: AccountsService,
    snapshots_service: SnapshotsService,
) -> ft.Control:
    ui = _estado_ui(page)

    def _mostrar_error(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje)

    monedas_por_codigo = {m["codigo"]: dict(m) for m in accounts_service.list_currencies()}
    codigos_moneda = list(monedas_por_codigo) or [MONEDA_DEFAULT]

    def _decimales(codigo: Optional[str]) -> int:
        return monedas_por_codigo.get(codigo or "", {}).get("decimales", DECIMALES_DEFAULT)

    # ------------------------------------------------------------
    # DATOS
    # ------------------------------------------------------------

    def _cargar_filas() -> list[dict]:
        return [dict(d) for d in debts_service.list_by_period(ui["mes"], ui["anio"])]  # CLAUDE.md §11

    def _con_signo(d: dict) -> int:
        """+ ME DEBE (a_favor), − DEBO (en_contra): el signo con el que se muestra y se edita."""
        return d["monto_minor"] if d["tipo"] == "a_favor" else -d["monto_minor"]

    def _monto_texto(d: dict) -> str:
        signo = "+" if d["tipo"] == "a_favor" else "-"
        return f"{signo} {amount_display(d['monto_minor'], d['decimales'], d['currency_symbol'] or '')}"

    def _valor_columna(d: dict, columna: str) -> str:
        """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
        if columna == "persona":
            return d["entidad_persona"] or ""
        if columna == "concepto":
            return d["concepto"] or ""
        if columna == "tipo":
            return ETIQUETA_TIPO.get(d["tipo"], d["tipo"])
        if columna == "monto":
            return _monto_texto(d)
        if columna == "moneda":
            return d["currency_code"] or ""
        return d["fecha"] or ""

    def _clave_orden(d: dict, columna: str) -> Any:
        if columna == "monto":
            return _con_signo(d) / (10 ** d["decimales"])
        return _valor_columna(d, columna).lower()

    def _firma(d: dict) -> tuple:
        return (
            d["entidad_persona"], d["concepto"], d["notas"], d["tipo"], d["monto_minor"], d["moneda_id"],
            d["currency_code"], d["currency_symbol"], d["decimales"], d["fecha"],
        )

    # ------------------------------------------------------------
    # BARRA DE SALDO NETO POR PERSONA
    # ------------------------------------------------------------

    contenedor_saldos = ft.Container()

    def _fin_de_mes() -> str:
        ultimo = calendar.monthrange(ui["anio"], ui["mes"])[1]
        return f"{ui['anio']:04d}-{ui['mes']:02d}-{ultimo:02d}"

    def _barra_saldos() -> ft.Control:
        codigo_sel = ui["moneda_saldo"]
        resumen = debts_service.summary_by_person(hasta_fecha=_fin_de_mes())
        chips: list[ChipResumen] = []
        for entrada in resumen:
            saldo = entrada["saldo_minor"]
            if entrada["moneda_codigo"] != codigo_sel or not saldo:
                continue
            color = TEXT_POSITIVO if saldo > 0 else TEXT_NEGATIVO
            monto = amount_display(abs(saldo), entrada["decimales"], entrada["moneda_simbolo"])
            chips.append(ChipResumen(
                color=color, nombre=entrada["entidad_persona"],
                monto=f"{'TE DEBE' if saldo > 0 else 'DEBÉS'} {monto}", color_monto=color, moneda=codigo_sel,
            ))
        monedas = [entrada["moneda_codigo"] for entrada in resumen if entrada["saldo_minor"]]
        return barra_resumen(
            "SALDO NETO POR PERSONA", ft.Icons.HANDSHAKE, chips, monedas, codigo_sel,
            on_moneda=_elegir_moneda_saldo, texto_vacio=f"NADIE DEBE NADA EN {codigo_sel}",
            acciones=[boton_recalculo],
        )

    def _elegir_moneda_saldo(codigo: str) -> None:
        ui["moneda_saldo"] = codigo
        contenedor_saldos.content = _barra_saldos()
        tabla.refrescar(contenedor_saldos)

    def _al_recargar() -> list[ft.Control]:
        contenedor_saldos.content = _barra_saldos()
        return [contenedor_saldos]

    # ------------------------------------------------------------
    # FILA "SALDO ANTERIOR" (snapshots de cierre de mes)
    # ------------------------------------------------------------

    pie: dict[str, Any] = {"periodo": None, "filas": []}

    def _filas_pie() -> list[FilaPie]:
        # Cacheadas por período (TablaPlanilla las pide en cada redibujo);
        # se recalculan al cambiar de mes, con ↻, o si ui/app.py
        # reconstruye la pantalla.
        periodo = (ui["mes"], ui["anio"])
        if pie["periodo"] != periodo:
            pie["filas"] = _calcular_filas_pie()
            pie["periodo"] = periodo
        return pie["filas"]

    def _calcular_filas_pie() -> list[FilaPie]:
        """Una fila por (persona, moneda) con saldo al cierre del mes anterior distinto de cero."""
        filas: list[FilaPie] = []
        for entrada in snapshots_service.get_saldos_anteriores_deudas(ui["mes"], ui["anio"]):
            saldo = entrada["monto_minor"]  # + te debe, − le debés
            if not saldo:
                continue
            signo = "-" if saldo < 0 else ""
            monto = f"{signo}{amount_display(abs(saldo), entrada['decimales'], entrada['moneda_simbolo'])}"
            filas.append(FilaPie(
                textos={
                    "persona": entrada["entidad_persona"],
                    "concepto": TEXTO_SALDO_ANTERIOR,
                    "monto": monto,
                    "moneda": entrada["moneda_codigo"],
                },
                positiva=saldo > 0,
                valores_filtro={"persona": entrada["entidad_persona"], "moneda": entrada["moneda_codigo"]},
                tooltip=TOOLTIP_SALDO_ANTERIOR,
            ))
        return filas

    def _tras_recalcular() -> None:
        pie["periodo"] = None
        tabla.recargar()

    boton_recalculo = boton_recalcular(page, snapshots_service, _tras_recalcular)

    # ------------------------------------------------------------
    # FILA DE ALTA
    # ------------------------------------------------------------

    alta_refs: dict[str, Any] = {}

    def _guardar_borrador_alta() -> None:
        if not alta_refs:
            return
        ui["alta"] = {
            "persona": alta_refs["persona"].value or "",
            "concepto": alta_refs["concepto"].value or "",
            "tipo": alta_refs["tipo"].valor,
            "monto": alta_refs["monto"].texto,
            "fecha": alta_refs["fecha"].value or "",
            "moneda": alta_refs["moneda"].valor,
        }

    def _on_cambio_borrador(e=None) -> None:
        # Solo guarda el borrador: el texto ya está en pantalla.
        _guardar_borrador_alta()
        sin_auto_update()

    def _construir_alta() -> FilaAlta:
        borrador = ui["alta"]
        moneda_inicial = borrador["moneda"] if borrador["moneda"] in codigos_moneda else codigos_moneda[0]

        campo_persona = ft.TextField(
            value=borrador["persona"], hint_text="PERSONA", autofocus=True,
            text_align=ft.TextAlign.CENTER, on_change=_on_cambio_borrador, **estilo_campo(),
        )
        campo_concepto = ft.TextField(
            value=borrador["concepto"], hint_text="EJ: CENA", text_align=ft.TextAlign.CENTER,
            on_change=_on_cambio_borrador, **estilo_campo(),
        )
        selector_tipo = tabla.selector_alta(
            list(ETIQUETA_TIPO.items()), borrador["tipo"], lambda clave: _guardar_borrador_alta(), "TIPO",
            colores=COLORES_TIPO,
        )
        # persistir_formula=True: el campo recuerda la fórmula mientras la
        # fila no se guarde. Negativo = el tipo contrario (lo resuelve el
        # service). Enter resuelve la fórmula y pasa a Fecha.
        campo_monto = CampoMonto(
            tabla.pagina_alta,
            on_confirmar=lambda monto_minor: _guardar_borrador_alta(),
            persistir_formula=True,
            decimales=_decimales(moneda_inicial),
            hint_text=HINT_MONTO_ALTA,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            on_avanzar=lambda: tabla.enfocar(campo_fecha),
        )
        sin_borde(campo_monto.control)
        campo_monto.control.text_align = ft.TextAlign.RIGHT
        if borrador["monto"]:
            campo_monto.control.value = borrador["monto"]
        selector_moneda = tabla.selector_alta(
            [(c, c) for c in codigos_moneda], moneda_inicial, lambda codigo: _guardar_borrador_alta(), "MONEDA",
        )
        celda_fecha, campo_fecha = tabla.campo_fecha_alta(
            borrador["fecha"] or date.today().isoformat(),
            on_cambio=_guardar_borrador_alta, on_submit=tabla.enfocar_confirmar,
        )
        boton_confirmar = tabla.boton_confirmar_alta("AGREGAR", lambda: _confirmar_alta())
        alta_refs.update(
            persona=campo_persona, concepto=campo_concepto, tipo=selector_tipo, monto=campo_monto,
            moneda=selector_moneda, fecha=campo_fecha, boton=boton_confirmar,
        )

        campo_persona.on_submit = lambda e: tabla.enfocar(campo_concepto)
        campo_concepto.on_submit = lambda e: tabla.enfocar(campo_monto.control)
        # Fecha es el último campo: Enter o Tab llevan al ✓ sin guardar (ahí Enter confirma).
        tabla.tab_a_confirmar(campo_fecha)

        return FilaAlta(
            celdas={
                "persona": campo_persona,
                "concepto": campo_concepto,
                "tipo": selector_tipo.control,
                "monto": campo_monto.control,
                "moneda": selector_moneda.control,
                "fecha": celda_fecha,
            },
            boton=boton_confirmar,
            foco=campo_persona,
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
        persona = (alta_refs["persona"].value or "").strip()
        fecha_str = (alta_refs["fecha"].value or "").strip()
        codigo = alta_refs["moneda"].valor
        if not persona:
            _mostrar_error("EL NOMBRE DE LA PERSONA NO PUEDE ESTAR VACÍO.")
            return
        campo_monto: CampoMonto = alta_refs["monto"]
        # confirmar() resuelve una fórmula pendiente (y marca el borde si no es válida).
        valor = numero(campo_monto.texto) if campo_monto.confirmar() else None
        if not valor:
            _mostrar_error("EL MONTO DEBE SER UN NÚMERO DISTINTO DE 0 (NEGATIVO = EL TIPO CONTRARIO).")
            return
        try:
            datetime.strptime(fecha_str, "%Y-%m-%d")
        except ValueError:
            _mostrar_error("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.")
            return
        moneda = monedas_por_codigo.get(codigo)
        if moneda is None:
            _mostrar_error("COMPLETÁ LA MONEDA.")
            return

        try:
            resultado = debts_service.create(
                entidad_persona=persona,
                concepto=alta_refs["concepto"].value,
                tipo=alta_refs["tipo"].valor,
                monto_minor=round(valor * 10 ** moneda["decimales"]),
                moneda_id=moneda["id"],
                fecha=fecha_str,
                origen_tipo="manual",
            )
        except DebtError as err:
            _mostrar_error(str(err))  # la fila queda como estaba para corregir
            return

        ui["alta"] = _alta_vacia(codigo)
        tabla.alta_ok()
        _mostrar_ok(f"MOVIMIENTO #{resultado.entity_id} CON '{persona.upper()}' REGISTRADO.")

    # ------------------------------------------------------------
    # FILAS DE DATOS — celdas editables inline (CLAUDE.md §10)
    # ------------------------------------------------------------

    def _construir_celdas(d: dict) -> dict[str, ft.Control]:
        def _guardar(**kwargs) -> str:
            # DebtError (y ValueError) suben a la celda, que los muestra y vuelve al valor anterior.
            debts_service.update(d["id"], **kwargs)
            return f"MOVIMIENTO #{d['id']} ACTUALIZADO."

        def _guardar_persona(nuevo: str) -> str:
            if not nuevo.strip():
                raise ValueError("EL NOMBRE DE LA PERSONA NO PUEDE ESTAR VACÍO.")
            return _guardar(entidad_persona=nuevo)

        def _guardar_monto(monto_minor: int) -> str:
            # Con el signo que se ve: + ME DEBE, − DEBO (el service da vuelta el tipo si es negativo).
            if monto_minor == 0:
                raise ValueError("EL MONTO NO PUEDE SER 0.")
            return _guardar(tipo="a_favor", monto_minor=monto_minor)

        def _guardar_moneda(nuevo_codigo: str) -> str:
            moneda = monedas_por_codigo[nuevo_codigo]
            # Mismo importe mostrado en la moneda nueva: con otros decimales se reescala.
            diferencia = moneda["decimales"] - d["decimales"]
            monto = d["monto_minor"] * 10 ** diferencia if diferencia >= 0 else round(d["monto_minor"] / 10 ** -diferencia)
            return _guardar(moneda_id=moneda["id"], monto_minor=monto)

        def _guardar_fecha(nuevo: str) -> str:
            try:
                datetime.strptime(nuevo.strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.") from None
            return _guardar(fecha=nuevo.strip())

        opciones_moneda = [(c, c) for c in codigos_moneda]
        if d["currency_code"] not in codigos_moneda:
            opciones_moneda.append((d["currency_code"], d["currency_code"]))

        return {
            "persona": tabla.celda_texto(d, "persona", d["entidad_persona"] or "", _guardar_persona),
            "concepto": tabla.celda_texto(d, "concepto", d["concepto"] or "", lambda nuevo: _guardar(concepto=nuevo)),
            "tipo": tabla.celda_dropdown(
                d, "tipo", _valor_columna(d, "tipo"), list(ETIQUETA_TIPO.items()), d["tipo"],
                lambda nuevo: _guardar(tipo=nuevo), color=COLORES_TIPO.get(d["tipo"], TEXT_SECONDARY),
            ),
            "monto": tabla.celda_monto(
                d, "monto", _monto_texto(d), COLORES_TIPO.get(d["tipo"], TEXT_SECONDARY), _con_signo(d),
                d["decimales"], _guardar_monto,
            ),
            "moneda": tabla.celda_dropdown(
                d, "moneda", d["currency_code"] or "", opciones_moneda, d["currency_code"], _guardar_moneda,
                color=TEXT_SECONDARY,
            ),
            "fecha": tabla.celda_texto(d, "fecha", d["fecha"] or "", _guardar_fecha),
        }

    # ------------------------------------------------------------
    # BARRA FLOTANTE: ELIMINAR
    # ------------------------------------------------------------

    def _avisos_eliminar(filas: list[dict]) -> str:
        vinculadas = sum(1 for d in filas if (d["origen_tipo"] or "manual") not in ("manual", "pago_migrado"))
        if not vinculadas:
            return ""
        return f"{vinculadas} VIENE(N) DE UN MOVIMIENTO O COMPRA (EL ORIGEN NO SE BORRA)"

    def _eliminar(filas: list[dict]) -> tuple[str, bool]:
        errores = []
        for d in filas:
            try:
                debts_service.delete(d["id"])
            except DebtError as err:
                errores.append(f"#{d['id']}: {err}")
        eliminadas = len(filas) - len(errores)
        if errores:
            return f"{eliminadas} ELIMINADA(S), {len(errores)} CON ERROR: " + " | ".join(errores), True
        return f"{eliminadas} MOVIMIENTO(S) ELIMINADO(S).", False

    # ------------------------------------------------------------
    # ARMADO
    # ------------------------------------------------------------

    tabla = TablaPlanilla(
        page,
        clave="deudas",
        columnas=COLUMNAS,
        pref_anchos=PREF_ANCHOS_COLUMNAS,
        cargar_filas=_cargar_filas,
        construir_celdas=_construir_celdas,
        construir_alta=_construir_alta,
        firma=_firma,
        valor_columna=_valor_columna,
        clave_orden=_clave_orden,
        texto_busqueda=lambda d: f"{d['entidad_persona'] or ''} {d['concepto'] or ''} {d['notas'] or ''}",
        on_eliminar=_eliminar,
        avisos_eliminar=_avisos_eliminar,
        al_recargar=_al_recargar,
        filas_pie=_filas_pie,
        errores_esperados=(DebtError,),
        texto_vacio="NO HAY MOVIMIENTOS DE DEUDAS PARA ESTE PERÍODO.",
    )
    control_tabla = tabla.construir()
    contenedor_saldos.content = _barra_saldos()

    titulo = barra_titulo(
        page, "DEUDAS", tabla, ui, lambda: tabla.recargar(limpiar_seleccion=True), "BUSCAR EN DEUDAS…",
    )
    # Scroll propio (el Registro lo tiene por la Column del dashboard).
    return ft.Column(
        [pantalla_planilla([titulo, contenedor_saldos, control_tabla])],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
