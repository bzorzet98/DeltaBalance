"""
DeltaBalance — ui/screens/ahorros.py

Ahorros e inversiones (services/savings_service.py), rediseño por
pestañas: RESUMEN · FCI · ACCIONES · CEDEARS · PLAZO FIJO · PLAZO FLEX ·
OTROS. Mismo fondo y paleta que las pantallas estilo planilla
(pantalla_planilla(), ui/theme/tabla_tokens.py).

Las pestañas son una fila de tabs con subrayado en la activa (pedido
explícito: "similar a tabs", antes eran pills que con wrap=True ocupaban
todo el ancho, una debajo de la otra). Hechas a mano, no ft.Tabs: la API
de tabs cambió en Flet 0.80+ y no está confirmada en este proyecto
(docs/FLET_API_NOTES.md). La fila scrollea en horizontal si no entra.

Reglas de arquitectura: solo SavingsService/AccountsService — nunca
repositories/ ni db/ directo (CLAUDE.md §2/§3). Los formularios viven en
ui/components/dialogo_compra_ahorro.py (construir_nuevo_activo(),
construir_movimiento(), construir_objetivos_movimiento()); acá se arma el
AlertDialog alrededor de cada uno.

--- Estado propio ---

Pestaña, vista del RESUMEN y el BORRADOR de la fila de alta de cada
pestaña (ui["altas"][tab]) en un almacén por página (_ESTADOS_UI): se
conservan cuando ui/app.py reconstruye la pantalla. El estado de cada
tabla (filtros, orden, selección, anchos) lo guarda TablaPlanilla, una por
pestaña (clave "ahorros_<tab>"). Los datos se piden de nuevo en cada
redibujo (SavingsService.get_resumen_por_tipo()).

--- RESUMEN ---

[POR INSTRUMENTO]: una sección por tipo de activo, cada activo con su
broker, su saldo (o sus unidades, en acciones / CEDEARs) y debajo su
reparto entre objetivos (_texto_reparto(): calculado desde los
movimientos, ver "Objetivos"). [POR OBJETIVO]: una sección por objetivo
— todos, también los que no tienen nada — con su meta y la parte que le
toca de cada activo (SavingsService.get_resumen_por_objetivo(), la suma
de lo asignado en cada movimiento); al final, SIN ASIGNAR con lo que
ningún objetivo tiene. Sin tabla de movimientos (pedido explícito).

Editar / eliminar objetivos (pedido del usuario, docs/DATA_MODEL_DECISIONS.md
sección 32): ✎ y 🗑 junto al título de cada objetivo en POR OBJETIVO. ✎
abre el mismo formulario que + NUEVO OBJETIVO, precargado
(_formulario_objetivo()). 🗑 abre
dialogo_compra_ahorro.construir_eliminar_objetivo(): por cada instrumento
donde el objetivo tiene algo, a qué objetivos pasa (lo que no llega al
100% queda SIN ASIGNAR). Las filas de alta que tenían elegido ese objetivo
se acomodan (_renombrar_en_borradores() / _sacar_de_borradores()).

--- Pestañas por tipo ---

Arriba, una tarjeta por activo (en grilla). FCI, PLAZO FIJO, PLAZO FLEX y
OTROS se cuentan en plata: SALDO (CAPITAL en los plazos) — el pedido decía
"Cuotapartes" para los FCI, pero sus movimientos se cargan en pesos, así
que lo que se conoce es el saldo. Botones: + RENDIMIENTO, + APORTE,
− RETIRO. ACCIONES y CEDEARS se cuentan en unidades: CANTIDAD y PRECIO
PROMEDIO (de las compras), botones + COMPRA, + VENTA, + RENDIMIENTO
(dividendos). Todas muestran además OBJETIVOS: el reparto calculado desde
los movimientos, solo lectura (el botón para editar un reparto fijo del
activo ya no existe, ver "Objetivos"). Esos botones abren el diálogo de
movimiento, que sí crea o vincula la transacción del Registro y tiene su
propia sección OBJETIVOS. PLAZO FIJO sin vencimiento: decisión del
usuario (el schema no lo guarda).

"+ NUEVO …" de cada pestaña crea un activo de ese tipo; en ACCIONES,
CEDEARS y los plazos, enseguida abre su primer movimiento (la compra / el
aporte del capital: MOVIMIENTO_TRAS_ALTA).

OTROS (no estaba en el pedido): los activos 'cripto' y 'otro' de antes del
rediseño — entre ellos el "Efectivo reservado en <cuenta>" que crea el
Registro con la categoría Ahorro/Inversión —, que si no solo se verían en
el RESUMEN. No tiene "+ NUEVO": esos nacen en el Registro.

"+ NUEVO OBJETIVO" (barra de título) crea un objetivo de ahorro (no estaba
en el pedido, pero sin objetivos no hay a qué repartir). Se editan y se
eliminan desde RESUMEN · POR OBJETIVO (ver arriba).

--- Tabla de movimientos (cada pestaña de instrumento) ---

Debajo de las tarjetas, los movimientos de los activos de la pestaña
(SavingsService.list_movimientos(), todas las fechas, el más nuevo
primero) en una TablaPlanilla — pedido explícito: cargar a mano los datos
históricos, en el estilo tabla del resto de la app, SIN transacción
asociada sí o sí. Reemplaza al diálogo "VER MOVIMIENTOS" de cada tarjeta.

Columnas: FECHA, ACTIVO, MOVIMIENTO, [CANTIDAD, PRECIO] (solo acciones /
CEDEARs), MONTO, COMISIÓN, MONEDA (la del movimiento, ver "Moneda"),
OBJETIVOS (las asignaciones del movimiento) y NOTAS. MONTO con signo
desde el activo: + lo que entra (compra, aporte, rendimiento), − lo que
sale (venta, retiro); en compra / venta es el bruto, sin la comisión.

Fila de alta: FECHA → ACTIVO (los de la pestaña) → MOVIMIENTO
(SelectorCiclico: COMPRA / VENTA / RENDIMIENTO en unidades, APORTE /
RETIRO / RENDIMIENTO en plata) → los valores → NOTAS → ✓. Llama a
registrar_*() con crear_transaccion=False: nunca crea una transacción del
Registro, aunque el activo tenga cuenta (son datos históricos). Compra /
venta: CANTIDAD + MONTO bruto + COMISIÓN (el PRECIO no se carga: lo
calcula el service); aporte / retiro / rendimiento: MONTO (+ COMISIÓN,
salvo el rendimiento). OBJETIVOS: ver "Objetivos". Al confirmar la fila CONSERVA lo cargado
(pedido explícito: carga en serie editando solo algunos campos), el foco
vuelve a FECHA. Enter nunca guarda salvo con el foco en el ✓ (NOTAS lleva
ahí, TablaPlanilla.tab_a_confirmar()).

Edición inline (CLAUDE.md §10), vía SavingsService.update_movement():
FECHA, CANTIDAD, MONTO, COMISIÓN, MONEDA (solo acciones / CEDEARs) y
NOTAS; el PRECIO es de solo lectura (calculado). ACTIVO y MOVIMIENTO no
se editan (borrar y volver a cargar). Un
movimiento vinculado a una transacción del Registro (🔗 en la columna de
acción) solo deja editar las NOTAS y los OBJETIVOS (CLAUDE.md §4: la
transacción depende de su monto y su fecha).

Barra flotante: Eliminar (SavingsService.delete_movement(), solo el
movimiento: si tenía transacción vinculada, esa queda en el Registro y la
confirmación lo avisa) y Σ de los montos seleccionados con su signo.
Tras cualquier cambio, las tarjetas se recalculan (al_recargar).

--- Instrumentos: editar, eliminar, ocultar (docs/DATA_MODEL_DECISIONS.md sección 35) ---

Cada tarjeta tiene ✎ (EDITAR: dialogo_compra_ahorro.construir_editar_activo();
el tipo no se edita, la moneda solo sin movimientos) y, si ya no tiene
tenencia (con_tenencia de get_resumen_por_tipo()), un segundo ícono según
el caso: 🗑 ELIMINAR si no tiene ningún movimiento (delete_activo(): se
borra del todo), u OCULTAR si tiene historial (ocultar_activo(): activa =
0, no se borra nada). Con saldo o unidades, ninguno de los dos. Ambos piden
confirmación; el service vuelve a validar.

Un instrumento oculto no aparece en el RESUMEN, ni en las tarjetas, ni en
la fila de alta, y sus movimientos no se listan en la tabla. "MOSTRAR
OCULTOS (n)" en la barra de la pestaña (solo si hay alguno; estado por
pestaña, ui["ocultos"]) los muestra atenuados, con " · OCULTO" y un botón
REACTIVAR (reactivar_activo()), junto con sus movimientos. Para eso la
pantalla pide get_resumen_por_tipo(incluir_ocultos=True) y filtra.

--- Moneda (por movimiento, docs/DATA_MODEL_DECISIONS.md sección 33) ---

Pedido del usuario: comprar un CEDEAR en pesos y venderlo en dólares. En
ACCIONES y CEDEARS la fila de alta tiene un Dropdown de MONEDA (CLAUDE.md
§10, default la del activo; vuelve a la del activo al cambiar de ACTIVO,
y como el resto de la fila se conserva entre cargas), la celda MONEDA de
las filas ya cargadas se edita (salvo vinculadas) y la tarjeta muestra un
PRECIO PROMEDIO por moneda. En FCI, plazos y OTROS la moneda es siempre
la del activo (decisión del usuario: un FCI en dólares es otro activo):
MONEDA de solo lectura. Compra / venta se cargan por MONTO bruto (cantidad
× precio, sin la comisión — decisión del usuario), no por precio
unitario: el PRECIO es calculado, solo lectura.

--- Objetivos (por movimiento, docs/DATA_MODEL_DECISIONS.md sección 31) ---

Pedido del usuario: el reparto entre objetivos no es fijo por activo
sino de cada movimiento ("ingreso X al FCI y de eso reparto para estos
objetivos"). La celda OBJETIVOS abre
dialogo_compra_ahorro.construir_objetivos_movimiento(): filas objetivo +
% (con el monto de cada una), + CREAR NUEVO OBJETIVO ahí mismo, y lo que
no llega al 100% queda SIN ASIGNAR.
- Fila de alta: lo elegido vive en el borrador (ui["altas"][tab]
  ["objetivos"]) y, como el resto de la fila, se CONSERVA al confirmar.
  Un RENDIMIENTO arranca en PROPORCIONAL (borrador["proporcional"],
  vuelve a True cada vez que se elige RENDIMIENTO): no se mandan
  asignaciones y el service lo reparte según lo que cada objetivo tenía
  en el activo antes de esa fecha. Abrir el diálogo precarga ese reparto;
  confirmarlo lo deja fijo a mano, "USAR REPARTO PROPORCIONAL" vuelve.
- Movimiento ya cargado: el diálogo → SavingsService.update_asignaciones().
  También en un movimiento vinculado al Registro (la transacción no
  depende de los objetivos, CLAUDE.md §4). En un rendimiento, "USAR
  REPARTO PROPORCIONAL" lo recalcula sin contarse a sí mismo.
"""

