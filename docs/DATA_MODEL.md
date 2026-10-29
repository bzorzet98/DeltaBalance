# DeltaBalance — Modelo de datos (schema real)

Este documento consolida el schema **tal como queda en una base después de
`DatabaseManager.inicializar()`**. Desde el versionado con `PRAGMA user_version`
(`docs/DATA_MODEL_DECISIONS.md` §37):

- `db/schema.sql`: la **foto completa del schema v1**. Solo se aplica a una base nueva.
  Lo único que no trae son los triggers `trg_sync_*`.
- `db/schema_migrations.py`: las migraciones versionadas (`run_migrations()`), la
  baseline v0 → v1 (la cadena anterior de columnas, tablas, reestructuraciones) y
  `preparar_sync()`.
- `db/schema_v0.sql`: el `schema.sql` anterior, congelado. Solo lo usa la baseline.

Se armó leyendo los dos archivos el 2026-10-23. Si cambia alguno, hay que
actualizar este documento.

Este documento dice **qué hay**. El **por qué** y la historia de cada decisión
están en `docs/DATA_MODEL_DECISIONS.md`. Cuando acá aparece "§N", es la sección N
de ese archivo.

---

## 1. Cómo se arma el schema real

`DatabaseManager.inicializar()` (`db/database.py`) lee `PRAGMA user_version`:

- **Base nueva** (sin tablas): `schema.sql` → `sembrar_brokers()` →
  `preparar_sync()` → `seed.sql` (solo si el archivo no existía) → `user_version = 4`
  (`schema.sql` ya la marca él mismo).
- **`user_version` menor que 4**: backup del archivo y las migraciones que falten, en
  orden, marcando la versión al terminar cada una:
  - **v0 → v1 (baseline)**: la cadena de abajo, una sola vez (§37 de
    `DATA_MODEL_DECISIONS.md`);
  - **v1 → v2**: borra `tipos_cambio`, `indices_inflacion`, `resumen_cargos_extra` y
    `activo_objetivos` (§38).
  - **v2 → v3**: `monedas` con `codigo` de PK; todo `moneda_id INTEGER` pasa a
    `moneda_codigo TEXT → monedas(codigo)` (§39).
  - **v3 → v4**: borra `autotransferencias`; las dos patas de una transferencia
    quedan como transacciones independientes (§40).
- **`user_version = 4`**: ninguna migración.
- **Siempre**, al final: `preparar_sync()`.

La baseline (`_migrar_a_v1()` en `db/schema_migrations.py`) corre, en este orden:

1. **`schema_v0.sql`** completo (`executescript`). Lleva `CREATE … IF NOT EXISTS`, así
   que sobre una base existente no cambia ninguna tabla.
2. **`aplicar_migraciones_columna()`**:
   - `exigir_ids_uuid()` frena si la base todavía tiene ids enteros.
   - Después aplica `MIGRACIONES_COLUMNA`: 17 `ALTER TABLE ADD COLUMN`, cada uno solo
     si `PRAGMA table_info` dice que esa columna falta.
3. **`aplicar_migraciones_tabla()`**:
   1. `MIGRACIONES_TABLA` crea `saldos_mensuales`, `deudas_mensuales`,
      `compartidos_mensuales`, `tarjetas_config` y `tarjetas_resumenes`.
   2. `reestructurar_deudas()` convierte una `deudas` vieja a la estructura final.
   3. `MIGRACIONES_COLUMNA_DEUDAS` agrega `deudas.tag`.
   4. `_deudas_mensuales_con_tab()` recrea `deudas_mensuales` si no tiene `tab`.
   5. `reestructurar_presupuestos_ingresos()` convierte las dos tablas a su
      estructura final. Esto pasa **también en una base nueva**, porque `schema.sql`
      las crea con la estructura vieja.
   6. `ampliar_tipos_ahorro()` reconstruye `activos_financieros` y
      `movimientos_activo` si su `CHECK` de `tipo` es el viejo.
   7. `sembrar_brokers()` carga los brokers iniciales.
   8. `migrar_cargos_extra_a_compras()` mueve `resumen_cargos_extra` a
      `compras_cuotas`.
   9. `preparar_sync()`:
      - agrega `sincronizado_en` a las 22 tablas sincronizadas;
      - crea `sync_cambios` y `sync_estado`;
      - borra y vuelve a crear los 66 triggers `trg_sync_*`.

El `seed.sql` (monedas, categorías y la cuenta "Caja Efectivo") ya no forma parte de
esta cadena: solo corre al crear una base nueva.

**Inventario (v4):** 32 tablas, todas en `schema.sql`. Además hay 3 vistas, 23 índices
explícitos, 7 triggers `updated_en` y 63 triggers de sync (3 por cada una de las 21
tablas sincronizadas; estos últimos, solo de `preparar_sync()`). En v2/v3 eran 33
tablas y 25 índices; en v1, 37 tablas, 28 índices y 66 triggers de sync. En `schema_v0.sql` había 30 tablas; las otras 7 las
crea la baseline.

---

## 2. Cómo leer las tablas

| Marca | Significado |
| --- | --- |
| **PK UUID** | `TEXT PRIMARY KEY NOT NULL DEFAULT <uuid v4 en SQL>`. Los repositorios generan el id en Python (`repositories/_ids.py`). El `DEFAULT` es la red de seguridad para `seed.sql` y `migration/`. |
| **ahora** | `NOT NULL DEFAULT CURRENT_TIMESTAMP` |
| **ISO** | `CHECK(col GLOB '????-??-??')`. Si la columna es nullable: `col IS NULL OR …`. |
| **mes** | `CHECK(col BETWEEN 1 AND 12)` |
| **→ tabla** | `REFERENCES tabla(id)`. Ninguna FK declara `ON DELETE` ni `ON UPDATE`. |
| **†** | Columna agregada por `schema_migrations.py` en la baseline. No estaba en `schema_v0.sql`; en `schema.sql` (foto v1) sí está. |
| **⇅** | Tabla sincronizada con Supabase: tiene `sincronizado_en` y los 3 triggers `trg_sync_<tabla>_{insert,update,delete}`. |

Las columnas aparecen en el orden que tienen en una base nueva. En una base vieja,
las columnas agregadas por `ALTER TABLE` pueden estar en otro orden físico.

---

## 3. Tablas por dominio

### 3.1 Núcleo

#### `monedas`

> **Desde v3** (`DATA_MODEL_DECISIONS.md` §39): la PK es `codigo` (ya no hay `id`), y en
> todo este documento cada `moneda_id` INTEGER → `monedas` debe leerse como
> `moneda_codigo` TEXT → `monedas(codigo)`, en la misma posición. Las tablas de abajo
> todavía muestran la forma de v2.

Catálogo de monedas. `decimales` define la escala de todos los montos `*_minor`
(ARS 2, BTC 8, CLP 0).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | Única tabla de dominio con id entero. |
| `codigo` | TEXT | NOT NULL UNIQUE | ARS, USD, USDT, USDC, BTC, CLP, BRL (seed) |
| `simbolo` | TEXT | | |
| `decimales` | INTEGER | NOT NULL DEFAULT 2 | |

No se sincroniza. Sale del seed, con los mismos ids en toda base.

#### `cuentas` ⇅

