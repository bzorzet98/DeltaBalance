"""
DeltaBalance — ui/components/registro_transacciones.py

Componente de "Registro de transacciones" estilo planilla: barra de
herramientas en una sola fila (período navegable ‹ Mes Año › → filtro de
Banco → filtro de Categoría → búsqueda por texto en concepto, alineada al
extremo derecho — construida por ui/components/barra_filtros.py, que
también reusa ui/screens/compras_cuotas.py, ver docstring de ese módulo
para el detalle de qué es genérico ahí y qué reglas de negocio quedan
afuera) + divisor sutil + encabezado de columnas + tabla de movimientos,
donde la PRIMERA fila de la tabla es siempre la fila de alta en blanco
(mismo estilo/anchos que las demás filas, sin labels propios — el
encabezado de columnas ya cumple ese rol; se resetea sola tras guardar,
nunca hay un botón separado de "agregar otra entrada" ni una fila nueva
agregada al layout) seguida de las filas ya cargadas, con edición inline
celda por celda y un ícono de "Compartir" por fila (ver
ui/components/compartir_gasto.py), filas separadas por ft.Divider (sin
Container con borde por fila). Vive en ui/components/ porque
ui/screens/compras_cuotas.py reusa este mismo patrón visual de fila de
tabla (ver CLAUDE.md §8 para la regla de layout que este archivo sigue).

Todos los controles de filtro/herramientas (período, filtro Banco, filtro
Categoría, búsqueda, y el TextField interno de CampoFiltrable en Banco/
Categoría) usan TypographyTokens.FILTER_SIZE — mismo tamaño de fuente
chico para los cuatro, pedido explícito para que ninguno resalte más que
otro.

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

Categoría y Cuenta (fila de alta y, para Categoría, también edición
inline) usan CampoFiltrable (ui/components/campo_filtrable.py) — un
componente propio, NO ft.Dropdown(enable_filter=True, editable=True) NI
ft.AutoComplete. Se probaron los dos controles nativos en rondas
anteriores de esta misma pantalla y ambos tuvieron fricción real:
- Dropdown(enable_filter=True, editable=True): el primer Tab solo
  enfocaba el selector cerrado, hacía falta un segundo Tab/Enter para
  poder escribir.
- ft.AutoComplete (reemplazo intentado después): no mostraba ninguna
  lista de sugerencias al escribir, en ninguna pantalla, confirmado por
  el usuario corriendo la app con la consola del navegador sin errores —
  no se investigó la causa a fondo (pedido explícito de reemplazar en vez
  de seguir depurando). Al construir CampoFiltrable se confirmó además,
  leyendo el código fuente real de Flet 0.86.5 instalado, que AutoComplete
  en esa versión es un control muy desnudo (solo value/suggestions/
  on_select/on_change + width/height) — sin label/hint_text/dense/
  text_size/text_style/border/focus()/on_submit.
Ver el docstring de ui/components/campo_filtrable.py para el detalle
completo de cómo funciona el reemplazo (filtrado propio, blur-vs-click,
por qué no tiene navegación por flechas) y
docs/FLET_API_NOTES.md para que una sesión futura no vuelva a intentar
Dropdown(enable_filter)/AutoComplete para este caso de uso.

Como CampoFiltrable es un ft.TextField real por dentro, SÍ tiene
text_size/focus()/on_submit reales — el encadenado de Enter de la fila de
alta vuelve a pasar por Banco/Categoría sin perder nada (a diferencia de
la ronda con AutoComplete, que no podía).

El bug de Dropdown (issue #5338 de flet-dev/flet, selección con teclado
sin disparar on_select) queda documentado acá solo como referencia
histórica — no aplica a Categoría/Cuenta (ya no son Dropdown). SÍ sigue
aplicando a la celda de Moneda en edición inline (_celda_dropdown() sigue
construyendo un Dropdown(enable_filter=True, editable=True) ahí — Moneda
no se tocó en esta ronda). El Dropdown de Moneda de la fila de alta no
está afectado: no usa enable_filter/editable, es un Dropdown simple de
click.

Monto (fila de alta y edición inline, _celda_monto()) usa CampoMonto
(componente propio, ver ui/components/campo_monto.py y CLAUDE.md §8) —
calculadora de fórmulas ("=15000+3200-500", ast, nunca eval()/exec())
disponible en ambos lugares. persistir_formula=False (default) en los dos
casos: el campo, una vez confirmado, siempre muestra el número, nunca la
fórmula que lo generó. La calculadora NO cambia la interpretación del
signo: campo_monto_alta.texto (fila de alta) ya llega a _confirmar_alta()
como el número resuelto (positivo o negativo), así que
"negativo=egreso/positivo=ingreso" y el routing especial por categoría
(Tarea 1b, ver más abajo) siguen leyendo el mismo valor de siempre, sin
tocar esa lógica. En la edición inline, _confirmar_monto() recibe
directamente el monto ya resuelto en minor units (CampoMonto se encarga de
la conversión) y solo aplica la validación de dominio de esta pantalla
(> 0) antes de llamar a TransactionService.update().

Fila de alta — es la primera fila de la tabla, no una sección aparte: va
posicionada justo debajo de encabezado_columnas (dentro de filas_tabla),
con los mismos anchos de columna que las demás filas y SIN label propio en
cada campo (el encabezado de columnas ya dice "Concepto"/"Banco"/etc. —
duplicarlo en cada TextField/Dropdown de esta fila sería la sección aparte
que se quería eliminar). Única excepción: el campo de Monto sí lleva
hint_text con la convención de signo (± ), porque esa info no está en el
encabezado y es la única fila donde el signo importa (las demás filas ya
tienen movement_type fijo).

Reset tras guardar: la fila NUNCA desaparece ni se agrega una nueva;
on_cambio() (ver docstring de build()) reconstruye TODO el Registro, lo que
de por sí recrea la fila de alta desde cero con sus valores default
(campo_concepto_alta usa autofocus=True, así que además recupera el foco
solo — no hace falta lógica de reset manual). Si el guardado falla
(TransactionError/ValueError), _confirmar_alta() vuelve ("return") ANTES de
llamar on_cambio(), así que la fila nunca se reconstruye y los valores
tipeados quedan intactos para corregir.

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
de ui/ — ver docs/FLET_API_NOTES.md. Los diálogos de "Compartir" viven en
ui/components/compartir_gasto.py; los dos nuevos de Tarea 1b (ver abajo)
viven acá mismo, son chicos y puntuales a esta fila de alta.

--- Tarea 1b (docs/PROXIMOS_PASOS.md): categorías especiales
Autotransferencia y Ahorro/Inversión ---

Dos categorías de tipo='movimiento' (normalmente excluidas del selector de
Categoría de esta pantalla, ver `categorias` más abajo: solo tipo IN
('ingreso', 'egreso')) se agregan como excepción puntual porque ahora
tienen un routing especial al confirmar la fila de alta —
_CATEGORIAS_ROUTING_ESPECIAL (abajo) es la fuente de verdad de cuáles son
y a qué modo rutean. Mismo mecanismo de "categoría especial resuelta una
sola vez contra la lista ya cargada" que ui/screens/compras_cuotas.py usa
para Impuesto/Recargo/Ajuste-Reintegro tarjeta (services/fees_service.py
CATEGORIAS_CARGO_EXTRA) — a diferencia de ese caso, acá no hay un único
service dueño del routing (Autotransferencia va a TransactionService,
Ahorro/Inversión va a SavingsService), así que el mapeo vive local a este
módulo en vez de importarse de un service.

- **"Autotransferencia"**: al confirmar, abre un mini-diálogo pidiendo
  Cuenta destino (CampoFiltrable, excluye la cuenta origen ya elegida en
  la fila) y llama a TransactionService.create_transfer() con
  fecha/monto/categoría de la fila + la cuenta destino elegida.
  create_transfer() NO tiene un parámetro `concept` (hardcodea "Auto-
  transfer (out/in)" en las dos transacciones que genera) — el concepto
  tipeado en la fila viaja como `notes` en su lugar, el mapeo más cercano
  disponible en la firma real del service (revisada antes de asumir).
  create_transfer() SÍ exige `category_id` (no es opcional) — se le pasa
  el id de la propia categoría "Autotransferencia" elegida en la fila, ya
  resuelto (no hace falta buscarlo de nuevo).
- **"Ahorro/Inversión"**: al confirmar, abre un mini-diálogo con DOS modos
  (tarea de unificación posterior a la Tarea 1b — antes ui/screens/
  ahorros.py tenía su propio formulario de Compra completo y separado para
  la misma acción, ahora comparten uno):
  - **Modo simple (default, comportamiento original sin cambios)**: pide
    el Objetivo de ahorro (CampoFiltrable con los objetivos_ahorro
    existentes + opción "+ Crear nuevo objetivo", que revela un campo de
    nombre y llama a SavingsService.create_objetivo() antes de continuar)
    y llama a SavingsService.get_or_create_reserved_cash_asset() (Tarea
    1b, motor de datos — ver services/savings_service.py) para resolver o
    crear el activo_financiero genérico "Efectivo reservado en <cuenta de
    la fila>", y por último a SavingsService.register_purchase(
    cuenta_id=..., categoria_id=..., asignaciones=[{objetivo_id,
    porcentaje: 100}]) — atómico, ya crea movimiento + asignación +
    transacción vinculada (mecanismo de la Tarea 6b).
  - **"Elegir activo específico" (link dentro del mismo modo simple)**:
    REEMPLAZA el contenido del MISMO AlertDialog ya abierto (no cierra y
    abre uno nuevo — ver el docstring de _abrir_dialogo_ahorro_inversion())
    por el formulario completo de ui/components/dialogo_compra_ahorro.py
    (compartido con el botón de Compra por activo de ui/screens/
    ahorros.py): activo CampoFiltrable + "crear nuevo" + cantidad/precio
    unitario según tipo + asignaciones a objetivos, con cuenta_id/monto/
    fecha/concepto de la fila ya precargados. Para este modo el usuario
    eligió explícitamente MÁS control — no pasa por
    get_or_create_reserved_cash_asset() ni fuerza 100% a un solo
    objetivo, llama a SavingsService.register_purchase() directo con lo
    que el formulario resuelva.
- En AMBOS casos el signo tipeado en Monto se ignora a propósito (se usa
  abs(monto)): a diferencia de una categoría normal (negativo=egreso,
  positivo=ingreso decide movement_type), acá el tipo de movimiento lo
  fuerza el método de destino (create_transfer() siempre arma un par
  egreso+ingreso; register_purchase() siempre es egreso/aporte — no existe
  todavía un flujo de "retiro" con Ahorro/Inversión desde el Registro,
  fuera de alcance de esta tarea) — mismo criterio que ya usa
  ui/screens/compras_cuotas.py para Autotransferencia (el signo no decide
  nada ahí tampoco).
- Las validaciones comunes (concepto, monto, fecha, cuenta, categoría,
  moneda) corren SIEMPRE antes de abrir cualquier diálogo — los dos modos
  especiales solo cambian el paso FINAL de persistencia. Si el usuario
  cancela el diálogo, no se llama a on_cambio() y la fila de alta queda
  intacta con lo tipeado, igual que un guardado fallido en el camino
  normal.
"""

