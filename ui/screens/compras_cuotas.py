"""
DeltaBalance — ui/screens/compras_cuotas.py

Pantalla de Compras en cuotas: fila de alta fija arriba (campos en línea,
monto CON SIGNO) + tabla de compras existentes abajo (FeesService.list_purchases()).
Reusa el patrón visual de fila de tabla de ui/components/color_chip.py — no
la lógica de guardado del Registro de transacciones del dashboard, que es
demasiado distinta (acá el signo dispara compra-nueva vs. ajuste-de-resumen,
no gasto-vs-ingreso). Sin edición inline en esta tarea (alcanza con listar y
dar de alta) — si se necesita editar más adelante, es tarea aparte
reusando el mismo patrón del Registro.

Lógica según el signo del monto al confirmar la alta:
- POSITIVO: compra nueva, vía FeesService.create_purchase() (monto y
  cantidad de cuotas tal cual se cargaron).
- NEGATIVO: reintegro/ajuste, NO una compra nueva. El campo de cuotas se
  deshabilita (no aplica). Resuelve el resumen de tarjeta de esa cuenta y
  el mes/año de la fecha cargada vía FeesService.open_statement()
  (idempotente), y carga el ajuste con
  FeesService.add_extra_charge(statement_id, concept=..., charge_type='ajuste',
  amount_minor=<negativo>). Si el resumen ya está cerrado/pagado,
  add_extra_charge() lanza StatementAlreadyClosedError/StatementAlreadyPaidError
  (subclases de FeesError) — se muestra ese mensaje tal cual en un SnackBar,
  no hay forma de cargar un ajuste retroactivo sobre un resumen ya cerrado
  con la lógica actual.

Reglas de arquitectura: solo AccountsService/CategoriasService/FeesService —
nunca repositories/ ni db/ directo (CLAUDE.md §2/§3). amount_minor para
add_extra_charge() se calcula acá con utils.money.amount_to_minor() (no
db.database.to_minor(), que viviría del lado prohibido de la frontera ui/↔db/).

Diálogos/SnackBar/botones: mismas convenciones de Flet 0.86.5 que el resto
de ui/ — ver docs/FLET_API_NOTES.md. Esta pantalla no abre AlertDialog.
"""

from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.fees_service import FeesService, FeesError
from ui.components.color_chip import color_chip
from utils.money import amount_display, amount_to_minor