Dónde vive la plata: débito, tarjeta de crédito, efectivo, crypto o inversión.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `nombre` | TEXT | NOT NULL UNIQUE | |
| `tipo` | TEXT | NOT NULL CHECK IN (`debito`, `credito`, `efectivo`, `crypto`, `inversion`) | |
| `cuenta_pago_id` | TEXT | → `cuentas` | Autorreferencia: la cuenta con la que se paga esta. |
| `activa` | INTEGER | NOT NULL DEFAULT 1 | Archivado (borrado lógico) |
| `notas` | TEXT | | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Lo actualiza el trigger `trg_cuentas_updated` |
| `color_hex` † | TEXT | DEFAULT `'#5F5E5A'` | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

#### `cuentas_saldos` ⇅

Qué monedas opera cada cuenta y con qué saldo inicial. Hay una fila por cada par
(cuenta, moneda).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` | PK compuesta |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | PK compuesta |
| `saldo_inicial_minor` | INTEGER | NOT NULL DEFAULT 0 | El único saldo que se guarda (ver §5.3). |
| `visible` | INTEGER | NOT NULL DEFAULT 1 | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

PRIMARY KEY (`cuenta_id`, `moneda_id`). Su clave de sync es `'<cuenta_id>|<moneda_id>'`.

#### `categorias` ⇅

Catálogo de dos niveles (principal y subcategoría). Clasifica transacciones,
compras, presupuestos y gastos compartidos.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `categoria_principal` | TEXT | NOT NULL | |
| `subcategoria` | TEXT | NOT NULL | |
| `tipo` | TEXT | NOT NULL CHECK IN (`ingreso`, `egreso`, `movimiento`) | |
| `activa` † | INTEGER | NOT NULL DEFAULT 1 | Soft-delete (§11) |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

UNIQUE (`categoria_principal`, `subcategoria`).

#### `transacciones` ⇅

El Registro. Cada fila es un movimiento real de plata en una cuenta, y de acá salen
los saldos.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `fecha` | TEXT | NOT NULL ISO | |
| `concepto` | TEXT | NOT NULL | |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` | |
| `categoria_id` | TEXT | NOT NULL → `categorias` | |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `tipo_movimiento` | TEXT | NOT NULL CHECK IN (`ingreso`, `egreso`, `movimiento`) | Define el signo. |
| `monto_minor` | INTEGER | NOT NULL CHECK(`monto_minor >= 0`) | Siempre positivo. |
| `tag` | TEXT | | |
| `notas` | TEXT | | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Trigger `trg_transacciones_updated` |
| `deleted_at` | TEXT | | Soft-delete. NULL = viva. |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

Índices: `fecha`, `cuenta_id`, `categoria_id`, `moneda_id`, `tipo_movimiento`.

#### `autotransferencias`

> **Eliminada en v4** (`DATA_MODEL_DECISIONS.md` §40): las dos patas son transacciones
> independientes con `tag = 'autotransferencia'`. Queda documentada porque una base en
> v3 o anterior, o un backup, todavía la tiene.

Vinculaba las dos patas de una transferencia entre cuentas propias: el egreso en la
cuenta de origen y el ingreso en la de destino.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `transaccion_salida_id` | TEXT | NOT NULL → `transacciones` | |
| `transaccion_entrada_id` | TEXT | NOT NULL → `transacciones` | |
| `notas` | TEXT | | |
| `creada_en` | TEXT | ahora | |

UNIQUE (`transaccion_salida_id`, `transaccion_entrada_id`) y
CHECK(`salida != entrada`). Índices: las dos FK. **No se sincroniza** (ver §6.4).

### 3.2 Tarjeta de crédito

#### `compras_cuotas` ⇅

Una compra con tarjeta, en 1 o más cuotas. Si `es_cargo_extra = 1`, es un cargo del
resumen (impuesto, recargo o ajuste) cargado como compra de 1 cuota (§28).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `fecha_compra` | TEXT | NOT NULL ISO | |
| `concepto` | TEXT | NOT NULL | |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` | La tarjeta |
| `categoria_id` | TEXT | NOT NULL → `categorias` | |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `monto_total_minor` | INTEGER | NOT NULL | |
| `total_cuotas` | INTEGER | NOT NULL DEFAULT 1 | |
| `monto_por_cuota_minor` | INTEGER | NOT NULL | |
| `estado` | TEXT | NOT NULL DEFAULT `'activa'` CHECK IN (`activa`, `cancelada`, `completada`) | |
| `notas` | TEXT | | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Sin trigger |
| `monto_reintegro_minor` † | INTEGER | DEFAULT 0 | Gastos compartidos (§2) |
| `modo_deuda` † | TEXT | DEFAULT `'prorrateado'` CHECK IN (`total_unico`, `prorrateado`) | Gastos compartidos (§2) |
| `tag` † | TEXT | | §26 |
| `es_cargo_extra` † | INTEGER | NOT NULL DEFAULT 0 | §28 |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

#### `cuotas_credito` ⇅

Cada cuota de una compra: en qué mes cae, en qué resumen entró y en qué estado
está.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `compra_id` | TEXT | NOT NULL → `compras_cuotas` | |
| `resumen_id` | TEXT | → `resumenes_tarjeta` | NULL hasta que entra en un resumen. |
| `numero_cuota` | INTEGER | NOT NULL | |
| `mes_proyectado` | INTEGER | NOT NULL mes | |
| `anio_proyectado` | INTEGER | NOT NULL | |
| `mes_real_pago` | INTEGER | CHECK(BETWEEN 1 AND 12) | |
| `anio_real_pago` | INTEGER | | |
| `monto_cuota_minor` | INTEGER | NOT NULL | |
| `estado` | TEXT | NOT NULL DEFAULT `'pendiente'` CHECK IN (`pendiente`, `en_resumen`, `pagado`, `omitido`) | |
| `notas` | TEXT | | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

UNIQUE (`compra_id`, `numero_cuota`). Índices: `compra_id`, `resumen_id`, `estado`.
No tiene `creada_en` ni `updated_en`.

#### `resumenes_tarjeta` ⇅

Resumen mensual de una tarjeta. Guarda los totales consolidados al cerrar y el
pago.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` | |
| `mes` | INTEGER | NOT NULL mes | |
| `anio` | INTEGER | NOT NULL | |
| `monto_consumos_minor` | INTEGER | NOT NULL DEFAULT 0 | Se calcula al cerrar (§12). |
| `monto_impuestos_minor` | INTEGER | NOT NULL DEFAULT 0 | Se calcula al cerrar (§12, §28). |
| `porcentaje_impuesto_bp` | INTEGER | NOT NULL DEFAULT 0 | En basis points, derivado. |
| `monto_total_pagado_minor` | INTEGER | NOT NULL | |
| `fecha_pago` | TEXT | ISO nullable | |
| `estado` | TEXT | NOT NULL DEFAULT `'abierto'` CHECK IN (`abierto`, `cerrado`, `pagado`) | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Sin trigger |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

UNIQUE (`cuenta_id`, `mes`, `anio`). Índice: (`anio`, `mes`).

#### `tarjetas_config` ⇅ — creada por `MIGRACIONES_TABLA`

Días de cierre y de vencimiento por defecto de cada tarjeta. Es 1:1 con `cuentas`
(§23).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `cuenta_id` | TEXT | NOT NULL UNIQUE → `cuentas` | |
| `dia_cierre` | INTEGER | NOT NULL CHECK(BETWEEN 1 AND 31) | |
| `dia_vencimiento` | INTEGER | NOT NULL CHECK(BETWEEN 1 AND 31) | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Lo escribe el repositorio en el upsert. |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

#### `tarjetas_resumenes` ⇅ — creada por `MIGRACIONES_TABLA`

