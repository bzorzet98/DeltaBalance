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
- **Vigente otra vez desde la sección 31** (después de un rediseño que puso un reparto
  fijo por activo): cada movimiento elige sus objetivos; el rendimiento es proporcional
  por defecto y editable.
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

> **Superado por la sección 28:** el caso sí pasa (tarjetas con cuotas en ARS y USD
> el mismo mes), y se resolvió con la moneda en cada cargo — que ahora es una fila de
> `compras_cuotas` —, no en el resumen. La regla de arriba solo la usa la migración,
> para los cargos viejos guardados sin moneda.

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
  tampoco tiene parámetro `concept` — el concepto tipeado en la fila viaja como
  `notes` en su lugar, el mapeo más cercano disponible. Los conceptos los arma el
  service con la convención del usuario: la salida "TRANSFERENCIA A <CUENTA
  DESTINO>" y la entrada "TRANSFERENCIA DESDE <CUENTA ORIGEN>"
  (`CONCEPTO_TRANSFERENCIA_SALIDA` / `_ENTRADA`; antes hardcodeaba "Auto-transfer
  (out/in)").
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
- La fecha real de un resumen puntual, si se cargó, manda sobre la calculada
  (sección 29).

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
`cuentas_saldos`, `cuotas_credito`, `resumenes_tarjeta`, `tarjetas_config`,
`tarjetas_resumenes` (sección 29) y `gasto_compartido_pagos`. Sin estas, una compra
llegaría sin sus cuotas y una cuenta sin sus saldos. `monedas` no viaja: sale del
seed, con los mismos ids en toda base.

**Localmente** (`preparar_sync()`, en cada `inicializar()`):

- `sincronizado_en` en cada tabla de `TABLAS_SINCRONIZADAS`.
- `sync_cambios(tabla, clave, operacion, modificado_en)`, que llenan triggers AFTER
  INSERT / UPDATE / DELETE en cada tabla. Así una edición y un borrado también
  viajan, sin tocar ningún service ni repositorio.
- Cada trigger hace `DELETE` de la anotación anterior de la fila + `INSERT` de la
  nueva, no `INSERT OR REPLACE`: dentro de un trigger SQLite usa la política de
  conflicto de la sentencia que lo dispara, y un upsert (`ON CONFLICT DO UPDATE`, ej.
  `tarjetas_config`) la fuerza a ABORT — con un cambio pendiente, guardar dos veces
  rompía con `UNIQUE constraint failed: sync_cambios.tabla, sync_cambios.clave`.
  `preparar_sync()` rehace los triggers en cada `inicializar()`, así una base vieja
  toma la definición nueva.
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
  error y se saltea. Excepción: las FK de una tabla compartida a filas privadas
  de otro miembro (`categoria_id`, `transaccion_id`) se traducen — sección 27.
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

## 26. Etiqueta `tag` en compras en cuotas y deudas — ✅ implementado (vía `db/schema_migrations.py`)

`compras_cuotas.tag` y `deudas.tag`: `TEXT`, nullable, texto libre — el mismo
criterio que `transacciones.tag`, que ya existía. Vacío = `NULL` (lo
normaliza el service: `FeesService.create_purchase()` / `update_purchase()`,
`DebtsService.create()` / `update()`; en los `update`, `''` borra la
etiqueta). Las tres pantallas la muestran como columna TAG, editable inline.

- Se agregan con `MigracionColumna` y no en `schema.sql` (pedido explícito de
  no tocar `schema.sql`). `compras_cuotas.tag` va en `MIGRACIONES_COLUMNA`;
  `deudas.tag`, en `MIGRACIONES_COLUMNA_DEUDAS`, que corre después de
  `reestructurar_deudas()`: en una base vieja esa función rearma la tabla
  desde `DDL_DEUDAS_FINAL`, y una columna agregada antes se perdería en esa
  misma corrida.
- Sin dependencias con estado propio: se edita y se borra siempre
  (CLAUDE.md §4).
- Sincronización: viaja en `datos` como cualquier otra columna; no hace falta
  tocar Supabase.
- Los cargos extra de un resumen (`resumen_cargos_extra`) no tienen tag: en
  Compras en cuotas, el tag del alta solo se usa con una categoría normal.

## 27. Referencias de filas compartidas a filas privadas — ✅ implementado (`sync/referencias.py`, sin cambio de schema)

**Problema.** Cada miembro del hogar tiene su propia base, y las categorías y
transacciones son privadas: cada base tiene las suyas, con sus propios UUID
(las del seed también: el mismo "Supermercado" tiene un UUID distinto en cada
base). Pero dos tablas compartidas apuntan a ellas:

- `gastos_compartidos.categoria_id` → `categorias` (NOT NULL).
- `gasto_compartido_pagos.transaccion_id` → `transacciones` (acepta NULL).

Una fila de NOELIA con su `categoria_id` no se podía guardar en la base de
BRUNO: `FOREIGN KEY constraint failed`, y como la marca de bajada seguía de
largo, no se volvía a intentar.

**Decisión: traducir en la frontera de la sync, sin tocar el schema.**

- En la base de quien carga el gasto, `categoria_id` sigue siendo una FK
  normal: ahí el UUID es correcto y la integridad se mantiene. Lo que no vale
  es mandar ese UUID solo a otra base, donde no significa nada.
- Al subir una fila compartida, dentro de `datos` viaja `_referencias`: por
  cada FK a una tabla privada, la fila apuntada descripta por su clave natural
  (`CLAVES_NATURALES`) más las columnas sin las que no se puede insertar. Ej.
  `{"categoria_id": {"categoria_principal": "EGRESOS VARIABLES",
  "subcategoria": "Supermercado", "tipo": "egreso"}}`. Es una marca como
  `_borrado` y `_modificado_en`: no es una columna local, y Supabase no cambia.
- Al bajar, cada base traduce por su cuenta, en este orden: la fila local con
  la misma clave natural → si no hay, se crea (UUID propio, **inactiva**:
  `activa = 0`) → si no se puede crear y la columna acepta NULL, NULL → si no,
  error con un mensaje que dice qué columna no se pudo traducir.
- Qué FK son "privadas" sale del schema (`PRAGMA foreign_key_list`): toda FK de
  una tabla de `TABLAS_COMPARTIDAS` a una tabla sincronizada que no es
  compartida. Una FK nueva de ese tipo queda cubierta sin tocar código.
- Lo que una base no ve, no lo pisa: si esta base guardó NULL porque no pudo
  traducir (ej. la transacción con la que pagó el otro) y vuelve a subir la
  fila, se conserva el valor de Supabase. Si no, se perdería el vínculo en la
  base del autor.

**Por qué esto y no otra cosa:**

- *Sincronizar el catálogo de categorías del hogar*: no. Las categorías son de
  cada uno. Unificarlas obligaría a cambiar UUIDs de categorías ya usadas en
  transacciones, presupuestos y compras privadas (como hace la adopción por
  clave natural al restaurar), y esas filas quedarían distintas de sus copias en
  Supabase. Además, haría que todos tengan las mismas categorías, justo lo que
  no se quería.
- *Guardar el nombre en una columna nueva de `gastos_compartidos` y hacer
  `categoria_id` nullable*: resuelve lo mismo, pero SQLite no cambia una FK sin
  reconstruir la tabla (con `gasto_compartido_pagos` colgando, la vista
  `vw_saldo_neto_hogar` y los triggers), sobre las bases reales de los dos.
  También habría que cambiar el repositorio y la pantalla (`INNER JOIN` →
  `LEFT JOIN`). La traducción da lo mismo sin migrar nada.
- *Crear la categoría faltante activa*: ensuciaría el catálogo del otro. Nace
  inactiva: la FK se cumple, el nombre se ve en Compartidos, y no aparece para
  cargar. En la pantalla Categorías se ve como inactiva y se puede reactivar.
  Sube como categoría privada de quien la tiene, como cualquier alta.

**Reparación de lo que ya estaba en Supabase.** Las filas subidas antes de este
cambio, o por `migration/subir_a_supabase.py`, no tienen `_referencias`, y solo
su autor puede describirlas. En la primera sync completa con esta versión, cada
base:

1. Completa `_referencias` en las filas compartidas que apuntan a filas suyas
   (upsert del mismo `datos` + la marca, sin tocar `_modificado_en`).
2. Baja las compartidas desde el principio, para reintentar lo que antes falló y
   quedó atrás de la marca de bajada.

Lo anota en `sync_estado` (clave `referencias_portables`) y no lo repite. Da
igual qué miembro actualice primero: el upsert de la reparación cambia el
`updated_at` de la fila, y el otro la baja en su próxima sync.

**Límites conocidos:**

- Si el autor renombra la categoría después de compartir, las otras bases
  siguen con el nombre viejo. Si otro miembro vuelve a subir la fila, el
  autor la traduce por ese nombre viejo y puede terminar con una categoría
  inactiva nueva con el nombre anterior.
- Una FK privada a una tabla sin clave natural y NOT NULL no tiene traducción
  posible: sigue siendo error. Hoy no hay ninguna.
- Privacidad: lo que viaja en `_referencias` (nombre y tipo de la categoría) lo
  ve todo el hogar. Si en el futuro una tabla compartida apunta a `cuentas`,
  viajaría el nombre de la cuenta.
- No resuelve el `origen_id` polimórfico (no es una FK declarada): en la base
  del otro miembro, el concepto y la moneda de un gasto compartido siguen
  saliendo de un origen que no está ahí.

Verificación: `verify/sync/verify_referencias_compartidas.py`.


## 28. Cargos extra de tarjeta en `compras_cuotas` — ✅ implementado (vía `db/schema_migrations.py`)

**Pedido:** los impuestos, recargos y ajustes/reintegros que se cargan en Compras en
cuotas con las categorías especiales de TARJETA DE CRÉDITO tienen que aparecer en el
total por tarjeta (barra de arriba) y en la tabla, cada uno en su moneda, y
sincronizarse.

**Antes:** vivían en `resumen_cargos_extra`, que no se sincroniza (no está en
`TABLAS_SINCRONIZADAS`), no se listaban en la tabla y `resumen_por_tarjeta()` solo los
sumaba si la tarjeta tenía cuotas ese mes en una sola moneda (sección 15). Un paso
intermedio les agregó `moneda_id` y `fecha` a esa tabla (las columnas quedan:
la migración de abajo las usa).

**Modelo:** un cargo extra es una fila de `compras_cuotas` con
`es_cargo_extra = 1` (columna nueva, `INTEGER NOT NULL DEFAULT 0`, vía
`MigracionColumna`):

- `total_cuotas = 1`; `monto_total_minor = monto_por_cuota_minor` = el monto del
  cargo, con su signo (negativo = a favor).
- `categoria_id` = la categoría especial de su tipo (`CATEGORIAS_CARGO_EXTRA`): la
  categoría ES el tipo. El tipo `'otro'` no tiene categoría y deja de aceptarse.
- `moneda_id` y `fecha_compra`: los de la fila de alta. Sin moneda, la única de la
  tarjeta; con varias, hay que pasarla. Sin fecha, el día 1 del mes del resumen.
- Una cuota en `cuotas_credito` en el mes de su resumen, ya incluida en él
  (`resumen_id`, `'en_resumen'`): el cargo es parte de ese resumen.

Como `compras_cuotas` y `cuotas_credito` se sincronizan, los cargos también. Las
filas nuevas quedan con `sincronizado_en` NULL (pendientes de subir), como cualquier
alta.

**Service (`FeesService`):**

- `add_extra_charge(statement_id, …, currency_code=None, date_str=None)`: misma
  firma; crea la compra y su cuota. Sigue exigiendo el resumen abierto.
- `delete_extra_charge(charge_id)`: borra la compra y su cuota mientras el resumen
  esté abierto (cerrado o pagado: `StatementAlreadyClosedError` /
  `StatementAlreadyPaidError`). `remove_extra_charge(charge_id, statement_id)` queda
  para quien ya pasa el resumen: valida que el cargo sea de ese resumen y llama a
  `delete_extra_charge()`.
- `list_extra_charges(statement_id)` / `list_extra_charges_in_month(month, year)`:
  leen de `compras_cuotas` (`es_cargo_extra = 1`), con `monto_minor`, `tipo`, moneda,
  fecha y `resumen_id`.
- `resumen_por_tarjeta()`: suma por (tarjeta, moneda) las cuotas de compras en
  `monto_cuotas_minor` y las de cargos en `monto_cargos_extra_minor`. Una tarjeta con
  solo cargos ese mes también aparece. `cargos_extra_multiples_monedas` queda siempre
  en False (ya no hay ambigüedad).
- `close_statement()` (`ResumenesTarjetaRepository.marcar_cerrado()`): los cargos son
  impuestos, no consumos — más lo que haya quedado en `resumen_cargos_extra`.

**Migración de lo que ya estaba** (`migrar_cargos_extra_a_compras()`, en cada
`inicializar()`, no hace nada si no queda nada que mover):

- Cada fila de `resumen_cargos_extra` pasa a `compras_cuotas` con el MISMO id, más su
  cuota (estado `'pagado'` si el resumen se pagó, `'en_resumen'` si no), y se borra de
  la tabla vieja. Todo en una transacción, con backup del archivo antes.
- Moneda: la del cargo; sin ella, la única de las compras con cuotas de esa tarjeta
  ese mes; sin cuotas, la única de la tarjeta; si no, **ARS**
  (`compras_cuotas.moneda_id` es NOT NULL y los impuestos de una tarjeta se cobran
  en pesos).
- Fecha: la del cargo; sin ella, el día 1 del mes del resumen.
- Un cargo de tipo `'otro'` (la pantalla nunca los creó) o cuya categoría especial no
  está en la base no se mueve: queda en `resumen_cargos_extra` y se avisa por consola.
- No corre con ids enteros (`migrar_a_uuid_pk.py` migra una copia así antes de
  convertirla).
- `resumen_cargos_extra` y su repositorio quedan **deprecados**: la tabla se sigue
  creando y `marcar_cerrado()` todavía suma lo que haya quedado.

**Pantalla Compras en cuotas:** los cargos llegan con las compras del mes y se ven
como filas con ícono de recibo: Categoría = la especial, Monto con signo, Cuotas y
1ª cuota "—". Solo lectura (sin cronograma ni edición inline). Se eliminan desde la
barra flotante, compartir los saltea, y un botón de recibo en la barra del total los
muestra u oculta (el total los cuenta siempre).

**En qué resumen entra un cargo** (pedido explícito): el de la **1ª cuota** de la fila
de alta, no el de su fecha. La fecha es la del hecho (ej. la devolución del súper),
y puede llegar en otro resumen. La 1ª cuota se sugiere igual que para una compra
(`suggest_first_fee()`) y se puede cambiar a cualquier mes. El service ya lo permitía:
`add_extra_charge()` no ata `date_str` al mes del resumen.

**Signo del monto** (pedido explícito): positivo o negativo con cualquier categoría.
Es cuidado del usuario, no una validación del sistema. `create_purchase()` acepta
totales negativos (una devolución: total y cuotas negativos) y solo rechaza el 0; la
pantalla ya no bloquea "monto negativo con categoría normal".

**Efecto a tener en cuenta:** al ser compras de una categoría, cualquier reporte que
sume `compras_cuotas` por categoría ve los cargos en "Impuesto tarjeta" / "Recargo
tarjeta" / "Ajuste/Reintegro tarjeta".

Verificación: `verify/compras_cuotas/verify_cargos_extra_sync.py` y
`verify_cargos_extra_moneda_fecha.py`.

## 29. Fechas reales de cierre y vencimiento por resumen — ✅ implementado (vía `db/schema_migrations.py`)

`tarjetas_config` guarda un día de cierre y uno de vencimiento por tarjeta (sección
23). Los bancos a veces corren un cierre (feriados, cambios de calendario): hace falta
poder cargar la fecha real de un resumen puntual.

**Tabla** `tarjetas_resumenes(id, cuenta_id, mes, anio, fecha_cierre, fecha_vence,
creada_en, updated_en)`, `UNIQUE(cuenta_id, mes, anio)`, en `MIGRACIONES_TABLA`
junto a `tarjetas_config`. Las dos fechas son opcionales (NULL = la calculada).
`updated_en` lo escribe el repositorio (`TarjetasResumenesRepository.guardar()`,
upsert parcial).

**Qué resumen es (mes, anio):** el mes en que cae su cierre con el día default — el
mismo período que recorre `card_cycle_dates()`. Ej.: con cierre el 15, el resumen
(09, 2026) es el que cierra el 15/09. Si su fecha real de cierre cae en otro mes
(ej. el 02/10), sigue siendo el (09, 2026).

**Service (`FeesService`):**

- `get_fecha_cierre(cuenta, mes, anio)` / `get_fecha_vencimiento(…)`: primero la
  fecha real; si no hay, la calculada desde `tarjetas_config` (sección 23). Con un
  cierre real y sin vencimiento real, el vencimiento se calcula desde ese cierre real.
  None si la tarjeta no tiene ninguna de las dos cosas.
- `set_fechas_resumen(cuenta, mes, anio, closing_date=…, due_date=…)`: cada fecha por
  separado (la que no se pasa no se toca); `None` / vacía la borra. Solo tarjetas de
  crédito. Rechaza, sin guardar nada, un vencimiento que no es posterior al cierre.
- `card_cycle_periods()`: ANTERIOR / ACTUAL / PRÓXIMO con su (mes, anio), sus fechas y
  si cada una es real. ACTUAL es el último que cerró, con la fecha real si la hay.
  `card_cycle_dates()` sigue devolviendo la misma forma de antes, ahora con las fechas
  reales.
- `suggest_first_fee()`: la compra cae en el primer resumen que cierra ese día o
  después (con su fecha real), y la 1ª cuota es el mes siguiente a ese resumen. Se
  mira desde el resumen del mes anterior, por si una fecha real corrió su cierre a
  este mes. Sin fechas reales da lo mismo que antes.

**Pantalla:** en el panel ⚙ de Compras en cuotas, cada tarjeta muestra sus días
default (se guardan con GUARDAR DÍAS DEFAULT) y sus tres resúmenes con la fecha de
cierre y la de vencimiento. Cada fecha se edita con un click (DD/MM/AAAA) y se guarda
al confirmar; vacía vuelve a la calculada. Una fecha real se ve en color de acento.

**Se sincroniza** (está en `TABLAS_SINCRONIZADAS`, después de `tarjetas_config`;
pedido explícito): tabla privada, en `deltabalance_filas` como las demás. Su
`sincronizado_en` y sus triggers los agrega `preparar_sync()`, así que cada fecha
guardada queda pendiente de subir sin que el service avise nada; las filas que ya
existían quedan pendientes por `sincronizado_en` NULL. Mismo límite que
`tarjetas_config`: si dos computadoras del mismo usuario cargan una fecha para el
mismo resumen antes de sincronizar, la segunda choca con el `UNIQUE(cuenta_id, mes,
anio)` al bajar y se cuenta como error (sección 24: una computadora por usuario a
la vez).

Verificación: `verify/compras_cuotas/verify_tarjetas_resumenes.py`.

## 30. Reestructuración del catálogo de categorías — ✅ implementado (`migration/reestructurar_categorias.py`, sin cambio de schema)

**Catálogo final** (`CATEGORIAS_FINALES` del script = sección CATEGORIAS de `db/seed.sql`):
todo en MAYÚSCULAS, los egresos bajo una sola categoría principal `EGRESOS` (antes
`EGRESOS FIJOS` / `EGRESOS VARIABLES`), `INGRESOS`, `MOVIMIENTO CAPITAL` y `TARJETA DE
CRÉDITO`. `EGRESOS / EGRESO VARIABLE` es transitoria.

**Migración de una base existente** (`MAPA_MIGRACION`: nombre viejo exacto → nombre
final). Decisión con el usuario: **renombrar en el lugar**, no crear una categoría
nueva por cada una:

- Si el destino no existe, la categoría vieja toma el nombre y el tipo nuevos con el
  **mismo id**: ninguna transacción, compra, presupuesto ni gasto compartido se toca, y
  a Supabase sube una sola fila por categoría. Si varias van al mismo destino (Sueldo +
  BECA → SUELDO / BECA, Impuestos + Servicios → SERVICIOS BÁSICOS…), se renombra la
  que más filas usan.
- Las demás se **fusionan**: las filas de cada tabla con una FK a `categorias` (salen de
  `PRAGMA foreign_key_list`) pasan al destino y la vieja queda `activa = 0` (sección 11:
  nunca DELETE).
- Lo que falte de `CATEGORIAS_FINALES` se crea; lo que existe inactivo se reactiva; un
  tipo distinto se corrige.
- Un cambio de tipo de categoría (ej. REINTEGRO PROMOCION: egreso → ingreso) se informa
  con cuántas transacciones de cada tipo de movimiento la usan; el tipo de movimiento de
  las transacciones no se toca. Una categoría de tipo ingreso no aparece en los
  selectores que filtran egresos (Compras en cuotas, Presupuestos).
- Las categorías fuera del mapa y del catálogo (las creadas a mano) quedan como están.

Dry-run por default; `--confirmar` se niega si la app tiene la base abierta, hace backup
(con el `-wal` ya pasado al archivo) y aplica todo en **una transacción**. Idempotente.

**Código que reconoce categorías por nombre:** `CATEGORIAS_PROTEGIDAS`
(`services/categorias_service.py`), `CATEGORIAS_CARGO_EXTRA`
(`services/fees_service.py`), el routing del Registro
(`ui/components/registro_transacciones.py`), la categoría de ahorro
(`services/savings_service.py`) y la migración de cargos extra
(`db/schema_migrations.py`) usan los nombres nuevos y comparan con
`utils/categorias.py clave_categoria()`: sin espacios de más y en mayúsculas, en Python
(`UPPER()` de SQLite no pasa la Ó). Así reconocen igual una base todavía sin migrar
("Impuesto tarjeta") que una migrada ("IMPUESTO TARJETA"). Única excepción: "Sueldo"
pasa a "SUELDO / BECA" (no es solo mayúsculas): en una base sin migrar deja de estar
protegida hasta correr el script.

**Sincronización:**

- Todo lo que escribe el script pasa por los triggers de sync y sube en la próxima
  sincronización.
- Sincronizar ANTES de correrlo: una fusión reescribe gastos compartidos, y si el otro
  miembro cambió uno que esta base todavía no bajó, la versión local lo pisaría
  (last-write-wins).
- Correrlo en la base de CADA miembro: un gasto compartido lleva su categoría por nombre
  (sección 27, comparación exacta en `SyncRepository.id_por_clave_natural()`); una base
  con los nombres viejos no reconoce los nuevos y crea una categoría inactiva con ese
  nombre.

**Quedan con los nombres viejos** (no se tocaron): los scripts históricos de
`migration/` (`agregar_categoria_*.py`, `migrar_*.py` — no volver a correrlos sobre una
base migrada) y `sync/tests/` (pytest, fuera del flujo de `verify/`).

## 31. Objetivos de ahorro por movimiento — ✅ implementado (sin cambio de schema)

**Problema.** El rediseño de Ahorros e Inversiones había puesto un reparto FIJO por
activo (`activo_objetivos`: "este FCI es 70% MOTO, 30% TERRENEITOR") del que cada
movimiento copiaba sus `asignaciones`, y el resumen por objetivo calculaba saldo de hoy ×
porcentaje de hoy. Eso obliga a decidir el reparto al crear el instrumento y a
recalcularlo cada vez que entra plata para otro objetivo. Pedido del usuario: "ingreso X
al FCI y de este monto reparto para estos objetivos", en varias cargas, en días
distintos.

**Decisión.** Vuelve el criterio de la sección 4: los objetivos son de cada
movimiento. `asignaciones` ya era por movimiento (porcentaje + monto asignado), así que
no hay cambio de schema.

- **Alta** (`SavingsService.registrar_*()`, parámetro `asignaciones`): lista de
  `{objetivo_id, porcentaje}`, validada antes de escribir (cada objetivo existe, sin
  repetir, porcentaje entre 0 y 100, suma ≤ 100). Lo que no llega al 100% queda **sin
  asignar**. `None` o `[]` = sin objetivos.
- **Rendimiento** (decisión del usuario: proporcional, editable): sin asignaciones
  (`None`) se reparte según `get_reparto_proporcional()` — lo que cada objetivo tenía en
  el activo **antes** de la fecha del rendimiento (un movimiento del mismo día no cuenta:
  el interés que se acredita hoy lo generó lo que había hasta ayer), en unidades en
  acciones / CEDEARs y en plata en el resto. Porcentajes con 4 decimales, redondeados
  con el método del resto mayor para que un reparto que cubría el 100% lo siga
  cubriendo. Un objetivo en cero o negativo no entra. Con una lista, esa.
- **Retiros / ventas** (decisión del usuario): solo se valida contra el total del
  activo, no contra lo de cada objetivo. Un objetivo puede quedar en negativo y el
  resumen lo muestra así.
- **Corrección** (`update_asignaciones()`): reemplaza las asignaciones de un movimiento
  ya cargado, también si está vinculado a una transacción del Registro (la transacción
  depende del monto y la fecha, no de los objetivos — sección 9 / CLAUDE.md §4). No
  recalcula otros movimientos: un rendimiento proporcional ya cargado conserva su
  reparto (se puede recalcular desde su celda con "USAR REPARTO PROPORCIONAL").
- **Resumen** (`get_resumen_por_tipo()` / `get_resumen_por_objetivo()`): la parte de
  cada objetivo en cada activo es la suma de lo asignado (aportes, compras y
  rendimientos suman; ventas y retiros restan); en unidades, cantidad × porcentaje de
  cada movimiento. SIN ASIGNAR = el total del activo menos esas partes.

**`activo_objetivos` queda DEPRECATED:** nada la lee ni la escribe
(`assign_objetivo()` / `remove_objetivo()` se borraron del service, y su UI — el botón
OBJETIVOS de la tarjeta y la sección OBJETIVOS al crear un activo — también), salvo
borrar las filas de un objetivo que se elimina (sección 32, por la FK). La tabla no se
borra: puede tener filas (mismo criterio que `resumen_cargos_extra`, sección 28). Las
tablas de ahorros no están en `TABLAS_SINCRONIZADAS`: cada base tiene los suyos. Una base que ya tenía repartos cargados: los
movimientos registrados con ese reparto conservan sus asignaciones; los cargados antes
de definirlo quedan SIN ASIGNAR y se corrigen desde la celda OBJETIVOS.

**UI** (`ui/screens/ahorros.py`): la celda OBJETIVOS de la tabla de movimientos (fila
de alta y filas ya cargadas) abre `dialogo_compra_ahorro.construir_objetivos_movimiento()`
(objetivo + %, el monto de cada fila, "+ CREAR NUEVO OBJETIVO" ahí mismo, ASIGNADO · SIN
ASIGNAR). La fila de alta conserva los objetivos elegidos entre cargas; un RENDIMIENTO
arranca en PROPORCIONAL. El diálogo de movimiento de la tarjeta tiene la misma sección.

Verify: `verify/ahorros/verify_objetivos_por_movimiento.py`.

## 32. Editar y eliminar objetivos de ahorro — ✅ implementado (sin cambio de schema)

Pedido del usuario: poder editar los objetivos y eliminarlos eligiendo cómo se reparten
los instrumentos que tenían.

**Editar** (`SavingsService.update_objetivo()`): nombre, meta y fecha meta, edición
directa (sección 9): las asignaciones lo referencian por id y nada depende del nombre
ni de la meta. `create_objetivo()` y `update_objetivo()` validan lo mismo
(`_datos_objetivo()`: nombre no vacío, meta entera > 0 o sin meta, fecha meta
AAAA-MM-DD o sin fecha). El `estado` (activo / cumplido / cancelado) no se edita desde
la app.

**Eliminar** (`delete_objetivo(objetivo_id, repartos)`): el objetivo tiene dependencias
(sus `asignaciones`), así que no se borra en silencio: antes se pasa su parte a otros
objetivos, elegida por el usuario por cada instrumento (`repartos`: activo_id →
`[{objetivo_id, porcentaje}]`, suma ≤ 100, lo que falta queda sin asignar).

- Cada asignación del objetivo en un instrumento se reparte con esos porcentajes y se
  suma a la que el destino ya tuviera en el mismo movimiento. Como es lineal, la parte
  que tenía en el instrumento (saldo o unidades) pasa entera en esa proporción, y el
  historial de cada movimiento queda coherente con el resumen. Montos con el redondeo
  del alta.
- `get_partes_de_objetivo()` lista los instrumentos a repartir, también aquellos donde
  el objetivo quedó en cero (ej. un plazo fijo ya retirado): ahí solo cambia a quién
  figura el historial; la UI los deja sin asignar por defecto.
- No se puede repartir hacia el mismo objetivo.
- DELETE físico (`objetivos_ahorro` no tiene soft-delete), en una transacción con la
  reescritura de las asignaciones y el borrado de sus filas en `activo_objetivos`
  (deprecated, sección 31: la FK lo exige).
- Las tablas de ahorros no se sincronizan: el borrado es solo de esta base.

**UI** (`ui/screens/ahorros.py`): ✎ y 🗑 junto a cada objetivo en RESUMEN · POR
OBJETIVO, que ahora lista todos los objetivos (también los que no tienen nada) con su
meta. 🗑 abre `dialogo_compra_ahorro.construir_eliminar_objetivo()`: un editor de reparto
por instrumento, sin "+ CREAR NUEVO OBJETIVO" (cada editor tiene su propia lista; un
objetivo creado desde uno no aparecería en los otros) — el destino nuevo se crea antes
con + NUEVO OBJETIVO.

Verify: `verify/ahorros/verify_editar_eliminar_objetivos.py`.

## 33. Moneda por movimiento en Ahorros e Inversiones — ✅ implementado (vía `db/schema_migrations.py`)

Pedido del usuario: un CEDEAR se compra en pesos y se vende en dólares (MEP); la moneda
tiene que ser de cada movimiento, no solo del activo.

**Schema.** `movimientos_activo.moneda_id INTEGER REFERENCES monedas(id)`, agregada por
`MIGRACIONES_COLUMNA` (no está en `db/schema.sql`, mismo criterio que `comision_minor` o
`transaccion_id`). Nullable porque SQLite no admite un `ADD COLUMN ... NOT NULL` con FK
y sin default; el backfill pone la moneda del activo en las filas que ya existían, y
`SavingsService` la escribe siempre (también los métodos viejos `register_purchase()`,
`register_sale()` y `register_return()`). Las lecturas que agrupan por moneda usan
`COALESCE(ma.moneda_id, af.moneda_id)` por las dudas. La columna también está en
`DDL_MOVIMIENTOS_FINAL`: `ampliar_tipos_ahorro()` corre después de las migraciones de
columna y reconstruye la tabla en una base vieja; sin eso, la perdería.

**Dónde puede ser otra moneda (decisión del usuario): solo en acciones / CEDEARs**
(`TIPOS_POR_UNIDADES`). En FCI, plazos y el resto la moneda de un movimiento es siempre
la del activo: un FCI en dólares es otro activo. `SavingsService._moneda_movimiento()`
lo aplica en `registrar_compra/venta/rendimiento()` (parámetro `moneda_id`, None = la
del activo) y en `update_movement(moneda_id=...)`; `registrar_aporte/retiro()` no lo
reciben.

**Por qué alcanza con eso para no mezclar monedas.** Todo lo que se calcula en plata
sobre un activo (saldo, validación de retiros, parte de cada objetivo, reparto
proporcional de un rendimiento) sigue sumando montos sin mirar la moneda. Eso es
correcto en los activos que tienen una sola moneda. En acciones / CEDEARs lo que manda
son las **unidades**, que no tienen moneda: tenencia, reparto entre objetivos,
proporcional de un dividendo, validación de una venta. Lo único en plata que se
muestra de ellas es el **precio promedio**, que ahora va **por moneda**
(`_precios_promedio()`: monto bruto / unidades de las compras de cada moneda). Su
`saldo_minor` puede mezclar monedas y no se muestra. `get_balance_por_tipo()` agrupa
por la moneda del movimiento.

**Compra / venta por monto bruto (decisión del usuario).** Se cargan CANTIDAD + MONTO,
ya no el precio unitario. MONTO = cantidad × precio, **sin** la comisión: compra total
= monto + comisión, venta total = monto − comisión (igual que antes). El precio
unitario se guarda calculado (`round(monto / cantidad)`) y no se edita;
`update_movement(monto_minor=...)` en una compra / venta es el bruto
(`_monto_bruto()`), y `list_movimientos()` lo devuelve como `monto_minor`. Las filas
viejas (cargadas por cantidad × precio) tienen el mismo bruto: total − comisión.

**Transacción vinculada** del Registro: en la moneda del movimiento (si la cuenta no
operaba en esa moneda, `TransaccionesRepository` le crea el saldo en 0, sección 14).

**Firma.** `registrar_compra/venta(activo_id, cantidad, monto_minor, comision_minor,
fecha, ..., moneda_id=None)`: `moneda_id` va al final y se conservan `notas`,
`crear_transaccion` y `asignaciones` (el pedido proponía otra firma que los dejaba
afuera y corría los posicionales).

**Pendiente (no se tocó):** `get_balance_por_activo()` devuelve un saldo por activo que
en un CEDEAR con movimientos en dos monedas las mezcla, y `get_objetivo_balance()` suma
montos de activos en distintas monedas (ya lo hacía antes); ninguno lo usa la UI. Al
vincular una transacción existente del Registro no se valida que esté en la misma
moneda que el movimiento. La Σ de la barra flotante de la tabla suma los montos
seleccionados sin mirar su moneda.

Verify: `verify/ahorros/verify_moneda_movimiento.py`.

## 34. Dashboard: resumen del mes y balance DISPONIBLE — ✅ implementado (sin cambio de schema)

Pantalla DASHBOARD (`ui/screens/resumen_mes.py`, la que abre la app; `ui/screens/
dashboard.py` es, por historia, la del Registro) alimentada por
`DashboardService.get_resumen_mes(mes, anio, usuario_local, moneda_codigo)`. Solo
lectura: compone los services de cada dominio y no duplica sus reglas.

**Una moneda por resumen.** Nunca se suman pesos con dólares: el resumen es de una
moneda (ARS por defecto) y `monedas_disponibles` lista las que tienen datos ese mes
(la pantalla muestra pills para cambiar).

**De dónde sale cada número (decisiones del usuario):**

- **Ingresos:** `IngresosService.list_by_month()`, estimado y cobrado, en total y por
  concepto.
- **Egresos fijos:** presupuestos `fijo` del mes, estimado y pagado (real cargado a
  mano), en total y por concepto; lo que **falta pagar** de cada uno (estimado − real,
  nunca negativo) se muestra como dato.
- **Cuotas:** `FeesService.resumen_por_tarjeta()`, el total de cada tarjeta.
- **Gastos del Registro:** egresos del mes por categoría, salvo
  `CATEGORIAS_EXCLUIDAS_GASTOS` (constante en `services/dashboard_service.py`, pedido
  del usuario para poder editarla: hoy AUTOTRANSFERENCIA y PAGO TARJETA — este último
  ya está en las cuotas). El real es el de `PresupuestosService.get_real_variable()`:
  un gasto compartido que pagó el usuario cuenta solo su parte. Estimado: el
  presupuesto variable de la categoría, si tiene; las categorías con presupuesto y sin
  gasto aparecen en 0. Las de `CATEGORIAS_DE_FIJOS` (otra constante editable: hoy
  VIVIENDA, SERVICIOS BÁSICOS y SEGUROS — donde el usuario paga sus fijos) se muestran
  como información de EGRESOS FIJOS pero no son "gastos variables" del balance: ya
  cuentan como egresos fijos.
- **Gastos compartidos:** la parte del usuario de lo que pagó el OTRO miembro en el mes
  (`monto_adeudado_minor` con pagador ≠ usuario local), por categoría — la regla del
  ítem COMPARTIDOS de Presupuestos. **Informativo: no entra en el balance** (lo que
  debe de eso ya está en las deudas).
- **Deudas — ACUMULADO hasta el último día del mes, sin filtro de mes** (la pantalla lo
  aclara): informales, `DebtsService.summary_by_person()` de cada tab; compartidos, el
  pendiente (`monto_pendiente_minor`, ya descuenta pagos parciales) de los gastos
  `pendiente`: los que pagó el usuario se los deben (persona: los otros miembros del
  hogar), los que pagó otro los debe él (persona: quien pagó). Un saldo negativo pasa
  al otro lado (alguien que pagó de más: se le debe).
- **DISPONIBLE, en dos modos** (switch ESTIMADO / REAL de la pantalla; el service
  devuelve los dos en `balance[modo]`):
  - ESTIMADO = ingresos estimados − fijos estimados − cuotas − gastos variables
    estimados + neto de deudas;
  - REAL = ingresos cobrados − fijos pagados − cuotas − gastos variables reales + neto
    de deudas.

  Gastos variables = GASTOS DEL MES sin las categorías de fijos. En REAL, lo gastado
  de cada categoría. En ESTIMADO (decisión del usuario, para ver lo real y lo
  proyectado juntos), lo gastado **más lo que falta** de su presupuesto (presupuesto −
  gastado, nunca negativo) — en la práctica el mayor de los dos: si ya se pasó, resta
  lo gastado; **sin presupuesto, resta lo gastado** (antes, 0: lo gastado en
  categorías sin presupuesto no aparecía en el ESTIMADO).
  `DashboardService._gasto_variable()`. (La primera versión restaba los fijos que
  faltaban pagar, los gastos del Registro y los compartidos; se reemplazó por estas
  fórmulas.)

**Doble conteo que queda.** Un gasto compartido que pagó el usuario cuenta en GASTOS solo
con su parte, y lo que le debe el otro de ese gasto suma además en las deudas
(acumuladas): el DISPONIBLE queda por encima por esa parte del otro mientras esté
pendiente. Lo que pagó el otro ya no se cuenta dos veces (los gastos compartidos
salieron del balance). Se dejó así: el usuario eligió deudas con todo lo acumulado.

**Calculadora de escenarios.** Cada modo trae además `grupos` — el DISPONIBLE desarmado
en ítems: ingresos y fijos por concepto, cuotas por tarjeta, variables por categoría y
el neto de deudas — con `aporte_minor` con signo (la suma es el DISPONIBLE) e ids que no
dependen del modo. `DashboardService.calcular_escenario(balance_modo, excluidos,
ajuste_minor)` (función pura) da el DISPONIBLE sin los ítems excluidos y con un ajuste
manual. La pantalla guarda lo excluido y el ajuste en `.deltabalance_prefs.json` bajo
`balance_escenario_{mes}_{anio}_{moneda}` (por moneda: la misma tarjeta puede tener
cuotas en ARS y en USD; se guarda lo EXCLUIDO, así lo nuevo aparece marcado). Es un
"qué pasaría si" de la pantalla: no cambia ningún dato.

**Moneda de un gasto compartido** sin origen en esta base (lo pagó el otro con una
transacción que no se sincroniza): `MONEDA_SIN_ORIGEN_CODIGO`, como Presupuestos.
`SnapshotsService` en cambio los saltea: el SALDO ANTERIOR de Compartidos no los
cuenta (inconsistencia previa, no se tocó).

**Sin usuario local** no se sabe qué pagó cada uno: no hay gastos compartidos y las
deudas son solo las informales (`sin_usuario_local`).

**Pantalla: una sola tarjeta** (pedido del usuario). No hay tarjetas aparte por dominio:
el BALANCE expandido es el dashboard entero. Cada grupo muestra sus ítems con checkbox y,
debajo, en gris, lo que no suma al DISPONIBLE: COBRADOS / ESTIMADOS de los ingresos,
PAGADOS · FALTA de los fijos (y lo del Registro en categorías de fijos), la barra real /
presupuesto de cada gasto variable, y ME DEBEN / DEBO por persona bajo el neto de deudas.
Todo sale del mismo `get_resumen_mes()`; el service no cambió.

Verify: `verify/dashboard/verify_resumen_mes.py`.

## 35. Editar, eliminar y ocultar instrumentos de ahorro — ✅ implementado (sin cambio de schema)

Pedido del usuario. Reglas de CLAUDE.md §4 aplicadas a `activos_financieros`:

- **Editar** (`SavingsService.update_activo()`): nombre, broker, cuenta y comisiones por
  defecto, directo (nada guardado depende de ellos; la cuenta nueva vale para los
  movimientos que se carguen desde ahora, las transacciones ya vinculadas quedan en la
  suya). `NO_CAMBIAR` = no tocar; `None` desvincula broker o cuenta. **El tipo no se
  edita.** **La moneda, solo si el instrumento no tiene ningún movimiento** (decisión
  del usuario): cada movimiento guarda su moneda (sección 33) y en FCI y plazos tiene
  que ser la del activo.
- **Eliminar** (`delete_activo()`): **solo sin ningún movimiento** (sin dependencias:
  borrado directo, junto con sus filas del reparto deprecated `activo_objetivos`, que la
  FK exige borrar antes). Con historial — aunque el saldo sea 0 — se rechaza.
- **Ocultar** (`ocultar_activo()`, `activa = 0`): para un instrumento con historial que
  ya no tiene tenencia. "Sin tenencia" = **0 unidades en acciones / CEDEARs** (su saldo
  en plata puede mezclar monedas o quedar distinto de 0 si se vendió más caro) y **saldo
  0 en el resto**. No borra nada; `reactivar_activo()` lo vuelve a mostrar.
- Con tenencia: ni eliminar ni ocultar.

`get_resumen_por_tipo(incluir_ocultos=True)` trae los ocultos (con `activa`,
`movimientos` y `con_tenencia`, para que la pantalla decida qué ofrecer) y
`list_movimientos()` marca los de un oculto (`activo_activa`). La pantalla de Ahorros
los esconde del RESUMEN, de las tarjetas, de la fila de alta y de la tabla, salvo con
"MOSTRAR OCULTOS" en su pestaña (ahí aparecen atenuados con REACTIVAR).

Repositorios: `ActivosFinancierosRepository.eliminar()` y `cuenta_id` en `actualizar()`;
`ActivoObjetivosRepository.eliminar_por_activo()`.

**Pendiente (no se tocó):** `get_or_create_reserved_cash_asset()` (el "Efectivo
reservado en <cuenta>" del Registro) reusa el activo de esa cuenta aunque esté oculto:
lo que se reserve después queda en un instrumento escondido.

Verify: `verify/ahorros/verify_eliminar_instrumento.py`,
`verify/ahorros/verify_editar_instrumento.py`.
