"""
DeltaBalance — ui/components/registro_transacciones.py

Registro de transacciones: barra de título (búsqueda + período) → barra de
saldo por cuenta → tabla (TablaPlanilla, ui/components/tabla_planilla.py)
con fila de alta, edición inline y barra flotante de selección (eliminar /
compartir). Toda la mecánica de tabla — anchos, filtros, orden, popups,
edición inline, selección, barra flotante, refresco parcial y performance —
vive en tabla_planilla.py, compartida con Compras en cuotas: acá queda solo
lo propio del Registro (columnas, datos, fila de alta con su routing,
eliminar, compartir, barra de saldo). Paleta en ui/theme/tabla_tokens.py.

Reglas de arquitectura: solo AccountsService/CategoriasService/
TransactionService/SharedExpensesService/SavingsService/DebtsService —
nunca repositories/ ni db/ directo (CLAUDE.md §2/§3). La firma de build()
no cambió.

--- Indicador de sincronización ---

A la derecha del título, el estado de la sincronización con Supabase
(_indicador_sync(): ícono de nube + "SINC" — verde sincronizado, amarillo
sincronizando, rojo sin conexión; click = sincronizar ahora). Toma el SyncEngine que registró ui/app.py
(sync.sync_engine.motor_registrado()), así que no hizo falta cambiar la
firma de build() ni la del dashboard.

--- Estado propio del Registro ---

Período, moneda de la barra de saldo y el BORRADOR de la fila de alta
viven en un almacén por página (_ESTADOS_UI, ver _estado_ui()); el estado
de la tabla (búsqueda, filtros, orden, selección, anchos) lo guarda
TablaPlanilla. Los dos sobreviven cuando ui/app.py reconstruye la pantalla
y al navegar. Tras un cambio de datos se llama a tabla.recargar(), que
también re-arma la barra de saldo (al_recargar): ya no se llama a
on_cambio() (queda en la firma para no romper al dashboard).

--- Fila de alta ---

Signo del monto: negativo = egreso, positivo = ingreso. Enter nunca guarda
la fila salvo con el foco en el ✓: Concepto → Banco → Categoría → Tag →
Monto → Fecha → Moneda (en Banco/Categoría Enter elige la sugerencia; en Monto
resuelve la fórmula) y en Moneda, el último campo, Enter o Tab llevan el
foco al ✓ sin activarlo; ahí Enter o un click confirman (TablaPlanilla.
tab_a_confirmar()). Al guardar, la fila se reconstruye vacía con el foco en
Concepto.

Tag: etiqueta libre y opcional (vacía = None), solo en el guardado normal
(TransactionService.create(tag=)) — la autotransferencia y el ahorro no la
usan (create_transfer() pone su propio tag).

Categorías especiales (_CATEGORIAS_ROUTING_ESPECIAL), sin cambios de
comportamiento:
- "Autotransferencia": pide la cuenta destino y llama a
  TransactionService.create_transfer() (el concepto viaja como `notes`,
  create_transfer() no tiene `concept`). Signo ignorado (abs).
- "Ahorro/Inversión": modo simple (objetivo + "crear nuevo" →
  SavingsService.get_or_create_reserved_cash_asset() +
  register_purchase()) o "Elegir activo específico" (formulario de
  ui/components/dialogo_compra_ahorro.py en el mismo diálogo). Signo
  ignorado.
- "Deuda": crea la transacción normal primero y después pide persona/
  vencimiento para vincularle una deuda (DebtsService.create(origen_tipo=
  'transaccion')); el tab sale del signo (egreso → 'me_deben', ingreso →
  'debo') y el monto va en positivo (la deuda crece). La tabla de deudas ya
  no tiene fecha de vencimiento: si se carga, va en las notas ("VENCE:
  AAAA-MM-DD").

--- Filas ---

Edición inline (CLAUDE.md §10): todas las columnas — Concepto, Banco,
Categoría, Tag (TablaPlanilla.celda_tag(): vacía la borra), Monto (con el
signo que se ve, − gasto / + ingreso: un negativo es válido; lo que
todavía no se puede es dar vuelta el signo, porque
TransactionService.update() no cambia tipo_movimiento), Fecha y
Moneda (entre las monedas de la cuenta de la fila; mantiene el importe
mostrado, TransactionService.update() recibe monto + moneda juntos). Banco
y Categoría excluyen tarjetas de crédito / usan las mismas categorías que
la fila de alta.

Columna de acción (TablaPlanilla.icono_accion(), azul si ya hay algo
vinculado, si no gris y solo al hover):
- Casita: compartir con el hogar — el flujo de ui/components/
  compartir_gasto.py (si ya está compartida, muestra el detalle).
- Handshake: registrar como deuda — diálogo con persona, ME DEBEN / DEBO y
  el monto como $ / 0.XX / % de "¿DE CUÁNTO?" (ui/components/
  tipo_valor.py, precargado con el monto del movimiento). Llama a
  DebtsService.create(origen_tipo='transaccion', origen_id=id); moneda,
  fecha y concepto salen del movimiento. Con deudas ya vinculadas (de acá
  o del routing "Deuda" de la fila de alta), el diálogo las lista y deja
  agregar otra (ej. una cena que te deben dos personas).

Eliminar (barra flotante): confirmación inline en la misma barra. Si
alguna fila es parte de una autotransferencia o el origen de un movimiento
de ahorro, el aviso lo dice ahí mismo
(TransactionService.get_delete_warnings()). Compartir: todas las filas
seleccionadas tienen que ser de la misma moneda (si no, error inline en la
barra). Con UNA fila, el flujo de siempre de compartir_gasto.py; con
varias, ui/components/compartir_varios.py con los mismos campos (hogar +
coeficiente de 0 a 1) — mismo hogar y mismo coeficiente para todas,
salteando las ya compartidas; los ingresos se registran como pago
recibido (coeficiente 100%, monto base negativo), igual que en el flujo de
una fila.
La barra muestra además la suma (Σ) de los montos seleccionados, con el
signo que se ve (− gasto, + ingreso) y una por moneda.

--- Saldo anterior (snapshots de cierre de mes) ---

Al final de la tabla, una fila "SALDO ANTERIOR" (FilaPie) por cada (cuenta,
moneda) con saldo al cierre del mes anterior distinto de cero
(SnapshotsService.get_saldos_anteriores_cuentas(): snapshot, o en vivo si
falta). Respeta el filtro de Banco (y el de Moneda) de la tabla. Se
cachea por período. El botón ↻ al extremo derecho de la barra de saldo
recalcula todos los snapshots (ui/components/saldo_anterior.py).

--- Sin confirmar corriendo la app ---

Todo lo de la tabla está listado en el docstring de tabla_planilla.py.
Propio de acá: page.run_task() sobre el on_click async del ícono de
compartir_gasto.py.
"""