from datetime import date, datetime
from typing import Callable, Optional

import flet as ft

from services.accounts_service import AccountsService
from services.categorias_service import CategoriasService
from services.savings_service import SavingsError, SavingsService
from services.shared_expenses_service import SharedExpensesService
from services.transaction_service import TransactionService, TransactionError
from ui.components import barra_filtros, compartir_gasto, dialogo_compra_ahorro
from ui.components.campo_filtrable import CampoFiltrable
from ui.components.campo_monto import CampoMonto
from ui.components.color_chip import color_chip
from ui.theme.tokens import SharedFieldText, TypographyTokens
from utils.money import amount_display, amount_to_minor

# --- Configuración de layout ---
# Anchos generosos a propósito: Concepto y Categoría son los textos más
# largos — lo que no entra se trunca con ellipsis + tooltip (ver
# _texto_celda()) en vez de romper el alineado de la fila.
ANCHO_COL_CONCEPTO = 220
ANCHO_COL_BANCO = 160
ANCHO_COL_CATEGORIA = 180
ANCHO_COL_MONTO = 120
ANCHO_COL_FECHA = 110
# Ampliado (era 80) — a ese ancho el código de moneda ("ARS") quedaba
# cortado dentro del Dropdown (chevron + padding interno de la decoración
# le dejaban poco lugar al texto). Pedido explícito.
ANCHO_COL_MONEDA = 100
ANCHO_COL_COMPARTIR = 48
ANCHO_BOTON_CONFIRMAR = 48
ANCHO_TOOLBAR_BUSQUEDA = 200
ANCHO_TOOLBAR_FILTRO_BANCO = 150
ANCHO_TOOLBAR_FILTRO_CATEGORIA = 160
ESPACIADO_FILA = 8
LIMITE_TRANSACCIONES_DEL_MES = 500  # tope de per_page al pedir las transacciones del período
ANCHO_DIALOGO_ROUTING = 320  # mini-diálogos de Autotransferencia/Ahorro-Inversión (Tarea 1b)

