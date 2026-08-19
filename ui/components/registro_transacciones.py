"""
DeltaBalance — ui/components/registro_transacciones.py

Componente de "Registro de transacciones" estilo planilla: barra de
herramientas (mes/año, modo Todos-del-mes/Últimos-N, filtro por banco) +
fila de alta fija (campos en línea, navegables por teclado) + tabla de
movimientos con edición inline celda por celda. Vive en ui/components/
porque ui/screens/compras_cuotas.py va a reusar este mismo patrón visual
de fila de tabla más adelante (tarea aparte, todavía no aplicada ahí — ver
CLAUDE.md §8 para la regla de layout que este archivo sigue).

Reglas de arquitectura: solo AccountsService/CategoriasService/
TransactionService — nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).

Limitación real descubierta al revisar TransactionService.update() (no
asumida): NO acepta account_id ni movement_type como parámetros — hoy no
hay forma de reasignar la cuenta de una transacción ya cargada, ni de
convertir un gasto en ingreso, vía el service tal como está. Por eso:
- La columna "Banco" es de solo lectura (chip + nombre) en la tabla, no
  editable inline como las demás.
- La celda de Monto edita el valor absoluto pero nunca cambia
  gasto↔ingreso — para eso hay que cargar un movimiento nuevo desde la
  fila de alta (que sí acepta signo).
- amount y currency_code viajan SIEMPRE juntos si se toca cualquiera de
  los dos (exigencia propia de TransactionService.update()).

Categoría y Cuenta (fila de alta) usan ft.Dropdown(enable_filter=True,
editable=True) — confirmado por docs.flet.dev (ver docs/FLET_API_NOTES.md).
OJO con el bug conocido documentado ahí (issue #5338 de flet-dev/flet):
seleccionar una opción con teclado (flechas + Enter) puede no disparar
on_select ni actualizar .value, aunque el texto visible cambie — la
selección con click SÍ confirma. No lo pude probar acá (no hay forma de
ejecutar la app desde este entorno), así que el flujo de teclado en esos
dos campos específicos queda como mejora "best effort" (se intenta
encadenar el foco igual, por si el bug no aplica a este caso puntual) y NO
como camino confirmado — click sigue siendo el camino principal. El resto
de la fila (TextField simples: Concepto/Monto/Fecha) no tiene ese problema,
on_submit ahí es un evento estándar de TextField sin cambios documentados.

Refresco: cada acción que cambia datos (alta, edición de celda, cambio de
mes/año/modo/cantidad/filtro banco en la barra de herramientas) llama a
on_cambio(), que el dashboard usa para reconstruir toda la pantalla
(patrimonio + este registro + gráfico) — no hay refresco parcial local acá
adentro, todo pasa por ese único mecanismo ya existente.

Diálogos/SnackBar/botones: mismas convenciones de Flet 0.86.5 que el resto
de ui/ — ver docs/FLET_API_NOTES.md. Este componente no abre AlertDialog
(la edición es inline), así que solo aplica el patrón de SnackBar acá.
"""

from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.transaction_service import TransactionService, TransactionError
from ui.components.color_chip import color_chip
from utils.money import amount_display

# --- Configuración de layout ---
# Anchos generosos a propósito: Concepto y Categoría son los textos más
# largos (texto libre y "categoria_principal · subcategoria" en la edición
# respectivamente) — lo que no entra se trunca con ellipsis + tooltip (ver
# _texto_celda()) en vez de romper el alineado de la fila.
ANCHO_COL_CONCEPTO = 220
ANCHO_COL_BANCO = 160
ANCHO_COL_CATEGORIA = 220
ANCHO_COL_MONTO = 120
ANCHO_COL_FECHA = 110
ANCHO_COL_MONEDA = 80
ANCHO_BOTON_CONFIRMAR = 48
ANCHO_TOOLBAR_MES = 150
ANCHO_TOOLBAR_ANIO = 100
ANCHO_TOOLBAR_MODO = 170
ANCHO_TOOLBAR_CANTIDAD = 90
ANCHO_TOOLBAR_FILTRO_BANCO = 170
ESPACIADO_FILA = 8
ANIOS_HACIA_ATRAS = 3
OPCIONES_CANTIDAD_ULTIMOS = (10, 20, 50)
CANTIDAD_ULTIMOS_DEFAULT = 20
LIMITE_TRANSACCIONES_DEL_MES = 500  # tope de per_page al pedir "todos los del mes"

_MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    transaction_service: TransactionService,
    estado: dict,
    on_cambio: Callable[[], None],
) -> ft.Control:
    """
    Args:
        estado:    Dict MUTABLE del caller (el `estado` del dashboard,
                   compartido con _seccion_gasto()) — {"mes", "anio",
                   "modo": "mes"|"ultimos", "cantidad", "filtro_banco":
                   int|None}. Se completa con setdefault() la primera vez.
                   Mes/año viven acá (se movieron desde arriba del
                   dashboard a esta barra de herramientas) pero siguen
                   siendo el mismo período que usa el gráfico de gasto de
                   más abajo — por eso cambiar mes/año acá dispara
                   on_cambio() (reconstruye TODO el dashboard), no un
                   refresco local.
        on_cambio: Callback tras cualquier cambio (alta, edición de celda,
                   o cualquier control de la barra de herramientas). El
                   dashboard reconstruye la pantalla entera con esto.
    """
    hoy = date.today()
    estado.setdefault("mes", hoy.month)
    estado.setdefault("anio", hoy.year)
    estado.setdefault("modo", "mes")
    estado.setdefault("cantidad", CANTIDAD_ULTIMOS_DEFAULT)
    estado.setdefault("filtro_banco", None)

    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        snack = ft.SnackBar(
            content=ft.Text(mensaje),
            bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None,
        )
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _mostrar_error(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=False)

    def _texto_celda(texto: str, color: Optional[str] = None, weight=None) -> ft.Text:
        """Texto de celda: 1 línea, ellipsis si no entra, tooltip con el valor completo."""
        return ft.Text(
            texto,
            color=color,
            weight=weight,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=texto,
        )

    cuentas_activas = accounts_service.list_accounts(solo_activas=True)
    cuentas_todas = accounts_service.list_accounts(solo_activas=False)
    cuentas_por_id = {c["id"]: c for c in cuentas_todas}
    categorias = [c for c in categorias_service.list_categories() if c["tipo"] in ("ingreso", "egreso")]

    # ------------------------------------------------------------
    # BARRA DE HERRAMIENTAS: mes/año, modo, cantidad, filtro por banco
    # ------------------------------------------------------------

    def _on_select_mes(e: ft.ControlEvent) -> None:
        estado["mes"] = int(dropdown_mes.value)
        on_cambio()

    def _on_select_anio(e: ft.ControlEvent) -> None:
        estado["anio"] = int(dropdown_anio.value)
        on_cambio()

    def _on_select_modo(e: ft.ControlEvent) -> None:
        estado["modo"] = dropdown_modo.value
        on_cambio()

    def _on_select_cantidad(e: ft.ControlEvent) -> None:
        estado["cantidad"] = int(dropdown_cantidad.value)
        on_cambio()

    def _on_select_filtro_banco(e: ft.ControlEvent) -> None:
        valor = dropdown_filtro_banco.value
        estado["filtro_banco"] = int(valor) if valor else None
        on_cambio()

    dropdown_mes = ft.Dropdown(
        label="Mes",
        width=ANCHO_TOOLBAR_MES,
        dense=True,
        options=[ft.dropdown.Option(key=str(i + 1), text=nombre) for i, nombre in enumerate(_MESES)],
        value=str(estado["mes"]),
        on_select=_on_select_mes,
    )
    anios_disponibles = list(range(hoy.year - ANIOS_HACIA_ATRAS, hoy.year + 1))
    dropdown_anio = ft.Dropdown(
        label="Año",
        width=ANCHO_TOOLBAR_ANIO,
        dense=True,
        options=[ft.dropdown.Option(key=str(a), text=str(a)) for a in reversed(anios_disponibles)],
        value=str(estado["anio"]),
        on_select=_on_select_anio,
    )
    dropdown_modo = ft.Dropdown(
        width=ANCHO_TOOLBAR_MODO,
        dense=True,
        options=[
            ft.dropdown.Option(key="mes", text="Todos los del mes"),
            ft.dropdown.Option(key="ultimos", text="Últimos N"),
        ],
        value=estado["modo"],
        on_select=_on_select_modo,
    )
    dropdown_cantidad = ft.Dropdown(
        width=ANCHO_TOOLBAR_CANTIDAD,
        dense=True,
        options=[ft.dropdown.Option(key=str(n), text=str(n)) for n in OPCIONES_CANTIDAD_ULTIMOS],
        value=str(estado["cantidad"]),
        visible=estado["modo"] == "ultimos",
        on_select=_on_select_cantidad,
    )
    dropdown_filtro_banco = ft.Dropdown(
        label="Banco",
        width=ANCHO_TOOLBAR_FILTRO_BANCO,
        dense=True,
        options=[ft.dropdown.Option(key="", text="Todos")] + [
            ft.dropdown.Option(key=str(c["id"]), text=c["nombre"]) for c in cuentas_todas
        ],
        value=str(estado["filtro_banco"]) if estado["filtro_banco"] is not None else "",
        on_select=_on_select_filtro_banco,
    )

    barra_herramientas = ft.Row(
        [dropdown_mes, dropdown_anio, dropdown_modo, dropdown_cantidad, dropdown_filtro_banco],
        spacing=ESPACIADO_FILA,
        wrap=True,
    )

    # ------------------------------------------------------------
    # FILA DE ALTA (campos en línea, no diálogo)
    # ------------------------------------------------------------

    def _refrescar_moneda_alta(cuenta_id: int) -> None:
        saldos = accounts_service.get_account(cuenta_id)["saldos"]
        dropdown_moneda_alta.options = [
            ft.dropdown.Option(key=s["moneda_codigo"], text=s["moneda_codigo"]) for s in saldos
        ]
        dropdown_moneda_alta.value = saldos[0]["moneda_codigo"] if saldos else None

    def _on_select_cuenta_alta(e: ft.ControlEvent) -> None:
        _refrescar_moneda_alta(int(dropdown_cuenta_alta.value))
        page.update()

    def _confirmar_alta(e: Optional[ft.ControlEvent] = None) -> None:
        if not cuentas_activas:
            _mostrar_error("Primero cargá una cuenta en Configuración → Cuentas.")
            return
        if not categorias:
            _mostrar_error("No hay categorías cargadas.")
            return
        if not campo_concepto_alta.value or not campo_concepto_alta.value.strip():
            _mostrar_error("El concepto no puede estar vacío.")
            return
        try:
            monto_con_signo = float((campo_monto_alta.value or "").strip().replace(",", "."))
        except ValueError:
            _mostrar_error("El monto no es un número válido.")
            return
        if monto_con_signo == 0:
            _mostrar_error("El monto no puede ser 0 — negativo es gasto, positivo es ingreso.")
            return
        try:
            datetime.strptime((campo_fecha_alta.value or "").strip(), "%Y-%m-%d")
        except ValueError:
            _mostrar_error("La fecha debe tener el formato AAAA-MM-DD.")
            return
        if not dropdown_cuenta_alta.value or not dropdown_categoria_alta.value or not dropdown_moneda_alta.value:
            _mostrar_error("Completá cuenta, categoría y moneda.")
            return

        try:
            resultado = transaction_service.create(
                date_str=campo_fecha_alta.value.strip(),
                concept=campo_concepto_alta.value.strip(),
                account_id=int(dropdown_cuenta_alta.value),
                category_id=int(dropdown_categoria_alta.value),
                currency_code=dropdown_moneda_alta.value,
                amount=abs(monto_con_signo),
                movement_type="egreso" if monto_con_signo < 0 else "ingreso",
            )
        except (TransactionError, ValueError) as err:
            _mostrar_error(str(err))
            return

        _mostrar_ok(f"Movimiento #{resultado.transaction_id} registrado.")
        on_cambio()

    campo_concepto_alta = ft.TextField(width=ANCHO_COL_CONCEPTO, label="Concepto", dense=True)
    dropdown_cuenta_alta = ft.Dropdown(
        width=ANCHO_COL_BANCO,
        label="Banco",
        dense=True,
        enable_filter=True,
        editable=True,
        options=[ft.dropdown.Option(key=str(c["id"]), text=c["nombre"]) for c in cuentas_activas],
        value=str(cuentas_activas[0]["id"]) if cuentas_activas else None,
        on_select=_on_select_cuenta_alta,
    )
    dropdown_categoria_alta = ft.Dropdown(
        width=ANCHO_COL_CATEGORIA,
        label="Categoría",
        dense=True,
        enable_filter=True,
        editable=True,
        options=[
            ft.dropdown.Option(key=str(c["id"]), text=f"{c['categoria_principal']} · {c['subcategoria']}")
            for c in categorias
        ],
        value=str(categorias[0]["id"]) if categorias else None,
    )
    campo_monto_alta = ft.TextField(width=ANCHO_COL_MONTO, label="Monto (± )", dense=True)
    campo_fecha_alta = ft.TextField(width=ANCHO_COL_FECHA, label="Fecha", dense=True, value=hoy.isoformat())
    dropdown_moneda_alta = ft.Dropdown(width=ANCHO_COL_MONEDA, label="Moneda", dense=True, options=[])
    if cuentas_activas:
        _refrescar_moneda_alta(cuentas_activas[0]["id"])
    boton_confirmar_alta = ft.IconButton(
        icon=ft.Icons.CHECK_CIRCLE,
        icon_color=ft.Colors.PRIMARY,
        tooltip="Agregar movimiento",
        on_click=_confirmar_alta,
    )

    # Encadenado de foco por teclado (Enter avanza al siguiente campo; el
    # último dispara el mismo guardado que el botón). Se asigna acá, con
    # todos los campos ya construidos, para no depender del orden de
    # definición. Concepto/Monto/Fecha son TextField simples (on_submit
    # estándar, sin cambios documentados). Banco/Categoría son Dropdown
    # enable_filter+editable — encadenar su on_submit es "best effort": el
    # bug conocido de selección por teclado (ver docstring del módulo)
    # podría hacer que Enter no confirme una opción ahí, sin forma de
    # probarlo desde acá. Tab nativo (orden de foco de Flutter, no de este
    # código) debería cubrir la navegación igual aunque ese punto falle —
    # sin confirmar corriendo la app.
    campo_concepto_alta.on_submit = lambda e: dropdown_cuenta_alta.focus()
    dropdown_cuenta_alta.on_submit = lambda e: dropdown_categoria_alta.focus()
    dropdown_categoria_alta.on_submit = lambda e: campo_monto_alta.focus()
    campo_monto_alta.on_submit = lambda e: campo_fecha_alta.focus()
    campo_fecha_alta.on_submit = lambda e: dropdown_moneda_alta.focus()
    dropdown_moneda_alta.on_submit = _confirmar_alta

    fila_alta = ft.Row(
        [
            campo_concepto_alta,
            dropdown_cuenta_alta,
            dropdown_categoria_alta,
            campo_monto_alta,
            campo_fecha_alta,
            dropdown_moneda_alta,
            boton_confirmar_alta,
        ],
        spacing=ESPACIADO_FILA,
        wrap=True,
    )

    # ------------------------------------------------------------
    # CELDAS EDITABLES GENÉRICAS (texto y dropdown)
    # ------------------------------------------------------------
    # No se re-renderizan solas tras un guardado exitoso: on_cambio()
    # dispara la reconstrucción completa del dashboard (incluida esta
    # celda, ya con el valor nuevo). Solo revierten a modo lectura si
    # on_confirmar() lanza TransactionError/ValueError — ahí sí hay que
    # volver a mostrar el valor anterior sin dejar nada guardado a medias.

    def _celda_texto(
        texto_mostrado: str,
        color_texto: Optional[str],
        valor_inicial: str,
        on_confirmar: Callable[[str], None],
        width: int,
    ) -> ft.Control:
        contenedor = ft.Container(width=width, padding=4)

        def _mostrar() -> None:
            contenedor.content = ft.Container(
                content=_texto_celda(texto_mostrado, color=color_texto),
                on_click=lambda e: _editar(),
                ink=True,
                padding=4,
            )
            page.update()

        def _editar() -> None:
            campo = ft.TextField(
                value=valor_inicial,
                width=max(width - ANCHO_BOTON_CONFIRMAR, 40),
                dense=True,
                autofocus=True,
            )

            def _confirmar(e=None) -> None:
                try:
                    on_confirmar(campo.value)
                except (TransactionError, ValueError) as err:
                    _mostrar_error(str(err))
                    _mostrar()
                    return

            campo.on_submit = _confirmar
            contenedor.content = ft.Row(
                [
                    campo,
                    ft.IconButton(icon=ft.Icons.CHECK, icon_color=ft.Colors.PRIMARY, on_click=_confirmar),
                ],
                spacing=0,
                tight=True,
            )
            page.update()

        _mostrar()
        return contenedor

    def _celda_dropdown(
        texto_mostrado: str,
        opciones: list[tuple[str, str]],
        valor_inicial: Optional[str],
        on_confirmar: Callable[[str], None],
        width: int,
    ) -> ft.Control:
        contenedor = ft.Container(width=width, padding=4)

        def _mostrar() -> None:
            contenedor.content = ft.Container(
                content=_texto_celda(texto_mostrado),
                on_click=lambda e: _editar(),
                ink=True,
                padding=4,
            )
            page.update()

        def _editar() -> None:
            # enable_filter+editable: mismo criterio que la fila de alta —
            # confirmar por click, Enter es best effort (ver docstring).
            dd = ft.Dropdown(
                width=width,
                dense=True,
                enable_filter=True,
                editable=True,
                value=valor_inicial,
                options=[ft.dropdown.Option(key=k, text=t) for k, t in opciones],
            )

            def _confirmar(e: Optional[ft.ControlEvent] = None) -> None:
                try:
                    on_confirmar(dd.value)
                except (TransactionError, ValueError) as err:
                    _mostrar_error(str(err))
                    _mostrar()
                    return

            dd.on_select = _confirmar
            dd.on_submit = _confirmar
            contenedor.content = dd
            page.update()

        _mostrar()
        return contenedor

    # ------------------------------------------------------------
    # FILAS DE LA TABLA
    # ------------------------------------------------------------

    def _fila_transaccion(t: dict) -> ft.Control:
        es_egreso = t["tipo_movimiento"] == "egreso"
        # Tokens de color del tema (ft.Colors.*), no hex sueltos.
        color_monto = ft.Colors.RED if es_egreso else ft.Colors.GREEN
        signo = "-" if es_egreso else "+"
        decimales = t["decimales"]
        monto_abs_actual = t["monto_minor"] / (10 ** decimales)

        def _guardar_campo(**kwargs) -> None:
            # amount y currency_code viajan siempre juntos — exigencia
            # propia de TransactionService.update() (ValueError si no).
            transaction_service.update(t["id"], **kwargs)
            _mostrar_ok(f"Movimiento #{t['id']} actualizado.")
            on_cambio()

        def _confirmar_concepto(nuevo: str) -> None:
            if not nuevo or not nuevo.strip():
                raise ValueError("El concepto no puede estar vacío.")
            _guardar_campo(concept=nuevo.strip())

        celda_concepto = _celda_texto(
            texto_mostrado=t["concepto"],
            color_texto=None,
            valor_inicial=t["concepto"],
            on_confirmar=_confirmar_concepto,
            width=ANCHO_COL_CONCEPTO,
        )

        cuenta_de_la_fila = cuentas_por_id.get(t["cuenta_id"])
        celda_banco = ft.Container(
            width=ANCHO_COL_BANCO,
            padding=4,
            content=ft.Row(
                [
                    color_chip(cuenta_de_la_fila["color_hex"] if cuenta_de_la_fila else None),
                    _texto_celda(t["account_name"]),
                ],
                spacing=6,
            ),
        )

        def _confirmar_categoria(nuevo_id: str) -> None:
            _guardar_campo(category_id=int(nuevo_id))

        celda_categoria = _celda_dropdown(
            texto_mostrado=t["category_name"],
            opciones=[(str(c["id"]), f"{c['categoria_principal']} · {c['subcategoria']}") for c in categorias],
            valor_inicial=str(t["categoria_id"]),
            on_confirmar=_confirmar_categoria,
            width=ANCHO_COL_CATEGORIA,
        )

        def _confirmar_monto(nuevo_texto: str) -> None:
            try:
                nuevo_monto = float((nuevo_texto or "").strip().replace(",", "."))
            except ValueError:
                raise ValueError("El monto no es un número válido.")
            if nuevo_monto <= 0:
                raise ValueError("El monto debe ser mayor a 0.")
            _guardar_campo(amount=nuevo_monto, currency_code=t["currency_code"])

        celda_monto = _celda_texto(
            texto_mostrado=f"{signo} {amount_display(t['monto_minor'], decimales, t['currency_symbol'] or '')}",
            color_texto=color_monto,
            valor_inicial=f"{monto_abs_actual:.2f}",
            on_confirmar=_confirmar_monto,
            width=ANCHO_COL_MONTO,
        )

        def _confirmar_fecha(nuevo_texto: str) -> None:
            try:
                datetime.strptime((nuevo_texto or "").strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError("La fecha debe tener el formato AAAA-MM-DD.")
            _guardar_campo(date_str=nuevo_texto.strip())

        celda_fecha = _celda_texto(
            texto_mostrado=t["fecha"],
            color_texto=None,
            valor_inicial=t["fecha"],
            on_confirmar=_confirmar_fecha,
            width=ANCHO_COL_FECHA,
        )

        # Moneda: solo entre las monedas operativas de la cuenta de esta
        # fila — mismo criterio que el resto de la app, nunca ofrecer una
        # moneda que la cuenta no maneja.
        opciones_moneda = (
            [(s["moneda_codigo"], s["moneda_codigo"]) for s in cuenta_de_la_fila["saldos"]]
            if cuenta_de_la_fila else [(t["currency_code"], t["currency_code"])]
        )

        def _confirmar_moneda(nuevo_codigo: str) -> None:
            _guardar_campo(amount=monto_abs_actual, currency_code=nuevo_codigo)

        celda_moneda = _celda_dropdown(
            texto_mostrado=t["currency_code"],
            opciones=opciones_moneda,
            valor_inicial=t["currency_code"],
            on_confirmar=_confirmar_moneda,
            width=ANCHO_COL_MONEDA,
        )

        return ft.Container(
            padding=ft.Padding.symmetric(vertical=4, horizontal=0),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=4,
            content=ft.Row(
                [celda_concepto, celda_banco, celda_categoria, celda_monto, celda_fecha, celda_moneda],
                spacing=ESPACIADO_FILA,
            ),
        )

    # ------------------------------------------------------------
    # CARGA DE DATOS + TABLA
    # ------------------------------------------------------------

    def _cargar_transacciones() -> list:
        filtro_cuenta = estado["filtro_banco"]
        if estado["modo"] == "ultimos":
            return transaction_service.list_transactions(
                account_id=filtro_cuenta, per_page=estado["cantidad"],
            )
        return transaction_service.list_transactions(
            account_id=filtro_cuenta,
            date_from=f"{estado['anio']:04d}-{estado['mes']:02d}-01",
            date_to=f"{estado['anio']:04d}-{estado['mes']:02d}-31",
            per_page=LIMITE_TRANSACCIONES_DEL_MES,
        )

    transacciones = _cargar_transacciones()
    if transacciones:
        filas_tabla: list[ft.Control] = [_fila_transaccion(t) for t in transacciones]
    else:
        filas_tabla = [ft.Text("No hay movimientos para mostrar.", italic=True, color=ft.Colors.OUTLINE)]

    encabezado_columnas = ft.Row(
        [
            ft.Text("Concepto", size=11, weight=ft.FontWeight.BOLD, width=ANCHO_COL_CONCEPTO),
            ft.Text("Banco", size=11, weight=ft.FontWeight.BOLD, width=ANCHO_COL_BANCO),
            ft.Text("Categoría", size=11, weight=ft.FontWeight.BOLD, width=ANCHO_COL_CATEGORIA),
            ft.Text("Monto", size=11, weight=ft.FontWeight.BOLD, width=ANCHO_COL_MONTO),
            ft.Text("Fecha", size=11, weight=ft.FontWeight.BOLD, width=ANCHO_COL_FECHA),
            ft.Text("Moneda", size=11, weight=ft.FontWeight.BOLD, width=ANCHO_COL_MONEDA),
        ],
        spacing=ESPACIADO_FILA,
    )

    return ft.Container(
        padding=16,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=8,
        content=ft.Column(
            [
                ft.Text("Registro de transacciones", size=16, weight=ft.FontWeight.BOLD),
                ft.Container(height=8),
                barra_herramientas,
                ft.Container(height=8),
                fila_alta,
                ft.Divider(),
                encabezado_columnas,
                ft.Column(filas_tabla, spacing=ESPACIADO_FILA),
            ],
            spacing=4,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
