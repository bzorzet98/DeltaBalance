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
acá por consistencia en vez de inventar un mecanismo distinto.

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

--- Resto del formulario (sin cambios de comportamiento respecto al
original de ahorros.py) ---

Cuenta de origen/categoría: mismo criterio ya establecido (cuenta siempre
opcional, categoría obligatoria SOLO si se eligió cuenta, validado en
_confirmar() en vez de togglear visibilidad reactiva — ver docstring de
ui/screens/ahorros.py para por qué). Asignaciones a objetivos:
construir_editor_asignaciones() (más abajo en este módulo) — extraído a
su propia función para que ui/screens/ahorros.py pueda reusarlo
LITERALMENTE (no una copia adaptada) en el diálogo "Egreso general"
(register_sale() con lista de asignaciones, Tarea 6d) — ver su docstring
para el detalle completo (mecanismo de filas dinámicas, validaciones,
contrato de resolver()). Errores de _confirmar() (incluida
AsignacionInvalidaError) se escriben en texto_error, un ft.Text que ya
forma parte de `contenido` — nunca SnackBar, el caller decide cómo mostrar
éxito (on_exito) pero NUNCA ve los errores de validación, ese texto ya está
en pantalla dentro del formulario.

`notas_inicial`: se pasa tal cual a register_purchase(notas=...) sin campo
propio en el formulario — el Registro lo usa para seguir mandando el
concepto tipeado en la fila (comportamiento ya existente, ver docstring de
registro_transacciones.py), ui/screens/ahorros.py simplemente no lo pasa
(None, no tiene un campo de concepto en su fila de activos).

`categoria_id` NO se precarga desde el caller (a diferencia de cuenta_id/
monto/fecha) aunque el Registro ya tenga una resuelta (la propia categoría
"Ahorro/Inversión" que disparó el routing) — al pasar a "Elegir activo
específico" el usuario pidió más control, precargar una categoría que ya
no necesariamente tiene sentido para el activo elegido habría sido más
confuso que útil; queda vacía, se elige de nuevo si hace falta (ver
resumen de la tarea).

Reglas de arquitectura: solo SavingsService/AccountsService/
CategoriasService — nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).
CampoMonto en todos los campos de monto (CLAUDE.md §9); `cantidad`/
`porcentaje` quedan TextField comunes (no son plata), mismo criterio que
ui/screens/ahorros.py.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.savings_service import SavingsError, SavingsResult, SavingsService
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.theme.tokens import TypographyTokens
from utils.money import amount_to_minor

# --- Configuración de layout ---
ANCHO_DIALOGO_COMPRA_AHORRO = 380
ANCHO_CAMPO_ASIGNACION_OBJETIVO = 200
ANCHO_CAMPO_ASIGNACION_PORCENTAJE = 90
ESPACIADO_DIALOGO = 12

TIPOS_ACTIVO = ("accion", "fci", "plazo_fijo", "cripto", "otro")
# Criterio de qué tipos tienen "unidad" real (tiene sentido pedir cantidad/
# precio_unitario al comprar) — ver docstring del módulo.
TIPOS_ACTIVO_CON_CANTIDAD = {"accion", "fci", "cripto"}

MONEDA_DOLAR_OFICIAL_CODIGO = "ARS"
DECIMALES_DEFAULT = 2

# Sentinel de "+ Crear nuevo activo" en el CampoFiltrable de activo — mismo
# criterio que _ID_OBJETIVO_NUEVO en registro_transacciones.py (nunca
# colisiona con un id real, INTEGER PRIMARY KEY siempre numérico como str).
_ID_ACTIVO_NUEVO = "__nuevo__"


def _tipo_activo_display(tipo: str) -> str:
    return {
        "accion": "Acción",
        "fci": "FCI",
        "plazo_fijo": "Plazo fijo",
        "cripto": "Cripto",
        "otro": "Otro",
    }.get(tipo, tipo)


