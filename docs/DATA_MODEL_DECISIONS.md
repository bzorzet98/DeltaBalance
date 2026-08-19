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