Fechas reales de cierre y vencimiento de un resumen puntual, cuando no coinciden
con las de `tarjetas_config` (§29).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` | |
| `mes` | INTEGER | NOT NULL mes | Mes del cierre calculado con el día por defecto. |
| `anio` | INTEGER | NOT NULL | |
| `fecha_cierre` | TEXT | ISO nullable | NULL = la calculada |
| `fecha_vence` | TEXT | ISO nullable | NULL = la calculada |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

UNIQUE (`cuenta_id`, `mes`, `anio`).

### 3.3 Deudas

#### `deudas` ⇅

Libro de movimientos de deudas informales con personas, separado en dos tabs. El
monto lleva signo (§22).

La estructura es la de `schema.sql`, idéntica a `DDL_DEUDAS_FINAL`. Una base vieja
llega a ella con `reestructurar_deudas()`.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `entidad_persona` | TEXT | NOT NULL | Normalizada en mayúsculas (`utils/personas.py`). |
| `concepto` | TEXT | | |
| `tab` | TEXT | NOT NULL CHECK IN (`me_deben`, `debo`) | |
| `monto_minor` | INTEGER | NOT NULL | **Con signo**: + la deuda crece, − es un pago. |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `fecha` | TEXT | NOT NULL ISO | |
| `notas` | TEXT | | |
| `origen_tipo` | TEXT | DEFAULT `'manual'` | Texto libre, sin CHECK. |
| `origen_id` | TEXT | | Referencia polimórfica, sin FK. |
| `sincronizado_en` | TEXT | DEFAULT NULL | Ya viene en `schema.sql`. |
| `creada_en` | TEXT | ahora | |
| `tag` † | TEXT | | `MIGRACIONES_COLUMNA_DEUDAS` |

El saldo de una persona en un tab es `SUM(monto_minor)`. No hay índices,
`updated_en` ni trigger (ver §6.3). Ya no existen `deuda_pagos`, `deudas_old*` ni
`vw_deudas_activas`.

#### `prestamos`

Préstamos formales (hipotecario, prendario, personal) con su tasa y su sistema de
amortización.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `entidad` | TEXT | NOT NULL | |
| `tipo` | TEXT | NOT NULL CHECK IN (`hipotecario`, `prendario`, `personal`, `otro`) | |
| `capital_original_minor` | INTEGER | NOT NULL | |
| `tasa_anual_bp` | INTEGER | NOT NULL | Basis points |
| `sistema_amortizacion` | TEXT | NOT NULL CHECK IN (`frances`, `aleman`) | |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `fecha_inicio` | TEXT | NOT NULL ISO | |
| `plazo_meses` | INTEGER | NOT NULL CHECK(`> 0`) | |
| `cuenta_debito_id` | TEXT | → `cuentas` | |
| `estado` | TEXT | NOT NULL DEFAULT `'activo'` CHECK IN (`activo`, `cancelado`, `finalizado`) | |
| `notas` | TEXT | | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Trigger `trg_prestamos_updated` |

Índice: `estado`.

#### `cuotas_prestamo`

Cronograma de cuotas de un préstamo (capital más interés). Lo calcula
`LoansService`; el schema solo guarda el resultado.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `prestamo_id` | TEXT | NOT NULL → `prestamos` | |
| `numero_cuota` | INTEGER | NOT NULL | |
| `mes` | INTEGER | NOT NULL mes | |
| `anio` | INTEGER | NOT NULL | |
| `monto_capital_minor` | INTEGER | NOT NULL | |
| `monto_interes_minor` | INTEGER | NOT NULL | |
| `monto_total_minor` | INTEGER | NOT NULL | |
| `estado` | TEXT | NOT NULL DEFAULT `'pendiente'` CHECK IN (`pendiente`, `pagado`) | |
| `fecha_pago` | TEXT | ISO nullable | |

UNIQUE (`prestamo_id`, `numero_cuota`). Índices: `prestamo_id`, (`anio`, `mes`),
`estado`.

### 3.4 Planificación

#### `presupuestos` ⇅ — estructura de `DDL_PRESUPUESTOS_FINAL`

Presupuesto mensual con dos tipos de ítem:

- **fijo**: concepto libre, con el real cargado a mano.
- **variable**: una categoría del Registro, con el real calculado. Hay un ítem
  especial `COMPARTIDOS` que tiene `categoria_id` NULL.

`schema.sql` todavía crea la estructura vieja. `reestructurar_presupuestos_ingresos()`
la reemplaza en el primer arranque, también en una base nueva.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `tipo` | TEXT | NOT NULL CHECK IN (`fijo`, `variable`) | |
| `concepto` | TEXT | | |
| `categoria_id` | TEXT | → `categorias` | NULL en los fijos y en COMPARTIDOS. |
| `monto_estimado_minor` | INTEGER | NOT NULL DEFAULT 0 | |
| `monto_real_minor` | INTEGER | NOT NULL DEFAULT 0 | En los fijos se carga a mano. En los variables el real se calcula de las transacciones. |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `mes` | INTEGER | NOT NULL mes | |
| `anio` | INTEGER | NOT NULL | |
| `es_recurrente` | INTEGER | NOT NULL DEFAULT 0 | |
| `sincronizado_en` | TEXT | DEFAULT NULL | |
| `creada_en` | TEXT | ahora | |

Sin UNIQUE: la regla de un variable por categoría y mes la controla el service. Ya
no existen `monto_ejecutado_minor`, `notas`, `formula_estimado` ni
`UNIQUE(categoria_id, mes, anio)`.

#### `ingresos_proyectados` ⇅ — estructura de `DDL_INGRESOS_FINAL`

Ingresos esperados por mes, con el estimado y lo cobrado. Pasa lo mismo que con
`presupuestos`: `schema.sql` crea la estructura vieja y la migración la reemplaza.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `concepto` | TEXT | NOT NULL | |
| `monto_estimado_minor` | INTEGER | NOT NULL DEFAULT 0 | |
| `monto_real_minor` | INTEGER | NOT NULL DEFAULT 0 | Lo cobrado. "Cobrado" = real > 0. |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `mes` | INTEGER | NOT NULL mes | |
| `anio` | INTEGER | NOT NULL | |
| `es_recurrente` | INTEGER | NOT NULL DEFAULT 0 | |
| `notas` | TEXT | | |
| `sincronizado_en` | TEXT | DEFAULT NULL | |
| `creada_en` | TEXT | ahora | |

Ya no existen `estado` ni `monto_percibido_minor`.

#### `empleos`

Empleos en relación de dependencia, con sus porcentajes de aportes.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `nombre_empresa` | TEXT | NOT NULL | |
| `puesto` | TEXT | | |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `porcentaje_jubilacion` | INTEGER | DEFAULT 1100 | Basis points (11,00 %) |
| `porcentaje_obra_social` | INTEGER | DEFAULT 300 | Basis points (3,00 %) |
| `porcentaje_gremio` | INTEGER | DEFAULT 0 | Basis points |
| `tope_copago_os_minor` | INTEGER | DEFAULT 0 | |
| `activa` | INTEGER | DEFAULT 1 | Sin NOT NULL |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Trigger `trg_empleos_updated` |

#### `recibos_sueldo`

Recibo de sueldo mensual de un empleo (bruto, descuentos y neto), con la
transacción del cobro.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `empleo_id` | TEXT | NOT NULL → `empleos` | |
| `mes` | INTEGER | NOT NULL | Sin CHECK de rango |
| `anio` | INTEGER | NOT NULL | |
| `sueldo_bruto_minor` | INTEGER | NOT NULL | |
| `desc_jubilacion_minor` | INTEGER | NOT NULL | |
| `desc_obra_social_minor` | INTEGER | NOT NULL | |
| `desc_copagos_os_minor` | INTEGER | DEFAULT 0 | |
| `desc_otros_minor` | INTEGER | DEFAULT 0 | |
| `monto_neto_final_minor` | INTEGER | NOT NULL | |
| `transaccion_id` | TEXT | → `transacciones` | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Sin trigger |

UNIQUE (`empleo_id`, `mes`, `anio`). Índice: (`anio`, `mes`).

#### `descuentos_programados`

Descuentos puntuales que se aplican en el recibo de un mes.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `concepto` | TEXT | NOT NULL | |
| `monto_minor` | INTEGER | NOT NULL | |
| `mes_aplicacion` | INTEGER | NOT NULL mes | |
| `anio_aplicacion` | INTEGER | NOT NULL | |
| `recibo_id` | TEXT | → `recibos_sueldo` | NULL hasta que se aplica. |
| `estado` | TEXT | NOT NULL DEFAULT `'pendiente'` CHECK IN (`pendiente`, `aplicado`, `cancelado`) | |
| `notas` | TEXT | | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Sin trigger |

Índice: (`anio_aplicacion`, `mes_aplicacion`).

### 3.5 Ahorro e inversiones

#### `brokers` ⇅

Dónde se opera un activo. `sembrar_brokers()` carga COCOS, BULL MARKET, IOL,
MERCADO PAGO, NACION y BALANZ al crear la base y en la baseline v0 → v1.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | Al sincronizar, el broker local adopta el id remoto por `nombre` (§24). |
| `nombre` | TEXT | NOT NULL UNIQUE | |
| `tipo` | TEXT | | |
| `activo` | INTEGER | NOT NULL DEFAULT 1 | Archivado |
| `creada_en` | TEXT | ahora | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

#### `activos_financieros` ⇅

Instrumentos de ahorro e inversión: acción, CEDEAR, FCI, plazo fijo, plazo flex,
cripto, u `otro` (el "efectivo reservado" que crea el Registro).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `nombre` | TEXT | NOT NULL | |
| `tipo` | TEXT | NOT NULL CHECK IN (`accion`, `fci`, `plazo_fijo`, `cripto`, `otro`, `cedear`, `plazo_flex`) | Una base vieja se amplía con `ampliar_tipos_ahorro()`. |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `activa` | INTEGER | NOT NULL DEFAULT 1 | Archivado |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Trigger `trg_activos_financieros_updated` |
| `cuenta_id` † | TEXT | → `cuentas` | §19 |
| `broker_id` † | TEXT | → `brokers` | |
| `comision_compra_minor` † | INTEGER | DEFAULT 0 | |
| `comision_venta_minor` † | INTEGER | DEFAULT 0 | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

#### `movimientos_activo` ⇅

Compras, ventas, aportes y rendimientos de un activo.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `activo_id` | TEXT | NOT NULL → `activos_financieros` | |
| `tipo` | TEXT | NOT NULL CHECK IN (`compra`, `venta`, `rendimiento`, `aporte`) | |
| `fecha` | TEXT | NOT NULL ISO | |
| `cantidad` | REAL | | Unidades del activo. No es plata. |
| `precio_unitario_minor` | INTEGER | | |
| `monto_total_minor` | INTEGER | NOT NULL | |
| `dolar_oficial_momento_minor` | INTEGER | | |
| `notas` | TEXT | | |
| `creada_en` | TEXT | ahora | |
| `transaccion_id` † | TEXT | → `transacciones` | §16 |
| `comision_minor` † | INTEGER | DEFAULT 0 | |
| `moneda_id` † | INTEGER | → `monedas` | Nullable por limitación de `ALTER TABLE`. El backfill copia la del activo (§33). |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

Índice: `activo_id`.

#### `objetivos_ahorro` ⇅

Metas de ahorro, con monto y fecha meta.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `nombre` | TEXT | NOT NULL | |
| `monto_meta_minor` | INTEGER | | No tiene moneda propia (ver §6.4). |
| `fecha_meta` | TEXT | ISO nullable | |
| `estado` | TEXT | NOT NULL DEFAULT `'activo'` CHECK IN (`activo`, `cumplido`, `cancelado`) | |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Trigger `trg_objetivos_ahorro_updated` |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

#### `asignaciones` ⇅

Qué parte de cada movimiento de activo va a cada objetivo. Es la tabla puente entre
`movimientos_activo` y `objetivos_ahorro` (§31).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `movimiento_id` | TEXT | NOT NULL → `movimientos_activo` | |
| `objetivo_id` | TEXT | NOT NULL → `objetivos_ahorro` | |
| `porcentaje` | REAL | NOT NULL CHECK(`> 0 AND <= 100`) | La regla "suma ≤ 100 por movimiento" la controla el service. |
| `monto_asignado_minor` | INTEGER | NOT NULL | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

UNIQUE (`movimiento_id`, `objetivo_id`). Índices: `movimiento_id`, `objetivo_id`.

### 3.6 Hogar (gastos compartidos)

#### `hogares` ⇅

Grupo de personas que comparten gastos. Se identifica por un código de invitación.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `codigo_invitacion` | TEXT | NOT NULL UNIQUE | |
| `nombre` | TEXT | | |
| `creada_en` | TEXT | ahora | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

#### `hogar_miembros` ⇅

Miembros de un hogar, identificados por nombre de usuario local, con su porcentaje
por defecto.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `hogar_id` | TEXT | NOT NULL → `hogares` | PK compuesta |
| `usuario_local` | TEXT | NOT NULL | PK compuesta. Es un string: no hay tabla de usuarios. |
| `porcentaje_default` | REAL | | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

PRIMARY KEY (`hogar_id`, `usuario_local`). Su clave de sync es
`'<hogar_id>|<usuario_local>'`. Índice: `hogar_id`.

#### `gastos_compartidos` ⇅

Un gasto que pagó un miembro y que otro debe en parte. Apunta a su origen: una
transacción, una compra en cuotas o una cuota.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `hogar_id` | TEXT | NOT NULL → `hogares` | |
| `pagador` | TEXT | NOT NULL | Es un string, no hay FK. |
| `origen_tipo` | TEXT | NOT NULL CHECK IN (`transaccion`, `compra_cuotas`, `cuota_credito`) | |
| `origen_id` | TEXT | NOT NULL | Referencia polimórfica, sin FK. |
| `categoria_id` | TEXT | NOT NULL → `categorias` | |
| `monto_base_minor` | INTEGER | NOT NULL | Ya con el reintegro descontado. |
| `coeficiente_deuda` | REAL | NOT NULL CHECK(`>= 0 AND <= 100`) | Es un porcentaje, pero REAL y no en bp. |
| `monto_adeudado_minor` | INTEGER | NOT NULL | El original, inmutable. **Puede ser negativo.** |
| `fecha` | TEXT | NOT NULL ISO | Si el origen es una cuota, es la fecha de esa cuota. |
| `descripcion` | TEXT | | |
| `estado` | TEXT | NOT NULL DEFAULT `'pendiente'` CHECK IN (`pendiente`, `saldado`) | |
| `sincronizado_en` | TEXT | | Ya viene en `schema.sql`, sin DEFAULT. |
| `creada_en` | TEXT | ahora | |
| `updated_en` | TEXT | ahora | Trigger `trg_gastos_compartidos_updated` |
| `monto_pendiente_minor` † | INTEGER | NOT NULL DEFAULT 0 | El backfill lo iguala a `monto_adeudado_minor`. Lo reducen los pagos (§20). |

Índices: `hogar_id`, (`origen_tipo`, `origen_id`), `estado`. No tiene moneda propia:
la toma del origen (§21).

#### `gasto_compartido_pagos` ⇅

Pagos parciales que reducen el pendiente de un gasto compartido (§20).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `gasto_compartido_id` | TEXT | NOT NULL → `gastos_compartidos` | |
| `transaccion_id` | TEXT | → `transacciones` | |
| `monto_aplicado_minor` | INTEGER | NOT NULL | |
| `tipo_pago` | TEXT | NOT NULL CHECK IN (`transaccion`, `compensacion`, `ajuste`) | |
| `notas` | TEXT | | |
| `fecha` | TEXT | NOT NULL ISO | |
| `creada_en` | TEXT | ahora | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

Índice: `gasto_compartido_id`.

### 3.7 Sync y caché

Ninguna de estas tablas se sincroniza: son locales. Las tres de snapshots conservan
un id entero (§25).

#### `sync_cambios` — creada por `preparar_sync()`

Cola de filas que cambiaron y todavía no se subieron a Supabase. La llenan los
triggers `trg_sync_*` (§24).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `tabla` | TEXT | NOT NULL | PK compuesta |
| `clave` | TEXT | NOT NULL | PK compuesta. Es la PK de la fila como texto. |
| `operacion` | TEXT | NOT NULL CHECK IN (`guardado`, `borrado`) | |
| `modificado_en` | TEXT | NOT NULL | UTC con milésimas. Es el reloj del last-write-wins. |

PRIMARY KEY (`tabla`, `clave`).

#### `sync_estado` — creada por `preparar_sync()`

Pares clave/valor que usa la sync para controlarse: la marca `escribiendo_sync`
(que apaga los triggers mientras la sync escribe) y las marcas `tabla_bajada:<tabla>`.
No figuraba en la lista del pedido, pero existe.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `clave` | TEXT | PRIMARY KEY | |
| `valor` | TEXT | | |

#### `saldos_mensuales` — creada por `MIGRACIONES_TABLA`

Caché del saldo de cada (cuenta, moneda) al cierre de cada mes. El mes en curso no
se guarda nunca (§21).

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `cuenta_id` | TEXT | NOT NULL → `cuentas` | |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `mes` | INTEGER | NOT NULL mes | |
| `anio` | INTEGER | NOT NULL | |
| `saldo_minor` | INTEGER | NOT NULL DEFAULT 0 | |
| `calculado_en` | TEXT | ahora | |

UNIQUE (`cuenta_id`, `moneda_id`, `mes`, `anio`). El upsert se hace con
`INSERT OR REPLACE`.

#### `deudas_mensuales` — creada por `MIGRACIONES_TABLA`

Caché del saldo de deudas por persona, tab y moneda al cierre de cada mes.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `entidad_persona` | TEXT | NOT NULL | Coincide con `deudas.entidad_persona` como texto. |
| `tab` | TEXT | NOT NULL CHECK IN (`me_deben`, `debo`) | |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `mes` | INTEGER | NOT NULL mes | |
| `anio` | INTEGER | NOT NULL | |
| `monto_minor` | INTEGER | NOT NULL DEFAULT 0 | |
| `calculado_en` | TEXT | ahora | |

UNIQUE (`entidad_persona`, `tab`, `moneda_id`, `mes`, `anio`).

#### `compartidos_mensuales` — creada por `MIGRACIONES_TABLA`

Caché del pendiente de gastos compartidos por hogar, pagador y moneda al cierre de
cada mes.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | |
| `hogar_id` | TEXT | NOT NULL → `hogares` | |
| `pagador` | TEXT | NOT NULL | |
| `moneda_id` | INTEGER | NOT NULL → `monedas` | |
| `mes` | INTEGER | NOT NULL mes | |
| `anio` | INTEGER | NOT NULL | |
| `monto_minor` | INTEGER | NOT NULL DEFAULT 0 | |
| `calculado_en` | TEXT | ahora | |

UNIQUE (`hogar_id`, `pagador`, `moneda_id`, `mes`, `anio`).

### 3.8 Sin uso (o deprecadas) — eliminadas en v2

> **Ya no existen** desde la migración v2 (`DATA_MODEL_DECISIONS.md` §38). Quedan
> documentadas porque una base en v0 o v1, o un backup, todavía las tiene.

#### `tipos_cambio`

Cotización entre dos monedas por fecha.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `fecha` | TEXT | NOT NULL ISO | |
| `moneda_origen_id` | INTEGER | NOT NULL → `monedas` | |
| `moneda_destino_id` | INTEGER | NOT NULL → `monedas` | |
| `tasa_minor` | INTEGER | NOT NULL | Escala ×10 000 (`obtener_tipo_cambio()` la divide por 10 000). |
| `fuente` | TEXT | | |

UNIQUE (`fecha`, `moneda_origen_id`, `moneda_destino_id`).

#### `indices_inflacion`

Índice de precios mensual, para llevar series a moneda constante.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `mes` | INTEGER | NOT NULL mes | |
| `anio` | INTEGER | NOT NULL | |
| `valor_indice` | REAL | NOT NULL | Es un número índice, no un porcentaje. |
| `fuente` | TEXT | | |
| `creada_en` | TEXT | ahora | |

UNIQUE (`mes`, `anio`). Índice: (`anio`, `mes`).

#### `resumen_cargos_extra` (deprecada, §28)

Cargos extra de un resumen. La reemplazó `compras_cuotas.es_cargo_extra`.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `resumen_id` | TEXT | NOT NULL → `resumenes_tarjeta` | |
| `concepto` | TEXT | NOT NULL | |
| `tipo` | TEXT | NOT NULL CHECK IN (`impuesto`, `recargo`, `ajuste`, `otro`) | |
| `monto_minor` | INTEGER | NOT NULL | Puede ser negativo (un ajuste a favor). |
| `creada_en` | TEXT | ahora | |
| `moneda_id` † | INTEGER | → `monedas` | |
| `fecha` † | TEXT | ISO nullable | |

Índice: `resumen_id`.

#### `activo_objetivos` ⇅ (deprecada, §31)

Reparto fijo de un activo entre objetivos. La reemplazaron las `asignaciones` por
movimiento.

| Columna | Tipo | Constraints | Notas |
| --- | --- | --- | --- |
| `id` | TEXT | PK UUID | |
| `activo_id` | TEXT | NOT NULL → `activos_financieros` | |
| `objetivo_id` | TEXT | NOT NULL → `objetivos_ahorro` | |
| `porcentaje` | REAL | NOT NULL CHECK(`> 0 AND <= 100`) | |
| `creada_en` | TEXT | ahora | |
| `sincronizado_en` † | TEXT | DEFAULT NULL | |

UNIQUE (`activo_id`, `objetivo_id`). Índice: `objetivo_id`.

### 3.9 Vistas y triggers

**Vistas** (`schema.sql`):

| Vista | Qué calcula | Quién la usa |
| --- | --- | --- |
| `vw_balance_cuentas` | Saldo por (cuenta, moneda) = `saldo_inicial_minor` + Σ transacciones no eliminadas. `ingreso` suma, `egreso` resta y `movimiento` suma. | `CuentasRepository.obtener_saldo()` |
| `vw_cuotas_pendientes` | Cuotas con `estado != 'pagado'` y el concepto de su compra. | Solo `verify/` |
| `vw_saldo_neto_hogar` | Σ `monto_pendiente_minor` de los gastos `pendiente`, por hogar. Se rehace con `DROP` + `CREATE` en cada arranque. | `GastosCompartidosRepository` |

**Triggers `updated_en`** (`schema.sql`): `AFTER UPDATE` pone `updated_en =
CURRENT_TIMESTAMP` en `cuentas`, `transacciones`, `empleos`, `activos_financieros`,
`objetivos_ahorro`, `gastos_compartidos` y `prestamos`. Las demás tablas que tienen
`updated_en` dependen de que el repositorio lo escriba.

**Triggers de sync** (`preparar_sync()`): `trg_sync_<tabla>_insert`, `_update` y
`_delete` en las 22 tablas ⇅. Escriben en `sync_cambios`, salvo cuando
`sync_estado` tiene la marca `escribiendo_sync`. Se borran y se vuelven a crear en
cada arranque.

---

## 4. Relaciones

### 4.1 Foreign keys declaradas

Ninguna FK declara `ON DELETE` ni `ON UPDATE`. Como la conexión corre con
`PRAGMA foreign_keys = ON`, borrar un padre que tiene hijos **falla**. El orden de
borrado lo maneja cada service: por ejemplo, `SavingsService` borra
`activo_objetivos` antes de borrar el objetivo.

```
monedas ◄── cuentas_saldos.moneda_id, transacciones.moneda_id,
            compras_cuotas.moneda_id, deudas.moneda_id, prestamos.moneda_id,
            presupuestos.moneda_id, ingresos_proyectados.moneda_id,
            empleos.moneda_id, activos_financieros.moneda_id,
            movimientos_activo.moneda_id, saldos_mensuales.moneda_id,
            deudas_mensuales.moneda_id, compartidos_mensuales.moneda_id,
            tipos_cambio.moneda_origen_id / moneda_destino_id,
            resumen_cargos_extra.moneda_id

