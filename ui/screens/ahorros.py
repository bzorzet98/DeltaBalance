"""
DeltaBalance — ui/screens/ahorros.py

Pantalla de Ahorros — rediseño Tarea 6d (docs/PROXIMOS_PASOS.md): de
"organizada por activo" (una tarjeta por activo, tres íconos de acción cada
una) a formato Registro — dashboard resumen arriba (tres bloques) + tabla
de movimientos abajo, mismo patrón que ui/components/registro_
transacciones.py y ui/screens/compras_cuotas.py. Sección de Activos
financieros se mantiene pero se reduce a alta/listado simple (Parte E).

--- Dashboard (Parte C), tres bloques, mismo estilo visual que "Patrimonio
total" de ui/screens/dashboard.py (Row [título, Row-de-chips-wrap, acción],
alignment=SPACE_BETWEEN) ---

- "Por objetivo": una tile por objetivo (get_objetivo_balance(), saldo
  neto). Un ícono por tile expande/colapsa un panel ÚNICO debajo del
  dashboard (contenedor_detalle_objetivo, singleton — mismo patrón que
  detalle_abierto en ui/screens/compras_cuotas.py) con el desglose por
  cuenta (get_balance_por_cuenta()) del objetivo expandido. La barra de
  progreso hacia monto_meta_minor que tenía la Sección 2 de la versión
  anterior NO se trasladó a esta tile — Parte C la pide explícitamente
  compacta ("una tile por objetivo con su saldo neto"), mismo criterio
  terso que "Patrimonio total"; si se necesita ver el progreso hacia la
  meta, queda para agregarlo en una tarea aparte, no se asumió acá.
- "Por tipo de ahorro": una tile por (tipo, moneda) con
  get_balance_por_tipo() (Parte A, nuevo) — NO pasa por asignaciones,
  es el total real de movimientos_activo agrupado por tipo de activo.
- "Por activo": una tile por activo con get_balance_por_activo() (Parte A,
  nuevo) — cantidad neta (si aplica) + saldo neto, sin segregar por
  objetivo — el número que coincide con la app del broker.

--- Registro de movimientos (Parte D) ---

Necesitó un método de servicio NUEVO no pedido explícitamente en la
consigna: SavingsService.list_movimientos() — ni el repositorio
(MovimientosActivoRepository solo tiene listar_por_activo()/
listar_por_tipo(), ambos exigen un activo_id puntual) ni el service tenían
antes una forma de listar movimientos de TODOS los activos a la vez, que
es exactamente lo que esta tabla necesita. Agregado en services/
savings_service.py con su propio verify (ver verify/ahorros/
verify_savings_service.py) — ver ese método para el detalle completo
(filtros, por qué cada movimiento ya trae sus asignaciones resueltas con
nombre de objetivo).

Barra de herramientas: período navegable (selector_periodo.build(), mismo
control que el Registro de transacciones) + filtro Objetivo + filtro Tipo
de activo + filtro Tipo de movimiento + búsqueda (Enter, client-side sobre
nombre de activo/objetivo — list_movimientos() no soporta texto libre,
mismo criterio que TransaccionesRepository). NO se reusa
ui/components/barra_filtros.py: ese componente está hardcodeado a
filtros Banco/Categoría (ver su docstring), acá los filtros son
conceptualmente distintos (Objetivo/Tipo de activo/Tipo de movimiento) —
se arma un toolbar propio con la misma estructura (selector_periodo +
Dropdowns + spacer + búsqueda), no una copia del componente.

Columna "Objetivo(s)": varias asignaciones del mismo movimiento se
muestran como etiquetas apiladas (ft.Row(wrap=True) de chips) en UNA sola
fila — nunca fragmentado en filas separadas por asignación. "Sin asignar"
en gris si list_movimientos() devolvió una lista de asignaciones vacía.

Botón "+" único (Parte D, punto 3) — ft.PopupMenuButton con tres
ft.PopupMenuItem (Compra/Rendimiento/Egreso general). PopupMenuButton no
se había usado antes en este proyecto — es un control estándar y antiguo
de Flet (no de la lista de cambios de 0.80+ de docs/FLET_API_NOTES.md),
pero como cualquier patrón nuevo para este proyecto, no está confirmado
corriendo la app.

- **Compra**: dialogo_compra_ahorro.construir(activo_fijo=None) — el
  formulario completo ya unificado con el popup "Ahorro/Inversión" del
  Registro de transacciones (tarea anterior), con su propio selector de
  activo + "crear nuevo" (no hay activo de contexto acá, a diferencia del
  viejo ícono por fila). Sin pre-carga (cuenta_id_inicial/monto_inicial/
  fecha_inicial/notas_inicial todos None).
- **Rendimiento**: mismo register_return() y mismo texto informativo de
  siempre ("se reparte automático, sin selección manual") — la única
  diferencia real respecto a la versión anterior es que ahora el diálogo
  necesita su PROPIO selector de activo (CampoFiltrable, activos
  existentes, sin "crear nuevo": un rendimiento sin compras previas no
  tiene nada que repartir) porque ya no llega con un activo de contexto
  desde un ícono de fila — antes ese contexto lo daba la fila sobre la
  que se hacía click, ahora el botón "+" es único y global. El Monto se
  reconstruye (mismo mecanismo de dialogo_compra_ahorro.py) cada vez que
  cambia el activo elegido, porque sus decimales dependen de la moneda de
  ese activo.
- **Egreso general** (reemplaza el viejo ícono de "Venta" de una fila
  puntual): selector de activo (CampoFiltrable, SOLO activos con
  get_balance_por_activo().saldo_neto_minor > 0 — no tiene sentido ofrecer
  un egreso de algo sin saldo), CampoMonto (decimales de la moneda de
  referencia del activo elegido, mismo mecanismo de reconstrucción),
  fecha, y el editor de asignaciones — ver más abajo. Llama a
  register_sale() con la lista de asignaciones (Parte B). Sin campos de
  cuenta/categoría/cantidad/precio_unitario/dólar oficial — la consigna de
  esta tarea solo pidió activo+monto+fecha+asignaciones para este diálogo
  (a diferencia de Compra, que sí los tiene todos); register_sale() los
  acepta igual como opcionales si una tarea futura los agrega acá. Campo
  de moneda NO editable (fijo a la moneda de referencia del activo, texto
  informativo en el label) — pedido explícito, hasta que la Tarea 6c
  implemente moneda por movimiento.

--- Editor de asignaciones compartido (pregunta (c) de la tarea) ---

Egreso general REUSA LITERALMENTE (no una copia adaptada)
dialogo_compra_ahorro.construir_editor_asignaciones() — la misma función
que ahora usa también el formulario de Compra (extraída de ese módulo en
esta misma tarea, ver su docstring). Antes de esta extracción, el
mecanismo de filas dinámicas (Objetivo + %, agregar/quitar) vivía inline
dentro de dialogo_compra_ahorro.construir(), atado a register_purchase();
esta pantalla necesitaba el mismo mecanismo pero atado a register_sale(),
así que se sacó a una función aparte que devuelve (contenido, resolver())
— cualquiera de los dos callers arma su propio Column con `contenido` y
llama a `resolver()` al confirmar. Sin esa extracción hubiera hecho falta
duplicar ~60 líneas de lógica de filas/validación.

--- Sección de Activos financieros (Parte E) ---

Lista simple (nombre, tipo, moneda) con "+ Nuevo activo" — SIN los tres
íconos de acción por fila de la versión anterior (Compra/Rendimiento/
Venta ahora viven todos detrás del botón "+" único del Registro de
movimientos, no por activo puntual).

--- General ---

Todo campo de monto vía CampoMonto (CLAUDE.md §9), persistir_formula=False
en todos los casos (cargas de hechos puntuales, no estimaciones). objetivos_
ahorro sigue sin columna moneda_id (limitación conocida ya documentada en
tareas anteriores) — get_objetivo_balance() se sigue mostrando sin símbolo
de moneda, asumiendo DECIMALES_SIN_MONEDA_DEFAULT. get_balance_por_tipo()/
get_balance_por_activo() SÍ tienen moneda real (agrupan por moneda_id o
heredan la del activo), así que esas dos tiles muestran símbolo correcto.

Reglas de arquitectura: solo SavingsService/AccountsService/
CategoriasService — nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).
"""

