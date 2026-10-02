# Decisiones de modelo de datos — recapitulación

Este documento resume las decisiones de diseño acordadas antes de escribir código,
para que queden como referencia persistente (no solo en el chat). Se actualiza cada vez
que se toca `schema.sql`.

## 1. Estimado vs. real
`presupuestos.monto_ejecutado_minor` se actualiza automáticamente sumando
`transacciones` de esa categoría/mes — nunca se carga a mano en dos lugares.

## 2. Gastos compartidos (familia) — ✅ implementado en schema.sql
Tabla `gastos_compartidos`, generalizada con `origen_tipo` ∈
(`transaccion`, `compra_cuotas`, `cuota_credito`):

- `coeficiente_deuda` es variable, no fijo 50/50.
- `compras_cuotas.modo_deuda` ∈ (`total_unico`, `prorrateado`) define si la deuda
  compartida se genera una vez sobre el total, o una fila por cuota.
- Si `modo_deuda = 'prorrateado'`, el reintegro (`compras_cuotas.monto_reintegro_minor`)
  se descuenta prorrateado entre cuotas antes de aplicar el coeficiente.
- **Orquestación de ambos modos — ✅ implementada**:
  `SharedExpensesService.add_shared_purchase(compra_id, hogar_id, pagador,
  coeficiente_deuda)`. Estas columnas estuvieron en el schema mucho tiempo sin
  ningún método que las usara — confirmado por lectura de
  `services/shared_expenses_service.py` y `services/fees_service.py` antes de
  escribirlo, no había ninguna orquestación real. Vive en `SharedExpensesService`
  (no en `FeesService`) porque el resultado son filas de `gastos_compartidos`, tabla
  que ya posee ese service; lee `ComprasCuotasRepository`/`CuotasCreditoRepository`
  solo para lectura. En modo `prorrateado`, el reintegro por cuota se calcula como
  `round(monto_reintegro_minor / total_cuotas)` aplicado igual a cada cuota (no
  reparte el resto de la división para que la suma dé exacto — ver docstring del
  método); las cuotas que ya tienen un gasto compartido asociado se SALTEAN en vez
  de abortar todo el lote. `fecha` de cada gasto en modo prorrateado es el primer
  día del `mes_proyectado`/`anio_proyectado` de esa cuota (`cuotas_credito` no
  guarda un día exacto de vencimiento, solo mes/año).
- `compras_cuotas.monto_reintegro_minor` y `compras_cuotas.modo_deuda` se agregan vía
  `db/schema_migrations.py`, no como `ALTER TABLE` directo en `schema.sql`: SQLite no
  soporta `ADD COLUMN IF NOT EXISTS`, y `schema.sql` se reaplica completo en cada
  `DatabaseManager.inicializar()` — un `ALTER TABLE` suelto ahí rompería con
  "duplicate column name" a la segunda corrida.
- `monto_adeudado_minor` puede ser **negativo**: si el reintegro de una cuota supera su
  monto, la deuda se invierte sola. El saldo neto entre dos personas es un único
  `SUM(monto_adeudado_minor)`, nunca dos deudas paralelas a reconciliar. Ese cálculo
  vive en la vista `vw_saldo_neto_hogar`, agrupado por `hogar_id`.
- Categoría (`categoria_id`) y "es compartido" son dimensiones ortogonales — no se
  duplica el catálogo de categorías.
- La sincronización remota (Supabase) solo mueve `gastos_compartidos`. Nunca
  `transacciones` completas.
- `hogares` + `hogar_miembros` modelan el vínculo familiar: `hogares.codigo_invitacion`
  es el código corto de invitación (la generación del código en sí es responsabilidad
  de la capa de servicio, no del schema); `hogar_miembros` es la tabla puente
  hogar↔persona con `porcentaje_default` opcional.
- **Nota deliberada sobre `usuario_local` / `pagador`**: son strings simples (no FK a
  una tabla de usuarios) porque todavía no existe autenticación real en la app — eso
  llega recién con `sync/` y Supabase Auth (ver sección 8). No es un descuido: se
  decidió explícitamente no adelantar infraestructura de auth antes de necesitarla.
  Cuando `sync/` exista, la migración de estos strings a un id de usuario real es un
  problema conocido y aceptado, no una sorpresa.

## 3. Ahorro reservado para resúmenes futuros
Se modela como un `objetivos_ahorro` (ver sección 4) con destino a un `resumenes_tarjeta`
correspondiente — nunca como `ingresos_proyectados`, para no contar la plata dos veces.

## 4. Savings con destino múltiple — ✅ implementado en schema.sql
- `activos_financieros`: definición del instrumento (SPY, FCI, plazo fijo).
- `movimientos_activo`: cada compra/venta/rendimiento, con cotización y
  `dolar_oficial_momento_minor` para calcular rendimiento real en USD.
- `objetivos_ahorro`: terreno, moto, vacaciones, etc.
- `asignaciones`: puente N:M entre movimientos y objetivos, con `porcentaje` (la suma
  por movimiento no puede superar 100%).
- Rendimientos se reparten automáticamente proporcional a las asignaciones vigentes del
  activo al momento del rendimiento.
- **Ventas/retiros**: asignación explícita a un objetivo puntual (el usuario elige de
  qué "sobre" sale la plata) — no se prorratea automático.
- **Pendiente**: la validación de que la suma de `porcentaje` por `movimiento_id` no
  supere el 100% no se puede expresar en SQLite con un CHECK de columna (no ve otras
  filas de la tabla) — queda para la capa de servicio (`SavingsService`, fase futura),
  que debe validarla antes de insertar/actualizar en `asignaciones`.

## 5. Deudas duras (hipotecario, prendario) — ✅ implementado en schema.sql
`prestamos` + `cuotas_prestamo`, separado de `deudas` (que es informal, entre
personas). Amortización francesa o alemana, capital e interés discriminados por cuota,
generada completa desde el alta. Individuales por ahora; un préstamo compartido se
resuelve con deudas periódicas manuales cruzadas, sin tabla nueva.
- `cuotas_prestamo` se genera completa en el alta (todas las cuotas del plazo, con
  capital e interés ya discriminados) vía `LoansService` (fase futura) — mismo patrón
  que ya usa `compras_cuotas` → `cuotas_credito` al crear una compra en cuotas. El
  schema solo almacena el resultado del cálculo de amortización, nunca lo calcula.

## 6. Inflación — ✅ implementado en schema.sql
`indices_inflacion(mes, anio, valor_indice)` para ajustar series históricas a moneda
constante.
- El ajuste en sí (dividir/multiplicar una serie histórica por el índice
  correspondiente) es lógica de servicio o de `lab/`, no del schema — la tabla solo
  guarda los valores del índice cargados.

## 7. Métrica de bienestar
Pospuesta hasta tener datos reales cargados. Vive en `lab/`, nunca en `services/` ni
`repositories/`. Va a necesitar como mínimo una tabla `bienestar_mensual` con
autoevaluación subjetiva del usuario — no diseñada en detalle todavía.

## 8. Sincronización familiar
Supabase (Postgres + Auth + RLS). Vínculo por código de invitación (`hogares` +
`hogar_miembros`). Polling al abrir la app y cada 5 minutos, no realtime.
- Actualizado (tarea "Módulo de sincronización con Supabase"): ya no viaja solo
  `gastos_compartidos` — sube todo lo del usuario (tablas privadas y compartidas) y
  baja solo lo propio. Ver hacerlo real y bajar lo del otro miembro: sección 24.

## 9. Regla de edición/borrado
Ver `CLAUDE.md` sección 4 — ventana de corrección temprana según si el registro ya
generó dependencias con estado propio.

**Edición de `compras_cuotas` — ✅ implementado en `FeesService.update_purchase()` /
`update_purchase_cuotas()`, sin cambio de schema.** Por campo:
- `concepto`, `categoria_id` y `fecha_compra` dentro del mismo mes: siempre (son
  descriptivos; las cuotas guardan mes/año, no el día).