cuentas ◄── cuentas.cuenta_pago_id            (autorreferencia)
        ◄── cuentas_saldos.cuenta_id
        ◄── transacciones.cuenta_id
        ◄── compras_cuotas.cuenta_id
        ◄── resumenes_tarjeta.cuenta_id
        ◄── tarjetas_config.cuenta_id         (UNIQUE → 1:1)
        ◄── tarjetas_resumenes.cuenta_id
        ◄── prestamos.cuenta_debito_id
        ◄── activos_financieros.cuenta_id
        ◄── saldos_mensuales.cuenta_id

categorias ◄── transacciones.categoria_id
           ◄── compras_cuotas.categoria_id
           ◄── presupuestos.categoria_id      (nullable)
           ◄── gastos_compartidos.categoria_id

transacciones ◄── autotransferencias.transaccion_salida_id
              ◄── autotransferencias.transaccion_entrada_id
              ◄── recibos_sueldo.transaccion_id
              ◄── movimientos_activo.transaccion_id
              ◄── gasto_compartido_pagos.transaccion_id

compras_cuotas    ◄── cuotas_credito.compra_id
resumenes_tarjeta ◄── cuotas_credito.resumen_id, resumen_cargos_extra.resumen_id

prestamos ◄── cuotas_prestamo.prestamo_id

