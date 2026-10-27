# DeltaBalance — Modelo de datos objetivo

Este documento describe el modelo de datos **tal como debería quedar** después de implementar las decisiones tomadas en la sesión de profesionalización. El modelo actual está en `docs/DATA_MODEL.md`.

**Resumen de cambios respecto al modelo actual:**

- De 37 tablas a 27 activas + 5 documentadas como versión futura
- FK de monedas migradas a clave natural (`moneda_codigo TEXT`)
- Soft delete unificado: `deleted_at` en tablas sincronizadas, `activa` donde es semántico
- `autotransferencias` eliminada — lógica pasa a diálogo en UI
- `resumenes_tarjeta` y `tarjetas_resumenes` eliminadas — lógica obsoleta
- `gastos_compartidos` simplificada — absorbe pagos como filas negativas
- `gasto_compartido_pagos` eliminada
- `tipos_cambio`, `indices_inflacion`, `resumen_cargos_extra`, `activo_objetivos` eliminadas

---

## Convenciones

| Marca | Significado |
| --- | --- |
| **PK UUID** | `TEXT PRIMARY KEY NOT NULL` generado en Python |
| **ahora** | `NOT NULL DEFAULT CURRENT_TIMESTAMP` |
| **ISO** | `CHECK(col GLOB '????-??-??')` |
| **mes** | `CHECK(col BETWEEN 1 AND 12)` |
| **→ tabla** | \`REFERENCES tabla(codigo |
| **⇅** | Tabla sincronizada con Supabase |

---

## 1. Tablas Base

### `monedas`  ⇅

Catálogo de monedas. La clave natural `codigo` reemplaza el id entero como FK en todo el modelo.

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `codigo` | TEXT | **PRIMARY KEY** (ARS, USD, BTC, etc.) |
| `simbolo` | TEXT |  |
| `decimales` | INTEGER | NOT NULL DEFAULT 2 |

Se sincroniza.&#32;

### `cuentas` ⇅

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `nombre` | TEXT | NOT NULL UNIQUE |  |
| `tipo` | TEXT | NOT NULL CHECK IN (`debito`, `credito`, `efectivo`, `crypto`, `inversion`) |  |
| `cuenta_pago_id` | TEXT | → `cuentas` | Cuenta con la que se paga esta |
| `activa` | INTEGER | NOT NULL DEFAULT 1 | Soft delete semántico — no sincronizada |
| `notas` | TEXT |  |  |
| `color_hex` | TEXT | DEFAULT `'#5F5E5A'` |  |
| `creada_en` | TEXT | ahora |  |
| `updated_en` | TEXT | ahora |  |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

### `cuentas_saldos` ⇅

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` | PK compuesta |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` | PK compuesta. Antes era `moneda_id INTEGER` |
| `saldo_inicial_minor` | INTEGER | NOT NULL DEFAULT 0 |  |
| `visible` | INTEGER | NOT NULL DEFAULT 1 |  |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

PRIMARY KEY (`cuenta_id`, `moneda_codigo`).

### `categorias` ⇅

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `categoria_principal` | TEXT | NOT NULL |  |
| `subcategoria` | TEXT | NOT NULL |  |
| `tipo` | TEXT | NOT NULL CHECK IN (`ingreso`, `egreso`, `movimiento`) |  |
| `activa` | INTEGER | NOT NULL DEFAULT 1 | Soft delete semántico |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

UNIQUE (`categoria_principal`, `subcategoria`).

### `transacciones` ⇅

El Registro. Fuente de verdad de todos los movimientos reales de plata.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `fecha` | TEXT | NOT NULL ISO |  |
| `concepto` | TEXT | NOT NULL |  |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` |  |
| `categoria_id` | TEXT | NOT NULL → `categorias` |  |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` | Antes era `moneda_id INTEGER` |
| `tipo_movimiento` | TEXT | NOT NULL CHECK IN (`ingreso`, `egreso`, `movimiento`) |  |
| `monto_minor` | INTEGER | NOT NULL CHECK(`>= 0`) | Siempre positivo |
| `cuenta_destino_id` | TEXT | → `cuentas` | Solo para AUTOTRANSFERENCIA |
| `tag` | TEXT |  |  |
| `notas` | TEXT |  |  |
| `creada_en` | TEXT | ahora |  |
| `updated_en` | TEXT | ahora |  |
| `deleted_at` | TEXT |  | Soft delete. NULL = viva |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

**Nota:** se agrega `cuenta_destino_id` para reemplazar la tabla `autotransferencias` eliminada. Cuando la categoría es AUTOTRANSFERENCIA, el diálogo completa este campo automáticamente y genera las dos filas en una sola operación atómica.

---

## 2. Tarjeta de crédito

### `compras_cuotas` ⇅

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `id` | TEXT | PK UUID |
| `fecha_compra` | TEXT | NOT NULL ISO |
| `concepto` | TEXT | NOT NULL |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` |
| `categoria_id` | TEXT | NOT NULL → `categorias` |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` |
| `monto_total_minor` | INTEGER | NOT NULL |
| `total_cuotas` | INTEGER | NOT NULL DEFAULT 1 |
| `monto_por_cuota_minor` | INTEGER | NOT NULL |
| `monto_reintegro_minor` | INTEGER | DEFAULT 0 |
| `modo_deuda` | TEXT | DEFAULT `'prorrateado'` CHECK IN (`total_unico`, `prorrateado`) |
| `tag` | TEXT |  |
| `notas` | TEXT |  |
| `creada_en` | TEXT | ahora |
| `updated_en` | TEXT | ahora |
| `deleted_at` | TEXT | Soft delete |
| `sincronizado_en` | TEXT | DEFAULT NULL |

**Eliminado:** `estado` (activa/cancelada/completada) y `es_cargo_extra` — los cargos extra se ingresan como movimientos normales en `transacciones`.

### `cuotas_credito` ⇅

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `compra_id` | TEXT | NOT NULL → `compras_cuotas` |  |
| `numero_cuota` | INTEGER | NOT NULL |  |
| `mes_proyectado` | INTEGER | NOT NULL mes |  |
| `anio_proyectado` | INTEGER | NOT NULL |  |
| `monto_cuota_minor` | INTEGER | NOT NULL |  |
| `monto_pagado_minor` | INTEGER | NOT NULL DEFAULT 0 | Reemplaza el sistema de estados. El usuario anota lo pagado. |
| `notas` | TEXT |  |  |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

**Eliminado:** `resumen_id`, `mes_real_pago`, `anio_real_pago`, `estado` (pendiente/en\_resumen/pagado/omitido) — toda la lógica de resumen queda obsoleta.

UNIQUE (`compra_id`, `numero_cuota`).

### `tarjetas_config` ⇅

Solo informativo — días de cierre y vencimiento por defecto.

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `id` | TEXT | PK UUID |
| `cuenta_id` | TEXT | NOT NULL UNIQUE → `cuentas` |
| `dia_cierre` | INTEGER | NOT NULL CHECK(BETWEEN 1 AND 31) |
| `dia_vencimiento` | INTEGER | NOT NULL CHECK(BETWEEN 1 AND 31) |
| `creada_en` | TEXT | ahora |
| `updated_en` | TEXT | ahora |
| `sincronizado_en` | TEXT | DEFAULT NULL |

---

## 3. Deudas

### `deudas` ⇅

Libro de movimientos de deudas informales con personas.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `entidad_persona` | TEXT | NOT NULL | Normalizada en mayúsculas |
| `concepto` | TEXT |  |  |
| `tab` | TEXT | NOT NULL CHECK IN (`me_deben`, `debo`) |  |
| `monto_minor` | INTEGER | NOT NULL | Con signo: + crece, − es pago |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` | Antes era `moneda_id INTEGER` |
| `fecha` | TEXT | NOT NULL ISO |  |
| `tag` | TEXT |  |  |
| `notas` | TEXT |  |  |
| `origen_tipo` | TEXT | DEFAULT `'manual'` |  |
| `origen_id` | TEXT |  | Referencia polimórfica |
| `creada_en` | TEXT | ahora |  |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

