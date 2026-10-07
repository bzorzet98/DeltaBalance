"""
DeltaBalance — ui/screens/gastos_compartidos.py

Gastos compartidos del hogar: barra de título (búsqueda + período) → barra
del hogar (selector de hogar, ⚙ configuración, saldo neto) → tabla
(TablaPlanilla, ui/components/tabla_planilla.py) con fila de alta, edición
inline y barra flotante de selección. Mismo formato visual que el Registro.
Extraída de la vieja ui/screens/deudas_y_compartidos.py (las deudas ahora
son ui/screens/deudas.py).

Reglas de arquitectura: solo SharedExpensesService/TransactionService/
FeesService/SnapshotsService — nunca repositories/ ni db/ directo
(CLAUDE.md §2/§3).

Saldo anterior: al final de la tabla, una fila "SALDO ANTERIOR" (FilaPie)
por (pagador, moneda) del hogar elegido con pendiente al cierre del mes
anterior distinto de cero (SnapshotsService.get_saldos_anteriores_
compartidos(); snapshot, o en vivo si falta), con el fondo verde/rojo
y el signo desde tu punto de vista, como la columna Adeudado (lo que
pagaste vos te lo deben: +; lo que pagó otro miembro lo debés: −). Respeta los
filtros de Pagador, Moneda y Estado. Se cachea por (hogar, período). El
botón ↻ de la barra recalcula todos los snapshots
(ui/components/saldo_anterior.py). Ojo: el snapshot usa el pendiente de
cuando se recalculó — tras un pago de un gasto viejo, la fila queda vieja
hasta el próximo ↻.

--- Hogar ---

Usuario local: ui/components/usuario_local.py (leer_usuario_local(), porque
build() es síncrono). Sin hogar todavía: la pantalla ofrece el mismo
diálogo de onboarding que compartir_gasto.py (abrir_dialogo_sin_hogar()) y
se re-arma al terminar. Con más de un hogar, un selector en la barra elige
el hogar activo (la tabla, el saldo y la fila de alta usan ese); con uno
solo, su nombre como texto fijo.

⚙ Configuración del hogar: miembros con su porcentaje default, agregar una
persona (SharedExpensesService.join_hogar() con el código del hogar),
código de invitación copiable y "Compartir enlace" (deshabilitado hasta
que exista la sincronización). El nombre del hogar y los porcentajes se
muestran pero NO se pueden editar todavía: SharedExpensesService no tiene
cómo hacerlo (TODO en _abrir_configuracion()).

--- Moneda y concepto de cada gasto ---

gastos_compartidos no tiene moneda ni concepto propios (db/schema.sql): se
sacan del ORIGEN del gasto — la transacción (TransactionService.get()), la
compra (FeesService.get_purchase()) o, para una cuota de una compra
prorrateada, la compra de esa cuota (mapa cuota → compra armado una vez
con FeesService.get_fees_for_purchase()). Se cachean por origen mientras
la pantalla vive (ui/app.py la reconstruye si otra pantalla cambió datos).
La descripción propia del gasto, si tiene, reemplaza al concepto del origen.
Un gasto del OTRO miembro tiene su origen en la base de él, no acá: la
sincronización trae el concepto de ese origen y lo deja como descripción
del gasto (sync/referencias.py, "El concepto del ORIGEN"), así que se ve
el concepto real en vez de "ORIGEN #… NO ENCONTRADO" — que queda solo
para un gasto que todavía no lo trae (hasta que su autor sincronice con
esta versión). Su moneda sigue siendo la default (MONEDA_DEFAULT): la del
origen no viaja.

--- Saldo neto ---

Desde el punto de vista del usuario local: un gasto que pagaste vos suma
(te deben su pendiente), uno que pagó otro miembro resta (debés). Por
moneda, nunca mezcladas, sobre los gastos 'pendiente' del hogar con fecha
<= último día del mes elegido: el saldo al cierre de ese mes, sin meses
futuros (mismo criterio que Deudas, el Registro y los snapshots de
SnapshotsService, "compartidos_mensuales"). Mismo límite que los
snapshots: usa el pendiente de HOY, así que un pago con fecha posterior
al mes elegido ya lo descuenta.
SharedExpensesService.get_net_balance() no se usa: suma todas las monedas
juntas y no mira quién pagó.

--- Fila de alta ---

gastos_compartidos.origen_tipo solo admite 'transaccion'/'compra_cuotas'/
'cuota_credito' (CHECK del schema): un gasto compartido no se puede crear
de cero. La fila de alta COMPARTE UN MOVIMIENTO YA CARGADO: en Concepto se
elige un movimiento del Registro del mes elegido que todavía no esté
compartido; Pagador (vos), Categoría, Monto base, Moneda y Fecha salen de
él (Estado muestra PENDIENTE en gris: así nace todo gasto). A diferencia
de Deudas, acá no hay carga libre: el CHECK del schema lo impide. El
reparto se elige con [%] [0.XX] [$] (ui/components/tipo_valor.py) en la
celda unida Coeficiente + Adeudado (190 px, exactamente esas dos
columnas), con la vista previa del adeudado al
lado: % = porcentaje del otro miembro (precargado con su porcentaje
default), 0.XX = coeficiente, $ = monto fijo en la moneda del movimiento
(no puede superar al monto base). El adeudado lo calcula
add_shared_expense() al confirmar. Un ingreso lleva monto base negativo y
el reparto elegido, como un gasto (pedido explícito: no se presupone que
se comparte el 100%), igual que compartir_gasto.py. Las
compras en cuotas se siguen compartiendo desde su pantalla. Enter nunca
guarda la fila salvo con el foco en el ✓: en Concepto elige el movimiento
y pasa al valor; en el valor (último campo) lleva el foco al ✓ sin
activarlo, y ahí Enter o un click confirman.

--- Tabla ---

Los gastos cuya fecha cae en el mes elegido. Mismo esquema de columnas que
Deudas (… montos → Moneda, Fecha, Estado): Concepto, Pagador y Categoría
se estiran con la ventana; las demás tienen ancho fijo, pero todos los
bordes se arrastran (tabla_planilla.py, "Columnas"). Moneda sale del
origen y no se edita acá. Edición inline (CLAUDE.md
§10), todo vía update_shared_expense(): Concepto (la descripción propia;
vacía = vuelve al concepto del origen) y Fecha en cualquier estado; Monto
base y Coeficiente (en %) solo con el gasto 'pendiente' — el service
además los bloquea si ya tiene pagos (CLAUDE.md §4). Adeudado se
recalcula solo; si hubo pagos parciales, el pendiente va en su tooltip.
Adeudado (y su tooltip) se muestra con signo desde tu punto de vista,
igual que el saldo neto: lo pagaste vos → + verde (te deben); lo pagó
otro miembro → − rojo (debés). El orden y los filtros usan ese mismo
valor con signo.

Barra flotante: Eliminar (delete_shared_expense(): solo sin pagos),
Registrar pago (un solo gasto pendiente: monto — precargado con el
pendiente —, tipo de pago y fecha; aplicar_pago() ajusta un sobrepago al
pendiente exacto) y la suma (Σ) del adeudado de las filas seleccionadas,
con el mismo signo que la columna (neto, una por moneda — igual que el
Registro).

--- Límite conocido (services/, fuera de alcance) ---

SharedExpensesService.list_shared_expenses() no expone paginación y el
repositorio pagina de a 50: la pantalla ve los 50 gastos más recientes del
hogar (y los 50 pendientes más recientes para el saldo).
"""

