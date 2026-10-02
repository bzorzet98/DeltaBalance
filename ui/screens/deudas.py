"""
DeltaBalance — ui/screens/deudas.py

Deudas informales en dos TABS (services/debts_service.py,
docs/DATA_MODEL_DECISIONS.md sección 22): barra de título (búsqueda +
período) → switch ME DEBEN / DEBO → barra "saldo neto por persona" del tab
activo → tabla (TablaPlanilla, ui/components/tabla_planilla.py) con fila de
alta, edición inline y barra flotante. Mismo formato visual que el Registro.

Cada tab es un libro de movimientos con monto CON SIGNO: positivo = entrada
(la deuda crece: le prestaste / te prestaron), negativo = salida (un pago
que la baja). No hay columna Tipo — el tab lo dice todo — ni "Registrar
pago" / "Marcar incobrable": un pago es una fila negativa en el mismo tab.

Reglas de arquitectura: solo DebtsService/AccountsService/SnapshotsService —
nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).

--- Estado propio ---

Tab activo, período, moneda de la barra de saldo y el BORRADOR de la fila
de alta viven en un almacén por página (_ESTADOS_UI), igual que el Registro.
El borrador es uno solo para los dos tabs: lo tipeado sigue ahí al cambiar
de tab, y la fila nueva va al tab activo al confirmar. Cada tab es su propia
TablaPlanilla (CLAVE_TABLA): búsqueda, filtros, orden, selección y anchos
(PREF_ANCHOS_COLUMNAS, una clave por tab) se guardan por separado.

--- Switch ---

Dos pills, estilo selector de moneda de barra_resumen() (la activa rellena
con BTN_COMPARTIR). Cambiar de tab re-arma la pantalla con la tabla del otro
tab; a la anterior se le avisa con su propio hook de navegación
(al_ocultar: su barra flotante y su teclado dejan de actuar), igual que
hace ui/app.py al cambiar de pantalla.

--- Barra de saldo ---

DebtsService.summary_by_person(tab, hasta_fecha=último día del mes elegido):
un chip por persona en la moneda elegida, salvo las saldadas (saldo neto
exactamente 0), que no se muestran. El saldo pendiente se ve en positivo y
en verde en los dos tabs (en DEBO, lo que todavía debés); un saldo negativo
— te pagaron / pagaste de más — en rojo, con su signo. Las pills de moneda
son solo las monedas con algún saldo distinto de 0; si la elegida no tiene
ninguno, se muestra la primera que sí (ARS primero). ↻ recalcula
los snapshots (ui/components/saldo_anterior.py).

--- Tabla ---

Las filas del tab con fecha en el mes elegido (DebtsService.list_by_tab()).
Edición inline (CLAUDE.md §10) de todas las celdas vía DebtsService.update():
Persona, Concepto, Tag (TablaPlanilla.celda_tag(): vacía la borra), Monto
(con signo: verde si es positivo, rojo si es negativo), Moneda y Fecha. Cambiar la moneda conserva el importe mostrado
(si la moneda nueva tiene otros decimales, se reescala el monto, mismo
criterio que el Registro). Las notas no tienen columna: entran en la
búsqueda.

--- Fila de alta ---

Persona, Concepto, Tag (opcional, texto libre: TablaPlanilla.
campo_tag_alta()), Monto (CampoMonto, con fórmulas; positivo = la deuda
crece, negativo = un pago), Moneda (SelectorCiclico,
TablaPlanilla.selector_alta(): en 70 px un Dropdown no entra) y Fecha.
Enter nunca guarda la fila salvo con el foco en el ✓: Persona → Concepto →
Tag → Monto → Fecha (Moneda es un botón: Enter lo cambiaría, así que la
cadena lo saltea; con Tab se llega igual); en Fecha, el último campo, Enter
o Tab llevan el foco al ✓ sin activarlo, y ahí Enter o un click confirman.

--- Barra flotante ---

Solo Eliminar (DebtsService.delete(): una fila no tiene dependencias con
estado propio, CLAUDE.md §4), y la suma (Σ) de los montos seleccionados con
su signo, una por moneda.

--- Saldo anterior ---

Al final de la tabla, una fila "SALDO ANTERIOR" (FilaPie) por (persona,
moneda) del tab con saldo al cierre del mes anterior distinto de cero
(SnapshotsService.get_saldos_anteriores_deudas(): snapshot, o en vivo si
falta). Respeta los filtros de Persona y Moneda. Se cachea por período.
"""

