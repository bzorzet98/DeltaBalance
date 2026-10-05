"""
DeltaBalance — ui/components/dialogo_compra_ahorro.py

Formulario completo de "Compra" de un activo financiero, extraído de
ui/screens/ahorros.py (donde vivía inline, atado a un activo YA elegido por
el usuario al tocar el ícono de esa fila) para poder reusarlo también desde
el popup "Ahorro/Inversión" de ui/components/registro_transacciones.py, que
hasta esta tarea tenía su PROPIO formulario simplificado y paralelo para
básicamente la misma acción (SavingsService.register_purchase()) — dos
formularios distintos para la misma operación, justo lo que esta tarea
pide unificar.

Este módulo NO abre ningún AlertDialog por sí solo — a diferencia de
ui/components/compartir_gasto.py/compartir_compra.py (que sí llaman
page.show_dialog() ellos mismos), acá construir() devuelve un
FormularioCompraAhorro(contenido, confirmar): `contenido` es el ft.Control
para poner en AlertDialog.content, `confirmar` es el callable para el
on_click del botón "Confirmar" de las actions del diálogo. La razón es el
caso de uso del Registro (ver su docstring, sección "Elegir activo
específico"): ahí el formulario completo REEMPLAZA EN VIVO el contenido de
un AlertDialog que ya está abierto en modo simple — necesita el control
crudo (contenido + función de confirmar), no un diálogo ya armado y
mostrado. ui/screens/ahorros.py, que sí quiere abrirlo directo como su
propio diálogo, arma el AlertDialog alrededor de estos dos valores en su
propio _abrir_dialogo_compra() (unas pocas líneas, ver ese archivo).

--- activo_fijo vs. selector de activo ---

Si `activo_fijo` viene con datos (caso ui/screens/ahorros.py: el usuario ya
tocó el ícono de Compra de una fila puntual), no hay selector — directo a
los campos de monto, con decimales/tipo resueltos una sola vez desde ese
activo, igual que la versión original.

Si `activo_fijo` es None (caso Registro, modo "Elegir activo específico"),
se agrega un CampoFiltrable de activos existentes + una opción sentinel
"+ Crear nuevo activo" (_ID_ACTIVO_NUEVO) que revela nombre/tipo/moneda —
MISMO patrón ya usado en este mismo diálogo del Registro para "+ Crear
nuevo objetivo" (_ID_OBJETIVO_NUEVO en registro_transacciones.py), reusado
acá por consistencia en vez de inventar un mecanismo distinto. Cada opción
de activo existente en ese CampoFiltrable muestra su cuenta asociada en el
label ("NVDA — Bull Market"; solo "NVDA" si el activo no tiene cuenta_id)
para desambiguar activos con el mismo nombre en cuentas distintas — ver
_label_activo() (Tarea 6g, docs/PROXIMOS_PASOS.md).

--- Cuenta/categoría — Tarea 6g ---

Ya NO son campos de este formulario a nivel movimiento: SavingsService.
register_purchase() los resuelve solos (cuenta_id desde
activo["cuenta_id"], categoria_id siempre la categoría protegida
'Ahorro/Inversión' — ver services/savings_service.py). El único lugar
donde una cuenta se elige acá es al CREAR un activo nuevo (sección
"+ Crear nuevo activo"): un CampoFiltrable de cuentas, opcional, que se
manda como create_activo(cuenta_id=...) — la cuenta queda vinculada al
activo, no al movimiento puntual. `cuenta_id_inicial` (ver más abajo) pasó
a precargar ESE selector en vez del removido "Cuenta de origen" a nivel
movimiento.

PUNTO DELICADO (pregunta (c) de la tarea): decimales/tipo del activo
determinan tanto la conversión a minor units de Monto/Precio unitario COMO
si Cantidad/Precio unitario se muestran (ver TIPOS_ACTIVO_CON_CANTIDAD) —
en la versión original de ahorros.py esto se resolvía UNA sola vez porque
`activo` era fijo desde el arranque. Acá, con selector, el activo resuelto
puede CAMBIAR en vivo (el usuario elige uno, después otro, o cambia la
moneda del "nuevo activo") — y CampoMonto fija sus `decimales` en el
constructor, no se pueden reasignar después. Solución: _reconstruir_
campos_monto() reconstruye Monto/Cantidad/Precio unitario (nuevas
instancias de CampoMonto, con los decimales/tipo del momento) cada vez que
cambia la selección de activo (on_seleccionar del CampoFiltrable de activo,
on_select del Dropdown de tipo nuevo, on_seleccionar del CampoFiltrable de
moneda nueva) — mismo patrón de "swap .content + page.update()" que ya usa
el resto de la app (ej. _celda_texto._editar() en registro_transacciones.py)
aplicado acá a un ft.Column contenedor en vez de a un ft.Container de
celda. El texto ya tipeado en Monto/Precio unitario se preserva entre
reconstrucciones (se relee con .texto antes de reconstruir y se
reconvierte a minor units con los decimales NUEVOS). Dólar oficial NO
necesita este mecanismo: siempre está en ARS sin importar la moneda del
activo (ver docstring de ui/screens/ahorros.py, mismo criterio), así que
se construye una sola vez.

--- Resto del formulario ---

Asignaciones a objetivos: construir_editor_asignaciones() (más abajo en
este módulo) — extraído a su propia función para que ui/screens/ahorros.py
pueda reusarlo LITERALMENTE (no una copia adaptada) en el diálogo "Egreso
general" (register_sale() con lista de asignaciones, Tarea 6d) — ver su
docstring para el detalle completo (mecanismo de filas dinámicas,
validaciones, contrato de resolver()). Errores de _confirmar() (incluida
AsignacionInvalidaError) se escriben en texto_error, un ft.Text que ya
forma parte de `contenido` — nunca SnackBar, el caller decide cómo mostrar
éxito (on_exito) pero NUNCA ve los errores de validación, ese texto ya está
en pantalla dentro del formulario.

`notas_inicial`: se pasa tal cual a register_purchase(notas=...) sin campo
propio en el formulario — el Registro lo usa para seguir mandando el
concepto tipeado en la fila (comportamiento ya existente, ver docstring de
registro_transacciones.py), ui/screens/ahorros.py simplemente no lo pasa
(None, no tiene un campo de concepto en su fila de activos).

Reglas de arquitectura: solo SavingsService/AccountsService — nunca
repositories/ ni db/ directo (CLAUDE.md §2/§3); CategoriasService dejó de
hacer falta acá (Tarea 6g: la categoría del movimiento ya no se elige en
este formulario). CampoMonto en todos los campos de monto (CLAUDE.md §9);
`cantidad`/`porcentaje` quedan TextField comunes (no son plata), mismo
criterio que ui/screens/ahorros.py.

--- Rediseño de Ahorros e Inversiones ---

construir() (el formulario de Compra del Registro) sigue igual: solo suma
los tipos nuevos ('cedear', 'plazo_flex') y un Broker opcional al crear un
activo nuevo. Tres formularios nuevos, mismo contrato
FormularioCompraAhorro (contenido + confirmar), que usa la pantalla de
Ahorros:
- construir_nuevo_activo(): nombre, tipo (FCI / ACCIÓN / CEDEAR / PLAZO
  FIJO / PLAZO FLEX), broker, moneda, cuenta asociada (opcional, Tarea
  6g) y comisiones por defecto. on_exito recibe el resultado de
  create_activo(). Sin objetivos: van en cada movimiento (ver abajo).
- construir_movimiento(): según el tipo de activo, COMPRA / VENTA /
  RENDIMIENTO (acciones, CEDEARs: moneda del movimiento + cantidad entera
  + monto bruto + comisión, y el dólar del día en la compra — sección 33)
  o APORTE / RETIRO / RENDIMIENTO (FCI, plazos: monto, en la moneda del
  activo). Fecha y, salvo en un rendimiento, una
  transacción del Registro del mismo mes para vincular (opcional; si no se
  elige ninguna y el activo tiene cuenta, el service crea la suya). Los
  campos se rearman al cambiar el tipo; la lista de transacciones, al
  cambiar el mes de la fecha. "RETIRO" es una venta por monto
  (registrar_retiro()). Abajo, OBJETIVOS del movimiento (ver "Objetivos
  por movimiento").
- construir_objetivos_movimiento(): el diálogo de la celda OBJETIVOS de la
  tabla de movimientos (fila de alta y filas ya cargadas).
- construir_editar_activo(): editar un instrumento — los campos de
  construir_nuevo_activo() precargados; el tipo no se edita y la moneda
  solo sin movimientos (docs/DATA_MODEL_DECISIONS.md sección 35).
- construir_eliminar_objetivo(): eliminar un objetivo eligiendo a quién
  pasa lo que tenía en cada instrumento (docs/DATA_MODEL_DECISIONS.md
  sección 32).

--- Objetivos por movimiento (docs/DATA_MODEL_DECISIONS.md sección 31) ---

Reemplaza al reparto fijo por activo (el antiguo construir_objetivos_activo()
y la sección OBJETIVOS de construir_nuevo_activo(), que lo cargaban con
assign_objetivo()): cada movimiento dice a qué objetivos va y en qué
porcentaje, con construir_editor_asignaciones() — que ganó crear un objetivo
ahí mismo, el monto de cada fila y la línea ASIGNADO · SIN ASIGNAR. Un
rendimiento arranca en PROPORCIONAL (TEXTO_REPARTO_PROPORCIONAL: lo
calcula el service al guardar, SavingsService.get_reparto_proporcional());
destildarlo, o abrir el diálogo, precarga ese reparto para cambiarlo.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.savings_service import TIPOS_ACTIVO, SavingsError, SavingsResult, SavingsService
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.components.tipo_valor import numero
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---
ANCHO_DIALOGO_COMPRA_AHORRO = 380
# construir_objetivos_movimiento(): sus filas suman la columna de monto.
ANCHO_DIALOGO_OBJETIVOS = 480
ANCHO_CAMPO_ASIGNACION_OBJETIVO = 200
ANCHO_CAMPO_ASIGNACION_PORCENTAJE = 90
ANCHO_CAMPO_ASIGNACION_MONTO = 110
ESPACIADO_DIALOGO = 12
ESPACIADO_ASIGNACIONES = 6
TAMANIO_ICONO_ASIGNACION = 16
TEXTO_CREAR_OBJETIVO = "+ CREAR NUEVO OBJETIVO"
TEXTO_REPARTO_PROPORCIONAL = "PROPORCIONAL: SEGÚN LO QUE CADA OBJETIVO TENÍA EN EL ACTIVO ANTES DE ESA FECHA"

# Criterio de qué tipos tienen "unidad" real (tiene sentido pedir cantidad/
# precio_unitario al comprar) — ver docstring del módulo.
TIPOS_ACTIVO_CON_CANTIDAD = {"accion", "cedear", "fci", "cripto"}
# Los que se crean desde la pantalla de Ahorros (construir_nuevo_activo()).
TIPOS_NUEVO_ACTIVO = ("fci", "accion", "cedear", "plazo_fijo", "plazo_flex")
# Movimientos de construir_movimiento(), según el activo se cuente en unidades o en plata.
TIPOS_MOVIMIENTO_POR_UNIDADES = (("compra", "COMPRA"), ("venta", "VENTA"), ("rendimiento", "RENDIMIENTO"))
TIPOS_MOVIMIENTO_POR_MONTO = (("aporte", "APORTE"), ("retiro", "RETIRO"), ("rendimiento", "RENDIMIENTO"))
# Suma de porcentajes que se considera <= 100 (33.33 + 33.33 + 33.34).
TOLERANCIA_PORCENTAJE = 1e-6

MONEDA_DOLAR_OFICIAL_CODIGO = "ARS"
DECIMALES_DEFAULT = 2

# Sentinel de "+ Crear nuevo activo" en el CampoFiltrable de activo — mismo
# criterio que _ID_OBJETIVO_NUEVO en registro_transacciones.py (nunca
# colisiona con un id real, INTEGER PRIMARY KEY siempre numérico como str).
_ID_ACTIVO_NUEVO = "__nuevo__"
# Ídem, "+ CREAR NUEVO OBJETIVO" en las filas de construir_editor_asignaciones().
_ID_OBJETIVO_NUEVO = "__nuevo_objetivo__"


def _tipo_activo_display(tipo: str) -> str:
    return {
        "accion": "ACCIÓN",
        "fci": "FCI",
        "cedear": "CEDEAR",
        "plazo_fijo": "PLAZO FIJO",
        "plazo_flex": "PLAZO FLEX",
        "cripto": "CRIPTO",
        "otro": "OTRO",
    }.get(tipo, tipo.upper())


def _cuentas_no_credito(cuentas: list[dict]) -> list[dict]:
    """Mismo filtro que ui/components/registro_transacciones.py — un ahorro no se origina desde una tarjeta de crédito."""
    return [c for c in cuentas if c["tipo"] != "credito"]


def _label_activo(activo: dict, cuentas_por_id: dict) -> str:
    """
    Label de una opción de activo existente en un CampoFiltrable: "nombre —
    cuenta" si el activo tiene cuenta_id vinculada, solo "nombre" si no
    (Tarea 6g, docs/PROXIMOS_PASOS.md) — desambigua activos con el mismo
    nombre en cuentas distintas (ej. "NVDA — Bull Market").
    """
    cuenta = cuentas_por_id.get(activo["cuenta_id"]) if activo["cuenta_id"] is not None else None
    return f"{activo['nombre']} — {cuenta['nombre']}" if cuenta else activo["nombre"]


def _texto_a_minor(texto: Optional[str], decimales: int) -> Optional[int]:
    """Reconvierte un texto ya confirmado (ej. de un CampoMonto que se va a reconstruir) a minor units con decimales nuevos."""
    if not texto:
        return None
    try:
        return amount_to_minor(float(texto.replace(",", ".")), decimales)
    except ValueError:
        return None


@dataclass
class FormularioCompraAhorro:
    """contenido: para AlertDialog.content. confirmar: para el on_click del botón Confirmar del caller — ver docstring del módulo."""
    contenido: ft.Control
    confirmar: Callable[[], None]


@dataclass
class EditorAsignaciones:
    """contenido: Column con las filas + "+ AGREGAR OBJETIVO", para insertar en el Column del diálogo del caller. resolver(): ver construir_editor_asignaciones()."""
    contenido: ft.Control
    resolver: Callable[[], tuple[Optional[list[dict]], Optional[str]]]


def _creador_objetivo(savings_service: SavingsService) -> Callable[[str], str]:
    """crear_objetivo de construir_editor_asignaciones(): el objetivo nuevo, sin meta ni fecha meta."""
    return lambda nombre: savings_service.create_objetivo(nombre=nombre).entity_id


def _fmt_porcentaje(valor: float) -> str:
    """33.33333 → '33.3333'; 100.0 → '100' (mismos decimales que SavingsService.DECIMALES_PORCENTAJE)."""
    return f"{round(valor, 4):g}"


def construir_editor_asignaciones(
    page: ft.Page,
    objetivos_disponibles: list,
    iniciales: Optional[list[dict]] = None,
    crear_objetivo: Optional[Callable[[str], str]] = None,
    monto_minor: Optional[int] = None,
    decimales: int = DECIMALES_DEFAULT,
    simbolo: str = "",
    con_resumen: bool = False,
    una_fila_vacia: bool = False,
) -> EditorAsignaciones:
    """
    Lista dinámica de filas (Objetivo + %) — extraída para que
    ui/screens/ahorros.py la reuse LITERALMENTE (no una copia adaptada)
    en vez de duplicar este mecanismo. Hoy la usan el formulario de Compra
    del Registro (construir()), el de movimiento de la pantalla de Ahorros
    (construir_movimiento()) y el diálogo de objetivos de un movimiento
    (construir_objetivos_movimiento()).

    iniciales: filas que arrancan cargadas, [{"objetivo_id", "porcentaje"}].
    Sin iniciales y con una_fila_vacia, arranca con una fila para elegir.

    "+ AGREGAR OBJETIVO" agrega una fila, con el porcentaje que falta para
    llegar a 100 ya cargado (la primera, 100). Cada fila tiene su ✕ para
    sacarla; puede quedar en cero filas.

    Opcionales del diálogo de objetivos de un movimiento (objetivos por
    movimiento, docs/DATA_MODEL_DECISIONS.md sección 31):
    - crear_objetivo(nombre) → id: suma "+ CREAR NUEVO OBJETIVO" a cada
      selector; elegirlo muestra el nombre + ✓, que lo crea EN EL MOMENTO
      (aunque después se cancele el diálogo: un objetivo suelto no rompe
      nada) y deja la fila con el objetivo nuevo elegido. Crearlo al
      confirmar daría duplicados si el movimiento falla y se reintenta.
    - monto_minor (+ decimales, simbolo): el monto que le toca a cada fila
      según su porcentaje, en vivo.
    - con_resumen: una línea "ASIGNADO · SIN ASIGNAR" debajo, en vivo.

    resolver() valida y arma la lista final (llamarlo al confirmar el
    diálogo del caller): [{"objetivo_id", "porcentaje", "nombre"}]. Una
    fila totalmente vacía (sin objetivo Y sin porcentaje) se ignora en
    silencio; una fila PARCIAL (una de las dos cosas cargada, la otra no)
    es un error explícito, igual que un mismo objetivo repetido en más de
    una fila — esto último no lo valida ningún service (dejaría un
    IntegrityError crudo de SQLite por el UNIQUE(movimiento_id,
    objetivo_id), nunca visto por el usuario como mensaje claro). Devuelve
    (asignaciones, None) si todo validó, o (None, mensaje_error) si no — el
    caller decide dónde mostrar ese mensaje (típicamente su propio
    texto_error). NO valida que la suma no supere 100 — esa regla de
    negocio queda del lado del service (AsignacionInvalidaError); el caller
    puede adelantarla con _suma_supera_100().
    """
    opciones = [{"id": str(o["id"]), "nombre": o["nombre"]} for o in objetivos_disponibles]
    filas: list[dict] = []
    columna_filas = ft.Column(spacing=ESPACIADO_ASIGNACIONES)
    texto_resumen = ft.Text("", size=TypographyTokens.LABEL_SIZE, visible=con_resumen)
    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE, visible=False)

    def _opciones_selector() -> list[tuple[str, str]]:
        base = [(o["id"], o["nombre"].upper()) for o in opciones]
        return base + [(_ID_OBJETIVO_NUEVO, TEXTO_CREAR_OBJETIVO)] if crear_objetivo is not None else base

    def _porcentaje(fila: dict) -> Optional[float]:
        texto = (fila["porcentaje"].value or "").strip()
        return numero(texto) if texto else None

    def _asignado() -> float:
        return sum(p for p in (_porcentaje(f) for f in filas) if p is not None and p > 0)

    def _actualizar_calculados() -> None:
        """Monto de cada fila y línea de resumen (sin page.update(): lo hace el caller)."""
        for fila in filas:
            if fila["monto"] is None:
                continue
            porcentaje = _porcentaje(fila)
            fila["monto"].value = (
                amount_display(round(monto_minor * porcentaje / 100), decimales, simbolo)
                if porcentaje is not None and porcentaje > 0 else ""
            )
        asignado = _asignado()
        if asignado > 100 + TOLERANCIA_PORCENTAJE:
            texto_resumen.value = f"LOS OBJETIVOS SUMAN {_fmt_porcentaje(asignado)}%: MÁS DE 100%."
            texto_resumen.color = ft.Colors.ERROR
        else:
            texto_resumen.value = (
                f"ASIGNADO: {_fmt_porcentaje(asignado)}% · SIN ASIGNAR: {_fmt_porcentaje(max(100 - asignado, 0))}%"
            )
            texto_resumen.color = None

    def _al_cambiar_porcentaje(e=None) -> None:
        _actualizar_calculados()
        page.update()

    def _mostrar_error(mensaje: str) -> None:
        texto_error.value = mensaje
        texto_error.visible = bool(mensaje)

    def _refrescar() -> None:
        columna_filas.controls = [f["contenedor"] for f in filas]
        _actualizar_calculados()
        page.update()

    def _al_elegir(fila: dict, id_: Optional[str]) -> None:
        fila["nuevo"].visible = id_ == _ID_OBJETIVO_NUEVO
        _mostrar_error("")
        page.update()

    def _crear(fila: dict) -> None:
        nombre = (fila["nombre_nuevo"].value or "").strip().upper()
        if not nombre:
            _mostrar_error("EL NOMBRE DEL OBJETIVO NUEVO NO PUEDE ESTAR VACÍO.")
            page.update()
            return
        try:
            objetivo_id = str(crear_objetivo(nombre))
        except (SavingsError, ValueError) as err:
            _mostrar_error(str(err).upper())
            page.update()
            return
        opciones.append({"id": objetivo_id, "nombre": nombre})
        _armar_fila(fila, objetivo_id)
        _mostrar_error("")
        _refrescar()

    def _quitar(fila: dict) -> None:
        filas.remove(fila)
        _refrescar()

    def _armar_fila(fila: dict, objetivo_id: Optional[str]) -> None:
        """(Re)arma los controles de una fila: al crearla y tras crear un objetivo nuevo desde ella."""
        texto_porcentaje = fila["porcentaje"].value if "porcentaje" in fila else fila.pop("porcentaje_inicial")
        fila["objetivo"] = CampoFiltrable(
            page, _opciones_selector(), on_seleccionar=lambda id_: _al_elegir(fila, id_),
            placeholder="OBJETIVO", width=ANCHO_CAMPO_ASIGNACION_OBJETIVO, valor_inicial_id=objetivo_id,
        )
        fila["porcentaje"] = ft.TextField(
            hint_text="%", width=ANCHO_CAMPO_ASIGNACION_PORCENTAJE, dense=True, value=texto_porcentaje,
            on_change=_al_cambiar_porcentaje,
        )
        fila["monto"] = (
            ft.Text("", width=ANCHO_CAMPO_ASIGNACION_MONTO, size=TypographyTokens.LABEL_SIZE, text_align=ft.TextAlign.RIGHT)
            if monto_minor is not None else None
        )
        fila["nombre_nuevo"] = ft.TextField(
            hint_text="NOMBRE DEL OBJETIVO NUEVO", width=ANCHO_CAMPO_ASIGNACION_OBJETIVO, dense=True,
            on_submit=lambda e: _crear(fila),
        )
        fila["nuevo"] = ft.Row(
            [
                fila["nombre_nuevo"],
                ft.IconButton(
                    icon=ft.Icons.CHECK, icon_size=TAMANIO_ICONO_ASIGNACION, tooltip="CREAR OBJETIVO",
                    on_click=lambda e: _crear(fila),
                ),
            ],
            spacing=ESPACIADO_ASIGNACIONES,
            visible=False,
        )
        controles: list[ft.Control] = [fila["objetivo"].control, fila["porcentaje"]]
        if fila["monto"] is not None:
            controles.append(fila["monto"])
        controles.append(ft.IconButton(
            icon=ft.Icons.CLOSE, icon_size=TAMANIO_ICONO_ASIGNACION, tooltip="QUITAR", on_click=lambda e: _quitar(fila),
        ))
        fila["contenedor"].content = ft.Column(
            [ft.Row(controles, spacing=ESPACIADO_ASIGNACIONES, vertical_alignment=ft.CrossAxisAlignment.CENTER), fila["nuevo"]],
            spacing=ESPACIADO_ASIGNACIONES,
        )

    def _agregar_fila(objetivo_id: Optional[str] = None, porcentaje: Optional[float] = None) -> None:
        if porcentaje is None:
            porcentaje = max(100 - _asignado(), 0) or None  # lo que falta para 100
        fila: dict = {
            "contenedor": ft.Container(),
            "porcentaje_inicial": _fmt_porcentaje(porcentaje) if porcentaje is not None else None,
        }
        _armar_fila(fila, str(objetivo_id) if objetivo_id is not None else None)
        filas.append(fila)

    def _al_agregar(e=None) -> None:
        _agregar_fila()
        _refrescar()

    boton_agregar = ft.TextButton(content=ft.Text("+ AGREGAR OBJETIVO"), on_click=_al_agregar)

    def _resolver() -> tuple[Optional[list[dict]], Optional[str]]:
        nombres = {o["id"]: o["nombre"] for o in opciones}
        asignaciones: list[dict] = []
        ids_vistos: set[str] = set()
        for fila in filas:
            objetivo_id = fila["objetivo"].id_seleccionado
            porcentaje_texto = (fila["porcentaje"].value or "").strip()
            if objetivo_id == _ID_OBJETIVO_NUEVO:
                return None, "CREÁ EL OBJETIVO NUEVO CON ✓ (O ELEGÍ UNO DE LA LISTA)."
            if not objetivo_id and not porcentaje_texto:
                continue  # fila vacía — se ignora, ver docstring
            if not objetivo_id:
                return None, "HAY UNA FILA SIN OBJETIVO ELEGIDO (ELEGILO O QUITALA CON ✕)."
            porcentaje = numero(porcentaje_texto)
            if porcentaje is None:
                return None, "EL PORCENTAJE DE UN OBJETIVO NO ES UN NÚMERO VÁLIDO."
            if porcentaje <= 0:
                return None, "EL PORCENTAJE DE UN OBJETIVO DEBE SER MAYOR A 0."
            if objetivo_id in ids_vistos:
                return None, "NO REPITAS EL MISMO OBJETIVO EN MÁS DE UNA FILA."
            ids_vistos.add(objetivo_id)
            asignaciones.append({"objetivo_id": objetivo_id, "porcentaje": porcentaje, "nombre": nombres.get(objetivo_id, "")})
        return asignaciones, None

    for inicial in iniciales or []:
        _agregar_fila(objetivo_id=inicial["objetivo_id"], porcentaje=inicial["porcentaje"])
    if not filas and una_fila_vacia:
        _agregar_fila()
    columna_filas.controls = [f["contenedor"] for f in filas]
    _actualizar_calculados()

    contenido = ft.Column([columna_filas, boton_agregar, texto_resumen, texto_error], spacing=ESPACIADO_ASIGNACIONES)
    return EditorAsignaciones(contenido=contenido, resolver=_resolver)


def construir(
    page: ft.Page,
    savings_service: SavingsService,
    accounts_service: AccountsService,
    on_exito: Callable[[SavingsResult], None],
    activo_fijo: Optional[dict] = None,
    cuenta_id_inicial: Optional[int] = None,
    monto_inicial: Optional[float] = None,
    fecha_inicial: Optional[str] = None,
    notas_inicial: Optional[str] = None,
) -> FormularioCompraAhorro:
    """
    Args:
        on_exito:          Llamado con el SavingsResult de
                            register_purchase() tras una confirmación
                            exitosa — el caller decide qué hacer (cerrar
                            diálogo, SnackBar, refrescar). Nunca se llama
                            si hay un error de validación: eso queda
                            escrito en texto_error, dentro de `contenido`.
        activo_fijo:        Row de activos_financieros ya elegido — sin
                            selector, ver docstring del módulo. None =
                            selector de activo + "crear nuevo".
        cuenta_id_inicial:  Precarga la Cuenta asociada del selector de
                            "+ Crear nuevo activo" (Tarea 6g — ya no hay
                            campo de cuenta a nivel movimiento, ver
                            docstring del módulo). Sin efecto si el
                            usuario termina eligiendo un activo existente.
        monto_inicial:      Precarga el Monto total (valor, no texto).
        fecha_inicial:      Precarga la Fecha (default: hoy si no se pasa).
        notas_inicial:      Va directo a register_purchase(notas=...), sin
                            campo propio en el formulario — ver docstring.
    """
    monedas = accounts_service.list_currencies()
    monedas_por_id = {m["id"]: m for m in monedas}
    moneda_ars = next((m for m in monedas if m["codigo"] == MONEDA_DOLAR_OFICIAL_CODIGO), None)
    decimales_dolar_oficial = moneda_ars["decimales"] if moneda_ars else DECIMALES_DEFAULT

    cuentas_activas = _cuentas_no_credito(accounts_service.list_accounts(solo_activas=True))
    cuentas_por_id = {c["id"]: c for c in accounts_service.list_accounts(solo_activas=False)}
    objetivos_disponibles = savings_service.list_objetivos()
    activos_existentes = savings_service.list_activos()

    campo_fecha = ft.TextField(label="Fecha", value=fecha_inicial or date.today().isoformat())

    # ------------------------------------------------------------
    # SELECCIÓN DE ACTIVO — solo si NO viene fijo (ver docstring). La
    # cuenta asociada se elige acá, en "+ Crear nuevo activo" — no hay
    # campo de cuenta a nivel movimiento (Tarea 6g).
    # ------------------------------------------------------------
    campo_activo: Optional[CampoFiltrable] = None
    campo_nombre_activo_nuevo: Optional[ft.TextField] = None
    dropdown_tipo_activo_nuevo: Optional[ft.Dropdown] = None
    campo_moneda_activo_nuevo: Optional[CampoFiltrable] = None
    campo_cuenta_activo_nuevo: Optional[CampoFiltrable] = None
    campo_broker_activo_nuevo: Optional[CampoFiltrable] = None
    seccion_activo_nuevo: Optional[ft.Column] = None

    if activo_fijo is None:
        opciones_activo = [(str(a["id"]), _label_activo(a, cuentas_por_id)) for a in activos_existentes] + [
            (_ID_ACTIVO_NUEVO, "+ Crear nuevo activo")
        ]
        campo_nombre_activo_nuevo = ft.TextField(label="Nombre del activo nuevo")
        dropdown_tipo_activo_nuevo = ft.Dropdown(
            label="Tipo",
            options=[ft.dropdown.Option(key=t, text=_tipo_activo_display(t)) for t in TIPOS_ACTIVO],
            value=TIPOS_ACTIVO[0],
            on_select=lambda e: _on_cambio_activo(),
        )
        campo_moneda_activo_nuevo = CampoFiltrable(
            page, [(str(m["id"]), m["codigo"]) for m in monedas],
            on_seleccionar=lambda id_: _on_cambio_activo(), placeholder="Moneda del activo nuevo", dense=False,
        )
        # Cuenta asociada al activo nuevo (Tarea 6g) — opcional, mismo
        # criterio que AccountsService.create_activo(cuenta_id=None):
        # queda sin vincular si no se elige ninguna, y los movimientos de
        # ese activo no generan transacción real hasta que se le agregue
        # una cuenta.
        campo_cuenta_activo_nuevo = CampoFiltrable(
            page, [(str(c["id"]), c["nombre"]) for c in cuentas_activas],
            on_seleccionar=lambda id_: None, placeholder="Cuenta asociada (opcional)",
            valor_inicial_id=str(cuenta_id_inicial) if cuenta_id_inicial is not None else None,
            dense=False,
        )
        campo_broker_activo_nuevo = CampoFiltrable(
            page, [(b["id"], b["nombre"]) for b in savings_service.get_brokers()],
            on_seleccionar=lambda id_: None, placeholder="BROKER (OPCIONAL)", dense=False,
        )
        seccion_activo_nuevo = ft.Column(
            [
                campo_nombre_activo_nuevo, dropdown_tipo_activo_nuevo,
                campo_moneda_activo_nuevo.control, campo_broker_activo_nuevo.control,
                campo_cuenta_activo_nuevo.control,
            ],
            visible=False, spacing=ESPACIADO_DIALOGO,
        )

        def _on_seleccionar_activo(id_: Optional[str]) -> None:
            seccion_activo_nuevo.visible = (id_ == _ID_ACTIVO_NUEVO)
            _on_cambio_activo()

        campo_activo = CampoFiltrable(
            page, opciones_activo, on_seleccionar=_on_seleccionar_activo, placeholder="Activo", dense=False,
        )

    def _info_activo_actual() -> Optional[dict]:
        """{'tipo':, 'moneda_id':} del activo resuelto en este momento (fijo, elegido, o "nuevo" con moneda ya elegida) — None si todavía no alcanza para saberlo."""
        if activo_fijo is not None:
            return {"tipo": activo_fijo["tipo"], "moneda_id": activo_fijo["moneda_id"]}
        if campo_activo is None or campo_activo.id_seleccionado is None:
            return None
        if campo_activo.id_seleccionado == _ID_ACTIVO_NUEVO:
            if not campo_moneda_activo_nuevo.id_seleccionado:
                return None
            return {"tipo": dropdown_tipo_activo_nuevo.value, "moneda_id": int(campo_moneda_activo_nuevo.id_seleccionado)}
        activo = next((a for a in activos_existentes if a["id"] == campo_activo.id_seleccionado), None)
        return {"tipo": activo["tipo"], "moneda_id": activo["moneda_id"]} if activo else None

    # ------------------------------------------------------------
    # CAMPOS DE MONTO (Monto total / Cantidad / Precio unitario) — se
    # RECONSTRUYEN cada vez que cambia el activo resuelto (ver docstring).
    # ------------------------------------------------------------
    contenedor_campos_monto = ft.Column(spacing=ESPACIADO_DIALOGO)
    refs: dict = {"monto": None, "cantidad": None, "precio_unitario": None}

    def _reconstruir_campos_monto() -> None:
        info = _info_activo_actual()
        if info is not None:
            moneda = monedas_por_id.get(info["moneda_id"])
            decimales = moneda["decimales"] if moneda else DECIMALES_DEFAULT
            codigo_moneda = moneda["codigo"] if moneda else ""
            mostrar_cantidad = info["tipo"] in TIPOS_ACTIVO_CON_CANTIDAD
        else:
            decimales = DECIMALES_DEFAULT
            codigo_moneda = ""
            mostrar_cantidad = False

        texto_monto_previo = (
            refs["monto"].texto if refs["monto"] is not None
            else (f"{monto_inicial:.2f}" if monto_inicial is not None else None)
        )
        texto_precio_previo = refs["precio_unitario"].texto if refs["precio_unitario"] is not None else None
        texto_cantidad_previo = refs["cantidad"].value if refs["cantidad"] is not None else None

        sufijo_moneda = f" ({codigo_moneda})" if codigo_moneda else ""
        campo_monto = CampoMonto(
            page, on_confirmar=lambda m: None, decimales=decimales, dense=False,
            valor_inicial_minor=_texto_a_minor(texto_monto_previo, decimales),
            label=f"Monto total{sufijo_moneda}",
        )
        refs["monto"] = campo_monto
        controles: list[ft.Control] = [campo_monto.control]

        if mostrar_cantidad:
            campo_cantidad = ft.TextField(label="Cantidad (opcional)", value=texto_cantidad_previo or "")
            campo_precio_unitario = CampoMonto(
                page, on_confirmar=lambda m: None, decimales=decimales, dense=False,
                valor_inicial_minor=_texto_a_minor(texto_precio_previo, decimales),
                label=f"Precio unitario{sufijo_moneda}, opcional",
            )
            refs["cantidad"] = campo_cantidad
            refs["precio_unitario"] = campo_precio_unitario
            controles += [campo_cantidad, campo_precio_unitario.control]
        else:
            refs["cantidad"] = None
            refs["precio_unitario"] = None

        contenedor_campos_monto.controls = controles
        page.update()

    def _on_cambio_activo() -> None:
        _reconstruir_campos_monto()

    _reconstruir_campos_monto()  # construcción inicial

    # Dólar oficial: SIEMPRE en ARS, no depende del activo — ver docstring.
    campo_dolar_oficial = CampoMonto(
        page, on_confirmar=lambda m: None, decimales=decimales_dolar_oficial, dense=False,
        label="Dólar oficial al momento (ARS, opcional)",
    )

    # ------------------------------------------------------------
    # ASIGNACIONES DINÁMICAS — extraído a construir_editor_asignaciones(),
    # ver su docstring (mismo editor reusado por "Egreso general" en
    # ui/screens/ahorros.py).
    # ------------------------------------------------------------
    editor_asignaciones = construir_editor_asignaciones(page, objetivos_disponibles)

    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

    # ------------------------------------------------------------
    # RESOLUCIÓN DE ACTIVO (crea el "nuevo" recién acá, al confirmar)
    # ------------------------------------------------------------

    def _resolver_activo_id() -> Optional[int]:
        if activo_fijo is not None:
            return activo_fijo["id"]
        if campo_activo is None or not campo_activo.id_seleccionado:
            texto_error.value = "Seleccioná un activo (o creá uno nuevo) de la lista de sugerencias."
            return None
        if campo_activo.id_seleccionado != _ID_ACTIVO_NUEVO:
            return campo_activo.id_seleccionado
        nombre_nuevo = (campo_nombre_activo_nuevo.value or "").strip()
        if not nombre_nuevo:
            texto_error.value = "El nombre del activo nuevo no puede estar vacío."
            return None
        if not campo_moneda_activo_nuevo.id_seleccionado:
            texto_error.value = "Seleccioná la moneda del activo nuevo."
            return None
        try:
            resultado_activo = savings_service.create_activo(
                nombre=nombre_nuevo, tipo=dropdown_tipo_activo_nuevo.value,
                moneda_id=int(campo_moneda_activo_nuevo.id_seleccionado),
                cuenta_id=(
                    campo_cuenta_activo_nuevo.id_seleccionado
                    if campo_cuenta_activo_nuevo.id_seleccionado else None
                ),
                broker_id=campo_broker_activo_nuevo.id_seleccionado or None,
            )
        except SavingsError as err:
            texto_error.value = str(err)
            return None
        return resultado_activo.entity_id

    def _confirmar(e=None) -> None:
        texto_error.value = ""
        try:
            datetime.strptime((campo_fecha.value or "").strip(), "%Y-%m-%d")
        except ValueError:
            texto_error.value = "La fecha debe tener el formato AAAA-MM-DD."
            page.update()
            return

        info_activo = _info_activo_actual()
        if info_activo is None:
            texto_error.value = "Elegí un activo (existente o nuevo, con su moneda) antes de confirmar."
            page.update()
            return
        moneda_activo = monedas_por_id.get(info_activo["moneda_id"])
        decimales = moneda_activo["decimales"] if moneda_activo else DECIMALES_DEFAULT
        mostrar_cantidad = info_activo["tipo"] in TIPOS_ACTIVO_CON_CANTIDAD

        campo_monto_actual = refs["monto"]
        try:
            monto = float((campo_monto_actual.texto or "").strip().replace(",", "."))
        except ValueError:
            texto_error.value = "El monto no es un número válido."
            page.update()
            return
        if monto <= 0:
            texto_error.value = "El monto debe ser mayor a 0."
            page.update()
            return
        monto_total_minor = amount_to_minor(monto, decimales)

        cantidad = None
        if mostrar_cantidad and refs["cantidad"] is not None and (refs["cantidad"].value or "").strip():
            try:
                cantidad = float(refs["cantidad"].value.strip().replace(",", "."))
            except ValueError:
                texto_error.value = "La cantidad no es un número válido."
                page.update()
                return

        precio_unitario_minor = None
        if mostrar_cantidad and refs["precio_unitario"] is not None and (refs["precio_unitario"].texto or "").strip():
            try:
                precio_unitario = float(refs["precio_unitario"].texto.strip().replace(",", "."))
            except ValueError:
                texto_error.value = "El precio unitario no es un número válido."
                page.update()
                return
            precio_unitario_minor = amount_to_minor(precio_unitario, decimales)

        dolar_oficial_minor = None
        if (campo_dolar_oficial.texto or "").strip():
            try:
                dolar_oficial = float(campo_dolar_oficial.texto.strip().replace(",", "."))
            except ValueError:
                texto_error.value = "El dólar oficial no es un número válido."
                page.update()
                return
            dolar_oficial_minor = amount_to_minor(dolar_oficial, decimales_dolar_oficial)

        asignaciones, error_asignaciones = editor_asignaciones.resolver()
        if error_asignaciones is not None:
            texto_error.value = error_asignaciones
            page.update()
            return

        activo_id = _resolver_activo_id()
        if activo_id is None:
            page.update()
            return

        try:
            resultado = savings_service.register_purchase(
                activo_id=activo_id,
                fecha=campo_fecha.value.strip(),
                monto_total_minor=monto_total_minor,
                dolar_oficial_momento_minor=dolar_oficial_minor,
                cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                asignaciones=asignaciones,
                notas=notas_inicial,
            )
        except (SavingsError, ValueError) as err:
            # Cubre AsignacionInvalidaError (se muestra ACÁ, en texto_error,
            # sin que el caller cierre nada) y cualquier otro error de esta
            # confirmación, por consistencia — ver docstring del módulo.
            texto_error.value = str(err)
            page.update()
            return

        on_exito(resultado)

    partes: list[ft.Control] = [campo_fecha]
    if campo_activo is not None:
        partes.append(campo_activo.control)
        partes.append(seccion_activo_nuevo)
    partes.append(contenedor_campos_monto)
    partes += [
        campo_dolar_oficial.control,
        ft.Divider(height=1),
        ft.Text("Asignación a objetivos (opcional)", size=TypographyTokens.LABEL_SIZE, weight=ft.FontWeight.BOLD),
        editor_asignaciones.contenido,
        texto_error,
    ]

    contenido = ft.Container(
        width=ANCHO_DIALOGO_COMPRA_AHORRO,
        content=ft.Column(partes, tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO),
    )

    return FormularioCompraAhorro(contenido=contenido, confirmar=_confirmar)


# ============================================================
# PANTALLA DE AHORROS — nuevo activo, movimiento, objetivos (ver docstring)
# ============================================================

def _minor_de_campo(campo: CampoMonto, decimales: int) -> tuple[Optional[int], bool]:
    """(monto en minor units, o None si está vacío; ¿es válido?). Resuelve antes una fórmula pendiente."""
    if not campo.confirmar():
        return None, False
    texto = (campo.texto or "").strip()
    if not texto:
        return None, True
    valor = numero(texto)
    if valor is None:
        return None, False
    return amount_to_minor(valor, decimales), True


def _suma_supera_100(asignaciones: list[dict]) -> bool:
    return sum(a["porcentaje"] for a in asignaciones) > 100 + TOLERANCIA_PORCENTAJE


def _contenido_formulario(partes: list[ft.Control]) -> ft.Control:
    return ft.Container(
        width=ANCHO_DIALOGO_COMPRA_AHORRO,
        content=ft.Column(partes, tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO),
    )


def _label_transaccion(t: dict) -> str:
    """'2026-10-05 · TRANSFERENCIA A COCOS · -$50,000.00 ARS · BBVA' para el selector de vínculo."""
    signo = {"egreso": "-", "ingreso": "+"}.get(t["tipo_movimiento"], "")
    monto = amount_display(t["monto_minor"], t["decimales"], t["currency_symbol"] or "")
    return (
        f"{t['fecha']} · {(t['concepto'] or '').upper()} · {signo}{monto} {t['currency_code']} · "
        f"{(t['account_name'] or '').upper()}"
    )


def construir_nuevo_activo(
    page: ft.Page,
    savings_service: SavingsService,
    accounts_service: AccountsService,
    on_exito: Callable[[SavingsResult], None],
    tipo_inicial: Optional[str] = None,
) -> FormularioCompraAhorro:
    """
    Alta de un activo (ver docstring del módulo). on_exito recibe el
    resultado de create_activo() (entity_id = el activo nuevo); los errores
    quedan en texto_error, dentro del formulario.
    """
    monedas = accounts_service.list_currencies()
    moneda_default = next((m for m in monedas if m["codigo"] == MONEDA_DOLAR_OFICIAL_CODIGO), monedas[0] if monedas else None)
    monedas_por_id = {str(m["id"]): m for m in monedas}
    cuentas_activas = _cuentas_no_credito(accounts_service.list_accounts(solo_activas=True))

    campo_nombre = ft.TextField(label="NOMBRE DEL ACTIVO", autofocus=True)
    dropdown_tipo = ft.Dropdown(
        label="TIPO", dense=True,
        options=[ft.dropdown.Option(key=t, text=_tipo_activo_display(t)) for t in TIPOS_NUEVO_ACTIVO],
        value=tipo_inicial if tipo_inicial in TIPOS_NUEVO_ACTIVO else TIPOS_NUEVO_ACTIVO[0],
    )
    campo_broker = CampoFiltrable(
        page, [(b["id"], b["nombre"]) for b in savings_service.get_brokers()],
        on_seleccionar=lambda id_: None, placeholder="BROKER (OPCIONAL)", dense=False,
    )
    dropdown_moneda = ft.Dropdown(
        label="MONEDA", dense=True,
        options=[ft.dropdown.Option(key=str(m["id"]), text=m["codigo"]) for m in monedas],
        value=str(moneda_default["id"]) if moneda_default else None,
    )
    campo_cuenta = CampoFiltrable(
        page, [(str(c["id"]), c["nombre"]) for c in cuentas_activas],
        on_seleccionar=lambda id_: None, placeholder="CUENTA ASOCIADA (OPCIONAL)", dense=False,
    )
    # Los decimales reales salen de la moneda elegida al confirmar (_minor_de_campo()).
    campo_comision_compra = CampoMonto(
        page, on_confirmar=lambda m: None, dense=False, label="COMISIÓN DE COMPRA POR DEFECTO (OPCIONAL)",
    )
    campo_comision_venta = CampoMonto(
        page, on_confirmar=lambda m: None, dense=False, label="COMISIÓN DE VENTA POR DEFECTO (OPCIONAL)",
    )
    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

    def _error(mensaje: str) -> None:
        texto_error.value = mensaje.upper()
        page.update()

    def _confirmar(e=None) -> None:
        texto_error.value = ""
        nombre = (campo_nombre.value or "").strip()
        if not nombre:
            _error("EL NOMBRE DEL ACTIVO NO PUEDE ESTAR VACÍO.")
            return
        moneda = monedas_por_id.get(dropdown_moneda.value or "")
        if moneda is None:
            _error("ELEGÍ LA MONEDA DEL ACTIVO.")
            return
        comision_compra, valida_compra = _minor_de_campo(campo_comision_compra, moneda["decimales"])
        comision_venta, valida_venta = _minor_de_campo(campo_comision_venta, moneda["decimales"])
        if not (valida_compra and valida_venta):
            _error("UNA COMISIÓN NO ES UN NÚMERO VÁLIDO.")
            return
        try:
            resultado = savings_service.create_activo(
                nombre=nombre,
                tipo=dropdown_tipo.value,
                moneda_id=moneda["id"],
                cuenta_id=campo_cuenta.id_seleccionado or None,
                broker_id=campo_broker.id_seleccionado or None,
                comision_compra_minor=comision_compra or 0,
                comision_venta_minor=comision_venta or 0,
            )
        except (SavingsError, ValueError) as err:
            _error(str(err))
            return
        on_exito(resultado)

    contenido = _contenido_formulario([
        campo_nombre, dropdown_tipo, campo_broker.control, dropdown_moneda, campo_cuenta.control,
        campo_comision_compra.control, campo_comision_venta.control,
        texto_error,
    ])
    return FormularioCompraAhorro(contenido=contenido, confirmar=_confirmar)


def construir_editar_activo(
    page: ft.Page,
    savings_service: SavingsService,
    accounts_service: AccountsService,
    activo: dict,
    on_exito: Callable[[SavingsResult], None],
) -> FormularioCompraAhorro:
    """
    Editar un instrumento (docs/DATA_MODEL_DECISIONS.md sección 35): los
    campos de construir_nuevo_activo(), precargados, con
    SavingsService.update_activo(). El TIPO se muestra pero no se edita; la
    MONEDA queda deshabilitada si el instrumento ya tiene movimientos
    (decisión del usuario). Vaciar el broker o la cuenta los desvincula. Un
    broker o una cuenta actuales que ya no se ofrecen (dados de baja, o una
    tarjeta) se agregan a sus opciones: si no, el campo arrancaría vacío y
    guardar los borraría sin querer. `activo`: una entrada de
    SavingsService.get_resumen_por_tipo().
    """
    monedas = accounts_service.list_currencies()
    monedas_por_id = {str(m["id"]): m for m in monedas}
    con_movimientos = activo["movimientos"] > 0

    opciones_broker = [(b["id"], b["nombre"]) for b in savings_service.get_brokers()]
    if activo["broker_id"] and all(id_ != activo["broker_id"] for id_, _ in opciones_broker):
        opciones_broker.append((activo["broker_id"], activo["broker"] or activo["broker_id"]))
    opciones_cuenta = [(str(c["id"]), c["nombre"]) for c in _cuentas_no_credito(accounts_service.list_accounts(solo_activas=True))]
    cuenta_actual = str(activo["cuenta_id"]) if activo["cuenta_id"] else None
    if cuenta_actual and all(id_ != cuenta_actual for id_, _ in opciones_cuenta):
        cuenta = next((c for c in accounts_service.list_accounts(solo_activas=False) if str(c["id"]) == cuenta_actual), None)
        opciones_cuenta.append((cuenta_actual, cuenta["nombre"] if cuenta else cuenta_actual))

    campo_nombre = ft.TextField(label="NOMBRE DEL ACTIVO", value=activo["activo"], autofocus=True)
    texto_tipo = ft.Text(
        f"TIPO: {_tipo_activo_display(activo['tipo'])} (NO SE EDITA)", size=TypographyTokens.LABEL_SIZE,
    )
    campo_broker = CampoFiltrable(
        page, opciones_broker, on_seleccionar=lambda id_: None, placeholder="BROKER (OPCIONAL)", dense=False,
        valor_inicial_id=activo["broker_id"],
    )
    dropdown_moneda = ft.Dropdown(
        label="MONEDA", dense=True,
        options=[ft.dropdown.Option(key=str(m["id"]), text=m["codigo"]) for m in monedas],
        value=str(activo["moneda_id"]), disabled=con_movimientos,
        tooltip="YA TIENE MOVIMIENTOS: SU MONEDA NO SE CAMBIA." if con_movimientos else None,
    )
    campo_cuenta = CampoFiltrable(
        page, opciones_cuenta, on_seleccionar=lambda id_: None, placeholder="CUENTA ASOCIADA (OPCIONAL)", dense=False,
        valor_inicial_id=cuenta_actual,
    )
    # Muestran un valor guardado: persistir_formula=True (CLAUDE.md §9).
    campo_comision_compra = CampoMonto(
        page, on_confirmar=lambda m: None, dense=False, label="COMISIÓN DE COMPRA POR DEFECTO (OPCIONAL)",
        valor_inicial_minor=activo["comision_compra_minor"] or None, persistir_formula=True,
    )
    campo_comision_venta = CampoMonto(
        page, on_confirmar=lambda m: None, dense=False, label="COMISIÓN DE VENTA POR DEFECTO (OPCIONAL)",
        valor_inicial_minor=activo["comision_venta_minor"] or None, persistir_formula=True,
    )
    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

    def _error(mensaje: str) -> None:
        texto_error.value = mensaje.upper()
        page.update()

    def _confirmar(e=None) -> None:
        texto_error.value = ""
        moneda = monedas_por_id.get(dropdown_moneda.value or "")
        if moneda is None:
            _error("ELEGÍ LA MONEDA DEL ACTIVO.")
            return
        comision_compra, valida_compra = _minor_de_campo(campo_comision_compra, moneda["decimales"])
        comision_venta, valida_venta = _minor_de_campo(campo_comision_venta, moneda["decimales"])
        if not (valida_compra and valida_venta):
            _error("UNA COMISIÓN NO ES UN NÚMERO VÁLIDO.")
            return
        try:
            resultado = savings_service.update_activo(
                activo["activo_id"],
                nombre=campo_nombre.value or "",
                broker_id=campo_broker.id_seleccionado or None,
                moneda_id=moneda["id"],  # la misma que tenía no es un cambio
                cuenta_id=campo_cuenta.id_seleccionado or None,
                comision_compra_minor=comision_compra or 0,
                comision_venta_minor=comision_venta or 0,
            )
        except (SavingsError, ValueError) as err:
            _error(str(err))
            return
        on_exito(resultado)

    partes: list[ft.Control] = [campo_nombre, texto_tipo, campo_broker.control, dropdown_moneda]
    if con_movimientos:
        partes.append(ft.Text(
            "YA TIENE MOVIMIENTOS: LA MONEDA NO SE PUEDE CAMBIAR.", size=TypographyTokens.LABEL_SIZE, italic=True,
        ))
    partes += [campo_cuenta.control, campo_comision_compra.control, campo_comision_venta.control, texto_error]
    return FormularioCompraAhorro(contenido=_contenido_formulario(partes), confirmar=_confirmar)


def construir_movimiento(
    page: ft.Page,
    savings_service: SavingsService,
    accounts_service: AccountsService,
    activo: dict,
    on_exito: Callable[[SavingsResult], None],
    tipo_inicial: Optional[str] = None,
) -> FormularioCompraAhorro:
    """
    Movimiento de un activo (ver docstring del módulo). `activo`: una
    entrada de SavingsService.get_resumen_por_tipo() (activo_id, activo,
    tipo, por_unidades, moneda_id, moneda, decimales, comisiones por
    defecto).

    Acciones / CEDEARs (moneda por movimiento, docs/DATA_MODEL_DECISIONS.md
    sección 33): MONEDA (default la del activo) y, en compra / venta,
    CANTIDAD + MONTO bruto (cantidad × precio, sin la comisión) en vez del
    precio unitario. FCI y plazos: sin selector, la moneda del activo.
    Cambiar la moneda solo cambia los labels: los CampoMonto se convierten
    al confirmar con los decimales de la moneda elegida.
    """
    opciones_tipo = TIPOS_MOVIMIENTO_POR_UNIDADES if activo["por_unidades"] else TIPOS_MOVIMIENTO_POR_MONTO
    claves_tipo = [clave for clave, _ in opciones_tipo]
    monedas = accounts_service.list_currencies()
    monedas_por_id = {str(m["id"]): m for m in monedas}
    moneda_ars = next((m for m in monedas if m["codigo"] == MONEDA_DOLAR_OFICIAL_CODIGO), None)
    decimales_dolar = moneda_ars["decimales"] if moneda_ars else DECIMALES_DEFAULT
    dropdown_moneda = ft.Dropdown(
        label="MONEDA", dense=True,
        options=[ft.dropdown.Option(key=str(m["id"]), text=m["codigo"]) for m in monedas],
        value=str(activo["moneda_id"]),
        on_select=lambda e: _al_cambiar_moneda(),
    )

    def _moneda() -> dict:
        """La moneda del movimiento: la elegida (acciones / CEDEARs) o la del activo."""
        moneda = monedas_por_id.get(dropdown_moneda.value or "") if activo["por_unidades"] else None
        return moneda or {"id": activo["moneda_id"], "codigo": activo["moneda"], "decimales": activo["decimales"]}

    def _labels_monto() -> None:
        codigo = _moneda()["codigo"]
        if "monto" in refs:
            sufijo = ", SIN COMISIÓN" if dropdown_tipo.value in ("compra", "venta") else ""
            refs["monto"].control.label = f"MONTO ({codigo}{sufijo})"
        if "comision" in refs:
            refs["comision"].control.label = f"COMISIÓN ({codigo}, OPCIONAL)"

    def _al_cambiar_moneda() -> None:
        _labels_monto()
        page.update()

    dropdown_tipo = ft.Dropdown(
        label="TIPO DE MOVIMIENTO", dense=True,
        options=[ft.dropdown.Option(key=clave, text=texto) for clave, texto in opciones_tipo],
        value=tipo_inicial if tipo_inicial in claves_tipo else claves_tipo[0],
        on_select=lambda e: _dibujar_campos(),
    )
    campo_fecha = ft.TextField(
        label="FECHA (AAAA-MM-DD)", value=date.today().isoformat(), on_blur=lambda e: _dibujar_vinculo(),
    )
    contenedor_campos = ft.Column(spacing=ESPACIADO_DIALOGO)
    contenedor_vinculo = ft.Column(spacing=ESPACIADO_DIALOGO)
    contenedor_objetivos = ft.Column(spacing=ESPACIADO_ASIGNACIONES)
    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)
    refs: dict = {"vinculo": None, "mes_vinculo": None, "modo_objetivos": None, "editor": None, "proporcional": None}

    def _editor_objetivos(iniciales: Optional[list[dict]] = None) -> EditorAsignaciones:
        return construir_editor_asignaciones(
            page, savings_service.list_objetivos(), iniciales=iniciales,
            crear_objetivo=_creador_objetivo(savings_service), con_resumen=True,
        )

    def _dibujar_objetivos() -> None:
        """
        Rendimiento: PROPORCIONAL tildado por defecto (get_reparto_proporcional()
        al guardar); destildarlo muestra el editor con ese reparto precargado.
        El resto: el editor, sin filas (los objetivos son opcionales). Solo
        se rearma al entrar o salir de RENDIMIENTO: pasar de APORTE a RETIRO
        conserva lo cargado.
        """
        modo = "rendimiento" if dropdown_tipo.value == "rendimiento" else "otros"
        if modo == refs["modo_objetivos"]:
            return
        refs["modo_objetivos"] = modo
        titulo = ft.Text("OBJETIVOS", size=TypographyTokens.LABEL_SIZE, weight=ft.FontWeight.BOLD)
        if modo == "rendimiento":
            refs["editor"] = None
            contenedor_editor = ft.Column(visible=False)
            refs["proporcional"] = ft.Checkbox(
                label=TEXTO_REPARTO_PROPORCIONAL, value=True,
                on_change=lambda e: _al_cambiar_proporcional(contenedor_editor),
            )
            contenedor_objetivos.controls = [titulo, refs["proporcional"], contenedor_editor]
        else:
            refs["proporcional"] = None
            refs["editor"] = _editor_objetivos()
            contenedor_objetivos.controls = [titulo, refs["editor"].contenido]

    def _al_cambiar_proporcional(contenedor_editor: ft.Column) -> None:
        if refs["proporcional"].value:
            refs["editor"] = None
            contenedor_editor.controls = []
            contenedor_editor.visible = False
        else:
            try:
                reparto = savings_service.get_reparto_proporcional(activo["activo_id"], (campo_fecha.value or "").strip())
            except SavingsError:
                reparto = []  # fecha inválida todavía: el editor arranca vacío
            refs["editor"] = _editor_objetivos(reparto)
            contenedor_editor.controls = [refs["editor"].contenido]
            contenedor_editor.visible = True
        page.update()

    def _dibujar_campos() -> None:
        tipo = dropdown_tipo.value
        decimales = _moneda()["decimales"]
        refs.pop("comision", None)
        # Cantidad, moneda, monto, comisión (orden del pedido); la moneda solo en acciones / CEDEARs.
        controles: list[ft.Control] = []
        if tipo in ("compra", "venta"):
            refs["cantidad"] = ft.TextField(label="CANTIDAD (ENTERA)")
            controles.append(refs["cantidad"])
        if activo["por_unidades"]:
            controles.append(dropdown_moneda)
        refs["monto"] = CampoMonto(page, on_confirmar=lambda m: None, decimales=decimales, dense=False)
        controles.append(refs["monto"].control)
        if tipo in ("compra", "venta"):
            comision_default = activo["comision_compra_minor"] if tipo == "compra" else activo["comision_venta_minor"]
            refs["comision"] = CampoMonto(
                page, on_confirmar=lambda m: None, decimales=decimales, dense=False,
                valor_inicial_minor=comision_default or None,
            )
            controles.append(refs["comision"].control)
            if tipo == "compra":
                refs["dolar"] = CampoMonto(
                    page, on_confirmar=lambda m: None, decimales=decimales_dolar, dense=False,
                    label="DÓLAR DEL DÍA (ARS, OPCIONAL)",
                )
                controles.append(refs["dolar"].control)
        _labels_monto()
        contenedor_campos.controls = controles
        # Un rendimiento no se vincula a una transacción (registrar_rendimiento() no la acepta).
        contenedor_vinculo.visible = tipo != "rendimiento"
        _dibujar_objetivos()
        page.update()

    def _dibujar_vinculo() -> None:
        """Transacciones del mes de la fecha — se rearma solo si cambió el mes (conserva lo elegido)."""
        fecha = (campo_fecha.value or "").strip()
        if fecha[:7] == refs["mes_vinculo"]:
            return
        try:
            transacciones = savings_service.list_transacciones_vinculables(fecha)
        except SavingsError:
            return  # fecha inválida todavía: se avisa al confirmar
        refs["mes_vinculo"] = fecha[:7]
        refs["vinculo"] = CampoFiltrable(
            page, [(t["id"], _label_transaccion(t)) for t in transacciones],
            on_seleccionar=lambda id_: None, placeholder="VINCULAR A TRANSACCIÓN DEL REGISTRO (OPCIONAL)", dense=False,
        )
        contenedor_vinculo.controls = [refs["vinculo"].control]
        page.update()

    _dibujar_campos()
    _dibujar_vinculo()

    def _error(mensaje: str) -> None:
        texto_error.value = mensaje.upper()
        page.update()

    def _confirmar(e=None) -> None:
        texto_error.value = ""
        fecha = (campo_fecha.value or "").strip()
        try:
            datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            _error("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.")
            return
        tipo = dropdown_tipo.value
        vinculo = refs["vinculo"]
        transaccion_id = (vinculo.id_seleccionado or None) if vinculo is not None and tipo != "rendimiento" else None
        # None = PROPORCIONAL (solo un rendimiento): lo calcula el service con la fecha.
        asignaciones: Optional[list[dict]] = None
        if refs["editor"] is not None:
            asignaciones, error_asignaciones = refs["editor"].resolver()
            if error_asignaciones is not None:
                _error(error_asignaciones)
                return
            if _suma_supera_100(asignaciones):
                _error("LOS OBJETIVOS SUMAN MÁS DE 100%.")
                return
        moneda = _moneda()
        decimales = moneda["decimales"]
        # Solo acciones / CEDEARs mandan moneda: en el resto la pone el service (la del activo).
        moneda_id = int(moneda["id"]) if activo["por_unidades"] else None
        monto, valido_monto = _minor_de_campo(refs["monto"], decimales)
        if not valido_monto or not monto:
            _error("INGRESÁ UN MONTO VÁLIDO.")
            return
        try:
            if tipo in ("compra", "venta"):
                texto_cantidad = (refs["cantidad"].value or "").strip()
                if not texto_cantidad.isdigit() or int(texto_cantidad) <= 0:
                    _error("LA CANTIDAD TIENE QUE SER UN NÚMERO ENTERO MAYOR A 0.")
                    return
                comision, valida_comision = _minor_de_campo(refs["comision"], decimales)
                if not valida_comision:
                    _error("LA COMISIÓN NO ES UN NÚMERO VÁLIDO.")
                    return
                if tipo == "compra":
                    dolar, valido_dolar = _minor_de_campo(refs["dolar"], decimales_dolar)
                    if not valido_dolar:
                        _error("EL DÓLAR DEL DÍA NO ES UN NÚMERO VÁLIDO.")
                        return
                    resultado = savings_service.registrar_compra(
                        activo["activo_id"], int(texto_cantidad), monto, comision or 0, fecha,
                        transaccion_id=transaccion_id, dolar_momento_minor=dolar, asignaciones=asignaciones,
                        moneda_id=moneda_id,
                    )
                else:
                    resultado = savings_service.registrar_venta(
                        activo["activo_id"], int(texto_cantidad), monto, comision or 0, fecha,
                        transaccion_id=transaccion_id, asignaciones=asignaciones, moneda_id=moneda_id,
                    )
            else:
                if tipo == "aporte":
                    resultado = savings_service.registrar_aporte(
                        activo["activo_id"], monto, fecha, transaccion_id=transaccion_id, asignaciones=asignaciones,
                    )
                elif tipo == "retiro":
                    resultado = savings_service.registrar_retiro(
                        activo["activo_id"], monto, fecha, transaccion_id=transaccion_id, asignaciones=asignaciones,
                    )
                else:
                    resultado = savings_service.registrar_rendimiento(
                        activo["activo_id"], monto, fecha, asignaciones=asignaciones, moneda_id=moneda_id,
                    )
        except (SavingsError, ValueError) as err:
            _error(str(err))
            return
        on_exito(resultado)

    contenido = _contenido_formulario([
        dropdown_tipo, campo_fecha, contenedor_campos, contenedor_vinculo, contenedor_objetivos, texto_error,
    ])
    return FormularioCompraAhorro(contenido=contenido, confirmar=_confirmar)


def construir_objetivos_movimiento(
    page: ft.Page,
    savings_service: SavingsService,
    iniciales: list[dict],
    on_confirmar: Callable[[Optional[list[dict]]], None],
    monto_minor: Optional[int] = None,
    decimales: int = DECIMALES_DEFAULT,
    simbolo: str = "",
    con_proporcional: bool = False,
) -> FormularioCompraAhorro:
    """
    Objetivos de UN movimiento (ver docstring del módulo): el diálogo que
    abre la celda OBJETIVOS de la tabla de ui/screens/ahorros.py, tanto en
    la fila de alta como en un movimiento ya cargado. El editor arranca con
    `iniciales` ([{"objetivo_id", "porcentaje"}]; sin iniciales, una fila
    para elegir), deja crear un objetivo nuevo ahí mismo y muestra el monto
    de cada fila si se pasa monto_minor.

    on_confirmar(asignaciones): [{"objetivo_id", "porcentaje", "nombre"}]
    ([] = sin objetivos), o None si se tocó "USAR REPARTO PROPORCIONAL"
    (solo con con_proporcional: los rendimientos) — qué hacer con cada caso
    lo decide el caller. Si on_confirmar lanza SavingsError / ValueError, el
    mensaje queda en el diálogo (no se cierra).
    """
    editor = construir_editor_asignaciones(
        page, savings_service.list_objetivos(), iniciales=iniciales, crear_objetivo=_creador_objetivo(savings_service),
        monto_minor=monto_minor, decimales=decimales, simbolo=simbolo, con_resumen=True, una_fila_vacia=True,
    )
    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

    def _error(mensaje: str) -> None:
        texto_error.value = mensaje.upper()
        page.update()

    def _entregar(asignaciones: Optional[list[dict]]) -> None:
        try:
            on_confirmar(asignaciones)
        except (SavingsError, ValueError) as err:
            _error(str(err))

    def _confirmar(e=None) -> None:
        texto_error.value = ""
        asignaciones, error_asignaciones = editor.resolver()
        if error_asignaciones is not None:
            _error(error_asignaciones)
            return
        if _suma_supera_100(asignaciones):
            _error("LOS OBJETIVOS SUMAN MÁS DE 100%.")
            return
        _entregar(asignaciones)

    partes: list[ft.Control] = [
        ft.Text(
            "A QUÉ OBJETIVOS VA ESTE MOVIMIENTO Y EN QUÉ PORCENTAJE. LO QUE NO LLEGUE AL 100% QUEDA SIN ASIGNAR.",
            size=TypographyTokens.LABEL_SIZE,
        ),
        editor.contenido,
    ]
    if con_proporcional:
        partes.append(ft.TextButton(
            content=ft.Text("USAR REPARTO PROPORCIONAL"), tooltip=TEXTO_REPARTO_PROPORCIONAL,
            on_click=lambda e: _entregar(None),
        ))
    partes.append(texto_error)
    contenido = ft.Container(
        width=ANCHO_DIALOGO_OBJETIVOS,
        content=ft.Column(partes, tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO),
    )
    return FormularioCompraAhorro(contenido=contenido, confirmar=_confirmar)


def _texto_parte_objetivo(parte: dict) -> str:
    """'FCI COCOS · PESOS PLUS: 382,115.40 ARS' / 'CEDEAR BULL MARKET · NVDA: 103 UNIDADES' (+ aviso si quedó en cero)."""
    nombre = f"{parte['broker']} · {parte['activo']}" if parte["broker"] else parte["activo"]
    if parte["por_unidades"]:
        tenencia = f"{parte['unidades'] or 0:g} UNIDADES"
    else:
        tenencia = f"{amount_display(parte['saldo_minor'], parte['decimales'], parte['simbolo'])} {parte['moneda']}"
    aviso = " (EN CERO: SOLO CAMBIA EL HISTORIAL)" if parte["en_cero"] else ""
    return f"{_tipo_activo_display(parte['tipo'])} {nombre}: {tenencia}{aviso}".upper()


def construir_eliminar_objetivo(
    page: ft.Page,
    savings_service: SavingsService,
    objetivo: dict,
    on_exito: Callable[[SavingsResult], None],
) -> FormularioCompraAhorro:
    """
    Eliminar un objetivo (docs/DATA_MODEL_DECISIONS.md sección 32): por cada
    instrumento donde tiene algo (SavingsService.get_partes_de_objetivo()),
    un editor para elegir a qué objetivos pasa su parte — lo que no llegue
    al 100% queda sin asignar — y SavingsService.delete_objetivo() al
    confirmar. `objetivo`: {"id", "nombre"}.

    Un instrumento con algo arranca con una fila para elegir (sin elegir,
    resolver() avisa: hay que elegir o quitarla con ✕, que es dejarlo sin
    asignar a propósito). Uno donde el objetivo ya está en cero arranca sin
    filas: solo cambia a quién figura el historial.

    Sin "+ CREAR NUEVO OBJETIVO": cada instrumento tiene su editor con su
    propia lista, y un objetivo creado desde uno no aparecería en los demás
    (se crearía dos veces). Para pasar la parte a un objetivo nuevo, se crea
    antes con + NUEVO OBJETIVO.
    """
    partes = savings_service.get_partes_de_objetivo(objetivo["id"])
    otros = [o for o in savings_service.list_objetivos() if o["id"] != objetivo["id"]]
    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)
    editores: list[tuple[dict, EditorAsignaciones]] = []
    controles: list[ft.Control] = []
    if partes:
        controles.append(ft.Text(
            f"SE ELIMINA {objetivo['nombre'].upper()}. ELEGÍ A QUÉ OBJETIVOS PASA LO QUE TENÍA EN CADA INSTRUMENTO "
            "(LO QUE NO LLEGUE AL 100% QUEDA SIN ASIGNAR). PARA PASARLO A UN OBJETIVO NUEVO, CREALO ANTES CON "
            "+ NUEVO OBJETIVO.",
            size=TypographyTokens.LABEL_SIZE,
        ))
    else:
        controles.append(ft.Text(
            f"{objetivo['nombre'].upper()} NO TIENE MOVIMIENTOS: SE ELIMINA DIRECTO.", size=TypographyTokens.LABEL_SIZE,
        ))
    for parte in partes:
        editor = construir_editor_asignaciones(
            page, otros,
            monto_minor=parte["saldo_minor"] if not parte["por_unidades"] and parte["saldo_minor"] > 0 else None,
            decimales=parte["decimales"], simbolo=parte["simbolo"], con_resumen=True,
            una_fila_vacia=not parte["en_cero"],
        )
        editores.append((parte, editor))
        controles += [
            ft.Divider(height=1),
            ft.Text(_texto_parte_objetivo(parte), size=TypographyTokens.LABEL_SIZE, weight=ft.FontWeight.BOLD),
            editor.contenido,
        ]
    controles.append(texto_error)

    def _error(mensaje: str) -> None:
        texto_error.value = mensaje.upper()
        page.update()

    def _confirmar(e=None) -> None:
        texto_error.value = ""
        repartos: dict[str, list[dict]] = {}
        for parte, editor in editores:
            asignaciones, error_asignaciones = editor.resolver()
            if error_asignaciones is None and _suma_supera_100(asignaciones):
                error_asignaciones = "LOS OBJETIVOS SUMAN MÁS DE 100%."
            if error_asignaciones is not None:
                _error(f"{parte['activo']}: {error_asignaciones}")
                return
            repartos[parte["activo_id"]] = [
                {"objetivo_id": a["objetivo_id"], "porcentaje": a["porcentaje"]} for a in asignaciones
            ]
        try:
            resultado = savings_service.delete_objetivo(objetivo["id"], repartos)
        except (SavingsError, ValueError) as err:
            _error(str(err))
            return
        on_exito(resultado)

    contenido = ft.Container(
        width=ANCHO_DIALOGO_OBJETIVOS,
        content=ft.Column(controles, tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO),
    )
    return FormularioCompraAhorro(contenido=contenido, confirmar=_confirmar)
