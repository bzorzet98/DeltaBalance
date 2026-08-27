# Próximos pasos — handoff para la nueva sesión

Este documento existe porque el contexto de conversación con Claude Code no
persiste entre sesiones. Antes de arrancar cualquier tarea, la nueva sesión
debe leer (en este orden): `CLAUDE.md`, `docs/ARCHITECTURE.md`,
`docs/DATA_MODEL_DECISIONS.md`, `docs/FLET_API_NOTES.md`, y este archivo.

## Estado actual

Toda la capa de datos (Fases 0-3: schema, repositorios, servicios de negocio)
está completa y probada — ver `docs/ARCHITECTURE.md` para el listado. La Fase
5 (UI Flet) está en curso. Hoy existen y funcionan: shell con sidebar
colapsable, Dashboard con patrimonio total + Registro de transacciones,
Estadísticas (gráfico de torta movido desde el dashboard), Cuentas (alta/
edición/archivado/eliminado, multi-moneda, color por cuenta), Categorías
(alta/edición/desactivado, con protección de categorías usadas por nombre en
el código), y una versión inicial de Compras en cuotas (lista + alta, sin el
formato de registro pulido todavía).

## Tarea 1 (prioridad inmediata): terminar la fila de carga del Registro

Hoy `ui/components/registro_transacciones.py` tiene DOS elementos separados:
una "fila de alta" con labels propios arriba de la tabla, y la tabla de
movimientos debajo. El diseño acordado era que fueran la MISMA fila: la
primera fila visual de la tabla es siempre una fila en blanco editable, con
el mismo estilo/anchos de columna que las demás filas (no una sección aparte
con labels). Al confirmar (Enter en el último campo, o el ícono de check en
esa fila), se guarda la transacción vía `TransactionService.create()` y esa
misma fila se limpia a su estado en blanco (Fecha=hoy, resto vacío), lista
para la próxima carga, con foco en el primer campo. No se agrega ninguna fila
nueva al layout — es siempre la misma fila #1, siempre en blanco cuando no se
está escribiendo. Si el guardado falla, la fila NO se limpia, se muestra el
error en SnackBar y el usuario corrige sobre los mismos valores.

## Tarea 1b (extensión del Registro de transacciones): categorías especiales
Autotransferencia y Ahorro/Inversión

Decisión tomada: la fuente de verdad para cargar CUALQUIER movimiento
(incluidas transferencias entre cuentas y aportes a ahorro) debe ser el
Registro de transacciones — el usuario prefiere cargar todo desde la misma
pestaña en vez de saltar entre pantallas, especialmente porque suele cargar
en lote una vez por semana. Mismo mecanismo de routing por categoría ya
construido para "Impuesto tarjeta"/"Recargo tarjeta"/"Ajuste/Reintegro
tarjeta" en Compras en cuotas.

1. Dos categorías especiales protegidas nuevas: "Autotransferencia" y
   "Ahorro/Inversión". Revisar primero la firma real de
   `TransactionService.create_transfer()` (cuenta origen/destino, cómo tagea
   las dos filas con tag='autotransferencia', si necesita categoria_id o no)
   antes de implementar — no asumir.

2. Al confirmar una fila del Registro con categoría "Autotransferencia": en
   vez de guardar una transacción simple, abrir un mini-diálogo pidiendo
   Cuenta destino (CampoFiltrable, excluyendo la cuenta origen ya elegida en
   la fila), y llamar a `create_transfer()` con los datos ya cargados
   (monto, fecha, concepto) más la cuenta destino elegida.

3. Al confirmar una fila con categoría "Ahorro/Inversión": abrir un
   mini-diálogo pidiendo el Objetivo de ahorro (CampoFiltrable con los
   `objetivos_ahorro` existentes, más opción "+ Crear nuevo" con solo el
   nombre). Al confirmar: usar (o crear si no existe todavía) un
   `activo_financiero` genérico tipo='otro' representando "Efectivo
   reservado en <nombre de cuenta>" para esa cuenta específica (uno por
   cuenta, reutilizado en cargas futuras — buscar primero si ya existe antes
   de crear uno nuevo), y llamar a
   `SavingsService.register_purchase(activo_id=<ese activo>, cuenta_id=<la
   cuenta de la fila>, monto_total_minor=<el monto>, asignaciones=[{objetivo_id,
   porcentaje:100}])` — que ya crea atómicamente el movimiento_activo, la
   asignación, Y la transacción real vinculada (mecanismo de la Tarea 6b).

