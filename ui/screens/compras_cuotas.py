"""
DeltaBalance — ui/screens/compras_cuotas.py

Compras y movimientos en cuotas: barra de título (búsqueda + período) →
barra "TOTAL A PAGAR POR TARJETA" → tabla (TablaPlanilla,
ui/components/tabla_planilla.py) con fila de alta, edición inline y barra
flotante de selección (eliminar / compartir). Misma tabla, misma cabecera y
misma paleta (ui/theme/tabla_tokens.py) que el Registro de transacciones:
las dos pantallas solo difieren en sus columnas y en su lógica de datos.

Reglas de arquitectura: solo AccountsService/CategoriasService/FeesService/
SharedExpensesService — nunca repositories/ ni db/ directo (CLAUDE.md
§2/§3). amount_minor para add_extra_charge() se calcula acá con
utils.money.amount_to_minor().

--- Estado propio de la pantalla ---

Período y moneda de la barra de totales viven en un almacén por página
(_ESTADOS_UI); el estado de la tabla (búsqueda, filtros, orden, selección,
anchos — clave "compras_anchos_columnas" en prefs) lo guarda TablaPlanilla.
La tabla muestra las compras cuya fecha_compra cae en el mes elegido.

--- Barra "TOTAL A PAGAR POR TARJETA" ---

Reemplaza a la tarjeta de desglose anterior, con el formato de la barra
de saldo del Registro: un chip por tarjeta de crédito activa (más las
archivadas que tengan algo a pagar ese mes) con el total del período según
FeesService.resumen_por_tarjeta() — cuotas que vencen ese mes + cargos
extra del resumen — en la moneda elegida con las pills de la derecha.
Verde si no queda nada a pagar (0 o a favor), color neutro si hay monto.
La sección "Cargos extra de este resumen" que se abría desde esa tarjeta
se sacó (pedido explícito): los cargos se siguen cargando desde la fila de
alta con las categorías especiales, y cuentan en el total de la barra.

--- Fila de alta ---

Concepto, Tarjeta y Categoría (CampoFiltrable, sugerencias flotantes), Monto
(CampoMonto, persistir_formula=True), Cuotas (default 1), Fecha y Moneda
(las monedas de la tarjeta elegida). Tab avanza entre campos, igual que
Enter; Tab en Moneda (el último) lleva el foco al ✓ y ahí Enter confirma y
guarda (TablaPlanilla.tab_a_confirmar()). Al guardar, la fila se
reconstruye vacía con el foco en Concepto.

Routing por CATEGORÍA al confirmar (sin cambios):
- Categoría NORMAL: FeesService.create_purchase() con el monto y la
  cantidad de cuotas tal cual se cargaron. El monto DEBE ser positivo.
- Categoría ESPECIAL ("Impuesto tarjeta"/"Recargo tarjeta"/
  "Ajuste/Reintegro tarjeta", services/fees_service.py
  CATEGORIAS_CARGO_EXTRA): NO crea una compra — resuelve el resumen de esa
  tarjeta y el mes/año de la fecha (FeesService.open_statement(),
  idempotente) y carga un cargo extra (add_extra_charge()) con el monto CON
  su signo. Cuotas se deshabilita al elegir una de estas categorías.

--- Filas ---

Devoluciones y reintegros (monto_total_minor negativo, típicamente
migrados desde el Excel) son filas normales: el monto se muestra "+" en
TEXT_POSITIVO (plata que vuelve); las compras, "−" en TEXT_NEGATIVO — mismo
lenguaje visual que el Registro.

Edición inline (CLAUDE.md §10): todas las columnas — Concepto, Banco,
Categoría, Monto total, Cuotas, Fecha y Moneda (entre las monedas de la
tarjeta de la fila; el service mantiene el importe mostrado) —, todas vía
FeesService.update_purchase() salvo Cuotas (update_purchase_cuotas(),
regenera el cronograma con el mismo total). Monto total se edita en valor
absoluto y conserva su signo, igual que en el Registro: un reintegro mal
cargado se corrige sin dejar de ser reintegro (update_purchase() acepta
totales negativos). Qué se puede corregir lo decide el service (CLAUDE.md
§4): si rechaza, su FeesError se muestra tal cual y la celda vuelve al
valor anterior. Reglas replicadas en la UI:
- Cuotas se muestra bloqueada, con el motivo en el tooltip, si alguna
  cuota ya no está 'pendiente' (pedido explícito).
- Una compra cancelada se muestra atenuada, con todas sus celdas de solo
  lectura y un ícono en la columna de acción.
El selector de Categoría inline excluye las 3 categorías especiales (una
compra ya cargada no puede convertirse en cargo extra).

--- Eliminar / compartir (barra flotante) ---

Eliminar intenta primero FeesService.delete_purchase() — borrado físico,
solo si ninguna cuota entró en un resumen o se pagó y nada depende de la
compra (compartida, deuda vinculada; ventana de corrección temprana,
CLAUDE.md §4), el caso típico de una compra cargada por error. Si el
service lo rechaza, la compra se cancela (cancel_purchase(): cuotas
pendientes → 'omitido', las ya en un resumen no se tocan) y queda en la
tabla atenuada. Compartir: con UNA compra, el flujo de siempre de
ui/components/compartir_compra.py (según el modo_deuda que ya tiene
guardado); con varias, ui/components/compartir_varios.py — mismo hogar y
mismo coeficiente para todas (add_shared_purchase(), cada una en su
modo_deuda), salteando las canceladas y las ya compartidas (total o
parcialmente).

--- Sin confirmar corriendo la app ---

Todo lo de la tabla está listado en el docstring de tabla_planilla.py.
Propio de acá: page.run_task() sobre el on_click async del ícono de
compartir_compra.py.
"""