---

## 4. Planificación

### `presupuestos` ⇅

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `tipo` | TEXT | NOT NULL CHECK IN (`fijo`, `variable`) |  |
| `concepto` | TEXT |  | Solo para fijos |
| `categoria_id` | TEXT | → `categorias` | Solo para variables |
| `monto_estimado_minor` | INTEGER | NOT NULL DEFAULT 0 |  |
| `formula_estimado` | TEXT |  |  |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` |  |
| `mes` | INTEGER | NOT NULL mes |  |
| `anio` | INTEGER | NOT NULL |  |
| `es_recurrente` | INTEGER | NOT NULL DEFAULT 0 |  |
| `notas` | TEXT |  |  |
| `creada_en` | TEXT | ahora |  |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

### `ingresos_proyectados` ⇅

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `concepto` | TEXT | NOT NULL |  |
| `monto_estimado_minor` | INTEGER | NOT NULL DEFAULT 0 |  |
| `monto_cobrado_minor` | INTEGER | NOT NULL DEFAULT 0 | Renombrado de `monto_real_minor`. Acepta negativos. |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` |  |
| `mes` | INTEGER | NOT NULL mes |  |
| `anio` | INTEGER | NOT NULL |  |
| `es_recurrente` | INTEGER | NOT NULL DEFAULT 0 |  |
| `notas` | TEXT |  |  |
| `creada_en` | TEXT | ahora |  |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