# Categorías especiales de routing (Tarea 1b, ver docstring del módulo) —
# fuente de verdad de cuáles son y a qué modo rutean. Clave: (categoria_
# principal, subcategoria) tal como están en categorias — mismo criterio
# que services/categorias_service.py CATEGORIAS_PROTEGIDAS (matchear por
# nombre, no por id fijo, que varía entre bases).
_CATEGORIAS_ROUTING_ESPECIAL: dict[tuple[str, str], str] = {
    ("MOVIMIENTO CAPITAL", "Autotransferencia"): "autotransferencia",
    ("MOVIMIENTO CAPITAL", "Ahorro/Inversión"): "ahorro_inversion",
}
# Sentinel de opción "+ Crear nuevo objetivo" en el CampoFiltrable del
# mini-diálogo de Ahorro/Inversión — nunca puede colisionar con un id real
# de objetivos_ahorro (INTEGER PRIMARY KEY, siempre numérico como string).
_ID_OBJETIVO_NUEVO = "__nuevo__"


def _cuentas_no_credito(cuentas: list[dict]) -> list[dict]:
    """Excluye tarjetas de crédito — solo aparecen en Compras en cuotas."""
    return [c for c in cuentas if c["tipo"] != "credito"]


def build(
    page: ft.Page,
    accounts_service: AccountsService,
    categorias_service: CategoriasService,
    transaction_service: TransactionService,
    shared_expenses_service: SharedExpensesService,
    savings_service: SavingsService,
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
    monedas_por_codigo = {m["codigo"]: m for m in accounts_service.list_currencies()}
    todas_las_categorias = categorias_service.list_categories()
    # Normal: tipo IN ('ingreso', 'egreso'). Especial (Tarea 1b): las dos de
    # _CATEGORIAS_ROUTING_ESPECIAL, aunque su tipo real sea 'movimiento'
    # (ver docstring del módulo) — se agregan al FINAL de la lista, así el
    # default de campo_categoria_alta (opciones_categoria_alta[0]) sigue
    # siendo la primera categoría normal, sin cambios de comportamiento.
    categorias_especiales = [
        c for c in todas_las_categorias
        if (c["categoria_principal"], c["subcategoria"]) in _CATEGORIAS_ROUTING_ESPECIAL
    ]
    categorias = [c for c in todas_las_categorias if c["tipo"] in ("ingreso", "egreso")] + categorias_especiales
    mapa_categoria_a_routing: dict[str, str] = {
        str(c["id"]): _CATEGORIAS_ROUTING_ESPECIAL[(c["categoria_principal"], c["subcategoria"])]
        for c in categorias_especiales
    }

    # ------------------------------------------------------------
    # BARRA DE HERRAMIENTAS: período → banco → categoría → búsqueda
    # ------------------------------------------------------------
    # Extraída a ui/components/barra_filtros.py (componente compartido con
    # ui/screens/compras_cuotas.py) — acá solo se arman las listas ya
    # filtradas propias de esta pantalla (Banco sin tarjetas de crédito,
    # ver _cuentas_no_credito()) y se le pasa el mismo FILTER_SIZE que el
    # resto de los controles de filtro/herramientas, para que se vean
    # todos del mismo tamaño (pedido explícito, ver TypographyTokens.
    # FILTER_SIZE).
    barra_herramientas = barra_filtros.build(
        estado,
        on_cambio=on_cambio,
        cuentas_filtro=cuentas_todas_no_credito,
        categorias_filtro=categorias,
        ancho_filtro_banco=ANCHO_TOOLBAR_FILTRO_BANCO,
        ancho_filtro_categoria=ANCHO_TOOLBAR_FILTRO_CATEGORIA,
        ancho_busqueda=ANCHO_TOOLBAR_BUSQUEDA,
        espaciado=ESPACIADO_FILA,
        text_size=TypographyTokens.FILTER_SIZE,
    )

    # ------------------------------------------------------------
    # FILA DE ALTA (primera fila de la tabla, no una sección aparte —
    # se posiciona más abajo, dentro de filas_tabla; siempre presente,
    # se resetea sola tras guardar)
    # ------------------------------------------------------------

    def _refrescar_moneda_alta(cuenta_id: int) -> None:
        saldos = accounts_service.get_account(cuenta_id)["saldos"]
        dropdown_moneda_alta.options = [
            ft.dropdown.Option(key=s["moneda_codigo"], text=s["moneda_codigo"]) for s in saldos
        ]
        dropdown_moneda_alta.value = saldos[0]["moneda_codigo"] if saldos else None

    # Banco/Categoría de la fila de alta: CampoFiltrable (componente
    # propio, ver ui/components/campo_filtrable.py) — NO Dropdown(
    # enable_filter=True, editable=True) ni ft.AutoComplete. Se probaron
    # los dos controles nativos en rondas anteriores y ambos tuvieron
    # fricción real: Dropdown necesitaba un segundo Tab/Enter para poder
    # escribir, y AutoComplete directamente no mostraba ninguna sugerencia
    # al escribir (confirmado por el usuario corriendo la app, sin
    # investigar la causa a fondo — ver docstring de campo_filtrable.py y
    # docs/FLET_API_NOTES.md). CampoFiltrable es un TextField real por
    # dentro, así que SÍ tiene text_size/focus()/on_submit reales — a
    # diferencia de la ronda anterior, acá el encadenado de Enter vuelve a
    # pasar por Banco/Categoría sin perder nada.
    opciones_cuenta_alta = [(str(c["id"]), c["nombre"]) for c in cuentas_activas]
    opciones_categoria_alta = [(str(c["id"]), c["subcategoria"]) for c in categorias]

    def _on_seleccionar_cuenta_alta(id_cuenta: Optional[str]) -> None:
        if id_cuenta is not None:
            _refrescar_moneda_alta(int(id_cuenta))
            page.update()

    # ------------------------------------------------------------
    # MINI-DIÁLOGOS DE ROUTING ESPECIAL (Tarea 1b) — ver docstring del
    # módulo. Cerrar/abrir sigue el mismo patrón que
    # ui/components/compartir_gasto.py (page.show_dialog()/pop_dialog()).
    # ------------------------------------------------------------

    def _cerrar_dialogo(e=None) -> None:
        page.pop_dialog()

    def _abrir_dialogo_autotransferencia(
        cuenta_origen_id: int, moneda_codigo: str, monto: float, fecha_str: str, concepto: str, categoria_id: int,
    ) -> None:
        opciones_destino = [(str(c["id"]), c["nombre"]) for c in cuentas_activas if c["id"] != cuenta_origen_id]
        if not opciones_destino:
            _mostrar_error("No hay otra cuenta disponible como destino para la autotransferencia.")
            return

        campo_destino = CampoFiltrable(
            page, opciones_destino, on_seleccionar=lambda id_: None,
            placeholder="Cuenta destino", width=ANCHO_DIALOGO_ROUTING, autofocus=True,
        )

        def _confirmar(e=None) -> None:
            if not campo_destino.id_seleccionado:
                _mostrar_error("Seleccioná la cuenta destino de la lista de sugerencias.")
                return
            try:
                resultado = transaction_service.create_transfer(
                    date_str=fecha_str,
                    origin_account_id=cuenta_origen_id,
                    dest_account_id=int(campo_destino.id_seleccionado),
                    currency_code=moneda_codigo,
                    # Signo ignorado a propósito — ver docstring del
                    # módulo: create_transfer() ya arma egreso (origen) +
                    # ingreso (destino), el signo tipeado no decide nada acá.
                    amount=abs(monto),
                    category_id=categoria_id,
                    # create_transfer() no tiene parámetro `concept` (firma
                    # real revisada antes de implementar) — el concepto
                    # tipeado en la fila viaja como `notes`.
                    notes=concepto,
                )
            except (TransactionError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _cerrar_dialogo()
            _mostrar_ok(
                f"Autotransferencia registrada (movimientos #{resultado.data['out_transaction_id']} "
                f"→ #{resultado.data['in_transaction_id']})."
            )
            on_cambio()

        dialogo = ft.AlertDialog(
            modal=True,
            title=ft.Text("Autotransferencia — Cuenta destino"),
            content=ft.Container(width=ANCHO_DIALOGO_ROUTING, content=campo_destino.control),
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Confirmar"), on_click=_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

    def _abrir_dialogo_ahorro_inversion(
        cuenta_id: int, moneda_codigo: str, monto: float, fecha_str: str, concepto: str, categoria_id: int,
    ) -> None:
        """
        Dos modos dentro del MISMO AlertDialog (ver docstring del módulo,
        "Elegir activo específico"): arranca en modo simple (objetivo +
        "crear nuevo", como siempre); un link cambia `contenedor_dialogo.
        content` por el formulario completo compartido de
        ui/components/dialogo_compra_ahorro.py, sin cerrar y reabrir un
        diálogo nuevo. `estado_confirmar["actual"]` indirecciona qué función
        dispara el botón "Confirmar" de las actions (que NO se reconstruye
        al cambiar de modo) — evita tener que reemplazar `dialogo.actions`
        después de mostrado, cuya mutabilidad post-show_dialog() no está
        confirmada corriendo la app (a diferencia de `.content`, que este
        mismo archivo ya muta en vivo en varios lugares, ej. _celda_texto()).
        """
        cuenta = cuentas_por_id.get(cuenta_id)
        objetivos = savings_service.list_objetivos()
        opciones_objetivo = [(str(o["id"]), o["nombre"]) for o in objetivos] + [
            (_ID_OBJETIVO_NUEVO, "+ Crear nuevo objetivo")
        ]

        campo_nombre_nuevo = ft.TextField(
            label="Nombre del objetivo nuevo", visible=False, width=ANCHO_DIALOGO_ROUTING, dense=True,
        )

        def _on_seleccionar_objetivo(id_: Optional[str]) -> None:
            campo_nombre_nuevo.visible = (id_ == _ID_OBJETIVO_NUEVO)
            page.update()

        campo_objetivo = CampoFiltrable(
            page, opciones_objetivo, on_seleccionar=_on_seleccionar_objetivo,
            placeholder="Objetivo de ahorro", width=ANCHO_DIALOGO_ROUTING, autofocus=True,
        )

        def _on_exito(resultado) -> None:
            _cerrar_dialogo()
            _mostrar_ok(f"Aporte a ahorro registrado (movimiento #{resultado.entity_id}).")
            on_cambio()

        def _confirmar_modo_simple(e=None) -> None:
            if not campo_objetivo.id_seleccionado:
                _mostrar_error("Seleccioná un objetivo de ahorro de la lista de sugerencias.")
                return
            if campo_objetivo.id_seleccionado == _ID_OBJETIVO_NUEVO:
                nombre_nuevo = (campo_nombre_nuevo.value or "").strip()
                if not nombre_nuevo:
                    _mostrar_error("El nombre del objetivo nuevo no puede estar vacío.")
                    return
                objetivo_id = savings_service.create_objetivo(nombre=nombre_nuevo).entity_id
            else:
                objetivo_id = int(campo_objetivo.id_seleccionado)

            moneda = monedas_por_codigo.get(moneda_codigo)
            if moneda is None:
                _mostrar_error(f"Moneda '{moneda_codigo}' no encontrada.")
                return
            nombre_cuenta = cuenta["nombre"] if cuenta else f"cuenta #{cuenta_id}"
            try:
                activo = savings_service.get_or_create_reserved_cash_asset(
                    cuenta_nombre=nombre_cuenta, moneda_id=moneda["id"],
                )
                resultado = savings_service.register_purchase(
                    activo_id=activo.entity_id,
                    fecha=fecha_str,
                    # Signo ignorado a propósito, mismo criterio que
                    # Autotransferencia — ver docstring del módulo.
                    monto_total_minor=amount_to_minor(abs(monto), moneda["decimales"]),
                    asignaciones=[{"objetivo_id": objetivo_id, "porcentaje": 100.0}],
                    cuenta_id=cuenta_id,
                    categoria_id=categoria_id,
                    notas=concepto,
                )
            except (SavingsError, ValueError) as err:
                _mostrar_error(str(err))
                return
            _on_exito(resultado)

        texto_titulo = ft.Text("Ahorro/Inversión — Objetivo")
        estado_confirmar = {"actual": _confirmar_modo_simple}

        def _click_confirmar(e=None) -> None:
            estado_confirmar["actual"]()

        def _ir_a_modo_completo(e=None) -> None:
            # activo_fijo=None: acá SÍ se muestra el selector de activo +
            # "crear nuevo" (ver ui/components/dialogo_compra_ahorro.py) —
            # a diferencia del botón de Compra por fila de
            # ui/screens/ahorros.py, acá el usuario todavía no eligió
            # ningún activo específico, para eso es este modo.
            formulario = dialogo_compra_ahorro.construir(
                page, savings_service, accounts_service, categorias_service,
                on_exito=_on_exito,
                cuenta_id_inicial=cuenta_id, monto_inicial=abs(monto),
                fecha_inicial=fecha_str, notas_inicial=concepto,
            )
            texto_titulo.value = "Ahorro/Inversión — Activo específico"
            # formulario.contenido ya trae su propio ancho (más generoso
            # que ANCHO_DIALOGO_ROUTING, tiene más campos) — se saca el
            # width fijo del contenedor exterior para no doble-constreñir.
            contenedor_dialogo.width = None
            contenedor_dialogo.content = formulario.contenido
            estado_confirmar["actual"] = formulario.confirmar
            page.update()

        boton_modo_completo = ft.TextButton(
            content=ft.Text("Elegir activo específico"), on_click=_ir_a_modo_completo,
        )

        contenedor_dialogo = ft.Container(
            width=ANCHO_DIALOGO_ROUTING,
            content=ft.Column(
                [campo_objetivo.control, campo_nombre_nuevo, boton_modo_completo], tight=True, spacing=10,
            ),
        )

        dialogo = ft.AlertDialog(
            modal=True,
            title=texto_titulo,
            content=contenedor_dialogo,
            actions=[
                ft.TextButton(content=ft.Text("Cancelar"), on_click=_cerrar_dialogo),
                ft.ElevatedButton(content=ft.Text("Confirmar"), on_click=_click_confirmar),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dialogo)

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
            monto_con_signo = float((campo_monto_alta.texto or "").strip().replace(",", "."))
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
        if not campo_cuenta_alta.id_seleccionado:
            _mostrar_error("Seleccioná una cuenta de la lista de sugerencias.")
            return
        if not campo_categoria_alta.id_seleccionado:
            _mostrar_error("Seleccioná una categoría de la lista de sugerencias.")
            return
        if not dropdown_moneda_alta.value:
            _mostrar_error("Completá la moneda.")
            return

        # Routing por categoría (Tarea 1b) — reemplaza el guardado normal
        # SOLO si la categoría elegida es una de las dos especiales de
        # _CATEGORIAS_ROUTING_ESPECIAL. Abre el mini-diálogo correspondiente
        # y sale: el guardado real (y on_cambio()) lo dispara el propio
        # diálogo al confirmar, no acá.
        routing = mapa_categoria_a_routing.get(campo_categoria_alta.id_seleccionado)
        if routing is not None:
            argumentos_routing = (
                int(campo_cuenta_alta.id_seleccionado),
                dropdown_moneda_alta.value,
                monto_con_signo,
                campo_fecha_alta.value.strip(),
                campo_concepto_alta.value.strip(),
                int(campo_categoria_alta.id_seleccionado),
            )
            if routing == "autotransferencia":
                _abrir_dialogo_autotransferencia(*argumentos_routing)
            else:  # "ahorro_inversion"
                _abrir_dialogo_ahorro_inversion(*argumentos_routing)
            return

        try:
            resultado = transaction_service.create(
                date_str=campo_fecha_alta.value.strip(),
                concept=campo_concepto_alta.value.strip(),
                account_id=int(campo_cuenta_alta.id_seleccionado),
                category_id=int(campo_categoria_alta.id_seleccionado),
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

    campo_concepto_alta = ft.TextField(
        width=ANCHO_COL_CONCEPTO, dense=True, autofocus=True, text_size=TypographyTokens.TABLE_CONTENT_SIZE,
    )
    campo_categoria_alta = CampoFiltrable(
        page,
        opciones_categoria_alta,
        on_seleccionar=lambda id_: None,
        placeholder="Categoría",
        valor_inicial_id=opciones_categoria_alta[0][0] if opciones_categoria_alta else None,
        width=ANCHO_COL_CATEGORIA,
        # FILTER_SIZE (no TABLE_CONTENT_SIZE) — pedido explícito de que el
        # TextField interno de CampoFiltrable use el mismo token que el
        # resto de los controles de filtro/herramientas. Hoy ambos tokens
        # valen 11, así que sigue viéndose igual que el resto de la fila
        # de alta (ver TypographyTokens.FILTER_SIZE).
        text_size=TypographyTokens.FILTER_SIZE,
        on_avanzar=lambda: campo_monto_alta.focus(),
    )
    campo_cuenta_alta = CampoFiltrable(
        page,
        opciones_cuenta_alta,
        on_seleccionar=_on_seleccionar_cuenta_alta,
        placeholder="Banco",
        valor_inicial_id=opciones_cuenta_alta[0][0] if opciones_cuenta_alta else None,
        width=ANCHO_COL_BANCO,
        text_size=TypographyTokens.FILTER_SIZE,
        on_avanzar=lambda: campo_categoria_alta.focus(),
    )
    # CampoMonto (ui/components/campo_monto.py, CLAUDE.md §8) — resuelve
    # una fórmula "=..." tipeada acá a un número antes de que la lógica de
    # signo de abajo (_confirmar_alta) la reciba; no cambia esa lógica, ni
    # confirma la fila por sí solo (persistir_formula=False default,
    # on_confirmar no-op — la fila entera confirma junta, ver docstring del
    # módulo, "Fila de alta").
    campo_monto_alta = CampoMonto(
        page,
        on_confirmar=lambda monto_minor: None,
        width=ANCHO_COL_MONTO,
        hint_text=SharedFieldText.HINT_MONTO_CON_SIGNO,
        text_size=TypographyTokens.TABLE_CONTENT_SIZE,
        on_avanzar=lambda: campo_fecha_alta.focus(),
    )
    campo_fecha_alta = ft.TextField(
        width=ANCHO_COL_FECHA, dense=True, value=hoy.isoformat(), text_size=TypographyTokens.TABLE_CONTENT_SIZE,
    )
    dropdown_moneda_alta = ft.Dropdown(
        width=ANCHO_COL_MONEDA, dense=True, options=[], text_size=TypographyTokens.TABLE_CONTENT_SIZE,
    )
    if cuentas_activas:
        _refrescar_moneda_alta(cuentas_activas[0]["id"])
    boton_confirmar_alta = ft.IconButton(
        icon=ft.Icons.CHECK_CIRCLE,
        icon_color=ft.Colors.PRIMARY,
        tooltip="Agregar movimiento",
        on_click=_confirmar_alta,
    )

    # Encadenado de foco por teclado (Enter avanza al siguiente campo; el
    # último dispara el mismo guardado que el botón) — vuelve a pasar por
    # Banco/Categoría (CampoFiltrable sí tiene focus()/on_submit reales,
    # ver su on_avanzar= más arriba), a diferencia de la ronda con
    # AutoComplete.
    campo_concepto_alta.on_submit = lambda e: campo_cuenta_alta.focus()
    campo_fecha_alta.on_submit = lambda e: dropdown_moneda_alta.focus()
    # Dropdown NO tiene on_submit (confirmado leyendo el .py real de Flet
    # 0.86.5) — se usa on_select, mismo criterio que _celda_dropdown() más
    # abajo, donde on_select ya era el confirmador real.
    dropdown_moneda_alta.on_select = _confirmar_alta

    # Sin wrap=True: es una fila más de la tabla, con el mismo comportamiento
    # de layout que las filas de datos (_fila_transaccion), que tampoco
    # envuelven — ver docstring del módulo sobre wrap=True + expand=True.
    fila_alta = ft.Row(
        [
            campo_concepto_alta,
            campo_cuenta_alta.control,
            campo_categoria_alta.control,
            campo_monto_alta.control,
            campo_fecha_alta,
            dropdown_moneda_alta,
            boton_confirmar_alta,
        ],
        spacing=ESPACIADO_FILA,
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
            # enable_filter+editable: mismo criterio que la fila de alta.
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

            # ft.Dropdown NO tiene on_submit (confirmado leyendo el .py real
            # de Flet 0.86.5 instalado) — on_select es la ÚNICA vía real de
            # confirmar, nunca hubo un "Enter" que funcionara acá pese a lo
            # que decía el comentario anterior.
            dd.on_select = _confirmar
            contenedor.content = dd
            page.update()

        _mostrar()
        return contenedor

    def _celda_campo_filtrable(
        texto_mostrado: str,
        opciones: list[tuple[str, str]],
        valor_inicial: Optional[str],
        on_confirmar: Callable[[str], None],
        width: int,
    ) -> ft.Control:
        """
        Igual que _celda_dropdown() pero con CampoFiltrable (componente
        propio, ver ui/components/campo_filtrable.py) — usada hoy solo
        para Categoría (Banco no tiene edición inline en esta tabla, ver
        docstring del módulo).
        """
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
            def _confirmar(id_seleccionado: Optional[str]) -> None:
                if id_seleccionado is None:
                    return
                try:
                    on_confirmar(id_seleccionado)
                except (TransactionError, ValueError) as err:
                    _mostrar_error(str(err))
                    _mostrar()

            campo = CampoFiltrable(
                page,
                opciones,
                on_seleccionar=_confirmar,
                valor_inicial_id=valor_inicial,
                width=width,
                text_size=TypographyTokens.FILTER_SIZE,
                autofocus=True,
            )
            contenedor.content = campo.control
            page.update()

        _mostrar()
        return contenedor

    def _celda_monto(
        texto_mostrado: str,
        color_texto: Optional[str],
        monto_inicial_minor: int,
        decimales: int,
        on_confirmar: Callable[[int], None],
        width: int,
        weight=None,
    ) -> ft.Control:
        """
        Igual que _celda_texto() pero con CampoMonto (componente propio,
        ver ui/components/campo_monto.py) — calculadora de fórmulas
        integrada en la edición inline de Monto. A diferencia de
        _celda_texto() (on_confirmar recibe el texto crudo), acá
        on_confirmar ya recibe el monto resuelto en minor units.
        """
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
            def _confirmar(monto_minor: int) -> None:
                try:
                    on_confirmar(monto_minor)
                except (TransactionError, ValueError) as err:
                    _mostrar_error(str(err))
                    _mostrar()
                    raise

            campo = CampoMonto(
                page,
                on_confirmar=_confirmar,
                decimales=decimales,
                valor_inicial_minor=monto_inicial_minor,
                width=max(width - ANCHO_BOTON_CONFIRMAR, 40),
                autofocus=True,
            )

            def _on_click_confirmar(e: ft.ControlEvent) -> None:
                campo.confirmar()

            contenedor.content = ft.Row(
                [
                    campo.control,
                    ft.IconButton(icon=ft.Icons.CHECK, icon_color=ft.Colors.PRIMARY, on_click=_on_click_confirmar),
                ],
                spacing=0,
                tight=True,
            )
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

        celda_categoria = _celda_campo_filtrable(
            texto_mostrado=t["category_name"],
            opciones=[(str(c["id"]), c["subcategoria"]) for c in categorias],
            valor_inicial=str(t["categoria_id"]),
            on_confirmar=_confirmar_categoria,
            width=ANCHO_COL_CATEGORIA,
        )

        def _confirmar_monto(monto_minor: int) -> None:
            if monto_minor <= 0:
                raise ValueError("El monto debe ser mayor a 0.")
            # TransactionService.update() recibe el monto como float, no en
            # minor units (ver docstring del módulo, "amount y
            # currency_code viajan SIEMPRE juntos") — CampoMonto ya resolvió
            # la fórmula/número a minor units, se reconvierte acá.
            _guardar_campo(amount=monto_minor / (10 ** decimales), currency_code=t["currency_code"])

        celda_monto = _celda_monto(
            texto_mostrado=f"{signo} {amount_display(t['monto_minor'], decimales, t['currency_symbol'] or '')}",
            color_texto=color_monto,
            monto_inicial_minor=t["monto_minor"],
            decimales=decimales,
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
    # fila_alta es siempre la primera fila de la tabla (ver docstring del
    # módulo) — no una sección aparte arriba del encabezado.
    filas_tabla: list[ft.Control] = [fila_alta, ft.Divider(height=1)]
    if transacciones:
        for i, t in enumerate(transacciones):
            if i > 0:
                filas_tabla.append(ft.Divider(height=1))
            filas_tabla.append(_fila_transaccion(t))
    else:
        filas_tabla.append(ft.Text("No hay movimientos para mostrar.", italic=True, color=ft.Colors.OUTLINE))

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
                encabezado_columnas,
                ft.Divider(height=1),
                ft.Column(filas_tabla, spacing=ESPACIADO_FILA),
            ],
            spacing=4,
            scroll=ft.ScrollMode.AUTO,
        ),
    )