from datetime import date, datetime
from typing import Any, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.fees_service import CATEGORIAS_CARGO_EXTRA, FeesError, FeesService
from services.shared_expenses_service import SharedExpensesService
from ui.components import compartir_compra
from ui.components.campo_monto import CampoMonto
from ui.components.compartir_varios import abrir_compartir_varios
from ui.components.tabla_planilla import (
    EXTRA_AJUSTE_DOT,
    ChipResumen,
    Columna,
    FilaAlta,
    TablaPlanilla,
    banco_con_dot,
    barra_resumen,
    barra_titulo,
    color_cuenta,
    estilo_campo,
    mostrar_mensaje,
    pantalla_planilla,
    sin_borde,
    texto_celda,
)
from ui.theme.tabla_tokens import (
    TEXT_ACCENT,
    TEXT_MUTED,
    TEXT_NEGATIVO,
    TEXT_POSITIVO,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from ui.theme.tokens import LayoutTokens, TypographyTokens
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---

# Concepto/Banco/Categoría redimensionables (proporción inicial); el resto,
# ancho fijo en px.
COLUMNAS = [
    Columna("concepto", "CONCEPTO", 200),
    Columna("banco", "BANCO", 130, extra_ajuste=EXTRA_AJUSTE_DOT),
    Columna("categoria", "CATEGORÍA", 140),
    Columna("monto", "MONTO TOTAL", 120, redimensionable=False, alineacion=ft.Alignment.CENTER_RIGHT),
    Columna("cuotas", "CUOTAS", 70, redimensionable=False),
    Columna("fecha", "FECHA", 110, redimensionable=False),
    Columna("moneda", "MONEDA", 80, redimensionable=False),
]
PREF_ANCHOS_COLUMNAS = "compras_anchos_columnas"
ICONO_ACCION = 14
# Las compras se piden de a este lote (ordenadas por fecha_compra DESC) hasta
# pasar el mes elegido — ver _cargar_compras().
LOTE_COMPRAS = 500
MONEDA_DEFAULT = "ARS"
HINT_MONTO_ALTA = "± MONTO"
TOOLTIP_CUOTAS_BLOQUEADAS = "NO SE PUEDE MODIFICAR: HAY CUOTAS YA PROCESADAS"
TOOLTIP_CANCELADA = "COMPRA CANCELADA"


# ============================================================
# ESTADO PROPIO POR PÁGINA (ver docstring del módulo)
# ============================================================

_ESTADOS_UI: dict[int, dict] = {}


def _estado_ui(page: ft.Page) -> dict:
    ui = _ESTADOS_UI.get(id(page))
    if ui is None:
        hoy = date.today()
        ui = {"mes": hoy.month, "anio": hoy.year, "moneda_resumen": MONEDA_DEFAULT}
        _ESTADOS_UI[id(page)] = ui
    return ui


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    fees_service: FeesService,
    shared_expenses_service: SharedExpensesService,
) -> ft.Control:
    ui = _estado_ui(page)

    def _mostrar_error(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje, es_error=True)

    def _mostrar_ok(mensaje: str) -> None:
        mostrar_mensaje(page, mensaje)

    # ------------------------------------------------------------
    # DATOS
    # ------------------------------------------------------------

    cuentas_por_id = {c["id"]: c for c in accounts_service.list_accounts(solo_activas=False)}
    # Compras en cuotas es exclusivamente sobre tarjetas de crédito.
    tarjetas_activas = sorted(
        (c for c in accounts_service.list_accounts(solo_activas=True) if c["tipo"] == "credito"),
        key=lambda c: c["nombre"],
    )
    categorias = [dict(c) for c in categorias_service.list_categories(tipo="egreso")]
    monedas_por_codigo = {m["codigo"]: dict(m) for m in accounts_service.list_currencies()}
    # id REAL (varía entre bases) de cada categoría especial → charge_type.
    mapa_categoria_a_charge_type: dict[str, str] = {
        str(c["id"]): CATEGORIAS_CARGO_EXTRA[(c["categoria_principal"], c["subcategoria"])]
        for c in categorias
        if (c["categoria_principal"], c["subcategoria"]) in CATEGORIAS_CARGO_EXTRA
    }
    opciones_tarjeta = [(str(c["id"]), c["nombre"]) for c in tarjetas_activas]
    opciones_categoria_alta = [(str(c["id"]), c["subcategoria"]) for c in categorias]
    # Inline: sin las 3 especiales (una compra ya cargada no se vuelve cargo extra).
    opciones_categoria_edicion = [
        (str(c["id"]), c["subcategoria"]) for c in categorias if str(c["id"]) not in mapa_categoria_a_charge_type
    ]

    def _cargar_compras() -> list[dict]:
        """
        Compras con fecha_compra en el mes elegido. list_purchases() no
        filtra por fecha y viene ordenada por fecha_compra DESC: se pide de
        a LOTE_COMPRAS hasta pasar el mes (antes era un solo lote de 500 y
        las compras de meses viejos podían quedar afuera). A cada compra se
        le agrega el estado de sus cuotas y si está compartida.
        """
        mes_str = f"{ui['anio']:04d}-{ui['mes']:02d}"
        compras: list[dict] = []
        pagina = 1
        while True:
            lote = [dict(c) for c in fees_service.list_purchases(page=pagina, per_page=LOTE_COMPRAS)]  # CLAUDE.md §11
            for compra in lote:
                if compra["fecha_compra"][:7] != mes_str:
                    continue
                estados = [q["estado"] for q in fees_service.get_fees_for_purchase(compra["id"])]
                compra["todas_pendientes"] = bool(estados) and all(e == "pendiente" for e in estados)
                compra["procesada"] = any(e in ("en_resumen", "pagado") for e in estados)
                _, compra["compartida"] = compartir_compra.build_icon(
                    page, shared_expenses_service, fees_service, compra, lambda: None,
                )
                compras.append(compra)
            if len(lote) < LOTE_COMPRAS or lote[-1]["fecha_compra"][:7] < mes_str:
                return compras
            pagina += 1

    def _moneda(compra: dict) -> dict:
        return monedas_por_codigo.get(compra["currency_code"], {})

    def _es_reintegro(compra: dict) -> bool:
        return compra["monto_total_minor"] < 0

    def _monto_texto(compra: dict) -> str:
        # Compra = plata que sale (−); reintegro/devolución = plata que vuelve (+).
        signo = "+" if _es_reintegro(compra) else "-"
        return f"{signo} {amount_display(abs(compra['monto_total_minor']), compra['decimales'], _moneda(compra).get('simbolo') or '')}"

    def _valor_columna(compra: dict, columna: str) -> str:
        """Valor tal como se muestra — base de los filtros por columna y de "Ajustar al contenido"."""
        if columna == "concepto":
            return compra["concepto"] or ""
        if columna == "banco":
            return compra["account_name"] or ""
        if columna == "categoria":
            return compra["category_name"] or ""
        if columna == "monto":
            return _monto_texto(compra)
        if columna == "cuotas":
            return str(compra["total_cuotas"])
        if columna == "fecha":
            return compra["fecha_compra"] or ""
        return compra["currency_code"] or ""

    def _clave_orden(compra: dict, columna: str) -> Any:
        if columna == "monto":
            return compra["monto_total_minor"] / (10 ** compra["decimales"])
        if columna == "cuotas":
            return compra["total_cuotas"]
        return _valor_columna(compra, columna).lower()

    def _firma(compra: dict) -> tuple:
        """Todo lo que la fila muestra: si no cambió, la fila cacheada se reusa tal cual."""
        return (
            compra["concepto"], compra["cuenta_id"], compra["account_name"], compra["categoria_id"],
            compra["category_name"], compra["monto_total_minor"], compra["total_cuotas"], compra["fecha_compra"],
            compra["currency_code"], compra["decimales"], compra["estado"], compra["todas_pendientes"],
            compra["procesada"], compra["compartida"],
        )

    # ------------------------------------------------------------
    # BARRA "TOTAL A PAGAR POR TARJETA"
    # ------------------------------------------------------------

    contenedor_totales = ft.Container()

    def _barra_totales() -> ft.Control:
        codigo_sel = ui["moneda_resumen"]
        moneda_sel = monedas_por_codigo.get(codigo_sel, {})
        resumen = fees_service.resumen_por_tarjeta(ui["mes"], ui["anio"])
        totales: dict[int, int] = {}
        for fila in resumen:
            if fila["currency_code"] == codigo_sel:
                totales[fila["cuenta_id"]] = totales.get(fila["cuenta_id"], 0) + fila["monto_total_minor"]
        ids_activas = {c["id"] for c in tarjetas_activas}
        tarjetas = tarjetas_activas + [
            cuentas_por_id[i] for i in sorted(totales) if i not in ids_activas and i in cuentas_por_id
        ]
        chips = []
        for tarjeta in tarjetas:
            total = totales.get(tarjeta["id"], 0)
            chips.append(ChipResumen(
                color=color_cuenta(tarjeta, tarjeta["nombre"]),
                nombre=tarjeta["nombre"],
                monto=amount_display(total, moneda_sel.get("decimales", 2), moneda_sel.get("simbolo") or ""),
                color_monto=TEXT_POSITIVO if total <= 0 else TEXT_PRIMARY,
                moneda=codigo_sel,
            ))
        return barra_resumen(
            "TOTAL A PAGAR POR TARJETA", ft.Icons.CREDIT_CARD, chips,
            [fila["currency_code"] for fila in resumen], codigo_sel,
            on_moneda=_elegir_moneda_resumen, texto_vacio="NO HAY TARJETAS DE CRÉDITO CARGADAS.",
        )

    def _elegir_moneda_resumen(codigo: str) -> None:
        ui["moneda_resumen"] = codigo
        contenedor_totales.content = _barra_totales()
        tabla.refrescar(contenedor_totales)

    def _al_recargar() -> list[ft.Control]:
        # Los totales cambian con cualquier alta/edición/borrado y con el período.
        contenedor_totales.content = _barra_totales()
        return [contenedor_totales]

    # ------------------------------------------------------------
    # FILA DE ALTA
    # ------------------------------------------------------------

    alta_refs: dict[str, Any] = {}

    def _construir_alta() -> FilaAlta:
        campo_concepto = ft.TextField(
            hint_text="EJ: HELADERA", autofocus=True, text_align=ft.TextAlign.CENTER, **estilo_campo(),
        )
        campo_cuotas = ft.TextField(value="1", text_align=ft.TextAlign.CENTER, **estilo_campo())
        dropdown_moneda = ft.Dropdown(
            options=[], dense=LayoutTokens.CELDA_DENSE, text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            border=ft.InputBorder.NONE, expand=True, text_align=ft.TextAlign.CENTER,
        )

        def _refrescar_monedas(cuenta_id: Optional[str]) -> None:
            cuenta = cuentas_por_id.get(int(cuenta_id)) if cuenta_id else None
            codigos = [s["moneda_codigo"] for s in (cuenta["saldos"] if cuenta else [])] or [MONEDA_DEFAULT]
            dropdown_moneda.options = [ft.dropdown.Option(key=c, text=c) for c in codigos]
            dropdown_moneda.value = codigos[0]

        def _on_tarjeta(id_tarjeta: Optional[str]) -> None:
            # CampoFiltrable parchea la fila (tabla.pagina_alta) justo después.
            if id_tarjeta is not None:
                _refrescar_monedas(id_tarjeta)

        def _on_categoria(id_categoria: Optional[str]) -> None:
            # Cuotas no aplica a un cargo extra (categoría especial).
            if id_categoria is not None:
                campo_cuotas.disabled = id_categoria in mapa_categoria_a_charge_type

        categoria_inicial = opciones_categoria_alta[0][0] if opciones_categoria_alta else None
        tarjeta_inicial = opciones_tarjeta[0][0] if opciones_tarjeta else None
        campo_categoria = tabla.campo_filtrable_alta(
            "categoria", opciones_categoria_alta, _on_categoria,
            placeholder="CATEGORÍA", valor_inicial_id=categoria_inicial,
            on_avanzar=lambda: tabla.enfocar(campo_monto.control),
        )
        campo_tarjeta = tabla.campo_filtrable_alta(
            "banco", opciones_tarjeta, _on_tarjeta,
            placeholder="BANCO", valor_inicial_id=tarjeta_inicial,
            on_avanzar=lambda: tabla.enfocar(campo_categoria.campo_texto),
        )
        # persistir_formula=True: el campo recuerda la fórmula mientras la
        # fila no se guarde. on_confirmar no-op — la fila entera confirma
        # junta (Tab/Enter en Moneda o ✓).
        campo_monto = CampoMonto(
            tabla.pagina_alta,
            on_confirmar=lambda monto_minor: None,
            persistir_formula=True,
            hint_text=HINT_MONTO_ALTA,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            on_avanzar=lambda: tabla.enfocar(campo_cuotas),
        )
        sin_borde(campo_monto.control)
        campo_monto.control.text_align = ft.TextAlign.RIGHT

        celda_fecha, campo_fecha = tabla.campo_fecha_alta(
            date.today().isoformat(), on_cambio=lambda: None, on_submit=lambda: tabla.enfocar(dropdown_moneda),
        )
        _refrescar_monedas(tarjeta_inicial)
        # Sync inicial: la categoría default podría ser una especial.
        if campo_categoria.id_seleccionado:
            campo_cuotas.disabled = campo_categoria.id_seleccionado in mapa_categoria_a_charge_type
        boton_confirmar = tabla.boton_confirmar_alta("AGREGAR", _confirmar_alta)

        alta_refs.update(
            concepto=campo_concepto, tarjeta=campo_tarjeta, categoria=campo_categoria, monto=campo_monto,
            cuotas=campo_cuotas, fecha=campo_fecha, moneda=dropdown_moneda, boton=boton_confirmar,
        )

        # Enter avanza al campo siguiente (en Tarjeta/Categoría, vía
        # on_avanzar de CampoFiltrable); en Moneda, Tab lleva al ✓ y ahí
        # Enter confirma.
        campo_concepto.on_submit = lambda e: tabla.enfocar(campo_tarjeta.campo_texto)
        campo_cuotas.on_submit = lambda e: tabla.enfocar(campo_fecha)
        tabla.tab_a_confirmar(dropdown_moneda)

        return FilaAlta(
            celdas={
                "concepto": campo_concepto,
                "banco": campo_tarjeta.control,
                "categoria": campo_categoria.control,
                "monto": campo_monto.control,
                "cuotas": campo_cuotas,
                "fecha": celda_fecha,
                "moneda": ft.Row([dropdown_moneda], spacing=0),
            },
            boton=boton_confirmar,
            foco=campo_concepto,
        )

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
            # Tras un alta exitosa `boton` ya no está en pantalla (la fila se
            # reconstruyó): refrescar() lo saltea.
            boton.disabled = False
            tabla.refrescar(boton)

    def _procesar_alta() -> None:
        concepto = (alta_refs["concepto"].value or "").strip()
        campo_tarjeta = alta_refs["tarjeta"]
        campo_categoria = alta_refs["categoria"]
        moneda_codigo = alta_refs["moneda"].value

        if not tarjetas_activas:
            _mostrar_error("PRIMERO CARGÁ UNA TARJETA DE CRÉDITO EN CONFIGURACIÓN → CUENTAS.")
            return
        if not categorias:
            _mostrar_error("NO HAY CATEGORÍAS DE EGRESO CARGADAS.")
            return
        if not concepto:
            _mostrar_error("EL COMERCIO/CONCEPTO NO PUEDE ESTAR VACÍO.")
            return
        try:
            monto_con_signo = float((alta_refs["monto"].texto or "").strip().replace(",", "."))
        except ValueError:
            _mostrar_error("EL MONTO NO ES UN NÚMERO VÁLIDO.")
            return
        if monto_con_signo == 0:
            _mostrar_error("EL MONTO NO PUEDE SER 0.")
            return
        fecha_str = (alta_refs["fecha"].value or "").strip()
        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d")
        except ValueError:
            _mostrar_error("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.")
            return
        if not campo_tarjeta.id_seleccionado:
            _mostrar_error("SELECCIONÁ UNA TARJETA DE LA LISTA DE SUGERENCIAS.")
            return
        if not campo_categoria.id_seleccionado:
            _mostrar_error("SELECCIONÁ UNA CATEGORÍA DE LA LISTA DE SUGERENCIAS.")
            return
        if not moneda_codigo:
            _mostrar_error("COMPLETÁ LA MONEDA.")
            return

        cuenta_id = int(campo_tarjeta.id_seleccionado)
        categoria_id = int(campo_categoria.id_seleccionado)
        # Routing por categoría (ver docstring del módulo): el signo por sí
        # solo no decide nada.
        charge_type = mapa_categoria_a_charge_type.get(campo_categoria.id_seleccionado)

        if charge_type is None:
            if monto_con_signo < 0:
                _mostrar_error(
                    "EL MONTO NO PUEDE SER NEGATIVO CON UNA CATEGORÍA NORMAL — ELEGÍ UNA CATEGORÍA ESPECIAL "
                    "DE TARJETA (IMPUESTO/RECARGO/AJUSTE-REINTEGRO) PARA CARGAR UN AJUSTE O REINTEGRO."
                )
                return
            try:
                cantidad_cuotas = int((alta_refs["cuotas"].value or "").strip())
            except ValueError:
                _mostrar_error("LA CANTIDAD DE CUOTAS DEBE SER UN NÚMERO ENTERO.")
                return
            if cantidad_cuotas < 1:
                _mostrar_error("LA CANTIDAD DE CUOTAS DEBE SER AL MENOS 1.")
                return
            try:
                resultado = fees_service.create_purchase(
                    date_str=fecha_str,
                    concept=concepto,
                    account_id=cuenta_id,
                    category_id=categoria_id,
                    currency_code=moneda_codigo,
                    total_amount=monto_con_signo,
                    total_fees=cantidad_cuotas,
                )
            except (FeesError, ValueError) as err:
                _mostrar_error(str(err))
                return
            mensaje = f"COMPRA #{resultado.entity_id} REGISTRADA EN {cantidad_cuotas} CUOTA(S)."
        else:
            # Cargo extra del resumen, con el signo tal cual se tipeó (la
            # categoría ya clasificó el TIPO de cargo, no su signo).
            try:
                resultado_resumen = fees_service.open_statement(account_id=cuenta_id, month=fecha.month, year=fecha.year)
                decimales = monedas_por_codigo.get(moneda_codigo, {}).get("decimales", 2)
                fees_service.add_extra_charge(
                    statement_id=resultado_resumen.entity_id,
                    concept=concepto,
                    charge_type=charge_type,
                    amount_minor=amount_to_minor(monto_con_signo, decimales),
                )
            except (FeesError, ValueError) as err:
                # Cubre StatementAlreadyClosedError/StatementAlreadyPaidError:
                # no hay cargo extra retroactivo sobre un resumen cerrado.
                _mostrar_error(str(err))
                return
            mensaje = f"CARGO EXTRA ({charge_type.upper()}) REGISTRADO EN EL RESUMEN DE {fecha.month:02d}/{fecha.year}."

        tabla.alta_ok()
        _mostrar_ok(mensaje)

    # ------------------------------------------------------------
    # FILAS DE DATOS — celdas editables inline (CLAUDE.md §10)
    # ------------------------------------------------------------

    def _construir_celdas(compra: dict) -> dict[str, ft.Control]:
        cuenta = cuentas_por_id.get(compra["cuenta_id"])
        color_monto = TEXT_POSITIVO if _es_reintegro(compra) else TEXT_NEGATIVO
        texto_monto = _monto_texto(compra)

        def _banco() -> ft.Control:
            return banco_con_dot(color_cuenta(cuenta, compra["account_name"] or ""), compra["account_name"] or "")

        def _moneda_lectura() -> ft.Control:
            return tabla.celda_lectura("moneda", texto_celda(compra["currency_code"] or "", color=TEXT_SECONDARY))

        if compra["estado"] == "cancelada":
            # Atenuada y sin edición (fila_atenuada + todas de solo lectura).
            return {
                "concepto": tabla.celda_lectura(
                    "concepto", texto_celda(compra["concepto"], tooltip=f"{TOOLTIP_CANCELADA}: {compra['concepto']}"),
                ),
                "banco": tabla.celda_lectura("banco", _banco()),
                "categoria": tabla.celda_lectura("categoria", texto_celda(compra["category_name"] or "")),
                "monto": tabla.celda_lectura(
                    "monto",
                    texto_celda(texto_monto, color=color_monto, size=TypographyTokens.REGISTRO_FONT_MONTO),
                ),
                "cuotas": tabla.celda_lectura("cuotas", texto_celda(str(compra["total_cuotas"]))),
                "fecha": tabla.celda_lectura("fecha", texto_celda(compra["fecha_compra"])),
                "moneda": _moneda_lectura(),
            }

        def _guardar(**kwargs) -> str:
            # FeesError del service (ej. campo bloqueado por §4) sube tal
            # cual a la celda, que lo muestra y vuelve al valor anterior.
            fees_service.update_purchase(compra["id"], **kwargs)
            return f"COMPRA #{compra['id']} ACTUALIZADA."

        def _guardar_concepto(nuevo: str) -> str:
            if not nuevo.strip():
                raise ValueError("EL COMERCIO/CONCEPTO NO PUEDE ESTAR VACÍO.")
            return _guardar(concepto=nuevo.strip())

        def _guardar_monto(monto_minor: int) -> str:
            # Se edita el valor absoluto; el signo (compra / reintegro) se conserva.
            if monto_minor <= 0:
                raise ValueError("EL MONTO TOTAL DEBE SER MAYOR A 0 (EL SIGNO NO SE CAMBIA DESDE ACÁ).")
            return _guardar(monto_total_minor=-monto_minor if _es_reintegro(compra) else monto_minor)

        def _guardar_moneda(nuevo_codigo: str) -> str:
            return _guardar(moneda_codigo=nuevo_codigo)

        # Moneda: entre las de la tarjeta de la fila (+ la actual, si la
        # tarjeta ya no la tiene), mismo criterio que la fila de alta.
        opciones_moneda = [(s["moneda_codigo"], s["moneda_codigo"]) for s in (cuenta["saldos"] if cuenta else [])]
        if compra["currency_code"] not in [codigo for codigo, _ in opciones_moneda]:
            opciones_moneda.append((compra["currency_code"], compra["currency_code"]))

        def _guardar_cuotas(nuevo: str) -> str:
            try:
                cantidad = int(nuevo.strip())
            except ValueError:
                raise ValueError("LA CANTIDAD DE CUOTAS DEBE SER UN NÚMERO ENTERO.") from None
            if cantidad < 1:
                raise ValueError("LA CANTIDAD DE CUOTAS DEBE SER AL MENOS 1.")
            fees_service.update_purchase_cuotas(compra["id"], cantidad)
            return f"COMPRA #{compra['id']} ACTUALIZADA."

        def _guardar_fecha(nuevo: str) -> str:
            try:
                datetime.strptime(nuevo.strip(), "%Y-%m-%d")
            except ValueError:
                raise ValueError("LA FECHA DEBE TENER EL FORMATO AAAA-MM-DD.") from None
            return _guardar(fecha=nuevo.strip())

        celda_monto = tabla.celda_monto(
            compra, "monto", texto_monto, color_monto, abs(compra["monto_total_minor"]), compra["decimales"],
            _guardar_monto,
        )

        # Cuotas: editable solo si TODAS siguen 'pendiente' (pedido
        # explícito). La regla completa la aplica update_purchase_cuotas().
        if compra["todas_pendientes"]:
            celda_cuotas = tabla.celda_texto(compra, "cuotas", str(compra["total_cuotas"]), _guardar_cuotas)
        else:
            celda_cuotas = tabla.celda_lectura(
                "cuotas", texto_celda(str(compra["total_cuotas"]), color=TEXT_MUTED, tooltip=TOOLTIP_CUOTAS_BLOQUEADAS),
            )

        return {
            "concepto": tabla.celda_texto(compra, "concepto", compra["concepto"], _guardar_concepto),
            "banco": tabla.celda_filtrable(
                compra, "banco", _banco, opciones_tarjeta, str(compra["cuenta_id"]),
                lambda id_: _guardar(cuenta_id=int(id_)),
            ),
            "categoria": tabla.celda_filtrable(
                compra, "categoria", lambda: texto_celda(compra["category_name"] or ""),
                opciones_categoria_edicion, str(compra["categoria_id"]),
                lambda id_: _guardar(categoria_id=int(id_)),
            ),
            "monto": celda_monto,
            "cuotas": celda_cuotas,
            "fecha": tabla.celda_texto(compra, "fecha", compra["fecha_compra"], _guardar_fecha),
            "moneda": tabla.celda_dropdown(
                compra, "moneda", compra["currency_code"] or "", opciones_moneda, compra["currency_code"],
                _guardar_moneda, color=TEXT_SECONDARY,
            ),
        }

    def _accion_fila(compra: dict) -> Optional[ft.Control]:
        if compra["estado"] == "cancelada":
            return ft.Icon(ft.Icons.BLOCK, size=ICONO_ACCION, color=TEXT_MUTED, tooltip=TOOLTIP_CANCELADA)
        if compra["compartida"]:
            return ft.Icon(ft.Icons.PEOPLE, size=ICONO_ACCION, color=TEXT_ACCENT, tooltip="COMPRA COMPARTIDA")
        return None

    # ------------------------------------------------------------
    # ELIMINAR / COMPARTIR (barra flotante)
    # ------------------------------------------------------------

    def _avisos_eliminar(compras: list[dict]) -> str:
        se_cancelan = sum(1 for c in compras if c["estado"] == "activa" and (c["procesada"] or c["compartida"]))
        if not se_cancelan:
            return ""
        return f"{se_cancelan} SE CANCELA(N) EN VEZ DE BORRARSE: TIENE(N) CUOTAS PROCESADAS O ESTÁ(N) COMPARTIDA(S)"

    def _eliminar(compras: list[dict]) -> tuple[str, bool]:
        borradas = canceladas = 0
        errores: list[str] = []
        for compra in compras:
            try:
                fees_service.delete_purchase(compra["id"])
                borradas += 1
                continue
            except FeesError:
                pass  # algo depende de ella (§4): se cancela en su lugar
            if compra["estado"] != "activa":
                errores.append(f"#{compra['id']}: YA ESTÁ {compra['estado'].upper()} Y NO SE PUEDE BORRAR")
                continue
            try:
                fees_service.cancel_purchase(compra["id"])
                canceladas += 1
            except FeesError as err:
                errores.append(f"#{compra['id']}: {err}")
        partes = []
        if borradas:
            partes.append(f"{borradas} COMPRA(S) ELIMINADA(S)")
        if canceladas:
            partes.append(f"{canceladas} CANCELADA(S) (TIENEN CUOTAS PROCESADAS, ESTÁN COMPARTIDAS O TIENEN UNA DEUDA)")
        if errores:
            partes.append(f"{len(errores)} CON ERROR: " + " | ".join(errores))
        return " · ".join(partes) or "NADA PARA ELIMINAR.", bool(errores)

    def _compartir_una(compra: dict, hogar_id: int, pagador: str, coeficiente: float) -> None:
        # Cada compra se comparte en su propio modo_deuda (compartir_compra.py, ídem).
        shared_expenses_service.add_shared_purchase(
            compra_id=compra["id"], hogar_id=hogar_id, pagador=pagador, coeficiente_deuda=coeficiente,
        )

    def _compartir(compras: list[dict]) -> None:
        if len(compras) == 1:
            if compras[0]["estado"] == "cancelada":
                _mostrar_error("UNA COMPRA CANCELADA NO SE PUEDE COMPARTIR.")
                return
            # El flujo existente (compartir_compra.py) es un diálogo por
            # compra: se dispara el on_click (async) de su propio ícono.
            icono, _ = compartir_compra.build_icon(
                page, shared_expenses_service, fees_service, compras[0], tabla.recargar,
            )
            page.run_task(icono.on_click, None)
            return
        canceladas = sum(1 for c in compras if c["estado"] == "cancelada")
        ya_compartidas = sum(1 for c in compras if c["estado"] != "cancelada" and c["compartida"])
        pendientes = [c for c in compras if c["estado"] != "cancelada" and not c["compartida"]]
        if not pendientes:
            _mostrar_error("NINGUNA DE LAS COMPRAS SELECCIONADAS SE PUEDE COMPARTIR (CANCELADAS O YA COMPARTIDAS).")
            return
        avisos = []
        if canceladas:
            avisos.append(f"{canceladas} CANCELADA(S): SE SALTEA(N).")
        if ya_compartidas:
            avisos.append(f"{ya_compartidas} YA COMPARTIDA(S) (TOTAL O PARCIALMENTE): SE SALTEA(N).")
        page.run_task(
            abrir_compartir_varios, page, shared_expenses_service,
            f"COMPARTIR {len(pendientes)} COMPRAS", pendientes, _compartir_una, avisos, tabla.recargar,
        )

    # ------------------------------------------------------------
    # ARMADO
    # ------------------------------------------------------------

    tabla = TablaPlanilla(
        page,
        clave="compras",
        columnas=COLUMNAS,
        pref_anchos=PREF_ANCHOS_COLUMNAS,
        cargar_filas=_cargar_compras,
        construir_celdas=_construir_celdas,
        construir_alta=_construir_alta,
        firma=_firma,
        valor_columna=_valor_columna,
        clave_orden=_clave_orden,
        texto_busqueda=lambda c: c["concepto"] or "",
        accion_fila=_accion_fila,
        fila_atenuada=lambda c: c["estado"] == "cancelada",
        on_eliminar=_eliminar,
        avisos_eliminar=_avisos_eliminar,
        on_compartir=_compartir,
        al_recargar=_al_recargar,
        errores_esperados=(FeesError,),
        texto_vacio="NO HAY COMPRAS EN CUOTAS PARA ESTE PERÍODO.",
    )
    control_tabla = tabla.construir()
    contenedor_totales.content = _barra_totales()

    titulo = barra_titulo(
        page, "COMPRAS Y MOVIMIENTOS EN CUOTAS", tabla, ui,
        lambda: tabla.recargar(limpiar_seleccion=True), "BUSCAR EN COMPRAS…",
    )
    # Scroll propio (el Registro lo tiene por la Column del dashboard).
    return ft.Column(
        [pantalla_planilla([titulo, contenedor_totales, control_tabla])],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
    )
