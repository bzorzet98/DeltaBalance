"""
DeltaBalance — ui/screens/deudas_y_compartidos.py

Pantalla combinada Deudas informales / Gastos compartidos del hogar —
Tarea 9, Parte B (docs/PROXIMOS_PASOS.md). Toggle arriba entre las dos
vistas (dos ft.TextButton actuando como segmented control — ver "Toggle"
más abajo), cada una con su propio dashboard + barra de filtros + tabla,
mismo patrón "estilo planilla" ya usado en
ui/screens/compras_cuotas.py/ui/components/registro_transacciones.py.

--- Toggle ---
Ni ft.Tabs ni un segmented button real están confirmados contra Flet
0.86.5 en docs/FLET_API_NOTES.md — la regla de ese documento es no
arriesgar una API sin confirmar. Se usan dos ft.TextButton (uno por
vista) que cambian de estilo según cuál está activa y reconstruyen
`contenedor_vista` — control mínimo, sin superficie de API nueva que
pueda fallar en runtime.

--- Vista "Deudas informales" ---
Dashboard: saldo neto por persona, agrupado también por moneda (nunca se
mezclan monedas distintas en una suma, mismo criterio que el resto de la
app — ver DashboardService.get_gasto_por_categoria()). NO se agregó un
método de agregación nuevo en DebtsService para esto: se arma acá mismo,
client-side, sobre list_active() (todas las deudas 'activa', sin los
filtros propios de la tabla de abajo — el dashboard es un total global,
igual que "Patrimonio total" en el Dashboard no se filtra por lo que esté
tipeado en el Registro). DebtsService.summary_by_person() YA EXISTE pero
no sirve tal cual: agrupa por (persona, tipo) por separado, para un
saldo NETO por persona con signo (a_favor +, en_contra -) hace falta
combinar ambos tipos en una sola cifra por persona — más simple sumarlo
acá que cambiar la firma de un método ya usado en otro lado.

Fila de alta: SÍ existe acá (a diferencia de Gastos compartidos, ver
abajo) — deudas.origen_tipo soporta 'manual' como default
(DebtsService.create()), así que una deuda informal se puede cargar desde
cero sin depender de ninguna otra entidad.

--- Vista "Gastos compartidos del hogar" ---
Dashboard: SharedExpensesService.get_net_balance(hogar_id) ya devuelve el
número + el mensaje de presentación armado ("Te deben"/"Debés"/"Están a
mano") — se usa tal cual, sin agregación nueva.

SIN fila de alta acá, a propósito: gastos_compartidos.origen_tipo tiene un
CHECK que solo admite 'transaccion'/'compra_cuotas'/'cuota_credito' (ver
db/schema.sql) — nunca 'manual'. Un gasto compartido SIEMPRE nace de una
transacción o compra ya cargada en otro lado (ícono "Compartir" del
Registro o de Compras en cuotas, ver ui/components/compartir_gasto.py/
compartir_compra.py) — no hay forma de crear uno desde cero, así que esta
pantalla es de gestión (editar / pagar / eliminar), nunca de alta.

Onboarding sin hogar: reusa abrir_dialogo_sin_hogar()/obtener_usuario_local()
de ui/components/usuario_local.py (el mismo mecanismo que ya usan
compartir_gasto.py/compartir_compra.py) — no se duplica el diálogo de
"crear/unirse a un hogar" acá.

BUG REAL corregido acá (root cause confirmado por lectura del código
fuente de Flet 0.86.5 realmente instalado, no por conjetura — ver
docstring completo de ui/components/usuario_local.py): la causa de que
"Crear hogar" no hiciera nada visible NO era un problema de orden de
page.update() (dos intentos previos en esa dirección no sirvieron de
nada, correctamente descartados) — era que obtener_usuario_local()/
guardar_usuario_local() usaban page.client_storage, una API que
directamente NO EXISTE en el Flet 0.86.5 instalado (cero coincidencias en
todo el paquete). Cada llamada lanzaba AttributeError, atrapado en
silencio — así que la identidad del usuario nunca se guardaba de verdad,
en NINGÚN lugar de la app. El fallback de compartir_gasto.py ante "no
tengo hogar todavía" (reabrir el mismo diálogo) hacía que el mismo bug se
viera ahí como "algo pasa" en vez de "no pasa nada" — de ahí la asimetría
reportada. Corregido usando la API real (ft.SharedPreferences, async) —
ver usuario_local.py. Por eso _tiene_hogar()/_construir_vista_gastos()/
_refrescar_todo()/_cambiar_vista() son ahora `async def`: obtener_usuario_
local() ya no puede resolverse de forma síncrona. El bootstrap inicial de
build() (más abajo) sigue siendo 100% síncrono a propósito: `vista`
siempre arranca en "deudas", que no depende de nada async.

--- Edición inline y acciones por fila ---
Celdas editables (click para editar, CLAUDE.md §10) — mismo patrón que
ui/screens/compras_cuotas.py/ui/components/registro_transacciones.py,
copiado acá (son closures de esos build(), no se pueden importar).

Deudas: Persona, Concepto y Notas son editables inline
(DebtsService.update()) — reemplazan al diálogo de editar, que se
eliminó. Solo si la deuda está 'activa' (update() rechaza el resto): si
no, las tres se muestran de solo lectura con el motivo en el tooltip.
Acciones de la fila: "Registrar pago" (mini-diálogo: CampoMonto + selector
tipo_pago, llama a register_payment() — la moneda del pago es SIEMPRE la
de la propia deuda, no editable en el diálogo, para no exponer el caso no
cubierto de un pago en una moneda distinta a la de la deuda) y "Marcar
incobrable" (write_off()) — antes vivían dentro del diálogo de editar,
ahora son íconos de la fila, visibles solo si la deuda está 'activa' — y
eliminar (delete_debt()).

Gastos compartidos: Descripción y Fecha son editables inline en cualquier
estado; Monto base (CampoMonto, persistir_formula=True) y Coeficiente (el
usuario tipea 0-1, el service recibe 0-100) solo si el gasto está
'pendiente' — si no, solo lectura con el motivo en el tooltip. Todas
llaman a SharedExpensesService.update_shared_expense(), que además
bloquea monto/coeficiente de un gasto pendiente con pagos parciales
(CLAUDE.md §4) y recalcula adeudado/pendiente. gastos_compartidos no tiene
columna de fórmula, así que la fórmula tipeada en Monto base no sobrevive
al refresco. Acciones de la fila: editar (diálogo de descripción, previo a
la edición inline), eliminar (delete_shared_expense()), "Registrar pago"
(mini-diálogo: CampoMonto + selector tipo_pago, llama a aplicar_pago() —
sin moneda, gastos_compartidos no tiene columna de moneda propia).
"Registrar pago" se deshabilita si el gasto ya está 'saldado'.

NO implementado en esta tarea (diferido, ver docs/PROXIMOS_PASOS.md Tarea 9
Parte C): "pago general" automático repartiendo un ingreso contra varios
pendientes en orden de fecha, y selección múltiple de filas. El botón
"Registrar pago" de acá apunta siempre a UNA fila puntual.

Reglas de arquitectura: solo DebtsService/SharedExpensesService/
AccountsService — nunca repositories/ ni db/ directo (CLAUDE.md §2/§3). El
manejo de errores de la fila de alta de Deudas atrapa (DebtError, ValueError)
para los rechazos de negocio esperados, MÁS un except Exception genérico
como red de contención para DeudaDuplicadaError (repositories/
deudas_repository.py — no se importa acá para no cruzar la frontera
services/↔repositories/ desde ui/, se deja que su mensaje ya legible suba
tal cual) — evita el mismo hueco que tiene hoy
ui/components/registro_transacciones.py (TransaccionDuplicadaError no
está en su except, se deja documentado, no se toca en esta tarea por estar
fuera de alcance).
"""