empleos        ◄── recibos_sueldo.empleo_id
recibos_sueldo ◄── descuentos_programados.recibo_id

brokers             ◄── activos_financieros.broker_id
activos_financieros ◄── movimientos_activo.activo_id, activo_objetivos.activo_id
movimientos_activo  ◄── asignaciones.movimiento_id
objetivos_ahorro    ◄── asignaciones.objetivo_id, activo_objetivos.objetivo_id

hogares            ◄── hogar_miembros.hogar_id, gastos_compartidos.hogar_id,
                       compartidos_mensuales.hogar_id
gastos_compartidos ◄── gasto_compartido_pagos.gasto_compartido_id
```

### 4.2 Referencias sin FK

Estas referencias existen, pero el schema no las protege. La integridad depende de
los services.

| Referencia | Apunta a | Por qué no hay FK |
| --- | --- | --- |
| `gastos_compartidos.(origen_tipo, origen_id)` | `transacciones` / `compras_cuotas` / `cuotas_credito`, según `origen_tipo` | Polimórfica: una columna no puede referenciar tablas distintas. |
| `deudas.(origen_tipo, origen_id)` | La fila que originó el movimiento. `'manual'` por defecto; `'pago_migrado'` viene de la migración. | Polimórfica y de texto libre (sin CHECK). |
| `hogar_miembros.usuario_local`, `gastos_compartidos.pagador`, `compartidos_mensuales.pagador` | Un usuario | Todavía no hay tabla de usuarios (§2). |
| `deudas_mensuales.entidad_persona` | `deudas.entidad_persona` | Las personas son texto normalizado, no una entidad. |
| `sync_cambios.(tabla, clave)` | Cualquier fila de una tabla ⇅ | Control genérico de la sync. |
| `moneda_id` entre bases distintas | La misma moneda en otra computadora | Los ids coinciden solo porque todas las bases siembran las monedas en el mismo orden (`sync/referencias.py`). |

---

## 5. Decisiones de diseño

### 5.1 Por qué UUIDs

Todas las tablas de dominio usan `id TEXT` con un UUID v4 (§25). La razón es la
**sincronización**: una fila tiene que tener la misma identidad en cualquier base.
Hay varias bases que comparten datos: la de cada usuario, la base de desarrollo, la
de la app empaquetada y Supabase. Con `INTEGER AUTOINCREMENT`, cada base genera sus
propios 1, 2, 3… y dos filas distintas chocarían con el mismo id al subir. Los
gastos compartidos además tienen que llegar a la base de *otro* usuario y seguir
siendo la misma fila.

- **Dónde se genera:** en Python (`repositories/_ids.py`, `uuid.uuid4()`). Los
  repositorios lo pasan explícito en el `INSERT` y lo devuelven. El `DEFAULT` en SQL
  es solo una red de seguridad para `seed.sql` y `migration/`.
- **`NOT NULL` explícito:** en SQLite, una `PRIMARY KEY` que no es `INTEGER` acepta
  NULL si no se lo prohíbe.
- **Costo:** `cursor.lastrowid` ya no sirve como id, y ordenar por `id` da un orden
  al azar. Para ordenar por momento de alta se usa `rowid`.
- **Excepciones:**
  - `monedas` conserva su id entero porque es un dato de referencia. Que sea igual
    en todas las bases depende del orden del seed. `codigo` es UNIQUE, pero las FK
    usan el entero.
  - Las tablas locales que nunca viajan (`saldos_mensuales`, `deudas_mensuales`,
    `compartidos_mensuales`) conservan ids enteros. `sync_cambios` y `sync_estado`
    no tienen columna `id`.

### 5.2 Por qué montos en enteros

Toda la plata se guarda en **minor units** (`*_minor INTEGER`). La escala la da
`monedas.decimales`: en ARS, 1 peso = 100 minor; en BTC, 1 = 10⁸; en CLP no hay
decimales. La conversión pasa solo por `to_minor()` / `from_minor()`
(`db/database.py`).

- **Por qué:** un float no representa exacto la mayoría de los decimales
  (`0.1 + 0.2 != 0.3`). En una app que suma años de movimientos, eso deja saldos que
  no cierran por centavos y comparaciones de igualdad que fallan. La suma de enteros
  es exacta, y SQLite la hace sin perder precisión.
- **Signo:** depende de la tabla.
  - `transacciones.monto_minor` es siempre `>= 0` (con CHECK) y el sentido lo da
    `tipo_movimiento`.
  - En `deudas.monto_minor`, `gastos_compartidos.monto_adeudado_minor` y los ajustes
    de tarjeta, el signo es parte del dato.
- **Lo que queda en REAL a propósito:** `movimientos_activo.cantidad` (unidades de
  un activo, no plata) e `indices_inflacion.valor_indice` (un número índice).

### 5.3 Por qué saldos derivados

No hay una columna "saldo actual" en ninguna cuenta. Lo único que se guarda es
`cuentas_saldos.saldo_inicial_minor`. El saldo se **calcula**: saldo inicial + Σ
transacciones no eliminadas (`vw_balance_cuentas`). Las deudas funcionan igual: el
saldo de una persona es `SUM(deudas.monto_minor)` de su tab.

- **Una sola fuente de verdad.** Si se edita, borra o restaura una transacción, el
  saldo queda bien solo, sin un segundo número que actualizar en la misma
  transacción. Un saldo guardado se puede desfasar de sus movimientos sin que nadie
  lo note.
- **Sync sin conflictos de saldo.** La sync resuelve por fila con last-write-wins.
  Si dos computadoras cargan un movimiento cada una, las dos filas se suman en
  todas las bases. En cambio, un saldo guardado y editado en ambos lados haría que
  uno pisara al otro y se perdiera un movimiento.
- **Costo y mitigación:** sumar toda la historia en cada pantalla se vuelve caro.
  Por eso existen los **snapshots** de cierre de mes (`saldos_mensuales`,
  `deudas_mensuales`, `compartidos_mensuales`, §21). Son un caché recalculable, no
  un dato propio: se pueden borrar y regenerar en cualquier momento, y el mes en
  curso siempre se calcula en vivo.
- **Totales que sí se guardan** (no son saldos de cuenta, pero conviene saberlo):
  - `resumenes_tarjeta.monto_consumos_minor`, `monto_impuestos_minor` y
    `porcentaje_impuesto_bp` se consolidan al cerrar el resumen;
  - `gastos_compartidos.monto_pendiente_minor` lo bajan los pagos parciales;
  - `presupuestos.monto_real_minor` de los fijos se carga a mano;
  - los importes de `recibos_sueldo`.

### 5.4 Por qué borrado lógico

El modelo combina **tres** formas de borrar. No todo es borrado lógico:

| Forma | Dónde | Por qué |
| --- | --- | --- |
| `deleted_at` (soft-delete reversible) | Solo `transacciones` | Son la fuente de los saldos y otras tablas las referencian: `autotransferencias`, `recibos_sueldo`, `movimientos_activo`, `gasto_compartido_pagos` y `gastos_compartidos` (polimórfica). Borrarlas físico rompería FKs o cambiaría la historia en silencio. Con `deleted_at` la fila sale de los saldos (la vista filtra `deleted_at IS NULL`) y se puede restaurar (`TransaccionesRepository`). |
| `activa` / `activo` (archivado) | `cuentas`, `categorias`, `empleos`, `activos_financieros`, `brokers` | Son catálogos que la historia sigue referenciando. Una categoría con transacciones **nunca** se borra físico (CLAUDE.md §4, §11): se oculta de los listados y las filas viejas mantienen su referencia. |
| `DELETE` físico, protegido por el service | Todo lo demás | Rige la ventana de corrección temprana (CLAUDE.md §4, §9). Si la fila no tiene dependencias con estado propio, se borra directo. Si las tiene, se bloquea y la corrección va por un movimiento de ajuste. |

**Relación con la sync.** `docs/ARCHITECTURE.md` dice que el borrado lógico es
"necesario para sync". Hoy no es así: la sync también propaga los `DELETE`
físicos. El trigger `trg_sync_<tabla>_delete` anota `'borrado'` en `sync_cambios`,
y en Supabase la fila queda marcada `borrado`. Un soft-delete de `transacciones`
viaja como un `UPDATE` más (`'guardado'`, con `deleted_at` lleno).

### 5.5 Por qué basis points para tasas

Una tasa o porcentaje se guarda como entero en **basis points**: 1 bp = 0,01 %, así
que 10 000 = 100 %. Es el mismo argumento que para los montos (§5.2): con enteros,
una tasa se compara, se suma y se guarda sin errores de redondeo binario, y la
precisión (centésimas de punto porcentual) queda fija en el schema.

| Columna | Valor |
| --- | --- |
| `prestamos.tasa_anual_bp` | Tasa nominal anual |
| `resumenes_tarjeta.porcentaje_impuesto_bp` | Derivado al cerrar: `monto_impuestos_minor * 10000 // monto_consumos_minor`, con división entera y 0 si no hay consumos (§12). |
| `empleos.porcentaje_jubilacion` / `_obra_social` / `_gremio` | Basis points aunque el nombre no lleve `_bp`: 1100 = 11 %, 300 = 3 %. |
| `tipos_cambio.tasa_minor` | Misma escala ×10 000, pero es una cotización, no un porcentaje. |