def _cuentas_no_credito(cuentas: list[dict]) -> list[dict]:
    """Mismo filtro que ui/components/registro_transacciones.py — un ahorro no se origina desde una tarjeta de crédito."""
    return [c for c in cuentas if c["tipo"] != "credito"]


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


def construir_editor_asignaciones(page: ft.Page, objetivos_disponibles: list[dict]) -> EditorAsignaciones:
    """
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

    def _agregar_fila(e=None) -> None:
        campo_objetivo_fila = CampoFiltrable(
            page, [(str(o["id"]), o["nombre"]) for o in objetivos_disponibles],
            on_seleccionar=lambda id_: None, placeholder="Objetivo", width=ANCHO_CAMPO_ASIGNACION_OBJETIVO,
        )
        campo_porcentaje_fila = ft.TextField(hint_text="%", width=ANCHO_CAMPO_ASIGNACION_PORCENTAJE, dense=True)
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
            objetivo_id = int(objetivo_id_str)
            if objetivo_id in ids_vistos:
                return None, "No repitas el mismo objetivo en más de una fila de asignación."
            ids_vistos.add(objetivo_id)
            asignaciones.append({"objetivo_id": objetivo_id, "porcentaje": porcentaje})
        return asignaciones, None

    contenido = ft.Column([columna_asignaciones, boton_agregar], spacing=6)
    return EditorAsignaciones(contenido=contenido, resolver=_resolver)


def construir(
    page: ft.Page,
    savings_service: SavingsService,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
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
        cuenta_id_inicial:  Precarga la Cuenta de origen (CampoFiltrable).
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
    categorias = categorias_service.list_categories()
    objetivos_disponibles = savings_service.list_objetivos()
    activos_existentes = savings_service.list_activos()

    campo_fecha = ft.TextField(label="Fecha", value=fecha_inicial or date.today().isoformat())

    # ------------------------------------------------------------
    # SELECCIÓN DE ACTIVO — solo si NO viene fijo (ver docstring)
    # ------------------------------------------------------------
    campo_activo: Optional[CampoFiltrable] = None
    campo_nombre_activo_nuevo: Optional[ft.TextField] = None
    dropdown_tipo_activo_nuevo: Optional[ft.Dropdown] = None
    campo_moneda_activo_nuevo: Optional[CampoFiltrable] = None
    seccion_activo_nuevo: Optional[ft.Column] = None

    if activo_fijo is None:
        opciones_activo = [(str(a["id"]), a["nombre"]) for a in activos_existentes] + [
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
        seccion_activo_nuevo = ft.Column(
            [campo_nombre_activo_nuevo, dropdown_tipo_activo_nuevo, campo_moneda_activo_nuevo.control],
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
        activo = next((a for a in activos_existentes if a["id"] == int(campo_activo.id_seleccionado)), None)
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

    campo_cuenta = CampoFiltrable(
        page, [(str(c["id"]), c["nombre"]) for c in cuentas_activas],
        on_seleccionar=lambda id_: None, placeholder="Cuenta de origen (opcional)",
        valor_inicial_id=str(cuenta_id_inicial) if cuenta_id_inicial is not None else None,
        dense=False,
    )
    campo_categoria = CampoFiltrable(
        page, [(str(c["id"]), c["subcategoria"]) for c in categorias],
        on_seleccionar=lambda id_: None, placeholder="Categoría (solo si elegís cuenta)", dense=False,
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
            return int(campo_activo.id_seleccionado)
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

        cuenta_id = int(campo_cuenta.id_seleccionado) if campo_cuenta.id_seleccionado else None
        categoria_id = None
        if cuenta_id is not None:
            if not campo_categoria.id_seleccionado:
                texto_error.value = "Elegiste una cuenta de origen — seleccioná también una categoría."
                page.update()
                return
            categoria_id = int(campo_categoria.id_seleccionado)

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
                cuenta_id=cuenta_id,
                categoria_id=categoria_id,
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
        campo_cuenta.control,
        campo_categoria.control,
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