4. Esta tarea depende de que la Tarea 6b (columna `transaccion_id` en
   `movimientos_activo`, parámetro `cuenta_id` opcional en
   `register_purchase()`) esté implementada primero — hacerlas en ese orden.



Aplicar el mismo patrón de tabla-con-fila-de-alta-en-blanco (ya construido en
la Tarea 1) a `ui/screens/compras_cuotas.py`, adaptado a los campos propios de
`FeesService.create_purchase()`: cuenta/tarjeta, concepto/comercio, categoría,
monto total (siempre positivo, recordar la decisión ya tomada: la carga
negativa en esta pantalla es un AJUSTE contra el resumen vía
`add_extra_charge()`, no una compra nueva — ver conversación previa,
documentado en el código existente), cantidad de cuotas, fecha de compra,
moneda. Reusar el componente de tabla de la Tarea 1 si quedó suficientemente
genérico, o adaptarlo — evaluar en el momento.

No hace falta ninguna sección de "agregar cuota de pago" manual (decisión
tomada: ya cubierto por `FeesService.confirm_fee()`/`pay_statement()`).

## Bugs conocidos pendientes (resolver junto con la Tarea 3)

- **Filtro global de Banco en Compras en cuotas muestra todas las cuentas,
  no solo las de crédito.** El dropdown de la barra de herramientas (filtro
  global, distinto del CampoFiltrable de la fila de alta, que sí prioriza
  tarjetas correctamente) no está aplicando el filtro por tipo='credito'.
  Debería mostrar únicamente cuentas tipo crédito, ya que es lo único
  relevante para filtrar compras en esta pantalla.
- **Campo Monto en Compras en cuotas**: debe tener el mismo tamaño y mismo
  texto (placeholder/formato) que el campo Monto del Registro de
  transacciones en el dashboard — hoy están inconsistentes entre las dos
  pantallas.

## Tarea 3: cargos extra del resumen — vía categorías especiales (rediseñado)

DISEÑO CAMBIADO respecto a la versión original: en vez de navegar al detalle
de un resumen específico para cargar impuestos/recargos/ajustes, se resuelve
con TRES categorías especiales protegidas: "Impuesto tarjeta", "Recargo
tarjeta", "Ajuste/Reintegro tarjeta" (bajo el categoria_principal que
corresponda, a definir en el momento). Se agregan a la lista de categorías
protegidas ya existente en `categorias_service.py` (mismo mecanismo que
"Sueldo").

En la fila de carga de Compras en cuotas, el campo Categoría (CampoFiltrable,
sin cambios de control) puede elegir estas tres además de las normales. Al
confirmar la fila:
- Si la categoría elegida es una de las tres especiales: NO es una compra
  nueva. Se resuelve el resumen correspondiente a esa cuenta/mes vía
  `FeesService.open_statement()` (idempotente) y se llama a
  `add_extra_charge(statement_id, concept=<concepto tipeado>, charge_type=
  <mapeo categoría→tipo: Impuesto tarjeta→'impuesto', Recargo tarjeta→
  'recargo', Ajuste/Reintegro tarjeta→'ajuste'>, amount_minor=<el monto tal
  cual se tipeó, con su signo>)`. El campo Cuotas se deshabilita/ignora
  (igual que antes). Si el resumen está cerrado/pagado, mostrar el error real
  de StatementAlreadyClosedError/StatementAlreadyPaidError en SnackBar.
- Si la categoría es cualquier otra: comportamiento normal ya existente
  (compra nueva vía create_purchase(), monto siempre positivo — ya no hace
  falta la regla vieja de "negativo=ajuste" para categorías normales, queda
  reemplazada por esto).

