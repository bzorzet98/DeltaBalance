"""
DeltaBalance — ui/screens/presupuestos.py

Pantalla de Presupuestos: a diferencia del Registro de transacciones o
Compras en cuotas (tablas que CRECEN con filas nuevas), esto es una grilla
editable por período — una fila FIJA por cada categoría activa de tipo
'egreso' (ver nota sobre 'ambos' más abajo), nunca se agregan ni sacan
filas, solo se edita el Estimado/Recurrente de cada una para el mes/año
seleccionado.

Nota sobre el pedido original ("categoría de tipo 'egreso'/'ambos'"):
db/schema.sql define `categorias.tipo` con CHECK(tipo IN ('ingreso',
'egreso', 'movimiento')) — no existe un tipo 'ambos' en este schema
(confirmado leyendo el CHECK real, CLAUDE.md §0.4: no inventar
convenciones que no estén en schema.sql). Se filtra solo tipo='egreso',
que además es semánticamente lo correcto acá: no tiene sentido presupuestar
categorías de 'ingreso' (Sueldo, Cobro Deuda) ni de 'movimiento'
(Autotransferencia, Inversiones) en una pantalla de gasto.

Estado propio (igual que ui/screens/estadisticas.py y
ui/screens/compras_cuotas.py) — no depende de ningún `estado` compartido
con otra pantalla.

Estimado es un TextField SIEMPRE editable dentro de la grilla (no hay modo
lectura/edición como en el Registro — acá cada fila ya es "la fila", no
hace falta togglear) que confirma con on_submit (Enter) O on_blur (perder
foco) — ambos, tal como lo pedía la consigna original, para que no haga
falta acordarse de apretar Enter en una grilla con muchas filas.
set_budget() hace upsert real (crea si no había presupuesto para esa
categoría/mes/año, actualiza si ya había) — mismo categoria_id/mes/anio
siempre resuelve a la misma fila por el UNIQUE de la tabla.

RIESGO CONOCIDO, no confirmable sin correr la app: cada confirmación de
Estimado (blur o Enter) dispara _refrescar() completo (reconstruye TODA la
grilla, mismo patrón de "reconstruir entero" que el resto de la app) para
que Real/la barra de diferencia/el checkbox Recurrente quedan consistentes
con el nuevo valor. Esto recrea el TextField que tenía el foco — si el
usuario tabula rápido de una fila de Estimado a la siguiente, el foco
podría no aterrizar donde se espera (mismo tipo de problema que motivó el
arreglo de Tab de ui/components/campo_filtrable.py, pero acá NO se aplicó
ningún arreglo equivalente todavía: N filas dinámicas hacen que encadenar
foco a mano sea mucho más caro que en una fila de alta fija de 5-6 campos).
Si se nota molesto en la práctica, la mejora futura sería no reconstruir
la grilla entera en cada blur, solo la fila tocada + la tarjeta/summary de
diferencia.

Moneda de un presupuesto NUEVO: set_budget() exige moneda_id explícito y
la consigna no pidió un selector de moneda por fila — se asume ARS por
default para presupuestos nuevos (MONEDA_DEFAULT_PRESUPUESTO_CODIGO, más
abajo), consistente con que ARS es la moneda base de esta app (primera en
db/seed.sql, primera cuenta default). Un presupuesto YA existente conserva
la moneda con la que se creó (set_budget()/upsert() nunca la reescribe en
el camino de UPDATE, ver PresupuestosRepository.upsert()) — si se necesita
presupuestar una categoría en otra moneda, queda fuera de alcance de esta
pantalla (no pedido, no se inventa un selector).

Columna Real: DashboardService.get_comparacion_presupuesto(mes, anio) NO
alcanza sola para esta grilla — solo devuelve categorías que YA TIENEN un
presupuesto cargado (itera sobre PresupuestosService.list_budgets(), ver
su propio docstring), pero acá TODAS las categorías activas de egreso
deben aparecer, tengan presupuesto o no. Para las que sí tienen
presupuesto se usa get_comparacion_presupuesto() tal cual (ya cruza el
gasto real en la MISMA moneda del presupuesto, sin mezclar). Para las que
todavía no tienen presupuesto, Real sale de
DashboardService.get_gasto_por_categoria(mes, anio) directo — si esa
categoría gastó en más de una moneda ese mes (posible, no común), se
muestra la de mayor gasto (mismo criterio ya usado por el gráfico de
torta de esta misma pantalla de Estadísticas para el caso ambiguo
análogo, ver ui/screens/estadisticas.py _grafico_torta()) en vez de sumar
monedas distintas en un solo número.

Reglas de arquitectura: solo CategoriasService/PresupuestosService/
DashboardService/AccountsService (esta última solo para
list_currencies()) — nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).

--- Parte A (pendiente de una tarea anterior que no llegó a correr):
categorías mostradas por default + "+ Agregar categoría" ---

Antes de esta tarea, la grilla mostraba SIEMPRE las ~14 categorías de
egreso completas del catálogo. Ahora, por default, solo se muestran las
categorías que tienen al menos una fila en `presupuestos` en CUALQUIER
período (PresupuestosService.list_budgeted_category_ids(), sin filtrar por
mes/año — ver PresupuestosRepository.listar_categoria_ids_con_presupuesto()).
El control "+ Agregar categoría" (junto a "Copiar recurrentes del mes
anterior") abre un AlertDialog con un CampoFiltrable de las categorías de
egreso que TODAVÍA no están en la lista visible; al confirmar, su id se
agrega a `estado["categorias_extra"]` (un set, vive en `estado` para
sobrevivir a _refrescar(), mismo criterio que mes/anio) y la fila aparece
con Estimado vacío — sin escribir ningún presupuesto todavía, eso pasa
recién cuando el usuario carga un Estimado real y _confirmar_estimado()
llama a set_budget(). Una vez que eso pasa, la categoría ya queda incluida
por list_budgeted_category_ids() de por sí — quedarse en
categorias_extra además de eso no rompe nada (unión de sets), solo es
redundante.

--- Parte B/C (histórico) → ahora centralizado en ui/components/campo_monto.py ---

La calculadora de fórmulas en Estimado (texto que empieza con "=", ej.
"=15000+3200-500", evaluado con utils.calculadora_segura.evaluar_expresion()
— ast, nunca eval()/exec()) y la persistencia de la fórmula usada (columna
presupuestos.formula_estimado, ver PresupuestosRepository.upsert()) fueron
la implementación ORIGINAL de esta pantalla — generalizada después a toda
la app y movida a ui/components/campo_monto.py (CampoMonto, ver su
docstring para el mecanismo completo: qué resuelve el componente en sí
—sintaxis de la fórmula, borde rojo si es inválida, formato de
visualización— y qué queda a cargo de esta pantalla vía on_confirmar
—que el estimado sea > 0, el guardado real con set_budget()—).
campo_estimado se construye acá con persistir_formula=True (único caso en
toda la app hoy): mismo comportamiento "tipo Excel" ya confirmado
(campo_estimado.on_focus precarga el TEXTO de la fórmula solo la primera
vez que el campo gana foco en esta instancia de fila; sin foco siempre
muestra el resultado numérico formateado, tenga o no fórmula) — ahora
implementado una sola vez dentro del componente en vez de duplicado acá.
"""