---

## 5. Ahorro e inversiones

### `brokers` ⇅

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `id` | TEXT | PK UUID |
| `nombre` | TEXT | NOT NULL UNIQUE |
| `tipo` | TEXT |  |
| `activo` | INTEGER | NOT NULL DEFAULT 1 |
| `creada_en` | TEXT | ahora |
| `sincronizado_en` | TEXT | DEFAULT NULL |

### `activos_financieros` ⇅

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `id` | TEXT | PK UUID |
| `nombre` | TEXT | NOT NULL |
| `tipo` | TEXT | NOT NULL CHECK IN (`accion`, `fci`, `plazo_fijo`, `cripto`, `otro`, `cedear`, `plazo_flex`) |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` |
| `activa` | INTEGER | NOT NULL DEFAULT 1 |
| `cuenta_id` | TEXT | → `cuentas` |
| `broker_id` | TEXT | → `brokers` |
| `comision_compra_minor` | INTEGER | DEFAULT 0 |
| `comision_venta_minor` | INTEGER | DEFAULT 0 |
| `creada_en` | TEXT | ahora |
| `updated_en` | TEXT | ahora |
| `sincronizado_en` | TEXT | DEFAULT NULL |

### `movimientos_activo` ⇅

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `id` | TEXT | PK UUID |
| `activo_id` | TEXT | NOT NULL → `activos_financieros` |
| `tipo` | TEXT | NOT NULL CHECK IN (`compra`, `venta`, `rendimiento`, `aporte`) |
| `fecha` | TEXT | NOT NULL ISO |
| `cantidad` | REAL |  |
| `precio_unitario_minor` | INTEGER |  |
| `monto_total_minor` | INTEGER | NOT NULL |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` |
| `transaccion_id` | TEXT | → `transacciones` |
| `comision_minor` | INTEGER | DEFAULT 0 |
| `notas` | TEXT |  |
| `creada_en` | TEXT | ahora |
| `sincronizado_en` | TEXT | DEFAULT NULL |

### `objetivos_ahorro` ⇅

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `nombre` | TEXT | NOT NULL |  |
| `monto_meta_minor` | INTEGER |  | Sin moneda propia — problema conocido, a resolver |
| `moneda_meta_codigo` | TEXT | → `monedas` | Columna nueva: define la moneda del monto meta |
| `fecha_meta` | TEXT | ISO nullable |  |
| `estado` | TEXT | NOT NULL DEFAULT `'activo'` CHECK IN (`activo`, `cumplido`, `cancelado`) |  |
| `creada_en` | TEXT | ahora |  |
| `updated_en` | TEXT | ahora |  |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

### `asignaciones` ⇅

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `id` | TEXT | PK UUID |
| `movimiento_id` | TEXT | NOT NULL → `movimientos_activo` |
| `objetivo_id` | TEXT | NOT NULL → `objetivos_ahorro` |
| `porcentaje` | REAL | NOT NULL CHECK(`> 0 AND <= 100`) |
| `monto_asignado_minor` | INTEGER | NOT NULL |
| `sincronizado_en` | TEXT | DEFAULT NULL |

UNIQUE (`movimiento_id`, `objetivo_id`).

---

## 6. Hogar

### `hogares` ⇅

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `id` | TEXT | PK UUID |
| `codigo_invitacion` | TEXT | NOT NULL UNIQUE |
| `nombre` | TEXT |  |
| `creada_en` | TEXT | ahora |
| `sincronizado_en` | TEXT | DEFAULT NULL |

### `hogar_miembros` ⇅

| Columna | Tipo | Constraints |
| --- | --- | --- |
| `hogar_id` | TEXT | NOT NULL → `hogares` |
| `usuario_local` | TEXT | NOT NULL |
| `porcentaje_default` | REAL |  |
| `sincronizado_en` | TEXT | DEFAULT NULL |

PRIMARY KEY (`hogar_id`, `usuario_local`).

### `gastos_compartidos` ⇅