_ANCHO_CONCEPTO = 170
_ANCHO_BANCO = 150
_ANCHO_CATEGORIA = 170
_ANCHO_MONTO = 110
_ANCHO_CUOTAS = 80
_ANCHO_FECHA = 110
_ANCHO_MONEDA = 70


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    fees_service: FeesService,
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

    cuentas_todas = accounts_service.list_accounts(solo_activas=False)
    cuentas_por_id = {c["id"]: c for c in cuentas_todas}
    # Tarjetas de crédito primero como ayuda visual — FeesService no exige
    # tipo='credito', así que no se restringe acá, solo se ordena.
    cuentas_activas = sorted(
        accounts_service.list_accounts(solo_activas=True),
        key=lambda c: c["tipo"] != "credito",
    )
    categorias = categorias_service.list_categories(tipo="egreso")
    monedas_por_codigo = {m["codigo"]: m for m in accounts_service.list_currencies()}

    # ------------------------------------------------------------
    # FILA DE ALTA
    # ------------------------------------------------------------

    dropdown_moneda_alta = ft.Dropdown(width=_ANCHO_MONEDA, label="Moneda", dense=True, options=[])

    def _refrescar_moneda_alta(cuenta_id: int) -> None:
        saldos = accounts_service.get_account(cuenta_id)["saldos"]
        dropdown_moneda_alta.options = [
            ft.dropdown.Option(key=s["moneda_codigo"], text=s["moneda_codigo"]) for s in saldos
        ]
        dropdown_moneda_alta.value = saldos[0]["moneda_codigo"] if saldos else None

    def _on_select_cuenta_alta(e: ft.ControlEvent) -> None:
        _refrescar_moneda_alta(int(dropdown_cuenta_alta.value))
        page.update()

    dropdown_cuenta_alta = ft.Dropdown(
        width=_ANCHO_BANCO,
        label="Cuenta",
        dense=True,
        options=[
            ft.dropdown.Option(key=str(c["id"]), text=f"{c['nombre']} ({c['tipo']})")
            for c in cuentas_activas
        ],
        value=str(cuentas_activas[0]["id"]) if cuentas_activas else None,
        on_select=_on_select_cuenta_alta,
    )
    if cuentas_activas:
        _refrescar_moneda_alta(cuentas_activas[0]["id"])

    dropdown_categoria_alta = ft.Dropdown(
        width=_ANCHO_CATEGORIA,
        label="Categoría",
        dense=True,
        options=[
            ft.dropdown.Option(key=str(c["id"]), text=f"{c['categoria_principal']} · {c['subcategoria']}")
            for c in categorias
        ],
        value=str(categorias[0]["id"]) if categorias else None,
    )

    campo_concepto_alta = ft.TextField(width=_ANCHO_CONCEPTO, label="Comercio / concepto", dense=True)
    campo_fecha_alta = ft.TextField(width=_ANCHO_FECHA, label="Fecha", dense=True, value=date.today().isoformat())
    campo_cuotas_alta = ft.TextField(width=_ANCHO_CUOTAS, label="Cuotas", dense=True, value="1")

    def _on_change_monto(e: ft.ControlEvent) -> None:
        # Negativo = reintegro/ajuste, no compra nueva — la cantidad de
        # cuotas no aplica, se grisa como señal visual (pedido explícito).
        es_negativo = (campo_monto_alta.value or "").strip().startswith("-")
        campo_cuotas_alta.disabled = es_negativo
        page.update()

    campo_monto_alta = ft.TextField(
        width=_ANCHO_MONTO,
        label="Monto (+ compra / - ajuste)",
        dense=True,
        on_change=_on_change_monto,
    )

    def _confirmar_alta(e: ft.ControlEvent) -> None:
        if not cuentas_activas:
            _mostrar_error("Primero cargá una cuenta en Configuración → Cuentas.")
            return
        if not categorias:
            _mostrar_error("No hay categorías de egreso cargadas.")
            return
        if not campo_concepto_alta.value or not campo_concepto_alta.value.strip():
            _mostrar_error("El comercio/concepto no puede estar vacío.")
            return
        try:
            monto_con_signo = float((campo_monto_alta.value or "").strip().replace(",", "."))
        except ValueError:
            _mostrar_error("El monto no es un número válido.")
            return
        if monto_con_signo == 0:
            _mostrar_error("El monto no puede ser 0 — positivo es compra nueva, negativo es ajuste.")
            return
        try:
            datetime.strptime((campo_fecha_alta.value or "").strip(), "%Y-%m-%d")
        except ValueError:
            _mostrar_error("La fecha debe tener el formato AAAA-MM-DD.")
            return
        if not dropdown_cuenta_alta.value or not dropdown_categoria_alta.value or not dropdown_moneda_alta.value:
            _mostrar_error("Completá cuenta, categoría y moneda.")
            return

        fecha_str = campo_fecha_alta.value.strip()

        if monto_con_signo > 0:
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
                    account_id=int(dropdown_cuenta_alta.value),
                    category_id=int(dropdown_categoria_alta.value),
                    currency_code=dropdown_moneda_alta.value,
                    total_amount=monto_con_signo,
                    total_fees=cantidad_cuotas,
                )
            except (FeesError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _mostrar_ok(f"Compra #{resultado.entity_id} registrada en {cantidad_cuotas} cuota(s).")
        else:
            dt = datetime.strptime(fecha_str, "%Y-%m-%d")
            try:
                resultado_resumen = fees_service.open_statement(
                    account_id=int(dropdown_cuenta_alta.value), month=dt.month, year=dt.year,
                )
            except (FeesError, ValueError) as err:
                _mostrar_error(str(err))
                return

            decimales = monedas_por_codigo.get(dropdown_moneda_alta.value, {}).get("decimales", 2)
            monto_minor = amount_to_minor(monto_con_signo, decimales)  # ya negativo

            try:
                fees_service.add_extra_charge(
                    statement_id=resultado_resumen.entity_id,
                    concept=campo_concepto_alta.value.strip(),
                    charge_type="ajuste",
                    amount_minor=monto_minor,
                )
            except (FeesError, ValueError) as err:
                # Cubre StatementAlreadyClosedError/StatementAlreadyPaidError
                # (subclases de FeesError) — el mensaje de esas excepciones
                # ya explica cuál de las dos pasó, se muestra tal cual: no
                # hay forma de cargar un ajuste retroactivo sobre un resumen
                # ya cerrado con la lógica actual, y eso hay que verlo, no
                # ocultarlo.
                _mostrar_error(str(err))
                return
            _mostrar_ok(f"Ajuste registrado en el resumen de {dt.month:02d}/{dt.year}.")

        _refrescar_tabla()

    fila_alta = ft.Row(
        [
            dropdown_cuenta_alta,
            campo_concepto_alta,
            dropdown_categoria_alta,
            campo_monto_alta,
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
        spacing=8,
        wrap=True,
    )

    # ------------------------------------------------------------
    # TABLA DE COMPRAS EXISTENTES (solo lectura)
    # ------------------------------------------------------------

    tabla_body = ft.Column(spacing=4)

    def _fila_compra(c: dict) -> ft.Control:
        cuenta = cuentas_por_id.get(c["cuenta_id"])
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=4, horizontal=0),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=4,
            content=ft.Row(
                [
                    ft.Container(width=_ANCHO_CONCEPTO, padding=4, content=ft.Text(c["concepto"])),
                    ft.Container(
                        width=_ANCHO_BANCO,
                        padding=4,
                        content=ft.Row(
                            [color_chip(cuenta["color_hex"] if cuenta else None), ft.Text(c["account_name"])],
                            spacing=6,
                        ),
                    ),
                    ft.Container(width=_ANCHO_CATEGORIA, padding=4, content=ft.Text(c["category_name"])),
                    ft.Container(
                        width=_ANCHO_MONTO,
                        padding=4,
                        content=ft.Text(
                            amount_display(c["monto_total_minor"], c["decimales"], ""),
                            weight=ft.FontWeight.BOLD,
                        ),
                    ),
                    ft.Container(width=_ANCHO_CUOTAS, padding=4, content=ft.Text(str(c["total_cuotas"]))),
                    ft.Container(width=_ANCHO_FECHA, padding=4, content=ft.Text(c["fecha_compra"])),
                    ft.Container(width=_ANCHO_MONEDA, padding=4, content=ft.Text(c["currency_code"])),
                ],
                spacing=8,
            ),
        )

    def _refrescar_tabla() -> None:
        compras = fees_service.list_purchases(per_page=200)
        if compras:
            tabla_body.controls = [_fila_compra(c) for c in compras]
        else:
            tabla_body.controls = [
                ft.Text("No hay compras en cuotas cargadas.", italic=True, color=ft.Colors.OUTLINE)
            ]
        page.update()

    encabezado_columnas = ft.Row(
        [
            ft.Text("Concepto", size=11, weight=ft.FontWeight.BOLD, width=_ANCHO_CONCEPTO),
            ft.Text("Banco", size=11, weight=ft.FontWeight.BOLD, width=_ANCHO_BANCO),
            ft.Text("Categoría", size=11, weight=ft.FontWeight.BOLD, width=_ANCHO_CATEGORIA),
            ft.Text("Monto total", size=11, weight=ft.FontWeight.BOLD, width=_ANCHO_MONTO),
            ft.Text("Cuotas", size=11, weight=ft.FontWeight.BOLD, width=_ANCHO_CUOTAS),
            ft.Text("Fecha", size=11, weight=ft.FontWeight.BOLD, width=_ANCHO_FECHA),
            ft.Text("Moneda", size=11, weight=ft.FontWeight.BOLD, width=_ANCHO_MONEDA),
        ],
        spacing=8,
    )

    _refrescar_tabla()

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
            ft.Container(
                padding=16,
                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                border_radius=8,
                content=ft.Column(
                    [fila_alta, ft.Divider(), encabezado_columnas, tabla_body],
                    spacing=4,
                ),
            ),
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