- `cuenta_id`: solo si todas las `cuotas_credito` siguen en `pendiente` — una cuota
  `en_resumen`/`pagado` pertenece al resumen de ESA tarjeta.
- `fecha_compra` a otro mes: el cronograma se regenera desde el mes nuevo (borrar +
  recrear cuotas en la misma transacción), solo si todas siguen `pendiente` y
  ninguna tiene un gasto compartido por cuota (`origen_tipo = 'cuota_credito'`).
- `total_cuotas` (`update_purchase_cuotas()`): mismo total, cuotas borradas y
  recreadas de `round(total / nueva_cantidad)`. Mismas condiciones que mover la
  fecha, más reparto por default (ver abajo). Una compra compartida en modo
  `total_unico` sí se puede: ese gasto sale del total, que no cambia.
- `monto_total_minor`: todas las cuotas en `pendiente`, sin `gastos_compartidos` por
  la compra (`'compra_cuotas'`) ni por cuota, y `monto_por_cuota_minor` es el
  reparto por default (`total / total_cuotas`, no un `amount_per_fee` custom). Se
  reescriben `monto_total_minor`, `monto_por_cuota_minor` y el `monto_cuota_minor` de
  todas las cuotas (`round(total / total_cuotas)`, mismo criterio que
  `create_purchase()`) en la misma transacción.
- `moneda_id`: todas las cuotas en `pendiente` (una cuota en un resumen es parte del
  total de ESA moneda) y sin `gastos_compartidos` por la compra ni por cuota
  (`gastos_compartidos` no tiene moneda propia: cambiarla mezclaría monedas en el
  saldo del hogar). Se mantiene el importe MOSTRADO, igual que el Registro
  (`TransactionService.update()` recibe monto + moneda juntos): si la moneda nueva
  tiene otros decimales (CLP 0, BTC 8) se reescalan `monto_total_minor`,
  `monto_por_cuota_minor` (recalculado del total nuevo si el reparto es el default),
  el `monto_cuota_minor` de cada cuota y `monto_reintegro_minor`. No valida que la
  moneda sea operativa de la tarjeta — igual que `create_purchase()`; eso lo filtra
  la UI.

Si una condición falla, `FeesError`: la corrección va por un cargo extra de tipo
`ajuste` en el resumen, nunca reescribiendo en silencio. Pendiente conocido: editar
`categoria_id`/`fecha_compra` NO propaga a `gastos_compartidos.categoria_id`/`fecha`
de una compra ya compartida (mismo comportamiento que `TransactionService.update()`
con transacciones compartidas).

**Edición de `gastos_compartidos` — ✅ implementado en
`SharedExpensesService.update_shared_expense()`, sin cambio de schema.**
`descripcion` y `fecha` se editan en cualquier estado. `monto_base_minor` y
`coeficiente_deuda` solo si el gasto está `pendiente` Y no tiene ninguna fila en
`gasto_compartido_pagos` (un pago es dependencia con estado propio — mismo criterio
que `delete_shared_expense()`); si se editan, se recalculan en la misma transacción
`monto_adeudado_minor = round(monto_base_minor * coeficiente_deuda / 100)` y
`monto_pendiente_minor` (= adeudado, porque no hay pagos). Editar el monto base
desacopla el gasto de su origen (`origen_tipo`/`origen_id`): no se toca la
transacción/compra de la que salió.

## 10. Convención general
Toda plata en minor units (enteros). Fechas en `TEXT` formato `YYYY-MM-DD` (ya
validado con `CHECK(... GLOB '????-??-??')` en el schema existente — mantener el
patrón en tablas nuevas).

## 11. Soft-delete de categorías — ✅ implementado
`categorias.activa` (`INTEGER NOT NULL DEFAULT 1`) se agregó vía
`db/schema_migrations.py`, no como `ALTER TABLE` directo en `schema.sql` — mismo
motivo que las columnas de `compras_cuotas` (ver sección 2): SQLite no soporta
`ADD COLUMN IF NOT EXISTS` y `schema.sql` se reaplica completo en cada
`DatabaseManager.inicializar()`.
- Una categoría **nunca** se borra físicamente si tiene transacciones asociadas —
  siempre soft-delete (`activa = 0`), consistente con la regla general de
  edición/borrado de la sección 9 y con `CLAUDE.md` sección 4.
- Filtrar categorías inactivas en los listados y bloquear el soft-delete cuando
  corresponda es trabajo de `CategoriasRepository` y de los services que lo consuman
  (Fase 2) — el schema solo provee la columna, no aplica la regla.

## 12. Cargos extra de resumen y cálculo de totales al cerrar — ✅ implementado en schema.sql
Tabla `resumen_cargos_extra` (`resumen_id`, `concepto`, `tipo` ∈ `impuesto`/`recargo`/
`ajuste`/`otro`, `monto_minor` — puede ser negativo, ej. un ajuste a favor del usuario).

Esto rediseña cómo `resumenes_tarjeta` calcula sus totales al cerrar un resumen:
- `resumenes_tarjeta.monto_impuestos_minor` deja de ser un input directo cargado a
  mano — pasa a ser la **suma** de `resumen_cargos_extra` de ese resumen, recalculada
  en el momento del cierre.
- `resumenes_tarjeta.monto_consumos_minor` se sigue consolidando igual que antes:
  sumando `monto_cuota_minor` de todas las `cuotas_credito` de ese resumen.
- `resumenes_tarjeta.porcentaje_impuesto_bp` pasa a ser un dato **derivado**
  (`monto_impuestos_minor * 10000 / monto_consumos_minor`, en basis points, división
  entera, 0 si no hay consumos) — ya no se carga a mano.
- `resumen_cargos_extra` no tiene `updated_en` ni soft-delete: son datos de apoyo al
  cierre, editables libremente (DELETE físico) antes de cerrar el resumen — no un
  registro contable independiente con historial propio, a diferencia de
  `deuda_pagos`/`transacciones`.

## 13. Autotransferencias — ✅ implementado en schema.sql (hueco cerrado)
Tabla `autotransferencias` (`transaccion_salida_id`, `transaccion_entrada_id`, `notas`),
vínculo formal entre las dos filas de `transacciones` que genera una transferencia
entre cuentas propias (egreso en origen + ingreso en destino).

Esta tabla **no existía en el schema hasta ahora**, a pesar de que el código legacy de
`db/database.py` (`crear_autotransferencia()`, eliminado en la limpieza de la Fase 2)
ya insertaba contra ella asumiendo que existía — nunca se había creado realmente. El
reemplazo moderno, `TransactionService.create_transfer()`, heredó esa misma asunción
en su docstring (decía que llenaba el vínculo) sin que el código lo hiciera. Se detectó
al auditar la limpieza de `db/database.py` y se cierra acá agregando la tabla y el
INSERT correspondiente.

- `UNIQUE(transaccion_salida_id, transaccion_entrada_id)`: evita vincular el mismo par
  de transacciones dos veces.
- `CHECK(transaccion_salida_id != transaccion_entrada_id)`: evita un vínculo
  degenerado (una transacción "transferida a sí misma").
- Sin `updated_en` ni soft-delete: es un registro de vínculo que se crea una vez junto
  con las dos transacciones y no se edita después — mismo criterio que `asignaciones`/
  `movimientos_activo`.
- No hay migración retroactiva de transferencias históricas sin vínculo — queda
  pendiente para una fase futura de migración de datos, no se resuelve acá.

## 14. Multi-moneda por cuenta, y relación cuentas ↔ activos_financieros