from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.debts_service import DebtError, DebtsService
from services.shared_expenses_service import (
    GastoCompartidoNotFoundError,
    SharedExpensesError,
    SharedExpensesService,
)
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.components.usuario_local import abrir_dialogo_sin_hogar, obtener_usuario_local
from ui.theme.tokens import LayoutTokens, TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---
_ANCHO_PERSONA = 140
_ANCHO_CONCEPTO = 200
_ANCHO_NOTAS = 180
_ANCHO_DESCRIPCION = 180
_ANCHO_TIPO = 110
_ANCHO_MONTO = 110
_ANCHO_FECHA = 110
_ANCHO_MONEDA = 90
_ANCHO_ESTADO = 100
_ANCHO_ACCIONES = 170
_ANCHO_CATEGORIA = 170
_ANCHO_COEFICIENTE = 90
_ESPACIADO_FILA = 8

_ANCHO_TOOLBAR_FILTRO_PERSONA = 160
_ANCHO_TOOLBAR_FILTRO_ESTADO = 140
_ANCHO_DIALOGO = 360

_LIMITE_FILAS = 500  # tope de per_page al pedir deudas/gastos (se filtra por período client-side)

# Celdas editables inline — mismos valores que
# ui/components/registro_transacciones.py / ui/screens/compras_cuotas.py.
_ANCHO_BOTON_CONFIRMAR = 48
_ANCHO_MINIMO_CAMPO_CELDA = 40
_ICONO_ACCION_FILA = 16
# gastos_compartidos no tiene moneda propia: los montos se muestran y se
# cargan con 2 decimales, mismo criterio que el resto de esta vista.
_DECIMALES_GASTO = 2
# Coeficiente: el usuario tipea 0-1 (0.5), el service recibe 0-100 (50.0),
# redondeado a esta cantidad de decimales para no guardar ruido de float
# (0.3 * 100 = 30.000000000000004).
_DECIMALES_COEFICIENTE = 2

_ESTADOS_DEUDA = ("activa", "saldada", "incobrable")
_ESTADOS_GASTO = ("pendiente", "saldado")
_TIPOS_PAGO = ("transaccion", "compensacion", "ajuste")

_MESES = [
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
]