from datetime import date
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.dashboard_service import DashboardService
from services.presupuestos_service import PresupuestoError, PresupuestosService
from ui.components import selector_periodo
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---
ANCHO_CATEGORIA = 220
ANCHO_ESTIMADO = 130
ANCHO_REAL = 130
ANCHO_RECURRENTE = 100
ANCHO_BARRA_DIFERENCIA = 160
ALTURA_BARRA_DIFERENCIA = 6
ESPACIADO_FILA = 8
MONEDA_DEFAULT_PRESUPUESTO_CODIGO = "ARS"
ANCHO_DIALOGO_AGREGAR_CATEGORIA = 320


def build(
    page: ft.Page,
    categorias_service: CategoriasService,
    presupuestos_service: PresupuestosService,
    dashboard_service: DashboardService,
    accounts_service: AccountsService,
    on_volver: Optional[Callable[[], None]] = None,
) -> ft.Control:
    hoy = date.today()
    # categorias_extra: ids agregados vía "+ Agregar categoría" en esta
    # sesión que todavía podrían no tener ningún presupuesto confirmado —
    # ver docstring del módulo, Parte A.
    estado = {"mes": hoy.month, "anio": hoy.year, "categorias_extra": set()}

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

    monedas_por_id = {m["id"]: m for m in accounts_service.list_currencies()}
    monedas_por_codigo = {m["codigo"]: m for m in accounts_service.list_currencies()}
    moneda_default = monedas_por_codigo.get(MONEDA_DEFAULT_PRESUPUESTO_CODIGO) or next(
        iter(monedas_por_codigo.values()), None
    )

    contenedor = ft.Column(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO)

    # ------------------------------------------------------------
    # DATOS DE LA GRILLA (ver docstring: get_comparacion_presupuesto() no
    # alcanza solo, se combina con get_gasto_por_categoria())
    # ------------------------------------------------------------

    def _filas_de_datos(mes: int, anio: int) -> list[dict]:
        # Parte A: por default, solo categorías con al menos un presupuesto
        # cargado alguna vez (cualquier período) + las agregadas a mano en
        # esta sesión vía "+ Agregar categoría" — ver docstring del módulo.
        todas_las_de_egreso = categorias_service.list_categories(tipo="egreso")
        ids_con_presupuesto = set(presupuestos_service.list_budgeted_category_ids())
        ids_visibles = ids_con_presupuesto | estado["categorias_extra"]
        categorias = [c for c in todas_las_de_egreso if c["id"] in ids_visibles]
        presupuestos_por_cat = {p["categoria_id"]: p for p in presupuestos_service.list_budgets(mes, anio)}

        gastos_por_cat: dict[int, list[dict]] = {}
        for g in dashboard_service.get_gasto_por_categoria(mes, anio):
            gastos_por_cat.setdefault(g["categoria_id"], []).append(g)

        # Cruce ya resuelto (moneda del presupuesto, sin mezclar) para las
        # categorías que sí tienen presupuesto — se reusa tal cual en vez
        # de reimplementar el mismo cruce acá.
        real_con_presupuesto = {
            c["categoria_id"]: c["real_minor"]
            for c in dashboard_service.get_comparacion_presupuesto(mes, anio)
        }

        filas = []
        for cat in categorias:
            presupuesto = presupuestos_por_cat.get(cat["id"])
            if presupuesto is not None:
                estimado_minor = presupuesto["monto_estimado_minor"]
                moneda_id = presupuesto["moneda_id"]
                es_recurrente = bool(presupuesto["es_recurrente"])
                formula_estimado = presupuesto["formula_estimado"]
                real_minor = real_con_presupuesto.get(cat["id"], 0)
            else:
                estimado_minor = None
                es_recurrente = False
                formula_estimado = None
                gastos_cat = gastos_por_cat.get(cat["id"], [])
                if gastos_cat:
                    # Sin presupuesto todavía: mismo criterio que el
                    # gráfico de torta de esta pantalla para el caso
                    # ambiguo de más de una moneda — se muestra la de
                    # mayor gasto, nunca se suman monedas distintas.
                    gasto_principal = max(gastos_cat, key=lambda g: g["monto_total_minor"])
                    real_minor = gasto_principal["monto_total_minor"]
                    moneda_id = gasto_principal["moneda_id"]
                else:
                    real_minor = 0
                    moneda_id = moneda_default["id"] if moneda_default else None

            filas.append({
                "categoria_id": cat["id"],
                "categoria_nombre": cat["subcategoria"],
                "estimado_minor": estimado_minor,
                "real_minor": real_minor,
                "moneda_id": moneda_id,
                "es_recurrente": es_recurrente,
                "formula_estimado": formula_estimado,
            })
        return filas

    # ------------------------------------------------------------
    # FILA DE LA GRILLA
    # ------------------------------------------------------------

    def _fila_presupuesto(fila: dict) -> ft.Control:
        moneda = monedas_por_id.get(fila["moneda_id"]) if fila["moneda_id"] else None
        decimales = moneda["decimales"] if moneda else 2
        simbolo = (moneda["simbolo"] if moneda else "") or ""

        checkbox_recurrente = ft.Checkbox(
            value=fila["es_recurrente"],
            disabled=fila["estimado_minor"] is None,
            tooltip=(
                "Cargá un Estimado primero para poder marcarla como recurrente"
                if fila["estimado_minor"] is None
                else "Se copia automáticamente con 'Copiar recurrentes del mes anterior'"
            ),
        )

        def _on_confirmar_estimado(monto_minor: int) -> None:
            # Validación de dominio (Presupuestos exige > 0) — no es una
            # regla universal de CampoMonto, ver su docstring. Lanzar acá
            # hace que el componente revierta el texto/fórmula al último
            # estado válido, sin propagar nada más arriba.
            if monto_minor <= 0:
                _mostrar_error("El estimado debe ser mayor a 0.")
                raise ValueError("estimado <= 0")
            if moneda_default is None:
                _mostrar_error("No hay ninguna moneda cargada en el sistema.")
                raise ValueError("sin moneda default")
            moneda_id_a_usar = fila["moneda_id"] if fila["estimado_minor"] is not None else moneda_default["id"]
            try:
                presupuestos_service.set_budget(
                    categoria_id=fila["categoria_id"],
                    mes=estado["mes"],
                    anio=estado["anio"],
                    moneda_id=moneda_id_a_usar,
                    monto_estimado_minor=monto_minor,
                    es_recurrente=checkbox_recurrente.value,
                    # Parte C original: se persiste el texto de la fórmula
                    # tal cual se tipeó (con "="), o None si se cargó como
                    # número directo — campo_estimado.formula ya resuelve
                    # esa distinción (persistir_formula=True, ver
                    # ui/components/campo_monto.py).
                    formula_estimado=campo_estimado.formula,
                )
            except PresupuestoError as err:
                _mostrar_error(str(err))
                raise
            _refrescar()

        # texto_numero: representación "tipo Excel" cuando el campo NO
        # tiene foco — siempre el resultado numérico formateado, tenga o
        # no fórmula guardada. Vacío = el usuario no tocó nada (o borró
        # todo): no hay forma de "borrar" un presupuesto ya cargado desde
        # acá (PresupuestosService no expone delete_budget()), así que
        # CampoMonto simplemente no hace nada en ese caso (ver su
        # docstring) — mismo comportamiento de siempre.
        campo_estimado = CampoMonto(
            page,
            on_confirmar=_on_confirmar_estimado,
            decimales=decimales,
            persistir_formula=True,
            valor_inicial_minor=fila["estimado_minor"],
            formula_inicial=fila["formula_estimado"],
            on_error=_mostrar_error,
            width=ANCHO_ESTIMADO,
            hint_text="Sin presupuesto",
            text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        )

        def _on_toggle_recurrente(e: ft.ControlEvent) -> None:
            if fila["estimado_minor"] is None:
                return  # defensivo — el checkbox ya viene disabled en este caso
            try:
                presupuestos_service.set_budget(
                    categoria_id=fila["categoria_id"],
                    mes=estado["mes"],
                    anio=estado["anio"],
                    moneda_id=fila["moneda_id"],
                    monto_estimado_minor=fila["estimado_minor"],
                    es_recurrente=checkbox_recurrente.value,
                    # Preserva la fórmula ya guardada (si había) — este
                    # toggle no toca el Estimado, formula_estimado
                    # SIEMPRE se reescribe en el UPDATE (ver
                    # PresupuestosRepository.upsert()), así que no pasarlo
                    # acá lo limpiaría a NULL por error aunque el usuario
                    # no haya tocado la fórmula para nada.
                    formula_estimado=fila["formula_estimado"],
                )
            except PresupuestoError as err:
                _mostrar_error(str(err))
                checkbox_recurrente.value = not checkbox_recurrente.value
                page.update()
                return
            _refrescar()

        checkbox_recurrente.on_change = _on_toggle_recurrente

        texto_real = amount_display(fila["real_minor"], decimales, simbolo)

        # Barra de diferencia: solo tiene sentido si hay Estimado contra
        # qué comparar — sin presupuesto, se deja un placeholder neutro.
        if fila["estimado_minor"]:
            proporcion = min(fila["real_minor"] / fila["estimado_minor"], 1.0)
            excedido = fila["real_minor"] > fila["estimado_minor"]
            color_barra = ft.Colors.RED if excedido else ft.Colors.GREEN
            texto_diferencia = (
                f"+{amount_display(fila['real_minor'] - fila['estimado_minor'], decimales, simbolo)}"
                if excedido
                else f"{proporcion * 100:.0f}%"
            )
            contenido_diferencia = ft.Column(
                [
                    ft.ProgressBar(
                        value=proporcion, width=ANCHO_BARRA_DIFERENCIA,
                        bar_height=ALTURA_BARRA_DIFERENCIA, color=color_barra,
                        bgcolor=ft.Colors.OUTLINE_VARIANT,
                    ),
                    ft.Text(texto_diferencia, size=TypographyTokens.LABEL_SIZE, color=color_barra),
                ],
                spacing=2,
            )
        else:
            contenido_diferencia = ft.Text("—", size=TypographyTokens.TABLE_CONTENT_SIZE, color=ft.Colors.OUTLINE)

        return ft.Row(
            [
                ft.Container(
                    width=ANCHO_CATEGORIA,
                    content=ft.Text(fila["categoria_nombre"], size=TypographyTokens.TABLE_CONTENT_SIZE),
                ),
                ft.Container(width=ANCHO_ESTIMADO, content=campo_estimado.control),
                ft.Container(
                    width=ANCHO_REAL,
                    content=ft.Text(
                        texto_real, size=TypographyTokens.TABLE_CONTENT_SIZE,
                        weight=TypographyTokens.TABLE_CONTENT_WEIGHT,
                    ),
                ),
                ft.Container(width=ANCHO_RECURRENTE, content=checkbox_recurrente),
                ft.Container(width=ANCHO_BARRA_DIFERENCIA, content=contenido_diferencia),
            ],
            spacing=ESPACIADO_FILA,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # ------------------------------------------------------------
    # BARRA DE HERRAMIENTAS: período + copiar recurrentes
    # ------------------------------------------------------------

    def _mes_anterior(mes: int, anio: int) -> tuple[int, int]:
        return (12, anio - 1) if mes == 1 else (mes - 1, anio)

    def _on_copiar_recurrentes(e=None) -> None:
        mes_ant, anio_ant = _mes_anterior(estado["mes"], estado["anio"])
        try:
            resultado = presupuestos_service.copy_period(
                mes_ant, anio_ant, estado["mes"], estado["anio"], solo_recurrentes=True,
            )
        except PresupuestoError as err:
            _mostrar_error(str(err))
            return
        _mostrar_ok(resultado.message)
        _refrescar()

    # ------------------------------------------------------------
    # "+ AGREGAR CATEGORÍA" (Parte A) — dialog con CampoFiltrable de las
    # categorías de egreso que todavía no están en la lista visible.
    # ------------------------------------------------------------

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    def _abrir_agregar_categoria(e=None) -> None:
        todas_las_de_egreso = categorias_service.list_categories(tipo="egreso")
        ids_con_presupuesto = set(presupuestos_service.list_budgeted_category_ids())
        ids_visibles = ids_con_presupuesto | estado["categorias_extra"]
        disponibles = [c for c in todas_las_de_egreso if c["id"] not in ids_visibles]
        if not disponibles:
            _mostrar_error("Todas las categorías de egreso ya están en la lista.")
            return

        campo_nueva_categoria = CampoFiltrable(
            page,
            [(str(c["id"]), c["subcategoria"]) for c in disponibles],
            on_seleccionar=lambda id_: None,
            placeholder="Categoría",
            width=ANCHO_DIALOGO_AGREGAR_CATEGORIA,
            autofocus=True,
        )

        def _confirmar(e=None) -> None:
            if not campo_nueva_categoria.id_seleccionado:
                _mostrar_error("Seleccioná una categoría de la lista de sugerencias.")
                return
            # Solo se agrega a categorias_extra (aparece con Estimado
            # vacío) — no se escribe ningún presupuesto acá, eso pasa
            # recién si el usuario carga un Estimado real (ver
            # _confirmar_estimado()).
            estado["categorias_extra"].add(int(campo_nueva_categoria.id_seleccionado))
            _cerrar_dialogo()
            _refrescar()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Agregar categoría a presupuestar"),
            content=ft.Container(width=ANCHO_DIALOGO_AGREGAR_CATEGORIA, content=campo_nueva_categoria.control),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Agregar"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    # ------------------------------------------------------------
    # REFRESCO (reconstruye TODA la pantalla — ver docstring, riesgo
    # conocido de foco al confirmar Estimado)
    # ------------------------------------------------------------

    def _refrescar() -> None:
        selector = selector_periodo.build(estado, _refrescar)
        boton_agregar_categoria = ft.ElevatedButton(
            content=ft.Text("+ Agregar categoría"),
            icon=ft.Icons.ADD,
            on_click=_abrir_agregar_categoria,
        )
        boton_copiar = ft.ElevatedButton(
            content=ft.Text("Copiar recurrentes del mes anterior"),
            icon=ft.Icons.CONTENT_COPY,
            on_click=_on_copiar_recurrentes,
        )
        barra_herramientas = ft.Row(
            [selector, ft.Container(expand=True), boton_agregar_categoria, boton_copiar],
            spacing=ESPACIADO_FILA,
        )

        encabezado = ft.Row(
            [
                ft.Container(width=ANCHO_CATEGORIA, content=ft.Text("Categoría", size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
                ft.Container(width=ANCHO_ESTIMADO, content=ft.Text("Estimado", size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
                ft.Container(width=ANCHO_REAL, content=ft.Text("Real", size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
                ft.Container(width=ANCHO_RECURRENTE, content=ft.Text("Recurrente", size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
                ft.Container(width=ANCHO_BARRA_DIFERENCIA, content=ft.Text("Diferencia", size=TypographyTokens.TABLE_HEADER_SIZE, weight=TypographyTokens.TABLE_HEADER_WEIGHT)),
            ],
            spacing=ESPACIADO_FILA,
        )

        filas_datos = _filas_de_datos(estado["mes"], estado["anio"])
        if filas_datos:
            filas_grilla: list[ft.Control] = []
            for i, fila in enumerate(filas_datos):
                if i > 0:
                    filas_grilla.append(ft.Divider(height=1))
                filas_grilla.append(_fila_presupuesto(fila))
        else:
            filas_grilla = [
                ft.Text(
                    "Todavía no presupuestaste ninguna categoría — usá \"+ Agregar categoría\" para empezar.",
                    italic=True, color=ft.Colors.OUTLINE,
                )
            ]

        contenedor.controls = [
            barra_herramientas,
            ft.Divider(height=1),
            encabezado,
            ft.Divider(height=1),
            ft.Column(filas_grilla, spacing=ESPACIADO_FILA),
        ]
        page.update()

    _refrescar()

    fila_titulo = [
        ft.Text("Presupuestos", size=TypographyTokens.PAGE_TITLE_SIZE, weight=TypographyTokens.PAGE_TITLE_WEIGHT)
    ]
    if on_volver is not None:
        fila_titulo.insert(
            0,
            ft.IconButton(icon=ft.Icons.ARROW_BACK, tooltip="Volver", on_click=lambda e: on_volver()),
        )

    return ft.Column(
        [
            ft.Row(fila_titulo, alignment=ft.MainAxisAlignment.START),
            ft.Container(height=8),
            contenedor,
        ],
        spacing=8,
        expand=True,
    )
