"""
DeltaBalance — ui/screens/compras_cuotas.py

Pantalla de Compras en cuotas: tarjeta de desglose "Total a pagar por
tarjeta" (ver _tarjeta_desglose_tarjeta() — Tarea 4 de
docs/PROXIMOS_PASOS.md, SOLO tarjetas con actividad real ese período,
detección dinámica vía FeesService.resumen_por_tarjeta()) → sección
expandible "Cargos extra de este resumen" (Tarea 3 — ver
_seccion_detalle_resumen(), se abre con el ícono de cada fila de la
tarjeta de desglose) → barra de herramientas de filtro (período → banco →
categoría → búsqueda, ver ui/components/barra_filtros.py, compartida con
ui/components/registro_transacciones.py) → fila de alta fija (campos en
línea, monto CON SIGNO) → tabla de compras existentes, filtrada por
mes/año/banco/categoría/búsqueda. Reusa el patrón visual de fila de tabla
de ui/components/color_chip.py — no la lógica de guardado del Registro de
transacciones del dashboard, que es demasiado distinta (acá el signo
dispara compra-nueva vs. ajuste-de-resumen, no gasto-vs-ingreso). Sin
edición inline de compras ya cargadas en esta tarea (alcanza con listar y
dar de alta) — si se necesita editar más adelante, es tarea aparte
reusando el mismo patrón del Registro.

A diferencia del Registro (que recibe su `estado` del dashboard y se
reconstruye entero vía el on_cambio() del caller), esta pantalla administra
su propio `estado` internamente (mismo patrón que
ui/screens/estadisticas.py) — no hay ningún caller externo que necesite
compartir mes/año/filtros con otra pantalla. _refrescar_pantalla()
reconstruye barra de herramientas + fila de alta (necesario para que el
texto "‹ Mes Año ›" se actualice y para que la fila de alta se resetee al
cambiar de período — mismo criterio ya establecido en
ui/components/registro_transacciones.py) y COLAPSA la sección de cargos
extra si estaba abierta (un resumen expandido es de una cuenta/mes/año
puntual, ya no corresponde si cambia el período) cada vez que cambia algo
en la barra de herramientas; _refrescar_datos() (más liviano, NO toca la
fila de alta ni la barra) recalcula la tarjeta de desglose, la sección de
cargos extra (si hay una expandida) y la tabla — usado tras confirmar una
alta, compartir una compra, o agregar/eliminar un cargo extra, para no
perder lo que el usuario tenga tipeado a medias en la fila de alta ni
resetear el período por una acción que no lo tocó.

Cargos extra del resumen (Tarea 3): FeesService.add_extra_charge()/
list_extra_charges()/remove_extra_charge() ya existían y estaban probados
(services/fees_service.py, ver también verify/compras_cuotas/
verify_fees_service.py) — esta pantalla es la primera UI que los
consume. Punto de entrada: el ícono de cada fila de "Total a pagar por
tarjeta" llama a FeesService.open_statement(cuenta, mes, año)
(idempotente) para resolver el statement_id y expande la sección debajo
— funciona igual sobre un resumen recién creado (vacío, 'abierto', sin
cargos) que sobre uno preexistente, no hace falta ninguna cuota
confirmada primero. Si el resumen NO está 'abierto' (ya 'cerrado' o
'pagado'), la fila de alta de cargos y el ícono de eliminar de cada cargo
existente se deshabilitan de entrada (con una nota visual) en vez de
dejar que el usuario intente una acción que add_extra_charge()/
remove_extra_charge() van a rechazar de todas formas — el mensaje real de
StatementAlreadyClosedError/StatementAlreadyPaidError (subclases de
FeesError) se muestra tal cual si de todos modos llega a fallar (ej. dos
pestañas del navegador). Eliminar un cargo pide confirmación (AlertDialog)
por ser una acción fácil de tocar sin querer.

_TIPOS_CARGO_EXTRA (arriba, en la configuración de layout) espeja
FeesService._EXTRA_CHARGE_TYPES — ese atributo es privado, así que la UI
no lo importa directo; el CHECK de resumen_cargos_extra en db/schema.sql
es la fuente de verdad real de esos 4 valores.

Filtro de Banco de la barra de herramientas y opciones de Cuenta de la
fila de alta: la MISMA función _cuentas_credito() filtra ambas listas a
tipo='credito' — antes cada una se armaba por separado y la barra de
herramientas terminó sin filtrar de verdad (bug corregido).

Routing por CATEGORÍA al confirmar la alta (Tarea 3 — reemplaza por
completo la regla vieja de "monto negativo = ajuste automático", ya no
existe ningún branch por signo):
- Categoría NORMAL (cualquiera que no sea una de las 3 especiales de
  abajo): comportamiento de siempre, vía FeesService.create_purchase()
  (monto y cantidad de cuotas tal cual se cargaron). El monto DEBE ser
  positivo acá — uno negativo con categoría normal es inválido (mismo
  tipo de error que ya existía para monto<=0), ya no hay forma de que un
  negativo con categoría normal signifique "ajuste".
- Categoría ESPECIAL ("Impuesto tarjeta"/"Recargo tarjeta"/
  "Ajuste/Reintegro tarjeta", ver services/categorias_service.py
  CATEGORIAS_PROTEGIDAS y services/fees_service.py CATEGORIAS_CARGO_EXTRA):
  NO crea una compra en cuotas — resuelve el resumen de esa cuenta y el
  mes/año de la fecha cargada vía FeesService.open_statement()
  (idempotente) y carga un cargo extra con
  FeesService.add_extra_charge(statement_id, concept=..., charge_type=<según
  el mapeo de categoría>, amount_minor=<el monto tipeado, CON el signo tal
  cual — positivo o negativo son ambos válidos acá, la categoría ya
  clasifica el tipo de cargo, no su signo>). El campo Cuotas se deshabilita
  automáticamente al elegir una de estas tres categorías (no aplica,
  mapa_categoria_a_charge_type resuelto una sola vez al construir la fila,
  ver _on_seleccionar_categoria_alta()). Si add_extra_charge() lanza
  StatementAlreadyClosedError/StatementAlreadyPaidError (subclases de
  FeesError), se muestra ese mensaje tal cual en un SnackBar — no hay forma
  de cargar un cargo extra retroactivo sobre un resumen ya cerrado con la
  lógica actual.

Cuenta/Categoría de la fila de alta usan CampoFiltrable (componente
propio, ver ui/components/campo_filtrable.py) — NO ft.Dropdown(
enable_filter=True) ni ft.AutoComplete. Se probaron los dos controles
nativos en rondas anteriores del proyecto (primero Dropdown, después
AutoComplete) y ambos tuvieron fricción real — ver el docstring de
ui/components/registro_transacciones.py para el historial completo y el
de campo_filtrable.py para el detalle de cómo funciona el reemplazo. Acá
NO hay edición inline de compras (ver arriba), así que el único lugar
afectado es la fila de alta. Cuenta muestra solo "nombre" (sin sufijo de
tipo — ya no hace falta, _cuentas_credito() filtra TODA la lista a solo
tipo='credito', ver más abajo) tanto en las opciones de Cuenta de la fila
de alta como en el filtro de Banco de la barra de herramientas — a
diferencia del Registro de transacciones, donde el filtro de Banco
EXCLUYE tarjetas de crédito por completo, acá son justamente lo único
relevante.

Monto de la fila de alta usa CampoMonto (componente propio, ver
ui/components/campo_monto.py y CLAUDE.md §8) — calculadora de fórmulas
("=..."), persistir_formula=False (default: siempre muestra el número,
nunca la fórmula que lo generó), on_confirmar no-op porque la fila entera
confirma junta en _confirmar_alta() (mismo motivo que en el Registro de
transacciones, ver su docstring). La calculadora NO cambia el routing por
categoría descripto arriba: campo_monto_alta.texto ya llega a
_confirmar_alta() como el número resuelto (con signo), así que la decisión
categoría-normal-positivo-obligatorio / categoría-especial-cualquier-signo
sigue leyendo el mismo valor de siempre.

Tipografía: TypographyTokens.FILTER_SIZE para los cuatro controles de la
barra de herramientas (período, filtro banco, filtro categoría, búsqueda)
y para el TextField interno de CampoFiltrable — mismo tamaño chico para
todos, pedido explícito. TypographyTokens.TABLE_CONTENT_SIZE para el resto
de los campos de la fila de alta y el contenido de la tabla (ambos tokens
valen 11 hoy, así que toda la fila de alta se sigue viendo uniforme).

Ícono "Compartir" por fila de compra ya guardada (ver
ui/components/compartir_compra.py): mismo patrón de
ui/components/compartir_gasto.py en el Registro de transacciones —
visible siempre si la compra YA tiene algún gasto_compartido asociado
(total o parcial, ver docstring de ese módulo), o solo al hover si
todavía no tiene ninguno. Comparte según el modo_deuda que la compra ya
tiene guardado (compras_cuotas.modo_deuda) — esta pantalla no ofrece
elegir el modo, es una decisión tomada al crear la compra.

Filtrado de la tabla: FeesService.list_purchases() solo soporta filtrar
por cuenta/estado/moneda de forma nativa — mes/año, categoría y búsqueda
de texto se filtran client-side sobre lo ya traído (mismo criterio que
ui/components/registro_transacciones.py con TransactionService.
list_transactions(), que tampoco soporta búsqueda de texto libre).

Reglas de arquitectura: solo AccountsService/CategoriasService/FeesService/
SharedExpensesService — nunca repositories/ ni db/ directo (CLAUDE.md
§2/§3). amount_minor para add_extra_charge() se calcula acá con
utils.money.amount_to_minor() (no db.database.to_minor(), que viviría del
lado prohibido de la frontera ui/↔db/).

Diálogos/SnackBar/botones: mismas convenciones de Flet 0.86.5 que el resto
de ui/ — ver docs/FLET_API_NOTES.md. Esta pantalla no abre AlertDialog
propio (los de "Compartir" viven en ui/components/compartir_compra.py).
"""