from datetime import date, datetime
from typing import Any, Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.savings_service import TIPOS_POR_UNIDADES, SavingsError, SavingsResult, SavingsService
from ui.components import dialogo_compra_ahorro
from ui.components.campo_monto import CampoMonto
from ui.components.tabla_planilla import (
    ANCHO_BORDE,
    ESPACIO_DOT,
    PILL_ALTURA,
    PILL_PADDING_H,
    PILL_RADIO,
    Columna,
    FilaAlta,
    TablaPlanilla,
    estilo_campo,
    mostrar_mensaje,
    pantalla_planilla,
    sin_auto_update,
    sin_borde,
    texto_celda,
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
from ui.theme.tokens import LayoutTokens, TypographyTokens
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
# "retiro" no es un tipo de movimientos_activo: es una venta sin cantidad (FCI, plazos).
TEXTO_MOVIMIENTO = {
    "compra": "COMPRA", "venta": "VENTA", "rendimiento": "RENDIMIENTO", "aporte": "APORTE", "retiro": "RETIRO",
}
COLOR_MOVIMIENTO = {"venta": TEXT_NEGATIVO, "retiro": TEXT_NEGATIVO, "rendimiento": TEXT_POSITIVO}
MOVIMIENTOS_SALIDA = ("venta", "retiro")  # restan del activo (signo − en MONTO)
# Opciones del selector MOVIMIENTO de la fila de alta.
MOVIMIENTOS_UNIDADES = [("compra", "COMPRA"), ("venta", "VENTA"), ("rendimiento", "RENDIMIENTO")]
MOVIMIENTOS_MONTO = [("aporte", "APORTE"), ("retiro", "RETIRO"), ("rendimiento", "RENDIMIENTO")]
TEXTO_SIN_OBJETIVOS = "SIN OBJETIVOS"
TEXTO_SIN_ASIGNAR = "SIN ASIGNAR"
TEXTO_PROPORCIONAL = "PROPORCIONAL"
# Decimales de los porcentajes en pantalla (el service guarda hasta 4).
DECIMALES_PORCENTAJE = 2
TOOLTIP_OBJETIVOS = "CLICK PARA ELEGIR A QUÉ OBJETIVOS VA EL MOVIMIENTO Y EN QUÉ PORCENTAJE."
TEXTO_OBJETIVO_VACIO = "SIN SALDO EN NINGÚN INSTRUMENTO"
# Instrumentos: editar / eliminar / ocultar (ver docstring, "Instrumentos").
TOOLTIP_EDITAR_ACTIVO = "EDITAR INSTRUMENTO"
TOOLTIP_ELIMINAR_ACTIVO = "ELIMINAR (NO TIENE MOVIMIENTOS)"
TOOLTIP_OCULTAR_ACTIVO = "OCULTAR (TIENE HISTORIAL Y YA NO TIENE SALDO)"
TEXTO_MOSTRAR_OCULTOS = "MOSTRAR OCULTOS"
TOOLTIP_MOSTRAR_OCULTOS = "VER LOS INSTRUMENTOS OCULTOS Y SUS MOVIMIENTOS (PARA REACTIVARLOS)"
SUFIJO_OCULTO = " · OCULTO"
OPACIDAD_TARJETA_OCULTA = 0.6
OPACIDAD_TARJETA_VISIBLE = 1.0
TAMANIO_ICONO_TITULO = 16  # ✎ / 🗑 junto al título de cada objetivo (RESUMEN · POR OBJETIVO)
TEXTO_SIN_VALOR = "—"
TEXTO_NOTAS_VACIAS = "NOTAS..."
SEPARADOR_OBJETIVOS = " · "
SEPARADOR_BROKER = " · "
PREFIJO_SUBLINEA = "└ "
DECIMALES_DEFAULT = 2
# objetivos_ahorro no tiene moneda: la meta se carga con estos decimales (como antes del rediseño).
DECIMALES_META = 2

TOOLTIP_VINCULADO = (
    "VINCULADO A UNA TRANSACCIÓN DEL REGISTRO: SOLO SE EDITAN LAS NOTAS Y LOS OBJETIVOS "
    "(PARA CORREGIR EL RESTO, BORRALO Y VOLVÉ A CARGARLO)."
)
TOOLTIP_PRECIO_CALCULADO = "CALCULADO: MONTO / CANTIDAD (EDITÁ EL MONTO O LA CANTIDAD)."
TOOLTIP_MONEDA_MOVIMIENTO = "MONEDA DE ESTE MOVIMIENTO (POR DEFECTO, LA DEL ACTIVO)."
TOOLTIP_MONEDA_ACTIVO = "EN FCI Y PLAZOS LA MONEDA ES SIEMPRE LA DEL ACTIVO."
HINT_CANTIDAD = "CANT."
HINT_PRECIO_CALCULADO = "CALCULADO"
HINT_COMISION = "COMISIÓN"
HINT_MONTO = "MONTO"
HINT_MONTO_BRUTO = "MONTO BRUTO"  # compra / venta: cantidad × precio, sin la comisión

# Pestañas (tabs con subrayado).
TAMANIO_TAB = TypographyTokens.REGISTRO_FONT_SALDO_BAR
PADDING_TAB_H = 16
PADDING_TAB_V = 10
ALTO_INDICADOR_TAB = 2

TAMANIO_PILLS = TypographyTokens.REGISTRO_FONT_SALDO_BAR
ESPACIO_PILLS = ESPACIO_DOT
ESPACIADO = 12
ESPACIADO_LINEAS = 4
PADDING_TARJETA = 12
RADIO_TARJETA = 8
ANCHO_TARJETA = 340
SANGRIA_RESUMEN = 16
ANCHO_LINEA_TITULO_SECCION = 24
ANCHO_DIALOGO = 380

# Columnas de la tabla de movimientos (px; las redimensionables, proporción inicial).
ANCHO_COL_FECHA = 100
ANCHO_COL_ACTIVO = 180
ANCHO_COL_MOVIMIENTO = 110
ANCHO_COL_CANTIDAD = 90
ANCHO_COL_PRECIO = 120
ANCHO_COL_COMISION = 110
ANCHO_COL_MONTO = 140
ANCHO_COL_MONEDA = 70
ANCHO_COL_OBJETIVOS = 160
ANCHO_COL_NOTAS = 160


def _columnas(por_unidades: bool) -> list[Columna]:
    """Columnas de la tabla de una pestaña: CANTIDAD y PRECIO solo en acciones / CEDEARs."""
    derecha = ft.Alignment.CENTER_RIGHT
    columnas = [
        Columna("fecha", "FECHA", ANCHO_COL_FECHA, redimensionable=False),
        Columna("activo", "ACTIVO", ANCHO_COL_ACTIVO),
        Columna("movimiento", "MOVIMIENTO", ANCHO_COL_MOVIMIENTO, redimensionable=False),
    ]
    if por_unidades:
        columnas += [
            Columna("cantidad", "CANTIDAD", ANCHO_COL_CANTIDAD, redimensionable=False, alineacion=derecha),
            Columna("precio", "PRECIO", ANCHO_COL_PRECIO, redimensionable=False, alineacion=derecha),
        ]
    columnas += [
        Columna("monto", "MONTO", ANCHO_COL_MONTO, redimensionable=False, alineacion=derecha),
        Columna("comision", "COMISIÓN", ANCHO_COL_COMISION, redimensionable=False, alineacion=derecha),
        Columna("moneda", "MONEDA", ANCHO_COL_MONEDA, redimensionable=False),
        Columna("objetivos", "OBJETIVOS", ANCHO_COL_OBJETIVOS),
        Columna("notas", "NOTAS", ANCHO_COL_NOTAS),
    ]
    return columnas


# ============================================================
# ESTADO PROPIO POR PÁGINA
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _alta_vacia() -> dict:
    """
    Borrador de la fila de alta de una pestaña: fecha vacía = hoy; activo / movimiento None = el primero.
    objetivos: [{objetivo_id, objetivo_nombre, porcentaje}] elegidos en su diálogo; proporcional: un
    rendimiento sin objetivos elegidos a mano (ver docstring, "Objetivos").
    """
    return {
        "fecha": "", "activo_id": None, "movimiento": None, "cantidad": "", "moneda_id": None, "comision": "",
        "monto": "", "notas": "", "objetivos": [], "proporcional": True,
    }


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        # ocultos: pestaña → ¿"MOSTRAR OCULTOS" prendido? (ver docstring, "Instrumentos").
        ui = {"tab": TAB_DEFAULT, "vista": VISTA_DEFAULT, "altas": {}, "ocultos": {}}
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


def _fmt_porcentaje(porcentaje: Optional[float]) -> str:
    """' 94.4%' (con DECIMALES_PORCENTAJE decimales como mucho); '' si no hay porcentaje (activo sin nada)."""
    return f" {round(porcentaje, DECIMALES_PORCENTAJE):g}%" if porcentaje is not None else ""


def _texto_reparto(entrada: dict) -> str:
    """
    El reparto de un activo calculado desde sus movimientos (get_resumen_por_tipo():
    objetivos + sin_asignar): 'MOTO 94.4% · TERRENEITOR 5.6%', y SIN ASIGNAR si queda algo.
    """
    partes = [f"{o['nombre'].upper()}{_fmt_porcentaje(o['porcentaje'])}" for o in entrada["objetivos"]]
    libre = entrada["sin_asignar"]["porcentaje"]
    if partes and libre is not None and round(libre, DECIMALES_PORCENTAJE) > 0:
        partes.append(f"{TEXTO_SIN_ASIGNAR}{_fmt_porcentaje(libre)}")
    return SEPARADOR_OBJETIVOS.join(partes) or TEXTO_SIN_OBJETIVOS


def _texto_asignaciones(asignaciones: list[dict]) -> str:
    """Las asignaciones de UN movimiento (list_movimientos(), o el borrador de la fila de alta)."""
    return SEPARADOR_OBJETIVOS.join(
        f"{a['objetivo_nombre'].upper()}{_fmt_porcentaje(a['porcentaje'])}" for a in asignaciones
    ) or TEXTO_SIN_OBJETIVOS


def _nombre_con_broker(nombre: str, broker: Optional[str]) -> str:
    return f"{broker}{SEPARADOR_BROKER}{nombre}".upper() if broker else nombre.upper()


def _clave_movimiento(movimiento: dict) -> str:
    """Clave de TEXTO_MOVIMIENTO: una venta sin cantidad es un RETIRO (FCI, plazos)."""
    if movimiento["tipo"] == "venta" and movimiento["cantidad"] is None:
        return "retiro"
    return movimiento["tipo"]


def _minor_de(campo: CampoMonto, decimales: int, nombre: str, opcional: bool = False) -> int:
    """
    Texto de un CampoMonto de la fila de alta → minor units. Opcional:
    vacío = 0. Lanza ValueError (MAYÚSCULAS) si no es un número válido
    (forma; las reglas de dominio las pone el service).
    """
    # confirmar() resuelve una fórmula pendiente (y marca el borde si no es válida).
    if not campo.confirmar():
        raise ValueError(f"{nombre} NO ES UN NÚMERO VÁLIDO.")
    texto = (campo.texto or "").strip()
    if not texto and opcional:
        return 0
    valor = numero(texto)
    if valor is None or valor < 0 or (valor == 0 and not opcional):
        raise ValueError(f"{nombre} TIENE QUE SER UN NÚMERO MAYOR A 0.")
    return amount_to_minor(valor, decimales)


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


def _titulo_seccion(texto: str, acciones: Optional[list[ft.Control]] = None) -> ft.Control:
    """'── FCI ─────────'; con acciones (ej. ✎ 🗑 de un objetivo), justo después del texto."""
    return ft.Row(
        [
            ft.Container(width=ANCHO_LINEA_TITULO_SECCION, height=ANCHO_BORDE, bgcolor=BORDER_DEFAULT),
            ft.Text(
                texto, size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                color=TEXT_SECONDARY,
            ),
            *(acciones or []),
            ft.Container(expand=True, height=ANCHO_BORDE, bgcolor=BORDER_DEFAULT),
        ],
        spacing=ESPACIADO,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _icono_titulo(icono: str, tooltip: str, on_click: Callable[[], None]) -> ft.Control:
    return ft.IconButton(
        icon=icono, icon_size=TAMANIO_ICONO_TITULO, icon_color=TEXT_SECONDARY, tooltip=tooltip,
        on_click=lambda e: on_click(),
    )


def _texto_meta(objetivo: dict) -> str:
    """'META: 513,842.10 · PARA EL 2026-12-31' (los objetivos no tienen moneda: la meta va sin símbolo)."""
    partes = []
    if objetivo["monto_meta_minor"] is not None:
        partes.append(f"META: {amount_display(objetivo['monto_meta_minor'], DECIMALES_META, '')}")
    if objetivo["fecha_meta"]:
        partes.append(f"PARA EL {objetivo['fecha_meta']}")
    return SEPARADOR_OBJETIVOS.join(partes)


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
    vista: dict[str, Any] = {"control_tabla": None}
    monedas_por_id = {m["id"]: dict(m) for m in accounts_service.list_currencies()}

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

    def _entradas_de(tab: str, con_ocultos: bool = False) -> list[dict]:
        """Los activos de la pestaña; los ocultos solo con con_ocultos (ver docstring, "Instrumentos")."""
        return [
            e for tipo in TIPOS_POR_TAB[tab] for e in datos["resumen"].get(tipo, [])
            if e["activa"] or con_ocultos
        ]

    def _mostrar_ocultos(tab: str) -> bool:
        return ui.setdefault("ocultos", {}).get(tab, False)

    def _refrescar_resumen() -> None:
        try:
            # Con los ocultos: las pestañas los muestran si se pide; el RESUMEN los saltea.
            datos["resumen"] = savings_service.get_resumen_por_tipo(incluir_ocultos=True)
        except SavingsError as err:
            _error(str(err))

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

    # --- Instrumentos: editar / eliminar / ocultar / reactivar (ver docstring, "Instrumentos") ---

    def _editar_activo(entrada: dict) -> None:
        formulario = dialogo_compra_ahorro.construir_editar_activo(
            page, savings_service, accounts_service, entrada, lambda resultado: _tras_guardar(resultado.message),
        )
        _abrir_formulario(f"EDITAR — {entrada['activo'].upper()}", formulario, "GUARDAR")

    def _confirmar_accion_activo(titulo: str, mensaje: str, texto_boton: str, accion: Callable[[], SavingsResult]) -> None:
        """Diálogo de confirmación; el service decide (y explica) si se puede."""
        def _confirmar(e=None) -> None:
            try:
                resultado = accion()
            except SavingsError as err:
                _cerrar_dialogo()
                _error(str(err))
                return
            _tras_guardar(resultado.message)

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text(titulo),
            content=ft.Container(width=ANCHO_DIALOGO, content=ft.Text(mensaje, size=TypographyTokens.METADATA_SIZE)),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text(texto_boton), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _eliminar_activo(entrada: dict) -> None:
        _confirmar_accion_activo(
            f"ELIMINAR — {entrada['activo'].upper()}",
            "NO TIENE MOVIMIENTOS: SE BORRA DEL TODO.",
            "ELIMINAR", lambda: savings_service.delete_activo(entrada["activo_id"]),
        )

    def _ocultar_activo(entrada: dict) -> None:
        _confirmar_accion_activo(
            f"OCULTAR — {entrada['activo'].upper()}",
            "TIENE HISTORIAL Y YA NO TIENE SALDO: SE ESCONDE DE LA PANTALLA SIN BORRAR NADA. "
            f"PARA VOLVER A VERLO, {TEXTO_MOSTRAR_OCULTOS} → REACTIVAR.",
            "OCULTAR", lambda: savings_service.ocultar_activo(entrada["activo_id"]),
        )

    def _reactivar_activo(entrada: dict) -> None:
        try:
            resultado = savings_service.reactivar_activo(entrada["activo_id"])
        except SavingsError as err:
            _error(str(err))
            return
        _ok(resultado.message)
        _redibujar()

    def _formulario_objetivo(objetivo: Optional[dict] = None) -> None:
        """Nuevo objetivo (objetivo=None) o edición de uno existente: nombre, meta y fecha meta (ver docstring)."""
        campo_nombre = ft.TextField(label="NOMBRE", autofocus=True, value=objetivo["nombre"] if objetivo else None)
        campo_meta = CampoMonto(
            page, on_confirmar=lambda m: None, decimales=DECIMALES_META, dense=False, label="META (OPCIONAL)",
            valor_inicial_minor=objetivo["monto_meta_minor"] if objetivo else None,
            persistir_formula=objetivo is not None,  # CLAUDE.md §9: muestra un valor guardado
        )
        campo_fecha_meta = ft.TextField(
            label="FECHA META AAAA-MM-DD (OPCIONAL)", value=(objetivo["fecha_meta"] or "") if objetivo else None,
        )
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
                if objetivo is None:
                    savings_service.create_objetivo(nombre=nombre, monto_meta_minor=monto_meta_minor, fecha_meta=fecha_meta)
                    mensaje = f"OBJETIVO '{nombre}' CREADO."
                else:
                    mensaje = savings_service.update_objetivo(objetivo["id"], nombre, monto_meta_minor, fecha_meta).message
            except SavingsError as err:
                _falla(str(err).upper())
                return
            if objetivo is not None:
                _renombrar_en_borradores(objetivo["id"], nombre)
            _tras_guardar(mensaje)

        page.show_dialog(ft.AlertDialog(
            modal=True,
            title=ft.Text("EDITAR OBJETIVO DE AHORRO" if objetivo else "NUEVO OBJETIVO DE AHORRO"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [campo_nombre, campo_meta.control, campo_fecha_meta, texto_error], tight=True, spacing=ESPACIADO,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("CANCELAR"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("GUARDAR" if objetivo else "CREAR"), on_click=lambda e: _confirmar()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        ))

    def _eliminar_objetivo(objetivo: dict) -> None:
        def _on_exito(resultado: SavingsResult) -> None:
            _sacar_de_borradores(objetivo["id"])
            _tras_guardar(resultado.message)

        formulario = dialogo_compra_ahorro.construir_eliminar_objetivo(page, savings_service, objetivo, _on_exito)
        _abrir_formulario(f"ELIMINAR OBJETIVO — {objetivo['nombre'].upper()}", formulario, "ELIMINAR")

    # Las filas de alta guardan los objetivos elegidos con su nombre (ver docstring, "Objetivos"):
    # se acomodan cuando un objetivo cambia de nombre o se elimina (si no, el alta mandaría un id borrado).

    def _renombrar_en_borradores(objetivo_id: str, nombre: str) -> None:
        for borrador in ui["altas"].values():
            for asignacion in borrador.get("objetivos", []):
                if asignacion["objetivo_id"] == objetivo_id:
                    asignacion["objetivo_nombre"] = nombre

    def _sacar_de_borradores(objetivo_id: str) -> None:
        for borrador in ui["altas"].values():
            borrador["objetivos"] = [a for a in borrador.get("objetivos", []) if a["objetivo_id"] != objetivo_id]

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
            hay_activos = False
            for tipo, entradas in datos["resumen"].items():
                visibles = [e for e in entradas if e["activa"]]  # los ocultos no van en el RESUMEN
                if not visibles:
                    continue
                hay_activos = True
                controles.append(_titulo_seccion(TITULO_TIPO.get(tipo, tipo.upper())))
                for e in visibles:
                    controles += [
                        _fila_resumen(_nombre_con_broker(e["activo"], e["broker"]), _texto_tenencia(e)),
                        _sublinea(_texto_reparto(e)),
                    ]
            if not hay_activos:
                controles.append(ft.Text("TODAVÍA NO HAY ACTIVOS — CREALOS DESDE SU PESTAÑA.", italic=True, color=TEXT_MUTED))
        else:
            # Agrupadas por id (get_resumen_por_objetivo() agrupa por nombre); SIN ASIGNAR = objetivo_id None.
            partes_por_objetivo: dict[Optional[str], list[dict]] = {}
            for partes in savings_service.get_resumen_por_objetivo().values():
                for p in partes:
                    partes_por_objetivo.setdefault(p["objetivo_id"], []).append(p)
            # Todos los objetivos, también los que no tienen nada: acá se editan y se eliminan.
            objetivos = [dict(o) for o in savings_service.list_objetivos()]
            for objetivo in objetivos:
                controles.append(_titulo_seccion(objetivo["nombre"].upper(), acciones=[
                    _icono_titulo(ft.Icons.EDIT_OUTLINED, "EDITAR OBJETIVO", lambda o=objetivo: _formulario_objetivo(o)),
                    _icono_titulo(ft.Icons.DELETE_OUTLINE, "ELIMINAR OBJETIVO", lambda o=objetivo: _eliminar_objetivo(o)),
                ]))
                if objetivo["monto_meta_minor"] is not None or objetivo["fecha_meta"]:
                    controles.append(_sublinea(_texto_meta(objetivo)))
                partes = partes_por_objetivo.get(objetivo["id"], [])
                for p in partes:
                    controles.append(_fila_resumen(f"{NOMBRE_TIPO.get(p['tipo'], '')} {p['activo'].upper()}", _texto_tenencia(p)))
                if not partes:
                    controles.append(_fila_resumen(TEXTO_OBJETIVO_VACIO, ""))
            sin_asignar = partes_por_objetivo.get(None, [])
            if sin_asignar:
                controles.append(_titulo_seccion(TEXTO_SIN_ASIGNAR))
                for p in sin_asignar:
                    controles.append(_fila_resumen(f"{NOMBRE_TIPO.get(p['tipo'], '')} {p['activo'].upper()}", _texto_tenencia(p)))
            if not objetivos and not sin_asignar:
                controles.append(ft.Text(
                    "TODAVÍA NO HAY OBJETIVOS — CREALOS CON + NUEVO OBJETIVO.", italic=True, color=TEXT_MUTED,
                ))
        return controles

    # ------------------------------------------------------------
    # PESTAÑAS POR TIPO — tarjetas
    # ------------------------------------------------------------

    def _linea(etiqueta: str, valor: str) -> ft.Control:
        return ft.Row(
            [_texto(f"{etiqueta}:", color=TEXT_SECONDARY), _texto(valor, weight=PESO_MONTO, expand=True)],
            spacing=ESPACIADO_LINEAS,
        )

    def _acciones_tarjeta(entrada: dict) -> list[ft.Control]:
        """✎ siempre; 🗑 según el caso (ver docstring, "Instrumentos"): ELIMINAR sin movimientos, OCULTAR sin tenencia."""
        acciones = [_icono_titulo(ft.Icons.EDIT_OUTLINED, TOOLTIP_EDITAR_ACTIVO, lambda: _editar_activo(entrada))]
        if entrada["activa"] and not entrada["con_tenencia"]:
            if entrada["movimientos"] == 0:
                acciones.append(_icono_titulo(ft.Icons.DELETE_OUTLINE, TOOLTIP_ELIMINAR_ACTIVO, lambda: _eliminar_activo(entrada)))
            else:
                acciones.append(_icono_titulo(ft.Icons.VISIBILITY_OFF_OUTLINED, TOOLTIP_OCULTAR_ACTIVO, lambda: _ocultar_activo(entrada)))
        return acciones

    def _tarjeta(entrada: dict) -> ft.Control:
        tipo = entrada["tipo"]
        titulo = entrada["activo"].upper() + (f" ({entrada['broker'].upper()})" if entrada["broker"] else "")
        if not entrada["activa"]:
            titulo += SUFIJO_OCULTO
        if entrada["por_unidades"]:
            lineas = [_linea("CANTIDAD", _texto_tenencia(entrada, UNIDADES_TIPO.get(tipo, UNIDADES_DEFAULT)))]
            if entrada["precios_promedio"]:
                # Uno por moneda de compra (docs/DATA_MODEL_DECISIONS.md sección 33).
                lineas.append(_linea("PRECIO PROMEDIO", SEPARADOR_OBJETIVOS.join(
                    f"{amount_display(p['precio_promedio_minor'], p['decimales'], p['simbolo'])} {p['moneda']}"
                    for p in entrada["precios_promedio"]
                )))
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
        lineas.append(_linea("OBJETIVOS", _texto_reparto(entrada)))
        if not entrada["activa"]:
            # Oculto: sin movimientos nuevos; solo volver a mostrarlo.
            botones = [_boton("REACTIVAR", lambda: _reactivar_activo(entrada))]
        return ft.Container(
            width=ANCHO_TARJETA,
            bgcolor=BG_SUPERFICIE,
            border=ft.Border.all(ANCHO_BORDE, BORDER_DEFAULT),
            border_radius=RADIO_TARJETA,
            padding=PADDING_TARJETA,
            # Flet exige un float entre 0 y 1: None rompe el update (y con él el refresco tras eliminar).
            opacity=OPACIDAD_TARJETA_OCULTA if not entrada["activa"] else OPACIDAD_TARJETA_VISIBLE,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            _texto(titulo, weight=TypographyTokens.SECTION_TITLE_WEIGHT, size=TypographyTokens.SECTION_TITLE_SIZE, expand=True),
                            _texto(entrada["moneda"], color=TEXT_SECONDARY),
                            *_acciones_tarjeta(entrada),
                        ],
                        spacing=0,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    *lineas,
                    ft.Row(botones, spacing=0, wrap=True),
                ],
                spacing=ESPACIADO_LINEAS,
            ),
        )

    def _tarjetas(tab: str) -> ft.Control:
        entradas = _entradas_de(tab, con_ocultos=_mostrar_ocultos(tab))
        if not entradas:
            return ft.Text(f"NO HAY ACTIVOS EN {dict(TABS)[tab]} TODAVÍA.", italic=True, color=TEXT_MUTED)
        return ft.Row([_tarjeta(e) for e in entradas], wrap=True, spacing=ESPACIADO, run_spacing=ESPACIADO)

    # ------------------------------------------------------------
    # PESTAÑAS POR TIPO — tabla de movimientos (ver docstring)
    # ------------------------------------------------------------

    def _tabla_movimientos(tab: str, contenedor_tarjetas: ft.Container) -> ft.Control:
        tipos = TIPOS_POR_TAB[tab]
        por_unidades = all(t in TIPOS_POR_UNIDADES for t in tipos)
        opciones_movimiento = MOVIMIENTOS_UNIDADES if por_unidades else MOVIMIENTOS_MONTO
        # Todos (para el broker de cada fila) y los visibles (los únicos que ofrece la fila de alta).
        todas = {e["activo_id"]: e for e in _entradas_de(tab, con_ocultos=True)}
        entradas = {id_: e for id_, e in todas.items() if e["activa"]}
        opciones_activo = [(id_, _nombre_con_broker(e["activo"], e["broker"])) for id_, e in entradas.items()]
        # Moneda por movimiento: solo en acciones / CEDEARs (ver docstring, "Moneda").
        opciones_moneda = [(str(id_), m["codigo"]) for id_, m in monedas_por_id.items()]

        # --- Datos ---

        def _cargar_filas() -> list[dict]:
            filtro = tipos[0] if len(tipos) == 1 else None
            filas = []
            for m in savings_service.list_movimientos(tipo_activo=filtro):  # ya son dicts (CLAUDE.md §11)
                if m["activo_tipo"] not in tipos:
                    continue
                if not m["activo_activa"] and not _mostrar_ocultos(tab):
                    continue  # movimientos de un instrumento oculto: solo con MOSTRAR OCULTOS
                moneda = monedas_por_id.get(m["moneda_id"], {})
                entrada = todas.get(m["activo_id"])
                m.update(
                    currency_code=moneda.get("codigo", ""),
                    currency_symbol=moneda.get("simbolo") or "",
                    decimales=moneda.get("decimales", DECIMALES_DEFAULT),
                    broker=entrada["broker"] if entrada else None,
                    movimiento=_clave_movimiento(m),
                )
                filas.append(m)
            return filas

        def _signo(m: dict) -> int:
            return -1 if m["movimiento"] in MOVIMIENTOS_SALIDA else 1

        def _texto_monto(m: dict) -> str:
            # El MONTO que cargó el usuario: en compra / venta, el bruto (sin la comisión, sección 33).
            signo = "-" if _signo(m) < 0 else "+"
            return f"{signo} {amount_display(m['monto_minor'], m['decimales'], m['currency_symbol'])}"

        def _color_monto(m: dict) -> str:
            return TEXT_NEGATIVO if _signo(m) < 0 else TEXT_POSITIVO

        def _texto_opcional(m: dict, clave: str) -> str:
            valor = m[clave]
            return amount_display(valor, m["decimales"], m["currency_symbol"]) if valor else TEXTO_SIN_VALOR

        def _valor_columna(m: dict, clave: str) -> str:
            """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
            if clave == "fecha":
                return m["fecha"] or ""
            if clave == "activo":
                return _nombre_con_broker(m["activo_nombre"], m["broker"])
            if clave == "movimiento":
                return TEXTO_MOVIMIENTO[m["movimiento"]]
            if clave == "cantidad":
                return _fmt_unidades(m["cantidad"]) if m["cantidad"] is not None else TEXTO_SIN_VALOR
            if clave == "precio":
                return _texto_opcional(m, "precio_unitario_minor")
            if clave == "monto":
                return _texto_monto(m)
            if clave == "comision":
                return _texto_opcional(m, "comision_minor")
            if clave == "moneda":
                return m["currency_code"]
            if clave == "objetivos":
                return _texto_asignaciones(m["asignaciones"])
            return m["notas"] or ""

        def _clave_orden(m: dict, clave: str) -> Any:
            escala = 10 ** m["decimales"]
            if clave == "monto":
                return _signo(m) * m["monto_minor"] / escala
            if clave == "cantidad":
                return m["cantidad"] or 0
            if clave == "precio":
                return (m["precio_unitario_minor"] or 0) / escala
            if clave == "comision":
                return (m["comision_minor"] or 0) / escala
            return _valor_columna(m, clave).lower()

        def _firma(m: dict) -> tuple:
            return (
                m["fecha"], m["activo_nombre"], m["broker"], m["tipo"], m["cantidad"], m["precio_unitario_minor"],
                m["comision_minor"], m["monto_total_minor"], m["currency_code"], m["notas"], m["transaccion_id"],
                tuple((a["objetivo_nombre"], a["porcentaje"]) for a in m["asignaciones"]),
            )

        def _al_recargar() -> list[ft.Control]:
            # Las tarjetas muestran la tenencia: cambian con cada movimiento.
            _refrescar_resumen()
            contenedor_tarjetas.content = _tarjetas(tab)
            return [contenedor_tarjetas]

        # --- Fila de alta ---

        ui.setdefault("altas", {}).setdefault(tab, _alta_vacia())
        alta_refs: dict[str, Any] = {}

        def _guardar_borrador_alta() -> None:
            if not alta_refs:
                return
            anterior = ui["altas"][tab]
            ui["altas"][tab] = {
                "fecha": alta_refs["fecha"].value or "",
                "activo_id": alta_refs["activo"].id_seleccionado,
                "movimiento": alta_refs["movimiento"].valor,
                "cantidad": (alta_refs["cantidad"].value or "") if por_unidades else "",
                "moneda_id": alta_refs["moneda"].value if por_unidades else None,
                "comision": alta_refs["comision"].texto,
                "monto": alta_refs["monto"].texto,
                "notas": alta_refs["notas"].value or "",
                # Los objetivos no son campos de la fila: los escribe su diálogo (_abrir_objetivos_alta()).
                "objetivos": anterior.get("objetivos", []),
                "proporcional": anterior.get("proporcional", True),
            }

        def _on_cambio_borrador(e=None) -> None:
            # Solo guarda el borrador: el texto ya está en pantalla.
            _guardar_borrador_alta()
            sin_auto_update()

        def _construir_alta() -> FilaAlta:
            borrador = ui["altas"][tab]
            # Mientras se arma la fila, _guardar_borrador_alta() no hace nada:
            # leería los campos de la fila anterior, que ya no está en pantalla.
            alta_refs.clear()
            claves_movimiento = [clave for clave, _ in opciones_movimiento]
            movimiento_inicial = (
                borrador["movimiento"] if borrador["movimiento"] in claves_movimiento else claves_movimiento[0]
            )
            activo_inicial = (
                borrador["activo_id"] if borrador["activo_id"] in entradas
                else (opciones_activo[0][0] if opciones_activo else None)
            )
            # Moneda inicial (acciones / CEDEARs): la del borrador, si no la del activo inicial.
            moneda_inicial = borrador.get("moneda_id") or (
                str(entradas[activo_inicial]["moneda_id"]) if activo_inicial else None
            )
            # Decimales de los CampoMonto: los de la moneda inicial. Al confirmar
            # se convierte con los de la moneda del movimiento (mismo criterio que Deudas).
            decimales = (
                monedas_por_id[int(moneda_inicial)]["decimales"]
                if moneda_inicial and int(moneda_inicial) in monedas_por_id else DECIMALES_DEFAULT
            )

            texto_moneda = texto_celda("", color=TEXT_SECONDARY)
            # Acciones / CEDEARs: la moneda es de cada movimiento (ver docstring, "Moneda").
            dropdown_moneda = ft.Dropdown(
                options=[ft.dropdown.Option(key=clave, text=texto) for clave, texto in opciones_moneda],
                value=moneda_inicial, dense=LayoutTokens.CELDA_DENSE, text_size=TypographyTokens.REGISTRO_FONT_CELDA,
                border=ft.InputBorder.NONE, expand=True, text_align=ft.TextAlign.CENTER,
                on_select=_on_cambio_borrador, tooltip=TOOLTIP_MONEDA_MOVIMIENTO,
            )
            texto_objetivos = texto_celda("", color=TEXT_SECONDARY, tooltip=TOOLTIP_OBJETIVOS)
            celda_objetivos = ft.Container(
                content=texto_objetivos, alignment=ft.Alignment.CENTER, on_click=lambda e: _abrir_objetivos_alta(),
            )

            def _proporcional() -> bool:
                """Un rendimiento sin objetivos elegidos a mano: los reparte el service (get_reparto_proporcional())."""
                return selector_movimiento.valor == "rendimiento" and ui["altas"][tab].get("proporcional", True)

            def _pintar_objetivos() -> None:
                texto_objetivos.value = (
                    TEXTO_PROPORCIONAL if _proporcional() else _texto_asignaciones(ui["altas"][tab].get("objetivos", []))
                )
                texto_objetivos.tooltip = f"{texto_objetivos.value} — {TOOLTIP_OBJETIVOS}"

            def _mostrar_activo() -> None:
                entrada = entradas.get(campo_activo.id_seleccionado or "")
                texto_moneda.value = entrada["moneda"] if entrada else ""

            def _moneda_alta(entrada: dict) -> dict:
                """La moneda del movimiento: la elegida (acciones / CEDEARs) o la del activo."""
                if por_unidades and dropdown_moneda.value and int(dropdown_moneda.value) in monedas_por_id:
                    return monedas_por_id[int(dropdown_moneda.value)]
                return monedas_por_id.get(entrada["moneda_id"], {"decimales": entrada["decimales"], "simbolo": entrada["simbolo"]})

            def _monto_alta(entrada: dict) -> Optional[int]:
                """Para el diálogo de objetivos: el total de la fila (en compra / venta, monto ± comisión), si se puede leer."""
                valor = numero(campo_monto.texto or "")
                if valor and _con_unidades():
                    signo = 1 if selector_movimiento.valor == "compra" else -1
                    valor += signo * (numero(campo_comision.texto or "") or 0)
                return amount_to_minor(valor, _moneda_alta(entrada)["decimales"]) if valor and valor > 0 else None

            def _abrir_objetivos_alta() -> None:
                _guardar_borrador_alta()
                entrada = entradas.get(campo_activo.id_seleccionado or "")
                moneda = _moneda_alta(entrada) if entrada is not None else {}
                es_rendimiento = selector_movimiento.valor == "rendimiento"
                if _proporcional() and entrada is not None:
                    try:
                        iniciales = savings_service.get_reparto_proporcional(
                            entrada["activo_id"], (campo_fecha.value or "").strip(),
                        )
                    except SavingsError:
                        iniciales = []  # fecha inválida todavía: el diálogo arranca vacío
                else:
                    iniciales = ui["altas"][tab].get("objetivos", [])

                def _confirmar(asignaciones: Optional[list[dict]]) -> None:
                    alta = ui["altas"][tab]
                    if asignaciones is None:  # USAR REPARTO PROPORCIONAL (solo rendimientos)
                        alta["proporcional"] = True
                    else:
                        alta["objetivos"] = [
                            {"objetivo_id": a["objetivo_id"], "objetivo_nombre": a["nombre"], "porcentaje": a["porcentaje"]}
                            for a in asignaciones
                        ]
                        if es_rendimiento:
                            alta["proporcional"] = False
                    _cerrar_dialogo()
                    _pintar_objetivos()
                    tabla.refrescar(celda_objetivos)

                formulario = dialogo_compra_ahorro.construir_objetivos_movimiento(
                    page, savings_service, iniciales, _confirmar,
                    monto_minor=_monto_alta(entrada) if entrada is not None else None,
                    decimales=moneda.get("decimales", DECIMALES_DEFAULT),
                    simbolo=moneda.get("simbolo") or "",
                    con_proporcional=es_rendimiento,
                )
                _abrir_formulario("OBJETIVOS DEL MOVIMIENTO", formulario)

            def _on_activo(id_activo: Optional[str]) -> None:
                # CampoFiltrable parchea la fila (tabla.pagina_alta) justo después.
                _mostrar_activo()
                entrada = entradas.get(id_activo or "")
                if por_unidades and entrada is not None:
                    dropdown_moneda.value = str(entrada["moneda_id"])  # otro activo: vuelve a su moneda
                _guardar_borrador_alta()

            def _campo_monto(texto: str, hint: str, on_avanzar: Callable[[], None]) -> CampoMonto:
                # persistir_formula=True: recuerda la fórmula mientras la fila no se guarde.
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
                campo.control.on_change = _on_cambio_borrador  # CampoMonto no usa on_change
                if texto:
                    campo.control.value = texto
                return campo

            def _con_unidades() -> bool:
                return por_unidades and selector_movimiento.valor in ("compra", "venta")

            def _habilitar(movimiento: str) -> None:
                """Compra / venta: cantidad + monto bruto (el precio lo calcula el service). Rendimiento: sin comisión."""
                if por_unidades:
                    campo_cantidad.disabled = movimiento not in ("compra", "venta")
                    campo_monto.control.hint_text = HINT_MONTO_BRUTO if movimiento in ("compra", "venta") else HINT_MONTO
                campo_comision.control.disabled = movimiento == "rendimiento"

            def _on_movimiento(movimiento: str) -> None:
                _habilitar(movimiento)
                _guardar_borrador_alta()
                if movimiento == "rendimiento":  # un rendimiento arranca en PROPORCIONAL (ver docstring)
                    ui["altas"][tab]["proporcional"] = True
                _pintar_objetivos()
                tabla.pagina_alta.update()  # la fila entera: cambian los campos habilitados

            def _primer_valor() -> ft.Control:
                return campo_cantidad if _con_unidades() else campo_monto.control

            def _tras_monto() -> ft.Control:
                # La comisión va después del monto (si aplica: un rendimiento no tiene).
                if not campo_comision.control.disabled:
                    return campo_comision.control
                return campo_notas

            celda_fecha, campo_fecha = tabla.campo_fecha_alta(
                borrador["fecha"] or date.today().isoformat(),
                on_cambio=_guardar_borrador_alta, on_submit=lambda: tabla.enfocar(campo_activo.campo_texto),
            )
            campo_activo = tabla.campo_filtrable_alta(
                "activo", opciones_activo, _on_activo, placeholder="ACTIVO", valor_inicial_id=activo_inicial,
                on_avanzar=lambda: tabla.enfocar(_primer_valor()),
            )
            # MOVIMIENTO es un botón (Enter lo cambiaría): la cadena de Enter lo saltea; con Tab se llega igual.
            selector_movimiento = tabla.selector_alta(
                opciones_movimiento, movimiento_inicial, _on_movimiento, "MOVIMIENTO", COLOR_MOVIMIENTO,
            )
            campo_notas = ft.TextField(
                value=borrador["notas"], hint_text=TEXTO_NOTAS_VACIAS, text_align=ft.TextAlign.CENTER,
                on_change=_on_cambio_borrador, **estilo_campo(),
            )
            campo_monto = _campo_monto(borrador["monto"], HINT_MONTO, lambda: tabla.enfocar(_tras_monto()))
            campo_comision = _campo_monto(borrador["comision"], HINT_COMISION, lambda: tabla.enfocar(campo_notas))
            celdas: dict[str, ft.Control] = {}
            refs_unidades: dict[str, Any] = {}
            if por_unidades:
                campo_cantidad = ft.TextField(
                    value=borrador["cantidad"], hint_text=HINT_CANTIDAD, text_align=ft.TextAlign.RIGHT,
                    on_change=_on_cambio_borrador, **estilo_campo(),
                )
                campo_cantidad.on_submit = lambda e: tabla.enfocar(campo_monto.control)
                # PRECIO no se carga: lo calcula el service (monto / cantidad, ver docstring, "Moneda").
                texto_precio = texto_celda(HINT_PRECIO_CALCULADO, color=TEXT_MUTED, tooltip=TOOLTIP_PRECIO_CALCULADO)
                celdas.update(cantidad=campo_cantidad, precio=texto_precio)
                refs_unidades.update(cantidad=campo_cantidad, moneda=dropdown_moneda)
            boton_confirmar = tabla.boton_confirmar_alta(f"AGREGAR EN {dict(TABS)[tab]}", lambda: _confirmar_alta())

            # Sync inicial (todavía sin montar: nada que parchear).
            _mostrar_activo()
            _pintar_objetivos()
            _habilitar(movimiento_inicial)
            # Todas las refs juntas, al final: hasta acá _guardar_borrador_alta() no hace nada.
            alta_refs.update(
                fecha=campo_fecha, activo=campo_activo, movimiento=selector_movimiento, comision=campo_comision,
                monto=campo_monto, notas=campo_notas, boton=boton_confirmar, **refs_unidades,
            )
            # NOTAS es el último campo: Enter o Tab llevan al ✓ sin guardar (ahí Enter confirma).
            tabla.tab_a_confirmar(campo_notas)

            celdas.update(
                fecha=celda_fecha,
                activo=campo_activo.control,
                movimiento=selector_movimiento.control,
                monto=campo_monto.control,
                comision=campo_comision.control,
                moneda=ft.Row([dropdown_moneda], spacing=0) if por_unidades else texto_moneda,
                objetivos=celda_objetivos,
                notas=campo_notas,
            )
            return FilaAlta(celdas=celdas, boton=boton_confirmar, foco=campo_fecha)

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
            if not entradas:
                _error(f"NO HAY ACTIVOS EN {dict(TABS)[tab]}: CREÁ UNO PRIMERO.")
                return
            entrada = entradas.get(alta_refs["activo"].id_seleccionado or "")
            if entrada is None:
                _error("ELEGÍ UN ACTIVO DE LA LISTA DE SUGERENCIAS.")
                return
            activo_id = entrada["activo_id"]
            # Acciones / CEDEARs: la moneda elegida en la fila; el resto, la del activo (la pone el service).
            moneda_id = int(alta_refs["moneda"].value) if por_unidades and alta_refs["moneda"].value else None
            decimales = monedas_por_id.get(moneda_id or entrada["moneda_id"], {}).get("decimales", entrada["decimales"])
            fecha = (alta_refs["fecha"].value or "").strip()
            movimiento = alta_refs["movimiento"].valor
            notas = (alta_refs["notas"].value or "").strip() or None
            alta = ui["altas"][tab]
            # None = PROPORCIONAL (solo un rendimiento): lo reparte el service según la fecha.
            asignaciones = (
                None if movimiento == "rendimiento" and alta.get("proporcional", True)
                else [{"objetivo_id": a["objetivo_id"], "porcentaje": a["porcentaje"]} for a in alta.get("objetivos", [])]
            )
            # Sin transacción del Registro (crear_transaccion=False): ver docstring, "Tabla de movimientos".
            try:
                comision = (
                    0 if movimiento == "rendimiento"
                    else _minor_de(alta_refs["comision"], decimales, "LA COMISIÓN", opcional=True)
                )
                monto = _minor_de(alta_refs["monto"], decimales, "EL MONTO")
                if movimiento in ("compra", "venta"):
                    cantidad = numero(alta_refs["cantidad"].value or "")
                    if cantidad is None or cantidad <= 0:
                        raise ValueError("LA CANTIDAD TIENE QUE SER UN NÚMERO MAYOR A 0.")
                    registrar = (
                        savings_service.registrar_compra if movimiento == "compra" else savings_service.registrar_venta
                    )
                    # monto = el bruto (cantidad × precio, sin la comisión): ver docstring, "Moneda".
                    resultado = registrar(
                        activo_id, cantidad, monto, comision, fecha, notas=notas, crear_transaccion=False,
                        asignaciones=asignaciones, moneda_id=moneda_id,
                    )
                else:
                    if movimiento == "rendimiento":
                        resultado = savings_service.registrar_rendimiento(
                            activo_id, monto, fecha, notas=notas, asignaciones=asignaciones, moneda_id=moneda_id,
                        )
                    else:
                        registrar = (
                            savings_service.registrar_aporte if movimiento == "aporte"
                            else savings_service.registrar_retiro
                        )
                        resultado = registrar(
                            activo_id, monto, fecha, comision_minor=comision, notas=notas, crear_transaccion=False,
                            asignaciones=asignaciones,
                        )
            except (SavingsError, ValueError) as err:
                _error(str(err))  # la fila queda como estaba para corregir
                return
            # La fila nueva conserva lo cargado (ver docstring): el borrador ya está guardado.
            tabla.alta_ok()
            _ok(resultado.message)

        # --- Filas de datos: celdas editables inline (CLAUDE.md §10) ---

        def _editar_objetivos(m: dict) -> None:
            """Celda OBJETIVOS de un movimiento ya cargado: su diálogo → update_asignaciones()."""
            es_rendimiento = m["tipo"] == "rendimiento"

            def _confirmar(asignaciones: Optional[list[dict]]) -> None:
                # SavingsError sube al diálogo, que lo muestra sin cerrarse.
                if asignaciones is None:  # USAR REPARTO PROPORCIONAL: sin contarse a sí mismo
                    asignaciones = savings_service.get_reparto_proporcional(
                        m["activo_id"], m["fecha"], excluir_movimiento_id=m["id"],
                    )
                resultado = savings_service.update_asignaciones(m["id"], asignaciones)
                _cerrar_dialogo()
                tabla.recargar()  # la firma incluye las asignaciones; al_recargar() rehace las tarjetas
                _ok(resultado.message)

            formulario = dialogo_compra_ahorro.construir_objetivos_movimiento(
                page, savings_service, m["asignaciones"], _confirmar, monto_minor=m["monto_total_minor"],
                decimales=m["decimales"], simbolo=m["currency_symbol"], con_proporcional=es_rendimiento,
            )
            _abrir_formulario(f"OBJETIVOS — {TEXTO_MOVIMIENTO[m['movimiento']]} DEL {m['fecha']}", formulario)

        def _construir_celdas(m: dict) -> dict[str, ft.Control]:
            editable = m["transaccion_id"] is None  # vinculado: solo NOTAS (ver docstring)

            def _guardar(**cambios: Any) -> str:
                # SavingsError (y ValueError) suben a la celda, que los muestra y vuelve al valor anterior.
                return savings_service.update_movement(m["id"], **cambios).message

            def _guardar_fecha(nuevo: str) -> str:
                try:
                    datetime.strptime(nuevo.strip(), "%Y-%m-%d")
                except ValueError:
                    raise ValueError("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.") from None
                return _guardar(fecha=nuevo.strip())

            def _guardar_cantidad(nuevo: str) -> str:
                valor = numero(nuevo)
                if valor is None or valor <= 0:
                    raise ValueError("LA CANTIDAD TIENE QUE SER UN NÚMERO MAYOR A 0.")
                return _guardar(cantidad=valor)

            def _lectura(clave: str, texto: str, color: str = TEXT_PRIMARY, tooltip: Optional[str] = None,
                         es_monto: bool = False, on_click: Optional[Callable[[], None]] = None) -> ft.Control:
                if es_monto:  # mismo estilo que el monto de TablaPlanilla.celda_monto()
                    contenido = texto_celda(
                        texto, color=color, tooltip=tooltip, size=TypographyTokens.REGISTRO_FONT_MONTO, weight=PESO_MONTO,
                    )
                else:
                    contenido = texto_celda(texto, color=color, tooltip=tooltip)
                return tabla.celda_lectura(clave, contenido, on_click=on_click)

            def _celda_monto_opcional(clave: str, columna: str, guardar: Callable[[int], str], aplica: bool) -> ft.Control:
                """COMISIÓN: "—" si no aplica a este movimiento; editable si no está vinculado."""
                if not aplica:
                    return _lectura(clave, TEXTO_SIN_VALOR, TEXT_MUTED)
                texto = _texto_opcional(m, columna)
                if not editable:
                    return _lectura(clave, texto, TEXT_SECONDARY, tooltip=TOOLTIP_VINCULADO)
                return tabla.celda_monto(m, clave, texto, TEXT_SECONDARY, m[columna] or 0, m["decimales"], guardar)

            # MONTO: el que cargó el usuario (en compra / venta, el bruto: ver docstring, "Moneda").
            if not editable:
                celda_monto = _lectura("monto", _texto_monto(m), _color_monto(m), tooltip=TOOLTIP_VINCULADO, es_monto=True)
            else:
                celda_monto = tabla.celda_monto(
                    m, "monto", _texto_monto(m), _color_monto(m), m["monto_minor"], m["decimales"],
                    lambda nuevo: _guardar(monto_minor=nuevo),
                )

            # MONEDA: editable solo en acciones / CEDEARs (en el resto es siempre la del activo).
            if por_unidades and editable:
                celda_moneda = tabla.celda_dropdown(
                    m, "moneda", m["currency_code"], opciones_moneda, str(m["moneda_id"]),
                    lambda nuevo: _guardar(moneda_id=int(nuevo)), color=TEXT_SECONDARY,
                )
            else:
                celda_moneda = _lectura(
                    "moneda", m["currency_code"], TEXT_SECONDARY,
                    tooltip=TOOLTIP_VINCULADO if por_unidades else TOOLTIP_MONEDA_ACTIVO,
                )

            celdas = {
                "fecha": (
                    tabla.celda_texto(m, "fecha", m["fecha"], _guardar_fecha) if editable
                    else _lectura("fecha", m["fecha"], tooltip=TOOLTIP_VINCULADO)
                ),
                "activo": _lectura("activo", _nombre_con_broker(m["activo_nombre"], m["broker"])),
                "movimiento": _lectura(
                    "movimiento", TEXTO_MOVIMIENTO[m["movimiento"]], COLOR_MOVIMIENTO.get(m["movimiento"], TEXT_PRIMARY),
                ),
                "monto": celda_monto,
                "comision": _celda_monto_opcional(
                    "comision", "comision_minor", lambda nuevo: _guardar(comision_minor=nuevo),
                    aplica=m["tipo"] != "rendimiento",
                ),
                "moneda": celda_moneda,
                # Los objetivos se editan siempre, también en un movimiento vinculado (ver docstring).
                "objetivos": _lectura(
                    "objetivos", _texto_asignaciones(m["asignaciones"]), TEXT_SECONDARY,
                    tooltip=f"{_texto_asignaciones(m['asignaciones'])} — {TOOLTIP_OBJETIVOS}",
                    on_click=lambda: _editar_objetivos(m),
                ),
                # Las notas se editan siempre, también en un movimiento vinculado.
                "notas": tabla.celda_texto(
                    m, "notas", m["notas"] or TEXTO_NOTAS_VACIAS, lambda nuevo: _guardar(notas=nuevo),
                    valor_inicial=m["notas"] or "", color=TEXT_PRIMARY if m["notas"] else TEXT_MUTED,
                ),
            }
            if por_unidades:
                if m["cantidad"] is None:
                    celdas["cantidad"] = _lectura("cantidad", TEXTO_SIN_VALOR, TEXT_MUTED)
                elif editable:
                    celdas["cantidad"] = tabla.celda_texto(
                        m, "cantidad", _fmt_unidades(m["cantidad"]), _guardar_cantidad, valor_inicial=f"{m['cantidad']:g}",
                    )
                else:
                    celdas["cantidad"] = _lectura("cantidad", _fmt_unidades(m["cantidad"]), tooltip=TOOLTIP_VINCULADO)
                # PRECIO: calculado por el service (monto / cantidad), nunca se edita.
                celdas["precio"] = (
                    _lectura("precio", _texto_opcional(m, "precio_unitario_minor"), TEXT_SECONDARY,
                             tooltip=TOOLTIP_PRECIO_CALCULADO)
                    if m["precio_unitario_minor"] is not None else _lectura("precio", TEXTO_SIN_VALOR, TEXT_MUTED)
                )
            return celdas

        def _accion_fila(m: dict) -> Optional[ft.Control]:
            if m["transaccion_id"] is None:
                return None
            # Solo indicador: el vínculo no se edita desde acá.
            return ft.Row([tabla.icono_accion(ft.Icons.LINK, TOOLTIP_VINCULADO, True, sin_auto_update)], spacing=0)

        # --- Barra flotante: eliminar ---

        def _avisos_eliminar(filas: list[dict]) -> str:
            vinculadas = sum(1 for m in filas if m["transaccion_id"] is not None)
            if not vinculadas:
                return ""
            return f"{vinculadas} TIENE(N) UNA TRANSACCIÓN DEL REGISTRO VINCULADA (ESA TRANSACCIÓN NO SE BORRA)"

        def _eliminar(filas: list[dict]) -> tuple[str, bool]:
            errores = []
            vinculadas = 0
            for m in filas:
                try:
                    savings_service.delete_movement(m["id"])  # solo el movimiento (ver docstring)
                except SavingsError as err:
                    errores.append(f"#{m['id']}: {err}")
                    continue
                vinculadas += m["transaccion_id"] is not None
            mensaje = f"{len(filas) - len(errores)} MOVIMIENTO(S) ELIMINADO(S)."
            if vinculadas:
                mensaje += f" {vinculadas} TENÍA(N) UNA TRANSACCIÓN VINCULADA: QUEDA(N) EN EL REGISTRO."
            if errores:
                return f"{mensaje} {len(errores)} CON ERROR: " + " | ".join(errores), True
            return mensaje, False

        # --- Armado ---

        tabla = TablaPlanilla(
            page,
            clave=f"ahorros_{tab}",
            columnas=_columnas(por_unidades),
            pref_anchos=f"ahorros_{tab}_anchos",
            cargar_filas=_cargar_filas,
            construir_celdas=_construir_celdas,
            construir_alta=_construir_alta,
            firma=_firma,
            valor_columna=_valor_columna,
            clave_orden=_clave_orden,
            texto_busqueda=lambda m: (
                f"{_nombre_con_broker(m['activo_nombre'], m['broker'])} {m['notas'] or ''} "
                f"{_texto_asignaciones(m['asignaciones'])}"
            ),
            on_eliminar=_eliminar,
            avisos_eliminar=_avisos_eliminar,
            accion_fila=_accion_fila,
            al_recargar=_al_recargar,
            errores_esperados=(SavingsError,),
            texto_vacio=f"NO HAY MOVIMIENTOS EN {dict(TABS)[tab]} — CARGALOS EN LA FILA DE ARRIBA.",
            columna_suma="monto_total_minor",
            signo_suma=_signo,  # el monto se guarda en positivo: venta / retiro restan
        )
        return tabla.construir()

    def _alternar_ocultos(tab: str) -> None:
        ocultos = ui.setdefault("ocultos", {})
        ocultos[tab] = not ocultos.get(tab, False)
        _redibujar()

    def _contenido_tipo(tab: str) -> tuple[list[ft.Control], ft.Control]:
        """Botón "+ NUEVO …", MOSTRAR OCULTOS, tarjetas y tabla de la pestaña. Devuelve (controles, control de la tabla)."""
        controles: list[ft.Control] = []
        barra: list[ft.Control] = []
        if tab in BOTON_NUEVO:
            barra.append(_boton(BOTON_NUEVO[tab], lambda: _nuevo_activo(TIPOS_POR_TAB[tab][0])))
        ocultos = sum(1 for e in _entradas_de(tab, con_ocultos=True) if not e["activa"])
        if ocultos or _mostrar_ocultos(tab):
            barra.append(_pill(
                f"{TEXTO_MOSTRAR_OCULTOS} ({ocultos})", _mostrar_ocultos(tab), lambda: _alternar_ocultos(tab),
                tooltip=TOOLTIP_MOSTRAR_OCULTOS,
            ))
        if barra:
            controles.append(ft.Row(barra, spacing=ESPACIADO, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        contenedor_tarjetas = ft.Container(content=_tarjetas(tab))
        control_tabla = _tabla_movimientos(tab, contenedor_tarjetas)
        controles += [contenedor_tarjetas, _titulo_seccion("MOVIMIENTOS"), control_tabla]
        return controles, control_tabla

    # ------------------------------------------------------------
    # ARMADO
    # ------------------------------------------------------------

    def _cambiar_tab(tab: str) -> None:
        if tab == ui["tab"]:
            sin_auto_update()
            return
        anterior = vista["control_tabla"]
        if anterior is not None and isinstance(anterior.data, dict):
            anterior.data["al_ocultar"]()  # su barra flotante y su teclado dejan de actuar (igual que Deudas)
        ui["tab"] = tab
        _redibujar()

    def _cambiar_vista(vista_resumen: str) -> None:
        if vista_resumen == ui["vista"]:
            sin_auto_update()
            return
        ui["vista"] = vista_resumen
        _redibujar()

    def _barra_tabs() -> ft.Control:
        """Tabs con subrayado en la activa (ver docstring). Row con scroll: cada tab mide su texto."""
        tabs: list[ft.Control] = []
        for clave, texto in TABS:
            activa = clave == ui["tab"]
            tabs.append(ft.Container(
                padding=ft.Padding.symmetric(horizontal=PADDING_TAB_H, vertical=PADDING_TAB_V),
                border=ft.Border.only(bottom=ft.BorderSide(
                    width=ALTO_INDICADOR_TAB, color=TEXT_ACCENT if activa else ft.Colors.TRANSPARENT,
                )),
                on_click=lambda e, c=clave: _cambiar_tab(c),
                content=ft.Text(
                    texto, size=TAMANIO_TAB, weight=PESO_HEADER, color=TEXT_PRIMARY if activa else TEXT_SECONDARY,
                ),
            ))
        return ft.Container(
            border=ft.Border.only(bottom=ft.BorderSide(width=ANCHO_BORDE, color=BORDER_DEFAULT)),
            content=ft.Row(tabs, spacing=0, scroll=ft.ScrollMode.AUTO),
        )

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
            _boton("+ NUEVO OBJETIVO", _formulario_objetivo),
        ]
        return ft.Row(controles, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _redibujar() -> None:
        _refrescar_resumen()
        if ui["tab"] == "resumen":
            contenido, control_tabla = _contenido_resumen(), None
        else:
            contenido, control_tabla = _contenido_tipo(ui["tab"])
        vista["control_tabla"] = control_tabla
        raiz.controls = [pantalla_planilla([_cabecera(), _barra_tabs(), *contenido])]
        # Update completo, como al navegar (ui/app.py): la primera vez que se
        # arma la tabla de una pestaña, su barra flotante y su host de popups
        # recién entran a page.overlay.
        page.update()

    _redibujar()
    return raiz
