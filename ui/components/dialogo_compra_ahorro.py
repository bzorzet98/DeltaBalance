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
  6g), comisiones por defecto y objetivos con porcentaje (suma <= 100;
  SavingsService.assign_objetivo() por cada uno). on_exito recibe el
  resultado de create_activo().
- construir_movimiento(): según el tipo de activo, COMPRA / VENTA /
  RENDIMIENTO (acciones, CEDEARs: cantidad entera + precio unitario +
  comisión, y el dólar del día en la compra) o APORTE / RETIRO /
  RENDIMIENTO (FCI, plazos: monto). Fecha y, salvo en un rendimiento, una
  transacción del Registro del mismo mes para vincular (opcional; si no se
  elige ninguna y el activo tiene cuenta, el service crea la suya). Los
  campos se rearman al cambiar el tipo; la lista de transacciones, al
  cambiar el mes de la fecha. "RETIRO" es una venta por monto
  (registrar_retiro()).
- construir_objetivos_activo(): el reparto del activo entre objetivos,
  editable (construir_editor_asignaciones() precargado con `iniciales`).
  Aplica las diferencias con remove_objetivo()/assign_objetivo(), primero
  las bajas y las rebajas para no pasar nunca del 100% a mitad de camino.
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
ANCHO_CAMPO_ASIGNACION_OBJETIVO = 200
ANCHO_CAMPO_ASIGNACION_PORCENTAJE = 90
ESPACIADO_DIALOGO = 12

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
    """contenido: Column con las filas + "+ Agregar otro objetivo", para insertar en el Column del diálogo del caller. resolver(): ver construir_editor_asignaciones()."""
    contenido: ft.Control
    resolver: Callable[[], tuple[Optional[list[dict]], Optional[str]]]