**Multi-moneda por cuenta — ✅ ya soportado en schema.sql, recién expuesto ahora
desde el service.** `cuentas_saldos` (`cuenta_id`, `moneda_id`, `saldo_inicial_minor`,
PK `(cuenta_id, moneda_id)`) ya permitía desde el diseño original que una misma
`cuentas` operara en más de una moneda (ej. una tarjeta que factura en ARS y en USD).
La limitación estaba en `AccountsService`, que hasta la Fase 5 solo exponía
`create_account()`/`get_account()`/`list_accounts()` para una única moneda por cuenta
— no en el schema. Se corrige acá:
- `AccountsService.create_account()` recibe `monedas: list[int]` (mínimo una, sin
  duplicados) y crea la cuenta más una fila en `cuentas_saldos` por cada moneda, todo
  atómico en una sola transacción.
- `AccountsService.add_currency_to_account()` agrega una moneda nueva a una cuenta ya
  existente, con saldo inicial 0. El set de monedas de una cuenta solo puede
  **crecer** — no existe (todavía) una forma de sacarle una moneda a una cuenta:
  hacerlo implicaría decidir qué pasa con el historial de transacciones en esa
  moneda, que queda fuera de alcance por ahora.
- `get_account()`/`list_accounts()` devuelven una lista `saldos` (uno por moneda
  operativa) en vez de un único saldo/moneda_codigo sueltos — `archive_account()` y
  `get_total_balance()` se ajustaron en consecuencia (una cuenta solo se puede
  archivar con saldo 0 en **todas** sus monedas; el patrimonio total nunca mezcla
  monedas distintas en una sola suma).
- `CuentasRepository.crear()` ahora acepta un `conn` opcional (mismo patrón que
  `ComprasCuotasRepository.crear()`) para poder participar de la transacción externa
  que arma `create_account()`; `crear_saldo_inicial()` es el método nuevo para las
  monedas adicionales de la lista.

**Relación cuentas ↔ activos_financieros — ✅ formalizada en la Tarea 6g (ver sección
19).** Esta sección documentaba la decisión ORIGINAL de dejarla informal a propósito
(activos_financieros/movimientos_activo sin ninguna columna que los vincule a una
cuentas puntual) — superada por la Tarea 6g, que agrega `activos_financieros.
cuenta_id` y simplifica `register_purchase()`/`register_sale()` en consecuencia. Se
deja el texto original abajo por contexto histórico de por qué no se había
formalizado antes.

> Original (Fase 5, antes de la Tarea 6g): de qué cuenta salió la plata para comprar
> un activo, o a qué cuenta vuelve al venderlo, era una decisión consciente de no
> formalizar todavía, no un hueco que faltara cerrar. Si en el futuro hacía falta ese
> vínculo (ej. para que el saldo de una cuenta de inversión se calcule solo,
> descontando compras y sumando ventas), la forma de agregarlo sería una columna
> opcional en `movimientos_activo` — no una tabla puente nueva ni un cambio a
> `cuentas_saldos`. La Tarea 6g terminó agregando la columna a `activos_financieros`
> en vez de a `movimientos_activo` — ver sección 19 para el razonamiento.

**DELETE físico de cuentas — ✅ implementado en `AccountsService.delete_account()`.**
Ventana de corrección temprana (CLAUDE.md §4) aplicada a `cuentas`: una cuenta se
puede borrar de verdad solo si nunca tuvo actividad real — cero filas en
`transacciones` que la referencien (contando también las soft-deleted: si una
transacción se cargó y después se borró lógicamente, la cuenta *tuvo* actividad,
aunque ya no sea visible), cero filas en `cuentas_saldos` con `saldo_inicial_minor
!= 0`, y ninguna otra cuenta que la use como `cuenta_pago_id`. Si falla cualquiera de
las tres, se archiva en vez de borrarse — nunca se reescribe en silencio.

## 15. Ambigüedad de moneda en `resumen_cargos_extra` — decisión de diseño, sin cambio de schema

`FeesService.resumen_por_tarjeta(mes, anio)` (Tarea 4: desglose del dashboard por
tarjeta de crédito) necesita sumarle a las `cuotas_credito` que vencen ese mes los
cargos extra (`resumen_cargos_extra`) del resumen de esa misma cuenta/mes/año, sin
mezclar monedas distintas en un mismo total (mismo principio que el resto del
dashboard — ver sección de `get_gasto_por_categoria()` en
`services/dashboard_service.py`).

El problema: `resumen_cargos_extra.monto_minor` no tiene columna de moneda propia, y
`resumenes_tarjeta` tampoco — un resumen es por `(cuenta_id, mes, anio)`, no por
`(cuenta_id, mes, anio, moneda_id)`, porque el diseño original asume que una tarjeta
física factura en una sola moneda por mes (cierto en el uso real). El schema, sin
embargo, sí permite en teoría que la MISMA tarjeta tenga `cuotas_credito` venciendo en
más de una moneda el mismo mes (`compras_cuotas.moneda_id` es por compra, no por
cuenta) — un caso límite que hoy no ocurre en la práctica pero que el modelo no
prohíbe.

**No se agregó una columna `moneda_id` a `resumen_cargos_extra` ni a
`resumenes_tarjeta` para esto** — hubiera sido una migración de schema para resolver
un caso que no se ha dado nunca, y esta tarea no pedía tocar `schema.sql`. En cambio,
`resumen_por_tarjeta()` resuelve la ambigüedad en tiempo de lectura: si una tarjeta
tiene cuotas venciendo en una sola moneda ese mes (el caso normal), los cargos extra
se suman ahí sin problema. Si tiene cuotas en más de una moneda ese mismo mes (el
caso límite), los cargos extra NO se suman a ninguno de los dos totales — se dejan en
0 en ambos, y el dict de esa cuenta lleva `cargos_extra_multiples_monedas=True` para
que quien consuma el resultado sepa que hay un monto sin asignar, en vez de adivinar
a cuál de las dos monedas pertenece.

Si en el futuro una tarjeta real empieza a facturar en más de una moneda por mes de
forma habitual, la resolución correcta sería agregar `moneda_id` a
`resumenes_tarjeta` (un resumen por cuenta/mes/año/moneda) vía
`db/schema_migrations.py` — no antes, siguiendo el mismo criterio de "no anticipar
schema para un caso que todavía no pasó" ya aplicado en la sección 14.

## 16. Vínculo entre movimientos de ahorro y transacciones reales — ✅ implementado (Tarea 6b)

Decisión tomada tras discutirlo con el usuario (ver docs/PROXIMOS_PASOS.md, Tarea 6b):
los aportes/retiros de ahorro deben poder generar una transacción real que
descuente/acredite la cuenta de origen, porque el patrimonio total del dashboard
tiene que coincidir siempre con lo que las apps de los bancos muestran de verdad — un
ahorro "aparte" que no toca el saldo real generaría una desincronización inaceptable.
Cubre los dos casos reales que diferenció el usuario: cuentas donde se reserva plata
dentro del mismo saldo global (ej. Mercado Pago) y luego se "reingresa" como ingreso
al usarla, y cuentas exclusivas de ahorro/inversión (ej. FCI de Cocos) donde la plata
realmente se transfiere afuera — ambos casos usan el mismo mecanismo.

- `movimientos_activo.transaccion_id INTEGER REFERENCES transacciones(id)`, nullable,
  agregada vía `db/schema_migrations.py` (no `ALTER TABLE` directo en `schema.sql`,
  mismo motivo que el resto de las columnas de esa lista: SQLite no soporta
  `ADD COLUMN IF NOT EXISTS` y `schema.sql` se reaplica completo en cada
  `DatabaseManager.inicializar()`). Mismo patrón exacto que
  `recibos_sueldo.transaccion_id` (esa sí nace en `schema.sql` porque `recibos_sueldo`
  es una tabla nueva, no una columna agregada a una existente). NULL para movimientos
  puramente informales — comportamiento previo sin cambios.