from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.savings_service import SavingsError, SavingsResult, SavingsService
from ui.components import dialogo_compra_ahorro
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.components.color_chip import color_chip
from ui.components.selector_periodo import build as construir_selector_periodo
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---
ANCHO_DIALOGO = 380
ESPACIADO_FILA = 8
ESPACIADO_DIALOGO = 12
ANCHO_NOMBRE_CUENTA_DESGLOSE = 160
ESPACIADO_CHIPS_DASHBOARD = 20

# Barra de herramientas del Registro de movimientos
ANCHO_TOOLBAR_FILTRO_OBJETIVO = 160
ANCHO_TOOLBAR_FILTRO_TIPO_ACTIVO = 140
ANCHO_TOOLBAR_FILTRO_TIPO_MOVIMIENTO = 140
ANCHO_TOOLBAR_BUSQUEDA = 200

# Columnas de la tabla de movimientos
ANCHO_COL_FECHA = 100
ANCHO_COL_ACTIVO = 170
ANCHO_COL_TIPO_MOVIMIENTO = 110
ANCHO_COL_CANTIDAD = 90
ANCHO_COL_MONTO = 120
ANCHO_COL_MONEDA = 90
ANCHO_COL_OBJETIVOS = 240
DIA_TOPE_RANGO_MES = 31  # cota superior de fecha_hasta del período — comparación lexicográfica de strings ISO, no una fecha real (ver _cargar_movimientos())

TIPOS_ACTIVO = ("accion", "fci", "plazo_fijo", "cripto", "otro")
_TIPOS_MOVIMIENTO_DISPLAY = {"compra": "Compra", "venta": "Venta", "rendimiento": "Rendimiento"}
_COLOR_TIPO_MOVIMIENTO = {"venta": ft.Colors.RED, "rendimiento": ft.Colors.GREEN}  # compra: sin color (neutral)

# objetivos_ahorro no tiene columna moneda_id — ver docstring del módulo.
DECIMALES_SIN_MONEDA_DEFAULT = 2


def _tipo_activo_display(tipo: str) -> str:
    return {
        "accion": "Acción",
        "fci": "FCI",
        "plazo_fijo": "Plazo fijo",
        "cripto": "Cripto",
        "otro": "Otro",
    }.get(tipo, tipo)


def _texto_a_minor(texto: Optional[str], decimales: int) -> Optional[int]:
    """Reconvierte un texto ya confirmado (de un CampoMonto que se va a reconstruir) a minor units con decimales nuevos — mismo helper que dialogo_compra_ahorro.py."""
    if not texto:
        return None
    try:
        return amount_to_minor(float(texto.replace(",", ".")), decimales)
    except ValueError:
        return None