**No se aplica en todo el modelo.** Los porcentajes de reparto son `REAL` de 0 a
100 (ver §6.4).

---

## 6. Problemas conocidos

### 6.1 Schema desactualizado: `schema.sql` ≠ base real

> **✅ Resuelto** con el versionado (`docs/DATA_MODEL_DECISIONS.md` §37): `schema.sql`
> es ahora la foto completa del schema v1. Lo que sigue describe `schema_v0.sql`, el
> archivo anterior. Queda como referencia de qué agrega la baseline. Excepción que
> sigue vigente: los triggers de sync no están en ningún `.sql` (punto 5).

Para entender el modelo, leer `schema.sql` solo no alcanza y además confunde. Las
diferencias concretas:

1. **Hay 7 tablas que no están en `schema.sql`:** `tarjetas_config`,
   `tarjetas_resumenes`, `saldos_mensuales`, `deudas_mensuales`,
   `compartidos_mensuales`, `sync_cambios` y `sync_estado`.
2. **Hay 2 tablas con estructura equivocada.** `presupuestos` e
   `ingresos_proyectados` se crean con la estructura vieja y se reconstruyen en el
   primer arranque, incluso en una base nueva. Las columnas que muestra
   `schema.sql` para ellas (`monto_ejecutado_minor`, `estado`,
   `monto_percibido_minor`, el UNIQUE de presupuestos) no existen en ninguna base
   real.