- `SavingsService.register_purchase()`/`register_sale()` ganan un parámetro opcional
  `cuenta_id`. Si se pasa, dentro de la MISMA transacción atómica que ya arma el
  movimiento (+ asignaciones), se crea además una transacción real vía
  `TransaccionesRepository.crear(conn=...)` — egreso para `register_purchase()`
  (aporte: plata que sale de la cuenta hacia el ahorro), ingreso para
  `register_sale()` (retiro: plata que vuelve a estar disponible) — y se vincula su id
  en `movimientos_activo.transaccion_id`. La moneda de esa transacción es
  `activos_financieros.moneda_id` del activo involucrado: es la única moneda
  disponible en el método (no se le pasa moneda/currency_code aparte), bajo el
  supuesto de que la cuenta indicada opera en esa moneda.
- **`categoria_id` era OBLIGATORIO cuando se pasaba `cuenta_id`** (`ValueError` si
  faltaba) en el diseño original de esta tarea — ver por qué en el bloque citado
  abajo. **Superado por la Tarea 6g** (sección 19): para entonces la categoría
  protegida "Ahorro/Inversión" ya existía (Tarea 1b, ver sección 17), así que dejó
  de tener sentido pedírsela al caller — `cuenta_id`/`categoria_id` dejaron de ser
  parámetros de `register_purchase()`/`register_sale()` por completo, se resuelven
  solos.

  > Razonamiento original (Tarea 6b, antes de que existiera la categoría
  > "Ahorro/Inversión"): se prefirió un parámetro explícito por sobre asumir una
  > categoría "razonable" automáticamente, mismo criterio que
  > `EmpleosService.create_receipt()` — el catálogo todavía no tenía una categoría
  > protegida dedicada a ahorro, y elegir una sin que el caller lo supiera hubiera
  > sido inventar una convención no pedida (CLAUDE.md §0.4). El verify de este
  > service usaba la categoría ya sembrada "MOVIMIENTO CAPITAL · Inversiones" como
  > categoría de ejemplo razonable — no era una categoría protegida ni hardcodeada
  > dentro del service.
- `register_return()` (rendimiento) NO gana `cuenta_id` — fuera de alcance de esta
  tarea. Un movimiento tipo='rendimiento' nunca tiene `transaccion_id` todavía.
- `SavingsService.get_balance_por_cuenta(objetivo_id) -> list[dict]`: agregación de
  solo lectura, cuánto de lo aportado/retirado a un objetivo pasó realmente por cada
  cuenta real (join `asignaciones` → `movimientos_activo` → `transacciones` →
  `cuentas`, filtrando implícitamente por `transaccion_id IS NOT NULL` vía INNER
  JOIN). Agrupa por `(cuenta_id, moneda_id)` — nunca mezcla monedas distintas en una
  misma suma, mismo criterio que `DashboardService.get_gasto_por_categoria()`. Vive en
  el service (no en un repositorio) por el mismo motivo que esa función: es
  agregación de reporte cruzando varias tablas, no CRUD de una sola.

## 17. Categorías especiales Autotransferencia / Ahorro-Inversión en el Registro — ✅ implementado (Tarea 1b)

Sin cambios de schema — esta tarea es routing de UI + un método nuevo de servicio.
Decisión tomada: la fuente de verdad para cargar CUALQUIER movimiento (incluidas
transferencias entre cuentas y aportes a ahorro) es el Registro de transacciones —
mismo mecanismo de routing por categoría ya construido para "Impuesto tarjeta"/
"Recargo tarjeta"/"Ajuste/Reintegro tarjeta" en Compras en cuotas (sección 12/Tarea 3).

- **"MOVIMIENTO CAPITAL · Autotransferencia" ya existía** en `db/seed.sql` y en
  `CATEGORIAS_PROTEGIDAS` de `services/categorias_service.py` desde antes de esta
  tarea — la documentaba `TransactionService.create_transfer()` como la categoría
  esperada, pero ningún caller real la usaba todavía. Esta tarea es la primera que
  la conecta de verdad: `ui/components/registro_transacciones.py` la reconoce como
  categoría de routing y abre un mini-diálogo (Cuenta destino) que llama a
  `create_transfer()`. No hizo falta agregarla a seed.sql ni a
  CATEGORIAS_PROTEGIDAS — ya estaba.
- **"MOVIMIENTO CAPITAL · Ahorro/Inversión" SÍ es nueva** — agregada a
  `db/seed.sql` (bases nuevas) y a `migration/agregar_categoria_ahorro_inversion.py`
  (bases existentes, mismo patrón que `migration/agregar_categorias_tarjeta.py` de
  la sección 3 — `INSERT OR IGNORE` en seed.sql no alcanza a una base que ya
  existía antes del cambio). Se agregó también a `CATEGORIAS_PROTEGIDAS`.
- **`create_transfer()` exige `category_id`** (no es opcional, a diferencia de
  `register_purchase()`/`register_sale()` de la sección 16) — se le pasa el id de
  la propia categoría "Autotransferencia" elegida en la fila. `create_transfer()`
  tampoco tiene parámetro `concept` (hardcodea "Auto-transfer (out/in)" en las dos
  transacciones que genera, firma real revisada antes de implementar) — el
  concepto tipeado en la fila viaja como `notes` en su lugar, el mapeo más cercano
  disponible.
- **El signo tipeado en Monto se ignora en ambos flujos especiales** (se usa
  `abs(monto)`): a diferencia de una categoría normal, acá el tipo de movimiento lo
  fuerza el método de destino (`create_transfer()` siempre arma egreso+ingreso;
  `register_purchase()` siempre es un aporte/egreso — no existe todavía un flujo de
  "retiro" con Ahorro/Inversión desde el Registro, fuera de alcance de esta tarea).
  Mismo criterio que ya usa `ui/screens/compras_cuotas.py` para las categorías
  especiales de tarjeta (el signo no decide el tipo de cargo ahí tampoco).
- **`SavingsService.get_or_create_reserved_cash_asset(cuenta_id, moneda_id)`**
  (método nuevo en esta tarea): busca un `activo_financiero` tipo='otro' ya
  existente (incluyendo inactivos — `solo_activos=False` — para nunca duplicar uno
  que el usuario haya desactivado a mano) y lo reusa; si no existe, lo crea. Vive en
  `SavingsService` (motor de datos), no en la UI — así queda testeable con un
  verify normal (`verify/ahorros/verify_savings_service.py`) y reusable fuera del
  Registro si algún día hace falta (ej. `migration/`). **Identidad de búsqueda
  ACTUALIZADA por la Tarea 6g** (sección 19): en el diseño original de esta tarea
  (Tarea 1b) la identidad era el nombre exacto construido
  `f"Efectivo reservado en {cuenta_nombre}"`, coherente con que la sección 14
  todavía describía la relación cuentas↔activos_financieros como informal a
  propósito. Desde que la Tarea 6g agregó `activos_financieros.cuenta_id`, la
  identidad pasó a ser `(cuenta_id, tipo='otro')` — el nombre generado se sigue
  guardando igual (sigue siendo útil para mostrarlo) pero ya no es la clave de
  búsqueda, más robusto ante un rename de la cuenta después de creado el activo.
- La moneda del activo genérico se fija en el momento de su PRIMERA creación (la
  moneda elegida en esa primera fila del Registro) y no se reescribe después — si
  una carga posterior a la misma cuenta usa una moneda distinta, la transacción
  vinculada de todos modos queda en la moneda original del activo (limitación
  conocida, no resuelta acá: no se pidió un selector de moneda por activo ni
  activos separados por cuenta+moneda, y el caso de una cuenta reservando ahorro en
  más de una moneda a la vez no es el uso típico descripto por el usuario). Esto no
  cambió con la Tarea 6g.

## 18. Persistencia de la fórmula del campo Estimado (Presupuestos) — ✅ implementado

