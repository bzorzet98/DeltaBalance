"""
DeltaBalance — ui/screens/compras_cuotas.py

Compras y movimientos en cuotas: barra de título (búsqueda + período) →
barra "TOTAL A PAGAR POR TARJETA" → tabla (TablaPlanilla,
ui/components/tabla_planilla.py) con fila de alta, edición inline y barra
flotante de selección (eliminar / compartir). Misma tabla, misma cabecera y
misma paleta (ui/theme/tabla_tokens.py) que el Registro de transacciones:
las dos pantallas solo difieren en sus columnas y en su lógica de datos.

Reglas de arquitectura: solo AccountsService/CategoriasService/FeesService/
SharedExpensesService — nunca repositories/ ni db/ directo (CLAUDE.md
§2/§3). amount_minor para add_extra_charge() se calcula acá con
utils.money.amount_to_minor().

--- Estado propio de la pantalla ---

Período y moneda de la barra de totales viven en un almacén por página
(_ESTADOS_UI); el estado de la tabla (búsqueda, filtros, orden, selección,
anchos — clave "compras_anchos_columnas" en prefs) lo guarda TablaPlanilla.
La tabla muestra las compras con al menos una cuota que VENCE en el mes
elegido (FeesService.list_purchases_due_in_month(), por
cuotas_credito.mes_proyectado/anio_proyectado — no por fecha de compra), y
en Monto la cuota de ese mes: lo que se cobra ese mes por cada compra.
Cuotas muestra cuál vence ("3/6"). Junto a las compras, los cargos extra
del resumen de ese mes de cada tarjeta (ver "Cargos extra en la tabla").

--- Barra "TOTAL A PAGAR POR TARJETA" ---

Reemplaza a la tarjeta de desglose anterior, con el formato de la barra
de saldo del Registro: un chip por tarjeta de crédito activa (más las
archivadas que tengan algo a pagar ese mes) con el total del período según
FeesService.resumen_por_tarjeta() — cuotas que vencen ese mes + cargos
extra del resumen — en la moneda elegida con las pills de la derecha.
El total neto (compras − reintegros) se muestra siempre sin signo (valor
absoluto). Verde si no queda nada a pagar (0 o a favor), color neutro si
hay monto.
La sección "Cargos extra de este resumen" que se abría desde esa tarjeta
se sacó (pedido explícito): los cargos se siguen cargando desde la fila de
alta con las categorías especiales, y cuentan en el total de la barra —
cada uno en su moneda, también en una tarjeta sin cuotas ese mes (que
aparece en la barra solo por sus cargos).

Antes del ⚙, el botón de recibo muestra u oculta los cargos extra en la
tabla (ui["mostrar_cargos"], por página; el total de la barra los cuenta
siempre).

⚙ al final de la barra: panel de configuración de tarjetas. Por cada
tarjeta de crédito activa, día de cierre y día de vencimiento DEFAULT
(FeesService.set_card_config(), tabla tarjetas_config; GUARDAR) y, debajo,
sus resúmenes ANTERIOR / ACTUAL / PRÓXIMO (FeesService.card_cycle_periods():
ACTUAL es el último que cerró) con la fecha de cierre y la de vencimiento
de cada uno. Cada fecha se edita con un click (✎, formato DD/MM/AAAA) y se
guarda al confirmar (Enter o al salir) como la fecha REAL de ese resumen
(FeesService.set_fechas_resumen(), tabla tarjetas_resumenes); vacía, vuelve
a la calculada desde el día default. Una fecha real se ve en color de
acento; una calculada, apagada. La 1ª cuota sugerida usa las fechas reales
(FeesService.suggest_first_fee()). Una tarjeta con los dos días default
vacíos se deja como está; con uno solo, es un error; sin días default no
tiene resúmenes que mostrar.

--- Fila de alta ---

Concepto, Tarjeta y Categoría (CampoFiltrable, sugerencias flotantes), Tag
(opcional, texto libre: TablaPlanilla.campo_tag_alta()), Monto (CampoMonto,
persistir_formula=True), Cuotas (default 1), 1ª cuota, Fecha y
Moneda (las monedas de la tarjeta elegida). Enter nunca guarda la fila
salvo con el foco en el ✓: cada campo pasa al siguiente (igual que Tab) y
en Moneda, el último, Enter o Tab llevan el foco al ✓ sin activarlo; ahí
Enter o un click confirman (TablaPlanilla.tab_a_confirmar()). Al guardar,
la fila se reconstruye CON LOS MISMOS VALORES y el foco en Concepto
(pedido explícito: para cargar varias compras seguidas editando solo
algunos campos). Si una categoría especial quedó elegida, el próximo alta
también es un cargo extra — Cuotas deshabilitado lo deja a la vista.

BORRADOR de la fila de alta (en _ESTADOS_UI, como el Registro y Deudas):
cada campo se guarda en cuanto cambia, así una reconstrucción de la
pantalla (ui/app.py: la sync periódica que bajó filas, o volver a la
pantalla con datos nuevos) no vacía lo que ya estaba tipeado — antes esta
fila no tenía borrador y se perdía todo. La 1ª cuota se restaura solo si
se había elegido a mano; si no, se vuelve a sugerir. Sin borrador, la
categoría default es la primera NO especial.

La tabla es del mes en que vencen las cuotas: una compra cuya 1ª cuota
vence en otro mes no aparece en el mes que se está viendo; el mensaje de
OK lo aclara.

1ª cuota (MM/AAAA): el mes de la cuota 1 — FeesService.create_purchase()
arma el cronograma desde ahí. La sugiere FeesService.suggest_first_fee()
(el mes siguiente a la compra; dos meses después si la tarjeta tiene día de
cierre y la compra es posterior) y se vuelve a sugerir al cambiar la
tarjeta o la fecha, hasta que se edita a mano (tipeando o con ◀ ▶: a partir
de ahí queda la elegida). Cualquier mes vale, también uno anterior al de
la compra (pedido explícito: es responsabilidad del usuario). No aplica a
un cargo extra (se deshabilita como Cuotas).

Routing por CATEGORÍA al confirmar (sin cambios):
- Categoría NORMAL: FeesService.create_purchase() con el monto, la
  cantidad de cuotas y el tag (vacío = None) tal cual se cargaron. El monto
  DEBE ser positivo.
- Categoría ESPECIAL ("Impuesto tarjeta"/"Recargo tarjeta"/
  "Ajuste/Reintegro tarjeta", services/fees_service.py
  CATEGORIAS_CARGO_EXTRA): NO crea una compra — resuelve el resumen de esa
  tarjeta y el mes/año de la fecha (FeesService.open_statement(),
  idempotente) y carga un cargo extra (add_extra_charge()) con el monto CON
  su signo, la moneda y la fecha de la fila (docs/DATA_MODEL_DECISIONS.md
  sección 28). Cuotas se deshabilita al elegir una de estas categorías. El
  tag no se usa: los cargos extra no tienen.

--- Filas ---

Devoluciones y reintegros (monto_total_minor negativo, típicamente
migrados desde el Excel) son filas normales: el monto se muestra "−" en
TEXT_NEGATIVO; las compras (monto_total_minor positivo, lo que se paga),
"+" en TEXT_POSITIVO (pedido explícito).

Edición inline (CLAUDE.md §10): todas las columnas — Concepto, Banco,
Categoría, Tag (TablaPlanilla.celda_tag(): vacía la borra), Monto (la
cuota del mes), Cuotas, Fecha y Moneda (entre las monedas de la
tarjeta de la fila; el service mantiene el importe mostrado) —, todas vía
FeesService.update_purchase() salvo Cuotas (update_purchase_cuotas(),
regenera el cronograma con el mismo total). La cuota del mes se edita con
el signo que se ve (+ compra, − reintegro): un negativo es válido y el
signo se puede dar vuelta; el nuevo total es cuota × cantidad de cuotas
(update_purchase() acepta totales negativos). Solo mientras TODAS las
cuotas siguen pendientes; si vence más de una este mes, no se edita desde
acá. Qué se puede corregir lo decide el service (CLAUDE.md
§4): si rechaza, su FeesError se muestra tal cual y la celda vuelve al
valor anterior. Reglas replicadas en la UI:
- Cuotas y Monto se muestran bloqueadas, con el motivo en el
  tooltip, si alguna cuota ya no está 'pendiente' (pedido explícito).
- Una compra cancelada se muestra atenuada, con todas sus celdas de solo
  lectura y un ícono en la columna de acción.
El selector de Categoría inline excluye las 3 categorías especiales (una
compra ya cargada no puede convertirse en cargo extra).

--- Cargos extra en la tabla ---

Los impuestos, recargos y ajustes/reintegros cargados con las categorías
especiales de TARJETA DE CRÉDITO son filas de compras_cuotas con
es_cargo_extra = 1 (docs/DATA_MODEL_DECISIONS.md sección 28): llegan con
las compras del mes (list_purchases_due_in_month(), su cuota cae en el mes
de su resumen) y se muestran como filas propias (pedido explícito):
Categoría = la categoría especial; Monto con su signo (+ a pagar, − a
favor), como una compra; Cuotas y 1ª cuota "—"; ícono de recibo en la
columna de acción. Solo lectura — sin cronograma ni edición inline —;
entran en la Σ de la barra flotante y se eliminan desde ahí
(FeesService.delete_extra_charge(), solo con su resumen abierto).
Compartir los saltea. El botón de recibo de la barra los oculta.

1ª cuota inline (MM/AAAA): rearma el cronograma desde ese mes con la misma
cantidad de cuotas — la cuota N queda en 1ª + (N − 1) meses, vía
FeesService.reschedule_fees() (las que se habían corrido a mano vuelven a
meses seguidos, igual que al cambiar Cuotas). Cualquier mes vale, también
uno anterior al de la compra. Editable solo mientras TODAS las cuotas
siguen 'pendiente' (como Cuotas); si no, se ajusta cuota por cuota desde el
cronograma (📅). Una cuota compartida por separado: el service rechaza y su
FeesError se muestra en la celda.

Barra flotante: además de eliminar / compartir, la suma (Σ) de la cuota del
mes (lo que muestra la columna Monto) de las compras seleccionadas, con su
signo y una por moneda.

Columna de acción: 📅 (TablaPlanilla.icono_accion(), visible al pasar el
mouse) abre el cronograma de la compra — cada cuota con su mes y su
estado; las pendientes con ◀ ▶ para correrlas un mes, las demás en gris y
sin botones. Dos cuotas en el mismo mes: aviso en el mismo panel y
GUARDAR deshabilitado. GUARDAR llama a FeesService.reschedule_fees(), que
repite las reglas (solo pendientes, no compartidas una por una, sin
repetir mes) y, si rechaza, su FeesError se muestra en el panel. Al lado,
el ícono de compra compartida, como antes.

--- Eliminar / compartir (barra flotante) ---

Eliminar intenta primero FeesService.delete_purchase() — borrado físico,
solo si ninguna cuota entró en un resumen o se pagó y nada depende de la
compra (compartida, deuda vinculada; ventana de corrección temprana,
CLAUDE.md §4), el caso típico de una compra cargada por error. Si el
service lo rechaza, la compra se cancela (cancel_purchase(): cuotas
pendientes → 'omitido', las ya en un resumen no se tocan) y queda en la
tabla atenuada. Compartir: con UNA compra, el flujo de siempre de
ui/components/compartir_compra.py (según el modo_deuda que ya tiene
guardado); con varias, ui/components/compartir_varios.py — mismo hogar y
mismo reparto [%] [0.XX] [$] para todas (add_shared_purchase(), cada una
en su modo_deuda; con $, el monto fijo es sobre el total de cada compra),
salteando las canceladas y las ya compartidas (total o parcialmente).

--- Sin confirmar corriendo la app ---

Todo lo de la tabla está listado en el docstring de tabla_planilla.py.
Propio de acá: page.run_task() sobre el on_click async del ícono de
compartir_compra.py; que las ◀ ▶ de la 1ª cuota (Containers con on_click,
sin ink) no roben el foco del Tab; y el ancho de la columna 1ª CUOTA (110
px) para ◀ MM/AAAA ▶.
"""