3. **Faltan columnas en tablas que sí están:**
   - 17 columnas reales: `compras_cuotas` ×4, `activos_financieros` ×4,
     `movimientos_activo` ×3, `resumen_cargos_extra` ×2, `cuentas.color_hex`,
     `categorias.activa`, `gastos_compartidos.monto_pendiente_minor` y
     `deudas.tag`.
   - `sincronizado_en` en 20 de las 22 tablas sincronizadas. Solo `deudas` y
     `gastos_compartidos` la traen en `schema.sql`.
4. **Una vista depende de una columna que `schema.sql` no declara.**
   `vw_saldo_neto_hogar` suma `gastos_compartidos.monto_pendiente_minor`, que agrega
   la migración.
5. **Los 66 triggers de sync no aparecen en ningún archivo SQL.** Se generan en
   Python (`_ddl_triggers_sync()`).
6. **Hay comentarios viejos en `schema.sql`:**
   - el encabezado dice `schema_v2.sql`;
   - se nombran como "fase futura" `SavingsService`, `LoansService`, el service de
     gastos compartidos y `sync/`, y todos ya existen;
   - el comentario de `gastos_compartidos.sincronizado_en` dice que "queda NULL
     hasta que exista sync/".

La causa de fondo es §6.3. Mientras no cambie el mecanismo de migraciones, este
documento es la referencia del schema real.