`presupuestos.formula_estimado TEXT`, nullable, agregada vía `db/schema_migrations.py`
(no `ALTER TABLE` directo en `schema.sql`, mismo motivo que el resto de las columnas de
esa lista). Guarda el texto tal cual se tipeó en el campo Estimado de
`ui/screens/presupuestos.py`, CON el `"="` incluido (ej. `"=15000+3200-500"`), cuando
`monto_estimado_minor` se calculó con `utils/calculadora_segura.py`; `NULL` si se cargó
como número directo.

- `PresupuestosRepository.upsert()` recibe `formula_estimado` opcional (default `None`)
  y lo reescribe SIEMPRE en el camino `UPDATE` (`ON CONFLICT ... DO UPDATE SET
  formula_estimado = excluded.formula_estimado`) — mismo criterio sin excepción que ya
  aplica a `monto_estimado_minor`/`es_recurrente`/`notas` (sección de `upsert()`, no hay
  sentinel de "no tocar" en este repositorio). Esto es deliberado: sobreescribir un
  presupuesto que tenía fórmula con un número directo (`formula_estimado=None`
  explícito) debe limpiar la columna a `NULL` — nunca debe quedar una fórmula vieja
  asociada a un monto que ya no le corresponde.
- **`PresupuestosRepository.copiar_periodo()` (usado por `copy_period()` sin fórmula) NO
  se tocó a propósito** — su `INSERT OR IGNORE` no incluye `formula_estimado`, así que
  todo presupuesto copiado a un período nuevo queda con la columna en `NULL`, aunque el
  origen tuviera fórmula. Decisión, no descuido: copiar un presupuesto recurrente lleva
  el MONTO ya calculado hacia adelante, no una fórmula "viva" que deba recalcularse cada
  vez — la fórmula es metadata de cómo se originó ESE número puntual en ESE período, no
  algo que tenga sentido reproducir automáticamente en el destino. Si en el futuro hace
  falta lo contrario, es un cambio deliberado aparte, no implícito acá.
- Falso positivo corregido en `verify/utils/verify_calculadora_segura.py`: el chequeo
  original de "el módulo nunca usa eval()/exec()" era un substring plano
  (`"eval(" in codigo_fuente`), que fallaba porque el propio docstring de
  `calculadora_segura.py` dice, en prosa, "NUNCA usa eval()/exec()" — esa advertencia
  CONTIENE el substring. El chequeo corregido parsea el código fuente con `ast.parse()`
  (análisis estático, no ejecución — mismo principio que el propio módulo aplica sobre
  la expresión del usuario) y busca específicamente nodos `ast.Call` con
  `func = ast.Name(id='eval'|'exec')` — 0 matches reales, confirmado por lectura del AST,
  no por ejecutar nada.

## 19. Vínculo formal activos_financieros ↔ cuentas — ✅ implementado (Tarea 6g)

Decisión tomada: formalizar la relación que la sección 14 dejaba deliberadamente
informal — un `activo_financiero` (FCI, acción, plazo fijo, "efectivo reservado", etc.)
ahora puede vincularse a la `cuentas` real desde la que se opera, una sola vez al
crear el activo, en vez de tener que elegir cuenta y categoría en cada movimiento
posterior.

- `activos_financieros.cuenta_id INTEGER REFERENCES cuentas(id)`, nullable, agregada
  vía `db/schema_migrations.py` (no `ALTER TABLE` directo en `schema.sql`, mismo
  motivo que el resto de las columnas de esa lista). La columna se agregó a
  `activos_financieros`, NO a `movimientos_activo` como sugería el texto original de
  la sección 14 — la cuenta es una propiedad del ACTIVO (una acción se opera siempre
  desde el mismo broker), no algo que deba repetirse o pueda variar por movimiento;
  un activo sin `cuenta_id` sigue siendo válido y genera movimientos puramente
  informales, igual que el comportamiento previo cuando no se pasaba `cuenta_id`.
- `SavingsService.create_activo()` gana `cuenta_id: Optional[int] = None`, validado
  contra `AccountNotFoundError` si se pasa.