from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.fees_service import CATEGORIAS_CARGO_EXTRA, FeesService, FeesError
from services.shared_expenses_service import SharedExpensesService
from ui.components import barra_filtros, compartir_compra
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.components.color_chip import color_chip
from ui.theme.tokens import SharedFieldText, TypographyTokens
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---
_ANCHO_CONCEPTO = 170
_ANCHO_BANCO = 150
_ANCHO_CATEGORIA = 170
_ANCHO_MONTO = 110
_ANCHO_CUOTAS = 80
_ANCHO_FECHA = 110
# Ampliado (era 70) — mismo motivo que ANCHO_COL_MONEDA en
# registro_transacciones.py: el código de moneda quedaba cortado.
_ANCHO_MONEDA = 100
_ANCHO_COMPARTIR = 48
_ESPACIADO_FILA = 8
_ANCHO_TOOLBAR_FILTRO_BANCO = 150
_ANCHO_TOOLBAR_FILTRO_CATEGORIA = 160
_ANCHO_TOOLBAR_BUSQUEDA = 200
_LIMITE_COMPRAS_DEL_MES = 500  # tope de per_page al pedir las compras (se filtra por mes client-side después)

# Sección "Cargos extra de este resumen" (Tarea 3)
_ANCHO_CONCEPTO_CARGO = 200
_ANCHO_TIPO_CARGO = 130
_ANCHO_MONTO_CARGO = 110
_ANCHO_ELIMINAR_CARGO = 40
# Espejo de FeesService._EXTRA_CHARGE_TYPES (services/fees_service.py) —
# ese atributo es privado (guion bajo), así que no se importa de acá: el
# CHECK de la tabla resumen_cargos_extra en db/schema.sql es la fuente de
# verdad real, esto es solo la copia que necesita el Dropdown de la UI.
_TIPOS_CARGO_EXTRA = ("impuesto", "recargo", "ajuste", "otro")


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    fees_service: FeesService,
    shared_expenses_service: SharedExpensesService,
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

    def _mostrar_error(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=False)

    hoy = date.today()
    # Estado propio de esta pantalla — no recibe estado de ningún caller
    # (ver docstring del módulo). setdefault() no hace falta acá porque se
    # inicializa completo de una — no lo consume ningún otro componente
    # antes de que exista, a diferencia del `estado` del dashboard.
    estado = {
        "mes": hoy.month,
        "anio": hoy.year,
        "filtro_banco": None,
        "filtro_categoria": None,
        "busqueda": "",
    }

    def _cuentas_credito(cuentas: list[dict]) -> list[dict]:
        """
        Compras en cuotas es exclusivamente sobre tarjetas de crédito —
        filtra cualquier lista de cuentas a solo tipo='credito'. Función
        ÚNICA usada tanto para las opciones del CampoFiltrable de Cuenta
        de la fila de alta como para el filtro de Banco de la barra de
        herramientas, para que las dos nunca vuelvan a desincronizarse
        (antes cada una armaba su propia lista por separado — la barra de
        herramientas terminó mostrando cuentas de cualquier tipo, sin
        filtrar de verdad).
        """
        return sorted((c for c in cuentas if c["tipo"] == "credito"), key=lambda c: c["nombre"])

    cuentas_todas = accounts_service.list_accounts(solo_activas=False)
    cuentas_por_id = {c["id"]: c for c in cuentas_todas}
    # Todas las tarjetas alguna vez usadas (incluye archivadas — para poder
    # filtrar compras históricas de una tarjeta ya dada de baja) para el
    # filtro de Banco; solo las activas (elegibles para una compra nueva)
    # para las opciones de la fila de alta.
    cuentas_credito_todas = _cuentas_credito(cuentas_todas)
    cuentas_activas = _cuentas_credito(accounts_service.list_accounts(solo_activas=True))
    categorias = categorias_service.list_categories(tipo="egreso")
    monedas_por_codigo = {m["codigo"]: m for m in accounts_service.list_currencies()}
    # Resuelve, una sola vez, el id REAL (varía entre bases) de cada una de
    # las 3 categorías especiales de CATEGORIAS_CARGO_EXTRA (ver
    # services/fees_service.py) contra la lista de categorías ya cargada
    # acá — el resto de la pantalla solo necesita id_seleccionado (str) →
    # charge_type, sin volver a comparar por nombre en cada alta.
    mapa_categoria_a_charge_type: dict[str, str] = {
        str(c["id"]): CATEGORIAS_CARGO_EXTRA[(c["categoria_principal"], c["subcategoria"])]
        for c in categorias
        if (c["categoria_principal"], c["subcategoria"]) in CATEGORIAS_CARGO_EXTRA
    }

    # ------------------------------------------------------------
    # TOTAL A PAGAR POR TARJETA (desglose compacto — Tarea 4) +
    # DETALLE DE RESUMEN / CARGOS EXTRA (Tarea 3)
    # ------------------------------------------------------------
    # Punto de entrada al detalle de un resumen (Tarea 3, punto 1): un
    # ícono por fila de la tarjeta de desglose, que resuelve el
    # statement_id vía FeesService.open_statement() (idempotente — lo crea
    # 'abierto' y vacío si todavía no existía para esa cuenta/mes/año) y
    # abre la sección de cargos extra debajo. Estado puramente de UI (qué
    # tarjeta está expandida), no vive en `estado` porque no es un filtro.
    detalle_abierto = {"cuenta_id": None, "statement_id": None, "account_name": "", "decimales": 2, "currency_symbol": ""}

    contenedor_desglose = ft.Container()
    contenedor_detalle_resumen = ft.Container()

    def _toggle_detalle_resumen(d: dict) -> None:
        if detalle_abierto["cuenta_id"] == d["cuenta_id"]:
            detalle_abierto.update(cuenta_id=None, statement_id=None, account_name="", decimales=2, currency_symbol="")
        else:
            resultado = fees_service.open_statement(account_id=d["cuenta_id"], month=estado["mes"], year=estado["anio"])
            detalle_abierto.update(
                cuenta_id=d["cuenta_id"],
                statement_id=resultado.entity_id,
                account_name=d["account_name"],
                decimales=d["decimales"],
                currency_symbol=d["currency_symbol"],
            )
        _refrescar_datos()

    def _tarjeta_desglose_tarjeta() -> ft.Control:
        desglose = fees_service.resumen_por_tarjeta(estado["mes"], estado["anio"])
        filtro_banco = estado.get("filtro_banco")
        if filtro_banco is not None:
            desglose = [d for d in desglose if d["cuenta_id"] == filtro_banco]

        if not desglose:
            contenido = ft.Text(
                "Sin tarjetas con actividad este período.",
                italic=True,
                color=ft.Colors.OUTLINE,
                size=TypographyTokens.TABLE_CONTENT_SIZE,
            )
        else:
            columnas = []
            for d in desglose:
                texto_monto = amount_display(d["monto_total_minor"], d["decimales"], d["currency_symbol"] or "")
                detalle_de_esta = detalle_abierto["cuenta_id"] == d["cuenta_id"]
                fila = [
                    ft.Text(d["account_name"], size=TypographyTokens.TABLE_CONTENT_SIZE),
                    ft.Text(
                        texto_monto,
                        size=TypographyTokens.TABLE_CONTENT_SIZE,
                        weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.EXPAND_LESS if detalle_de_esta else ft.Icons.RECEIPT_LONG,
                        icon_size=18,
                        tooltip="Cerrar detalle" if detalle_de_esta else "Ver cargos extra de este resumen",
                        on_click=lambda e, d=d: _toggle_detalle_resumen(d),
                    ),
                ]
                hijos = [ft.Row(fila, spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)]
                if d["cargos_extra_multiples_monedas"]:
                    # Ver docs/DATA_MODEL_DECISIONS.md sección 15 — caso
                    # límite, no se adivina a qué moneda pertenecen.
                    hijos.append(
                        ft.Text(
                            "cargos extra sin asignar (cuotas en más de una moneda este mes)",
                            size=TypographyTokens.LABEL_SIZE,
                            italic=True,
                            color=ft.Colors.OUTLINE,
                        )
                    )
                columnas.append(ft.Column(hijos, spacing=2))
            contenido = ft.Row(columnas, spacing=20, wrap=True)

        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Text(
                        "Total a pagar por tarjeta",
                        size=TypographyTokens.SECTION_TITLE_SIZE,
                        weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                    ),
                    contenido,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _actualizar_desglose() -> None:
        contenedor_desglose.content = _tarjeta_desglose_tarjeta()

    def _confirmar_eliminar_cargo(cargo: dict) -> None:
        def _cerrar_dialogo(e=None) -> None:
            page.pop_dialog()

        def _eliminar(e=None) -> None:
            try:
                fees_service.remove_extra_charge(charge_id=cargo["id"], statement_id=detalle_abierto["statement_id"])
            except FeesError as err:
                _cerrar_dialogo()
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            _mostrar_ok(f"Cargo '{cargo['concepto']}' eliminado.")
            _refrescar_datos()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Eliminar cargo extra"),
            content=ft.Text(f"¿Eliminar '{cargo['concepto']}'? Esta acción no se puede deshacer."),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Eliminar"), on_click=_eliminar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _seccion_detalle_resumen() -> ft.Control:
        """
        Cargos extra (resumen_cargos_extra) del resumen actualmente
        expandido en la tarjeta de desglose — vacío (ft.Container() sin
        content) si no hay ninguno expandido. statement_id siempre viene
        de un FeesService.open_statement() ya corrido (ver
        _toggle_detalle_resumen()), así que el resumen siempre existe acá
        (idempotente: 'abierto', montos en 0, sin cargos, si recién se
        creó) — get_statement() solo puede devolver None si alguien lo
        borró en otro lado mientras esta sección estaba abierta, caso
        defensivo, no esperado en el uso normal.
        """
        if detalle_abierto["statement_id"] is None:
            return ft.Container()

        statement_id = detalle_abierto["statement_id"]
        statement = fees_service.get_statement(statement_id)
        if statement is None:
            return ft.Container()

        decimales = detalle_abierto["decimales"]
        simbolo = detalle_abierto["currency_symbol"] or ""
        resumen_abierto = statement["estado"] == "abierto"
        cargos = fees_service.list_extra_charges(statement_id)

        if not cargos:
            lista_cargos = ft.Text(
                "Sin cargos extra cargados todavía.",
                italic=True, color=ft.Colors.OUTLINE, size=TypographyTokens.TABLE_CONTENT_SIZE,
            )
        else:
            filas_cargos = []
            for c in cargos:
                color_monto = ft.Colors.GREEN if c["monto_minor"] >= 0 else ft.Colors.RED
                filas_cargos.append(
                    ft.Row(
                        [
                            ft.Container(width=_ANCHO_CONCEPTO_CARGO, content=ft.Text(c["concepto"], size=TypographyTokens.TABLE_CONTENT_SIZE)),
                            ft.Container(width=_ANCHO_TIPO_CARGO, content=ft.Text(c["tipo"], size=TypographyTokens.TABLE_CONTENT_SIZE)),
                            ft.Container(
                                width=_ANCHO_MONTO_CARGO,
                                content=ft.Text(
                                    amount_display(c["monto_minor"], decimales, simbolo),
                                    size=TypographyTokens.TABLE_CONTENT_SIZE,
                                    weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                                    color=color_monto,
                                ),
                            ),
                            ft.Container(
                                width=_ANCHO_ELIMINAR_CARGO,
                                content=ft.IconButton(
                                    icon=ft.Icons.DELETE_OUTLINE,
                                    icon_size=16,
                                    tooltip="Eliminar cargo",
                                    disabled=not resumen_abierto,
                                    on_click=lambda e, c=c: _confirmar_eliminar_cargo(c),
                                ),
                            ),
                        ],
                        spacing=_ESPACIADO_FILA,
                    )
                )
            lista_cargos = ft.Column(filas_cargos, spacing=4)

        # Fila de alta de un cargo nuevo — deshabilitada por completo si el
        # resumen no está 'abierto' (Tarea 3, punto 3: no dejar que el
        # usuario intente cargar algo que add_extra_charge() va a rechazar
        # de todas formas).
        campo_concepto_cargo = ft.TextField(
            width=_ANCHO_CONCEPTO_CARGO, label="Concepto", dense=True, disabled=not resumen_abierto,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )
        dropdown_tipo_cargo = ft.Dropdown(
            width=_ANCHO_TIPO_CARGO, label="Tipo", dense=True, disabled=not resumen_abierto,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
            options=[ft.dropdown.Option(key=t, text=t) for t in _TIPOS_CARGO_EXTRA],
            value=_TIPOS_CARGO_EXTRA[0],
        )
        # CampoMonto (ui/components/campo_monto.py, CLAUDE.md §9) — mismo
        # criterio que el resto de los campos de Monto de esta pantalla:
        # resuelve una fórmula "=..." tipeada acá a un número antes de que
        # _confirmar_cargo_nuevo() la reciba, sin cambiar la validación de
        # dominio existente (monto != 0, cualquier signo — ver docstring
        # del módulo). persistir_formula=False default, on_confirmar
        # no-op porque esta fila también confirma junta (botón "Agregar
        # cargo"), igual que fila_alta_cargo no tenía encadenado de Enter
        # antes de este cambio. disabled se setea sobre .control (el
        # ft.TextField real) porque el constructor de CampoMonto no expone
        # ese parámetro — no hacía falta agregarlo para esta tarea.
        campo_monto_cargo = CampoMonto(
            page,
            on_confirmar=lambda monto_minor: None,
            width=_ANCHO_MONTO_CARGO,
            hint_text=SharedFieldText.HINT_MONTO_CON_SIGNO,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )
        campo_monto_cargo.control.disabled = not resumen_abierto

        def _confirmar_cargo_nuevo(e=None) -> None:
            if not campo_concepto_cargo.value or not campo_concepto_cargo.value.strip():
                _mostrar_error("El concepto no puede estar vacío.")
                return
            try:
                monto = float((campo_monto_cargo.texto or "").strip().replace(",", "."))
            except ValueError:
                _mostrar_error("El monto no es un número válido.")
                return
            if monto == 0:
                _mostrar_error("El monto no puede ser 0.")
                return
            try:
                fees_service.add_extra_charge(
                    statement_id=statement_id,
                    concept=campo_concepto_cargo.value.strip(),
                    charge_type=dropdown_tipo_cargo.value,
                    amount_minor=amount_to_minor(monto, decimales),
                )
            except FeesError as err:
                _mostrar_error(str(err))
                return
            _mostrar_ok("Cargo extra agregado.")
            _refrescar_datos()

        fila_alta_cargo = ft.Row(
            [
                campo_concepto_cargo,
                dropdown_tipo_cargo,
                campo_monto_cargo.control,
                ft.IconButton(
                    icon=ft.Icons.ADD_CIRCLE,
                    icon_color=ft.Colors.PRIMARY,
                    tooltip="Agregar cargo",
                    disabled=not resumen_abierto,
                    on_click=_confirmar_cargo_nuevo,
                ),
            ],
            spacing=_ESPACIADO_FILA,
        )

        hijos_seccion = [
            ft.Row(
                [
                    ft.Text(
                        f"Cargos extra — {detalle_abierto['account_name']} {estado['mes']:02d}/{estado['anio']}",
                        size=TypographyTokens.SECTION_TITLE_SIZE,
                        weight=TypographyTokens.SECTION_TITLE_WEIGHT,
                    ),
                    ft.IconButton(icon=ft.Icons.CLOSE, icon_size=18, tooltip="Cerrar", on_click=lambda e: _toggle_detalle_resumen({"cuenta_id": detalle_abierto["cuenta_id"], "account_name": "", "decimales": 2, "currency_symbol": ""})),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        ]
        if not resumen_abierto:
            hijos_seccion.append(
                ft.Text(
                    f"Este resumen ya está '{statement['estado']}' — no se pueden cargar ni eliminar cargos extra.",
                    size=TypographyTokens.LABEL_SIZE, color=ft.Colors.ERROR,
                )
            )
        hijos_seccion += [lista_cargos, ft.Divider(height=1), fila_alta_cargo]

        return ft.Container(
            padding=16,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Column(hijos_seccion, spacing=8),
        )

    def _actualizar_detalle_resumen() -> None:
        contenedor_detalle_resumen.content = _seccion_detalle_resumen()

    # ------------------------------------------------------------
    # FILA DE ALTA (reconstruida por _refrescar_pantalla() — ver docstring)
    # ------------------------------------------------------------

    def _construir_fila_alta() -> ft.Control:
        dropdown_moneda_alta = ft.Dropdown(
            width=_ANCHO_MONEDA, label="Moneda", dense=True, options=[],
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )

        def _refrescar_moneda_alta(cuenta_id: int) -> None:
            saldos = accounts_service.get_account(cuenta_id)["saldos"]
            dropdown_moneda_alta.options = [
                ft.dropdown.Option(key=s["moneda_codigo"], text=s["moneda_codigo"]) for s in saldos
            ]
            dropdown_moneda_alta.value = saldos[0]["moneda_codigo"] if saldos else None

        # Cuenta/Categoría: CampoFiltrable (componente propio) — ver
        # docstring del módulo. El id real seleccionado se lee de
        # campo_cuenta_alta.id_seleccionado / campo_categoria_alta.id_seleccionado
        # (propiedad del propio componente, ya validada internamente
        # contra el texto tipeado), no de ningún .value ni evento de
        # selección crudo.
        # Sin sufijo "(tipo)": cuentas_activas ya está filtrada a solo
        # tipo='credito' (ver _cuentas_credito() más arriba), así que
        # agregarlo sería ruido repetido en cada opción.
        opciones_cuenta_alta = [(str(c["id"]), c["nombre"]) for c in cuentas_activas]
        opciones_categoria_alta = [(str(c["id"]), c["subcategoria"]) for c in categorias]

        def _on_seleccionar_cuenta_alta(id_cuenta: Optional[str]) -> None:
            if id_cuenta is not None:
                _refrescar_moneda_alta(int(id_cuenta))
                page.update()

        def _on_seleccionar_categoria_alta(id_categoria: Optional[str]) -> None:
            # Cuotas se deshabilita SOLO cuando la categoría elegida es una
            # de las 3 especiales (routing a cargo extra, ver docstring del
            # módulo) — ya no según el signo del monto (regla vieja, sacada
            # por completo en esta tarea).
            if id_categoria is not None:
                campo_cuotas_alta.disabled = id_categoria in mapa_categoria_a_charge_type
                page.update()

        campo_concepto_alta = ft.TextField(
            width=_ANCHO_CONCEPTO, label="Comercio / concepto", dense=True,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )
        campo_categoria_alta = CampoFiltrable(
            page,
            opciones_categoria_alta,
            on_seleccionar=_on_seleccionar_categoria_alta,
            placeholder="Categoría",
            valor_inicial_id=opciones_categoria_alta[0][0] if opciones_categoria_alta else None,
            width=_ANCHO_CATEGORIA,
            text_size=TypographyTokens.FILTER_SIZE,
            on_avanzar=lambda: campo_monto_alta.focus(),
        )
        campo_cuenta_alta = CampoFiltrable(
            page,
            opciones_cuenta_alta,
            on_seleccionar=_on_seleccionar_cuenta_alta,
            placeholder="Banco",
            valor_inicial_id=opciones_cuenta_alta[0][0] if opciones_cuenta_alta else None,
            width=_ANCHO_BANCO,
            text_size=TypographyTokens.FILTER_SIZE,
            on_avanzar=lambda: campo_categoria_alta.focus(),
        )
        if cuentas_activas:
            _refrescar_moneda_alta(cuentas_activas[0]["id"])

        campo_fecha_alta = ft.TextField(
            width=_ANCHO_FECHA, label="Fecha", dense=True, value=date.today().isoformat(),
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )
        campo_cuotas_alta = ft.TextField(
            width=_ANCHO_CUOTAS, label="Cuotas", dense=True, value="1",
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )
        # Sync inicial defensivo: valor_inicial_id de campo_categoria_alta
        # (arriba) precarga una selección sin pasar por
        # _on_seleccionar_categoria_alta (el constructor de CampoFiltrable
        # no dispara on_seleccionar para su propio preload) — si esa
        # categoría default resultara ser una de las 3 especiales, Cuotas
        # debe arrancar deshabilitada igual. Mismo patrón ya usado un poco
        # más arriba para sincronizar la Moneda default con la Cuenta
        # default (_refrescar_moneda_alta(cuentas_activas[0]["id"])).
        if campo_categoria_alta.id_seleccionado:
            campo_cuotas_alta.disabled = campo_categoria_alta.id_seleccionado in mapa_categoria_a_charge_type

        # Mismo hint_text/text_size que campo_monto_alta del Registro de
        # transacciones (SharedFieldText.HINT_MONTO_CON_SIGNO). El signo
        # ya no decide compra-vs-ajuste (regla vieja, sacada) — ahora lo
        # decide la categoría elegida (ver _confirmar_alta()); el hint
        # sigue siendo "± monto" porque el signo SÍ sigue importando: con
        # una categoría especial puede ser negativo (reintegro), con una
        # categoría normal debe ser positivo.
        # CampoMonto (ui/components/campo_monto.py, CLAUDE.md §8) — mismo
        # criterio que el Registro de transacciones: resuelve una fórmula
        # "=..." tipeada acá a un número antes de que _confirmar_alta() la
        # reciba, sin cambiar la interpretación del signo/routing por
        # categoría (persistir_formula=False default, on_confirmar no-op —
        # la fila entera confirma junta).
        campo_monto_alta = CampoMonto(
            page,
            on_confirmar=lambda monto_minor: None,
            width=_ANCHO_MONTO,
            hint_text=SharedFieldText.HINT_MONTO_CON_SIGNO,
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
            on_avanzar=lambda: campo_cuotas_alta.focus(),
        )

        def _confirmar_alta(e: Optional[ft.ControlEvent] = None) -> None:
            if not cuentas_activas:
                _mostrar_error("Primero cargá una tarjeta de crédito en Configuración → Cuentas.")
                return
            if not categorias:
                _mostrar_error("No hay categorías de egreso cargadas.")
                return
            if not campo_concepto_alta.value or not campo_concepto_alta.value.strip():
                _mostrar_error("El comercio/concepto no puede estar vacío.")
                return
            try:
                monto_con_signo = float((campo_monto_alta.texto or "").strip().replace(",", "."))
            except ValueError:
                _mostrar_error("El monto no es un número válido.")
                return
            if monto_con_signo == 0:
                _mostrar_error("El monto no puede ser 0.")
                return
            try:
                datetime.strptime((campo_fecha_alta.value or "").strip(), "%Y-%m-%d")
            except ValueError:
                _mostrar_error("La fecha debe tener el formato AAAA-MM-DD.")
                return
            if not campo_cuenta_alta.id_seleccionado:
                _mostrar_error("Seleccioná una cuenta de la lista de sugerencias.")
                return
            if not campo_categoria_alta.id_seleccionado:
                _mostrar_error("Seleccioná una categoría de la lista de sugerencias.")
                return
            if not dropdown_moneda_alta.value:
                _mostrar_error("Completá la moneda.")
                return

            fecha_str = campo_fecha_alta.value.strip()
            cuenta_id = int(campo_cuenta_alta.id_seleccionado)
            categoria_id = int(campo_categoria_alta.id_seleccionado)
            # Routing por categoría (Tarea 3) — reemplaza por completo la
            # regla vieja de "negativo = ajuste automático" (cualquier
            # categoría, cualquier signo). Ahora el signo por sí solo no
            # decide nada: lo decide si la categoría elegida es una de las
            # 3 especiales de CATEGORIAS_CARGO_EXTRA (ver
            # services/fees_service.py).
            charge_type = mapa_categoria_a_charge_type.get(campo_categoria_alta.id_seleccionado)

            if charge_type is None:
                # Categoría normal: comportamiento de siempre, monto
                # siempre positivo — un negativo ya no es un camino válido
                # (antes significaba "ajuste automático", esa regla se sacó
                # por completo).
                if monto_con_signo < 0:
                    _mostrar_error(
                        "El monto no puede ser negativo con una categoría normal — "
                        "elegí una categoría especial de tarjeta (Impuesto/Recargo/"
                        "Ajuste-Reintegro) para cargar un ajuste o reintegro."
                    )
                    return
                try:
                    cantidad_cuotas = int((campo_cuotas_alta.value or "").strip())
                except ValueError:
                    _mostrar_error("La cantidad de cuotas debe ser un número entero.")
                    return
                if cantidad_cuotas < 1:
                    _mostrar_error("La cantidad de cuotas debe ser al menos 1.")
                    return
                try:
                    resultado = fees_service.create_purchase(
                        date_str=fecha_str,
                        concept=campo_concepto_alta.value.strip(),
                        account_id=cuenta_id,
                        category_id=categoria_id,
                        currency_code=dropdown_moneda_alta.value,
                        total_amount=monto_con_signo,
                        total_fees=cantidad_cuotas,
                    )
                except (FeesError, ValueError) as err:
                    _mostrar_error(str(err))
                    return
                _mostrar_ok(f"Compra #{resultado.entity_id} registrada en {cantidad_cuotas} cuota(s).")
            else:
                # Categoría especial: cargo extra del resumen, NO una
                # compra en cuotas — Cuotas ya está deshabilitado en la UI
                # (ver _on_seleccionar_categoria_alta()), acá no se lee su
                # valor. amount_minor viaja con el signo tal cual se
                # tipeó (positivo o negativo son ambos válidos: un
                # impuesto/recargo suele ser positivo, un
                # ajuste/reintegro suele ser negativo, pero no se fuerza
                # ninguno de los dos — la categoría ya clasificó el TIPO
                # de cargo, no su signo).
                dt = datetime.strptime(fecha_str, "%Y-%m-%d")
                try:
                    resultado_resumen = fees_service.open_statement(
                        account_id=cuenta_id, month=dt.month, year=dt.year,
                    )
                except (FeesError, ValueError) as err:
                    _mostrar_error(str(err))
                    return

                decimales = monedas_por_codigo.get(dropdown_moneda_alta.value, {}).get("decimales", 2)
                monto_minor = amount_to_minor(monto_con_signo, decimales)

                try:
                    fees_service.add_extra_charge(
                        statement_id=resultado_resumen.entity_id,
                        concept=campo_concepto_alta.value.strip(),
                        charge_type=charge_type,
                        amount_minor=monto_minor,
                    )
                except (FeesError, ValueError) as err:
                    # Cubre StatementAlreadyClosedError/StatementAlreadyPaidError
                    # (subclases de FeesError) — el mensaje de esas excepciones
                    # ya explica cuál de las dos pasó, se muestra tal cual: no
                    # hay forma de cargar un cargo extra retroactivo sobre un
                    # resumen ya cerrado con la lógica actual, y eso hay que
                    # verlo, no ocultarlo.
                    _mostrar_error(str(err))
                    return
                _mostrar_ok(f"Cargo extra ({charge_type}) registrado en el resumen de {dt.month:02d}/{dt.year}.")

            # Refresco liviano a propósito (NO _refrescar_pantalla()): una
            # alta no debe resetear el período ni la barra de herramientas,
            # solo la tabla y la tarjeta de desglose (que puede cambiar si
            # la compra cae en el mes ya seleccionado) — ver docstring del
            # módulo. La fila de alta en sí no se limpia sola hoy (mismo
            # comportamiento que ya tenía esta pantalla antes de esta
            # tarea, fuera de alcance cambiarlo acá).
            _refrescar_datos()

        # Encadenado de foco por teclado (Enter avanza al siguiente campo;
        # el último dispara el mismo guardado que el botón) — CampoFiltrable
        # es un TextField real por dentro, así que Cuenta/Categoría SÍ
        # pueden participar de la cadena.
        campo_concepto_alta.on_submit = lambda e: campo_cuenta_alta.focus()
        campo_cuotas_alta.on_submit = lambda e: campo_fecha_alta.focus()
        campo_fecha_alta.on_submit = lambda e: dropdown_moneda_alta.focus()
        # Dropdown no tiene on_submit real en Flet 0.86.5 — on_select en su lugar.
        dropdown_moneda_alta.on_select = _confirmar_alta

        # Orden alineado con encabezado_columnas (Concepto, Banco, Categoría,
        # Monto, Cuotas, Fecha, Moneda).
        return ft.Row(
            [
                campo_concepto_alta,
                campo_cuenta_alta.control,
                campo_categoria_alta.control,
                campo_monto_alta.control,
                campo_cuotas_alta,
                campo_fecha_alta,
                dropdown_moneda_alta,
                ft.IconButton(
                    icon=ft.Icons.CHECK_CIRCLE,
                    icon_color=ft.Colors.PRIMARY,
                    tooltip="Agregar",
                    on_click=_confirmar_alta,
                ),
            ],
            spacing=_ESPACIADO_FILA,
        )

    # ------------------------------------------------------------
    # TABLA DE COMPRAS EXISTENTES (solo lectura salvo el ícono Compartir)
    # ------------------------------------------------------------

    tabla_body = ft.Column(spacing=4)

    def _texto_celda(texto: str, weight=None) -> ft.Text:
        return ft.Text(
            texto,
            weight=weight or TypographyTokens.TABLE_CONTENT_WEIGHT_REGULAR,
            size=TypographyTokens.TABLE_CONTENT_SIZE,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=texto,
        )

    def _fila_compra(c: dict) -> ft.Control:
        cuenta = cuentas_por_id.get(c["cuenta_id"])

        icono_compartir, ya_compartido = compartir_compra.build_icon(
            page, shared_expenses_service, fees_service, c, _refrescar_datos,
        )
        celda_compartir = ft.Container(
            width=_ANCHO_COMPARTIR,
            content=icono_compartir,
            opacity=1.0 if ya_compartido else 0.0,
        )

        fila_contenido = ft.Row(
            [
                ft.Container(width=_ANCHO_CONCEPTO, padding=4, content=_texto_celda(c["concepto"])),
                ft.Container(
                    width=_ANCHO_BANCO,
                    padding=4,
                    content=ft.Row(
                        [
                            color_chip(cuenta["color_hex"] if cuenta else None),
                            _texto_celda(c["account_name"]),
                        ],
                        spacing=6,
                    ),
                ),
                ft.Container(width=_ANCHO_CATEGORIA, padding=4, content=_texto_celda(c["category_name"])),
                ft.Container(
                    width=_ANCHO_MONTO,
                    padding=4,
                    content=_texto_celda(
                        amount_display(c["monto_total_minor"], c["decimales"], ""),
                        weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                    ),
                ),
                ft.Container(width=_ANCHO_CUOTAS, padding=4, content=_texto_celda(str(c["total_cuotas"]))),
                ft.Container(width=_ANCHO_FECHA, padding=4, content=_texto_celda(c["fecha_compra"])),
                ft.Container(width=_ANCHO_MONEDA, padding=4, content=_texto_celda(c["currency_code"])),
                celda_compartir,
            ],
            spacing=_ESPACIADO_FILA,
        )

        def _on_hover_fila(e: ft.ControlEvent) -> None:
            # Normalizado defensivamente — mismo patrón sin confirmar que
            # ui/components/registro_transacciones.py (ver su docstring).
            hover_activo = str(e.data).lower() == "true"
            celda_compartir.opacity = 1.0 if (hover_activo or ya_compartido) else 0.0
            page.update()

        return ft.Container(
            padding=ft.Padding.symmetric(vertical=4, horizontal=0),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=4,
            content=fila_contenido,
            on_hover=_on_hover_fila,
        )

    def _cargar_compras() -> list:
        """
        FeesService.list_purchases() filtra cuenta/estado/moneda de forma
        nativa — mes/año, categoría y búsqueda de texto se aplican acá
        client-side sobre lo ya traído (ver docstring del módulo).
        """
        compras = fees_service.list_purchases(
            account_id=estado["filtro_banco"],
            per_page=_LIMITE_COMPRAS_DEL_MES,
        )
        mes_str = f"{estado['anio']:04d}-{estado['mes']:02d}"
        compras = [c for c in compras if c["fecha_compra"][:7] == mes_str]
        if estado["filtro_categoria"] is not None:
            compras = [c for c in compras if c["categoria_id"] == estado["filtro_categoria"]]
        texto_busqueda = (estado["busqueda"] or "").strip().lower()
        if texto_busqueda:
            compras = [c for c in compras if texto_busqueda in c["concepto"].lower()]
        return compras

    def _actualizar_tabla() -> None:
        compras = _cargar_compras()
        if compras:
            tabla_body.controls = [_fila_compra(c) for c in compras]
        else:
            tabla_body.controls = [
                ft.Text("No hay compras en cuotas para este período/filtro.", italic=True, color=ft.Colors.OUTLINE)
            ]

    def _header(texto: str, width: int) -> ft.Text:
        return ft.Text(
            texto,
            size=TypographyTokens.TABLE_HEADER_SIZE,
            weight=TypographyTokens.TABLE_HEADER_WEIGHT,
            width=width,
        )

    encabezado_columnas = ft.Row(
        [
            _header("Concepto", _ANCHO_CONCEPTO),
            _header("Banco", _ANCHO_BANCO),
            _header("Categoría", _ANCHO_CATEGORIA),
            _header("Monto total", _ANCHO_MONTO),
            _header("Cuotas", _ANCHO_CUOTAS),
            _header("Fecha", _ANCHO_FECHA),
            _header("Moneda", _ANCHO_MONEDA),
            _header("", _ANCHO_COMPARTIR),
        ],
        spacing=_ESPACIADO_FILA,
    )

    # ------------------------------------------------------------
    # REFRESCO
    # ------------------------------------------------------------
    # Dos niveles a propósito (ver docstring del módulo):
    # _refrescar_datos() (liviano: tarjeta de desglose + tabla) para alta/
    # compartir, _refrescar_pantalla() (pesado: + barra de herramientas +
    # fila de alta) para cualquier cambio en la barra de herramientas.

    contenedor_alta_y_toolbar = ft.Column(spacing=4)

    def _refrescar_datos() -> None:
        _actualizar_desglose()
        _actualizar_detalle_resumen()
        _actualizar_tabla()
        page.update()

    def _refrescar_pantalla() -> None:
        # El resumen expandido (si había uno) es de un mes/año/cuenta
        # puntual — si cambia cualquier cosa de la barra de herramientas
        # (típicamente el período), ya no corresponde seguir mostrándolo
        # expandido.
        detalle_abierto.update(cuenta_id=None, statement_id=None, account_name="", decimales=2, currency_symbol="")
        contenedor_alta_y_toolbar.controls = [
            barra_filtros.build(
                estado,
                on_cambio=_refrescar_pantalla,
                cuentas_filtro=cuentas_credito_todas,
                categorias_filtro=categorias,
                ancho_filtro_banco=_ANCHO_TOOLBAR_FILTRO_BANCO,
                ancho_filtro_categoria=_ANCHO_TOOLBAR_FILTRO_CATEGORIA,
                ancho_busqueda=_ANCHO_TOOLBAR_BUSQUEDA,
                espaciado=_ESPACIADO_FILA,
                text_size=TypographyTokens.FILTER_SIZE,
            ),
            ft.Divider(height=1),
            _construir_fila_alta(),
        ]
        _refrescar_datos()

    _refrescar_pantalla()

    fila_titulo = [ft.Text("Compras en cuotas", size=24, weight=ft.FontWeight.BOLD)]
    if on_volver is not None:
        fila_titulo.insert(
            0,
            ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Volver", on_click=lambda e: on_volver()),
        )

    return ft.Column(
        [
            ft.Row(fila_titulo, alignment=ft.MainAxisAlignment.START),
            ft.Container(height=8),
            contenedor_desglose,
            contenedor_detalle_resumen,
            ft.Container(height=8),
            ft.Container(
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=8,
                content=ft.Column(
                    [contenedor_alta_y_toolbar, ft.Divider(), encabezado_columnas, tabla_body],
                    spacing=4,
                ),
            ),
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
