"""
DeltaBalance — ui/components/registro_transacciones.py

Componente de "Registro de transacciones" estilo planilla: barra de
herramientas en una sola fila (período navegable ‹ Mes Año › → filtro de
Banco → filtro de Categoría → búsqueda por texto en concepto, alineada al
extremo derecho — ver ui/components/selector_periodo.py) + divisor sutil +
fila de alta SIEMPRE presente (se resetea sola tras guardar, nunca hay un
botón separado de "agregar otra entrada") + tabla de movimientos con
edición inline celda por celda y un ícono de "Compartir" por fila (ver
ui/components/compartir_gasto.py), filas separadas por ft.Divider (sin
Container con borde por fila). Vive en ui/components/ porque
ui/screens/compras_cuotas.py va a reusar este mismo patrón visual de fila
de tabla más adelante (tarea aparte, todavía no aplicada ahí — ver
CLAUDE.md §8 para la regla de layout que este archivo sigue).

El Registro siempre muestra TODAS las transacciones del período
seleccionado — el selector "Todos los del mes / Últimos N" que existía
antes se sacó por completo (pedido explícito): ya no hay modo "últimos N".

Reglas de arquitectura: solo AccountsService/CategoriasService/
TransactionService/SharedExpensesService — nunca repositories/ ni db/
directo (CLAUDE.md §2/§3).

Tarjetas de crédito (cuentas.tipo='credito') se excluyen de todos los
selectores de banco de este archivo (fila de alta y filtro de la barra de
herramientas) — solo aparecen en ui/screens/compras_cuotas.py, nunca en el
Registro de transacciones.

Categoría: el texto visible de cada opción es SOLO el nombre de la
subcategoría (nunca "categoria_principal · subcategoria").

Búsqueda por concepto: filtrado client-side sobre los datos ya cargados del
período — TransaccionesRepository no soporta un filtro de texto libre, y
agregarlo ahí está fuera de alcance de esta tarea.

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
selección con click SÍ confirma. El resto de la fila (TextField simples:
Concepto/Monto/Fecha) no tiene ese problema.

Fila de alta — reset tras guardar: la fila NUNCA desaparece ni se agrega
una nueva; on_cambio() (ver docstring de build()) reconstruye TODO el
Registro, lo que de por sí recrea la fila de alta desde cero con sus
valores default (campo_concepto_alta usa autofocus=True, así que además
recupera el foco solo — no hace falta lógica de reset manual). Si el
guardado falla (TransactionError/ValueError), _confirmar_alta() vuelve
("return") ANTES de llamar on_cambio(), así que la fila nunca se
reconstruye y los valores tipeados quedan intactos para corregir.

Ícono "Compartir" por fila (ver ui/components/compartir_gasto.py): visible
siempre si la transacción YA tiene un gasto compartido asociado (indicador
persistente), o solo al hacer hover sobre la fila si todavía no lo tiene
(acción de descubrimiento, para no ensuciar visualmente el caso común).
Hover implementado con Container.on_hover — normalizado defensivamente
(str(e.data).lower() == "true") por si el shape de HoverEvent cambió en
0.80+; no está en la lista de cambios confirmados de
docs/FLET_API_NOTES.md, avisar si no dispara corriendo la app.

Refresco: cada acción que cambia datos (alta, edición de celda, cambio de
período/búsqueda/filtro banco/filtro categoría en la barra de herramientas,
alta de un gasto compartido) llama a on_cambio(), que el dashboard usa para
reconstruir toda la pantalla (patrimonio + este registro) — no hay refresco
parcial local acá adentro, todo pasa por ese único mecanismo ya existente.

Diálogos/SnackBar/botones: mismas convenciones de Flet 0.86.5 que el resto
de ui/ — ver docs/FLET_API_NOTES.md. Este componente no abre AlertDialog
propio (la edición es inline) — los diálogos de "Compartir" viven en
ui/components/compartir_gasto.py.
"""

from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.shared_expenses_service import SharedExpensesService
from services.transaction_service import TransactionService, TransactionError
from ui.components import compartir_gasto, selector_periodo
from ui.components.color_chip import color_chip
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---
# Anchos generosos a propósito: Concepto y Categoría son los textos más
# largos — lo que no entra se trunca con ellipsis + tooltip (ver
# _texto_celda()) en vez de romper el alineado de la fila.
ANCHO_COL_CONCEPTO = 220
ANCHO_COL_BANCO = 160
ANCHO_COL_CATEGORIA = 180
ANCHO_COL_MONTO = 120
ANCHO_COL_FECHA = 110
ANCHO_COL_MONEDA = 80
ANCHO_COL_COMPARTIR = 48
ANCHO_BOTON_CONFIRMAR = 48
ANCHO_TOOLBAR_BUSQUEDA = 200
ANCHO_TOOLBAR_FILTRO_BANCO = 150
ANCHO_TOOLBAR_FILTRO_CATEGORIA = 160
ESPACIADO_FILA = 8
LIMITE_TRANSACCIONES_DEL_MES = 500  # tope de per_page al pedir las transacciones del período