Ya NO hace falta ninguna navegación a un detalle de resumen separado ni una
sección "Cargos extra de este resumen" aparte — todo se carga desde la misma
fila de siempre, eligiendo la categoría correspondiente. Si más adelante hace
falta ver/eliminar cargos ya cargados fuera de la fila de alta, evaluarlo
como mejora futura, no bloquea esta tarea.

## Tarea 4: dashboard segregado por tarjeta de crédito

Vista (probablemente dentro de la propia pantalla de Compras en cuotas, o una
subsección de Estadísticas — decidir en el momento) que muestre, mes a mes,
el total a pagar por cada tarjeta de crédito: suma de `monto_cuota_minor` de
`cuotas_credito` agrupada por `cuenta_id`, MÁS la suma de los cargos extra
del resumen de esa cuenta/mes (Tarea 3) — el total real que pagás en esa
tarjeta ese mes es cuotas + impuestos/recargos/ajustes, no solo las cuotas
solas. Requiere un método de agregación nuevo (candidato:
`DashboardService.get_cuotas_por_tarjeta(mes, anio)` o un método dedicado en
`FeesService`, evaluar cuál encaja mejor con el criterio ya usado de "reporte
= vive en el service, no en el repositorio"). Mismo criterio de no mezclar
monedas distintas en una suma que se aplicó en todo el resto del dashboard.

Refinamiento acordado (no implementado todavía): el dashboard debe ser
GENÉRICO respecto a qué tarjetas/cuentas muestra — en vez de listar siempre
todas las cuentas tipo='credito' existan o no movimientos ese período,
detectar dinámicamente para el mes/año consultado cuáles tarjetas tienen
actividad real (al menos una cuota_credito con mes_vencimiento/
anio_vencimiento en ese período) y armar el desglose solo con esas. El mismo
patrón de detección dinámica por período (en vez de una lista fija de
cuentas) se propuso también para el desglose de "Patrimonio total" del
Registro de transacciones — evaluar si aplica ahí también al momento de
implementar esta tarea, como forma de segregar mejor el patrimonio por
cuenta/período real en vez de mostrar siempre todas las cuentas activas.

## Tareas 5-10: pantallas que faltan para la primera versión completa

Todas tienen el backend ya construido y probado — es trabajo de UI puro,
reusando el patrón de tabla-con-fila-de-alta-en-blanco y CampoFiltrable ya
perfeccionados en las Tareas 1-2. Orden sugerido por valor/dependencia, no
estricto:

- **Tarea 5 — Presupuestos**: pantalla para cargar/editar `monto_estimado_minor`
  por categoría/mes vía `PresupuestosService.set_budget()`, listado con
  `list_budgets()`, y mostrar la comparación estimado vs. real que ya calcula
  `DashboardService.get_comparacion_presupuesto()` (hoy solo se usa
  internamente en el dashboard, falta una vista dedicada). Incluir
  `copy_period()` con el flag `solo_recurrentes` como acción ("copiar
  presupuestos recurrentes al mes siguiente").

- **Tarea 6 — Ahorros**: la más grande. Pantallas/secciones para
  `activos_financieros` (alta/listado), registrar movimientos (compra/venta/
  rendimiento vía `SavingsService`), gestión de `objetivos_ahorro`, y una
  vista de balance por objetivo (`get_objetivo_balance()`).

### Tarea 6b (extensión de schema/service, resolver antes o junto con la UI
de la Tarea 6): vínculo entre ahorro y cuenta real

Decisión tomada tras discutirlo con el usuario: los aportes/retiros de
ahorro DEBEN poder generar una transacción real que descuente/acredite la
cuenta de origen, porque el patrimonio total del dashboard tiene que
coincidir siempre con lo que las apps de los bancos muestran de verdad — un
ahorro "aparte" que no toca el saldo real generaría una desincronización
inaceptable. El usuario diferenció dos casos reales: cuentas donde reserva
plata dentro del mismo saldo global (ej. Mercado Pago) y luego la
"reingresa" como ingreso cuando la usa; y cuentas exclusivas de ahorro/
inversión (ej. FCI de Cocos) donde la plata realmente se transfiere afuera.
Ambos casos se resuelven con el mismo mecanismo de abajo.

1. Migración de columna (schema_migrations.py, no schema.sql directo):
   `movimientos_activo.transaccion_id INTEGER REFERENCES transacciones(id)`,
   nullable — mismo patrón exacto que `recibos_sueldo.transaccion_id`. Se
   llena cuando el movimiento de ahorro generó una transacción real
   vinculada; queda NULL si el movimiento fue puramente informal (compatible
   con lo que ya existe hoy, no rompe nada).

2. `SavingsService.register_purchase()` y `register_sale()` ganan un
   parámetro opcional `cuenta_id`: si se pasa, dentro de la misma
   transacción atómica que ya crea el movimiento_activo (+ asignaciones si
   aplica), también crea una transacción real vía TransaccionesRepository
   (egreso para register_purchase/aporte, ingreso para register_sale/
   retiro), categoría a definir (podría ser una de las categorías especiales
   ya creadas o una nueva "Ahorro/Reserva" — evaluar en el momento), y
   vincula transaccion_id al movimiento_activo. Si no se pasa cuenta_id,
   comportamiento actual sin cambios (movimiento puramente informal, como ya
   funciona).

3. Método de agregación nuevo para la pantalla de Ahorros (Tarea 6): "cuánto
   de mi saldo en la cuenta X corresponde a cada objetivo de ahorro" — join
   entre movimientos_activo.transaccion_id → transacciones.cuenta_id,
   agrupado por objetivo vía asignaciones.

- **Tarea 7 — Préstamos** (POSPUESTA — sin datos reales de préstamos todavía,
  no tiene sentido pulir esta pantalla ahora; retomar más cerca de la
  migración de datos históricos, momento en el que sí va a haber préstamos
  reales para cargar): alta de préstamo (`LoansService.create_loan()`,
  mostrando la tabla de amortización generada), vista de cuotas con
  `adjust_installment()` para ajustar mes a mes contra lo que cobra el banco
  de verdad (decisión ya tomada: la tabla generada es un punto de partida
  estimado, editable).


- **Tarea 8 — Empleos / Obra social**: alta de empleo
  (`EmpleosService.create_employment()`), carga de recibos de sueldo
  (`create_receipt()`, con el flag opcional de generar la transacción de
  ingreso vinculada), descuentos programados
  (`schedule_discount()`/`apply_discount()`).

- **Tarea 9 — Hogares / Gastos compartidos como pantalla propia**: hoy solo
  existe el ícono inline en los registros. Falta una vista dedicada con el
  saldo neto (`get_net_balance()`), historial completo
  (`list_shared_expenses()`), y saldar (`settle_expense()`) sin depender de
  encontrar la transacción puntual.

- **Tarea 10 — pulido general**: una vez que las Tareas 5-9 estén andando,
  revisión de consistencia visual/tipográfica entre todas las pantallas
  nuevas y las ya construidas (mismo criterio que se aplicó ya varias veces
  entre Registro y Compras en cuotas).

## Tarea 11: "Disponible real" en el dashboard (proyección tipo Sueldo Neto)

Retoma la fórmula original del diseño de hace semanas. Métrica nueva para el
Dashboard: Patrimonio total (por moneda) MENOS la suma de presupuestos no
gastados del mes actual (monto_estimado_minor - monto_ejecutado_minor, solo
cuando estimado > ejecutado, sumado por categoría) MENOS deudas activas tipo
'en_contra' pendientes (monto_pendiente_minor) MENOS cuotas de tarjeta
pendientes del mes actual + cargos extra del resumen. Es agregación cruzando
varios services (Presupuestos, Deudas, Compras en cuotas) — evaluar si vive
en DashboardService o amerita un service de agregación propio. No mezclar
monedas. Depende de que existan datos reales cargados en los módulos
correspondientes para tener sentido probarlo con un escenario completo.

## Tarea 12: ahorros recurrentes mensuales (diseño resuelto)

Decisión final tras discutirlo: NO se sincroniza con `presupuestos` (evita
categorías sintéticas por objetivo y evita que
`get_comparacion_presupuesto()` tenga que leer de dos tablas distintas según
el tipo de fila — mantiene ambos módulos simples y desacoplados).

En su lugar: agregar a `objetivos_ahorro` un campo opcional
`aporte_mensual_objetivo_minor` (meta de aporte mensual, editable en
cualquier momento, sin mecanismo de "por mes" — cambiarlo aplica hacia
adelante). En la pantalla de Presupuestos, una sección de solo lectura
"Ahorros programados este mes" que, para cada objetivo con este campo
seteado, compara el aporte mensual objetivo contra la suma real de
`movimientos_activo` tipo 'compra' asignados a ese objetivo en el mes
seleccionado (vía `asignaciones`). Si un mes no se aporta nada, simplemente
no hay movimiento cargado ese mes — no existe ninguna fila de "presupuesto de
ahorro" que eliminar o ajustar, la meta es constante y la realidad se mide
por lo efectivamente aportado. Requiere: migración de columna en
`schema_migrations.py` para `aporte_mensual_objetivo_minor` en
`objetivos_ahorro`, un método de agregación (candidato en `SavingsService` o
`DashboardService`, evaluar) para el total real aportado por objetivo en un
mes dado.

## Tarea 6d: rediseño de la pantalla de Ahorros a formato Registro
(insertar ANTES de la Tarea 6c — 6c se construye mejor sobre esta estructura
nueva)

Decisión tomada: en vez de organizar la pantalla por activo financiero (con
tres íconos de acción por cada uno), reorganizarla igual que el resto de la
app — un dashboard resumen arriba + una tabla tipo Registro abajo.

**Dashboard arriba, TRES bloques (mismo estilo visual que "Patrimonio
total"):**
- "Por objetivo": una tile por objetivo con su saldo neto
  (`get_objetivo_balance()`). Click/expand muestra el detalle por cuenta ya
  construido (`get_balance_por_cuenta()`).
- "Por tipo de ahorro": una tile por tipo de activo con su saldo neto
  (`get_balance_por_tipo()`, nuevo).
- "Por activo" (NUEVO, agregado tras revisión): una tile por
  activo_financiero con su total REAL agregado (cantidad si aplica + monto),
  sin segregar por objetivo — es la vista que coincide con lo que muestra la
  app del broker/banco (ej. "NVDA: 3 unidades" en un solo número, no
  repartido). Nuevo método de agregación en SavingsService (candidato
  `get_balance_por_activo()`), sumando compras-ventas por activo_id.

**Registro abajo (tabla, mismo patrón que Registro de transacciones/Compras
en cuotas):**
- Barra de herramientas: período navegable, filtro por objetivo, filtro por
  tipo de activo, filtro por tipo de movimiento (compra/venta/rendimiento),
  búsqueda.
- Columnas: Fecha, Activo, Tipo de movimiento (color: venta=rojo,
  rendimiento=verde, compra=neutral), Cantidad (si aplica, "—" si no),
  Monto, Moneda, Objetivo(s) (una etiqueta por asignación de ese
  movimiento — un movimiento repartido entre varios objetivos muestra UNA
  fila con varias etiquetas apiladas, nunca fragmentado en filas separadas).
- Botón "+" único (no más íconos por activo) con un menú corto de TRES
  opciones: Compra (abre el `dialogo_compra_ahorro` ya unificado),
  Rendimiento (sin cambios, reparto automático proporcional, sin selección
  manual), **Egreso general** (reemplaza el antiguo "Venta" — ahora con el
  MISMO editor de múltiples filas objetivo+monto/porcentaje que ya tiene
  Compra, en vez de forzar un único objetivo al 100%). Requiere extender
  `SavingsService.register_sale()` para aceptar una lista de asignaciones
  en vez de un solo objetivo_id — cambia comportamiento ya probado en
  `verify_savings_service.py` (actualizar los casos existentes de venta a
  un solo objetivo para que sigan siendo un caso válido dentro de la nueva
  firma con lista, no que se rompan).
  NOTA: el campo de moneda de este diálogo queda con el comportamiento
  actual (moneda de referencia del activo, no editable) hasta que la Tarea
  6c implemente moneda por movimiento — en ese momento este mismo diálogo
  gana el campo de moneda editable sin necesidad de rediseñarlo de nuevo.



Sección de Activos financieros: se mantiene, pero se reduce a solo
alta/listado simple (sin los íconos de acción) — la gestión de movimientos
pasa a vivir enteramente en el Registro de esta pantalla.



Cluster de tres decisiones relacionadas, definidas en conversación:

1. **Moneda por movimiento, no por activo.** Hoy `activos_financieros.
   moneda_id` fija la moneda de todos sus movimientos — no permite comprar
   una acción en ARS y venderla en USD (caso real del usuario). Migración
   de columna: `movimientos_activo.moneda_id INTEGER REFERENCES
   monedas(id)`, obligatoria de ahora en más en `crear()`. `activos_
   financieros.moneda_id` se conserva pero pasa a ser "moneda de
   referencia" (default sugerido al cargar un movimiento nuevo, siempre
   editable — precarga sin fricción para FCI/plazo_fijo/reserva, donde la
   moneda no cambia nunca en la práctica; totalmente libre para acciones,
   donde compra y venta pueden diferir). `get_objetivo_balance()`/
   `get_balance_por_cuenta()` deben agrupar por moneda real del movimiento,
   nunca mezclar ARS con USD en una suma — esto resuelve además la
   limitación conocida ya documentada de que esos métodos sumaban en minor
   units sin distinguir moneda.
   NO se implementa en esta tarea: cálculo automático de ganancia/pérdida
   real ajustada por tipo de cambio usando dolar_oficial_momento_minor de
   compra vs. venta — decisión explícita de dejarlo para una sesión aparte,
   dedicada.

2. **Objetivo siempre obligatorio, con "General" como respaldo.** La
   "compra libre" sin objetivo (hoy válida en `register_purchase()`)
   desaparece — SIEMPRE hace falta al menos un objetivo, sin excepción, ni
   siquiera desde la pantalla completa de Ahorros. Se crea automáticamente
   (si no existe) un `objetivo_ahorro` protegido llamado **"General"**
   (mismo patrón get_or_create que `get_or_create_reserved_cash_asset()`),
   que sirve de destino por default cuando el usuario todavía no sabe a qué
   objetivo específico va el aporte. Cambia el comportamiento ya probado en
   `verify_savings_service.py` (los casos de "compra libre" deben
   actualizarse, no seguir esperando que sea válido sin objetivo).

3. **Redistribución entre objetivos, sin tocar el movimiento subyacente.**
   Nuevo método en SavingsService (candidato:
   `redistribuir_asignacion(movimiento_id, objetivo_origen_id,
   objetivo_destino_id, monto_a_mover_minor)`): reduce la asignación
   existente del objetivo origen en ese movimiento (elimina la fila si
   llega a 0) y aumenta (o crea) la asignación del objetivo destino en el
   mismo movimiento, por el monto indicado — atómico, nunca toca
   `movimientos_activo` (ni monto, ni moneda, ni activo). Requiere UI en
   Ahorros (sección o acción nueva, "Reasignar entre objetivos") para
   elegir movimiento de origen, objetivo actual, objetivo nuevo, y cuánto
   mover.

Depende de la unificación de popups ya en curso (register_purchase() vía
Registro y Ahorros usando el mismo formulario) — evaluar si conviene
completar esa tarea primero y aplicar este cluster después, o si se pueden
hacer juntas. Recomendado: primero cerrar la unificación de popups, después
este cluster, para no mezclar dos refactors grandes sobre el mismo código a
la vez.

## Convenciones que la nueva sesión debe seguir sin que se le repitan

- Nunca ejecutar código (ni python -c, ni ast.parse, ni py_compile, ni nada) —
  el usuario ejecuta y reporta resultados.
- Toda pantalla de `ui/screens/` define sus números mágicos como constantes
  nombradas al principio del archivo.
- Antes de usar cualquier API de Flet cuya forma no esté confirmada en
  `docs/FLET_API_NOTES.md`, no adivinar — dejarlo marcado como pendiente de
  confirmar corriendo la app, o pedirle al usuario que corra un chequeo
  puntual (mismo patrón ya usado para PieChart, AutoComplete, ElevatedButton).
- Los `verify/` en `verify/` (organizados por subcarpeta de dominio) siguen
  siendo la fuente de verificación del backend — no se tocan salvo que una
  tarea toque directamente esa lógica.