import calendar
from datetime import date, datetime
from typing import Any, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.debts_service import TABS, DebtError, DebtsService
from services.snapshots_service import SnapshotsService
from ui.components.campo_monto import CampoMonto
from ui.components.saldo_anterior import TEXTO_SALDO_ANTERIOR, TOOLTIP_SALDO_ANTERIOR, boton_recalcular
from ui.components.tabla_planilla import (
    ANCHO_BORDE,
    ESPACIO_DOT,
    PILL_ALTURA,
    PILL_PADDING_H,
    PILL_RADIO,
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
from ui.theme.tabla_tokens import (
    BORDER_DEFAULT,
    BTN_COMPARTIR,
    PESO_HEADER,
    TEXT_NEGATIVO,
    TEXT_POSITIVO,
    TEXT_SECONDARY,
    TEXT_SOBRE_BOTON,
)
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---

COLUMNAS = [
    Columna("persona", "PERSONA", 150),
    Columna("concepto", "CONCEPTO", 200),
    Columna("tag", "TAG", 100),
    Columna("monto", "MONTO", 120, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
    Columna("moneda", "MONEDA", 70, redimensionable=False),
    Columna("fecha", "FECHA", 100, redimensionable=False),
]
# Una tabla por tab (ver docstring, "Estado propio").
CLAVE_TABLA = {"me_deben": "deudas_me_deben", "debo": "deudas_debo"}
PREF_ANCHOS_COLUMNAS = {"me_deben": "deudas_me_deben_anchos", "debo": "deudas_debo_anchos"}
TAB_DEFAULT = "me_deben"
MONEDA_DEFAULT = "ARS"
DECIMALES_DEFAULT = 2
HINT_MONTO_ALTA = "± MONTO"

# Switch ME DEBEN / DEBO: mismas pills que el selector de moneda de barra_resumen().
TAMANIO_SWITCH = TypographyTokens.REGISTRO_FONT_SALDO_BAR
ESPACIO_SWITCH = ESPACIO_DOT

ETIQUETA_TAB = {"me_deben": "ME DEBEN", "debo": "DEBO"}
TEXTO_VACIO_BARRA = {"me_deben": "NADIE TE DEBE NADA EN {moneda}", "debo": "NO LE DEBÉS NADA A NADIE EN {moneda}"}


# ============================================================
# ESTADO PROPIO POR PÁGINA (ver docstring del módulo)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia(moneda: Optional[str] = None) -> dict:
    return {
        "persona": "", "concepto": "", "tag": "", "monto": "", "fecha": date.today().isoformat(), "moneda": moneda,
    }


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {
            "mes": hoy.month, "anio": hoy.year, "tab": TAB_DEFAULT, "moneda_saldo": MONEDA_DEFAULT,
            "alta": _alta_vacia(),
        }
        _ESTADOS_UI[id(page)] = ui
    return ui


def _texto_monto(monto_minor: int, decimales: int, simbolo: str) -> str:
    """Monto con el signo con el que se guarda (+ entrada, - pago): '+ $1,000.00' / '- $1,000.00'."""
    signo = "-" if monto_minor < 0 else "+"
    return f"{signo} {amount_display(abs(monto_minor), decimales, simbolo)}"


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

    def _fin_de_mes() -> str:
        ultimo = calendar.monthrange(ui["anio"], ui["mes"])[1]
        return f"{ui['anio']:04d}-{ui['mes']:02d}-{ultimo:02d}"

    # ------------------------------------------------------------
    # SWITCH ME DEBEN / DEBO
    # ------------------------------------------------------------

    raiz = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    vista: dict[str, Any] = {"control_tabla": None}

    def _switch(activo: str) -> ft.Control:
        pills: list[ft.Control] = []
        for tab in TABS:
            es_activo = tab == activo
            pills.append(ft.Container(
                height=PILL_ALTURA,
                padding=ft.Padding.symmetric(horizontal=PILL_PADDING_H),
                border_radius=PILL_RADIO,
                bgcolor=BTN_COMPARTIR if es_activo else None,
                border=None if es_activo else ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
                alignment=ft.Alignment.CENTER,
                on_click=lambda e, t=tab: _cambiar_tab(t),
                content=ft.Text(
                    ETIQUETA_TAB[tab], size=TAMANIO_SWITCH, weight=PESO_HEADER,
                    color=TEXT_SOBRE_BOTON if es_activo else TEXT_SECONDARY,
                ),
            ))
        return ft.Row(pills, spacing=ESPACIO_SWITCH)

    def _cambiar_tab(tab: str) -> None:
        if tab == ui["tab"]:
            sin_auto_update()
            return
        anterior = vista["control_tabla"]
        if anterior is not None and isinstance(anterior.data, dict):
            anterior.data["al_ocultar"]()  # su barra flotante y su teclado dejan de actuar
        ui["tab"] = tab
        _mostrar_vista()
        # Update completo, como al navegar entre pantallas (ui/app.py): la
        # barra flotante de la tabla anterior (page.overlay) se esconde, y
        # la primera vez que se arma la tabla de este tab su barra y su host
        # de popups recién entran a page.overlay.
        page.update()

    def _mostrar_vista() -> None:
        controles, control_tabla = _construir_vista(ui["tab"])
        vista["control_tabla"] = control_tabla
        raiz.controls = [pantalla_planilla(controles)]

    # ------------------------------------------------------------
    # VISTA DE UN TAB
    # ------------------------------------------------------------

    def _construir_vista(tab: str) -> tuple[list[ft.Control], ft.Control]:
        """Título, switch, barra de saldo y tabla del tab. Devuelve (controles, control de la tabla)."""

        # --- Datos ---

        def _cargar_filas() -> list[dict]:
            return [dict(d) for d in debts_service.list_by_tab(tab, ui["mes"], ui["anio"])]  # CLAUDE.md §11

        def _monto_fila(d: dict) -> str:
            return _texto_monto(d["monto_minor"], d["decimales"], d["currency_symbol"] or "")

        def _color_monto(d: dict) -> str:
            return TEXT_NEGATIVO if d["monto_minor"] < 0 else TEXT_POSITIVO

        def _valor_columna(d: dict, columna: str) -> str:
            """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
            if columna == "persona":
                return d["entidad_persona"] or ""
            if columna == "concepto":
                return d["concepto"] or ""
            if columna == "tag":
                return d["tag"] or ""
            if columna == "monto":
                return _monto_fila(d)
            if columna == "moneda":
                return d["currency_code"] or ""
            return d["fecha"] or ""

        def _clave_orden(d: dict, columna: str) -> Any:
            if columna == "monto":
                return d["monto_minor"] / (10 ** d["decimales"])
            return _valor_columna(d, columna).lower()

        def _firma(d: dict) -> tuple:
            return (
                d["entidad_persona"], d["concepto"], d["tag"], d["notas"], d["monto_minor"], d["moneda_id"],
                d["currency_code"], d["currency_symbol"], d["decimales"], d["fecha"],
            )

        # --- Barra de saldo neto por persona ---

        contenedor_saldos = ft.Container()

        def _barra_saldos() -> ft.Control:
            resumen = debts_service.summary_by_person(tab, hasta_fecha=_fin_de_mes())
            # Pills: solo las monedas en las que alguien tiene saldo distinto de 0 (ARS primero).
            monedas_con_saldo = sorted(
                {entrada["moneda_codigo"] for entrada in resumen if entrada["saldo_minor"] != 0},
                key=lambda codigo: (codigo != MONEDA_DEFAULT, codigo),
            )
            # barra_resumen() siempre muestra la pill de la moneda elegida: si en
            # esa no hay saldos, se muestra la primera que sí tiene (sin cambiar
            # la elección guardada, que vale para los dos tabs).
            codigo_sel = ui["moneda_saldo"]
            if monedas_con_saldo and codigo_sel not in monedas_con_saldo:
                codigo_sel = monedas_con_saldo[0]
            de_la_moneda = [entrada for entrada in resumen if entrada["moneda_codigo"] == codigo_sel]
            # Las saldadas (saldo neto exactamente 0) no se muestran.
            personas_con_saldo = [p for p in de_la_moneda if p["saldo_minor"] != 0]
            chips: list[ChipResumen] = []
            for entrada in personas_con_saldo:
                saldo = entrada["saldo_minor"]
                color, signo = (TEXT_POSITIVO, "+") if saldo > 0 else (TEXT_NEGATIVO, "-")
                monto = f"{signo}{amount_display(abs(saldo), entrada['decimales'], entrada['moneda_simbolo'])}"
                chips.append(ChipResumen(
                    color=color, nombre=entrada["entidad_persona"], monto=monto, color_monto=color, moneda=codigo_sel,
                ))
            return barra_resumen(
                "SALDO NETO POR PERSONA", ft.Icons.HANDSHAKE, chips, monedas_con_saldo, codigo_sel,
                on_moneda=_elegir_moneda_saldo, texto_vacio=TEXTO_VACIO_BARRA[tab].format(moneda=codigo_sel),
                acciones=[boton_recalculo],
            )

        def _elegir_moneda_saldo(codigo: str) -> None:
            ui["moneda_saldo"] = codigo
            contenedor_saldos.content = _barra_saldos()
            tabla.refrescar(contenedor_saldos)

        def _al_recargar() -> list[ft.Control]:
            contenedor_saldos.content = _barra_saldos()
            return [contenedor_saldos]

        # --- Fila "SALDO ANTERIOR" (snapshots de cierre de mes) ---

        pie: dict[str, Any] = {"periodo": None, "filas": []}

        def _filas_pie() -> list[FilaPie]:
            # Cacheadas por período (TablaPlanilla las pide en cada redibujo);
            # se recalculan al cambiar de mes, con ↻, o si se re-arma la vista.
            periodo = (ui["mes"], ui["anio"])
            if pie["periodo"] != periodo:
                pie["filas"] = _calcular_filas_pie()
                pie["periodo"] = periodo
            return pie["filas"]

        def _calcular_filas_pie() -> list[FilaPie]:
            """Una fila por (persona, moneda) del tab con saldo al cierre del mes anterior distinto de cero."""
            filas: list[FilaPie] = []
            for entrada in snapshots_service.get_saldos_anteriores_deudas(ui["mes"], ui["anio"], tab):
                saldo = entrada["monto_minor"]
                if not saldo:
                    continue
                filas.append(FilaPie(
                    textos={
                        "persona": entrada["entidad_persona"],
                        "concepto": TEXTO_SALDO_ANTERIOR,
                        "monto": _texto_monto(saldo, entrada["decimales"], entrada["moneda_simbolo"]),
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

        # --- Fila de alta ---

        alta_refs: dict[str, Any] = {}

        def _guardar_borrador_alta() -> None:
            if not alta_refs:
                return
            ui["alta"] = {
                "persona": alta_refs["persona"].value or "",
                "concepto": alta_refs["concepto"].value or "",
                "tag": alta_refs["tag"].value or "",
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
            campo_tag = tabla.campo_tag_alta(borrador.get("tag") or "")
            campo_tag.on_change = _on_cambio_borrador
            # persistir_formula=True: el campo recuerda la fórmula mientras la
            # fila no se guarde. Positivo = la deuda crece, negativo = un pago.
            # Enter resuelve la fórmula y pasa a Fecha.
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
            boton_confirmar = tabla.boton_confirmar_alta(f"AGREGAR EN {ETIQUETA_TAB[tab]}", lambda: _confirmar_alta())
            alta_refs.update(
                persona=campo_persona, concepto=campo_concepto, tag=campo_tag, monto=campo_monto,
                moneda=selector_moneda, fecha=campo_fecha, boton=boton_confirmar,
            )

            campo_persona.on_submit = lambda e: tabla.enfocar(campo_concepto)
            campo_concepto.on_submit = lambda e: tabla.enfocar(campo_tag)
            campo_tag.on_submit = lambda e: tabla.enfocar(campo_monto.control)
            # Fecha es el último campo: Enter o Tab llevan al ✓ sin guardar (ahí Enter confirma).
            tabla.tab_a_confirmar(campo_fecha)

            return FilaAlta(
                celdas={
                    "persona": campo_persona,
                    "concepto": campo_concepto,
                    "tag": campo_tag,
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
                _mostrar_error("EL MONTO DEBE SER UN NÚMERO DISTINTO DE 0 (NEGATIVO = UN PAGO).")
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
                    tag=alta_refs["tag"].value,  # el service lo recorta; vacío = None
                    tab=tab,  # el del switch activo
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
            _mostrar_ok(f"MOVIMIENTO #{resultado.entity_id} CON '{persona.upper()}' REGISTRADO EN {ETIQUETA_TAB[tab]}.")

        # --- Filas de datos: celdas editables inline (CLAUDE.md §10) ---

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
                # Con el signo que se ve: positivo = la deuda crece, negativo = un pago.
                if monto_minor == 0:
                    raise ValueError("EL MONTO NO PUEDE SER 0.")
                return _guardar(monto_minor=monto_minor)

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
                # update() borra el tag con "" (lo guarda como NULL).
                "tag": tabla.celda_tag(d, "tag", d["tag"], lambda nuevo: _guardar(tag=nuevo)),
                "monto": tabla.celda_monto(
                    d, "monto", _monto_fila(d), _color_monto(d), d["monto_minor"], d["decimales"], _guardar_monto,
                ),
                "moneda": tabla.celda_dropdown(
                    d, "moneda", d["currency_code"] or "", opciones_moneda, d["currency_code"], _guardar_moneda,
                    color=TEXT_SECONDARY,
                ),
                "fecha": tabla.celda_texto(d, "fecha", d["fecha"] or "", _guardar_fecha),
            }

        # --- Barra flotante: eliminar ---

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

        # --- Armado ---

        tabla = TablaPlanilla(
            page,
            clave=CLAVE_TABLA[tab],
            columnas=COLUMNAS,
            pref_anchos=PREF_ANCHOS_COLUMNAS[tab],
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
            texto_vacio=f"NO HAY MOVIMIENTOS EN {ETIQUETA_TAB[tab]} PARA ESTE PERÍODO.",
            columna_suma="monto_minor",  # ya con su signo (+ la deuda crece, − un pago)
        )
        control_tabla = tabla.construir()
        contenedor_saldos.content = _barra_saldos()

        titulo = barra_titulo(
            page, "DEUDAS", tabla, ui, lambda: tabla.recargar(limpiar_seleccion=True), "BUSCAR EN DEUDAS…",
        )
        return [titulo, _switch(tab), contenedor_saldos, control_tabla], control_tabla

    _mostrar_vista()
    # Scroll propio (el Registro lo tiene por la Column del dashboard).
    return raiz