- `SavingsService.register_purchase()`/`register_sale()` PIERDEN los parámetros
  `cuenta_id`/`categoria_id` que había agregado la Tarea 6b (sección 16) — se
  resuelven solos: `cuenta_id` sale de `activo["cuenta_id"]` (si el activo no tiene
  cuenta vinculada, el movimiento queda informal, mismo comportamiento previo a
  cuando no se pasaba `cuenta_id`), y `categoria_id` siempre es el id de la
  categoría protegida "MOVIMIENTO CAPITAL · Ahorro/Inversión" (sección 17),
  resuelto por nombre (`SavingsService._get_categoria_ahorro_inversion_id()`, nunca
  hardcodeado el id numérico — mismo criterio de "matchear por nombre, no por id
  fijo" que `CATEGORIAS_PROTEGIDAS` de `services/categorias_service.py`). El
  mecanismo de "crear la transacción real vinculada dentro de la misma transacción
  atómica" que armó la Tarea 6b no cambió — solo cambió de dónde salen sus dos
  parámetros.
- `SavingsService.get_or_create_reserved_cash_asset()` cambia de firma:
  `cuenta_nombre: str` → `cuenta_id: int` — ver sección 17 para el detalle completo
  del cambio de identidad de búsqueda (por nombre → por `cuenta_id`).
- **UI (Tarea 6g, Parte D):** ningún diálogo de movimiento (Compra en
  `ui/components/dialogo_compra_ahorro.py`, Rendimiento/Egreso general en
  `ui/screens/ahorros.py`, el mini-diálogo "Ahorro/Inversión" de
  `ui/components/registro_transacciones.py`) pide Cuenta ni Categoría — se
  resuelven solas por el mecanismo de arriba. El único lugar donde se elige una
  cuenta es al CREAR un activo nuevo (sección "+ Crear nuevo activo"/"+ Nuevo
  activo"): un `CampoFiltrable` de cuentas, opcional, que se manda como
  `create_activo(cuenta_id=...)`. Cualquier `CampoFiltrable` que liste activos
  existentes para elegir uno (Compra "elegir activo específico", Rendimiento,
  Egreso general) muestra la cuenta asociada en el label de cada opción
  (`"NVDA — Bull Market"`, solo `"NVDA"` si no tiene cuenta vinculada) para
  desambiguar activos con el mismo nombre en cuentas distintas.
- `verify/ahorros/verify_savings_service.py` actualizado: los casos que antes
  pasaban `cuenta_id`/`categoria_id` a `register_purchase()`/`register_sale()` en
  cada llamada ahora setean `cuenta_id` una sola vez con `create_activo(cuenta_id=
  ...)`, y confirman que la transacción vinculada sigue usando la cuenta correcta y
  SIEMPRE la categoría "Ahorro/Inversión". `get_balance_por_cuenta()` (sección 16)
  ahora necesita un `activo_financiero` distinto por cada cuenta real involucrada
  en el escenario de prueba — antes un único activo podía "saltar" de cuenta en
  cuenta pasando `cuenta_id` en cada movimiento, ya no es posible (la cuenta es fija
  por activo).

## 20. Pago parcial de gastos_compartidos — ✅ implementado (Tarea 9, Parte A)

Espejo del mecanismo de `deudas` (sección 5 no lo detalla porque ya existía
antes de este documento — ver `deuda_pagos`/`monto_pendiente_minor` en
`db/schema.sql`): hasta ahora `gastos_compartidos` solo tenía un estado
binario `pendiente`/`saldado`, sin forma de trackear pagos parciales ni
compensaciones sin movimiento bancario real (caso real que motivó la
tarea: "ella compra tomate y yo lo descuento").

- `gastos_compartidos.monto_pendiente_minor INTEGER NOT NULL`, agregada vía
  `db/schema_migrations.py` (no `ALTER TABLE` directo en `schema.sql`,
  mismo motivo que el resto de las columnas de esa lista). A diferencia de
  las columnas anteriores de esa lista, esta necesitó además un
  **backfill** (`MigracionColumna.sql_backfill`, campo nuevo agregado en
  esta tarea): para cualquier gasto compartido que ya existiera en una base
  real, `monto_pendiente_minor` arranca igual a `monto_adeudado_minor`
  (nada se había pagado todavía, porque el mecanismo de pago parcial no
  existía antes). Un `DEFAULT 0` del propio `ALTER TABLE` no alcanzaba acá
  porque el valor correcto depende de otra columna de la misma fila, no de
  una constante — por eso el campo `sql_backfill` se ejecuta una sola vez,
  inmediatamente después del `ALTER TABLE`, solo en la corrida donde la
  columna se agrega por primera vez.
- Tabla nueva `gasto_compartido_pagos` — espejo exacto de `deuda_pagos`
  (mismas columnas: `gasto_compartido_id`, `transaccion_id` nullable,
  `monto_aplicado_minor`, `tipo_pago` ∈ `transaccion`/`compensacion`/
  `ajuste`, `notas`, `fecha`). **Decisión de diseño que difiere de
  `deuda_pagos`**: en vez de vivir como métodos dentro de
  `GastosCompartidosRepository` (que es como vive `deuda_pagos` dentro de
  `DeudasRepository` — confirmado por lectura antes de implementar), se
  creó `repositories/gasto_compartido_pagos_repository.py` con su propia
  clase `GastoCompartidoPagosRepository`, siguiendo la regla POR DEFECTO de
  `CLAUDE.md` §3 ("un repositorio = una entidad de la base de datos") en
  vez de la excepción que ya usa `deuda_pagos`. La atomicidad INSERT (en
  `gasto_compartido_pagos`) + UPDATE (`monto_pendiente_minor`/`estado` en
  `gastos_compartidos`) — que en `DeudasRepository.registrar_pago()` vive
  en un único método de repositorio — se resuelve acá un nivel más arriba,
  en `SharedExpensesService.aplicar_pago()`, pasando el mismo `conn` a
  ambos repositorios dentro de una sola `self._db.transaction()`.
- `SharedExpensesService.aplicar_pago(gasto_id, hogar_id,
  monto_aplicado_minor, fecha, tipo_pago='transaccion',
  transaccion_id=None, notas=None)`: valida pertenencia (mismo criterio que
  `settle_expense()`), rechaza gastos ya `'saldado'` con la excepción nueva
  `GastoCompartidoYaSaldadoError` (no se reusó `GastoCompartidoDuplicadoError`
  — esa es semánticamente "ya existe un gasto para ese origen", un caso
  distinto de "este gasto ya no acepta pagos"). **Sobrepago**:
  `monto_aplicado_minor` que supera el pendiente se CLAMPEA al pendiente
  exacto (nunca lo cruza de signo, nunca lanza excepción) — el resultado
  trae `ajustado=True` y el `monto_aplicado_minor` REAL persistido. Se
  eligió clamp sobre excepción porque el "pago general" de la Parte C
  (sesión futura, no implementada en esta tarea) va a repartir un ingreso
  contra varios gastos pendientes del más viejo al más nuevo hasta agotar
  el monto — un sobrepago en el ÚLTIMO gasto de esa cadena es el caso
  normal (sobra plata tras saldarlo justo), no un error del usuario.
  `monto_pendiente_minor` puede ser NEGATIVO (hereda el signo de
  `monto_adeudado_minor`, ver sección 2) — el pago siempre reduce la
  MAGNITUD hacia 0 en la dirección correcta según el signo, nunca lo
  invierte.
- `fecha` es un parámetro EXPLÍCITO y obligatorio de `aplicar_pago()` — no
  estaba en la firma sugerida originalmente en `docs/PROXIMOS_PASOS.md`,
  pero `gasto_compartido_pagos.fecha` es `NOT NULL` en el schema, y
  asumir `fecha = hoy` en cada llamada violaría `CLAUDE.md` §6 (todo alta
  debe poder hacerse con fecha pasada, para no bloquear una futura carga
  en lote desde `migration/`).
- `settle_expense(gasto_id, hogar_id)` **no** ganó un parámetro `fecha`
  nuevo (comportamiento observable sin cambios, según lo pedido) — por
  dentro llama a `aplicar_pago()` con el pendiente completo,
  `tipo_pago='ajuste'`, y `fecha=date.today()` como excepción DELIBERADA:
  "saldar" es siempre una acción manual en el momento, nunca una carga
  histórica en lote. La lógica de "marcar saldado cuando el pendiente
  llega a 0" vive ahora ÚNICAMENTE en `aplicar_pago()` —
  `GastosCompartidosRepository.marcar_saldado()` queda sin caller desde
  `SharedExpensesService` (se deja en el repositorio por si algún llamador
  externo lo necesitara, no se borró en esta tarea).

## 21. Snapshots mensuales de saldos — ✅ implementado (vía `db/schema_migrations.py`)

Tres tablas **derivadas** — un caché recalculable, no datos propios: todo lo
que guardan sale de otras tablas y se puede regenerar en cualquier momento
(`SnapshotsService.recalcular_todo()` / `recalcular_desde()`,
`services/snapshots_service.py`). Sirven para la fila "SALDO ANTERIOR" del
Registro, Deudas y Gastos compartidos sin recorrer toda la historia cada vez.

- `saldos_mensuales(cuenta_id, moneda_id, mes, anio, saldo_minor)`: saldo de
  cada fila de `cuentas_saldos` al cierre del mes = `saldo_inicial_minor` +
  transacciones no eliminadas hasta el último día del mes, con el MISMO
  signo que `vw_balance_cuentas` (ingreso suma, egreso resta, `movimiento`
  suma). Snapshot del mes anterior + movimientos del mes en curso = el saldo
  que muestra la app.
- `deudas_mensuales(entidad_persona, tab, moneda_id, mes, anio,
  monto_minor)`: `SUM(monto_minor)` — ya con signo — de las filas de
  `deudas` (sección 22) de ese `tab` con `fecha` <= fin de mes, por persona
  y moneda (el saldo de la barra de Deudas de ese tab). Los tabs
  `me_deben` y `debo` van por separado: la misma persona puede deberte algo
  y vos deberle otra cosa. `tab` se agregó en la reestructuración final de
  deudas: una base que ya tenía la tabla sin esa columna la recrea vacía
  (`_deudas_mensuales_con_tab()` en `db/schema_migrations.py` — es un caché:
  se vuelve a llenar con ↻, y mientras tanto se calcula en vivo). La
  persona se guarda normalizada (sin espacios de más, en mayúsculas): "Noe"
  y "NOE" son la misma fila.
- `compartidos_mensuales(hogar_id, pagador, moneda_id, mes, anio,
  monto_minor)`: pendiente ACTUAL de los gastos `pendiente` con `fecha` <=
  fin de mes, por hogar, pagador y moneda. `gastos_compartidos` no tiene
  moneda: sale del origen (transacción, compra en cuotas o la compra de la
  cuota).

Decisiones:

- Van en `MIGRACIONES_TABLA` de `db/schema_migrations.py` (pedido explícito),
  no en `schema.sql`: se agregan sobre bases existentes. Son `CREATE TABLE IF
  NOT EXISTS`, así que reaplicarlas en cada `inicializar()` no hace nada.
  `UNIQUE(clave, mes, anio)` + `INSERT OR REPLACE` como upsert.
- El mes en curso **nunca** se guarda: siempre se calcula en vivo. Rango:
  del primer mes con datos al mes anterior al actual.
- Lecturas: snapshot del mes anterior si existe; si falta (nunca se
  recalculó, o empezó un mes nuevo) se calcula en vivo con la misma regla —
  en el primer mes de la app eso da `saldo_inicial_minor`.
- **Límite conocido**: compartidos usa el pendiente de HOY (regla pedida), así
  que un pago registrado hoy sobre un gasto viejo deja el snapshot
  desactualizado hasta el próximo recálculo (↻ de las pantallas). Deudas ya
  no: desde la sección 22 un pago es una fila con su propia fecha. Lo mismo con los saldos si se carga o edita una transacción de
  un mes cerrado. Alternativa posible (no implementada): reconstruir el
  pendiente al cierre de cada mes con las fechas de
  `gasto_compartido_pagos`, que deja los meses cerrados fijos.
- Sin `deleted_at` ni reglas de edición (sección 9): se borran y se
  reescriben enteras al recalcular.

## 22. Deudas: libro de movimientos en dos tabs — ✅ implementado en schema.sql

`deudas` es un **libro de movimientos** separado en dos tabs:

- `tab = 'me_deben'`: lo que te deben (le prestaste, pagaste algo por esa persona);
- `tab = 'debo'`: lo que debés (te prestaron, alguien pagó algo por vos).

`monto_minor` tiene **signo**: positivo = entrada (la deuda crece), negativo
= salida (un pago que la baja). El saldo de una persona en un tab es
`SUM(monto_minor)`: en `me_deben`, positivo = te debe y negativo = te pagó de
más; en `debo`, positivo = le debés y negativo = le pagaste de más. No hay
`monto_pendiente_minor`, ni `estado`, ni vencimiento, ni tabla de pagos
(`deuda_pagos` ya no existe): registrar un pago es crear una fila negativa
en el mismo tab. `DebtsService.register_payment()` / `write_off()` /
`mark_uncollectable()` / `apply_payment()` ya no existen.

Columnas (`db/schema.sql`): `id, entidad_persona, concepto, tab, monto_minor,
moneda_id, fecha, notas, origen_tipo, origen_id, sincronizado_en, creada_en`
(`sincronizado_en` reservado para la sincronización futura). Sin índices,
triggers ni vistas sobre las columnas nuevas en `schema.sql`: corre antes de
la migración, y en una base todavía sin convertir un `CREATE INDEX` sobre
`tab` fallaría. `vw_deudas_activas`, `idx_deudas_estado` y el trigger
`trg_deudas_updated` se eliminaron (usaban columnas que ya no existen).

Historia: la tabla tuvo dos estructuras anteriores —
**ORIGINAL** (`monto_original_minor` / `monto_pendiente_minor` / `estado`,
con los pagos en `deuda_pagos`) y un **LIBRO** intermedio (`tipo`
`'a_favor'` / `'en_contra'` + `monto_minor` siempre positivo, con la tabla
original renombrada a `deudas_old`).

Migración (`reestructurar_deudas()` en `db/schema_migrations.py`, corre sola
en `inicializar()`; no hace nada si la tabla ya tiene `tab`):

- Convierte cualquiera de las dos directo a la final **conservando el
  sentido** de cada fila (el saldo de cada persona no cambia). No copia
  `monto_minor` tal cual, como proponía el pedido: en LIBRO nunca tuvo
  signo, y un pago recibido habría quedado como deuda tuya en `debo`
  (decisión confirmada con el usuario).
  - ORIGINAL: cada deuda → su tab (`a_favor` → `me_deben`, `en_contra` →
    `debo`) con su monto ORIGINAL en positivo, su `fecha_inicio` y su mismo
    id; su vencimiento, si tenía, pasa a `notas` (`VENCE: AAAA-MM-DD`). Cada
    pago de `deuda_pagos` → el tab de su deuda, en negativo
    (`origen_tipo='pago_migrado'`, `origen_id` = id del pago, fecha
    recortada a `AAAA-MM-DD`, sin concepto → `PAGO`).
  - LIBRO: los pagos migrados (`pago_migrado`, de tipo opuesto a su deuda)
    → el tab de su deuda, en negativo; las filas importadas del Excel
    (`notas` que empiezan con `MIGRADO DESDE EXCEL — TABLA DEUDAS`) →
    `me_deben`, `en_contra` en negativo; el resto → por su tipo, en
    positivo. Conserva id y `sincronizado_en`.
  - **Límite**: una fila de LIBRO cargada a mano como `en_contra` para anotar
    un pago RECIBIDO no se distingue de una deuda tuya: queda en `debo`.
- Normaliza la persona (`utils/personas.py`) y elimina `deuda_pagos`,
  `deudas_old` (y `deudas_old_N`), la vista, la tabla anterior y sus
  índices/trigger. Todo en una transacción: si algo falla, rollback completo.
- Si hay datos, antes copia el archivo
  (`<base>_backup_antes_deudas_<fecha>_<hora>.db`, al lado de la base).
- Las deudas `incobrable` se migran tal cual (decisión explícita): vuelven a
  sumar su monto completo. Las `saldada` quedan en 0 (deuda + pagos).

Reglas de dominio (`DebtsService`): la persona se normaliza (sin espacios de
más, en mayúsculas); `tab` válido; `monto_minor` entero distinto de 0, con
su signo tal cual; edición y borrado directos (una fila no tiene
dependencias con estado propio — sección 9). El routing "Deuda" del Registro
guarda el vencimiento en `notas`.

Snapshots (sección 21): `deudas_mensuales` guarda `SUM(monto_minor)` por
(persona, tab, moneda) al cierre de cada mes, así que un mes cerrado solo
cambia si se carga, edita o borra una fila con fecha de ese mes.

Importación del Excel: `migration/migrar_deudas.py` (TABLA DEUDAS en CSV):
todo va a `me_deben` — PRECIO positivo suma; PRECIO negativo, la columna
negativa y las filas de `COBRO DEUDA` restan (pagos recibidos).
`--reemplazar` borra y vuelve a importar lo que ya se había importado.

## 23. Días de cierre/vencimiento de tarjetas y cronograma editable — ✅ implementado (vía `db/schema_migrations.py`)

`tarjetas_config(cuenta_id UNIQUE, dia_cierre, dia_vencimiento, creada_en,
updated_en)`: una fila por tarjeta de crédito, con los dos días entre 1 y 31.
Va en `MIGRACIONES_TABLA` (pedido explícito de no tocar `schema.sql`, como
los snapshots de la sección 21). Sin trigger: `updated_en` lo escribe el
upsert de `TarjetasConfigRepository` (`INSERT … ON CONFLICT(cuenta_id) DO
UPDATE`, así la fila conserva su id y su `creada_en`). La maneja
`FeesService` (`set_card_config()` / `get_card_config()`): solo tarjetas de
crédito activas.

Fechas de resumen (`FeesService.card_cycle_dates()`), calculadas, no
guardadas:

- Cierre: `dia_cierre` de cada mes; si el mes es más corto, su último día
  (31 → 30 / 28).
- Vencimiento: `dia_vencimiento` del mismo mes del cierre, o del mes
  siguiente cuando `dia_vencimiento <= dia_cierre`. El pedido decía `<`; con
  `=` también va al mes siguiente, porque un resumen no puede vencer el mismo
  día que cierra.
- ACTUAL es el último resumen que cerró (cierre <= hoy); ANTERIOR, el de
  antes; PRÓXIMO, el que cierra después.

1ª cuota (`create_purchase(first_fee_month, first_fee_year)`): el cronograma
arranca en ese mes en vez del mes de compra. No puede ser anterior al mes de
compra. Sin esos parámetros sigue arrancando en el mes de compra, que es lo
que hacen las migraciones y los scripts de `verify/`. La pantalla pasa
siempre uno: el que sugiere `suggest_first_fee()` o el que se elige a mano.
La sugerencia es el mes siguiente a la compra, o dos meses después si la
tarjeta tiene `dia_cierre` y la compra es posterior a ese día; el mismo día
del cierre todavía entra en ese resumen.

Cronograma editable (`reschedule_fees()`): mueve cuotas a otro mes
(`cuotas_credito.mes_proyectado` / `anio_proyectado`), con estas reglas
(sección 9):

- solo las `pendiente`;
- no una cuota compartida por su cuenta: su gasto compartido tiene la fecha
  del mes de la cuota, la misma regla que al mover la fecha de la compra;
- nunca dos cuotas de la misma compra en el mismo mes.

Qué pasa con el cronograma al editar la compra (decisión con el usuario:
conservar la 1ª cuota):

- `update_purchase_cuotas()` lo rearma desde el mes que tiene hoy la cuota 1.
  Las cuotas que se habían movido a mano vuelven a meses consecutivos.
- `update_purchase(fecha a otro mes)` ya no lo rearma desde el mes nuevo:
  corre cada cuota la misma cantidad de meses que la fecha. Así se conservan
  la 1ª cuota elegida y las cuotas movidas a mano. Para las compras de
  siempre, con la 1ª cuota en el mes de compra, el resultado es el mismo
  que antes.

Las compras ya cargadas no se tocan: siguen con la 1ª cuota en su mes de
compra.

## 24. Sincronización con Supabase — ✅ implementado (`sync/`, vía `db/schema_migrations.py`)

Módulo de aplicación (`sync/`): `supabase_client.py` (un único cliente por
proceso), `auth.py` (login con email y contraseña, sesión guardada en
`.deltabalance_prefs.json` → `"supabase_session"`, nombre de display local →
`"display_name"`) y `sync_engine.py`. El SQL de Supabase está en
`sync/supabase_schema.sql` y se corre una vez en el SQL Editor.

**En Supabase**, una tabla genérica, `deltabalance_filas(usuario_id, tabla, clave,
datos jsonb, hogar_codigo, borrado, actualizado_local, subido_en)`, con clave
`(usuario_id, tabla, clave)`. `clave` es la clave primaria local como texto: el UUID
de la fila (sección 25), o `'<uuid>|1'` en `cuentas_saldos` y `hogar_miembros`.
Genérica y no una tabla espejo por
tabla local porque el schema local cambia seguido: con espejos, cada columna nueva
rompería la subida hasta tocar Supabase a mano. Además:

- `deltabalance_hogar_miembros(codigo, usuario_id)`: quién es miembro de qué hogar,
  por su `codigo_invitacion`. Se llena al subir cada hogar.
- RLS: cada usuario lee, escribe y borra solo sus filas, y lee también las
  compartidas (`hogar_codigo`) de sus hogares. La consulta de "mis hogares" pasa por
  una función `security definer`, para que la política no se consulte a sí misma.

**Tablas** (`TABLAS_SINCRONIZADAS`, en orden de dependencias): las 10 pedidas más
`cuentas_saldos`, `cuotas_credito`, `resumenes_tarjeta`, `tarjetas_config` y
`gasto_compartido_pagos`. Sin estas, una compra llegaría sin sus cuotas y una cuenta
sin sus saldos. `monedas` no viaja: sale del seed, con los mismos ids en toda base.

**Localmente** (`preparar_sync()`, en cada `inicializar()`):

- `sincronizado_en` en las 15 tablas.
- `sync_cambios(tabla, clave, operacion, modificado_en)`, que llenan triggers AFTER
  INSERT / UPDATE / DELETE en cada tabla. Así una edición y un borrado también
  viajan, sin tocar ningún service ni repositorio.
- `sync_estado`: la marca con la que la sync apaga esos triggers mientras escribe,
  dentro de su propia transacción. Nunca se comitea.

Pendiente de subir = lo que está en `sync_cambios`, más las filas con
`sincronizado_en` NULL (las anteriores a los triggers).

**Reglas** (decisiones con el usuario):

1. Se sube todo lo pendiente del usuario.
2. Se baja solo lo PROPIO, por ejemplo para recuperar la base en otra computadora.
   Con los ids UUID (sección 25) una fila es la misma en toda computadora. Lo del
   otro miembro del hogar no baja todavía: un gasto suyo apunta a una transacción
   que solo existe en su base, y la pantalla de Compartidos no sabe mostrarlo.
   Restaurar sobre una base nueva: las categorías del seed y "Caja Efectivo" nacen
   con otros UUIDs; `SyncRepository` las reconoce por clave natural
   (`CLAVES_NATURALES`) y les pone el id remoto en vez de chocar con el UNIQUE.
3. Conflicto: last-write-wins por `actualizado_local`. Es el `modificado_en` del
   trigger, o `updated_en` / `creada_en`; una fila sin fecha pierde.
4. La primera sincronización de un usuario en una computadora es una restauración:
   baja primero y lo remoto gana. Si no, las categorías del seed de una base nueva
   pisarían las de Supabase. `migration/subir_a_supabase.py` deja la marca de "ya
   sincronizó", así la computadora original nunca pasa por este caso.

**Límites conocidos:**

- Usar una sola computadora por usuario a la vez: dos bases del mismo usuario
  generan ids que chocan.
- Una fila bajada que referencia algo que acá no existe (una FK) se cuenta como
  error y se saltea.
- Renombrar la clave primaria de una fila (ej. `usuario_local` en `hogar_miembros`)
  deja la versión vieja en Supabase.
- `sync_fila()` existe pero todavía no la llama nadie. Las ediciones privadas suben
  al abrir la app, al tocar el indicador del Registro o con la sincronización
  periódica, que es solo de las tablas compartidas.

## 25. Ids UUID en todas las tablas — ✅ implementado en schema.sql

Todas las tablas pasaron de `id INTEGER PRIMARY KEY AUTOINCREMENT` a `id TEXT PRIMARY
KEY NOT NULL` con un UUID v4 en texto, y sus FKs a `TEXT`. Así una fila tiene la
misma identidad en cualquier computadora, que es lo que necesita la sincronización
(sección 24).

**Excepciones:**

- `monedas` conserva su id entero (es un dato de referencia, igual en toda base), y
  `moneda_id` / `moneda_*_id` siguen siendo `INTEGER`.
- Los snapshots (`saldos_mensuales`, `deudas_mensuales`, `compartidos_mensuales`) y
  `sync_cambios` / `sync_estado` también conservan su id: son locales y no viajan.
  Sus FKs a tablas con UUID sí pasaron a `TEXT`.

**Quién genera el id:**

- Los repositorios lo generan en Python (`repositories/_ids.py`, `uuid.uuid4()`),
  lo pasan explícito en el INSERT y lo devuelven (`str`).
- `cursor.lastrowid` ya no sirve como id: en una tabla con clave de texto es el
  rowid interno.
- El `DEFAULT` de cada `id` en `schema.sql` arma un UUID v4 en SQL. Es la red de
  seguridad para un INSERT sin id: `seed.sql` y los scripts de `migration/`.
- `NOT NULL` va explícito porque en SQLite una PRIMARY KEY que no es INTEGER acepta
  NULL si no se lo prohíbe.
- `seed.sql` engancha el saldo de "Caja Efectivo" por nombre, ya no por `id = 1`.

**Orden de alta:** donde se desempataba por `id`, ahora se usa `rowid`, porque
ordenar por un UUID es al azar. La migración copia las filas en su rowid original,
así que el orden se conserva.

**Bases viejas:** `migration/migrar_a_uuid_pk.py` (ensayo por default, `--confirmar`
para aplicar).

- Arma una base nueva con este schema y copia todo con un mapa id → UUID: el id, las
  FKs declaradas (salen de `PRAGMA foreign_key_list`, no de una lista a mano) y las
  referencias polimórficas `origen_tipo` / `origen_id` de `deudas` y
  `gastos_compartidos`. `'pago_migrado'` queda en NULL, porque `deuda_pagos` ya no
  existe.
- Verifica filas y sumas por tabla, formato de los UUID, `foreign_key_check` e
  `integrity_check`. Recién entonces hace el backup y reemplaza el archivo, y nunca
  si la app la tiene abierta.
- La sync arranca de cero: `sincronizado_en` queda en NULL y se borra la marca de
  bajada.

**Guarda:** con una base que todavía tiene ids enteros, `inicializar()` frena
(`exigir_ids_uuid()`) con el mensaje de qué script correr, en vez de romperse en el
primer alta.