def build(
    page: ft.Page,
    savings_service: SavingsService,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    on_volver: Optional[Callable[[], None]] = None,
) -> ft.Control:
    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        snack = ft.SnackBar(
            content=ft.Text(mensaje),
            bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None,
        )
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _mostrar_ok(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=False)

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    hoy = date.today()
    estado = {
        "mes": hoy.month, "anio": hoy.year,
        "filtro_objetivo": None, "filtro_tipo_activo": None, "filtro_tipo_movimiento": None,
        "busqueda": "",
        # objetivo_expandido_id: qué tile de "Por objetivo" tiene el
        # desglose por cuenta abierto — estado de UI, no un filtro (mismo
        # patrón que detalle_abierto en ui/screens/compras_cuotas.py).
        "objetivo_expandido_id": None,
    }

    monedas = accounts_service.list_currencies()
    monedas_por_id = {m["id"]: m for m in monedas}
    cuentas_por_id = {c["id"]: c for c in accounts_service.list_accounts(solo_activas=False)}

    def _fmt_sin_moneda(monto_minor: int) -> str:
        """Ver docstring del módulo — get_objetivo_balance() no expone moneda."""
        return amount_display(monto_minor, DECIMALES_SIN_MONEDA_DEFAULT, "")

    contenedor_dashboard = ft.Column(spacing=ESPACIADO_FILA)
    contenedor_detalle_objetivo = ft.Container()
    contenedor_toolbar = ft.Container()
    tabla_body = ft.Column(spacing=4)
    contenedor_activos = ft.Column(spacing=ESPACIADO_FILA)

    # ------------------------------------------------------------
    # DASHBOARD (Parte C) — tres bloques + panel de detalle expandible
    # ------------------------------------------------------------

    def _tile_objetivo(objetivo: dict) -> ft.Control:
        balance = savings_service.get_objetivo_balance(objetivo["id"])
        color_saldo = ft.Colors.GREEN if balance["saldo_neto_minor"] >= 0 else ft.Colors.RED
        expandido = estado["objetivo_expandido_id"] == objetivo["id"]
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=8, vertical=4),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=6,
            content=ft.Row(
                [
                    ft.Column(
                        [
                            ft.Text(objetivo["nombre"], size=TypographyTokens.LABEL_SIZE, color=ft.Colors.OUTLINE),
                            ft.Text(_fmt_sin_moneda(balance["saldo_neto_minor"]), weight=ft.FontWeight.BOLD, color=color_saldo),
                        ],
                        spacing=2,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.EXPAND_LESS if expandido else ft.Icons.EXPAND_MORE,
                        icon_size=16,
                        tooltip="Ocultar desglose por cuenta" if expandido else "Ver desglose por cuenta",
                        on_click=lambda e, oid=objetivo["id"]: _toggle_objetivo(oid),
                    ),
                ],
                spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _tarjeta_por_objetivo() -> ft.Control:
        objetivos = savings_service.list_objetivos()
        if not objetivos:
            contenido = ft.Text(
                "Todavía no cargaste ningún objetivo de ahorro.",
                italic=True, color=ft.Colors.OUTLINE, size=TypographyTokens.TABLE_CONTENT_SIZE,
            )
        else:
            contenido = ft.Row([_tile_objetivo(o) for o in objetivos], spacing=ESPACIADO_CHIPS_DASHBOARD, wrap=True)
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Text("Por objetivo", size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT),
                    contenido,
                    ft.ElevatedButton(content=ft.Text("+ Nuevo objetivo"), icon=ft.Icons.ADD, on_click=_abrir_dialogo_nuevo_objetivo),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _tarjeta_por_tipo() -> ft.Control:
        balances = savings_service.get_balance_por_tipo()
        if not balances:
            contenido = ft.Text(
                "Sin actividad todavía.", italic=True, color=ft.Colors.OUTLINE, size=TypographyTokens.TABLE_CONTENT_SIZE,
            )
        else:
            partes = []
            for b in balances:
                moneda = monedas_por_id.get(b["moneda_id"])
                decimales = moneda["decimales"] if moneda else DECIMALES_SIN_MONEDA_DEFAULT
                simbolo = (moneda["simbolo"] if moneda else "") or ""
                color = ft.Colors.GREEN if b["saldo_neto_minor"] >= 0 else ft.Colors.RED
                partes.append(
                    ft.Row(
                        [
                            ft.Text(_tipo_activo_display(b["tipo"]), size=TypographyTokens.LABEL_SIZE, color=ft.Colors.OUTLINE),
                            ft.Text(
                                amount_display(b["saldo_neto_minor"], decimales, simbolo),
                                size=TypographyTokens.SECTION_TITLE_SIZE, weight=ft.FontWeight.BOLD, color=color,
                            ),
                        ],
                        spacing=6,
                    )
                )
            contenido = ft.Row(partes, spacing=ESPACIADO_CHIPS_DASHBOARD, wrap=True)
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Text("Por tipo de ahorro", size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT),
                    contenido,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _tarjeta_por_activo() -> ft.Control:
        balances = savings_service.get_balance_por_activo()
        activos_por_id_local = {a["id"]: a for a in savings_service.list_activos()}
        if not balances:
            contenido = ft.Text(
                "Sin actividad todavía.", italic=True, color=ft.Colors.OUTLINE, size=TypographyTokens.TABLE_CONTENT_SIZE,
            )
        else:
            partes = []
            for b in balances:
                activo = activos_por_id_local.get(b["activo_id"])
                nombre = activo["nombre"] if activo else f"Activo #{b['activo_id']}"
                moneda = monedas_por_id.get(activo["moneda_id"]) if activo else None
                decimales = moneda["decimales"] if moneda else DECIMALES_SIN_MONEDA_DEFAULT
                simbolo = (moneda["simbolo"] if moneda else "") or ""
                color = ft.Colors.GREEN if b["saldo_neto_minor"] >= 0 else ft.Colors.RED
                prefijo_cantidad = f"{b['cantidad_neta']:g} u. · " if b["cantidad_neta"] is not None else ""
                partes.append(
                    ft.Row(
                        [
                            ft.Text(nombre, size=TypographyTokens.LABEL_SIZE, color=ft.Colors.OUTLINE),
                            ft.Text(
                                f"{prefijo_cantidad}{amount_display(b['saldo_neto_minor'], decimales, simbolo)}",
                                size=TypographyTokens.SECTION_TITLE_SIZE, weight=ft.FontWeight.BOLD, color=color,
                            ),
                        ],
                        spacing=6,
                    )
                )
            contenido = ft.Row(partes, spacing=ESPACIADO_CHIPS_DASHBOARD, wrap=True)
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Text("Por activo", size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT),
                    contenido,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _panel_detalle_objetivo(objetivo: dict) -> ft.Control:
        filas = savings_service.get_balance_por_cuenta(objetivo["id"])
        if not filas:
            contenido = ft.Text(
                "Sin movimientos vinculados a una cuenta real todavía.",
                italic=True, size=TypographyTokens.LABEL_SIZE, color=ft.Colors.OUTLINE,
            )
        else:
            filas_control = []
            for f in filas:
                moneda = monedas_por_id.get(f["moneda_id"])
                decimales = moneda["decimales"] if moneda else DECIMALES_SIN_MONEDA_DEFAULT
                simbolo = (moneda["simbolo"] if moneda else "") or ""
                cuenta = cuentas_por_id.get(f["cuenta_id"])
                filas_control.append(
                    ft.Row(
                        [
                            color_chip(cuenta["color_hex"] if cuenta else None),
                            ft.Text(f["cuenta_nombre"], size=TypographyTokens.TABLE_CONTENT_SIZE, width=ANCHO_NOMBRE_CUENTA_DESGLOSE),
                            ft.Text(
                                amount_display(f["saldo_minor"], decimales, simbolo),
                                size=TypographyTokens.TABLE_CONTENT_SIZE, weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                            ),
                        ],
                        spacing=ESPACIADO_FILA,
                    )
                )
            contenido = ft.Column(filas_control, spacing=4)
        return ft.Container(
            padding=12, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=8,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Text(
                                f"Desglose por cuenta — {objetivo['nombre']}",
                                size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                            ),
                            ft.IconButton(icon=ft.Icons.CLOSE, icon_size=18, tooltip="Cerrar", on_click=lambda e: _toggle_objetivo(objetivo["id"])),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    contenido,
                ],
                spacing=8,
            ),
        )

    def _toggle_objetivo(objetivo_id: int) -> None:
        estado["objetivo_expandido_id"] = None if estado["objetivo_expandido_id"] == objetivo_id else objetivo_id
        _actualizar_dashboard()

    def _actualizar_dashboard() -> None:
        contenedor_dashboard.controls = [_tarjeta_por_objetivo(), _tarjeta_por_tipo(), _tarjeta_por_activo()]
        if estado["objetivo_expandido_id"] is not None:
            objetivo = next(
                (o for o in savings_service.list_objetivos() if o["id"] == estado["objetivo_expandido_id"]), None,
            )
            contenedor_detalle_objetivo.content = _panel_detalle_objetivo(objetivo) if objetivo is not None else None
        else:
            contenedor_detalle_objetivo.content = None
        page.update()

    # ------------------------------------------------------------
    # DIÁLOGO: Nuevo objetivo (sin cambios respecto a la versión anterior)
    # ------------------------------------------------------------

    def _abrir_dialogo_nuevo_objetivo(e=None) -> None:
        campo_nombre = ft.TextField(label="Nombre", autofocus=True)
        campo_meta = CampoMonto(
            page, on_confirmar=lambda m: None, decimales=DECIMALES_SIN_MONEDA_DEFAULT, dense=False,
            label="Meta (opcional)",
        )
        campo_fecha_meta = ft.TextField(label="Fecha meta AAAA-MM-DD (opcional)")
        texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

        def _confirmar(e=None) -> None:
            texto_error.value = ""
            nombre = (campo_nombre.value or "").strip()
            if not nombre:
                texto_error.value = "El nombre no puede estar vacío."
                page.update()
                return
            monto_meta_minor = None
            if (campo_meta.texto or "").strip():
                try:
                    monto_meta_minor = amount_to_minor(
                        float(campo_meta.texto.strip().replace(",", ".")), DECIMALES_SIN_MONEDA_DEFAULT,
                    )
                except ValueError:
                    texto_error.value = "La meta no es un número válido."
                    page.update()
                    return
            fecha_meta = (campo_fecha_meta.value or "").strip() or None
            if fecha_meta is not None:
                try:
                    datetime.strptime(fecha_meta, "%Y-%m-%d")
                except ValueError:
                    texto_error.value = "La fecha meta debe tener el formato AAAA-MM-DD."
                    page.update()
                    return
            try:
                savings_service.create_objetivo(nombre=nombre, monto_meta_minor=monto_meta_minor, fecha_meta=fecha_meta)
            except SavingsError as err:
                texto_error.value = str(err)
                page.update()
                return
            _cerrar_dialogo()
            _mostrar_ok(f"Objetivo '{nombre}' creado.")
            _refrescar()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Nuevo objetivo de ahorro"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [campo_nombre, campo_meta.control, campo_fecha_meta, texto_error],
                    tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Crear"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    # ------------------------------------------------------------
    # SECCIÓN: ACTIVOS FINANCIEROS (Parte E — simplificada)
    # ------------------------------------------------------------

    def _fila_activo(activo: dict) -> ft.Control:
        moneda = monedas_por_id.get(activo["moneda_id"])
        codigo_moneda = moneda["codigo"] if moneda else "?"
        return ft.Container(
            padding=12, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=8,
            content=ft.Column(
                [
                    ft.Text(activo["nombre"], weight=ft.FontWeight.BOLD),
                    ft.Text(
                        f"{_tipo_activo_display(activo['tipo'])} · {codigo_moneda}",
                        size=TypographyTokens.METADATA_SIZE, color=ft.Colors.OUTLINE,
                    ),
                ],
                spacing=2,
            ),
        )

    def _actualizar_activos() -> None:
        activos = savings_service.list_activos()
        if activos:
            contenedor_activos.controls = [_fila_activo(a) for a in activos]
        else:
            contenedor_activos.controls = [
                ft.Text("Todavía no cargaste ningún activo financiero.", italic=True, color=ft.Colors.OUTLINE)
            ]
        page.update()

    def _abrir_dialogo_nuevo_activo(e=None) -> None:
        campo_nombre = ft.TextField(label="Nombre", autofocus=True)
        dropdown_tipo = ft.Dropdown(
            label="Tipo",
            options=[ft.dropdown.Option(key=t, text=_tipo_activo_display(t)) for t in TIPOS_ACTIVO],
            value=TIPOS_ACTIVO[0],
        )
        campo_moneda = CampoFiltrable(
            page, [(str(m["id"]), m["codigo"]) for m in monedas],
            on_seleccionar=lambda id_: None, placeholder="Moneda", width=ANCHO_DIALOGO, dense=False,
        )
        texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

        def _confirmar(e=None) -> None:
            texto_error.value = ""
            nombre = (campo_nombre.value or "").strip()
            if not nombre:
                texto_error.value = "El nombre no puede estar vacío."
                page.update()
                return
            if not campo_moneda.id_seleccionado:
                texto_error.value = "Seleccioná una moneda de la lista de sugerencias."
                page.update()
                return
            try:
                savings_service.create_activo(
                    nombre=nombre, tipo=dropdown_tipo.value, moneda_id=int(campo_moneda.id_seleccionado),
                )
            except SavingsError as err:
                texto_error.value = str(err)
                page.update()
                return
            _cerrar_dialogo()
            _mostrar_ok(f"Activo '{nombre}' creado.")
            _refrescar()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Nuevo activo financiero"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [campo_nombre, dropdown_tipo, campo_moneda.control, texto_error],
                    tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Crear"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    # ------------------------------------------------------------
    # BOTÓN "+" — Compra / Rendimiento / Egreso general (Parte D, punto 3)
    # ------------------------------------------------------------

    def _abrir_dialogo_compra_menu(e=None) -> None:
        def _on_exito(resultado: SavingsResult) -> None:
            _cerrar_dialogo()
            _mostrar_ok(f"Compra registrada (movimiento #{resultado.entity_id}).")
            _refrescar()

        # activo_fijo=None: el formulario muestra su propio selector de
        # activo + "crear nuevo" — acá no hay ninguna fila de contexto que
        # ya traiga un activo elegido (a diferencia de la versión anterior
        # de esta pantalla). Sin pre-carga, pedido explícito.
        formulario = dialogo_compra_ahorro.construir(
            page, savings_service, accounts_service, categorias_service, on_exito=_on_exito,
        )
        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Compra"),
            content=formulario.contenido,
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Confirmar"), on_click=lambda e: formulario.confirmar()),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _abrir_dialogo_rendimiento_menu(e=None) -> None:
        activos_lista = savings_service.list_activos()
        if not activos_lista:
            _mostrar_ok("Todavía no cargaste ningún activo financiero.")
            return

        ref_monto: dict = {"campo": None}
        contenedor_monto = ft.Column(spacing=ESPACIADO_DIALOGO)

        def _reconstruir_monto() -> None:
            activo = None
            if campo_activo.id_seleccionado:
                activo = next((a for a in activos_lista if a["id"] == int(campo_activo.id_seleccionado)), None)
            moneda = monedas_por_id.get(activo["moneda_id"]) if activo else None
            decimales = moneda["decimales"] if moneda else DECIMALES_SIN_MONEDA_DEFAULT
            codigo = moneda["codigo"] if moneda else ""
            texto_previo = ref_monto["campo"].texto if ref_monto["campo"] is not None else None
            campo_monto = CampoMonto(
                page, on_confirmar=lambda m: None, decimales=decimales, dense=False,
                valor_inicial_minor=_texto_a_minor(texto_previo, decimales),
                label="Monto del rendimiento" + (f" ({codigo})" if codigo else ""),
            )
            ref_monto["campo"] = campo_monto
            contenedor_monto.controls = [campo_monto.control]
            page.update()

        campo_activo = CampoFiltrable(
            page, [(str(a["id"]), a["nombre"]) for a in activos_lista],
            on_seleccionar=lambda id_: _reconstruir_monto(), placeholder="Activo", dense=False, autofocus=True,
        )
        campo_fecha = ft.TextField(label="Fecha", value=date.today().isoformat())
        _reconstruir_monto()

        texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

        def _confirmar(e=None) -> None:
            texto_error.value = ""
            if not campo_activo.id_seleccionado:
                texto_error.value = "Seleccioná el activo del rendimiento."
                page.update()
                return
            activo_id = int(campo_activo.id_seleccionado)
            activo = next((a for a in activos_lista if a["id"] == activo_id), None)
            moneda = monedas_por_id.get(activo["moneda_id"]) if activo else None
            decimales = moneda["decimales"] if moneda else DECIMALES_SIN_MONEDA_DEFAULT
            try:
                datetime.strptime((campo_fecha.value or "").strip(), "%Y-%m-%d")
            except ValueError:
                texto_error.value = "La fecha debe tener el formato AAAA-MM-DD."
                page.update()
                return
            try:
                monto = float((ref_monto["campo"].texto or "").strip().replace(",", "."))
            except ValueError:
                texto_error.value = "El monto no es un número válido."
                page.update()
                return
            if monto <= 0:
                texto_error.value = "El monto debe ser mayor a 0."
                page.update()
                return
            try:
                resultado = savings_service.register_return(
                    activo_id=activo_id, fecha=campo_fecha.value.strip(),
                    monto_total_minor=amount_to_minor(monto, decimales),
                )
            except (SavingsError, ValueError) as err:
                texto_error.value = str(err)
                page.update()
                return
            _cerrar_dialogo()
            aviso = (
                " (repartido entre los objetivos ya asignados)"
                if resultado.data.get("repartido")
                else " (sin repartir: este activo todavía no tiene asignaciones)"
            )
            _mostrar_ok(f"Rendimiento registrado{aviso}.")
            _refrescar()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Rendimiento"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [
                        ft.Text(
                            "El rendimiento se reparte automáticamente entre los objetivos "
                            "ya asignados a este activo, en proporción a lo invertido — no se elige a mano.",
                            size=TypographyTokens.LABEL_SIZE, color=ft.Colors.OUTLINE,
                        ),
                        campo_activo.control,
                        campo_fecha,
                        contenedor_monto,
                        texto_error,
                    ],
                    tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Confirmar"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _abrir_dialogo_egreso_general(e=None) -> None:
        balances_por_activo = {b["activo_id"]: b for b in savings_service.get_balance_por_activo()}
        activos_con_saldo = [
            a for a in savings_service.list_activos()
            if balances_por_activo.get(a["id"], {}).get("saldo_neto_minor", 0) > 0
        ]
        if not activos_con_saldo:
            _mostrar_ok("No hay ningún activo con saldo positivo del que registrar un egreso todavía.")
            return

        objetivos_disponibles = savings_service.list_objetivos()
        ref_monto: dict = {"campo": None}
        contenedor_monto = ft.Column(spacing=ESPACIADO_DIALOGO)

        def _activo_seleccionado() -> Optional[dict]:
            if not campo_activo.id_seleccionado:
                return None
            return next((a for a in activos_con_saldo if a["id"] == int(campo_activo.id_seleccionado)), None)

        def _reconstruir_monto() -> None:
            activo = _activo_seleccionado()
            moneda = monedas_por_id.get(activo["moneda_id"]) if activo else None
            decimales = moneda["decimales"] if moneda else DECIMALES_SIN_MONEDA_DEFAULT
            codigo = moneda["codigo"] if moneda else ""
            texto_previo = ref_monto["campo"].texto if ref_monto["campo"] is not None else None
            # Moneda NO editable — fija a la de referencia del activo
            # elegido, pedido explícito hasta la Tarea 6c (ver docstring
            # del módulo).
            campo_monto = CampoMonto(
                page, on_confirmar=lambda m: None, decimales=decimales, dense=False,
                valor_inicial_minor=_texto_a_minor(texto_previo, decimales),
                label="Monto del egreso" + (f" ({codigo}, moneda del activo)" if codigo else ""),
            )
            ref_monto["campo"] = campo_monto
            contenedor_monto.controls = [campo_monto.control]
            page.update()

        campo_activo = CampoFiltrable(
            page, [(str(a["id"]), a["nombre"]) for a in activos_con_saldo],
            on_seleccionar=lambda id_: _reconstruir_monto(), placeholder="Activo (con saldo positivo)",
            dense=False, autofocus=True,
        )
        campo_fecha = ft.TextField(label="Fecha", value=date.today().isoformat())
        _reconstruir_monto()

        editor_asignaciones = dialogo_compra_ahorro.construir_editor_asignaciones(page, objetivos_disponibles)
        texto_error = ft.Text("", color=ft.Colors.ERROR, size=TypographyTokens.LABEL_SIZE)

        def _confirmar(e=None) -> None:
            texto_error.value = ""
            activo = _activo_seleccionado()
            if activo is None:
                texto_error.value = "Seleccioná el activo del que sale este egreso."
                page.update()
                return
            try:
                datetime.strptime((campo_fecha.value or "").strip(), "%Y-%m-%d")
            except ValueError:
                texto_error.value = "La fecha debe tener el formato AAAA-MM-DD."
                page.update()
                return
            moneda = monedas_por_id.get(activo["moneda_id"])
            decimales = moneda["decimales"] if moneda else DECIMALES_SIN_MONEDA_DEFAULT
            try:
                monto = float((ref_monto["campo"].texto or "").strip().replace(",", "."))
            except ValueError:
                texto_error.value = "El monto no es un número válido."
                page.update()
                return
            if monto <= 0:
                texto_error.value = "El monto debe ser mayor a 0."
                page.update()
                return

            asignaciones, error_asignaciones = editor_asignaciones.resolver()
            if error_asignaciones is not None:
                texto_error.value = error_asignaciones
                page.update()
                return
            if not asignaciones:
                texto_error.value = "Agregá al menos una fila de objetivo — un egreso siempre sale de algún objetivo."
                page.update()
                return

            try:
                resultado = savings_service.register_sale(
                    activo_id=activo["id"], fecha=campo_fecha.value.strip(),
                    monto_total_minor=amount_to_minor(monto, decimales),
                    asignaciones=asignaciones,
                )
            except (SavingsError, ValueError) as err:
                texto_error.value = str(err)
                page.update()
                return
            _cerrar_dialogo()
            _mostrar_ok(f"Egreso registrado para '{activo['nombre']}' (movimiento #{resultado.entity_id}).")
            _refrescar()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Egreso general"),
            content=ft.Container(
                width=ANCHO_DIALOGO,
                content=ft.Column(
                    [
                        campo_activo.control,
                        campo_fecha,
                        contenedor_monto,
                        ft.Divider(height=1),
                        ft.Text("Asignación a objetivos", size=TypographyTokens.LABEL_SIZE, weight=ft.FontWeight.BOLD),
                        editor_asignaciones.contenido,
                        texto_error,
                    ],
                    tight=True, spacing=ESPACIADO_DIALOGO, scroll=ft.ScrollMode.AUTO,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Confirmar"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    boton_agregar_movimiento = ft.PopupMenuButton(
        icon=ft.Icons.ADD,
        tooltip="Agregar movimiento",
        items=[
            ft.PopupMenuItem(content="Compra", icon=ft.Icons.ADD_SHOPPING_CART, on_click=_abrir_dialogo_compra_menu),
            ft.PopupMenuItem(content="Rendimiento", icon=ft.Icons.TRENDING_UP, on_click=_abrir_dialogo_rendimiento_menu),
            ft.PopupMenuItem(content="Egreso general", icon=ft.Icons.REMOVE_SHOPPING_CART, on_click=_abrir_dialogo_egreso_general),
        ],
    )

    # ------------------------------------------------------------
    # REGISTRO DE MOVIMIENTOS (Parte D) — toolbar + tabla
    # ------------------------------------------------------------

    def _construir_toolbar() -> ft.Control:
        control_periodo = construir_selector_periodo(estado, _refrescar, text_size=TypographyTokens.FILTER_SIZE)

        def _on_select_filtro_objetivo(e: ft.ControlEvent) -> None:
            valor = dropdown_filtro_objetivo.value
            estado["filtro_objetivo"] = int(valor) if valor else None
            _refrescar()

        def _on_select_filtro_tipo_activo(e: ft.ControlEvent) -> None:
            estado["filtro_tipo_activo"] = dropdown_filtro_tipo_activo.value or None
            _refrescar()

        def _on_select_filtro_tipo_movimiento(e: ft.ControlEvent) -> None:
            estado["filtro_tipo_movimiento"] = dropdown_filtro_tipo_movimiento.value or None
            _refrescar()

        def _on_submit_busqueda(e: ft.ControlEvent) -> None:
            estado["busqueda"] = campo_busqueda.value or ""
            _refrescar()

        dropdown_filtro_objetivo = ft.Dropdown(
            label="Objetivo", width=ANCHO_TOOLBAR_FILTRO_OBJETIVO, dense=True, text_size=TypographyTokens.FILTER_SIZE,
            options=[ft.dropdown.Option(key="", text="Todos")] + [
                ft.dropdown.Option(key=str(o["id"]), text=o["nombre"]) for o in savings_service.list_objetivos()
            ],
            value=str(estado["filtro_objetivo"]) if estado["filtro_objetivo"] is not None else "",
            on_select=_on_select_filtro_objetivo,
        )
        dropdown_filtro_tipo_activo = ft.Dropdown(
            label="Tipo de activo", width=ANCHO_TOOLBAR_FILTRO_TIPO_ACTIVO, dense=True, text_size=TypographyTokens.FILTER_SIZE,
            options=[ft.dropdown.Option(key="", text="Todos")] + [
                ft.dropdown.Option(key=t, text=_tipo_activo_display(t)) for t in TIPOS_ACTIVO
            ],
            value=estado["filtro_tipo_activo"] or "",
            on_select=_on_select_filtro_tipo_activo,
        )
        dropdown_filtro_tipo_movimiento = ft.Dropdown(
            label="Movimiento", width=ANCHO_TOOLBAR_FILTRO_TIPO_MOVIMIENTO, dense=True, text_size=TypographyTokens.FILTER_SIZE,
            options=[ft.dropdown.Option(key="", text="Todos")] + [
                ft.dropdown.Option(key=t, text=d) for t, d in _TIPOS_MOVIMIENTO_DISPLAY.items()
            ],
            value=estado["filtro_tipo_movimiento"] or "",
            on_select=_on_select_filtro_tipo_movimiento,
        )
        campo_busqueda = ft.TextField(
            width=ANCHO_TOOLBAR_BUSQUEDA, label="Buscar", hint_text="Activo/objetivo... (Enter)", dense=True,
            text_size=TypographyTokens.FILTER_SIZE, prefix_icon=ft.Icons.SEARCH,
            value=estado["busqueda"], on_submit=_on_submit_busqueda,
        )

        return ft.Row(
            [
                control_periodo, dropdown_filtro_objetivo, dropdown_filtro_tipo_activo,
                dropdown_filtro_tipo_movimiento, ft.Container(expand=True), campo_busqueda,
            ],
            spacing=ESPACIADO_FILA,
        )

    def _texto_celda(texto: str, color: Optional[str] = None, weight=None) -> ft.Text:
        return ft.Text(
            texto, color=color, weight=weight or TypographyTokens.TABLE_CONTENT_WEIGHT_REGULAR,
            size=TypographyTokens.TABLE_CONTENT_SIZE, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=texto,
        )

    def _fila_movimiento(m: dict) -> ft.Control:
        color_tipo = _COLOR_TIPO_MOVIMIENTO.get(m["tipo"])
        texto_tipo = _TIPOS_MOVIMIENTO_DISPLAY.get(m["tipo"], m["tipo"])
        moneda = monedas_por_id.get(m["moneda_id"])
        decimales = moneda["decimales"] if moneda else DECIMALES_SIN_MONEDA_DEFAULT
        simbolo = (moneda["simbolo"] if moneda else "") or ""
        codigo_moneda = moneda["codigo"] if moneda else "?"
        texto_cantidad = f"{m['cantidad']:g}" if m["cantidad"] is not None else "—"

        if m["asignaciones"]:
            contenido_objetivos: ft.Control = ft.Row(
                [
                    ft.Container(
                        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
                        bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST, border_radius=10,
                        content=ft.Text(a["objetivo_nombre"], size=TypographyTokens.LABEL_SIZE),
                    )
                    for a in m["asignaciones"]
                ],
                spacing=4, wrap=True,
            )
        else:
            contenido_objetivos = ft.Text("Sin asignar", size=TypographyTokens.LABEL_SIZE, italic=True, color=ft.Colors.OUTLINE)

        return ft.Row(
            [
                ft.Container(width=ANCHO_COL_FECHA, padding=4, content=_texto_celda(m["fecha"])),
                ft.Container(width=ANCHO_COL_ACTIVO, padding=4, content=_texto_celda(m["activo_nombre"])),
                ft.Container(
                    width=ANCHO_COL_TIPO_MOVIMIENTO, padding=4,
                    content=_texto_celda(texto_tipo, color=color_tipo, weight=TypographyTokens.TABLE_CONTENT_WEIGHT),
                ),
                ft.Container(width=ANCHO_COL_CANTIDAD, padding=4, content=_texto_celda(texto_cantidad)),
                ft.Container(
                    width=ANCHO_COL_MONTO, padding=4,
                    content=_texto_celda(
                        amount_display(m["monto_total_minor"], decimales, simbolo), weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                    ),
                ),
                ft.Container(width=ANCHO_COL_MONEDA, padding=4, content=_texto_celda(codigo_moneda)),
                ft.Container(width=ANCHO_COL_OBJETIVOS, padding=4, content=contenido_objetivos),
            ],
            spacing=ESPACIADO_FILA,
        )

    def _cargar_movimientos() -> list[dict]:
        fecha_desde = f"{estado['anio']:04d}-{estado['mes']:02d}-01"
        fecha_hasta = f"{estado['anio']:04d}-{estado['mes']:02d}-{DIA_TOPE_RANGO_MES}"
        movimientos = savings_service.list_movimientos(
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            objetivo_id=estado["filtro_objetivo"], tipo_activo=estado["filtro_tipo_activo"],
            tipo_movimiento=estado["filtro_tipo_movimiento"],
        )
        texto_busqueda = (estado["busqueda"] or "").strip().lower()
        if texto_busqueda:
            def _coincide(m: dict) -> bool:
                if texto_busqueda in m["activo_nombre"].lower():
                    return True
                return any(texto_busqueda in a["objetivo_nombre"].lower() for a in m["asignaciones"])
            movimientos = [m for m in movimientos if _coincide(m)]
        return movimientos

    def _actualizar_tabla() -> None:
        movimientos = _cargar_movimientos()
        if movimientos:
            filas: list[ft.Control] = []
            for i, m in enumerate(movimientos):
                if i > 0:
                    filas.append(ft.Divider(height=1))
                filas.append(_fila_movimiento(m))
            tabla_body.controls = filas
        else:
            tabla_body.controls = [
                ft.Text("No hay movimientos para este período/filtro.", italic=True, color=ft.Colors.OUTLINE)
            ]
        page.update()

    def _header(texto: str, width: int) -> ft.Text:
        return ft.Text(texto, size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT, width=width)

    encabezado_columnas = ft.Row(
        [
            _header("Fecha", ANCHO_COL_FECHA),
            _header("Activo", ANCHO_COL_ACTIVO),
            _header("Tipo", ANCHO_COL_TIPO_MOVIMIENTO),
            _header("Cantidad", ANCHO_COL_CANTIDAD),
            _header("Monto", ANCHO_COL_MONTO),
            _header("Moneda", ANCHO_COL_MONEDA),
            _header("Objetivo(s)", ANCHO_COL_OBJETIVOS),
        ],
        spacing=ESPACIADO_FILA,
    )

    # ------------------------------------------------------------
    # REFRESCO + LAYOUT GENERAL
    # ------------------------------------------------------------

    def _refrescar() -> None:
        _actualizar_dashboard()
        _actualizar_activos()
        contenedor_toolbar.content = _construir_toolbar()
        _actualizar_tabla()
        page.update()

    _refrescar()

    fila_titulo = [ft.Text("Ahorros", size=TypographyTokens.PAGE_TITLE_SIZE, weight=TypographyTokens.PAGE_TITLE_WEIGHT)]
    if on_volver is not None:
        fila_titulo.insert(
            0, ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Volver", on_click=lambda e: on_volver()),
        )

    return ft.Column(
        [
            ft.Row(fila_titulo, alignment=ft.MainAxisAlignment.START),
            ft.Container(height=8),
            contenedor_dashboard,
            contenedor_detalle_objetivo,
            ft.Container(height=16),
            ft.Container(
                padding=16, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=8,
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(
                                    "Registro de movimientos",
                                    size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                                ),
                                boton_agregar_movimiento,
                            ],
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        ),
                        contenedor_toolbar,
                        ft.Divider(height=1),
                        encabezado_columnas,
                        ft.Divider(height=1),
                        tabla_body,
                    ],
                    spacing=4,
                ),
            ),
            ft.Container(height=16),
            ft.Row(
                [
                    ft.Text("Activos financieros", size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT),
                    ft.ElevatedButton(content=ft.Text("+ Nuevo activo"), icon=ft.Icons.ADD, on_click=_abrir_dialogo_nuevo_activo),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            contenedor_activos,
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