Simplificada: absorbe los pagos como filas negativas. El saldo neto por miembro surge de `SUM(monto_adeudado_minor)`.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID |  |
| `hogar_id` | TEXT | NOT NULL → `hogares` |  |
| `pagador` | TEXT | NOT NULL |  |
| `origen_tipo` | TEXT | CHECK IN (`transaccion`, `compra_cuotas`, `cuota_credito`, `pago`, `ajuste`) | `pago` y `ajuste` son tipos nuevos para filas negativas |
| `origen_id` | TEXT |  | Referencia polimórfica. NULL para pagos manuales |
| `categoria_id` | TEXT | → `categorias` |  |
| `monto_base_minor` | INTEGER | NOT NULL |  |
| `coeficiente_deuda` | REAL | NOT NULL CHECK(`>= 0 AND <= 100`) |  |
| `monto_adeudado_minor` | INTEGER | NOT NULL | Con signo: positivo = deben, negativo = pago |
| `fecha` | TEXT | NOT NULL ISO |  |
| `descripcion` | TEXT |  |  |
| `creada_en` | TEXT | ahora |  |
| `updated_en` | TEXT | ahora |  |
| `deleted_at` | TEXT |  | Soft delete |
| `sincronizado_en` | TEXT | DEFAULT NULL |  |

**Eliminado:** `estado` (pendiente/saldado), `monto_pendiente_minor`.

---

## 7. Sync y caché

### `sync_cambios`

Sin cambios respecto al modelo actual.

### `sync_estado`

Sin cambios respecto al modelo actual.

### `saldos_mensuales`

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |  |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` |  |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` | Antes era `moneda_id INTEGER` |
| `mes` | INTEGER | NOT NULL mes |  |
| `anio` | INTEGER | NOT NULL |  |
| `saldo_minor` | INTEGER | NOT NULL DEFAULT 0 |  |
| `calculado_en` | TEXT | ahora |  |

### `deudas_mensuales`

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |  |
| `entidad_persona` | TEXT | NOT NULL |  |
| `tab` | TEXT | NOT NULL CHECK IN (`me_deben`, `debo`) |  |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` | Antes era `moneda_id INTEGER` |
| `mes` | INTEGER | NOT NULL mes |  |
| `anio` | INTEGER | NOT NULL |  |
| `monto_minor` | INTEGER | NOT NULL DEFAULT 0 |  |
| `calculado_en` | TEXT | ahora |  |

### `compartidos_mensuales`

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT |  |
| `hogar_id` | TEXT | NOT NULL → `hogares` |  |
| `pagador` | TEXT | NOT NULL |  |
| `moneda_codigo` | TEXT | NOT NULL → `monedas` | Antes era `moneda_id INTEGER` |
| `mes` | INTEGER | NOT NULL mes |  |
| `anio` | INTEGER | NOT NULL |  |
| `monto_minor` | INTEGER | NOT NULL DEFAULT 0 |  |
| `calculado_en` | TEXT | ahora |  |

---

## 8. Versión futura (no implementar ahora)

Estas tablas y sus services y repositories existen en el código pero no tienen UI ni sync. No se tocan hasta que se decida implementar las funcionalidades.

| Tabla | Descripción |
| --- | --- |
| `prestamos` | Préstamos formales con amortización francesa o alemana |
| `cuotas_prestamo` | Cronograma de cuotas de un préstamo |
| `empleos` | Empleos en relación de dependencia |
| `recibos_sueldo` | Recibos de sueldo mensuales |
| `descuentos_programados` | Descuentos puntuales sobre recibos |

Cuando se implementen, hay que agregarlas a `TABLAS_SINCRONIZADAS` y crear los triggers de sync.

---

## 9. Tablas eliminadas

| Tabla | Razón |
| --- | --- |
| `autotransferencias` | Lógica reemplazada por `cuenta_destino_id` en `transacciones` |
| `resumenes_tarjeta` | Lógica de resumen obsoleta según PRD |
| `tarjetas_resumenes` | Redundante con `resumenes_tarjeta` |
| `gasto_compartido_pagos` | Absorbida por `gastos_compartidos` como filas negativas |
| `tipos_cambio` | Sin uso — ningún service la escribe |
| `indices_inflacion` | Sin uso — ningún código la lee |
| `resumen_cargos_extra` | Deprecada — migrar filas restantes antes de eliminar |
| `activo_objetivos` | Deprecada — sacar de `TABLAS_SINCRONIZADAS` antes de eliminar |