### 6.2 Tablas sin uso

> **✅ Resuelto** en la migración v2 (`DATA_MODEL_DECISIONS.md` §38): las cuatro se
> borraron, junto con el código de la tabla de abajo. Lo que sigue es el estado
> anterior a ese cambio.

Ninguna de las cuatro está del todo muerta. Por eso borrarlas no es solo un
`DROP TABLE`.

| Tabla | Estado real | ¿Sync? | Código que la toca |
| --- | --- | --- | --- |
| `tipos_cambio` | La app nunca la escribe. No tiene repositorio. | No | `DatabaseManager.obtener_tipo_cambio()` (`db/database.py`), sin ningún caller. |
| `indices_inflacion` | Ningún código la lee ni la escribe. | No | Solo `verify/schema/verify_schema_completo.py`. |
| `resumen_cargos_extra` | Deprecada (§28), pero **se sigue leyendo**. Quedan las filas que no se pudieron migrar (tipo `otro`, o sin categoría especial). | **No** | `ResumenesTarjetaRepository.marcar_cerrado()` suma esas filas en `monto_impuestos_minor` al cerrar un resumen. `migrar_cargos_extra_a_compras()` la consulta en cada arranque. Tiene repositorio propio. |
| `activo_objetivos` | Deprecada (§31). Nada la lee. Solo se borran sus filas cuando se elimina un activo o un objetivo (lo exige la FK). | **Sí** | `SavingsService`, `ActivoObjetivosRepository`, `TABLAS_SINCRONIZADAS` y `sync/sync_engine.py`. |

Dos consecuencias que vale la pena tener presentes:

- `resumen_cargos_extra` no se sincroniza. Las filas que quedan existen solo en la
  base local, así que **el mismo resumen cerrado en otra computadora puede dar otro
  `monto_impuestos_minor`**.
- `activo_objetivos` sigue teniendo triggers de sync y viajando a Supabase aunque
  nadie la use. Para retirarla hay que sacarla de `TABLAS_SINCRONIZADAS` y de la
  lista de `sync_engine.py` antes de borrar la tabla.

### 6.3 El problema de `PRAGMA user_version`

> **✅ Implementado** (v1 baseline, `docs/DATA_MODEL_DECISIONS.md` §37). Lo que sigue
> describe cómo funcionaba antes: hoy esa cadena corre una sola vez por base, como
> migración v0 → v1. `preparar_sync()` sigue corriendo en cada arranque.

**Estado actual:** `PRAGMA user_version` no se lee ni se escribe en ningún lado, así
que vale 0 en toda base. **La base no sabe en qué versión de schema está.** Cada
`inicializar()` lo deduce mirándola, y por eso rehace en cada arranque:

- reaplica `schema.sql` entero;
- pregunta `PRAGMA table_info` por cada una de las 40 migraciones de columna (17 +
  1 + 22);
- detecta qué estructura tiene una tabla mirando qué columnas tiene:
  - `deudas`: `tab`, `monto_original_minor` o `tipo`;
  - `presupuestos`: `tipo` o `categoria_id`;
  - `ingresos_proyectados`: `monto_real_minor` o `monto_percibido_minor`;
- busca el texto `'plazo_flex'` y `'aporte'` dentro del `CREATE TABLE` que guarda
  `sqlite_master`;
- consulta `resumen_cargos_extra` para ver si quedan cargos por migrar;
- borra y vuelve a crear los 66 triggers de sync.

**Consecuencias:**

1. **`schema.sql` no puede ser la foto actual.** Corre *antes* que las migraciones
   y contra cualquier base, por vieja que sea. Por eso no puede crear índices,
   vistas ni triggers sobre columnas que una base vieja todavía no tiene: esa es la
   razón por la que `deudas` no tiene índices. Esa restricción, sumada a la
   convención de que "toda columna nueva va solo a `schema_migrations.py`", es lo
   que produce §6.1.
2. **El orden depende de reglas escritas en comentarios.** Hay tres casos:
   - `deudas.tag` tiene que ir después de `reestructurar_deudas()`; si no, se
     pierde;
   - `presupuestos.formula_estimado` hubo que sacarla de la lista porque se volvía
     a agregar en cada arranque;
   - `preparar_sync()` tiene que ir último.
3. **Detectar la versión mirando la base es frágil.** Si aparece una estructura que
   ninguna regla reconoce, la app no arranca (`RuntimeError`). Y la ampliación de
   ahorros depende de comparar texto contra el SQL guardado en `sqlite_master`.
4. **Una migración de datos no deja marca estructural.**
   `migrar_cargos_extra_a_compras()` usa "quedan filas" como marca de pendiente.
   Si quedaron cargos que no se pueden mover, la consulta corre en cada arranque y
   el aviso "N cargo(s) extra sin categoría especial…" se imprime cada vez.
5. **No hay forma de saber en qué versión está una base.** Pasa con la base de
   desarrollo, la de la app empaquetada y los `*_backup_antes_*.db`. Para
   diagnosticar, hay que inspeccionar columnas a mano.
6. **El trabajo de arranque crece con cada migración**, porque ninguna se marca
   nunca como hecha.

**Dirección propuesta** (`docs/ARCHITECTURE.md` §6, no implementada):

- migraciones numeradas que corren solo si `user_version < N`, cada una en su
  transacción y subiendo `user_version` al terminar;
- un paso inicial de *baseline* para las bases existentes (las que tienen
  `user_version = 0`): corre una sola vez la cadena actual basada en introspección
  y deja estampada la versión;
- después de eso, `schema.sql` puede pasar a ser la foto real del schema para
  bases nuevas.

### 6.4 Otras observaciones

Aparecieron al cruzar los dos archivos con el código. Quedan anotadas acá; no se
corrigió nada.

- **Porcentajes en dos formatos.** Las tasas están en basis points (§5.5). En
  cambio, `gastos_compartidos.coeficiente_deuda`,
  `hogar_miembros.porcentaje_default`, `asignaciones.porcentaje` y
  `activo_objetivos.porcentaje` son `REAL` de 0 a 100. Además, los porcentajes de
  `empleos` están en bp pero el nombre no lleva el sufijo `_bp`.
- **`autotransferencias` no se sincroniza, pero `transacciones` sí.** En otra
  computadora, las dos patas de una transferencia llegan sin el vínculo que las une.
- **Hay montos sin moneda propia.** `objetivos_ahorro.monto_meta_minor` no dice en
  qué moneda está, y sin moneda no se sabe cuántos decimales tiene.
  `gastos_compartidos` toma la moneda de su origen; eso está documentado en §21.
- **Hay tablas con service pero sin pantalla y sin sync.** `empleos`,
  `recibos_sueldo`, `descuentos_programados`, `prestamos` y `cuotas_prestamo`
  tienen repositorio y service (`EmpleosService`, `LoansService`), pero nada de
  `ui/` ni de `sync/` los usa. Si se les arma una pantalla, van a tener que entrar
  en `TABLAS_SINCRONIZADAS`.
- **`docs/ARCHITECTURE.md` §4 no coincide con el código en dos puntos.**
  - "Borrado lógico con `deleted_at`" vale solo para `transacciones` (ver §5.4).
  - "Claves naturales para monedas" no es así: las FK usan el id entero y la
    coincidencia entre bases depende del orden del seed (ver §5.1).