import calendar
from datetime import date, datetime
from typing import Any, Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.debts_service import TABS as TABS_DEUDA
from services.debts_service import DebtError, DebtsService
from services.savings_service import SavingsError, SavingsService
from services.shared_expenses_service import SharedExpensesService
from services.snapshots_service import SnapshotsService
from services.transaction_service import TransactionError, TransactionService
from sync.sync_engine import (
    ESTADO_SIN_CONEXION,
    ESTADO_SIN_SESION,
    ESTADO_SINCRONIZADO,
    ESTADO_SINCRONIZANDO,
    SyncEngine,
    motor_registrado,
)
from ui.components import compartir_gasto, dialogo_compra_ahorro
from ui.components.saldo_anterior import TEXTO_SALDO_ANTERIOR, TOOLTIP_SALDO_ANTERIOR, boton_recalcular
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.components.compartir_varios import FORMATO_UNIDAD, abrir_compartir_varios
from ui.components.tipo_valor import TIPO_COEFICIENTE, TIPO_MONTO, TIPO_PORCENTAJE, CampoTipoValor
from ui.components.tabla_planilla import (
    EXTRA_AJUSTE_DOT,
    ChipResumen,
    Columna,
    FilaAlta,
    FilaPie,
    TablaPlanilla,
    banco_con_dot,
    barra_resumen,
    barra_titulo,
    color_cuenta,
    estilo_campo,
    mostrar_mensaje,
    pantalla_planilla,
    refrescar,
    sin_auto_update,
    sin_borde,
    texto_celda,
)
from ui.theme.tabla_tokens import (
    BG_SUPERFICIE,
    BORDER_DEFAULT,
    TEXT_ACCENT,
    TEXT_NEGATIVO,
    TEXT_POSITIVO,
    TEXT_SECONDARY,
)
from ui.theme.tokens import LayoutTokens, TypographyTokens
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---

# Columnas (todas redimensionables): proporción inicial en px de referencia.
COLUMNAS = [
    Columna("concepto", "CONCEPTO", 220),
    Columna("banco", "BANCO", 130, extra_ajuste=EXTRA_AJUSTE_DOT),
    Columna("categoria", "CATEGORÍA", 150),
    Columna("tag", "TAG", 100),
    Columna("monto", "MONTO", 120, alineacion=ft.Alignment.CENTER_RIGHT),
    Columna("fecha", "FECHA", 110),
    Columna("moneda", "MONEDA", 80, ancho_min=60),
]
PREF_ANCHOS_COLUMNAS = "registro_anchos_columnas"
ESPACIADO = 8
LIMITE_TRANSACCIONES_DEL_MES = 500
ANCHO_TIPO_DEUDA = 160
ANCHO_DIALOGO_ROUTING = 320
MONEDA_DEFAULT = "ARS"
HINT_MONTO_ALTA = "± MONTO"
MENSAJE_SIGNO_NO_EDITABLE = (
    "CAMBIAR UN GASTO A INGRESO (O AL REVÉS) TODAVÍA NO SE PUEDE DESDE LA TABLA: "
    "BORRALO Y CARGALO DE NUEVO CON EL SIGNO CORRECTO."
)

# Indicador de sincronización con Supabase (barra superior, a la derecha del título).
COLOR_SYNC_EN_CURSO = "#FFC107"  # amarillo: no hay token de "en curso" en ui/theme/tabla_tokens.py
TEXTO_INDICADOR_SYNC = "SINC"
TAMANIO_ICONO_SYNC = 16
ESPACIO_ICONO_SYNC = 4
PADDING_INDICADOR_H = 10
ALTURA_INDICADOR = 36
RADIO_INDICADOR = 18
# estado de SyncEngine → (ícono, color, tooltip corto).
ESTADOS_INDICADOR_SYNC = {
    ESTADO_SINCRONIZADO: (ft.Icons.CLOUD_DONE, TEXT_POSITIVO, "SINCRONIZADO"),
    ESTADO_SINCRONIZANDO: (ft.Icons.CLOUD_SYNC, COLOR_SYNC_EN_CURSO, "SINCRONIZANDO…"),
    ESTADO_SIN_CONEXION: (ft.Icons.CLOUD_OFF, TEXT_NEGATIVO, "SIN CONEXIÓN"),
    ESTADO_SIN_SESION: (ft.Icons.CLOUD_OFF, TEXT_NEGATIVO, "SIN SESIÓN"),
}

# Categorías especiales de routing de la fila de alta — clave:
# (categoria_principal, subcategoria), mismo criterio que
# services/categorias_service.py CATEGORIAS_PROTEGIDAS (por nombre, el id
# varía entre bases).
_CATEGORIAS_ROUTING_ESPECIAL: dict[tuple[str, str], str] = {
    ("MOVIMIENTO CAPITAL", "Autotransferencia"): "autotransferencia",
    ("MOVIMIENTO CAPITAL", "Ahorro/Inversión"): "ahorro_inversion",
    ("MOVIMIENTO CAPITAL", "Deuda"): "deuda",
}
# Opción "+ Crear nuevo objetivo" del diálogo de Ahorro/Inversión — nunca
# colisiona con un id real (siempre numérico).
_ID_OBJETIVO_NUEVO = "__nuevo__"


# ============================================================
# ESTADO PROPIO POR PÁGINA (ver docstring del módulo)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia() -> dict:
    return {
        "concepto": "", "cuenta_id": None, "categoria_id": None,
        "tag": "", "monto": "", "fecha": date.today().isoformat(), "moneda": None,
    }


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {"mes": hoy.month, "anio": hoy.year, "moneda_saldo": MONEDA_DEFAULT, "alta": _alta_vacia()}
        _ESTADOS_UI[id(page)] = ui
    return ui