def construir_editor_asignaciones(
    page: ft.Page, objetivos_disponibles: list[dict], iniciales: Optional[list[dict]] = None,
) -> EditorAsignaciones:
    """
    iniciales (opcional, rediseño de Ahorros e Inversiones): filas que
    arrancan cargadas, [{"objetivo_id": ..., "porcentaje": ...}] — las usa
    construir_objetivos_activo() para editar el reparto actual de un activo.

    Lista dinámica de filas (Objetivo + %) — extraída para que
    ui/screens/ahorros.py la reuse LITERALMENTE (no una copia adaptada)
    en el diálogo "Egreso general" (register_sale() con lista de
    asignaciones, Tarea 6d), en vez de duplicar este mecanismo.

    "+ Agregar otro objetivo" agrega una fila nueva a una lista Python
    interna Y a un ft.Column visible, cada fila con su propio botón
    "Quitar" que saca esa fila de ambos y refresca la columna. Puede
    quedar en cero filas.

    resolver() valida y arma la lista final de asignaciones (llamarlo al
    confirmar el diálogo del caller): una fila totalmente vacía (sin
    objetivo Y sin porcentaje) se ignora en silencio; una fila PARCIAL
    (una de las dos cosas cargada, la otra no) es un error explícito,
    igual que un mismo objetivo repetido en más de una fila — esto
    último no lo valida ningún service (dejaría un IntegrityError crudo
    de SQLite por el UNIQUE(movimiento_id, objetivo_id), nunca visto por
    el usuario como mensaje claro). Devuelve (asignaciones, None) si todo
    validó, o (None, mensaje_error) si no — el caller decide dónde
    mostrar ese mensaje (típicamente su propio texto_error). NO valida
    que la suma de porcentaje no supere 100 — esa regla de negocio queda
    del lado del service (AsignacionInvalidaError), tanto para
    register_purchase() como para register_sale().
    """
    filas_asignacion: list[dict] = []
    columna_asignaciones = ft.Column(spacing=6)

    def _refrescar_columna() -> None:
        columna_asignaciones.controls = [f["row"] for f in filas_asignacion]
        page.update()

    def _agregar_fila(e=None, objetivo_id: Optional[str] = None, porcentaje: Optional[float] = None) -> None:
        campo_objetivo_fila = CampoFiltrable(
            page, [(str(o["id"]), o["nombre"]) for o in objetivos_disponibles],
            on_seleccionar=lambda id_: None, placeholder="Objetivo", width=ANCHO_CAMPO_ASIGNACION_OBJETIVO,
            valor_inicial_id=str(objetivo_id) if objetivo_id is not None else None,
        )
        campo_porcentaje_fila = ft.TextField(
            hint_text="%", width=ANCHO_CAMPO_ASIGNACION_PORCENTAJE, dense=True,
            value=f"{porcentaje:g}" if porcentaje is not None else None,
        )
        fila_dict = {"objetivo": campo_objetivo_fila, "porcentaje": campo_porcentaje_fila}

        def _quitar(e=None, fila_dict=fila_dict) -> None:
            filas_asignacion.remove(fila_dict)
            _refrescar_columna()

        fila_dict["row"] = ft.Row(
            [
                campo_objetivo_fila.control,
                campo_porcentaje_fila,
                ft.IconButton(icon=ft.Icons.CLOSE, icon_size=16, tooltip="Quitar", on_click=_quitar),
            ],
            spacing=6,
        )
        filas_asignacion.append(fila_dict)
        _refrescar_columna()

    boton_agregar = ft.TextButton(content=ft.Text("+ Agregar otro objetivo"), on_click=_agregar_fila)

    def _resolver() -> tuple[Optional[list[dict]], Optional[str]]:
        asignaciones: list[dict] = []
        ids_vistos: set[int] = set()
        for fila in filas_asignacion:
            objetivo_id_str = fila["objetivo"].id_seleccionado
            porcentaje_texto = (fila["porcentaje"].value or "").strip()
            if not objetivo_id_str and not porcentaje_texto:
                continue  # fila vacía — se ignora, ver docstring
            if not objetivo_id_str:
                return None, "Hay una fila de asignación sin objetivo seleccionado."
            try:
                porcentaje = float(porcentaje_texto.replace(",", "."))
            except ValueError:
                return None, "El porcentaje de una asignación no es un número válido."
            if porcentaje <= 0:
                return None, "El porcentaje de una asignación debe ser mayor a 0."
            objetivo_id = objetivo_id_str
            if objetivo_id in ids_vistos:
                return None, "No repitas el mismo objetivo en más de una fila de asignación."
            ids_vistos.add(objetivo_id)
            asignaciones.append({"objetivo_id": objetivo_id, "porcentaje": porcentaje})
        return asignaciones, None

    for inicial in iniciales or []:
        _agregar_fila(objetivo_id=inicial["objetivo_id"], porcentaje=inicial["porcentaje"])

    contenido = ft.Column([columna_asignaciones, boton_agregar], spacing=6)
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
    editor_objetivos = construir_editor_asignaciones(page, savings_service.list_objetivos())
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
        asignaciones, error_asignaciones = editor_objetivos.resolver()
        if error_asignaciones is not None:
            _error(error_asignaciones)
            return
        if _suma_supera_100(asignaciones):
            _error("LOS OBJETIVOS SUMAN MÁS DE 100%.")
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
        for asignacion in asignaciones:
            try:
                savings_service.assign_objetivo(resultado.entity_id, asignacion["objetivo_id"], asignacion["porcentaje"])
            except (SavingsError, ValueError) as err:
                _error(f"EL ACTIVO SE CREÓ, PERO NO SE PUDO ASIGNAR UN OBJETIVO: {err}")
                return
        on_exito(resultado)

    contenido = _contenido_formulario([
        campo_nombre, dropdown_tipo, campo_broker.control, dropdown_moneda, campo_cuenta.control,
        campo_comision_compra.control, campo_comision_venta.control,
        ft.Divider(height=1),
        ft.Text("OBJETIVOS (SUMA HASTA 100%)", size=TypographyTokens.LABEL_SIZE, weight=ft.FontWeight.BOLD),
        editor_objetivos.contenido,
        texto_error,
    ])
    return FormularioCompraAhorro(contenido=contenido, confirmar=_confirmar)


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
    tipo, por_unidades, moneda, decimales, comisiones por defecto).
    """
    opciones_tipo = TIPOS_MOVIMIENTO_POR_UNIDADES if activo["por_unidades"] else TIPOS_MOVIMIENTO_POR_MONTO
    claves_tipo = [clave for clave, _ in opciones_tipo]
    decimales = activo["decimales"]
    codigo = activo["moneda"]
    moneda_ars = next((m for m in accounts_service.list_currencies() if m["codigo"] == MONEDA_DOLAR_OFICIAL_CODIGO), None)
    decimales_dolar = moneda_ars["decimales"] if moneda_ars else DECIMALES_DEFAULT

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
    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)
    refs: dict = {"vinculo": None, "mes_vinculo": None}

    def _dibujar_campos() -> None:
        tipo = dropdown_tipo.value
        if tipo in ("compra", "venta"):
            comision_default = activo["comision_compra_minor"] if tipo == "compra" else activo["comision_venta_minor"]
            refs["cantidad"] = ft.TextField(label="CANTIDAD (ENTERA)")
            refs["precio"] = CampoMonto(
                page, on_confirmar=lambda m: None, decimales=decimales, dense=False,
                label=f"PRECIO UNITARIO ({codigo})",
            )
            refs["comision"] = CampoMonto(
                page, on_confirmar=lambda m: None, decimales=decimales, dense=False,
                valor_inicial_minor=comision_default or None, label=f"COMISIÓN ({codigo}, OPCIONAL)",
            )
            controles: list[ft.Control] = [refs["cantidad"], refs["precio"].control, refs["comision"].control]
            if tipo == "compra":
                refs["dolar"] = CampoMonto(
                    page, on_confirmar=lambda m: None, decimales=decimales_dolar, dense=False,
                    label="DÓLAR DEL DÍA (ARS, OPCIONAL)",
                )
                controles.append(refs["dolar"].control)
        else:
            refs["monto"] = CampoMonto(
                page, on_confirmar=lambda m: None, decimales=decimales, dense=False, label=f"MONTO ({codigo})",
            )
            controles = [refs["monto"].control]
        contenedor_campos.controls = controles
        # Un rendimiento no se vincula a una transacción (registrar_rendimiento() no la acepta).
        contenedor_vinculo.visible = tipo != "rendimiento"
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
        try:
            if tipo in ("compra", "venta"):
                texto_cantidad = (refs["cantidad"].value or "").strip()
                if not texto_cantidad.isdigit() or int(texto_cantidad) <= 0:
                    _error("LA CANTIDAD TIENE QUE SER UN NÚMERO ENTERO MAYOR A 0.")
                    return
                precio, valido_precio = _minor_de_campo(refs["precio"], decimales)
                comision, valida_comision = _minor_de_campo(refs["comision"], decimales)
                if not valido_precio or not precio:
                    _error("INGRESÁ UN PRECIO UNITARIO VÁLIDO.")
                    return
                if not valida_comision:
                    _error("LA COMISIÓN NO ES UN NÚMERO VÁLIDO.")
                    return
                if tipo == "compra":
                    dolar, valido_dolar = _minor_de_campo(refs["dolar"], decimales_dolar)
                    if not valido_dolar:
                        _error("EL DÓLAR DEL DÍA NO ES UN NÚMERO VÁLIDO.")
                        return
                    resultado = savings_service.registrar_compra(
                        activo["activo_id"], int(texto_cantidad), precio, comision or 0, fecha,
                        transaccion_id=transaccion_id, dolar_momento_minor=dolar,
                    )
                else:
                    resultado = savings_service.registrar_venta(
                        activo["activo_id"], int(texto_cantidad), precio, comision or 0, fecha,
                        transaccion_id=transaccion_id,
                    )
            else:
                monto, valido_monto = _minor_de_campo(refs["monto"], decimales)
                if not valido_monto or not monto:
                    _error("INGRESÁ UN MONTO VÁLIDO.")
                    return
                if tipo == "aporte":
                    resultado = savings_service.registrar_aporte(
                        activo["activo_id"], monto, fecha, transaccion_id=transaccion_id,
                    )
                elif tipo == "retiro":
                    resultado = savings_service.registrar_retiro(
                        activo["activo_id"], monto, fecha, transaccion_id=transaccion_id,
                    )
                else:
                    resultado = savings_service.registrar_rendimiento(activo["activo_id"], monto, fecha)
        except (SavingsError, ValueError) as err:
            _error(str(err))
            return
        on_exito(resultado)

    contenido = _contenido_formulario([dropdown_tipo, campo_fecha, contenedor_campos, contenedor_vinculo, texto_error])
    return FormularioCompraAhorro(contenido=contenido, confirmar=_confirmar)


def construir_objetivos_activo(
    page: ft.Page,
    savings_service: SavingsService,
    activo: dict,
    on_exito: Callable[[str], None],
) -> FormularioCompraAhorro:
    """
    Reparto del activo entre objetivos (ver docstring del módulo). `activo`:
    una entrada de get_resumen_por_tipo() (activo_id, activo, objetivos).
    on_exito recibe el mensaje para mostrar.
    """
    editor = construir_editor_asignaciones(page, savings_service.list_objetivos(), iniciales=activo["objetivos"])
    texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

    def _error(mensaje: str) -> None:
        texto_error.value = mensaje.upper()
        page.update()

    def _confirmar(e=None) -> None:
        texto_error.value = ""
        asignaciones, error_asignaciones = editor.resolver()
        if error_asignaciones is not None:
            _error(error_asignaciones)
            return
        if _suma_supera_100(asignaciones):
            _error("LOS OBJETIVOS SUMAN MÁS DE 100%.")
            return
        actuales = {o["objetivo_id"]: o["porcentaje"] for o in activo["objetivos"]}
        nuevos = {a["objetivo_id"]: a["porcentaje"] for a in asignaciones}
        try:
            # Primero las bajas y las rebajas: así la suma nunca pasa de 100 a mitad de camino.
            for objetivo_id in actuales.keys() - nuevos.keys():
                savings_service.remove_objetivo(activo["activo_id"], objetivo_id)
            cambios = sorted(
                (item for item in nuevos.items() if actuales.get(item[0]) != item[1]),
                key=lambda item: item[1] - actuales.get(item[0], 0),
            )
            for objetivo_id, porcentaje in cambios:
                savings_service.assign_objetivo(activo["activo_id"], objetivo_id, porcentaje)
        except (SavingsError, ValueError) as err:
            _error(str(err))
            return
        on_exito(f"OBJETIVOS DE {activo['activo'].upper()} ACTUALIZADOS.")

    contenido = _contenido_formulario([
        ft.Text(
            f"{activo['activo'].upper()}: QUÉ PARTE ES DE CADA OBJETIVO (SUMA HASTA 100%). "
            "VALE PARA LOS MOVIMIENTOS QUE CARGUES DESDE AHORA.",
            size=TypographyTokens.LABEL_SIZE,
        ),
        editor.contenido,
        texto_error,
    ])
    return FormularioCompraAhorro(contenido=contenido, confirmar=_confirmar)