from datetime import date, datetime
from typing import Any, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.fees_service import CATEGORIAS_CARGO_EXTRA, FeesError, FeesService
from services.shared_expenses_service import SharedExpensesService
from ui.components import compartir_compra
from ui.components.campo_monto import CampoMonto
from ui.components.compartir_varios import abrir_compartir_varios
from ui.components.tabla_planilla import (
    ANCHO_ICONO_ACCION,
    EXTRA_AJUSTE_DOT,
    ChipResumen,
    Columna,
    FilaAlta,
    TablaPlanilla,
    banco_con_dot,
    barra_resumen,
    barra_titulo,
    color_cuenta,
    estilo_campo,
    mostrar_mensaje,
    pantalla_planilla,
    sin_auto_update,
    sin_borde,
    texto_celda,
)
from ui.theme.tabla_tokens import (
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_NEGATIVO,
    TEXT_POSITIVO,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.theme.tokens import LayoutTokens, TypographyTokens
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---

# Concepto/Banco/Categoría/Tag redimensionables (proporción inicial); el
# resto, ancho fijo en px.
COLUMNAS = [
    Columna("concepto", "CONCEPTO", 200),
    Columna("banco", "BANCO", 130, extra_ajuste=EXTRA_AJUSTE_DOT),
    Columna("categoria", "CATEGORÍA", 140),
    Columna("tag", "TAG", 100),
    # Monto: la cuota que vence en el mes elegido (lo que se cobra ese mes), no el total.
    Columna("monto", "MONTO", 120, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
    Columna("cuotas", "CUOTAS", 70, redimensionable=False),
    # Mes de la cuota 1 (MM/AAAA): en el alta se elige; en las filas, solo lectura.
    Columna("primera", "1ª CUOTA", 110, redimensionable=False),
    Columna("fecha", "FECHA", 110, redimensionable=False),
    Columna("moneda", "MONEDA", 80, redimensionable=False),
]
PREF_ANCHOS_COLUMNAS = "compras_anchos_columnas"
ICONO_ACCION = 14
MONEDA_DEFAULT = "ARS"
HINT_MONTO_ALTA = "± MONTO"
TOOLTIP_CUOTAS_BLOQUEADAS = "NO SE PUEDE MODIFICAR: HAY CUOTAS YA PROCESADAS"
TOOLTIP_VARIAS_CUOTAS = "VENCE MÁS DE UNA CUOTA DE ESTA COMPRA ESTE MES: EL MONTO NO SE EDITA DESDE ACÁ"
TOOLTIP_CANCELADA = "COMPRA CANCELADA"
TOOLTIP_CRONOGRAMA = "EDITAR CRONOGRAMA DE CUOTAS"
TOOLTIP_CONFIG_TARJETAS = "CONFIGURAR TARJETAS (CIERRE Y VENCIMIENTO)"

# Cargos extra en la tabla (ver docstring del módulo).
TEXTO_SIN_CUOTAS = "—"  # Cuotas / 1ª cuota de un cargo extra
TOOLTIP_CARGO = "CARGO EXTRA DEL RESUMEN"
TOOLTIP_OCULTAR_CARGOS = "OCULTAR LOS CARGOS EXTRA DE LA TABLA (EL TOTAL LOS SIGUE CONTANDO)"
TOOLTIP_MOSTRAR_CARGOS = "MOSTRAR LOS CARGOS EXTRA EN LA TABLA"

# 1ª cuota en la fila de alta: ◀ campo MM/AAAA ▶.
HINT_PRIMERA_CUOTA = "MM/AAAA"
ANCHO_FLECHA_PERIODO = 18
ICONO_FLECHA_PERIODO = 16
DIGITOS_ANIO = 4

# Paneles (AlertDialog): configuración de tarjetas y cronograma de una compra.
DIA_MINIMO, DIA_MAXIMO = 1, 31  # días de cierre / vencimiento (mismo rango que el CHECK de tarjetas_config)
ANCHO_DIALOGO_TARJETAS = 640
ANCHO_CAMPO_DIA = 130  # entra el label "CIERRE DEFAULT"
ANCHO_DIALOGO_CRONOGRAMA = 480
ANCHO_TEXTO_CUOTA = 80
ANCHO_TEXTO_PERIODO = 70
ICONO_ESTADO_CUOTA = 16
ESPACIADO_DIALOGO = 8
ESPACIADO_SECCION_DIALOGO = 16
TAMANIO_TEXTO_DIALOGO = TypographyTokens.REGISTRO_FONT_CELDA
PESO_TITULO_BLOQUE = ft.FontWeight.W_600

# Estado de una cuota en el cronograma: (texto, ícono). Solo 'pendiente' se puede mover.
ESTADOS_CUOTA = {
    "pendiente": ("PENDIENTE", ft.Icons.HOURGLASS_EMPTY),
    "en_resumen": ("EN RESUMEN", ft.Icons.RECEIPT_LONG),
    "pagado": ("PAGADA", ft.Icons.CHECK_CIRCLE),
    "omitido": ("OMITIDA", ft.Icons.BLOCK),
}
# Resúmenes del panel de tarjetas: clave de FeesService.card_cycle_periods() y etiqueta.
RESUMENES_TARJETA = (("anterior", "ANTERIOR"), ("actual", "ACTUAL"), ("proximo", "PRÓXIMO"))
# Fechas de cada resumen en el panel: texto (o campo) + ✎, y su columna.
ANCHO_ETIQUETA_RESUMEN = 90
ANCHO_FECHA_RESUMEN = 130
ALTURA_FECHA_RESUMEN = LayoutTokens.ALTURA_FILA_TABLA
ICONO_EDITAR_FECHA = 14
ESPACIO_ICONO_FECHA = 4
FORMATO_FECHA_RESUMEN = "%d/%m/%Y"
HINT_FECHA_RESUMEN = "DD/MM/AAAA"
TOOLTIP_FECHA_REAL = "FECHA REAL DE ESTE RESUMEN (VACÍA = VUELVE A LA CALCULADA)"
TOOLTIP_FECHA_CALCULADA = "CALCULADA DESDE EL DÍA DEFAULT — CLICK PARA CARGAR LA REAL"


# ============================================================
# ESTADO PROPIO POR PÁGINA (ver docstring del módulo)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia() -> dict:
    """Borrador de la fila de alta (ver docstring, "BORRADOR"): fecha vacía = hoy; ids None = el default."""
    return {
        "concepto": "", "tarjeta_id": None, "categoria_id": None, "tag": "", "monto": "", "cuotas": "1",
        "primera": "", "primera_manual": False, "fecha": "", "moneda": None,
    }


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {
            "mes": hoy.month, "anio": hoy.year, "moneda_resumen": MONEDA_DEFAULT, "alta": _alta_vacia(),
            "mostrar_cargos": True,
        }
        _ESTADOS_UI[id(page)] = ui
    return ui


# ============================================================
# PERÍODOS MM/AAAA (1ª cuota y cronograma)
# ============================================================

def _texto_periodo(mes: int, anio: int) -> str:
    return f"{mes:02d}/{anio:04d}"


def _leer_periodo(texto: Optional[str]) -> Optional[tuple[int, int]]:
    """'MM/AAAA' (o 'M/AAAA') → (mes, anio), o None si no es válido."""
    partes = (texto or "").strip().split("/")
    if len(partes) != 2 or not partes[0].strip().isdigit():
        return None
    anio_texto = partes[1].strip()
    if len(anio_texto) != DIGITOS_ANIO or not anio_texto.isdigit():
        return None
    mes = int(partes[0])
    return (mes, int(anio_texto)) if 1 <= mes <= 12 else None


def _correr_periodo(mes: int, anio: int, meses: int) -> tuple[int, int]:
    """(mes, anio) corrido `meses` (negativo = para atrás)."""
    indice = anio * 12 + (mes - 1) + meses
    return indice % 12 + 1, indice // 12


def _fecha_corta(fecha_iso: str) -> str:
    """'2026-08-15' → '15/08/2026'."""
    return f"{fecha_iso[8:10]}/{fecha_iso[5:7]}/{fecha_iso[:4]}"


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    fees_service: FeesService,
    shared_expenses_service: SharedExpensesService,
) -> ft.Control:
    ui = _estado_ui(page)
    ui.setdefault("mostrar_cargos", True)  # un almacén creado antes de que existiera la clave

    def _mostrar_error(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje)

    # ------------------------------------------------------------
    # DATOS
    # ------------------------------------------------------------

    cuentas_por_id = {c["id"]: c for c in accounts_service.list_accounts(solo_activas=False)}
    # Compras en cuotas es exclusivamente sobre tarjetas de crédito.
    tarjetas_activas = sorted(
        (c for c in accounts_service.list_accounts(solo_activas=True) if c["tipo"] == "credito"),
        key=lambda c: c["nombre"],
    )
    categorias = [dict(c) for c in categorias_service.list_categories(tipo="egreso")]
    monedas_por_codigo = {m["codigo"]: dict(m) for m in accounts_service.list_currencies()}
    # id REAL (varía entre bases) de cada categoría especial → charge_type.
    mapa_categoria_a_charge_type: dict[str, str] = {
        str(c["id"]): CATEGORIAS_CARGO_EXTRA[(c["categoria_principal"], c["subcategoria"])]
        for c in categorias
        if (c["categoria_principal"], c["subcategoria"]) in CATEGORIAS_CARGO_EXTRA
    }
    opciones_tarjeta = [(str(c["id"]), c["nombre"]) for c in tarjetas_activas]
    opciones_categoria_alta = [(str(c["id"]), c["subcategoria"]) for c in categorias]
    # Inline: sin las 3 especiales (una compra ya cargada no se vuelve cargo extra).
    opciones_categoria_edicion = [
        (str(c["id"]), c["subcategoria"]) for c in categorias if str(c["id"]) not in mapa_categoria_a_charge_type
    ]

    def _es_cargo(fila: dict) -> bool:
        """Cargo extra del resumen (compras_cuotas.es_cargo_extra = 1), no una compra (ver docstring)."""
        return bool(fila.get("es_cargo_extra"))

    def _cargar_filas() -> list[dict]:
        """
        Compras con al menos una cuota que VENCE en el mes elegido, con el
        monto de esa cuota (FeesService.list_purchases_due_in_month() —
        antes era la fecha de compra), incluidos los cargos extra de ese mes
        salvo que estén ocultos (ui["mostrar_cargos"]). A cada compra se le
        agrega el estado de TODAS sus cuotas y si está compartida; un cargo
        extra no tiene cronograma ni se comparte, así que va con esas claves
        apagadas (las usan _firma() y las celdas).
        """
        filas = fees_service.list_purchases_due_in_month(ui["mes"], ui["anio"])  # ya son dicts (CLAUDE.md §11)
        if not ui["mostrar_cargos"]:
            filas = [f for f in filas if not _es_cargo(f)]
        for compra in filas:
            if _es_cargo(compra):
                compra.update(todas_pendientes=False, procesada=False, primera_cuota=None, compartida=False)
                continue
            cuotas = fees_service.get_fees_for_purchase(compra["id"])  # por numero_cuota
            estados = [q["estado"] for q in cuotas]
            compra["todas_pendientes"] = bool(estados) and all(e == "pendiente" for e in estados)
            compra["procesada"] = any(e in ("en_resumen", "pagado") for e in estados)
            compra["primera_cuota"] = (cuotas[0]["mes_proyectado"], cuotas[0]["anio_proyectado"]) if cuotas else None
            _, compra["compartida"] = compartir_compra.build_icon(
                page, shared_expenses_service, fees_service, compra, lambda: None,
            )
        return filas

    def _moneda(compra: dict) -> dict:
        return monedas_por_codigo.get(compra["currency_code"], {})

    def _es_reintegro(compra: dict) -> bool:
        return compra["monto_total_minor"] < 0

    def _monto_texto(compra: dict) -> str:
        # La cuota del mes. Compra = lo que se paga (+); reintegro/devolución (−).
        signo = "-" if _es_reintegro(compra) else "+"
        cuota = abs(compra["monto_cuota_mes_minor"])
        return f"{signo} {amount_display(cuota, compra['decimales'], _moneda(compra).get('simbolo') or '')}"

    def _texto_cuotas(compra: dict) -> str:
        """Qué cuota vence este mes, de cuántas: "3/6" (o "2+3/6" si vence más de una)."""
        numeros = "+".join(str(n) for n in compra["numeros_cuota_mes"])
        return f"{numeros}/{compra['total_cuotas']}"

    def _valor_columna(compra: dict, columna: str) -> str:
        """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
        if columna == "concepto":
            return compra["concepto"] or ""
        if columna == "banco":
            return compra["account_name"] or ""
        if columna == "categoria":
            return compra["category_name"] or ""
        if columna == "tag":
            return compra["tag"] or ""
        if columna == "monto":
            return _monto_texto(compra)
        if columna in ("cuotas", "primera") and _es_cargo(compra):
            return TEXTO_SIN_CUOTAS
        if columna == "cuotas":
            return _texto_cuotas(compra)
        if columna == "primera":
            return _texto_periodo(*compra["primera_cuota"]) if compra["primera_cuota"] else ""
        if columna == "fecha":
            return compra["fecha_compra"] or ""
        return compra["currency_code"] or ""

    def _clave_orden(compra: dict, columna: str) -> Any:
        if columna == "monto":
            return compra["monto_cuota_mes_minor"] / (10 ** compra["decimales"])
        if columna == "cuotas":
            return (compra["numeros_cuota_mes"][0], compra["total_cuotas"])
        if columna == "primera":
            mes, anio = compra["primera_cuota"] or (0, 0)
            return (anio, mes)
        return _valor_columna(compra, columna).lower()

    def _firma(compra: dict) -> tuple:
        """Todo lo que la fila muestra: si no cambió, la fila cacheada se reusa tal cual."""
        return (
            compra["concepto"], compra["cuenta_id"], compra["account_name"], compra["categoria_id"],
            compra["category_name"], compra["tag"], compra["monto_total_minor"], compra["monto_cuota_mes_minor"],
            tuple(compra["numeros_cuota_mes"]), compra["total_cuotas"], compra["fecha_compra"],
            compra["currency_code"], compra["decimales"], compra["estado"], compra["todas_pendientes"],
            compra["procesada"], compra["compartida"], compra["primera_cuota"], compra["es_cargo_extra"],
        )

    # ------------------------------------------------------------
    # BARRA "TOTAL A PAGAR POR TARJETA"
    # ------------------------------------------------------------

    contenedor_totales = ft.Container()

    def _barra_totales() -> ft.Control:
        codigo_sel = ui["moneda_resumen"]
        moneda_sel = monedas_por_codigo.get(codigo_sel, {})
        resumen = fees_service.resumen_por_tarjeta(ui["mes"], ui["anio"])
        totales: dict[int, int] = {}
        # monto_total_minor viene con signo: las compras (+) suman y los
        # reintegros/devoluciones (−) restan. El chip muestra el neto sin signo.
        for fila in resumen:
            if fila["currency_code"] == codigo_sel:
                totales[fila["cuenta_id"]] = totales.get(fila["cuenta_id"], 0) + fila["monto_total_minor"]
        ids_activas = {c["id"] for c in tarjetas_activas}
        tarjetas = tarjetas_activas + [
            cuentas_por_id[i] for i in sorted(totales) if i not in ids_activas and i in cuentas_por_id
        ]
        chips = []
        for tarjeta in tarjetas:
            total = totales.get(tarjeta["id"], 0)
            chips.append(ChipResumen(
                color=color_cuenta(tarjeta, tarjeta["nombre"]),
                nombre=tarjeta["nombre"],
                monto=amount_display(abs(total), moneda_sel.get("decimales", 2), moneda_sel.get("simbolo") or ""),
                color_monto=TEXT_POSITIVO if total <= 0 else TEXT_PRIMARY,
                moneda=codigo_sel,
            ))
        return barra_resumen(
            "TOTAL A PAGAR POR TARJETA", ft.Icons.CREDIT_CARD, chips,
            [fila["currency_code"] for fila in resumen], codigo_sel,
            on_moneda=_elegir_moneda_resumen, texto_vacio="NO HAY TARJETAS DE CRÉDITO CARGADAS.",
            acciones=[boton_cargos, boton_config_tarjetas],
        )

    def _elegir_moneda_resumen(codigo: str) -> None:
        ui["moneda_resumen"] = codigo
        contenedor_totales.content = _barra_totales()
        tabla.refrescar(contenedor_totales)

    # Muestra / oculta los cargos extra en la tabla (ver docstring, "Barra").
    boton_cargos = ft.IconButton(icon=ft.Icons.RECEIPT_LONG, on_click=lambda e: _alternar_cargos())

    def _pintar_boton_cargos() -> None:
        visibles = ui["mostrar_cargos"]
        boton_cargos.icon_color = TEXT_ACCENT if visibles else TEXT_MUTED
        boton_cargos.tooltip = TOOLTIP_OCULTAR_CARGOS if visibles else TOOLTIP_MOSTRAR_CARGOS

    def _alternar_cargos() -> None:
        ui["mostrar_cargos"] = not ui["mostrar_cargos"]
        _pintar_boton_cargos()
        # Sin selección: no quedan cargos ocultos seleccionados. al_recargar re-arma la barra, con el botón.
        tabla.recargar(limpiar_seleccion=True)

    _pintar_boton_cargos()

    def _al_recargar() -> list[ft.Control]:
        # Los totales cambian con cualquier alta/edición/borrado y con el período.
        contenedor_totales.content = _barra_totales()
        return [contenedor_totales]

    # ------------------------------------------------------------
    # ⚙ CONFIGURACIÓN DE TARJETAS (cierre / vencimiento + resúmenes)
    # ------------------------------------------------------------

    def _texto_dialogo(texto: str, color: str = TEXT_SECONDARY, **kwargs: Any) -> ft.Text:
        return ft.Text(texto, size=TAMANIO_TEXTO_DIALOGO, color=color, **kwargs)

    def _abrir_config_tarjetas() -> None:
        if not tarjetas_activas:
            _mostrar_error("PRIMERO CARGÁ UNA TARJETA DE CRÉDITO EN CONFIGURACIÓN → CUENTAS.")
            return
        campos: dict[str, tuple[ft.TextField, ft.TextField]] = {}
        periodos_por_tarjeta: dict[str, ft.Column] = {}
        estado = _texto_dialogo("", visible=False)

        def _mostrar_estado(texto: str, es_error: bool) -> None:
            estado.value, estado.color, estado.visible = texto, TEXT_NEGATIVO if es_error else TEXT_POSITIVO, True

        def _resugerir_primera() -> None:
            # La 1ª cuota sugerida del alta depende de las fechas de cierre.
            if "sugerir_primera" in alta_refs:
                alta_refs["sugerir_primera"]()

        def _guardar_fecha(tarjeta: dict, periodo: dict, clave: str, texto: str) -> None:
            """Guarda la fecha real ('cierre' o 'vencimiento') de un resumen; vacía, la borra."""
            nombre = tarjeta["nombre"].upper()
            if texto != _fecha_corta(periodo[clave]):  # sin cambios: no se guarda una "real" igual a la calculada
                try:
                    valor = datetime.strptime(texto, FORMATO_FECHA_RESUMEN).date().isoformat() if texto else None
                    parametro = "closing_date" if clave == "cierre" else "due_date"
                    fees_service.set_fechas_resumen(tarjeta["id"], periodo["mes"], periodo["anio"], **{parametro: valor})
                except ValueError:
                    _mostrar_estado(f"{nombre}: LA FECHA DEBE TENER EL FORMATO {HINT_FECHA_RESUMEN}.", True)
                except FeesError as err:
                    _mostrar_estado(f"{nombre}: {err}", True)
                else:
                    que = "CIERRE" if clave == "cierre" else "VENCIMIENTO"
                    nueva = texto if valor else "LA CALCULADA DESDE EL DÍA DEFAULT"
                    _mostrar_estado(
                        f"{nombre}: {que} DEL RESUMEN {_texto_periodo(periodo['mes'], periodo['anio'])} → {nueva}.", False,
                    )
                    _resugerir_primera()
            _armar_periodos(tarjeta)
            page.update()

        def _celda_fecha(tarjeta: dict, periodo: dict, clave: str) -> ft.Control:
            """
            Fecha de cierre ('cierre') o de vencimiento ('vencimiento') de un
            resumen: texto + ✎ — en color de acento si es la real —; un click
            la pasa a un campo DD/MM/AAAA que guarda con Enter o al salir.
            """
            real = periodo[f"{clave}_especifica"]
            texto = _fecha_corta(periodo[clave])
            contenedor = ft.Container(
                width=ANCHO_FECHA_RESUMEN, height=ALTURA_FECHA_RESUMEN, alignment=ft.Alignment.CENTER_LEFT,
            )

            def _editar() -> None:
                confirmado = {"listo": False}

                def _confirmar(e=None) -> None:
                    if confirmado["listo"]:  # Enter, y después el blur de la misma edición
                        return
                    confirmado["listo"] = True
                    _guardar_fecha(tarjeta, periodo, clave, (campo.value or "").strip())

                campo = ft.TextField(
                    value=texto, hint_text=HINT_FECHA_RESUMEN, dense=LayoutTokens.CELDA_DENSE, autofocus=True,
                    text_size=TAMANIO_TEXTO_DIALOGO, on_submit=_confirmar, on_blur=_confirmar,
                )
                contenedor.content = campo
                page.update()

            contenedor.content = ft.Container(
                content=ft.Row(
                    [
                        _texto_dialogo(
                            texto, color=TEXT_ACCENT if real else TEXT_SECONDARY,
                            weight=PESO_TITULO_BLOQUE if real else None,
                        ),
                        ft.Icon(ft.Icons.EDIT, size=ICONO_EDITAR_FECHA, color=TEXT_MUTED),
                    ],
                    spacing=ESPACIO_ICONO_FECHA, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                tooltip=TOOLTIP_FECHA_REAL if real else TOOLTIP_FECHA_CALCULADA,
                on_click=lambda e: _editar(), ink=True, padding=LayoutTokens.PADDING_CELDA,
            )
            return contenedor

        def _armar_periodos(tarjeta: dict) -> None:
            """ANTERIOR / ACTUAL / PRÓXIMO de la tarjeta, con sus fechas editables (FeesService.card_cycle_periods())."""
            ciclo = fees_service.card_cycle_periods(tarjeta["id"])
            columna = periodos_por_tarjeta[tarjeta["id"]]
            if ciclo is None:
                columna.controls = [_texto_dialogo(
                    "CARGÁ EL DÍA DE CIERRE Y EL DE VENCIMIENTO DEFAULT PARA VER SUS RESÚMENES.",
                    color=TEXT_MUTED, italic=True,
                )]
                return
            columna.controls = [
                ft.Row(
                    [
                        _texto_dialogo(etiqueta, color=TEXT_PRIMARY, width=ANCHO_ETIQUETA_RESUMEN),
                        _texto_dialogo("CIERRE:"),
                        _celda_fecha(tarjeta, ciclo[clave], "cierre"),
                        _texto_dialogo("VENCE:"),
                        _celda_fecha(tarjeta, ciclo[clave], "vencimiento"),
                    ],
                    spacing=ESPACIADO_DIALOGO, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
                for clave, etiqueta in RESUMENES_TARJETA
            ]

        bloques: list[ft.Control] = []
        for tarjeta in tarjetas_activas:
            config = fees_service.get_card_config(tarjeta["id"])
            campo_cierre = ft.TextField(
                label="CIERRE DEFAULT", hint_text="DÍA", width=ANCHO_CAMPO_DIA, dense=LayoutTokens.CELDA_DENSE,
                text_align=ft.TextAlign.CENTER, value=str(config["dia_cierre"]) if config else "",
            )
            campo_vencimiento = ft.TextField(
                label="VENCE DEFAULT", hint_text="DÍA", width=ANCHO_CAMPO_DIA, dense=LayoutTokens.CELDA_DENSE,
                text_align=ft.TextAlign.CENTER, value=str(config["dia_vencimiento"]) if config else "",
            )
            campos[tarjeta["id"]] = (campo_cierre, campo_vencimiento)
            periodos_por_tarjeta[tarjeta["id"]] = ft.Column(spacing=0, tight=True)
            _armar_periodos(tarjeta)
            nombre = ft.Container(
                expand=True, content=banco_con_dot(color_cuenta(tarjeta, tarjeta["nombre"]), tarjeta["nombre"]),
            )
            if bloques:
                bloques.append(ft.Divider())
            bloques.append(ft.Column(
                [
                    ft.Row(
                        [nombre, campo_cierre, campo_vencimiento],
                        spacing=ESPACIADO_DIALOGO, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    periodos_por_tarjeta[tarjeta["id"]],
                ],
                spacing=ESPACIADO_DIALOGO, tight=True,
            ))

        def _guardar(e=None) -> None:
            """GUARDAR: los días default de cada tarjeta (las fechas reales se guardan al editarlas)."""
            errores: list[str] = []
            guardadas = 0
            for tarjeta in tarjetas_activas:
                campo_cierre, campo_vencimiento = campos[tarjeta["id"]]
                texto_cierre = (campo_cierre.value or "").strip()
                texto_vencimiento = (campo_vencimiento.value or "").strip()
                if not texto_cierre and not texto_vencimiento:
                    continue  # sin configurar: se deja como está
                nombre = tarjeta["nombre"].upper()
                try:
                    dia_cierre, dia_vencimiento = int(texto_cierre), int(texto_vencimiento)
                except ValueError:
                    errores.append(f"{nombre}: COMPLETÁ LOS DOS DÍAS CON NÚMEROS")
                    continue
                if not (DIA_MINIMO <= dia_cierre <= DIA_MAXIMO and DIA_MINIMO <= dia_vencimiento <= DIA_MAXIMO):
                    errores.append(f"{nombre}: LOS DÍAS VAN DE {DIA_MINIMO} A {DIA_MAXIMO}")
                    continue
                try:
                    fees_service.set_card_config(tarjeta["id"], dia_cierre, dia_vencimiento)
                    guardadas += 1
                except FeesError as err:
                    errores.append(f"{nombre}: {err}")
            for tarjeta in tarjetas_activas:
                _armar_periodos(tarjeta)
            if errores:
                _mostrar_estado(" | ".join(errores), True)
            else:
                _mostrar_estado(f"CONFIGURACIÓN GUARDADA ({guardadas} TARJETA(S)).", False)
            _resugerir_primera()
            page.update()

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("CONFIGURACIÓN DE TARJETAS"),
            content=ft.Container(
                width=ANCHO_DIALOGO_TARJETAS,
                content=ft.Column(
                    [*bloques, estado],
                    tight=True, spacing=ESPACIADO_SECCION_DIALOGO, scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("CERRAR"), on_click=lambda e: page.pop_dialog()),
                ft.ElevatedButton(content=ft.Text("GUARDAR DÍAS DEFAULT"), on_click=_guardar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    boton_config_tarjetas = ft.IconButton(
        icon=ft.Icons.SETTINGS, icon_color=TEXT_SECONDARY, tooltip=TOOLTIP_CONFIG_TARJETAS,
        on_click=lambda e: _abrir_config_tarjetas(),
    )

    # ------------------------------------------------------------
    # FILA DE ALTA
    # ------------------------------------------------------------

    alta_refs: dict[str, Any] = {}
    ui.setdefault("alta", _alta_vacia())

    def _guardar_borrador_alta() -> None:
        """Ver docstring del módulo, "BORRADOR"."""
        if not alta_refs:
            return
        ui["alta"] = {
            "concepto": alta_refs["concepto"].value or "",
            "tarjeta_id": alta_refs["tarjeta"].id_seleccionado,
            "categoria_id": alta_refs["categoria"].id_seleccionado,
            "tag": alta_refs["tag"].value or "",
            "monto": alta_refs["monto"].texto,
            "cuotas": alta_refs["cuotas"].value or "",
            "primera": alta_refs["primera"].value or "",
            "primera_manual": alta_refs["estado_primera"]["manual"],
            "fecha": alta_refs["fecha"].value or "",
            "moneda": alta_refs["moneda"].value,
        }

    def _on_cambio_borrador(e=None) -> None:
        # Solo guarda el borrador: el cambio ya está en pantalla.
        _guardar_borrador_alta()
        sin_auto_update()

    def _construir_alta() -> FilaAlta:
        borrador = ui["alta"]
        # Mientras se arma la fila, _guardar_borrador_alta() no hace nada (la
        # 1ª cuota sugerida lo llama): si no, leería los campos de la fila
        # ANTERIOR, que ya no está en pantalla.
        alta_refs.clear()
        campo_concepto = ft.TextField(
            value=borrador["concepto"], hint_text="EJ: HELADERA", autofocus=True, text_align=ft.TextAlign.CENTER,
            on_change=_on_cambio_borrador, **estilo_campo(),
        )
        campo_cuotas = ft.TextField(
            value=borrador["cuotas"] or "1", text_align=ft.TextAlign.CENTER, on_change=_on_cambio_borrador,
            **estilo_campo(),
        )
        dropdown_moneda = ft.Dropdown(
            options=[], dense=LayoutTokens.CELDA_DENSE, text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            border=ft.InputBorder.NONE, expand=True, text_align=ft.TextAlign.CENTER,
            on_select=_on_cambio_borrador,
        )

        def _refrescar_monedas(cuenta_id: Optional[str], preferida: Optional[str] = None) -> None:
            cuenta = cuentas_por_id.get(cuenta_id) if cuenta_id else None
            codigos = [s["moneda_codigo"] for s in (cuenta["saldos"] if cuenta else [])] or [MONEDA_DEFAULT]
            dropdown_moneda.options = [ft.dropdown.Option(key=c, text=c) for c in codigos]
            dropdown_moneda.value = preferida if preferida in codigos else codigos[0]

        # --- 1ª cuota: ◀ MM/AAAA ▶ (ver docstring del módulo, "Fila de alta") ---
        # Del borrador solo vuelve la elegida a mano; una sugerida se recalcula.
        primera: dict[str, Any] = {
            "periodo": _leer_periodo(borrador["primera"]) if borrador["primera_manual"] else None,
            "manual": borrador["primera_manual"],
        }
        campo_primera = ft.TextField(
            value=borrador["primera"] if borrador["primera_manual"] else None,
            hint_text=HINT_PRIMERA_CUOTA, text_align=ft.TextAlign.CENTER, expand=True, **estilo_campo(),
        )

        def _mostrar_primera() -> None:
            campo_primera.value = _texto_periodo(*primera["periodo"]) if primera["periodo"] else ""
            tabla.refrescar(campo_primera)
            _guardar_borrador_alta()

        def _sugerir_primera() -> None:
            """La sugerencia del service para la tarjeta y la fecha actuales, salvo que ya se eligió a mano."""
            if primera["manual"]:
                return
            id_tarjeta = campo_tarjeta.id_seleccionado
            try:
                primera["periodo"] = fees_service.suggest_first_fee(
                    id_tarjeta or None, (campo_fecha.value or "").strip(),
                )
            except ValueError:
                return  # fecha a medio tipear: queda la sugerencia anterior
            _mostrar_primera()

        def _mover_primera(meses: int) -> None:
            actual = _leer_periodo(campo_primera.value) or primera["periodo"]
            if actual is None or campo_primera.disabled:
                return
            primera["periodo"] = _correr_periodo(*actual, meses)
            primera["manual"] = True
            _mostrar_primera()

        def _on_cambio_primera(e=None) -> None:
            primera["manual"] = True  # tipeada a mano: ya no se re-sugiere
            _guardar_borrador_alta()
            sin_auto_update()  # el texto ya está en pantalla

        campo_primera.on_change = _on_cambio_primera

        def _flecha_primera(icono: str, tooltip: str, meses: int) -> ft.Control:
            return ft.Container(
                width=ANCHO_FLECHA_PERIODO, alignment=ft.Alignment.CENTER, tooltip=tooltip,
                on_click=lambda e: _mover_primera(meses),
                content=ft.Icon(icono, size=ICONO_FLECHA_PERIODO, color=TEXT_SECONDARY),
            )

        celda_primera = ft.Row(
            [
                _flecha_primera(ft.Icons.CHEVRON_LEFT, "UN MES ANTES", -1),
                campo_primera,
                _flecha_primera(ft.Icons.CHEVRON_RIGHT, "UN MES DESPUÉS", 1),
            ],
            spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        def _on_tarjeta(id_tarjeta: Optional[str]) -> None:
            # CampoFiltrable parchea la fila (tabla.pagina_alta) justo después.
            if id_tarjeta is not None:
                _refrescar_monedas(id_tarjeta)
                _sugerir_primera()  # el día de cierre es de la tarjeta
            _guardar_borrador_alta()

        def _habilitar_cuotas(id_categoria: str) -> None:
            # Cuotas y 1ª cuota no aplican a un cargo extra (categoría especial).
            es_cargo_extra = id_categoria in mapa_categoria_a_charge_type
            campo_cuotas.disabled = es_cargo_extra
            celda_primera.disabled = es_cargo_extra
            campo_primera.disabled = es_cargo_extra

        def _on_categoria(id_categoria: Optional[str]) -> None:
            if id_categoria is not None:
                _habilitar_cuotas(id_categoria)
            _guardar_borrador_alta()

        # Del borrador, si todavía es una opción válida; si no, el default —
        # nunca una categoría especial: sin elegirla a mano, el alta sería un
        # cargo extra en vez de una compra.
        ids_categoria = {id_ for id_, _ in opciones_categoria_alta}
        ids_tarjeta = {id_ for id_, _ in opciones_tarjeta}
        categoria_inicial = (
            borrador["categoria_id"] if borrador["categoria_id"] in ids_categoria
            else (opciones_categoria_edicion[0][0] if opciones_categoria_edicion else None)
        )
        tarjeta_inicial = (
            borrador["tarjeta_id"] if borrador["tarjeta_id"] in ids_tarjeta
            else (opciones_tarjeta[0][0] if opciones_tarjeta else None)
        )
        campo_tag = tabla.campo_tag_alta(borrador["tag"])
        campo_tag.on_change = _on_cambio_borrador
        campo_categoria = tabla.campo_filtrable_alta(
            "categoria", opciones_categoria_alta, _on_categoria,
            placeholder="CATEGORÍA", valor_inicial_id=categoria_inicial,
            on_avanzar=lambda: tabla.enfocar(campo_tag),
        )
        campo_tarjeta = tabla.campo_filtrable_alta(
            "banco", opciones_tarjeta, _on_tarjeta,
            placeholder="BANCO", valor_inicial_id=tarjeta_inicial,
            on_avanzar=lambda: tabla.enfocar(campo_categoria.campo_texto),
        )
        # persistir_formula=True: el campo recuerda la fórmula mientras la
        # fila no se guarde. on_confirmar no-op — la fila entera confirma
        # junta, solo desde el ✓ (Enter con el foco ahí, o click).
        campo_monto = CampoMonto(
            tabla.pagina_alta,
            on_confirmar=lambda monto_minor: None,
            persistir_formula=True,
            hint_text=HINT_MONTO_ALTA,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            on_avanzar=lambda: tabla.enfocar(campo_cuotas),
        )
        sin_borde(campo_monto.control)
        campo_monto.control.text_align = ft.TextAlign.RIGHT
        campo_monto.control.on_change = _on_cambio_borrador  # CampoMonto no usa on_change
        if borrador["monto"]:
            campo_monto.control.value = borrador["monto"]

        def _on_cambio_fecha() -> None:
            _sugerir_primera()  # la 1ª cuota sugerida depende de la fecha
            _guardar_borrador_alta()

        celda_fecha, campo_fecha = tabla.campo_fecha_alta(
            # on_cambio: al tipear o elegir en el calendario.
            borrador["fecha"] or date.today().isoformat(), on_cambio=_on_cambio_fecha,
            on_submit=lambda: tabla.enfocar(dropdown_moneda),
        )
        _refrescar_monedas(tarjeta_inicial, preferida=borrador["moneda"])
        # Sync inicial: la categoría default podría ser una especial.
        if campo_categoria.id_seleccionado:
            _habilitar_cuotas(campo_categoria.id_seleccionado)
        _sugerir_primera()  # todavía sin montar: refrescar() lo saltea
        boton_confirmar = tabla.boton_confirmar_alta("AGREGAR", _confirmar_alta)

        alta_refs.update(
            concepto=campo_concepto, tarjeta=campo_tarjeta, categoria=campo_categoria, tag=campo_tag, monto=campo_monto,
            cuotas=campo_cuotas, primera=campo_primera, fecha=campo_fecha, moneda=dropdown_moneda,
            boton=boton_confirmar, sugerir_primera=_sugerir_primera, estado_primera=primera,
        )

        # Enter avanza al campo siguiente (en Tarjeta/Categoría, vía
        # on_avanzar de CampoFiltrable) y nunca guarda la fila; en Moneda, el
        # último, Enter o Tab llevan al ✓ sin activarlo (ahí Enter confirma).
        campo_concepto.on_submit = lambda e: tabla.enfocar(campo_tarjeta.campo_texto)
        campo_tag.on_submit = lambda e: tabla.enfocar(campo_monto.control)
        campo_cuotas.on_submit = lambda e: tabla.enfocar(campo_primera)
        campo_primera.on_submit = lambda e: tabla.enfocar(campo_fecha)
        tabla.tab_a_confirmar(dropdown_moneda)

        return FilaAlta(
            celdas={
                "concepto": campo_concepto,
                "banco": campo_tarjeta.control,
                "categoria": campo_categoria.control,
                "tag": campo_tag,
                "monto": campo_monto.control,
                "cuotas": campo_cuotas,
                "primera": celda_primera,
                "fecha": celda_fecha,
                "moneda": ft.Row([dropdown_moneda], spacing=0),
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
            # Tras un alta exitosa `boton` ya no está en pantalla (la fila se
            # reconstruyó): refrescar() lo saltea.
            boton.disabled = False
            tabla.refrescar(boton)

    def _procesar_alta() -> None:
        concepto = (alta_refs["concepto"].value or "").strip()
        campo_tarjeta = alta_refs["tarjeta"]
        campo_categoria = alta_refs["categoria"]
        moneda_codigo = alta_refs["moneda"].value

        if not tarjetas_activas:
            _mostrar_error("PRIMERO CARGÁ UNA TARJETA DE CRÉDITO EN CONFIGURACIÓN → CUENTAS.")
            return
        if not categorias:
            _mostrar_error("NO HAY CATEGORÍAS DE EGRESO CARGADAS.")
            return
        if not concepto:
            _mostrar_error("EL COMERCIO/CONCEPTO NO PUEDE ESTAR VACÍO.")
            return
        try:
            monto_con_signo = float((alta_refs["monto"].texto or "").strip().replace(",", "."))
        except ValueError:
            _mostrar_error("EL MONTO NO ES UN NÚMERO VÁLIDO.")
            return
        if monto_con_signo == 0:
            _mostrar_error("EL MONTO NO PUEDE SER 0.")
            return
        fecha_str = (alta_refs["fecha"].value or "").strip()
        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d")
        except ValueError:
            _mostrar_error("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.")
            return
        if not campo_tarjeta.id_seleccionado:
            _mostrar_error("SELECCIONÁ UNA TARJETA DE LA LISTA DE SUGERENCIAS.")
            return
        if not campo_categoria.id_seleccionado:
            _mostrar_error("SELECCIONÁ UNA CATEGORÍA DE LA LISTA DE SUGERENCIAS.")
            return
        if not moneda_codigo:
            _mostrar_error("COMPLETÁ LA MONEDA.")
            return

        cuenta_id = campo_tarjeta.id_seleccionado
        categoria_id = campo_categoria.id_seleccionado
        # Routing por categoría (ver docstring del módulo): el signo por sí
        # solo no decide nada.
        charge_type = mapa_categoria_a_charge_type.get(campo_categoria.id_seleccionado)

        if charge_type is None:
            if monto_con_signo < 0:
                _mostrar_error(
                    "EL MONTO NO PUEDE SER NEGATIVO CON UNA CATEGORÍA NORMAL — ELEGÍ UNA CATEGORÍA ESPECIAL "
                    "DE TARJETA (IMPUESTO/RECARGO/AJUSTE-REINTEGRO) PARA CARGAR UN AJUSTE O REINTEGRO."
                )
                return
            try:
                cantidad_cuotas = int((alta_refs["cuotas"].value or "").strip())
            except ValueError:
                _mostrar_error("LA CANTIDAD DE CUOTAS DEBE SER UN NÚMERO ENTERO.")
                return
            if cantidad_cuotas < 1:
                _mostrar_error("LA CANTIDAD DE CUOTAS DEBE SER AL MENOS 1.")
                return
            periodo_primera = _leer_periodo(alta_refs["primera"].value)
            if periodo_primera is None:
                _mostrar_error(f"LA 1ª CUOTA DEBE TENER EL FORMATO {HINT_PRIMERA_CUOTA}.")
                return
            # Cualquier mes vale, también uno anterior al de la compra (pedido explícito).
            mes_primera, anio_primera = periodo_primera
            try:
                resultado = fees_service.create_purchase(
                    date_str=fecha_str,
                    concept=concepto,
                    account_id=cuenta_id,
                    category_id=categoria_id,
                    currency_code=moneda_codigo,
                    total_amount=monto_con_signo,
                    total_fees=cantidad_cuotas,
                    first_fee_month=mes_primera,
                    first_fee_year=anio_primera,
                    tag=alta_refs["tag"].value,  # el service lo recorta; vacío = None
                )
            except (FeesError, ValueError) as err:
                _mostrar_error(str(err))
                return
            mensaje = (
                f"COMPRA #{resultado.entity_id} REGISTRADA EN {cantidad_cuotas} CUOTA(S), "
                f"LA 1ª EN {_texto_periodo(mes_primera, anio_primera)}."
            )
            # La tabla y el total por tarjeta son del mes en que VENCE la
            # cuota: si la 1ª no vence en el mes que se está viendo, la compra
            # no aparece acá todavía — decirlo, para que no parezca perdida.
            if (mes_primera, anio_primera) != (ui["mes"], ui["anio"]):
                mensaje += (
                    f" APARECE EN LA TABLA Y EN EL TOTAL DE LA TARJETA DESDE "
                    f"{_texto_periodo(mes_primera, anio_primera)}."
                )
        else:
            # Cargo extra del resumen, con el signo tal cual se tipeó (la
            # categoría ya clasificó el TIPO de cargo, no su signo).
            try:
                resultado_resumen = fees_service.open_statement(account_id=cuenta_id, month=fecha.month, year=fecha.year)
                decimales = monedas_por_codigo.get(moneda_codigo, {}).get("decimales", 2)
                fees_service.add_extra_charge(
                    statement_id=resultado_resumen.entity_id,
                    concept=concepto,
                    charge_type=charge_type,
                    amount_minor=amount_to_minor(monto_con_signo, decimales),
                    currency_code=moneda_codigo,
                    date_str=fecha_str,
                )
            except (FeesError, ValueError) as err:
                # Cubre StatementAlreadyClosedError/StatementAlreadyPaidError:
                # no hay cargo extra retroactivo sobre un resumen cerrado.
                _mostrar_error(str(err))
                return
            mensaje = f"CARGO EXTRA ({charge_type.upper()}) REGISTRADO EN EL RESUMEN DE {fecha.month:02d}/{fecha.year}."
            # Igual que una compra: va a la tabla y al total del mes de su resumen.
            if (fecha.month, fecha.year) != (ui["mes"], ui["anio"]):
                mensaje += (
                    f" APARECE EN LA TABLA Y EN EL TOTAL DE LA TARJETA DE "
                    f"{_texto_periodo(fecha.month, fecha.year)}."
                )

        # La fila nueva conserva lo cargado (pedido explícito: carga en serie
        # editando solo algunos campos — ver docstring, "BORRADOR").
        _guardar_borrador_alta()
        tabla.alta_ok()
        _mostrar_ok(mensaje)

    # ------------------------------------------------------------
    # FILAS DE DATOS — celdas editables inline (CLAUDE.md §10)
    # ------------------------------------------------------------

    def _construir_celdas_cargo(cargo: dict) -> dict[str, ft.Control]:
        """Cargo extra del resumen: todas de solo lectura (ver docstring, "Cargos extra en la tabla")."""
        cuenta = cuentas_por_id.get(cargo["cuenta_id"])
        color_monto = TEXT_NEGATIVO if _es_reintegro(cargo) else TEXT_POSITIVO

        def _lectura(clave: str, color: str = TEXT_PRIMARY, tooltip: Optional[str] = None) -> ft.Control:
            return tabla.celda_lectura(clave, texto_celda(_valor_columna(cargo, clave), color=color, tooltip=tooltip))

        return {
            "concepto": _lectura(
                "concepto", tooltip=f"{TOOLTIP_CARGO} {_texto_periodo(ui['mes'], ui['anio'])}: {cargo['concepto']}",
            ),
            "banco": tabla.celda_lectura(
                "banco", banco_con_dot(color_cuenta(cuenta, cargo["account_name"] or ""), cargo["account_name"] or ""),
            ),
            "categoria": _lectura("categoria"),
            "tag": _lectura("tag", color=TEXT_MUTED),
            "monto": tabla.celda_lectura("monto", texto_celda(
                _monto_texto(cargo), color=color_monto, size=TypographyTokens.REGISTRO_FONT_MONTO,
            )),
            "cuotas": _lectura("cuotas", color=TEXT_MUTED),
            "primera": _lectura("primera", color=TEXT_MUTED),
            "fecha": _lectura("fecha"),
            "moneda": _lectura("moneda", color=TEXT_SECONDARY),
        }

    def _construir_celdas(compra: dict) -> dict[str, ft.Control]:
        if _es_cargo(compra):
            return _construir_celdas_cargo(compra)
        cuenta = cuentas_por_id.get(compra["cuenta_id"])
        color_monto = TEXT_NEGATIVO if _es_reintegro(compra) else TEXT_POSITIVO
        texto_monto = _monto_texto(compra)

        def _banco() -> ft.Control:
            return banco_con_dot(color_cuenta(cuenta, compra["account_name"] or ""), compra["account_name"] or "")

        def _moneda_lectura() -> ft.Control:
            return tabla.celda_lectura("moneda", texto_celda(compra["currency_code"] or "", color=TEXT_SECONDARY))

        # 1ª cuota: de solo lectura en una compra cancelada (ver más abajo la editable).
        celda_primera = tabla.celda_lectura(
            "primera", texto_celda(_valor_columna(compra, "primera"), color=TEXT_SECONDARY),
        )

        if compra["estado"] == "cancelada":
            # Atenuada y sin edición (fila_atenuada + todas de solo lectura).
            return {
                "concepto": tabla.celda_lectura(
                    "concepto", texto_celda(compra["concepto"], tooltip=f"{TOOLTIP_CANCELADA}: {compra['concepto']}"),
                ),
                "banco": tabla.celda_lectura("banco", _banco()),
                "categoria": tabla.celda_lectura("categoria", texto_celda(compra["category_name"] or "")),
                "tag": tabla.celda_lectura("tag", texto_celda(compra["tag"] or "", color=TEXT_MUTED)),
                "monto": tabla.celda_lectura(
                    "monto",
                    texto_celda(texto_monto, color=color_monto, size=TypographyTokens.REGISTRO_FONT_MONTO),
                ),
                "cuotas": tabla.celda_lectura("cuotas", texto_celda(_texto_cuotas(compra))),
                "primera": celda_primera,
                "fecha": tabla.celda_lectura("fecha", texto_celda(compra["fecha_compra"])),
                "moneda": _moneda_lectura(),
            }

        def _guardar(**kwargs) -> str:
            # FeesError del service (ej. campo bloqueado por §4) sube tal
            # cual a la celda, que lo muestra y vuelve al valor anterior.
            fees_service.update_purchase(compra["id"], **kwargs)
            return f"COMPRA #{compra['id']} ACTUALIZADA."

        def _guardar_concepto(nuevo: str) -> str:
            if not nuevo.strip():
                raise ValueError("EL COMERCIO/CONCEPTO NO PUEDE ESTAR VACÍO.")
            return _guardar(concepto=nuevo.strip())

        def _guardar_monto(monto_minor: int) -> str:
            # Se edita la cuota del mes con el signo que se ve: + compra,
            # − reintegro/devolución. Un negativo es válido y el signo se
            # puede dar vuelta (update_purchase() acepta totales negativos).
            # Todas las cuotas de la compra valen lo mismo (split default):
            # nuevo total = cuota × cantidad de cuotas.
            if monto_minor == 0:
                raise ValueError("EL MONTO NO PUEDE SER 0.")
            return _guardar(monto_total_minor=monto_minor * compra["total_cuotas"])

        def _guardar_moneda(nuevo_codigo: str) -> str:
            return _guardar(moneda_codigo=nuevo_codigo)

        # Moneda: entre las de la tarjeta de la fila (+ la actual, si la
        # tarjeta ya no la tiene), mismo criterio que la fila de alta.
        opciones_moneda = [(s["moneda_codigo"], s["moneda_codigo"]) for s in (cuenta["saldos"] if cuenta else [])]
        if compra["currency_code"] not in [codigo for codigo, _ in opciones_moneda]:
            opciones_moneda.append((compra["currency_code"], compra["currency_code"]))

        def _guardar_cuotas(nuevo: str) -> str:
            try:
                cantidad = int(nuevo.strip())
            except ValueError:
                raise ValueError("LA CANTIDAD DE CUOTAS DEBE SER UN NÚMERO ENTERO.") from None
            if cantidad < 1:
                raise ValueError("LA CANTIDAD DE CUOTAS DEBE SER AL MENOS 1.")
            fees_service.update_purchase_cuotas(compra["id"], cantidad)
            return f"COMPRA #{compra['id']} ACTUALIZADA."

        def _guardar_fecha(nuevo: str) -> str:
            try:
                datetime.strptime(nuevo.strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.") from None
            return _guardar(fecha=nuevo.strip())

        def _guardar_primera(nuevo: str) -> str:
            # Rearma el cronograma desde ese mes, con la misma cantidad de
            # cuotas: la cuota N pasa a 1ª + (N − 1) meses (ver docstring del
            # módulo, "1ª cuota inline"). Cualquier mes vale. FeesError del
            # service (cuota no pendiente o compartida) sube a la celda.
            periodo = _leer_periodo(nuevo)
            if periodo is None:
                raise ValueError(f"LA 1ª CUOTA DEBE TENER EL FORMATO {HINT_PRIMERA_CUOTA}.")
            cuotas = fees_service.get_fees_for_purchase(compra["id"])  # por numero_cuota
            resultado = fees_service.reschedule_fees(
                compra["id"], {q["id"]: _correr_periodo(*periodo, q["numero_cuota"] - 1) for q in cuotas},
            )
            if not resultado.success:  # ya estaba así (ej. "4/2026" sobre "04/2026")
                return f"COMPRA #{compra['id']}: EL CRONOGRAMA YA EMPEZABA EN {_texto_periodo(*periodo)}."
            return f"COMPRA #{compra['id']}: CRONOGRAMA REARMADO DESDE {_texto_periodo(*periodo)}."

        # 1ª cuota: editable (rearma el cronograma) solo mientras TODAS las
        # cuotas siguen 'pendiente' — mismo criterio que Cuotas; si no, se
        # ajusta cuota por cuota desde el cronograma (📅).
        if compra["todas_pendientes"] and compra["primera_cuota"]:
            texto_primera = _valor_columna(compra, "primera")
            celda_primera = tabla.celda_texto(
                compra, "primera", texto_primera, _guardar_primera, valor_inicial=texto_primera, color=TEXT_SECONDARY,
            )
        else:
            celda_primera = tabla.celda_lectura("primera", texto_celda(
                _valor_columna(compra, "primera"), color=TEXT_MUTED, tooltip=TOOLTIP_CUOTAS_BLOQUEADAS,
            ))

        # Monto (la cuota del mes): editable solo mientras TODAS las cuotas
        # siguen 'pendiente' — una vez procesadas, no se toca (pedido
        # explícito; update_purchase() aplica la misma regla, CLAUDE.md §4).
        # Si vence más de una cuota este mes, el monto mostrado es su suma
        # y no se edita desde acá.
        if not compra["todas_pendientes"]:
            celda_monto = tabla.celda_lectura("monto", texto_celda(
                texto_monto, color=color_monto, size=TypographyTokens.REGISTRO_FONT_MONTO,
                tooltip=f"{texto_monto} ({TOOLTIP_CUOTAS_BLOQUEADAS})",
            ))
        elif len(compra["numeros_cuota_mes"]) > 1:
            celda_monto = tabla.celda_lectura("monto", texto_celda(
                texto_monto, color=color_monto, size=TypographyTokens.REGISTRO_FONT_MONTO,
                tooltip=f"{texto_monto} ({TOOLTIP_VARIAS_CUOTAS})",
            ))
        else:
            celda_monto = tabla.celda_monto(
                compra, "monto", texto_monto, color_monto,
                compra["monto_cuota_mes_minor"],  # con el signo que se ve (+ compra, − reintegro)
                compra["decimales"], _guardar_monto,
            )

        # Cuotas: muestra "3/6" (qué cuota vence este mes) y edita la
        # cantidad total, solo si TODAS siguen 'pendiente' (pedido
        # explícito). La regla completa la aplica update_purchase_cuotas().
        if compra["todas_pendientes"]:
            celda_cuotas = tabla.celda_texto(
                compra, "cuotas", _texto_cuotas(compra), _guardar_cuotas, valor_inicial=str(compra["total_cuotas"]),
            )
        else:
            celda_cuotas = tabla.celda_lectura(
                "cuotas", texto_celda(_texto_cuotas(compra), color=TEXT_MUTED, tooltip=TOOLTIP_CUOTAS_BLOQUEADAS),
            )

        return {
            "concepto": tabla.celda_texto(compra, "concepto", compra["concepto"], _guardar_concepto),
            "banco": tabla.celda_filtrable(
                compra, "banco", _banco, opciones_tarjeta, str(compra["cuenta_id"]),
                lambda id_: _guardar(cuenta_id=id_),
            ),
            "categoria": tabla.celda_filtrable(
                compra, "categoria", lambda: texto_celda(compra["category_name"] or ""),
                opciones_categoria_edicion, str(compra["categoria_id"]),
                lambda id_: _guardar(categoria_id=id_),
            ),
            # update_purchase() borra el tag con "" (lo guarda como NULL).
            "tag": tabla.celda_tag(compra, "tag", compra["tag"], lambda nuevo: _guardar(tag=nuevo)),
            "monto": celda_monto,
            "cuotas": celda_cuotas,
            "primera": celda_primera,
            "fecha": tabla.celda_texto(compra, "fecha", compra["fecha_compra"], _guardar_fecha),
            "moneda": tabla.celda_dropdown(
                compra, "moneda", compra["currency_code"] or "", opciones_moneda, compra["currency_code"],
                _guardar_moneda, color=TEXT_SECONDARY,
            ),
        }

    def _accion_fila(compra: dict) -> Optional[ft.Control]:
        if _es_cargo(compra):
            # Siempre visible: es lo que distingue un cargo de una compra.
            return ft.Icon(
                ft.Icons.RECEIPT_LONG, size=ICONO_ACCION, color=TEXT_SECONDARY,
                tooltip=f"{TOOLTIP_CARGO} {_texto_periodo(ui['mes'], ui['anio'])} (NO ES UNA COMPRA)",
            )
        if compra["estado"] == "cancelada":
            return ft.Icon(ft.Icons.BLOCK, size=ICONO_ACCION, color=TEXT_MUTED, tooltip=TOOLTIP_CANCELADA)
        # 📅 visible con el mouse encima (icono_accion()); al lado, el de compartida (o su lugar vacío).
        compartida = ft.Container(width=ANCHO_ICONO_ACCION, alignment=ft.Alignment.CENTER)
        if compra["compartida"]:
            compartida.content = ft.Icon(ft.Icons.PEOPLE, size=ICONO_ACCION, color=TEXT_ACCENT)
            compartida.tooltip = "COMPRA COMPARTIDA"
        return ft.Row(
            [tabla.icono_accion(ft.Icons.EDIT_CALENDAR, TOOLTIP_CRONOGRAMA, False, lambda: _abrir_cronograma(compra)),
             compartida],
            spacing=0,
            tight=True,
        )

    # ------------------------------------------------------------
    # 📅 CRONOGRAMA DE UNA COMPRA (mover cuotas pendientes)
    # ------------------------------------------------------------

    def _abrir_cronograma(compra: dict) -> None:
        cuotas = [dict(q) for q in fees_service.get_fees_for_purchase(compra["id"])]  # CLAUDE.md §11
        if not cuotas:
            _mostrar_error(f"LA COMPRA #{compra['id']} NO TIENE CUOTAS.")
            return
        # Mes de cada cuota en el panel (se guarda recién con GUARDAR).
        periodos = {q["id"]: (q["mes_proyectado"], q["anio_proyectado"]) for q in cuotas}
        filas = ft.Column(spacing=ESPACIADO_DIALOGO, tight=True)
        aviso = _texto_dialogo("", color=TEXT_NEGATIVO, visible=False)
        boton_guardar = ft.ElevatedButton(content=ft.Text("GUARDAR"), on_click=lambda e: _guardar())

        def _cambios() -> dict[int, tuple[int, int]]:
            return {
                q["id"]: periodos[q["id"]] for q in cuotas
                if periodos[q["id"]] != (q["mes_proyectado"], q["anio_proyectado"])
            }

        def _meses_repetidos() -> list[str]:
            vistos: set[tuple[int, int]] = set()
            repetidos: list[str] = []
            for q in cuotas:
                periodo = periodos[q["id"]]
                if periodo in vistos and _texto_periodo(*periodo) not in repetidos:
                    repetidos.append(_texto_periodo(*periodo))
                vistos.add(periodo)
            return repetidos

        def _fila(q: dict) -> ft.Control:
            texto_estado, icono_estado = ESTADOS_CUOTA.get(q["estado"], (q["estado"].upper(), ft.Icons.HELP_OUTLINE))
            movible = q["estado"] == "pendiente"
            color = TEXT_PRIMARY if movible else TEXT_MUTED
            movida = periodos[q["id"]] != (q["mes_proyectado"], q["anio_proyectado"])
            controles: list[ft.Control] = [
                _texto_dialogo(f"CUOTA {q['numero_cuota']}", color=color, width=ANCHO_TEXTO_CUOTA),
                _texto_dialogo("→", color=color),
                _texto_dialogo(
                    _texto_periodo(*periodos[q["id"]]), color=TEXT_ACCENT if movida else color,
                    weight=PESO_TITULO_BLOQUE if movida else None, width=ANCHO_TEXTO_PERIODO,
                ),
                ft.Icon(icono_estado, size=ICONO_ESTADO_CUOTA, color=color),
                _texto_dialogo(texto_estado, color=color, expand=True),
            ]
            if movible:
                controles += [
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_LEFT, icon_color=TEXT_SECONDARY, tooltip="UN MES ANTES",
                        on_click=lambda e, cuota_id=q["id"]: _mover(cuota_id, -1),
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CHEVRON_RIGHT, icon_color=TEXT_SECONDARY, tooltip="UN MES DESPUÉS",
                        on_click=lambda e, cuota_id=q["id"]: _mover(cuota_id, 1),
                    ),
                ]
            return ft.Row(controles, spacing=ESPACIADO_DIALOGO, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def _armar() -> None:
            filas.controls = [_fila(q) for q in cuotas]
            repetidos = _meses_repetidos()
            aviso.value = f"NO PUEDE HABER DOS CUOTAS EN EL MISMO MES: {', '.join(repetidos)}." if repetidos else ""
            aviso.visible = bool(repetidos)
            boton_guardar.disabled = bool(repetidos) or not _cambios()

        def _mover(cuota_id: str, meses: int) -> None:
            periodos[cuota_id] = _correr_periodo(*periodos[cuota_id], meses)
            _armar()
            page.update()

        def _guardar() -> None:
            cambios = _cambios()
            if not cambios:
                page.pop_dialog()
                return
            try:
                resultado = fees_service.reschedule_fees(compra["id"], cambios)
            except FeesError as err:
                # El service repite las reglas (solo pendientes, no compartidas, sin repetir mes).
                aviso.value, aviso.visible = str(err), True
                page.update()
                return
            page.pop_dialog()
            tabla.recargar()
            _mostrar_ok(
                f"CRONOGRAMA DE LA COMPRA #{compra['id']} ACTUALIZADO "
                f"({resultado.data['fees_moved']} CUOTA(S) MOVIDA(S))."
            )

        _armar()
        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(f"{(compra['concepto'] or '').upper()} — {compra['total_cuotas']} CUOTA(S)"),
            content=ft.Container(
                width=ANCHO_DIALOGO_CRONOGRAMA,
                content=ft.Column([filas, aviso], tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO),
            ),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=lambda e: page.pop_dialog()),
                boton_guardar,
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    # ------------------------------------------------------------
    # ELIMINAR / COMPARTIR (barra flotante)
    # ------------------------------------------------------------

    def _avisos_eliminar(filas: list[dict]) -> str:
        # Un cargo extra no se cancela: se borra, o el service lo rechaza (resumen cerrado o pagado).
        se_cancelan = sum(
            1 for c in filas if not _es_cargo(c) and c["estado"] == "activa" and (c["procesada"] or c["compartida"])
        )
        if not se_cancelan:
            return ""
        return f"{se_cancelan} SE CANCELA(N) EN VEZ DE BORRARSE: TIENE(N) CUOTAS PROCESADAS O ESTÁ(N) COMPARTIDA(S)"

    def _eliminar_cargos(cargos: list[dict]) -> tuple[int, list[str]]:
        """Borra cargos extra (delete_extra_charge(): solo con su resumen abierto). → (borrados, errores)."""
        borrados = 0
        errores: list[str] = []
        for cargo in cargos:
            try:
                fees_service.delete_extra_charge(cargo["id"])
                borrados += 1
            except FeesError as err:
                errores.append(f"{(cargo['concepto'] or '').upper()}: {err}")
        return borrados, errores

    def _eliminar(filas: list[dict]) -> tuple[str, bool]:
        cargos_borrados, errores = _eliminar_cargos([f for f in filas if _es_cargo(f)])
        borradas = canceladas = 0
        for compra in (f for f in filas if not _es_cargo(f)):
            try:
                fees_service.delete_purchase(compra["id"])
                borradas += 1
                continue
            except FeesError:
                pass  # algo depende de ella (§4): se cancela en su lugar
            if compra["estado"] != "activa":
                errores.append(f"#{compra['id']}: YA ESTÁ {compra['estado'].upper()} Y NO SE PUEDE BORRAR")
                continue
            try:
                fees_service.cancel_purchase(compra["id"])
                canceladas += 1
            except FeesError as err:
                errores.append(f"#{compra['id']}: {err}")
        partes = []
        if borradas:
            partes.append(f"{borradas} COMPRA(S) ELIMINADA(S)")
        if canceladas:
            partes.append(f"{canceladas} CANCELADA(S) (TIENEN CUOTAS PROCESADAS, ESTÁN COMPARTIDAS O TIENEN UNA DEUDA)")
        if cargos_borrados:
            partes.append(f"{cargos_borrados} CARGO(S) EXTRA ELIMINADO(S)")
        if errores:
            partes.append(f"{len(errores)} CON ERROR: " + " | ".join(errores))
        return " · ".join(partes) or "NADA PARA ELIMINAR.", bool(errores)

    def _compartir_una(compra: dict, hogar_id: str, pagador: str, coeficiente: float) -> None:
        # Cada compra se comparte en su propio modo_deuda (compartir_compra.py, ídem).
        shared_expenses_service.add_shared_purchase(
            compra_id=compra["id"], hogar_id=hogar_id, pagador=pagador, coeficiente_deuda=coeficiente,
        )

    def _compartir(filas: list[dict]) -> None:
        # Los cargos extra del resumen no se comparten: solo las compras.
        compras = [f for f in filas if not _es_cargo(f)]
        if not compras:
            _mostrar_error("LOS CARGOS EXTRA DEL RESUMEN NO SE COMPARTEN: SELECCIONÁ AL MENOS UNA COMPRA.")
            return
        if len(compras) == 1:
            if compras[0]["estado"] == "cancelada":
                _mostrar_error("UNA COMPRA CANCELADA NO SE PUEDE COMPARTIR.")
                return
            # El flujo existente (compartir_compra.py) es un diálogo por
            # compra: se dispara el on_click (async) de su propio ícono.
            icono, _ = compartir_compra.build_icon(
                page, shared_expenses_service, fees_service, compras[0], tabla.recargar,
            )
            page.run_task(icono.on_click, None)
            return
        canceladas = sum(1 for c in compras if c["estado"] == "cancelada")
        ya_compartidas = sum(1 for c in compras if c["estado"] != "cancelada" and c["compartida"])
        pendientes = [c for c in compras if c["estado"] != "cancelada" and not c["compartida"]]
        if not pendientes:
            _mostrar_error("NINGUNA DE LAS COMPRAS SELECCIONADAS SE PUEDE COMPARTIR (CANCELADAS O YA COMPARTIDAS).")
            return
        avisos = []
        if len(filas) > len(compras):
            avisos.append(f"{len(filas) - len(compras)} CARGO(S) EXTRA: NO SE COMPARTE(N).")
        if canceladas:
            avisos.append(f"{canceladas} CANCELADA(S): SE SALTEA(N).")
        if ya_compartidas:
            avisos.append(f"{ya_compartidas} YA COMPARTIDA(S) (TOTAL O PARCIALMENTE): SE SALTEA(N).")
        page.run_task(
            abrir_compartir_varios, page, shared_expenses_service,
            f"COMPARTIR {len(pendientes)} COMPRAS", pendientes, _compartir_una, avisos, tabla.recargar,
            # Base de cada compra: su total (con $, el monto fijo es sobre el total).
            compartir_compra.monto_base_compra,
        )

    # ------------------------------------------------------------
    # ARMADO
    # ------------------------------------------------------------

    tabla = TablaPlanilla(
        page,
        clave="compras",
        columnas=COLUMNAS,
        pref_anchos=PREF_ANCHOS_COLUMNAS,
        cargar_filas=_cargar_filas,
        construir_celdas=_construir_celdas,
        construir_alta=_construir_alta,
        firma=_firma,
        valor_columna=_valor_columna,
        clave_orden=_clave_orden,
        texto_busqueda=lambda c: c["concepto"] or "",
        accion_fila=_accion_fila,
        fila_atenuada=lambda c: c["estado"] == "cancelada",
        on_eliminar=_eliminar,
        avisos_eliminar=_avisos_eliminar,
        on_compartir=_compartir,
        al_recargar=_al_recargar,
        errores_esperados=(FeesError,),
        texto_vacio="NO HAY COMPRAS EN CUOTAS NI CARGOS EXTRA PARA ESTE PERÍODO.",
        # Σ de lo que muestra la columna Monto: la cuota del mes (pedido explícito) o el cargo, con su signo.
        columna_suma="monto_cuota_mes_minor",
    )
    control_tabla = tabla.construir()
    contenedor_totales.content = _barra_totales()

    titulo = barra_titulo(
        page, "COMPRAS Y MOVIMIENTOS EN CUOTAS", tabla, ui,
        lambda: tabla.recargar(limpiar_seleccion=True), "BUSCAR EN COMPRAS…",
    )
    # Scroll propio (el Registro lo tiene por la Column del dashboard).
    return ft.Column(
        [pantalla_planilla([titulo, contenedor_totales, control_tabla])],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