def _cuentas_no_credito(cuentas: list[dict]) -> list[dict]:
    """Las tarjetas de crédito solo aparecen en Compras en cuotas."""
    return [c for c in cuentas if c["tipo"] != "credito"]


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    transaction_service: TransactionService,
    shared_expenses_service: SharedExpensesService,
    savings_service: SavingsService,
    debts_service: DebtsService,
    estado: dict,
    on_cambio: Callable[[], None],
    snapshots_service: SnapshotsService,
) -> ft.Control:
    """
    Args:
        estado:    Dict del dashboard. Se le copian mes/anio del período
                   elegido acá.
        on_cambio: Ya no se llama: el Registro se refresca solo tras cada
                   cambio de datos (ver docstring del módulo). Queda para no
                   cambiar la firma.
        snapshots_service: Fila SALDO ANTERIOR y botón ↻ (ver docstring
                   del módulo).
    """
    ui = _estado_ui(page)
    estado["mes"], estado["anio"] = ui["mes"], ui["anio"]

    def _mostrar_error(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje)

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    # ------------------------------------------------------------
    # DATOS
    # ------------------------------------------------------------

    datos: dict[str, Any] = {}

    def _cargar_cuentas() -> None:
        datos["cuentas_por_id"] = {c["id"]: c for c in accounts_service.list_accounts(solo_activas=False)}
        datos["cuentas_activas_todas"] = accounts_service.list_accounts(solo_activas=True)

    _cargar_cuentas()
    cuentas_activas = _cuentas_no_credito(datos["cuentas_activas_todas"])
    cuentas_todas_no_credito = _cuentas_no_credito(list(datos["cuentas_por_id"].values()))
    monedas_por_codigo = {m["codigo"]: dict(m) for m in accounts_service.list_currencies()}

    todas_las_categorias = [dict(c) for c in categorias_service.list_categories()]
    # Normales (ingreso/egreso) + las 3 especiales de routing al final, así
    # la primera opción por default sigue siendo una categoría normal.
    categorias_especiales = [
        c for c in todas_las_categorias
        if (c["categoria_principal"], c["subcategoria"]) in _CATEGORIAS_ROUTING_ESPECIAL
    ]
    categorias = [c for c in todas_las_categorias if c["tipo"] in ("ingreso", "egreso")] + categorias_especiales
    mapa_categoria_a_routing: dict[str, str] = {
        str(c["id"]): _CATEGORIAS_ROUTING_ESPECIAL[(c["categoria_principal"], c["subcategoria"])]
        for c in categorias_especiales
    }
    opciones_cuenta = [(str(c["id"]), c["nombre"]) for c in cuentas_activas]
    opciones_cuenta_edicion = [(str(c["id"]), c["nombre"]) for c in cuentas_todas_no_credito]
    opciones_categoria = [(str(c["id"]), c["subcategoria"]) for c in categorias]

    def _cargar_transacciones() -> list[dict]:
        ultimo_dia = calendar.monthrange(ui["anio"], ui["mes"])[1]
        filas = transaction_service.list_transactions(
            date_from=f"{ui['anio']:04d}-{ui['mes']:02d}-01",
            date_to=f"{ui['anio']:04d}-{ui['mes']:02d}-{ultimo_dia:02d}",
            per_page=LIMITE_TRANSACCIONES_DEL_MES,
        )
        transacciones = [dict(t) for t in filas]  # CLAUDE.md §11
        datos["compartidos"] = {
            t["id"] for t in transacciones
            if shared_expenses_service.get_shared_expense_by_origin("transaccion", t["id"]) is not None
        }
        _cargar_deudas_vinculadas()
        return transacciones

    def _cargar_deudas_vinculadas() -> None:
        """transacción id → deudas con origen_tipo='transaccion' que la apuntan (ícono handshake)."""
        vinculadas: dict[int, list[dict]] = {}
        for tab in TABS_DEUDA:
            for deuda in debts_service.list_by_tab(tab):
                if deuda["origen_tipo"] == "transaccion" and deuda["origen_id"] is not None:
                    vinculadas.setdefault(deuda["origen_id"], []).append(dict(deuda))
        datos["deudas"] = vinculadas

    def _es_egreso(t: dict) -> bool:
        return t["tipo_movimiento"] == "egreso"

    def _monto_con_signo(t: dict) -> str:
        signo = "-" if _es_egreso(t) else "+"
        return f"{signo} {amount_display(t['monto_minor'], t['decimales'], t['currency_symbol'] or '')}"

    def _valor_columna(t: dict, columna: str) -> str:
        """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
        if columna == "concepto":
            return t["concepto"] or ""
        if columna == "banco":
            return t["account_name"] or ""
        if columna == "categoria":
            return t["category_name"] or ""
        if columna == "tag":
            return t["tag"] or ""
        if columna == "monto":
            return _monto_con_signo(t)
        if columna == "fecha":
            return t["fecha"] or ""
        return t["currency_code"] or ""

    def _clave_orden(t: dict, columna: str) -> Any:
        if columna == "monto":
            monto = t["monto_minor"] / (10 ** t["decimales"])
            return -monto if _es_egreso(t) else monto
        return _valor_columna(t, columna).lower()

    def _firma(t: dict) -> tuple:
        """Todo lo que la fila muestra: si no cambió, la fila cacheada se reusa tal cual."""
        return (
            t["concepto"], t["cuenta_id"], t["account_name"], t["categoria_id"], t["category_name"], t["tag"],
            t["monto_minor"], t["tipo_movimiento"], t["fecha"], t["currency_code"], t["decimales"],
            t["currency_symbol"], t["id"] in datos["compartidos"],
            tuple(d["id"] for d in datos["deudas"].get(t["id"], [])),
        )

    # ------------------------------------------------------------
    # BARRA DE SALDO POR CUENTA
    # ------------------------------------------------------------

    contenedor_saldos = ft.Container()

    def _barra_saldos() -> ft.Control:
        codigo_sel = ui["moneda_saldo"]
        moneda_sel = monedas_por_codigo.get(codigo_sel, {})
        cuentas = datos["cuentas_activas_todas"]
        chips: list[ChipResumen] = []
        for cuenta in cuentas:
            saldo = next((s for s in cuenta["saldos"] if s["moneda_codigo"] == codigo_sel), None)
            if saldo is None or saldo["saldo_minor"] == 0:
                continue
            negativo = saldo["saldo_minor"] < 0
            monto = amount_display(abs(saldo["saldo_minor"]), moneda_sel.get("decimales", 2), saldo["moneda_simbolo"] or "")
            chips.append(ChipResumen(
                color=color_cuenta(cuenta, cuenta["nombre"]),
                nombre=cuenta["nombre"],
                monto=f"{'-' if negativo else ''}{monto}",
                color_monto=TEXT_NEGATIVO if negativo else TEXT_POSITIVO,
                moneda=codigo_sel,
            ))
        # Monedas con algún saldo distinto de cero (+ la elegida, la agrega barra_resumen()).
        monedas = [s["moneda_codigo"] for c in cuentas for s in c["saldos"] if s["saldo_minor"] != 0]
        return barra_resumen(
            "SALDO POR CUENTA", ft.Icons.ACCOUNT_BALANCE_WALLET_OUTLINED, chips, monedas, codigo_sel,
            on_moneda=_elegir_moneda_saldo, texto_vacio=f"SIN SALDOS EN {codigo_sel}",
            acciones=[boton_recalculo],
        )

    def _elegir_moneda_saldo(codigo: str) -> None:
        ui["moneda_saldo"] = codigo
        contenedor_saldos.content = _barra_saldos()
        tabla.refrescar(contenedor_saldos)

    def _al_recargar() -> list[ft.Control]:
        # Los saldos cambian con cualquier alta/edición/borrado.
        _cargar_cuentas()
        contenedor_saldos.content = _barra_saldos()
        return [contenedor_saldos]

    # ------------------------------------------------------------
    # FILA "SALDO ANTERIOR" (snapshots de cierre de mes)
    # ------------------------------------------------------------

    pie: dict[str, Any] = {"periodo": None, "filas": []}

    def _filas_pie() -> list[FilaPie]:
        # Cacheadas por período (TablaPlanilla las pide en cada redibujo):
        # editar el mes mostrado no cambia el cierre del anterior. Se
        # recalculan al cambiar de mes, con ↻, o si ui/app.py reconstruye
        # la pantalla.
        periodo = (ui["mes"], ui["anio"])
        if pie["periodo"] != periodo:
            pie["filas"] = _calcular_filas_pie()
            pie["periodo"] = periodo
        return pie["filas"]

    def _calcular_filas_pie() -> list[FilaPie]:
        """Una fila por (cuenta, moneda) con saldo al cierre del mes anterior distinto de cero."""
        filas: list[FilaPie] = []
        cuentas_por_id = datos["cuentas_por_id"]
        entradas = [
            e for e in snapshots_service.get_saldos_anteriores_cuentas(ui["mes"], ui["anio"])
            if e["saldo_minor"] and e["cuenta_id"] in cuentas_por_id
        ]
        for entrada in sorted(entradas, key=lambda e: (cuentas_por_id[e["cuenta_id"]]["nombre"], e["moneda_codigo"])):
            saldo = entrada["saldo_minor"]
            nombre = cuentas_por_id[entrada["cuenta_id"]]["nombre"]
            signo = "-" if saldo < 0 else "+"
            filas.append(FilaPie(
                textos={
                    "concepto": TEXTO_SALDO_ANTERIOR,
                    "banco": nombre,
                    "monto": f"{signo} {amount_display(abs(saldo), entrada['decimales'], entrada['moneda_simbolo'])}",
                    "moneda": entrada["moneda_codigo"],
                },
                positiva=saldo > 0,
                # Mismo valor que la columna: el filtro de Banco (y el de Moneda) las filtra.
                valores_filtro={"banco": nombre, "moneda": entrada["moneda_codigo"]},
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
            "concepto": alta_refs["concepto"].value or "",
            "cuenta_id": alta_refs["cuenta"].id_seleccionado,
            "categoria_id": alta_refs["categoria"].id_seleccionado,
            "tag": alta_refs["tag"].value or "",
            "monto": alta_refs["monto"].texto,
            "fecha": alta_refs["fecha"].value or "",
            "moneda": alta_refs["moneda"].value,
        }

    def _on_cambio_borrador(e=None) -> None:
        # Solo guarda el borrador: el texto ya está en pantalla.
        _guardar_borrador_alta()
        sin_auto_update()

    def _construir_alta() -> FilaAlta:
        borrador = ui["alta"]
        cuenta_inicial = borrador["cuenta_id"] or (opciones_cuenta[0][0] if opciones_cuenta else None)
        categoria_inicial = borrador["categoria_id"] or (opciones_categoria[0][0] if opciones_categoria else None)

        campo_concepto = ft.TextField(
            value=borrador["concepto"], hint_text="EJ: SUPERMERCADO", autofocus=True,
            text_align=ft.TextAlign.CENTER, **estilo_campo(),
        )
        dropdown_moneda = ft.Dropdown(
            options=[], dense=LayoutTokens.CELDA_DENSE, text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            border=ft.InputBorder.NONE, expand=True, text_align=ft.TextAlign.CENTER,
        )

        def _refrescar_monedas(cuenta_id: Optional[str], preferida: Optional[str] = None) -> None:
            cuentas_por_id = datos["cuentas_por_id"]
            saldos = cuentas_por_id[cuenta_id]["saldos"] if cuenta_id and cuenta_id in cuentas_por_id else []
            codigos = [s["moneda_codigo"] for s in saldos] or [MONEDA_DEFAULT]
            dropdown_moneda.options = [ft.dropdown.Option(key=c, text=c) for c in codigos]
            dropdown_moneda.value = preferida if preferida in codigos else codigos[0]

        def _on_cuenta(id_cuenta: Optional[str]) -> None:
            # CampoFiltrable parchea la fila (tabla.pagina_alta) justo después.
            if id_cuenta is not None:
                _refrescar_monedas(id_cuenta)
            _guardar_borrador_alta()

        campo_tag = tabla.campo_tag_alta(borrador.get("tag") or "")
        campo_categoria = tabla.campo_filtrable_alta(
            "categoria", opciones_categoria, lambda id_: _guardar_borrador_alta(),
            placeholder="CATEGORÍA", valor_inicial_id=categoria_inicial,
            on_avanzar=lambda: tabla.enfocar(campo_tag),
        )
        campo_cuenta = tabla.campo_filtrable_alta(
            "banco", opciones_cuenta, _on_cuenta,
            placeholder="BANCO", valor_inicial_id=cuenta_inicial,
            on_avanzar=lambda: tabla.enfocar(campo_categoria.campo_texto),
        )

        # persistir_formula=True (pedido explícito): el campo recuerda la
        # fórmula mientras la fila no se guarde. on_confirmar solo guarda el
        # borrador — la fila entera confirma junta (Enter o ✓).
        campo_monto = CampoMonto(
            tabla.pagina_alta,
            on_confirmar=lambda monto_minor: _guardar_borrador_alta(),
            persistir_formula=True,
            hint_text=HINT_MONTO_ALTA,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            on_avanzar=lambda: tabla.enfocar(campo_fecha),
        )
        sin_borde(campo_monto.control)
        campo_monto.control.text_align = ft.TextAlign.RIGHT
        if borrador["monto"]:
            campo_monto.control.value = borrador["monto"]

        celda_fecha, campo_fecha = tabla.campo_fecha_alta(
            borrador["fecha"] or date.today().isoformat(),
            on_cambio=_guardar_borrador_alta, on_submit=lambda: tabla.enfocar(dropdown_moneda),
        )
        _refrescar_monedas(cuenta_inicial, preferida=borrador["moneda"])
        boton_confirmar = tabla.boton_confirmar_alta("AGREGAR MOVIMIENTO", _confirmar_alta)

        alta_refs.update(
            concepto=campo_concepto, cuenta=campo_cuenta, categoria=campo_categoria, tag=campo_tag,
            monto=campo_monto, fecha=campo_fecha, moneda=dropdown_moneda, boton=boton_confirmar,
        )

        # Enter nunca guarda la fila: cada campo pasa al siguiente (Banco/
        # Categoría vía on_avanzar de CampoFiltrable, Monto vía el de
        # CampoMonto) y en Moneda, el último, Enter o Tab llevan el foco al ✓
        # sin activarlo — ahí Enter (o un click) confirma.
        campo_concepto.on_submit = lambda e: tabla.enfocar(campo_cuenta.campo_texto)
        campo_concepto.on_change = _on_cambio_borrador
        campo_tag.on_submit = lambda e: tabla.enfocar(campo_monto.control)
        campo_tag.on_change = _on_cambio_borrador
        dropdown_moneda.on_select = _on_cambio_borrador
        tabla.tab_a_confirmar(dropdown_moneda)

        return FilaAlta(
            celdas={
                "concepto": campo_concepto,
                "banco": campo_cuenta.control,
                "categoria": campo_categoria.control,
                "tag": campo_tag,
                "monto": campo_monto.control,
                "fecha": celda_fecha,
                "moneda": ft.Row([dropdown_moneda], spacing=0),
            },
            boton=boton_confirmar,
            foco=campo_concepto,
        )

    def _alta_ok() -> None:
        """Una alta terminó bien: la fila de alta vuelve a sus valores default."""
        ui["alta"] = _alta_vacia()
        tabla.alta_ok()

    # --- Guardado de la fila de alta ---

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
            # Tras un alta exitosa `boton` ya no está en pantalla (la fila se
            # reconstruyó): refrescar() lo saltea.
            boton.disabled = False
            tabla.refrescar(boton)

    def _procesar_alta() -> None:
        campo_concepto = alta_refs["concepto"]
        campo_cuenta = alta_refs["cuenta"]
        campo_categoria = alta_refs["categoria"]
        campo_monto = alta_refs["monto"]
        campo_fecha = alta_refs["fecha"]
        dropdown_moneda = alta_refs["moneda"]
        _guardar_borrador_alta()

        if not cuentas_activas:
            _mostrar_error("PRIMERO CARGÁ UNA CUENTA (NO TARJETA DE CRÉDITO) EN CONFIGURACIÓN → CUENTAS.")
            return
        if not categorias:
            _mostrar_error("NO HAY CATEGORÍAS CARGADAS.")
            return
        concepto = (campo_concepto.value or "").strip()
        if not concepto:
            _mostrar_error("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
            return
        try:
            monto_con_signo = float((campo_monto.texto or "").strip().replace(",", "."))
        except ValueError:
            _mostrar_error("EL MONTO NO ES UN NÚMERO VÁLIDO.")
            return
        if monto_con_signo == 0:
            _mostrar_error("EL MONTO NO PUEDE SER 0 — NEGATIVO ES GASTO, POSITIVO ES INGRESO.")
            return
        fecha_str = (campo_fecha.value or "").strip()
        try:
            datetime.strptime(fecha_str, "%Y-%m-%d")
        except ValueError:
            _mostrar_error("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.")
            return
        if not campo_cuenta.id_seleccionado:
            _mostrar_error("SELECCIONÁ UNA CUENTA DE LA LISTA DE SUGERENCIAS.")
            return
        if not campo_categoria.id_seleccionado:
            _mostrar_error("SELECCIONÁ UNA CATEGORÍA DE LA LISTA DE SUGERENCIAS.")
            return
        if not dropdown_moneda.value:
            _mostrar_error("COMPLETÁ LA MONEDA.")
            return

        cuenta_id = campo_cuenta.id_seleccionado
        categoria_id = campo_categoria.id_seleccionado
        moneda_codigo = dropdown_moneda.value

        # "autotransferencia"/"ahorro_inversion" REEMPLAZAN el guardado
        # normal (el diálogo guarda y llama a _alta_ok()); "deuda" lo
        # EXTIENDE (transacción normal primero, después el diálogo).
        routing = mapa_categoria_a_routing.get(campo_categoria.id_seleccionado)
        if routing == "autotransferencia":
            _abrir_dialogo_autotransferencia(cuenta_id, moneda_codigo, monto_con_signo, fecha_str, concepto, categoria_id)
            return
        if routing == "ahorro_inversion":
            _abrir_dialogo_ahorro_inversion(cuenta_id, moneda_codigo, monto_con_signo, fecha_str, concepto)
            return

        try:
            resultado = transaction_service.create(
                date_str=fecha_str,
                concept=concepto,
                account_id=cuenta_id,
                category_id=categoria_id,
                currency_code=moneda_codigo,
                amount=abs(monto_con_signo),
                movement_type="egreso" if monto_con_signo < 0 else "ingreso",
                tag=(alta_refs["tag"].value or "").strip() or None,
            )
        except (TransactionError, ValueError) as err:
            _mostrar_error(str(err))  # la fila queda como estaba para corregir
            return

        if routing == "deuda":
            _abrir_dialogo_deuda(resultado.transaction_id, moneda_codigo, monto_con_signo, fecha_str, concepto)
            return

        _alta_ok()
        _mostrar_ok(f"MOVIMIENTO #{resultado.transaction_id} REGISTRADO.")

    # --- Mini-diálogos de routing especial (misma lógica que antes) ---

    def _abrir_dialogo_autotransferencia(
        cuenta_origen_id: str, moneda_codigo: str, monto: float, fecha_str: str, concepto: str, categoria_id: str,
    ) -> None:
        opciones_destino = [(str(c["id"]), c["nombre"]) for c in cuentas_activas if c["id"] != cuenta_origen_id]
        if not opciones_destino:
            _mostrar_error("NO HAY OTRA CUENTA DISPONIBLE COMO DESTINO PARA LA AUTOTRANSFERENCIA.")
            return
        campo_destino = CampoFiltrable(
            page, opciones_destino, on_seleccionar=lambda id_: None,
            placeholder="CUENTA DESTINO", width=ANCHO_DIALOGO_ROUTING, autofocus=True,
        )

        def _confirmar(e=None) -> None:
            if not campo_destino.id_seleccionado:
                _mostrar_error("SELECCIONÁ LA CUENTA DESTINO DE LA LISTA DE SUGERENCIAS.")
                return
            try:
                resultado = transaction_service.create_transfer(
                    date_str=fecha_str,
                    origin_account_id=cuenta_origen_id,
                    dest_account_id=campo_destino.id_seleccionado,
                    currency_code=moneda_codigo,
                    amount=abs(monto),  # el signo no decide nada: siempre egreso + ingreso
                    category_id=categoria_id,
                    notes=concepto,  # create_transfer() no tiene `concept`
                )
            except (TransactionError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            _alta_ok()
            _mostrar_ok(
                f"AUTOTRANSFERENCIA REGISTRADA (MOVIMIENTOS #{resultado.data['out_transaction_id']} "
                f"→ #{resultado.data['in_transaction_id']})."
            )

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("AUTOTRANSFERENCIA — CUENTA DESTINO"),
            content=ft.Container(width=ANCHO_DIALOGO_ROUTING, content=campo_destino.control),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("CONFIRMAR"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _abrir_dialogo_deuda(transaction_id: str, moneda_codigo: str, monto: float, fecha_str: str, concepto: str) -> None:
        # La transacción YA está guardada: este diálogo solo decide si además
        # se le vincula una deuda. Cancelarlo no la deshace.
        campo_persona = ft.TextField(label="PERSONA / ENTIDAD", width=ANCHO_DIALOGO_ROUTING, autofocus=True)
        campo_vencimiento = ft.TextField(label="FECHA DE VENCIMIENTO (OPCIONAL, AAAA-MM-DD)", width=ANCHO_DIALOGO_ROUTING)
        # Egreso (le diste plata a alguien) → te debe; ingreso (te prestaron) → le debés.
        tab_deuda = "me_deben" if monto < 0 else "debo"
        texto_direccion = "TE DEBE" if tab_deuda == "me_deben" else "LE DEBÉS"

        def _cancelar(e=None) -> None:
            _cerrar_dialogo()
            _alta_ok()
            _mostrar_ok(f"MOVIMIENTO #{transaction_id} REGISTRADO (SIN DEUDA VINCULADA — SE CANCELÓ EL DIÁLOGO).")

        def _confirmar(e=None) -> None:
            persona = (campo_persona.value or "").strip()
            if not persona:
                _mostrar_error("INGRESÁ LA PERSONA/ENTIDAD.")
                return
            due_date = (campo_vencimiento.value or "").strip() or None
            if due_date is not None:
                try:
                    datetime.strptime(due_date, "%Y-%m-%d")
                except ValueError:
                    _mostrar_error("LA FECHA DE VENCIMIENTO DEBE TENER EL FORMATO AAAA-MM-DD.")
                    return
            try:
                moneda = monedas_por_codigo[moneda_codigo]
                resultado_deuda = debts_service.create(
                    entidad_persona=persona,
                    concepto=concepto,
                    tab=tab_deuda,
                    # Positivo: la deuda crece (negativo sería un pago, DebtsService).
                    monto_minor=amount_to_minor(abs(monto), moneda["decimales"]),
                    moneda_id=moneda["id"],
                    fecha=fecha_str,
                    # La tabla de deudas ya no tiene fecha de vencimiento: va en las notas.
                    notas=f"VENCE: {due_date}" if due_date else None,
                    origen_tipo="transaccion",
                    origen_id=transaction_id,
                )
            except (DebtError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            _alta_ok()
            _mostrar_ok(
                f"MOVIMIENTO #{transaction_id} REGISTRADO Y VINCULADO A UNA DEUDA CON "
                f"'{persona}' (#{resultado_deuda.entity_id}) — {texto_direccion}."
            )

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("DEUDA — PERSONA / VENCIMIENTO"),
            content=ft.Container(
                width=ANCHO_DIALOGO_ROUTING,
                content=ft.Column([campo_persona, campo_vencimiento], tight=True, spacing=ESPACIADO),
            ),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cancelar),
                ft.ElevatedButton(content=ft.Text("CONFIRMAR"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _abrir_dialogo_ahorro_inversion(
        cuenta_id: str, moneda_codigo: str, monto: float, fecha_str: str, concepto: str,
    ) -> None:
        # Dos modos en el MISMO AlertDialog: el link "Elegir activo
        # específico" reemplaza contenedor_dialogo.content por el formulario
        # de dialogo_compra_ahorro.py; estado_confirmar indirecciona qué
        # confirma el botón (las actions no se reconstruyen).
        objetivos = savings_service.list_objetivos()
        opciones_objetivo = [(str(o["id"]), o["nombre"]) for o in objetivos] + [
            (_ID_OBJETIVO_NUEVO, "+ CREAR NUEVO OBJETIVO")
        ]
        campo_nombre_nuevo = ft.TextField(
            label="NOMBRE DEL OBJETIVO NUEVO", visible=False, width=ANCHO_DIALOGO_ROUTING, dense=True,
        )

        def _on_objetivo(id_: Optional[str]) -> None:
            campo_nombre_nuevo.visible = (id_ == _ID_OBJETIVO_NUEVO)
            page.update()

        campo_objetivo = CampoFiltrable(
            page, opciones_objetivo, on_seleccionar=_on_objetivo,
            placeholder="OBJETIVO DE AHORRO", width=ANCHO_DIALOGO_ROUTING, autofocus=True,
        )

        def _on_exito(resultado) -> None:
            _cerrar_dialogo()
            _alta_ok()
            _mostrar_ok(f"APORTE A AHORRO REGISTRADO (MOVIMIENTO #{resultado.entity_id}).")

        def _confirmar_modo_simple(e=None) -> None:
            if not campo_objetivo.id_seleccionado:
                _mostrar_error("SELECCIONÁ UN OBJETIVO DE AHORRO DE LA LISTA DE SUGERENCIAS.")
                return
            if campo_objetivo.id_seleccionado == _ID_OBJETIVO_NUEVO:
                nombre_nuevo = (campo_nombre_nuevo.value or "").strip()
                if not nombre_nuevo:
                    _mostrar_error("EL NOMBRE DEL OBJETIVO NUEVO NO PUEDE ESTAR VACÍO.")
                    return
                objetivo_id = savings_service.create_objetivo(nombre=nombre_nuevo).entity_id
            else:
                objetivo_id = campo_objetivo.id_seleccionado
            moneda = monedas_por_codigo.get(moneda_codigo)
            if moneda is None:
                _mostrar_error(f"MONEDA '{moneda_codigo}' NO ENCONTRADA.")
                return
            try:
                activo = savings_service.get_or_create_reserved_cash_asset(cuenta_id=cuenta_id, moneda_id=moneda["id"])
                resultado = savings_service.register_purchase(
                    activo_id=activo.entity_id,
                    fecha=fecha_str,
                    monto_total_minor=amount_to_minor(abs(monto), moneda["decimales"]),  # signo ignorado
                    asignaciones=[{"objetivo_id": objetivo_id, "porcentaje": 100.0}],
                    notas=concepto,
                )
            except (SavingsError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _on_exito(resultado)

        texto_titulo = ft.Text("AHORRO/INVERSIÓN — OBJETIVO")
        estado_confirmar = {"actual": _confirmar_modo_simple}

        def _ir_a_modo_completo(e=None) -> None:
            formulario = dialogo_compra_ahorro.construir(
                page, savings_service, accounts_service,
                on_exito=_on_exito,
                cuenta_id_inicial=cuenta_id, monto_inicial=abs(monto),
                fecha_inicial=fecha_str, notas_inicial=concepto,
            )
            texto_titulo.value = "AHORRO/INVERSIÓN — ACTIVO ESPECÍFICO"
            contenedor_dialogo.width = None  # el formulario trae su propio ancho
            contenedor_dialogo.content = formulario.contenido
            estado_confirmar["actual"] = formulario.confirmar
            page.update()

        contenedor_dialogo = ft.Container(
            width=ANCHO_DIALOGO_ROUTING,
            content=ft.Column(
                [
                    campo_objetivo.control,
                    campo_nombre_nuevo,
                    ft.TextButton(content=ft.Text("ELEGIR ACTIVO ESPECÍFICO"), on_click=_ir_a_modo_completo),
                ],
                tight=True,
                spacing=ESPACIADO,
            ),
        )
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=texto_titulo,
            content=contenedor_dialogo,
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("CONFIRMAR"), on_click=lambda e: estado_confirmar["actual"]()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # FILAS DE DATOS — celdas editables inline (CLAUDE.md §10)
    # ------------------------------------------------------------

    def _guardar(t: dict, **kwargs) -> str:
        transaction_service.update(t["id"], **kwargs)
        return f"MOVIMIENTO #{t['id']} ACTUALIZADO."

    def _construir_celdas(t: dict) -> dict[str, ft.Control]:
        cuenta = datos["cuentas_por_id"].get(t["cuenta_id"])
        decimales = t["decimales"]

        def _guardar_concepto(nuevo: str) -> str:
            if not nuevo.strip():
                raise ValueError("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
            return _guardar(t, concept=nuevo.strip())

        def _guardar_fecha(nuevo: str) -> str:
            try:
                datetime.strptime(nuevo.strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.") from None
            return _guardar(t, date_str=nuevo.strip())

        def _guardar_monto(monto_minor: int) -> str:
            # Se edita con el signo que se ve (− gasto, + ingreso), igual que
            # la fila de alta: un negativo es válido. Lo que todavía no se
            # puede es DAR VUELTA el signo: TransactionService.update() no
            # cambia tipo_movimiento.
            if monto_minor == 0:
                raise ValueError("EL MONTO NO PUEDE SER 0.")
            if (monto_minor < 0) != _es_egreso(t):
                raise ValueError(MENSAJE_SIGNO_NO_EDITABLE)
            # update() recibe el monto (positivo) como float y la moneda junto con él.
            return _guardar(t, amount=abs(monto_minor) / (10 ** decimales), currency_code=t["currency_code"])

        def _guardar_moneda(nuevo_codigo: str) -> str:
            # Mismo importe mostrado en la moneda nueva (monto + moneda juntos).
            return _guardar(t, amount=t["monto_minor"] / (10 ** decimales), currency_code=nuevo_codigo)

        # Moneda: entre las de la cuenta de la fila (+ la actual, si la
        # cuenta ya no la tiene), mismo criterio que la fila de alta.
        opciones_moneda = [(s["moneda_codigo"], s["moneda_codigo"]) for s in (cuenta["saldos"] if cuenta else [])]
        if t["currency_code"] not in [codigo for codigo, _ in opciones_moneda]:
            opciones_moneda.append((t["currency_code"], t["currency_code"]))

        return {
            "concepto": tabla.celda_texto(t, "concepto", t["concepto"], _guardar_concepto),
            "banco": tabla.celda_filtrable(
                t, "banco",
                lambda: banco_con_dot(color_cuenta(cuenta, t["account_name"] or ""), t["account_name"] or ""),
                opciones_cuenta_edicion, str(t["cuenta_id"]),
                lambda id_: _guardar(t, account_id=id_),
            ),
            "categoria": tabla.celda_filtrable(
                t, "categoria", lambda: texto_celda(t["category_name"] or ""),
                opciones_categoria, str(t["categoria_id"]),
                lambda id_: _guardar(t, category_id=id_),
            ),
            # update() borra el tag con "" (lo guarda como NULL).
            "tag": tabla.celda_tag(t, "tag", t["tag"], lambda nuevo: _guardar(t, tag=nuevo.strip())),
            "monto": tabla.celda_monto(
                t, "monto", _monto_con_signo(t), TEXT_NEGATIVO if _es_egreso(t) else TEXT_POSITIVO,
                -t["monto_minor"] if _es_egreso(t) else t["monto_minor"], decimales, _guardar_monto,
            ),
            "fecha": tabla.celda_texto(t, "fecha", t["fecha"], _guardar_fecha),
            "moneda": tabla.celda_dropdown(
                t, "moneda", t["currency_code"] or "", opciones_moneda, t["currency_code"], _guardar_moneda,
                color=TEXT_SECONDARY,
            ),
        }

    def _accion_fila(t: dict) -> Optional[ft.Control]:
        compartido = t["id"] in datos["compartidos"]
        deudas = datos["deudas"].get(t["id"], [])
        tooltip_deuda = (
            "DEUDA VINCULADA: " + ", ".join(d["entidad_persona"].upper() for d in deudas)
            if deudas else "REGISTRAR COMO DEUDA"
        )
        return ft.Row(
            [
                tabla.icono_accion(
                    ft.Icons.HOME, "GASTO COMPARTIDO CON EL HOGAR" if compartido else "COMPARTIR CON EL HOGAR",
                    compartido, lambda: _compartir_con_hogar(t),
                ),
                tabla.icono_accion(ft.Icons.HANDSHAKE, tooltip_deuda, bool(deudas), lambda: _abrir_registrar_deuda(t)),
            ],
            spacing=0,
            tight=True,
        )

    # --- Registrar como deuda (ícono handshake) ---

    def _abrir_registrar_deuda(t: dict) -> None:
        existentes = datos["deudas"].get(t["id"], [])
        decimales = t["decimales"]
        # $ y "¿DE CUÁNTO?" arrancan con el monto del movimiento.
        monto_movimiento = f"{t['monto_minor'] / 10 ** decimales:.2f}"
        campo_persona = ft.TextField(label="PERSONA", width=ANCHO_DIALOGO_ROUTING, autofocus=True)
        dropdown_tab = ft.Dropdown(
            label="TIPO", width=ANCHO_TIPO_DEUDA,
            options=[
                ft.dropdown.Option(key="me_deben", text="ME DEBEN"),
                ft.dropdown.Option(key="debo", text="DEBO"),
            ],
            # Mismo criterio que el routing "Deuda": egreso (pusiste vos) → te deben.
            value="me_deben" if _es_egreso(t) else "debo",
        )
        campo_valor = CampoTipoValor(
            page, orden=(TIPO_MONTO, TIPO_COEFICIENTE, TIPO_PORCENTAJE), decimales=decimales, con_base=True,
            disposicion="dialogo", valor_inicial=monto_movimiento, base_inicial=monto_movimiento,
            # Enter no registra: del último campo pasa al botón (ahí Enter o click registran).
            on_enter=lambda: page.run_task(boton_registrar.focus),
        )

        def _confirmar(e=None) -> None:
            persona = (campo_persona.value or "").strip()
            if not persona:
                _mostrar_error("INGRESÁ LA PERSONA.")
                return
            try:
                monto_minor = campo_valor.monto_minor()
            except ValueError as err:
                _mostrar_error(str(err))
                return
            try:
                resultado = debts_service.create(
                    entidad_persona=persona,
                    concepto=t["concepto"],
                    tab=dropdown_tab.value,
                    monto_minor=monto_minor,
                    moneda_id=t["moneda_id"],
                    fecha=t["fecha"],
                    origen_tipo="transaccion",
                    origen_id=t["id"],
                )
            except (DebtError, ValueError) as err:
                _mostrar_error(str(err))
                return
            except Exception as err:
                # Red de contención para DeudaDuplicadaError (repositories/
                # deudas_repository.py): no se importa desde ui/ (CLAUDE.md
                # §2/§3); su mensaje ya es legible.
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            texto_monto = amount_display(monto_minor, decimales, t["currency_symbol"] or "")
            _mostrar_ok(f"DEUDA #{resultado.entity_id} CON '{persona.upper()}' POR {texto_monto} REGISTRADA.")
            tabla.recargar()

        campo_persona.on_submit = lambda e: page.run_task(campo_valor.campo_foco.focus)
        boton_registrar = ft.ElevatedButton(content=ft.Text("REGISTRAR"), on_click=_confirmar)
        controles: list[ft.Control] = [
            ft.Text(f"MOVIMIENTO: {(t['concepto'] or '').upper()} — {_monto_con_signo(t)} {t['currency_code']}",
                    color=TEXT_SECONDARY),
        ]
        for deuda in existentes:
            direccion = "TE DEBE" if deuda["tab"] == "me_deben" else "LE DEBÉS"
            signo = "-" if deuda["monto_minor"] < 0 else ""
            monto_deuda = signo + amount_display(
                abs(deuda["monto_minor"]), deuda["decimales"], deuda["currency_symbol"] or "",
            )
            controles.append(ft.Text(
                f"YA VINCULADA: {deuda['entidad_persona'].upper()} — {direccion} {monto_deuda}",
                color=TEXT_ACCENT,
            ))
        controles += [campo_persona, dropdown_tab, campo_valor.control]

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("REGISTRAR COMO DEUDA"),
            content=ft.Container(
                width=ANCHO_DIALOGO_ROUTING,
                content=ft.Column(controles, tight=True, spacing=ESPACIADO, scroll=ft.ScrollMode.AUTO),
            ),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                boton_registrar,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # ELIMINAR / COMPARTIR (barra flotante)
    # ------------------------------------------------------------

    def _avisos_eliminar(filas: list[dict]) -> str:
        autotransferencias = origenes_ahorro = 0
        for t in filas:
            avisos = transaction_service.get_delete_warnings(t["id"])
            autotransferencias += bool(avisos["es_autotransferencia"])
            origenes_ahorro += bool(avisos["es_origen_ahorro"])
        partes = []
        if autotransferencias:
            partes.append(f"{autotransferencias} ES PARTE DE UNA AUTOTRANSFERENCIA (LA OTRA PATA NO SE BORRA)")
        if origenes_ahorro:
            partes.append(f"{origenes_ahorro} ES ORIGEN DE UN MOVIMIENTO DE AHORRO (QUEDA SIN VÍNCULO)")
        return " · ".join(partes)

    def _eliminar(filas: list[dict]) -> tuple[str, bool]:
        errores = []
        for t in filas:
            try:
                resultado = transaction_service.delete(t["id"])
            except TransactionError as err:
                errores.append(f"#{t['id']}: {err}")
                continue
            if not resultado.success:
                errores.append(f"#{t['id']}: {resultado.message}")
        eliminadas = len(filas) - len(errores)
        if errores:
            return f"{eliminadas} ELIMINADA(S), {len(errores)} CON ERROR: " + " | ".join(errores), True
        return f"{eliminadas} MOVIMIENTO(S) ELIMINADO(S).", False

    def _es_ingreso(t: dict) -> bool:
        # Mismo criterio que compartir_gasto.py.
        return t["tipo_movimiento"] == "ingreso"

    def _compartir_una(t: dict, hogar_id: str, pagador: str, coeficiente: float) -> None:
        # Ingreso = pago recibido: coeficiente 100% y monto base negativo,
        # igual que el flujo de una fila (compartir_gasto.py).
        shared_expenses_service.add_shared_expense(
            hogar_id=hogar_id,
            pagador=pagador,
            origen_tipo="transaccion",
            origen_id=t["id"],
            categoria_id=t["categoria_id"],
            monto_base_minor=-t["monto_minor"] if _es_ingreso(t) else t["monto_minor"],
            coeficiente_deuda=100.0 if _es_ingreso(t) else coeficiente,
            fecha=t["fecha"],
        )

    def _compartir_con_hogar(t: dict) -> None:
        # El flujo existente (compartir_gasto.py) es un diálogo por
        # transacción: se dispara el on_click (async) de su propio ícono.
        icono, _ = compartir_gasto.build_icon(page, shared_expenses_service, t, tabla.recargar)
        page.run_task(icono.on_click, None)

    def _compartir(filas: list[dict]) -> None:
        if len({t["currency_code"] for t in filas}) > 1:
            tabla.aviso_barra("SELECCIONÁ SOLO TRANSACCIONES DE LA MISMA MONEDA PARA COMPARTIR EN LOTE.")
            return
        if len(filas) == 1:
            _compartir_con_hogar(filas[0])
            return
        pendientes = [t for t in filas if t["id"] not in datos["compartidos"]]
        if not pendientes:
            tabla.aviso_barra("LOS MOVIMIENTOS SELECCIONADOS YA ESTÁN COMPARTIDOS.")
            return
        avisos = []
        ya_compartidos = len(filas) - len(pendientes)
        if ya_compartidos:
            avisos.append(f"{ya_compartidos} YA ESTABA(N) COMPARTIDO(S) Y SE SALTEA(N).")
        ingresos = sum(1 for t in pendientes if _es_ingreso(t))
        if ingresos:
            avisos.append(f"{ingresos} ES/SON INGRESO(S): SE REGISTRA(N) COMO PAGO RECIBIDO (COEFICIENTE 100%).")
        page.run_task(
            abrir_compartir_varios, page, shared_expenses_service,
            f"COMPARTIR {len(pendientes)} MOVIMIENTOS", pendientes, _compartir_una, avisos, tabla.recargar,
            (), FORMATO_UNIDAD, ingresos < len(pendientes),  # coeficiente 0-1, como compartir_gasto.py
        )

    # ------------------------------------------------------------
    # ARMADO
    # ------------------------------------------------------------

    tabla = TablaPlanilla(
        page,
        clave="registro",
        columnas=COLUMNAS,
        pref_anchos=PREF_ANCHOS_COLUMNAS,
        cargar_filas=_cargar_transacciones,
        construir_celdas=_construir_celdas,
        construir_alta=_construir_alta,
        firma=_firma,
        valor_columna=_valor_columna,
        clave_orden=_clave_orden,
        texto_busqueda=lambda t: t["concepto"] or "",
        accion_fila=_accion_fila,
        on_eliminar=_eliminar,
        avisos_eliminar=_avisos_eliminar,
        on_compartir=_compartir,
        al_recargar=_al_recargar,
        filas_pie=_filas_pie,
        errores_esperados=(TransactionError,),
        texto_vacio="NO HAY MOVIMIENTOS PARA MOSTRAR.",
        # monto_minor se guarda en positivo: el signo sale de tipo_movimiento (− gasto, + ingreso).
        columna_suma="monto_minor",
        signo_suma=lambda t: -1 if _es_egreso(t) else 1,
    )
    control_tabla = tabla.construir()
    contenedor_saldos.content = _barra_saldos()

    def _al_cambiar_periodo() -> None:
        # Una selección de otro mes quedaría invisible: se descarta.
        estado["mes"], estado["anio"] = ui["mes"], ui["anio"]
        tabla.recargar(limpiar_seleccion=True)

    # El indicador de sync va al extremo derecho, en la MISMA fila que el
    # buscador y el selector de mes: en una fila aparte, con la ventana
    # angosta la barra de título se desbordaba por debajo del indicador.
    titulo = barra_titulo(
        page, "REGISTRO DE TRANSACCIONES", tabla, ui, _al_cambiar_periodo, "BUSCAR EN EL REGISTRO…",
        acciones=[_indicador_sync(page)],
    )
    return pantalla_planilla([titulo, contenedor_saldos, control_tabla])


def _indicador_sync(page: ft.Page) -> ft.Control:
    """
    Estado de la sincronización con Supabase (sync/sync_engine.py, el motor
    que registró ui/app.py), compacto: ícono de nube + "SINC" — verde
    CLOUD_DONE sincronizado, amarillo CLOUD_SYNC sincronizando, rojo
    CLOUD_OFF sin conexión (también trabajando sin sesión). Tooltip de una
    línea: el estado, la hora de la última sincronización y, si la hubo, la
    cantidad de errores. Click: fuerza una sincronización completa en otro
    hilo. El motor avisa cada cambio de estado desde el hilo de la sync: el
    repintado pasa a la UI con page.run_task(). El oyente se registra con una
    clave fija ("registro"): cuando el Registro se reconstruye, el indicador
    nuevo reemplaza al viejo.
    """
    motor = motor_registrado()
    icono = ft.Icon(ft.Icons.CLOUD_OFF, size=TAMANIO_ICONO_SYNC)  # _pintar() pone el del estado
    texto = ft.Text(TEXTO_INDICADOR_SYNC, size=TypographyTokens.REGISTRO_FONT_SALDO_BAR, weight=ft.FontWeight.W_500)
    indicador = ft.Container(
        height=ALTURA_INDICADOR,
        padding=ft.Padding.symmetric(horizontal=PADDING_INDICADOR_H),
        bgcolor=BG_SUPERFICIE,
        border=ft.Border.all(1, BORDER_DEFAULT),
        border_radius=RADIO_INDICADOR,
        alignment=ft.Alignment.CENTER,
        content=ft.Row(
            [icono, texto], spacing=ESPACIO_ICONO_SYNC, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    def _estado() -> str:
        return motor.estado if motor is not None and motor.hay_sesion() else ESTADO_SIN_SESION

    def _tooltip(estado: str, etiqueta: str) -> str:
        partes = [etiqueta]
        if estado in (ESTADO_SINCRONIZADO, ESTADO_SIN_CONEXION) and motor.ultima_sync is not None:
            partes.append(f"{motor.ultima_sync:%H:%M}")
        resultado = motor.ultimo_resultado if motor is not None else None
        if estado == ESTADO_SINCRONIZADO and resultado is not None and resultado.errores:
            partes.append(f"{resultado.errores} ERROR(ES)")
        return " · ".join(partes)

    def _pintar() -> None:
        estado = _estado()
        nube, color, etiqueta = ESTADOS_INDICADOR_SYNC.get(estado, ESTADOS_INDICADOR_SYNC[ESTADO_SIN_CONEXION])
        icono.icon = nube
        icono.color = color
        texto.color = color
        indicador.tooltip = _tooltip(estado, etiqueta)

    async def _repintar() -> None:
        _pintar()
        refrescar(page, indicador)

    def _al_cambiar(_motor: SyncEngine) -> None:
        page.run_task(_repintar)  # llega desde el hilo de la sync

    def _forzar(e=None) -> None:
        if motor is None or not motor.hay_sesion():
            mostrar_mensaje(page, "INICIÁ SESIÓN DESDE LA BARRA LATERAL PARA SINCRONIZAR.", es_error=True)
            return
        page.run_thread(motor.sync_completo)  # el motor avisa SINCRONIZANDO… y el resultado
        sin_auto_update()

    indicador.on_click = _forzar
    if motor is not None:
        motor.escuchar("registro", _al_cambiar)
    _pintar()
    return indicador