def _cuentas_no_credito(cuentas: list[dict]) -> list[dict]:
    """Excluye tarjetas de crédito — solo aparecen en Compras en cuotas."""
    return [c for c in cuentas if c["tipo"] != "credito"]


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    transaction_service: TransactionService,
    shared_expenses_service: SharedExpensesService,
    estado: dict,
    on_cambio: Callable[[], None],
) -> ft.Control:
    """
    Args:
        estado:    Dict MUTABLE del caller (el `estado` del dashboard) —
                   {"mes", "anio", "filtro_banco": int|None,
                   "filtro_categoria": int|None, "busqueda": str}. Se
                   completa con setdefault() la primera vez. Mes/año viven
                   acá (barra de herramientas del Registro) — Estadísticas
                   mantiene su propio período independiente, no comparten
                   este dict.
        on_cambio: Callback tras cualquier cambio (alta, edición de celda,
                   gasto compartido nuevo, o cualquier control de la barra
                   de herramientas). El dashboard reconstruye la pantalla
                   entera con esto.
    """
    hoy = date.today()
    estado.setdefault("mes", hoy.month)
    estado.setdefault("anio", hoy.year)
    estado.setdefault("filtro_banco", None)
    estado.setdefault("filtro_categoria", None)
    estado.setdefault("busqueda", "")

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
            weight=weight or TypographyTokens.TABLE_CONTENT_WEIGHT_REGULAR,
            size=TypographyTokens.TABLE_CONTENT_SIZE,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=texto,
        )

    cuentas_activas = _cuentas_no_credito(accounts_service.list_accounts(solo_activas=True))
    cuentas_todas = accounts_service.list_accounts(solo_activas=False)
    cuentas_todas_no_credito = _cuentas_no_credito(cuentas_todas)
    cuentas_por_id = {c["id"]: c for c in cuentas_todas}
    categorias = [c for c in categorias_service.list_categories() if c["tipo"] in ("ingreso", "egreso")]

    # ------------------------------------------------------------
    # BARRA DE HERRAMIENTAS: período → banco → categoría → búsqueda
    # ------------------------------------------------------------

    def _on_cambio_periodo() -> None:
        on_cambio()

    def _on_change_busqueda(e: ft.ControlEvent) -> None:
        estado["busqueda"] = campo_busqueda.value or ""
        on_cambio()

    def _on_select_filtro_banco(e: ft.ControlEvent) -> None:
        valor = dropdown_filtro_banco.value
        estado["filtro_banco"] = int(valor) if valor else None
        on_cambio()

    def _on_select_filtro_categoria(e: ft.ControlEvent) -> None:
        valor = dropdown_filtro_categoria.value
        estado["filtro_categoria"] = int(valor) if valor else None
        on_cambio()

    control_periodo = selector_periodo.build(estado, _on_cambio_periodo)

    dropdown_filtro_banco = ft.Dropdown(
        label="Banco",
        width=ANCHO_TOOLBAR_FILTRO_BANCO,
        dense=True,
        options=[ft.dropdown.Option(key="", text="Todos")] + [
            ft.dropdown.Option(key=str(c["id"]), text=c["nombre"]) for c in cuentas_todas_no_credito
        ],
        value=str(estado["filtro_banco"]) if estado["filtro_banco"] is not None else "",
        on_select=_on_select_filtro_banco,
    )
    dropdown_filtro_categoria = ft.Dropdown(
        label="Categoría",
        width=ANCHO_TOOLBAR_FILTRO_CATEGORIA,
        dense=True,
        options=[ft.dropdown.Option(key="", text="Todas")] + [
            ft.dropdown.Option(key=str(c["id"]), text=c["subcategoria"]) for c in categorias
        ],
        value=str(estado["filtro_categoria"]) if estado["filtro_categoria"] is not None else "",
        on_select=_on_select_filtro_categoria,
    )
    # on_submit (Enter) confirma la búsqueda — mismo criterio que el resto
    # de los campos de esta pantalla (nada se filtra "en vivo" tecla por
    # tecla, cada acción se confirma explícitamente antes de reconstruir).
    campo_busqueda = ft.TextField(
        width=ANCHO_TOOLBAR_BUSQUEDA,
        label="Buscar",
        hint_text="Concepto... (Enter)",
        dense=True,
        prefix_icon=ft.Icons.SEARCH,
        value=estado["busqueda"],
        on_submit=_on_change_busqueda,
    )

    # Período → Banco → Categoría → (spacer) → Búsqueda, alineada al
    # extremo derecho. SIN wrap=True acá a propósito: Wrap (lo que Flet
    # renderiza cuando wrap=True) no soporta hijos flexibles/expand —
    # combinarlos tira un error de layout del lado de Flutter (nunca llega
    # a la consola de Python) que en un build web release se ve como un
    # rectángulo gris sólido del tamaño del widget roto. Bug real
    # encontrado y corregido — no reintroducir wrap=True acá mientras el
    # spacer expand=True siga estando.
    barra_herramientas = ft.Row(
        [
            control_periodo,
            dropdown_filtro_banco,
            dropdown_filtro_categoria,
            ft.Container(expand=True),
            campo_busqueda,
        ],
        spacing=ESPACIADO_FILA,
    )

    # ------------------------------------------------------------
    # FILA DE ALTA (siempre presente, se resetea sola tras guardar)
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
            _mostrar_error("Primero cargá una cuenta (no tarjeta de crédito) en Configuración → Cuentas.")
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
            # NO se resetea la fila: se vuelve acá antes de on_cambio(), así
            # que la reconstrucción (que es lo único que recrea la fila con
            # valores default) nunca se dispara — lo tipeado queda intacto.
            _mostrar_error(str(err))
            return

        _mostrar_ok(f"Movimiento #{resultado.transaction_id} registrado.")
        # on_cambio() reconstruye todo el Registro — la fila de alta se
        # recrea desde cero con sus valores default (Fecha=hoy, Moneda=
        # default de la primera cuenta activa, resto vacío) y
        # campo_concepto_alta.autofocus=True le devuelve el foco sin
        # lógica extra.
        on_cambio()

    campo_concepto_alta = ft.TextField(width=ANCHO_COL_CONCEPTO, label="Concepto", dense=True, autofocus=True)
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
            ft.dropdown.Option(key=str(c["id"]), text=c["subcategoria"])
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
    # último dispara el mismo guardado que el botón).
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
        weight=None,
    ) -> ft.Control:
        contenedor = ft.Container(width=width, padding=4)

        def _mostrar() -> None:
            contenedor.content = ft.Container(
                content=_texto_celda(texto_mostrado, color=color_texto, weight=weight),
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
            opciones=[(str(c["id"]), c["subcategoria"]) for c in categorias],
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
            weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
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

        icono_compartir, ya_compartido = compartir_gasto.build_icon(page, shared_expenses_service, t, on_cambio)
        celda_compartir = ft.Container(
            width=ANCHO_COL_COMPARTIR,
            content=icono_compartir,
            opacity=1.0 if ya_compartido else 0.0,
        )

        fila_contenido = ft.Row(
            [celda_concepto, celda_banco, celda_categoria, celda_monto, celda_fecha, celda_moneda, celda_compartir],
            spacing=ESPACIADO_FILA,
        )

        def _on_hover_fila(e: ft.ControlEvent) -> None:
            # Normalizado defensivamente — ver docstring del módulo.
            hover_activo = str(e.data).lower() == "true"
            celda_compartir.opacity = 1.0 if (hover_activo or ya_compartido) else 0.0
            page.update()

        return ft.Container(content=fila_contenido, on_hover=_on_hover_fila)

    # ------------------------------------------------------------
    # CARGA DE DATOS + TABLA
    # ------------------------------------------------------------

    def _cargar_transacciones() -> list:
        transacciones = transaction_service.list_transactions(
            account_id=estado["filtro_banco"],
            category_id=estado["filtro_categoria"],
            date_from=f"{estado['anio']:04d}-{estado['mes']:02d}-01",
            date_to=f"{estado['anio']:04d}-{estado['mes']:02d}-31",
            per_page=LIMITE_TRANSACCIONES_DEL_MES,
        )
        texto_busqueda = (estado["busqueda"] or "").strip().lower()
        if texto_busqueda:
            transacciones = [t for t in transacciones if texto_busqueda in t["concepto"].lower()]
        return transacciones

    transacciones = _cargar_transacciones()
    filas_tabla: list[ft.Control] = []
    if transacciones:
        for i, t in enumerate(transacciones):
            if i > 0:
                filas_tabla.append(ft.Divider(height=1))
            filas_tabla.append(_fila_transaccion(t))
    else:
        filas_tabla = [ft.Text("No hay movimientos para mostrar.", italic=True, color=ft.Colors.OUTLINE)]

    def _header(texto: str, width: int) -> ft.Text:
        return ft.Text(
            texto,
            size=TypographyTokens.TABLE_HEADER_SIZE,
            weight=TypographyTokens.TABLE_HEADER_WEIGHT,
            width=width,
        )

    encabezado_columnas = ft.Row(
        [
            _header("Concepto", ANCHO_COL_CONCEPTO),
            _header("Banco", ANCHO_COL_BANCO),
            _header("Categoría", ANCHO_COL_CATEGORIA),
            _header("Monto", ANCHO_COL_MONTO),
            _header("Fecha", ANCHO_COL_FECHA),
            _header("Moneda", ANCHO_COL_MONEDA),
            _header("", ANCHO_COL_COMPARTIR),
        ],
        spacing=ESPACIADO_FILA,
    )

    return ft.Container(
        padding=16,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=8,
        content=ft.Column(
            [
                ft.Text(
                    "Registro de transacciones",
                    size=TypographyTokens.SECTION_TITLE_SIZE,
                    weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                ),
                ft.Container(height=8),
                barra_herramientas,
                ft.Divider(height=1),
                fila_alta,
                ft.Divider(),
                encabezado_columnas,
                ft.Divider(height=1),
                ft.Column(filas_tabla, spacing=ESPACIADO_FILA),
            ],
            spacing=4,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
