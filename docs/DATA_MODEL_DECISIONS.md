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
Supabase (Postgres + Auth + RLS). Solo `gastos_compartidos` viaja al servidor. Vínculo
por código de invitación (`hogares` + `hogar_miembros`). Polling al abrir la app, no
realtime.
- `hogares` y `hogar_miembros` ya están implementados en `schema.sql` (ver sección 2) —
  son las tablas locales de las que depende este apartado. El resto (cliente `sync/`,
  Supabase, Auth, RLS, polling) sigue sin implementar.

## 9. Regla de edición/borrado
Ver `CLAUDE.md` sección 4 — ventana de corrección temprana según si el registro ya
generó dependencias con estado propio.

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

**Relación cuentas ↔ activos_financieros — informal a propósito, no pendiente
técnico.** `activos_financieros`/`movimientos_activo` (sección 4) no tienen ninguna
columna que los vincule a una `cuentas` puntual — de qué cuenta salió la plata para
comprar un activo, o a qué cuenta vuelve al venderlo, es una decisión consciente de
**no formalizar todavía**, no un hueco que falte cerrar. Si en el futuro hace falta
ese vínculo (ej. para que el saldo de una cuenta de inversión se calcule solo,
descontando compras y sumando ventas), la forma de agregarlo sería una columna
opcional `cuenta_origen_id` en `movimientos_activo` (vía `db/schema_migrations.py`,
como toda columna nueva sobre una tabla existente) — no una tabla puente nueva ni un
cambio a `cuentas_saldos`.

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
- **`categoria_id` es OBLIGATORIO cuando se pasa `cuenta_id`** (`ValueError` si falta)
  — no se asume ninguna categoría por default, el caller la elige explícitamente.
  Mismo criterio exacto que `EmpleosService.create_receipt()` con `cuenta_id`/
  `categoria_id`, que ya resolvía este mismo cruce hacia `transacciones` antes. Se
  prefirió este criterio (parámetro explícito) por sobre asumir una categoría
  "razonable" automáticamente: el catálogo actual no tiene todavía una categoría
  protegida dedicada a ahorro (la futura "Ahorro/Inversión" de la Tarea 1b de
  docs/PROXIMOS_PASOS.md, UI, no existe aún en este paso) y elegir una sin que el
  caller lo sepa hubiera sido inventar una convención no pedida (CLAUDE.md §0.4). El
  verify de este service usa la categoría ya sembrada "MOVIMIENTO CAPITAL ·
  Inversiones" como categoría de ejemplo razonable — no es una categoría protegida ni
  hardcodeada dentro del service.
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
- **`SavingsService.get_or_create_reserved_cash_asset(cuenta_nombre, moneda_id)`**
  (método nuevo): busca por nombre exacto `f"Efectivo reservado en {cuenta_nombre}"`
  un `activo_financiero` tipo='otro' ya existente (incluyendo inactivos —
  `solo_activos=False` — para nunca duplicar uno que el usuario haya desactivado a
  mano) y lo reusa; si no existe, lo crea. Vive en `SavingsService` (motor de
  datos), no en la UI — así queda testeable con un verify normal
  (`verify/ahorros/verify_savings_service.py`) y reusable fuera del Registro si
  algún día hace falta (ej. `migration/`). Coherente con la sección 14
  ("Relación cuentas ↔ activos_financieros — informal a propósito"): la identidad
  de "a qué cuenta pertenece" sigue siendo el nombre exacto del activo, no una
  columna nueva — no se necesitó tocar el schema para esto.
- La moneda del activo genérico se fija en el momento de su PRIMERA creación (la
  moneda elegida en esa primera fila del Registro) y no se reescribe después — si
  una carga posterior a la misma cuenta usa una moneda distinta, la transacción
  vinculada de todos modos queda en la moneda original del activo (limitación
  conocida, no resuelta acá: no se pidió un selector de moneda por activo ni
  activos separados por cuenta+moneda, y el caso de una cuenta reservando ahorro en
  más de una moneda a la vez no es el uso típico descripto por el usuario).

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