import calendar
from datetime import date, datetime
from typing import Any, Optional

import flet as ft

from services.fees_service import FeesService
from services.shared_expenses_service import SharedExpensesError, SharedExpensesService
from services.snapshots_service import SnapshotsService
from services.transaction_service import TransactionService
from ui.components.campo_monto import CampoMonto
from ui.components.tabla_planilla import (
    AccionBarra,
    ChipResumen,
    Columna,
    FilaAlta,
    FilaPie,
    TablaPlanilla,
    barra_resumen,
    barra_titulo,
    mostrar_mensaje,
    pantalla_planilla,
    texto_celda,
)
from ui.components.saldo_anterior import TEXTO_SALDO_ANTERIOR, TOOLTIP_SALDO_ANTERIOR, boton_recalcular
from ui.components.tipo_valor import (
    TIPO_COEFICIENTE,
    TIPO_MONTO,
    TIPO_PORCENTAJE,
    CampoTipoValor,
    numero,
)
from ui.components.usuario_local import abrir_dialogo_sin_hogar, leer_usuario_local, ordenar_hogares
from ui.theme.tabla_tokens import (
    BTN_REGISTRAR_PAGO,
    PESO_MONTO,
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_NEGATIVO,
    TEXT_POSITIVO,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.theme.tokens import LayoutTokens, TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---

# Mismo esquema que Deudas: datos propios → montos → Moneda, Fecha, Estado.
# Las fijas no se estiran con la ventana pero se arrastran igual (ver
# docstring de tabla_planilla.py, "Columnas").
ANCHO_COEFICIENTE = 80
ANCHO_ADEUDADO = 110
COLUMNAS = [
    Columna("concepto", "CONCEPTO", 180),
    Columna("pagador", "PAGADOR", 120),
    Columna("categoria", "CATEGORÍA", 130),
    Columna("monto_base", "MONTO BASE", 110, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
    # Coeficiente + Adeudado forman la celda unida del alta: nunca más angostas que su diseño.
    Columna("coeficiente", "COEFICIENTE", ANCHO_COEFICIENTE, redimensionable=False, ancho_min=ANCHO_COEFICIENTE),
    Columna("adeudado", "ADEUDADO", ANCHO_ADEUDADO, redimensionable=False,
            alineacion=ft.Alignment.CENTER_RIGHT, ancho_min=ANCHO_ADEUDADO),
    Columna("moneda", "MONEDA", 70, redimensionable=False),
    Columna("fecha", "FECHA", 100, redimensionable=False),
    Columna("estado", "ESTADO", 80, redimensionable=False),
]
PREF_ANCHOS_COLUMNAS = "compartidos_anchos_columnas"
# Alta: [%][0.XX][$] + valor + vista previa ocupan Coeficiente + Adeudado
# (80 + 110 = 190 px; adentro: ANCHO_PILLS = 76 px de pills, la vista previa
# y el resto para el valor). Moneda y Estado quedan con lo suyo.
COLUMNAS_UNIDAS_ALTA = {"coeficiente": 2}
ORDEN_TIPOS = (TIPO_PORCENTAJE, TIPO_COEFICIENTE, TIPO_MONTO)
ANCHO_VISTA_PREVIA = 52
TAMANIO_VISTA_PREVIA = TypographyTokens.REGISTRO_FONT_HEADER
ESPACIO_ALTA = 4
ESTADO_ALTA = "PENDIENTE"
ANCHO_SELECTOR_HOGAR = 220
ANCHO_DIALOGO = 380
ANCHO_CAMPO_PORCENTAJE = 90
ESPACIADO = 8
LIMITE_MOVIMIENTOS_DEL_MES = 500
LOTE_COMPRAS = 500
MONEDA_DEFAULT = "ARS"
DECIMALES_DEFAULT = 2
SIMBOLO_DEFAULT = "$"
# Coeficiente tipeado (0.XX / %): 0.3 * 100 = 30.000000000000004 → se redondea.
DECIMALES_COEFICIENTE = 4
DECIMALES_COEFICIENTE_VISIBLES = 2
TIPOS_PAGO = [("transaccion", "TRANSACCIÓN"), ("compensacion", "COMPENSACIÓN"), ("ajuste", "AJUSTE")]
TOOLTIP_SIN_SINCRONIZACION = "DISPONIBLE CUANDO SE ACTIVE LA SINCRONIZACIÓN"
TOOLTIP_SIN_EDICION = "TODAVÍA NO SE PUEDE EDITAR DESDE LA APP"
MOTIVO_SALDADO = "NO SE PUEDE MODIFICAR: EL GASTO YA FUE SALDADO."


# ============================================================
# ESTADO PROPIO POR PÁGINA (mismo criterio que el Registro)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia() -> dict:
    return {"transaccion_id": None, "tipo_valor": TIPO_PORCENTAJE, "valor": ""}


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {
            "mes": hoy.month, "anio": hoy.year, "moneda_saldo": MONEDA_DEFAULT,
            "hogar_id": None, "alta": _alta_vacia(),
        }
        _ESTADOS_UI[id(page)] = ui
    return ui


def _texto_monto(minor: int, decimales: int, simbolo: str) -> str:
    """Con signo adelante: -$1,234.00 (amount_display() lo pondría después del símbolo)."""
    signo = "-" if minor < 0 else ""
    return f"{signo}{amount_display(abs(minor), decimales, simbolo)}"


def _nombre_hogar(hogar: dict) -> str:
    return (hogar["nombre"] or f"HOGAR #{hogar['hogar_id']}").upper()


def build(
    page: ft.Page,
    shared_expenses_service: SharedExpensesService,
    transaction_service: TransactionService,
    fees_service: FeesService,
    snapshots_service: SnapshotsService,
) -> ft.Control:
    ui = _estado_ui(page)
    raiz = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _mostrar_error(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje)

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    # ------------------------------------------------------------
    # SIN HOGAR TODAVÍA
    # ------------------------------------------------------------

    def _pantalla_sin_hogar(usuario: Optional[str]) -> ft.Control:
        async def _tras_onboarding() -> None:
            _armar()
            # Update completo a propósito (una vez): la tabla recién creada
            # agregó su barra flotante y su host de popups a page.overlay, y
            # parchear solo `raiz` no los montaría.
            page.update()

        def _abrir_onboarding(e=None) -> None:
            abrir_dialogo_sin_hogar(page, shared_expenses_service, usuario or "", on_listo=_tras_onboarding)

        return pantalla_planilla([
            ft.Text(
                "GASTOS COMPARTIDOS DEL HOGAR", size=TypographyTokens.PAGE_TITLE_SIZE,
                weight=TypographyTokens.PAGE_TITLE_WEIGHT, color=TEXT_PRIMARY,
            ),
            ft.Text(
                "TODAVÍA NO PERTENECÉS A NINGÚN HOGAR COMPARTIDO.",
                size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, color=TEXT_SECONDARY,
            ),
            ft.Row([ft.ElevatedButton(content=ft.Text("CONFIGURAR HOGAR COMPARTIDO"), on_click=_abrir_onboarding)]),
        ])

    # ------------------------------------------------------------
    # PANTALLA CON HOGAR
    # ------------------------------------------------------------

    def _armar() -> None:
        usuario = leer_usuario_local()
        # El hogar por defecto (el primero) es el de más miembros — mismo
        # criterio que los diálogos de compartir, ver ordenar_hogares().
        hogares = ordenar_hogares(shared_expenses_service, shared_expenses_service.list_my_hogares(usuario)) if usuario else []
        if not hogares:
            raiz.controls = [_pantalla_sin_hogar(usuario)]
            return
        if ui["hogar_id"] not in [h["hogar_id"] for h in hogares]:
            ui["hogar_id"] = hogares[0]["hogar_id"]
        raiz.controls = [_pantalla_hogar(usuario, hogares)]

    def _pantalla_hogar(usuario: str, hogares: list[dict]) -> ft.Control:
        datos: dict[str, Any] = {"gastos": [], "pendientes": [], "origenes": {}, "cuotas": None}

        def _hogar_actual() -> dict:
            return next(h for h in hogares if h["hogar_id"] == ui["hogar_id"])

        # --- Origen de cada gasto (moneda + concepto), ver docstring ---

        def _mapa_cuotas() -> dict[int, tuple[dict, int]]:
            """cuota_credito id → (compra, número de cuota). Se arma una sola vez, solo si hace falta."""
            if datos["cuotas"] is None:
                mapa: dict[int, tuple[dict, int]] = {}
                pagina = 1
                while True:
                    lote = fees_service.list_purchases(page=pagina, per_page=LOTE_COMPRAS)
                    for fila in lote:
                        compra = dict(fila)
                        # total_unico nunca genera gastos por cuota.
                        if compra.get("modo_deuda") == "total_unico":
                            continue
                        for cuota in fees_service.get_fees_for_purchase(compra["id"]):
                            mapa[cuota["id"]] = (compra, cuota["numero_cuota"])
                    if len(lote) < LOTE_COMPRAS:
                        break
                    pagina += 1
                datos["cuotas"] = mapa
            return datos["cuotas"]

        def _resolver_origen(origen_tipo: str, origen_id: str) -> dict:
            fila, concepto = None, None
            if origen_tipo == "transaccion":
                fila = transaction_service.get(origen_id)
                concepto = fila["concepto"] if fila else None
            elif origen_tipo == "compra_cuotas":
                fila = fees_service.get_purchase(origen_id)
                concepto = fila["concepto"] if fila else None
            elif origen_tipo == "cuota_credito" and origen_id in _mapa_cuotas():
                compra, numero_cuota = _mapa_cuotas()[origen_id]
                # La compra del mapa sale de list_purchases(), que NO trae
                # currency_symbol (KeyError al abrir la pantalla con una
                # compra prorrateada compartida): la moneda, de get_purchase().
                fila = fees_service.get_purchase(compra["id"])
                concepto = f"{compra['concepto']} — CUOTA {numero_cuota}/{compra['total_cuotas']}"
            if fila is None:
                return {
                    "concepto_origen": f"ORIGEN #{origen_id} NO ENCONTRADO", "currency_code": MONEDA_DEFAULT,
                    "decimales": DECIMALES_DEFAULT, "currency_symbol": SIMBOLO_DEFAULT,
                }
            return {
                "concepto_origen": concepto or "", "currency_code": fila["currency_code"],
                "decimales": fila["decimales"], "currency_symbol": fila["currency_symbol"] or "",
            }

        def _con_origen(g: dict) -> dict:
            clave = (g["origen_tipo"], g["origen_id"])
            if clave not in datos["origenes"]:
                datos["origenes"][clave] = _resolver_origen(*clave)
            g.update(datos["origenes"][clave])
            return g

        # --- Datos ---

        def _cargar_gastos() -> list[dict]:
            hogar_id = ui["hogar_id"]
            # dict() antes de pasar a la UI (CLAUDE.md §11).
            datos["gastos"] = [_con_origen(dict(g)) for g in shared_expenses_service.list_shared_expenses(hogar_id)]
            datos["pendientes"] = [
                _con_origen(dict(g)) for g in shared_expenses_service.list_shared_expenses(hogar_id, estado="pendiente")
            ]
            mes = f"{ui['anio']:04d}-{ui['mes']:02d}"
            return [g for g in datos["gastos"] if (g["fecha"] or "")[:7] == mes]

        def _fin_de_mes() -> str:
            ultimo = calendar.monthrange(ui["anio"], ui["mes"])[1]
            return f"{ui['anio']:04d}-{ui['mes']:02d}-{ultimo:02d}"

        def _signo_mio(g: dict) -> int:
            """+1 si lo pagaste vos (te deben), -1 si lo pagó otro miembro (debés)."""
            return 1 if g["pagador"] == usuario else -1

        def _adeudado_mio(g: dict) -> int:
            """Adeudado desde tu punto de vista: + te deben, − debés (columna Adeudado y Σ)."""
            return _signo_mio(g) * g["monto_adeudado_minor"]

        def _concepto(g: dict) -> str:
            return g["descripcion"] or g["concepto_origen"]

        def _monto(g: dict, minor: int) -> str:
            return _texto_monto(minor, g["decimales"], g["currency_symbol"])

        def _texto_coeficiente(g: dict) -> str:
            return f"{round(g['coeficiente_deuda'], DECIMALES_COEFICIENTE_VISIBLES):g}"

        def _valor_columna(g: dict, columna: str) -> str:
            """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
            if columna == "concepto":
                return _concepto(g)
            if columna == "pagador":
                return g["pagador"] or ""
            if columna == "categoria":
                return g["category_name"] or ""
            if columna == "monto_base":
                return _monto(g, g["monto_base_minor"])
            if columna == "coeficiente":
                return f"{_texto_coeficiente(g)}%"
            if columna == "adeudado":
                return _monto(g, _adeudado_mio(g))
            if columna == "moneda":
                return g["currency_code"] or ""
            if columna == "fecha":
                return g["fecha"] or ""
            return (g["estado"] or "").upper()

        def _clave_orden(g: dict, columna: str) -> Any:
            if columna == "monto_base":
                return g["monto_base_minor"] / (10 ** g["decimales"])
            if columna == "adeudado":
                return _adeudado_mio(g) / (10 ** g["decimales"])
            if columna == "coeficiente":
                return g["coeficiente_deuda"]
            return _valor_columna(g, columna).lower()

        def _firma(g: dict) -> tuple:
            return (
                g["descripcion"], g["concepto_origen"], g["pagador"], g["category_name"], g["monto_base_minor"],
                g["coeficiente_deuda"], g["monto_adeudado_minor"], g["monto_pendiente_minor"], g["fecha"],
                g["estado"], g["currency_code"], g["decimales"], g["currency_symbol"],
            )

        # --- Barra del hogar: selector, ⚙ y saldo neto ---

        contenedor_resumen = ft.Container()

        def _barra_hogar() -> ft.Control:
            hogar = _hogar_actual()
            if len(hogares) > 1:
                selector: ft.Control = ft.Dropdown(
                    options=[ft.dropdown.Option(key=str(h["hogar_id"]), text=_nombre_hogar(h)) for h in hogares],
                    value=str(hogar["hogar_id"]),
                    on_select=lambda e: _cambiar_hogar(e.control.value),
                    width=ANCHO_SELECTOR_HOGAR, dense=LayoutTokens.CELDA_DENSE,
                    text_size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, tooltip="HOGAR",
                )
            else:
                selector = ft.Text(
                    _nombre_hogar(hogar),
                    size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                    color=TEXT_PRIMARY,
                )
            boton_configuracion = ft.IconButton(
                icon=ft.Icons.SETTINGS_OUTLINED, icon_color=TEXT_SECONDARY, tooltip="CONFIGURACIÓN DEL HOGAR",
                on_click=lambda e: _abrir_configuracion(),
            )

            codigo_sel = ui["moneda_saldo"]
            netos: dict[str, int] = {}
            formato: dict[str, tuple[int, str]] = {}
            # Al cierre del mes elegido: los gastos con fecha posterior no cuentan (ver docstring, "Saldo neto").
            fin_de_mes = _fin_de_mes()
            for g in datos["pendientes"]:
                if (g["fecha"] or "") > fin_de_mes:
                    continue
                netos[g["currency_code"]] = netos.get(g["currency_code"], 0) + _signo_mio(g) * g["monto_pendiente_minor"]
                formato[g["currency_code"]] = (g["decimales"], g["currency_symbol"])
            neto = netos.get(codigo_sel, 0)
            chips: list[ChipResumen] = []
            if neto:
                decimales, simbolo = formato[codigo_sel]
                color = TEXT_POSITIVO if neto > 0 else TEXT_NEGATIVO
                chips.append(ChipResumen(
                    color=color, nombre="TE DEBEN" if neto > 0 else "DEBÉS",
                    monto=amount_display(abs(neto), decimales, simbolo), color_monto=color, moneda=codigo_sel,
                ))
            monedas = [codigo for codigo, valor in netos.items() if valor]
            return barra_resumen(
                "", ft.Icons.HOME, chips, monedas, codigo_sel, on_moneda=_elegir_moneda_saldo,
                texto_vacio=f"ESTÁN A MANO EN {codigo_sel}", controles_titulo=[selector, boton_configuracion],
                acciones=[boton_recalculo],
            )

        def _elegir_moneda_saldo(codigo: str) -> None:
            ui["moneda_saldo"] = codigo
            contenedor_resumen.content = _barra_hogar()
            tabla.refrescar(contenedor_resumen)

        def _cambiar_hogar(hogar_id: str) -> None:
            ui["hogar_id"] = hogar_id
            # Alta nueva: el coeficiente sugerido depende del hogar.
            ui["alta"] = _alta_vacia()
            tabla.alta_ok()

        def _al_recargar() -> list[ft.Control]:
            contenedor_resumen.content = _barra_hogar()
            return [contenedor_resumen]

        # --- Fila "SALDO ANTERIOR" (snapshots de cierre de mes) ---

        pie: dict[str, Any] = {"clave": None, "filas": []}

        def _filas_pie() -> list[FilaPie]:
            # Cacheadas por (hogar, período) — TablaPlanilla las pide en cada
            # redibujo; se recalculan al cambiar de hogar o de mes, con ↻, o
            # si ui/app.py reconstruye la pantalla.
            clave = (ui["hogar_id"], ui["mes"], ui["anio"])
            if pie["clave"] != clave:
                pie["filas"] = _calcular_filas_pie()
                pie["clave"] = clave
            return pie["filas"]

        def _calcular_filas_pie() -> list[FilaPie]:
            """Una fila por (pagador, moneda) del hogar con pendiente al cierre del mes anterior distinto de cero."""
            filas: list[FilaPie] = []
            for entrada in snapshots_service.get_saldos_anteriores_compartidos(ui["hogar_id"], ui["mes"], ui["anio"]):
                pendiente = entrada["monto_minor"]
                if not pendiente:
                    continue
                # Desde tu punto de vista, como la columna Adeudado: lo que pagaste vos te lo deben (+).
                pendiente_mio = (1 if entrada["pagador"] == usuario else -1) * pendiente
                filas.append(FilaPie(
                    textos={
                        "concepto": TEXTO_SALDO_ANTERIOR,
                        "pagador": entrada["pagador"],
                        "adeudado": _texto_monto(pendiente_mio, entrada["decimales"], entrada["moneda_simbolo"]),
                        "moneda": entrada["moneda_codigo"],
                        "estado": ESTADO_ALTA,
                    },
                    positiva=pendiente_mio > 0,
                    valores_filtro={
                        "pagador": entrada["pagador"], "moneda": entrada["moneda_codigo"], "estado": ESTADO_ALTA,
                    },
                    tooltip=TOOLTIP_SALDO_ANTERIOR,
                ))
            return filas

        def _tras_recalcular() -> None:
            pie["clave"] = None
            tabla.recargar()

        boton_recalculo = boton_recalcular(page, snapshots_service, _tras_recalcular)

        # --- ⚙ Configuración del hogar ---

        def _abrir_configuracion() -> None:
            hogar = _hogar_actual()
            codigo = hogar["codigo_invitacion"]
            lista_miembros = ft.Column(spacing=ESPACIADO, tight=True)

            def _dibujar_miembros() -> None:
                filas = []
                for miembro in shared_expenses_service.list_miembros(hogar["hogar_id"]):
                    nombre = miembro["usuario_local"]
                    porcentaje = miembro["porcentaje_default"]
                    filas.append(ft.Row(
                        [
                            ft.Text(
                                nombre.upper() + (" (VOS)" if nombre == usuario else ""), expand=True,
                                color=TEXT_PRIMARY,
                            ),
                            # TODO: SharedExpensesService no expone
                            # HogarMiembrosRepository.actualizar_porcentaje_default():
                            # habilitar la edición inline cuando exista el método.
                            ft.TextField(
                                value=f"{porcentaje:g}" if porcentaje is not None else "", suffix="%",
                                hint_text="—", width=ANCHO_CAMPO_PORCENTAJE, dense=True,
                                text_align=ft.TextAlign.RIGHT, disabled=True, tooltip=TOOLTIP_SIN_EDICION,
                            ),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ))
                lista_miembros.controls = filas

            campo_persona = ft.TextField(label="NOMBRE", expand=True, dense=True)
            campo_porcentaje = ft.TextField(
                label="% DEFAULT", width=ANCHO_CAMPO_PORCENTAJE, dense=True, text_align=ft.TextAlign.RIGHT,
            )

            def _agregar_persona(e=None) -> None:
                nombre = (campo_persona.value or "").strip()
                if not nombre:
                    _mostrar_error("INGRESÁ EL NOMBRE DE LA PERSONA.")
                    return
                texto_porcentaje = (campo_porcentaje.value or "").strip()
                porcentaje = numero(texto_porcentaje) if texto_porcentaje else None
                if texto_porcentaje and (porcentaje is None or not 0 < porcentaje <= 100):
                    _mostrar_error("EL PORCENTAJE DEFAULT DEBE SER MAYOR A 0 Y COMO MÁXIMO 100.")
                    return
                try:
                    shared_expenses_service.join_hogar(
                        codigo_invitacion=codigo, nombre_local=nombre, porcentaje_default=porcentaje,
                    )
                except (SharedExpensesError, ValueError) as err:
                    _mostrar_error(str(err))
                    return
                campo_persona.value = ""
                campo_porcentaje.value = ""
                _dibujar_miembros()
                page.update(contenido)
                _mostrar_ok(f"'{nombre.upper()}' AGREGADO AL HOGAR.")

            async def _copiar_codigo(e=None) -> None:
                await ft.Clipboard().set(codigo)
                _mostrar_ok("CÓDIGO DE INVITACIÓN COPIADO.")

            _dibujar_miembros()
            contenido = ft.Column(
                [
                    # TODO: SharedExpensesService (y HogaresRepository) no
                    # tienen cómo renombrar un hogar: habilitar cuando exista.
                    ft.TextField(
                        label="NOMBRE DEL HOGAR", value=hogar["nombre"] or "", disabled=True,
                        tooltip=TOOLTIP_SIN_EDICION,
                    ),
                    ft.Text("MIEMBROS Y PORCENTAJE DEFAULT", weight=TypographyTokens.SECTION_TITLE_WEIGHT),
                    lista_miembros,
                    ft.Row(
                        [
                            campo_persona,
                            campo_porcentaje,
                            ft.IconButton(icon=ft.Icons.PERSON_ADD, tooltip="AGREGAR PERSONA", on_click=_agregar_persona),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(),
                    ft.Text("CÓDIGO DE INVITACIÓN", weight=TypographyTokens.SECTION_TITLE_WEIGHT),
                    ft.Row(
                        [
                            ft.Text(codigo, selectable=True, size=TypographyTokens.SECTION_TITLE_SIZE,
                                    weight=PESO_MONTO, color=TEXT_ACCENT),
                            ft.IconButton(icon=ft.Icons.CONTENT_COPY, tooltip="COPIAR CÓDIGO", on_click=_copiar_codigo),
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        tooltip=TOOLTIP_SIN_SINCRONIZACION,
                        content=ft.OutlinedButton(
                            content=ft.Row([ft.Icon(ft.Icons.SHARE), ft.Text("COMPARTIR ENLACE")], tight=True),
                            disabled=True,
                        ),
                    ),
                ],
                tight=True,
                spacing=ESPACIADO,
                scroll=ft.ScrollMode.AUTO,
            )
            page.show_dialog(ft.AlertDialog(
                modal=True,
                title=ft.Text(f"CONFIGURACIÓN — {_nombre_hogar(hogar)}"),
                content=ft.Container(width=ANCHO_DIALOGO, content=contenido),
                actions=[ft.TextButton(content=ft.Text("CERRAR"), on_click=_cerrar_dialogo)],
                actions_alignment=ft.MainAxisAlignment.END,
            ))

        # --- Fila de alta: compartir un movimiento del Registro ---

        alta_refs: dict[str, Any] = {}

        def _movimientos_para_compartir() -> list[dict]:
            """Movimientos del mes elegido que todavía no están compartidos."""
            ultimo_dia = calendar.monthrange(ui["anio"], ui["mes"])[1]
            filas = transaction_service.list_transactions(
                date_from=f"{ui['anio']:04d}-{ui['mes']:02d}-01",
                date_to=f"{ui['anio']:04d}-{ui['mes']:02d}-{ultimo_dia:02d}",
                per_page=LIMITE_MOVIMIENTOS_DEL_MES,
            )
            return [
                dict(t) for t in filas
                if shared_expenses_service.get_shared_expense_by_origin("transaccion", t["id"]) is None
            ]

        def _es_ingreso(t: dict) -> bool:
            # Mismo criterio que compartir_gasto.py.
            return t["tipo_movimiento"] == "ingreso"

        def _monto_base(t: dict) -> int:
            # Ingreso: monto base negativo (compartir_gasto.py).
            return -t["monto_minor"] if _es_ingreso(t) else t["monto_minor"]

        def _texto_movimiento(t: dict) -> str:
            signo = "+" if _es_ingreso(t) else "-"
            monto = amount_display(t["monto_minor"], t["decimales"], t["currency_symbol"] or "")
            return f"{t['concepto']} · {t['fecha']} · {signo}{monto}"

        def _porcentaje_sugerido() -> str:
            """Porcentaje default del otro miembro (mismo criterio que compartir_gasto.py), o vacío."""
            otros = [
                m for m in shared_expenses_service.list_miembros(ui["hogar_id"]) if m["usuario_local"] != usuario
            ]
            if not otros:
                return ""
            sugerido = shared_expenses_service.get_suggested_coefficient(ui["hogar_id"], otros[0]["usuario_local"])
            return f"{sugerido:g}" if sugerido is not None else ""

        def _guardar_borrador_alta() -> None:
            if not alta_refs:
                return
            ui["alta"] = {
                "transaccion_id": alta_refs["movimiento"].id_seleccionado,
                "tipo_valor": alta_refs["valor"].tipo,
                "valor": alta_refs["valor"].texto_valor,
            }

        def _construir_alta() -> FilaAlta:
            borrador = ui["alta"]
            movimientos = {str(t["id"]): t for t in _movimientos_para_compartir()}
            inicial = borrador["transaccion_id"] if borrador["transaccion_id"] in movimientos else None
            valor_inicial = borrador["valor"]
            if not valor_inicial and borrador["tipo_valor"] == TIPO_PORCENTAJE:
                valor_inicial = _porcentaje_sugerido()

            texto_categoria = texto_celda("", color=TEXT_SECONDARY)
            texto_monto_base = texto_celda("", color=TEXT_SECONDARY, size=TypographyTokens.REGISTRO_FONT_MONTO)
            texto_fecha = texto_celda("", color=TEXT_SECONDARY)
            texto_moneda = texto_celda("", color=TEXT_SECONDARY)
            vista_previa = ft.Text(
                "", width=ANCHO_VISTA_PREVIA, size=TAMANIO_VISTA_PREVIA, color=TEXT_SECONDARY,
                text_align=ft.TextAlign.RIGHT, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
            )
            primero = movimientos.get(inicial) if inicial else None
            campo_valor = CampoTipoValor(
                tabla.pagina_alta, orden=ORDEN_TIPOS,
                decimales=primero["decimales"] if primero else DECIMALES_DEFAULT, con_base=False,
                tipo_inicial=borrador["tipo_valor"], valor_inicial=valor_inicial,
                # El valor es el último campo: Enter lleva al ✓ sin guardar (ahí Enter confirma).
                on_cambio=lambda: _al_cambiar_valor(), on_enter=tabla.enfocar_confirmar,
            )

            def _movimiento() -> Optional[dict]:
                return movimientos.get(campo_movimiento.id_seleccionado or "")

            def _actualizar_vista_previa() -> None:
                t = _movimiento()
                if t is None:
                    vista_previa.value = ""
                    return
                base = _monto_base(t)
                adeudado = campo_valor.vista_previa_minor(abs(base))
                if adeudado is not None and base < 0:
                    adeudado = -adeudado  # hereda el signo del monto base (ingreso)
                # Sin prefijo: en ANCHO_VISTA_PREVIA entra el monto; qué es, en el tooltip.
                vista_previa.value = (
                    _texto_monto(adeudado, t["decimales"], t["currency_symbol"] or "") if adeudado is not None else "—"
                )
                vista_previa.tooltip = f"ADEUDADO CALCULADO: {vista_previa.value}"

            def _al_cambiar_valor() -> None:
                _guardar_borrador_alta()
                _actualizar_vista_previa()
                tabla.refrescar(vista_previa)

            def _mostrar_movimiento() -> None:
                t = _movimiento()
                texto_categoria.value = (t["category_name"] or "") if t else ""
                texto_monto_base.value = (
                    _texto_monto(_monto_base(t), t["decimales"], t["currency_symbol"] or "") if t else ""
                )
                texto_fecha.value = t["fecha"] if t else ""
                texto_moneda.value = t["currency_code"] if t else ""
                if t is not None:
                    campo_valor.cambiar_decimales(t["decimales"])
                _actualizar_vista_previa()

            def _on_movimiento(id_: Optional[str]) -> None:
                # CampoFiltrable parchea la fila (tabla.pagina_alta) justo después.
                _mostrar_movimiento()
                _guardar_borrador_alta()

            campo_movimiento = tabla.campo_filtrable_alta(
                "concepto", [(id_, _texto_movimiento(t)) for id_, t in movimientos.items()], _on_movimiento,
                placeholder="ELEGÍ UN MOVIMIENTO DEL MES", valor_inicial_id=inicial, autofocus=True,
                on_avanzar=lambda: tabla.enfocar(campo_valor.campo_foco),
            )
            boton_confirmar = tabla.boton_confirmar_alta("COMPARTIR MOVIMIENTO", lambda: _confirmar_alta())
            alta_refs.update(
                movimiento=campo_movimiento, movimientos=movimientos, valor=campo_valor, boton=boton_confirmar,
            )
            _mostrar_movimiento()

            campo_valor.control.expand = True
            return FilaAlta(
                celdas={
                    "concepto": campo_movimiento.control,
                    "pagador": texto_celda(usuario, color=TEXT_SECONDARY),
                    "categoria": texto_categoria,
                    "monto_base": texto_monto_base,
                    "coeficiente": ft.Row([campo_valor.control, vista_previa], spacing=ESPACIO_ALTA),
                    "moneda": texto_moneda,
                    "fecha": texto_fecha,
                    # Todo gasto nuevo nace PENDIENTE (informativo, no se elige).
                    "estado": texto_celda(ESTADO_ALTA, color=TEXT_MUTED),
                },
                boton=boton_confirmar,
                foco=campo_movimiento.campo_texto,
                unidas=COLUMNAS_UNIDAS_ALTA,
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
            t = alta_refs["movimientos"].get(alta_refs["movimiento"].id_seleccionado or "")
            if t is None:
                _mostrar_error("ELEGÍ UN MOVIMIENTO DEL MES DE LA LISTA DE SUGERENCIAS.")
                return
            base = _monto_base(t)
            campo_valor: CampoTipoValor = alta_refs["valor"]
            # Un ingreso también usa el reparto elegido (no se presupone el 100%).
            try:
                coeficiente = campo_valor.porcentaje(base)
            except ValueError as err:
                _mostrar_error(str(err))
                return
            # Con $ el porcentaje va sin redondear: así el adeudado da el monto fijo exacto.
            if campo_valor.tipo != TIPO_MONTO:
                coeficiente = round(coeficiente, DECIMALES_COEFICIENTE)
            try:
                resultado = shared_expenses_service.add_shared_expense(
                    hogar_id=ui["hogar_id"],
                    pagador=usuario,
                    origen_tipo="transaccion",
                    origen_id=t["id"],
                    categoria_id=t["categoria_id"],
                    monto_base_minor=base,
                    coeficiente_deuda=coeficiente,
                    fecha=t["fecha"],
                )
            except (SharedExpensesError, ValueError) as err:
                _mostrar_error(str(err))  # la fila queda como estaba para corregir
                return
            ui["alta"] = _alta_vacia()
            tabla.alta_ok()
            adeudado = _texto_monto(
                resultado.data["monto_adeudado_minor"], t["decimales"], t["currency_symbol"] or "",
            )
            _mostrar_ok(f"GASTO COMPARTIDO #{resultado.entity_id} REGISTRADO — ADEUDADO: {adeudado} {t['currency_code']}.")

        # --- Filas de datos: celdas editables inline (CLAUDE.md §10) ---

        def _construir_celdas(g: dict) -> dict[str, ft.Control]:
            pendiente = g["estado"] == "pendiente"

            def _guardar(**kwargs) -> str:
                shared_expenses_service.update_shared_expense(gasto_id=g["id"], hogar_id=ui["hogar_id"], **kwargs)
                return f"GASTO COMPARTIDO #{g['id']} ACTUALIZADO."

            def _guardar_concepto(nuevo: str) -> str:
                # Vacío = sin descripción propia (vuelve al concepto del origen).
                return _guardar(descripcion=nuevo.strip() or None)

            def _guardar_monto_base(monto_minor: int) -> str:
                # Puede ser negativo (pago recibido / reintegro); nunca 0.
                if monto_minor == 0:
                    raise ValueError("EL MONTO BASE NO PUEDE SER 0.")
                return _guardar(monto_base_minor=monto_minor)

            def _guardar_coeficiente(nuevo: str) -> str:
                valor = numero(nuevo)
                if valor is None or not 0 < valor <= 100:
                    raise ValueError("EL COEFICIENTE VA EN %: MAYOR A 0 Y COMO MÁXIMO 100 (EJ: 50).")
                return _guardar(coeficiente_deuda=round(valor, DECIMALES_COEFICIENTE))

            def _guardar_fecha(nuevo: str) -> str:
                try:
                    datetime.strptime(nuevo.strip(), "%Y-%m-%d")
                except ValueError:
                    raise ValueError("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.") from None
                return _guardar(fecha=nuevo.strip())

            def _lectura_monto(clave: str, texto: str, color: str, tooltip: Optional[str] = None) -> ft.Control:
                return tabla.celda_lectura(clave, texto_celda(
                    texto, color=color, size=TypographyTokens.REGISTRO_FONT_MONTO, weight=PESO_MONTO, tooltip=tooltip,
                ))

            # Adeudado desde tu punto de vista: + verde si te deben, − rojo si debés.
            a_favor = _adeudado_mio(g) > 0
            color_adeudado = TEXT_MUTED if not pendiente else (TEXT_POSITIVO if a_favor else TEXT_NEGATIVO)
            tooltip_adeudado = None
            if g["monto_pendiente_minor"] != g["monto_adeudado_minor"]:
                tooltip_adeudado = f"PENDIENTE: {_monto(g, _signo_mio(g) * g['monto_pendiente_minor'])}"

            texto_base = _valor_columna(g, "monto_base")
            texto_coeficiente = _valor_columna(g, "coeficiente")
            if pendiente:
                celda_base = tabla.celda_monto(
                    g, "monto_base", texto_base, TEXT_PRIMARY, g["monto_base_minor"], g["decimales"],
                    _guardar_monto_base,
                )
                celda_coeficiente = tabla.celda_texto(
                    g, "coeficiente", texto_coeficiente, _guardar_coeficiente, valor_inicial=_texto_coeficiente(g),
                )
            else:
                celda_base = _lectura_monto("monto_base", texto_base, TEXT_MUTED, f"{texto_base} ({MOTIVO_SALDADO})")
                celda_coeficiente = tabla.celda_lectura("coeficiente", texto_celda(
                    texto_coeficiente, color=TEXT_MUTED, tooltip=f"{texto_coeficiente} ({MOTIVO_SALDADO})",
                ))

            return {
                "concepto": tabla.celda_texto(g, "concepto", _concepto(g), _guardar_concepto),
                "pagador": tabla.celda_lectura("pagador", texto_celda(g["pagador"] or "", color=TEXT_SECONDARY)),
                "categoria": tabla.celda_lectura("categoria", texto_celda(g["category_name"] or "")),
                "monto_base": celda_base,
                "coeficiente": celda_coeficiente,
                "adeudado": _lectura_monto(
                    "adeudado", _valor_columna(g, "adeudado"), color_adeudado, tooltip_adeudado,
                ),
                # Moneda del origen (el gasto no tiene moneda propia): no se edita acá.
                "moneda": tabla.celda_lectura("moneda", texto_celda(_valor_columna(g, "moneda"), color=TEXT_SECONDARY)),
                "fecha": tabla.celda_texto(g, "fecha", g["fecha"] or "", _guardar_fecha),
                "estado": tabla.celda_lectura("estado", texto_celda(_valor_columna(g, "estado"), color=TEXT_SECONDARY)),
            }

        # --- Barra flotante: eliminar / registrar pago ---

        def _eliminar(filas: list[dict]) -> tuple[str, bool]:
            errores = []
            for g in filas:
                try:
                    shared_expenses_service.delete_shared_expense(g["id"], ui["hogar_id"])
                except SharedExpensesError as err:
                    errores.append(f"#{g['id']}: {err}")
            eliminados = len(filas) - len(errores)
            if errores:
                return f"{eliminados} ELIMINADO(S), {len(errores)} CON ERROR: " + " | ".join(errores), True
            return f"{eliminados} GASTO(S) COMPARTIDO(S) ELIMINADO(S).", False

        def _motivo_sin_pago(filas: list[dict]) -> Optional[str]:
            if len(filas) != 1:
                return "SELECCIONÁ UN SOLO GASTO PARA REGISTRAR UN PAGO."
            if filas[0]["estado"] != "pendiente":
                return "EL GASTO YA ESTÁ SALDADO."
            return None

        def _dialogo_pago(filas: list[dict]) -> None:
            g = filas[0]
            campo_monto = CampoMonto(
                page, on_confirmar=lambda monto_minor: None, decimales=g["decimales"],
                valor_inicial_minor=abs(g["monto_pendiente_minor"]), label="MONTO", autofocus=True,
            )
            dropdown_tipo_pago = ft.Dropdown(
                label="TIPO DE PAGO", options=[ft.dropdown.Option(key=k, text=t) for k, t in TIPOS_PAGO],
                value=TIPOS_PAGO[0][0],
            )
            campo_fecha = ft.TextField(label="FECHA (AAAA-MM-DD)", value=date.today().isoformat())

            def _confirmar(e=None) -> None:
                valor = numero(campo_monto.texto) if campo_monto.confirmar() else None
                if valor is None or valor <= 0:
                    _mostrar_error("EL MONTO DEBE SER UN NÚMERO MAYOR A 0.")
                    return
                fecha_str = (campo_fecha.value or "").strip()
                try:
                    datetime.strptime(fecha_str, "%Y-%m-%d")
                except ValueError:
                    _mostrar_error("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.")
                    return
                try:
                    resultado = shared_expenses_service.aplicar_pago(
                        gasto_id=g["id"], hogar_id=ui["hogar_id"],
                        monto_aplicado_minor=round(valor * 10 ** g["decimales"]),
                        fecha=fecha_str, tipo_pago=dropdown_tipo_pago.value,
                    )
                except (SharedExpensesError, ValueError) as err:
                    _mostrar_error(str(err))
                    return
                _cerrar_dialogo()
                tabla.recargar(limpiar_seleccion=True)
                mensaje = f"PAGO DE {_monto(g, resultado.data['monto_aplicado_minor'])} REGISTRADO."
                if resultado.data["ajustado"]:
                    mensaje += " (SE AJUSTÓ AL PENDIENTE EXACTO.)"
                if resultado.data["saldado"]:
                    mensaje += " GASTO SALDADO."
                else:
                    mensaje += f" PENDIENTE: {_monto(g, resultado.data['pendiente_minor'])}."
                _mostrar_ok(mensaje)

            page.show_dialog(ft.AlertDialog(
                modal=True,
                title=ft.Text(f"REGISTRAR PAGO — {_concepto(g).upper()}"),
                content=ft.Container(
                    width=ANCHO_DIALOGO,
                    content=ft.Column(
                        [
                            ft.Text(
                                f"PENDIENTE ACTUAL: {_monto(g, g['monto_pendiente_minor'])} {g['currency_code']}",
                                color=TEXT_SECONDARY,
                            ),
                            campo_monto.control, dropdown_tipo_pago, campo_fecha,
                        ],
                        tight=True, spacing=ESPACIADO,
                    ),
                ),
                actions=[
                    ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                    ft.ElevatedButton(content=ft.Text("CONFIRMAR"), on_click=_confirmar),
                ],
                actions_alignment=ft.MainAxisAlignment.END,
            ))

        # --- Armado ---

        tabla = TablaPlanilla(
            page,
            clave="compartidos",
            columnas=COLUMNAS,
            pref_anchos=PREF_ANCHOS_COLUMNAS,
            cargar_filas=_cargar_gastos,
            construir_celdas=_construir_celdas,
            construir_alta=_construir_alta,
            firma=_firma,
            valor_columna=_valor_columna,
            clave_orden=_clave_orden,
            texto_busqueda=lambda g: f"{_concepto(g)} {g['pagador'] or ''} {g['category_name'] or ''}",
            fila_atenuada=lambda g: g["estado"] == "saldado",
            on_eliminar=_eliminar,
            avisos_eliminar=lambda filas: "EL MOVIMIENTO O LA COMPRA DE ORIGEN NO SE BORRA",
            acciones_barra=[
                AccionBarra(ft.Icons.PAYMENTS_OUTLINED, BTN_REGISTRAR_PAGO, "REGISTRAR PAGO", _dialogo_pago, _motivo_sin_pago),
            ],
            al_recargar=_al_recargar,
            filas_pie=_filas_pie,
            errores_esperados=(SharedExpensesError,),
            texto_vacio="NO HAY GASTOS COMPARTIDOS PARA ESTE PERÍODO.",
            columna_suma="monto_adeudado_minor",
            signo_suma=_signo_mio,  # como la columna Adeudado: + te deben, − debés
        )
        control_tabla = tabla.construir()
        contenedor_resumen.content = _barra_hogar()

        def _al_cambiar_periodo() -> None:
            # Otro mes: la fila de alta ofrece los movimientos de ese mes.
            ui["alta"] = _alta_vacia()
            tabla.alta_ok()

        titulo = barra_titulo(
            page, "GASTOS COMPARTIDOS DEL HOGAR", tabla, ui, _al_cambiar_periodo, "BUSCAR EN COMPARTIDOS…",
        )
        return pantalla_planilla([titulo, contenedor_resumen, control_tabla])

    _armar()
    return raiz