def build(
    page: ft.Page,
    debts_service: DebtsService,
    shared_expenses_service: SharedExpensesService,
    accounts_service: AccountsService,
    on_volver: Optional[Callable[[], None]] = None,
) -> ft.Control:
    def _mostrar_mensaje(mensaje: str, es_error: bool = False) -> None:
        snack = ft.SnackBar(content=ft.Text(mensaje), bgcolor=ft.Colors.ERROR_CONTAINER if es_error else None)
        page.overlay.append(snack)
        snack.open = True
        page.update()

    def _mostrar_error(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        _mostrar_mensaje(mensaje, es_error=False)

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    hoy = date.today()
    monedas = accounts_service.list_currencies()

    # Estado propio de esta pantalla (mismo criterio que
    # ui/screens/compras_cuotas.py — no recibe ni comparte estado con
    # ningún caller externo).
    vista = {"activa": "deudas"}
    estado_deudas = {
        "mes": hoy.month, "anio": hoy.year, "filtro_persona": None, "filtro_estado": None,
    }
    estado_gastos = {
        "mes": hoy.month, "anio": hoy.year, "filtro_pagador": None, "filtro_estado": None,
    }
    hogar_actual: dict = {"id": None}

    contenedor_vista = ft.Container()

    # ------------------------------------------------------------
    # TOGGLE (ver docstring del módulo)
    # ------------------------------------------------------------

    def _boton_toggle(texto: str, id_vista: str) -> ft.TextButton:
        activo = vista["activa"] == id_vista

        # async: cambiar a "compartidos" dispara _tiene_hogar(), que ahora
        # necesita await (ver docstring del módulo) — no se puede armar
        # con un lambda sync, así que se define una closure async chica
        # capturando id_vista de la misma forma que hacía el lambda
        # (default arg evaluado en el momento de construir el botón).
        async def _on_click(e, i=id_vista) -> None:
            await _cambiar_vista(i)

        return ft.TextButton(
            content=ft.Text(texto, weight=ft.FontWeight.BOLD if activo else ft.FontWeight.NORMAL),
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST if activo else None,
            ),
            on_click=_on_click,
        )

    async def _cambiar_vista(id_vista: str) -> None:
        vista["activa"] = id_vista
        await _refrescar_todo()

    def _fila_toggle() -> ft.Control:
        return ft.Row(
            [
                _boton_toggle("Deudas informales", "deudas"),
                _boton_toggle("Gastos compartidos del hogar", "compartidos"),
            ],
            spacing=4,
        )

    contenedor_toggle = ft.Container()

    # ============================================================
    # VISTA "DEUDAS INFORMALES"
    # ============================================================

    def _cargar_deudas() -> list:
        # dict() antes de pasar a la UI (CLAUDE.md §11).
        deudas = [
            dict(d) for d in debts_service.list_debts(
                estado=estado_deudas["filtro_estado"], per_page=_LIMITE_FILAS,
            )
        ]
        mes_str = f"{estado_deudas['anio']:04d}-{estado_deudas['mes']:02d}"
        deudas = [d for d in deudas if d["fecha_inicio"][:7] == mes_str]
        if estado_deudas["filtro_persona"] is not None:
            deudas = [d for d in deudas if d["entidad_persona"] == estado_deudas["filtro_persona"]]
        return deudas

    def _dashboard_deudas() -> ft.Control:
        """
        Saldo neto por (persona, moneda) sobre TODAS las deudas activas
        (sin los filtros propios de la tabla de abajo) — ver docstring del
        módulo para por qué se arma acá y no en DebtsService.
        """
        activas = debts_service.list_active()
        netos: dict[tuple[str, str], dict] = {}
        for d in activas:
            clave = (d["entidad_persona"], d["currency_code"])
            signo = 1 if d["tipo"] == "a_favor" else -1
            entrada = netos.setdefault(
                clave, {"minor": 0, "decimales": d["decimales"], "simbolo": d["currency_symbol"] or ""},
            )
            entrada["minor"] += signo * d["monto_pendiente_minor"]

        if not netos:
            contenido = ft.Text(
                "Sin deudas activas.", italic=True, color=ft.Colors.OUTLINE, size=TypographyTokens.TABLE_CONTENT_SIZE,
            )
        else:
            tiles = []
            for (persona, moneda_codigo), v in sorted(netos.items()):
                if v["minor"] == 0:
                    continue
                color = ft.Colors.GREEN if v["minor"] > 0 else ft.Colors.RED
                texto_signo = "Te debe" if v["minor"] > 0 else "Le debés a"
                monto_texto = amount_display(abs(v["minor"]), v["decimales"], v["simbolo"])
                tiles.append(
                    ft.Column(
                        [
                            ft.Text(f"{persona} ({moneda_codigo})", size=TypographyTokens.LABEL_SIZE, color=ft.Colors.OUTLINE),
                            ft.Text(
                                f"{texto_signo} {monto_texto}",
                                size=TypographyTokens.TABLE_CONTENT_SIZE,
                                weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                                color=color,
                            ),
                        ],
                        spacing=2,
                    )
                )
            contenido = ft.Row(tiles, spacing=20, wrap=True) if tiles else ft.Text(
                "Todo saldado — nadie debe nada.", italic=True, color=ft.Colors.OUTLINE, size=TypographyTokens.TABLE_CONTENT_SIZE,
            )

        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=8,
            content=ft.Row(
                [
                    ft.Text("Saldo neto por persona", size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT),
                    contenido,
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )

    def _personas_conocidas() -> list[str]:
        todas = debts_service.list_debts(per_page=_LIMITE_FILAS)
        return sorted({d["entidad_persona"] for d in todas})

    def _toolbar_deudas() -> ft.Control:
        def _avanzar(delta: int, e=None) -> None:
            mes = estado_deudas["mes"] + delta
            anio = estado_deudas["anio"]
            if mes < 1:
                mes, anio = 12, anio - 1
            elif mes > 12:
                mes, anio = 1, anio + 1
            estado_deudas["mes"], estado_deudas["anio"] = mes, anio
            _refrescar_toolbar_deudas()

        personas = _personas_conocidas()
        campo_filtro_persona = CampoFiltrable(
            page,
            [(p, p) for p in personas],
            on_seleccionar=lambda pid: (_set_filtro_persona(pid), _refrescar_toolbar_deudas()),
            placeholder="Persona",
            valor_inicial_id=estado_deudas["filtro_persona"],
            width=_ANCHO_TOOLBAR_FILTRO_PERSONA,
            text_size=TypographyTokens.FILTER_SIZE,
        )

        def _set_filtro_persona(pid: Optional[str]) -> None:
            estado_deudas["filtro_persona"] = pid

        def _on_select_estado(e: ft.ControlEvent) -> None:
            estado_deudas["filtro_estado"] = dropdown_estado.value or None
            _refrescar_toolbar_deudas()

        dropdown_estado = ft.Dropdown(
            label="Estado", width=_ANCHO_TOOLBAR_FILTRO_ESTADO, dense=True,
            text_size=TypographyTokens.FILTER_SIZE,
            options=[ft.dropdown.Option(key="", text="Todos")] + [
                ft.dropdown.Option(key=s, text=s) for s in _ESTADOS_DEUDA
            ],
            value=estado_deudas["filtro_estado"] or "",
            on_select=_on_select_estado,
        )

        return ft.Row(
            [
                ft.IconButton(icon=ft.Icons.CHEVRON_LEFT, tooltip="Mes anterior", on_click=lambda e: _avanzar(-1, e)),
                ft.Text(
                    f"{_MESES[estado_deudas['mes'] - 1]} {estado_deudas['anio']}",
                    size=TypographyTokens.FILTER_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT, width=140,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, tooltip="Mes siguiente", on_click=lambda e: _avanzar(1, e)),
                campo_filtro_persona.control,
                dropdown_estado,
            ],
            spacing=_ESPACIADO_FILA,
        )

    # --- Fila de alta (deudas) ---

    def _construir_fila_alta_deudas() -> ft.Control:
        campo_persona = ft.TextField(width=_ANCHO_PERSONA, label="Persona", dense=True, text_size=TypographyTokens.TABLE_CONTENT_SIZE)
        campo_concepto = ft.TextField(width=_ANCHO_CONCEPTO, label="Concepto", dense=True, text_size=TypographyTokens.TABLE_CONTENT_SIZE)
        dropdown_tipo = ft.Dropdown(
            width=_ANCHO_TIPO, label="Tipo", dense=True, text_size=TypographyTokens.TABLE_CONTENT_SIZE,
            options=[
                ft.dropdown.Option(key="a_favor", text="Me deben"),
                ft.dropdown.Option(key="en_contra", text="Debo"),
            ],
            value="a_favor",
        )
        campo_fecha = ft.TextField(
            width=_ANCHO_FECHA, label="Fecha", dense=True, value=date.today().isoformat(),
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )
        dropdown_moneda = ft.Dropdown(
            width=_ANCHO_MONEDA, label="Moneda", dense=True, text_size=TypographyTokens.TABLE_CONTENT_SIZE,
            options=[ft.dropdown.Option(key=m["codigo"], text=m["codigo"]) for m in monedas],
            value=monedas[0]["codigo"] if monedas else None,
        )
        campo_monto = CampoMonto(
            page,
            on_confirmar=lambda monto_minor: None,
            width=_ANCHO_MONTO,
            hint_text="monto",
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )

        def _confirmar_alta(e: Optional[ft.ControlEvent] = None) -> None:
            # Deshabilita el botón ANTES de procesar — mismo mecanismo
            # anti doble-click/doble-Enter que registro_transacciones.py/
            # compras_cuotas.py.
            boton_confirmar.disabled = True
            page.update()
            try:
                _procesar_alta()
            finally:
                boton_confirmar.disabled = False
                page.update()

        def _procesar_alta() -> None:
            if not campo_persona.value or not campo_persona.value.strip():
                _mostrar_error("El nombre de la persona no puede estar vacío.")
                return
            if not campo_concepto.value or not campo_concepto.value.strip():
                _mostrar_error("El concepto no puede estar vacío.")
                return
            try:
                monto = float((campo_monto.texto or "").strip().replace(",", "."))
            except ValueError:
                _mostrar_error("El monto no es un número válido.")
                return
            if monto <= 0:
                _mostrar_error("El monto debe ser positivo.")
                return
            try:
                datetime.strptime((campo_fecha.value or "").strip(), "%Y-%m-%d")
            except ValueError:
                _mostrar_error("La fecha debe tener el formato AAAA-MM-DD.")
                return
            if not dropdown_moneda.value:
                _mostrar_error("Completá la moneda.")
                return

            try:
                resultado = debts_service.create(
                    person=campo_persona.value.strip(),
                    debt_type=dropdown_tipo.value,
                    amount=monto,
                    currency_code=dropdown_moneda.value,
                    date_str=campo_fecha.value.strip(),
                    concept=campo_concepto.value.strip(),
                )
            except (DebtError, ValueError) as err:
                _mostrar_error(str(err))
                return
            except Exception as err:
                # Red de contención para DeudaDuplicadaError (repositories/
                # deudas_repository.py) — ver docstring del módulo, no se
                # importa esa excepción acá para no cruzar la frontera
                # services/↔repositories/ desde ui/.
                _mostrar_error(str(err))
                return

            _mostrar_ok(f"Deuda #{resultado.debt_id} registrada.")
            _refrescar_vista_deudas()

        campo_persona.on_submit = lambda e: campo_concepto.focus()
        campo_concepto.on_submit = lambda e: campo_fecha.focus()
        campo_fecha.on_submit = lambda e: dropdown_moneda.focus()
        dropdown_moneda.on_select = _confirmar_alta

        boton_confirmar = ft.IconButton(
            icon=ft.Icons.CHECK_CIRCLE, icon_color=ft.Colors.PRIMARY, tooltip="Agregar", on_click=_confirmar_alta,
        )

        return ft.Row(
            [campo_persona, campo_concepto, dropdown_tipo, campo_monto.control, campo_fecha, dropdown_moneda, boton_confirmar],
            spacing=_ESPACIADO_FILA,
        )

    # --- Mini-diálogos (deudas) ---

    def _dialogo_registrar_pago_deuda(d: dict) -> None:
        campo_monto_pago = CampoMonto(
            page, on_confirmar=lambda monto_minor: None, hint_text="monto", width=160,
        )
        dropdown_tipo_pago = ft.Dropdown(
            label="Tipo de pago", width=160,
            options=[ft.dropdown.Option(key=t, text=t) for t in _TIPOS_PAGO],
            value="transaccion",
        )
        campo_fecha_pago = ft.TextField(label="Fecha", value=date.today().isoformat(), width=160)

        def _confirmar(e=None) -> None:
            try:
                monto = float((campo_monto_pago.texto or "").strip().replace(",", "."))
            except ValueError:
                _mostrar_mensaje("El monto no es un número válido.", es_error=True)
                return
            if monto <= 0:
                _mostrar_mensaje("El monto debe ser positivo.", es_error=True)
                return
            try:
                datetime.strptime((campo_fecha_pago.value or "").strip(), "%Y-%m-%d")
            except ValueError:
                _mostrar_mensaje("La fecha debe tener el formato AAAA-MM-DD.", es_error=True)
                return
            try:
                resultado = debts_service.register_payment(
                    debt_id=d["id"], amount=monto, currency_code=d["currency_code"],
                    date_str=campo_fecha_pago.value.strip(), payment_type=dropdown_tipo_pago.value,
                )
            except (DebtError, ValueError) as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_ok(resultado.message)
            _refrescar_vista_deudas()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Registrar pago — {d['entidad_persona']}"),
            content=ft.Container(
                width=_ANCHO_DIALOGO,
                content=ft.Column(
                    [
                        ft.Text(f"Pendiente actual: {amount_display(d['monto_pendiente_minor'], d['decimales'], d['currency_symbol'] or '')}"),
                        campo_monto_pago.control, dropdown_tipo_pago, campo_fecha_pago,
                    ],
                    tight=True, spacing=10,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Confirmar"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _dialogo_incobrable(d: dict) -> None:
        campo_notas = ft.TextField(label="Motivo (opcional)", width=300)

        def _confirmar(e=None) -> None:
            try:
                debts_service.write_off(d["id"], notes=campo_notas.value or None)
            except DebtError as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_ok(f"Deuda #{d['id']} marcada como incobrable.")
            _refrescar_vista_deudas()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Marcar incobrable"),
            content=ft.Container(
                width=_ANCHO_DIALOGO,
                content=ft.Column(
                    [ft.Text(f"¿Marcar la deuda de '{d['entidad_persona']}' como incobrable?"), campo_notas],
                    tight=True, spacing=10,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Marcar incobrable"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _confirmar_eliminar_deuda(d: dict) -> None:
        def _eliminar(e=None) -> None:
            try:
                debts_service.delete_debt(d["id"])
            except DebtError as err:
                _cerrar_dialogo()
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            _mostrar_ok(f"Deuda #{d['id']} eliminada.")
            _refrescar_vista_deudas()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Eliminar deuda"),
            content=ft.Text(f"¿Eliminar la deuda de '{d['entidad_persona']}'? Esta acción no se puede deshacer."),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Eliminar"), on_click=_eliminar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    # --- Tabla (deudas) ---

    def _texto_celda(texto: str, weight=None, color=None) -> ft.Text:
        return ft.Text(
            texto, weight=weight or TypographyTokens.TABLE_CONTENT_WEIGHT_REGULAR,
            size=TypographyTokens.TABLE_CONTENT_SIZE, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
            tooltip=texto, color=color,
        )

    # ------------------------------------------------------------
    # CELDAS EDITABLES INLINE (deudas y gastos compartidos) — mismo patrón
    # que _celda_texto()/_celda_monto() de ui/screens/compras_cuotas.py y
    # ui/components/registro_transacciones.py (CLAUDE.md §10). No se
    # re-renderizan solas tras un guardado exitoso: on_confirmar() dispara
    # el refresco liviano de su vista, que reconstruye la tabla ya con el
    # valor nuevo. Solo vuelven a modo lectura acá si on_confirmar() lanza
    # uno de _ERRORES_EDICION.
    # ------------------------------------------------------------

    _ERRORES_EDICION = (DebtError, SharedExpensesError, ValueError)

    def _contenedor_celda(width: int) -> ft.Container:
        return ft.Container(
            width=width,
            height=LayoutTokens.ALTURA_FILA_TABLA,
            padding=LayoutTokens.PADDING_CELDA,
        )

    def _contenido_lectura(texto_control: ft.Control, on_click: Callable[[], None]) -> ft.Control:
        return ft.Container(
            content=texto_control,
            on_click=lambda e: on_click(),
            ink=True,
            padding=LayoutTokens.PADDING_CELDA,
            alignment=ft.Alignment.CENTER_LEFT,
        )

    def _boton_confirmar_celda(on_click=None) -> ft.IconButton:
        # Ícono y padding reducidos: con los default de Material (40px) el
        # botón no entra en la celda de alto fijo (ver LayoutTokens).
        return ft.IconButton(
            icon=ft.Icons.CHECK,
            icon_color=ft.Colors.PRIMARY,
            icon_size=LayoutTokens.ICONO_BOTON_CELDA,
            style=ft.ButtonStyle(padding=ft.Padding.all(LayoutTokens.PADDING_BOTON_CELDA)),
            on_click=on_click,
        )

    def _celda_texto(
        texto_mostrado: str,
        valor_inicial: str,
        on_confirmar: Callable[[str], None],
        width: int,
        weight=None,
        color=None,
    ) -> ft.Control:
        contenedor = _contenedor_celda(width)

        def _mostrar() -> None:
            contenedor.content = _contenido_lectura(
                _texto_celda(texto_mostrado, weight=weight, color=color), _editar,
            )
            page.update()

        def _editar() -> None:
            campo = ft.TextField(
                value=valor_inicial,
                width=max(width - _ANCHO_BOTON_CONFIRMAR, _ANCHO_MINIMO_CAMPO_CELDA),
                dense=LayoutTokens.CELDA_DENSE,
                text_size=TypographyTokens.TABLE_CONTENT_SIZE,
                autofocus=True,
            )
            boton_confirmar_celda = _boton_confirmar_celda()

            def _confirmar(e=None) -> None:
                # Deshabilita campo y botón ANTES de llamar al service —
                # evita que un doble Enter/click dispare dos guardados
                # (mismo mecanismo que el Registro).
                campo.disabled = True
                boton_confirmar_celda.disabled = True
                page.update()
                try:
                    on_confirmar(campo.value)
                except _ERRORES_EDICION as err:
                    _mostrar_error(str(err))
                    _mostrar()

            campo.on_submit = _confirmar
            boton_confirmar_celda.on_click = _confirmar
            contenedor.content = ft.Row([campo, boton_confirmar_celda], spacing=0, tight=True)
            page.update()

        _mostrar()
        return contenedor

    def _celda_monto(
        texto_mostrado: str,
        monto_inicial_minor: int,
        decimales: int,
        on_confirmar: Callable[[int], None],
        width: int,
        weight=None,
        color=None,
    ) -> ft.Control:
        """
        on_confirmar recibe el monto ya resuelto en minor units (CampoMonto
        resuelve la fórmula/número). Re-lanza la excepción tras revertir la
        celda para que CampoMonto también revierta su texto.
        """
        contenedor = _contenedor_celda(width)

        def _mostrar() -> None:
            contenedor.content = _contenido_lectura(
                _texto_celda(texto_mostrado, weight=weight, color=color), _editar,
            )
            page.update()

        def _editar() -> None:
            def _confirmar(monto_minor: int) -> None:
                # Enter y el botón ✓ pasan los dos por campo.confirmar(),
                # así que deshabilitar acá cubre los dos caminos.
                campo.control.disabled = True
                boton_confirmar_monto.disabled = True
                page.update()
                try:
                    on_confirmar(monto_minor)
                except _ERRORES_EDICION as err:
                    _mostrar_error(str(err))
                    _mostrar()
                    raise

            campo = CampoMonto(
                page,
                on_confirmar=_confirmar,
                decimales=decimales,
                persistir_formula=True,
                valor_inicial_minor=monto_inicial_minor,
                width=max(width - _ANCHO_BOTON_CONFIRMAR, _ANCHO_MINIMO_CAMPO_CELDA),
                dense=LayoutTokens.CELDA_DENSE,
                text_size=TypographyTokens.TABLE_CONTENT_SIZE,
                autofocus=True,
            )
            boton_confirmar_monto = _boton_confirmar_celda(on_click=lambda e: campo.confirmar())
            contenedor.content = ft.Row([campo.control, boton_confirmar_monto], spacing=0, tight=True)
            page.update()

        _mostrar()
        return contenedor

    def _celda_bloqueada(texto_mostrado: str, motivo: str, width: int, weight=None) -> ft.Control:
        """Celda de solo lectura con el motivo del bloqueo como tooltip — mismo alto que las editables."""
        contenedor = _contenedor_celda(width)
        contenedor.content = ft.Container(
            content=ft.Text(
                texto_mostrado,
                size=TypographyTokens.TABLE_CONTENT_SIZE,
                weight=weight or TypographyTokens.TABLE_CONTENT_WEIGHT_REGULAR,
                color=ft.Colors.OUTLINE,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
                tooltip=motivo,
            ),
            padding=LayoutTokens.PADDING_CELDA,
            alignment=ft.Alignment.CENTER_LEFT,
        )
        return contenedor

    def _fila_deuda(d: dict) -> ft.Control:
        activa = d["estado"] == "activa"
        color_monto = ft.Colors.GREEN if d["tipo"] == "a_favor" else ft.Colors.RED

        def _guardar_deuda(**kwargs) -> None:
            # DebtError del service sube tal cual a la celda, que lo
            # muestra y revierte.
            debts_service.update(d["id"], **kwargs)
            _mostrar_ok(f"DEUDA #{d['id']} ACTUALIZADA.")
            _refrescar_vista_deudas()

        def _confirmar_persona(nuevo: str) -> None:
            if not nuevo or not nuevo.strip():
                raise ValueError("EL NOMBRE DE LA PERSONA NO PUEDE ESTAR VACÍO.")
            _guardar_deuda(person=nuevo.strip())

        def _confirmar_concepto(nuevo: str) -> None:
            if not nuevo or not nuevo.strip():
                raise ValueError("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
            _guardar_deuda(concept=nuevo.strip())

        def _confirmar_notas(nuevo: str) -> None:
            # '' limpia las notas (contrato de DebtsService.update()).
            _guardar_deuda(notes=(nuevo or "").strip())

        # DebtsService.update() solo acepta deudas 'activa' (mismo criterio
        # que tenía el diálogo de editar, que deshabilitaba los campos) —
        # si no, las tres celdas quedan de solo lectura con el motivo.
        def _celda_deuda(texto: str, on_confirmar: Callable[[str], None], width: int) -> ft.Control:
            if activa:
                return _celda_texto(texto, texto, on_confirmar, width)
            return _celda_bloqueada(texto, "NO SE PUEDE MODIFICAR: LA DEUDA YA NO ESTÁ ACTIVA", width)

        celda_persona = _celda_deuda(d["entidad_persona"], _confirmar_persona, _ANCHO_PERSONA)
        celda_concepto = _celda_deuda(d["concepto"] or "", _confirmar_concepto, _ANCHO_CONCEPTO)
        celda_notas = _celda_deuda(d["notas"] or "", _confirmar_notas, _ANCHO_NOTAS)

        # Registrar pago / Marcar incobrable: antes vivían dentro del
        # diálogo de editar (eliminado) — ahora son acciones de la fila,
        # solo para deudas activas (register_payment()/write_off() las
        # rechazarían de todas formas).
        acciones: list[ft.Control] = []
        if activa:
            acciones += [
                ft.IconButton(
                    icon=ft.Icons.PAYMENTS_OUTLINED,
                    icon_size=_ICONO_ACCION_FILA,
                    tooltip="REGISTRAR PAGO",
                    on_click=lambda e, d=d: _dialogo_registrar_pago_deuda(d),
                ),
                ft.IconButton(
                    icon=ft.Icons.MONEY_OFF_OUTLINED,
                    icon_size=_ICONO_ACCION_FILA,
                    icon_color=ft.Colors.ERROR,
                    tooltip="MARCAR INCOBRABLE",
                    on_click=lambda e, d=d: _dialogo_incobrable(d),
                ),
            ]
        acciones.append(
            ft.IconButton(
                icon=ft.Icons.DELETE_OUTLINE,
                icon_size=_ICONO_ACCION_FILA,
                icon_color=ft.Colors.ERROR,
                tooltip="ELIMINAR",
                on_click=lambda e, d=d: _confirmar_eliminar_deuda(d),
            )
        )
        botones_accion = ft.Row(acciones, spacing=0)

        fila = ft.Row(
            [
                celda_persona,
                celda_concepto,
                celda_notas,
                ft.Container(width=_ANCHO_TIPO, padding=4, content=_texto_celda("Me deben" if d["tipo"] == "a_favor" else "Debo")),
                ft.Container(width=_ANCHO_MONTO, padding=4, content=_texto_celda(amount_display(d["monto_original_minor"], d["decimales"], ""))),
                ft.Container(width=_ANCHO_MONTO, padding=4, content=_texto_celda(amount_display(d["monto_pendiente_minor"], d["decimales"], ""), weight=TypographyTokens.TABLE_CONTENT_WEIGHT, color=color_monto)),
                ft.Container(width=_ANCHO_FECHA, padding=4, content=_texto_celda(d["fecha_inicio"])),
                ft.Container(width=_ANCHO_ESTADO, padding=4, content=_texto_celda(d["estado"])),
                ft.Container(width=_ANCHO_ACCIONES, padding=4, content=botones_accion),
            ],
            spacing=_ESPACIADO_FILA,
        )
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=4, horizontal=0),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=4, content=fila,
        )

    tabla_body_deudas = ft.Column(spacing=4)

    def _actualizar_tabla_deudas() -> None:
        deudas = _cargar_deudas()
        if deudas:
            tabla_body_deudas.controls = [_fila_deuda(d) for d in deudas]
        else:
            tabla_body_deudas.controls = [
                ft.Text("No hay deudas para este período/filtro.", italic=True, color=ft.Colors.OUTLINE)
            ]

    def _header(texto: str, width: int) -> ft.Text:
        return ft.Text(texto, size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT, width=width)

    encabezado_deudas = ft.Row(
        [
            _header("PERSONA", _ANCHO_PERSONA), _header("CONCEPTO", _ANCHO_CONCEPTO), _header("NOTAS", _ANCHO_NOTAS),
            _header("TIPO", _ANCHO_TIPO), _header("MONTO ORIGINAL", _ANCHO_MONTO), _header("PENDIENTE", _ANCHO_MONTO),
            _header("FECHA", _ANCHO_FECHA), _header("ESTADO", _ANCHO_ESTADO), _header("", _ANCHO_ACCIONES),
        ],
        spacing=_ESPACIADO_FILA,
    )

    contenedor_toolbar_deudas = ft.Container()
    contenedor_alta_deudas = ft.Container()
    contenedor_dashboard_deudas = ft.Container()

    def _refrescar_vista_deudas() -> None:
        """
        Refresco LIVIANO: dashboard + tabla, sin tocar la barra de
        herramientas ni la fila de alta — usado tras alta/pago/editar/
        eliminar/incobrable, para no perder lo tipeado a medias en esos
        controles ni resetear el período (mismo criterio que
        ui/screens/compras_cuotas.py, ver su docstring).
        """
        contenedor_dashboard_deudas.content = _dashboard_deudas()
        _actualizar_tabla_deudas()
        page.update()

    def _refrescar_toolbar_deudas() -> None:
        """
        Refresco PESADO: reconstruye también la barra de herramientas —
        necesario para que el texto "‹ Mes Año ›" se actualice al navegar
        de período (mismo motivo exacto que compras_cuotas.py). Usado por
        los propios controles de la barra (mes, filtro persona, filtro
        estado), nunca por acciones de fila.
        """
        contenedor_toolbar_deudas.content = _toolbar_deudas()
        _refrescar_vista_deudas()

    def _construir_vista_deudas() -> ft.Control:
        contenedor_dashboard_deudas.content = _dashboard_deudas()
        contenedor_toolbar_deudas.content = _toolbar_deudas()
        contenedor_alta_deudas.content = _construir_fila_alta_deudas()
        _actualizar_tabla_deudas()
        return ft.Column(
            [
                contenedor_dashboard_deudas,
                ft.Container(height=8),
                ft.Container(
                    padding=16, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=8,
                    content=ft.Column(
                        [contenedor_toolbar_deudas, ft.Divider(height=1), contenedor_alta_deudas, ft.Divider(), encabezado_deudas, tabla_body_deudas],
                        spacing=4,
                    ),
                ),
            ],
            spacing=8,
        )

    # ============================================================
    # VISTA "GASTOS COMPARTIDOS DEL HOGAR"
    # ============================================================

    async def _tiene_hogar() -> bool:
        # async: obtener_usuario_local() ahora usa ft.SharedPreferences,
        # el reemplazo real de la inexistente page.client_storage — ver
        # docstring de ui/components/usuario_local.py para el detalle
        # completo del bug (root cause real, confirmado por lectura del
        # código fuente de Flet 0.86.5 instalado) que esto corrige.
        usuario_local = await obtener_usuario_local(page)
        if not usuario_local:
            return False
        mis_hogares = shared_expenses_service.list_my_hogares(usuario_local)
        if not mis_hogares:
            return False
        if hogar_actual["id"] is None or hogar_actual["id"] not in [h["hogar_id"] for h in mis_hogares]:
            hogar_actual["id"] = mis_hogares[0]["hogar_id"]
        return True

    def _construir_vista_sin_hogar() -> ft.Control:
        async def _abrir_onboarding(e=None) -> None:
            usuario_local = await obtener_usuario_local(page) or ""

            async def _tras_crear_o_unirse() -> None:
                # Forzamos la vista a "compartidos" — el usuario terminó de
                # configurar su hogar, tiene que ver esa vista, no "deudas"
                # (que es el default inicial y nunca cambió porque el usuario
                # llegó acá sin pasar por el toggle).
                vista["activa"] = "compartidos"
                await _refrescar_todo()

            abrir_dialogo_sin_hogar(page, shared_expenses_service, usuario_local, on_listo=_tras_crear_o_unirse)

        return ft.Column(
            [
                ft.Text("Todavía no pertenecés a ningún hogar compartido.", size=TypographyTokens.SECTION_TITLE_SIZE),
                ft.ElevatedButton(content=ft.Text("Configurar hogar compartido"), on_click=_abrir_onboarding),
            ],
            spacing=12,
        )

    def _cargar_gastos() -> list:
        # dict() antes de pasar a la UI (CLAUDE.md §11).
        gastos = [
            dict(g) for g in shared_expenses_service.list_shared_expenses(
                hogar_actual["id"], estado=estado_gastos["filtro_estado"], pagador=estado_gastos["filtro_pagador"],
            )
        ]
        mes_str = f"{estado_gastos['anio']:04d}-{estado_gastos['mes']:02d}"
        return [g for g in gastos if g["fecha"][:7] == mes_str]

    def _dashboard_gastos() -> ft.Control:
        balance = shared_expenses_service.get_net_balance(hogar_actual["id"])
        color = ft.Colors.GREEN if balance["saldo_neto_minor"] > 0 else (
            ft.Colors.RED if balance["saldo_neto_minor"] < 0 else None
        )
        return ft.Container(
            padding=ft.Padding.symmetric(horizontal=16, vertical=12),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=8,
            content=ft.Row(
                [
                    ft.Text("Saldo neto del hogar", size=TypographyTokens.SECTION_TITLE_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT),
                    ft.Text(balance["mensaje"], size=TypographyTokens.TABLE_CONTENT_SIZE, weight=TypographyTokens.TABLE_CONTENT_WEIGHT, color=color),
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
        )

    def _toolbar_gastos() -> ft.Control:
        def _avanzar(delta: int, e=None) -> None:
            mes = estado_gastos["mes"] + delta
            anio = estado_gastos["anio"]
            if mes < 1:
                mes, anio = 12, anio - 1
            elif mes > 12:
                mes, anio = 1, anio + 1
            estado_gastos["mes"], estado_gastos["anio"] = mes, anio
            _refrescar_toolbar_gastos()

        miembros = shared_expenses_service.list_miembros(hogar_actual["id"])
        opciones_pagador = [(m["usuario_local"], m["usuario_local"]) for m in miembros]
        campo_filtro_pagador = CampoFiltrable(
            page, opciones_pagador,
            on_seleccionar=lambda pid: (_set_filtro_pagador(pid), _refrescar_toolbar_gastos()),
            placeholder="Pagador", valor_inicial_id=estado_gastos["filtro_pagador"],
            width=_ANCHO_TOOLBAR_FILTRO_PERSONA, text_size=TypographyTokens.FILTER_SIZE,
        )

        def _set_filtro_pagador(pid: Optional[str]) -> None:
            estado_gastos["filtro_pagador"] = pid

        def _on_select_estado(e: ft.ControlEvent) -> None:
            estado_gastos["filtro_estado"] = dropdown_estado.value or None
            _refrescar_toolbar_gastos()

        dropdown_estado = ft.Dropdown(
            label="Estado", width=_ANCHO_TOOLBAR_FILTRO_ESTADO, dense=True, text_size=TypographyTokens.FILTER_SIZE,
            options=[ft.dropdown.Option(key="", text="Todos")] + [ft.dropdown.Option(key=s, text=s) for s in _ESTADOS_GASTO],
            value=estado_gastos["filtro_estado"] or "",
            on_select=_on_select_estado,
        )

        controles = [
            ft.IconButton(icon=ft.Icons.CHEVRON_LEFT, tooltip="Mes anterior", on_click=lambda e: _avanzar(-1, e)),
            ft.Text(
                f"{_MESES[estado_gastos['mes'] - 1]} {estado_gastos['anio']}",
                size=TypographyTokens.FILTER_SIZE, weight=TypographyTokens.SECTION_TITLE_WEIGHT, width=140,
                text_align=ft.TextAlign.CENTER,
            ),
            ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, tooltip="Mes siguiente", on_click=lambda e: _avanzar(1, e)),
            campo_filtro_pagador.control,
            dropdown_estado,
        ]
        return ft.Row(controles, spacing=_ESPACIADO_FILA)

    def _dialogo_registrar_pago_gasto(g: dict) -> None:
        campo_monto_pago = CampoMonto(page, on_confirmar=lambda monto_minor: None, hint_text="monto", width=160)
        dropdown_tipo_pago = ft.Dropdown(
            label="Tipo de pago", width=160,
            options=[ft.dropdown.Option(key=t, text=t) for t in _TIPOS_PAGO],
            value="transaccion",
        )
        campo_fecha_pago = ft.TextField(label="Fecha", value=date.today().isoformat(), width=160)

        def _confirmar(e=None) -> None:
            try:
                monto = float((campo_monto_pago.texto or "").strip().replace(",", "."))
            except ValueError:
                _mostrar_mensaje("El monto no es un número válido.", es_error=True)
                return
            if monto <= 0:
                _mostrar_mensaje("El monto debe ser positivo.", es_error=True)
                return
            try:
                datetime.strptime((campo_fecha_pago.value or "").strip(), "%Y-%m-%d")
            except ValueError:
                _mostrar_mensaje("La fecha debe tener el formato AAAA-MM-DD.", es_error=True)
                return
            monto_minor = round(monto * 100)
            try:
                resultado = shared_expenses_service.aplicar_pago(
                    gasto_id=g["id"], hogar_id=hogar_actual["id"], monto_aplicado_minor=monto_minor,
                    fecha=campo_fecha_pago.value.strip(), tipo_pago=dropdown_tipo_pago.value,
                )
            except (SharedExpensesError, ValueError) as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_ok(resultado.message)
            _refrescar_vista_gastos()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Registrar pago — {g['pagador']}"),
            content=ft.Container(
                width=_ANCHO_DIALOGO,
                content=ft.Column(
                    [
                        ft.Text(f"Pendiente actual: {amount_display(g['monto_pendiente_minor'], 2, '')}"),
                        campo_monto_pago.control, dropdown_tipo_pago, campo_fecha_pago,
                    ],
                    tight=True, spacing=10,
                ),
            ),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Confirmar"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _dialogo_editar_gasto(g: dict) -> None:
        campo_descripcion = ft.TextField(label="Descripción", value=g["descripcion"] or "", width=300)

        def _confirmar(e=None) -> None:
            try:
                shared_expenses_service.update_shared_expense(
                    gasto_id=g["id"], hogar_id=hogar_actual["id"], descripcion=campo_descripcion.value or None,
                )
            except GastoCompartidoNotFoundError as err:
                _mostrar_mensaje(str(err), es_error=True)
                return
            _cerrar_dialogo()
            _mostrar_ok("Gasto compartido actualizado.")
            _refrescar_vista_gastos()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Editar gasto compartido"),
            content=ft.Container(width=_ANCHO_DIALOGO, content=ft.Column([campo_descripcion], tight=True, spacing=10)),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Guardar"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _confirmar_eliminar_gasto(g: dict) -> None:
        def _eliminar(e=None) -> None:
            try:
                shared_expenses_service.delete_shared_expense(g["id"], hogar_actual["id"])
            except SharedExpensesError as err:
                _cerrar_dialogo()
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            _mostrar_ok(f"Gasto compartido #{g['id']} eliminado.")
            _refrescar_vista_gastos()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Eliminar gasto compartido"),
            content=ft.Text(f"¿Eliminar el gasto de '{g['pagador']}'? Esta acción no se puede deshacer."),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Eliminar"), on_click=_eliminar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _fila_gasto(g: dict) -> ft.Control:
        saldado = g["estado"] == "saldado"
        pendiente = g["estado"] == "pendiente"
        color_monto = ft.Colors.OUTLINE if saldado else None

        def _guardar_gasto(**kwargs) -> None:
            # SharedExpensesError/ValueError del service (ej. monto de un
            # gasto con pagos registrados) sube tal cual a la celda, que lo
            # muestra y revierte.
            shared_expenses_service.update_shared_expense(gasto_id=g["id"], hogar_id=hogar_actual["id"], **kwargs)
            _mostrar_ok(f"GASTO COMPARTIDO #{g['id']} ACTUALIZADO.")
            _refrescar_vista_gastos()

        def _confirmar_descripcion(nuevo: str) -> None:
            # Vacío limpia la descripción (None, contrato del service).
            _guardar_gasto(descripcion=(nuevo or "").strip() or None)

        def _confirmar_monto_base(monto_minor: int) -> None:
            # Puede ser negativo (reintegro mayor al monto, ver
            # add_shared_expense()); nunca 0.
            if monto_minor == 0:
                raise ValueError("EL MONTO BASE NO PUEDE SER 0.")
            _guardar_gasto(monto_base_minor=monto_minor)

        def _confirmar_coeficiente(nuevo: str) -> None:
            try:
                valor = float((nuevo or "").strip().replace(",", "."))
            except ValueError:
                raise ValueError("EL COEFICIENTE DEBE SER UN NÚMERO ENTRE 0 Y 1 (EJ. 0.5).") from None
            if not (0 < valor <= 1):
                raise ValueError("EL COEFICIENTE DEBE SER MAYOR A 0 Y COMO MÁXIMO 1 (EJ. 0.5).")
            _guardar_gasto(coeficiente_deuda=round(valor * 100, _DECIMALES_COEFICIENTE))

        def _confirmar_fecha(nuevo: str) -> None:
            try:
                datetime.strptime((nuevo or "").strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.") from None
            _guardar_gasto(fecha=nuevo.strip())

        celda_descripcion = _celda_texto(
            g["descripcion"] or "", g["descripcion"] or "", _confirmar_descripcion, _ANCHO_DESCRIPCION,
        )

        # Monto base y coeficiente: editables solo si el gasto sigue
        # 'pendiente' (pedido explícito). Un gasto pendiente con pagos
        # parciales también los bloquea, pero esa regla la aplica
        # update_shared_expense() (CLAUDE.md §4) — su SharedExpensesError
        # se muestra en la celda.
        texto_monto_base = amount_display(g["monto_base_minor"], _DECIMALES_GASTO, "")
        texto_coeficiente = f"{g['coeficiente_deuda']}%"
        if pendiente:
            celda_monto_base = _celda_monto(
                texto_monto_base, g["monto_base_minor"], _DECIMALES_GASTO, _confirmar_monto_base, _ANCHO_MONTO,
            )
            celda_coeficiente = _celda_texto(
                texto_coeficiente, f"{g['coeficiente_deuda'] / 100:g}", _confirmar_coeficiente, _ANCHO_COEFICIENTE,
            )
        else:
            motivo = "NO SE PUEDE MODIFICAR: EL GASTO YA FUE SALDADO"
            celda_monto_base = _celda_bloqueada(texto_monto_base, motivo, _ANCHO_MONTO)
            celda_coeficiente = _celda_bloqueada(texto_coeficiente, motivo, _ANCHO_COEFICIENTE)

        celda_fecha = _celda_texto(g["fecha"], g["fecha"], _confirmar_fecha, _ANCHO_FECHA)

        botones_accion = ft.Row(
            [
                ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, icon_size=16, tooltip="Editar", on_click=lambda e, g=g: _dialogo_editar_gasto(g)),
                ft.IconButton(icon=ft.Icons.PAYMENTS_OUTLINED, icon_size=16, tooltip="Registrar pago", disabled=saldado, on_click=lambda e, g=g: _dialogo_registrar_pago_gasto(g)),
                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_size=16, icon_color=ft.Colors.ERROR, tooltip="Eliminar", on_click=lambda e, g=g: _confirmar_eliminar_gasto(g)),
            ],
            spacing=0,
        )

        fila = ft.Row(
            [
                ft.Container(width=_ANCHO_PERSONA, padding=4, content=_texto_celda(g["pagador"])),
                celda_descripcion,
                ft.Container(width=_ANCHO_CATEGORIA, padding=4, content=_texto_celda(g["category_name"] or "")),
                celda_monto_base,
                celda_coeficiente,
                ft.Container(width=_ANCHO_MONTO, padding=4, content=_texto_celda(amount_display(g["monto_adeudado_minor"], 2, ""))),
                ft.Container(width=_ANCHO_MONTO, padding=4, content=_texto_celda(amount_display(g["monto_pendiente_minor"], 2, ""), weight=TypographyTokens.TABLE_CONTENT_WEIGHT, color=color_monto)),
                celda_fecha,
                ft.Container(width=_ANCHO_ESTADO, padding=4, content=_texto_celda(g["estado"])),
                ft.Container(width=_ANCHO_ACCIONES, padding=4, content=botones_accion),
            ],
            spacing=_ESPACIADO_FILA,
        )
        return ft.Container(
            padding=ft.Padding.symmetric(vertical=4, horizontal=0),
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=4, content=fila,
        )

    tabla_body_gastos = ft.Column(spacing=4)

    def _actualizar_tabla_gastos() -> None:
        gastos = _cargar_gastos()
        if gastos:
            tabla_body_gastos.controls = [_fila_gasto(g) for g in gastos]
        else:
            tabla_body_gastos.controls = [
                ft.Text("No hay gastos compartidos para este período/filtro.", italic=True, color=ft.Colors.OUTLINE)
            ]

    encabezado_gastos = ft.Row(
        [
            _header("PAGADOR", _ANCHO_PERSONA), _header("DESCRIPCIÓN", _ANCHO_DESCRIPCION),
            _header("CATEGORÍA", _ANCHO_CATEGORIA), _header("MONTO BASE", _ANCHO_MONTO),
            _header("COEFICIENTE", _ANCHO_COEFICIENTE), _header("ADEUDADO", _ANCHO_MONTO), _header("PENDIENTE", _ANCHO_MONTO),
            _header("FECHA", _ANCHO_FECHA), _header("ESTADO", _ANCHO_ESTADO), _header("", _ANCHO_ACCIONES),
        ],
        spacing=_ESPACIADO_FILA,
    )

    contenedor_toolbar_gastos = ft.Container()
    contenedor_dashboard_gastos = ft.Container()

    def _refrescar_vista_gastos() -> None:
        """Refresco LIVIANO — ver docstring de _refrescar_vista_deudas(), mismo criterio."""
        contenedor_dashboard_gastos.content = _dashboard_gastos()
        _actualizar_tabla_gastos()
        page.update()

    def _refrescar_toolbar_gastos() -> None:
        """Refresco PESADO — ver docstring de _refrescar_toolbar_deudas(), mismo criterio."""
        contenedor_toolbar_gastos.content = _toolbar_gastos()
        _refrescar_vista_gastos()

    async def _construir_vista_gastos() -> ft.Control:
        if not await _tiene_hogar():
            return _construir_vista_sin_hogar()

        contenedor_dashboard_gastos.content = _dashboard_gastos()
        contenedor_toolbar_gastos.content = _toolbar_gastos()
        _actualizar_tabla_gastos()
        return ft.Column(
            [
                contenedor_dashboard_gastos,
                ft.Container(height=8),
                ft.Container(
                    padding=16, border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT), border_radius=8,
                    content=ft.Column(
                        [contenedor_toolbar_gastos, ft.Divider(), encabezado_gastos, tabla_body_gastos],
                        spacing=4,
                    ),
                ),
            ],
            spacing=8,
        )

    # ============================================================
    # REFRESCO GENERAL
    # ============================================================

    async def _refrescar_todo() -> None:
        contenedor_toggle.content = _fila_toggle()
        if vista["activa"] == "deudas":
            contenedor_vista.content = _construir_vista_deudas()
        else:
            contenedor_vista.content = await _construir_vista_gastos()
        page.update()

    # Bootstrap inicial SIN await: `vista` siempre arranca en "deudas" (ver
    # su default más arriba), que no toca usuario_local/_tiene_hogar() en
    # absoluto — evita tener que lanzar una tarea async solo para el primer
    # render de build(), que debe seguir siendo síncrono (lo llama
    # ui/app.py como un builder de pantalla normal). Cualquier navegación
    # posterior (toggle a "Gastos compartidos", o el propio onboarding) sí
    # pasa por _refrescar_todo() async vía sus handlers de evento.
    contenedor_toggle.content = _fila_toggle()
    contenedor_vista.content = _construir_vista_deudas()

    fila_titulo = [ft.Text("Deudas y gastos compartidos", size=24, weight=ft.FontWeight.BOLD)]
    if on_volver is not None:
        fila_titulo.insert(0, ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Volver", on_click=lambda e: on_volver()))

    return ft.Column(
        [
            ft.Row(fila_titulo, alignment=ft.MainAxisAlignment.START),
            contenedor_toggle,
            ft.Container(height=8),
            contenedor_vista,
        ],
        spacing=8,
        expand=True,
        scroll=ft.ScrollMode.AUTO,
    )
