# Auditoría: `DatabaseManager.execute()` y el bug de `lastrowid or rowcount`

Auditoría posterior al fix puntual en `repositories/cuotas_credito_repository.py`
(Fase 2, COMPRAS_CUOTAS paso 1, corrección de
`marcar_estado_por_resumen()`/`marcar_estado_por_compra()`). Esta tarea es
solo diagnóstico — no se corrigió ningún código nuevo acá.

## El bug

`db/database.py::DatabaseManager.execute()`:

```python
def execute(self, sql: str, params: tuple = (), autocommit: bool = True) -> int:
    ...
    cur = self.conn.execute(sql, params)
    if autocommit:
        self.conn.commit()
    return cur.lastrowid or cur.rowcount
```

En el módulo `sqlite3` de Python, `cursor.lastrowid` **no** es `None`/`0`
después de un `UPDATE` o `DELETE` — retiene el valor de
`sqlite3_last_insert_rowid()`, que es el id de la última fila **insertada**
en toda la conexión (no de este cursor, no de este statement), un valor
obsoleto pero casi siempre truthy si alguna vez se insertó algo en la
conexión. Como consecuencia, `cur.lastrowid or cur.rowcount` **nunca** cae
al lado del `rowcount` para un UPDATE/DELETE: cualquier caller que lea el
valor de retorno de `execute()` para saber "cuántas filas afecté" recibe un
número falso (el id de otra fila insertada recientemente en cualquier
tabla), sin ningún error visible.

Esto **no afecta** al mismo patrón cuando el statement es un `INSERT` puro
(sin `ON CONFLICT ... DO UPDATE`): ahí `cur.lastrowid` es el comportamiento
correcto y esperado.

`INSERT ... ON CONFLICT ... DO UPDATE` (upsert) es un caso mixto: si no hay
conflicto, se comporta como INSERT (correcto); si hay conflicto, SQLite
ejecuta un UPDATE internamente y **no** inserta fila nueva — mismo bug que
un UPDATE directo.

## Metodología

Se buscó `self._db.execute(` / `self.execute(` en `services/`,
`repositories/`, `verify/` y `db/database.py` (no existe `dal/`, se borró en
Fase 0). Se excluyó `tests/` por instrucción ya establecida (no se usa como
referencia). Cada call site se clasificó en:

- **(a) CONFIRMADO AFECTADO**: el valor de retorno se usa para lógica real.
- **(b) RETORNADO/CAPTURADO PERO NO USADO**: el UPDATE/DELETE se ejecuta,
  pero nada lee el valor de retorno (ni siquiera se captura en una
  variable, en todos los casos encontrados).
- **(c) NO AFECTADO**: el statement es un INSERT puro.

Aparte, se relevaron (sin contarlos en las categorías de arriba, porque no
son `self._db.execute()`/`self.execute()`) los `conn.execute(...)` directos
sobre un `sqlite3.Connection` real en `services/fees_service.py` — esos
**no** pasan por el wrapper buggy, así que `cursor.rowcount` leído ahí
directamente sí es confiable. Se incluyen como referencia/contraste al
final.

## Conteo total

| Categoría | Call sites |
|---|---|
| **(a) CONFIRMADO AFECTADO** | **3** |
| **(b) RETORNADO/CAPTURADO PERO NO USADO** | **24** |
| **(c) NO AFECTADO (INSERT puro)** | **22** |
| Total `self._db.execute()`/`self.execute()` relevados | 49 |
| Ya corregido en tarea anterior (fuera de este conteo) | 2 |

## (a) CONFIRMADO AFECTADO

| Archivo | Línea | Método | Qué pasaría si el valor está mal |
|---|---|---|---|
| `db/database.py` | 439 | `DatabaseManager.eliminar_transaccion()` | `rowcount = self.execute(UPDATE transacciones SET deleted_at=...)`; `return rowcount > 0` (línea 442). Como `rowcount` es casi siempre un id truthy de otro INSERT reciente, el método reportaría `True` (borrado exitoso) **incluso para un `transaccion_id` inexistente o que no tocó ninguna fila**. **Sin callers activos hoy** (grep confirma cero usos fuera de su propia definición) — `TransactionService.delete()` ya migró a `TransaccionesRepository.eliminar()`, que no depende de este valor. Candidato a eliminar directamente (no arreglar) cuando se limpie `db/database.py`. |
| `db/database.py` | 820 | `DatabaseManager.upsert_presupuesto()` | `INSERT INTO presupuestos (...) ON CONFLICT(...) DO UPDATE SET ...; return self.execute(...)`. Si la fila ya existía (camino `DO UPDATE`), `lastrowid` no se actualiza — el método devolvería el id de la última fila insertada en cualquier otra tabla de la conexión, no el id real del presupuesto. Solo es correcto en el camino "fila nueva". **Sin callers activos hoy** (no existe ningún `PresupuestoService` todavía). |
| `db/database.py` | 973 | `DatabaseManager.registrar_tipo_cambio()` | Mismo patrón: `INSERT ... ON CONFLICT(fecha, moneda_origen_id, moneda_destino_id) DO UPDATE ...`. Re-registrar una cotización para una fecha/par que ya existe (caso de uso plausible: corregir una tasa mal cargada) devolvería un id falso. **Sin callers activos hoy.** |

Las tres están en `db/database.py` (la clase legacy deprecada) y ninguna
tiene callers activos en el repo hoy — el bug es real pero actualmente
dormido, no está produciendo comportamiento incorrecto observable en
ningún flujo que se ejecute hoy.

## (b) RETORNADO/CAPTURADO PERO NO USADO

En todos los casos encontrados, el UPDATE se ejecuta como sentencia suelta
(`self._db.execute(sql, params)` sin `x = ...`) — el valor de retorno buggy
existe pero nada lo lee.

| Archivo | Línea | Método |
|---|---|---|
| `repositories/cuentas_repository.py` | 83 | `CuentasRepository.actualizar()` — retorna `True` hardcodeado |
| `repositories/cuentas_repository.py` | 88 | `CuentasRepository.archivar()` — sin return |
| `repositories/categorias_repository.py` | 54 | `CategoriasRepository.desactivar()` — sin return |
| `repositories/categorias_repository.py` | 57 | `CategoriasRepository.activar()` — sin return |
| `repositories/transacciones_repository.py` | 243 | `TransaccionesRepository.actualizar()` — retorna `True` hardcodeado |
| `repositories/transacciones_repository.py` | 250 | `TransaccionesRepository.eliminar()` — sin return |
| `repositories/transacciones_repository.py` | 257 | `TransaccionesRepository.restaurar()` — sin return |
| `repositories/deudas_repository.py` | 248 | `DeudasRepository.actualizar()` — retorna `True` hardcodeado |
| `repositories/deudas_repository.py` | 335 | `DeudasRepository.write_off()` — sin return |
| `repositories/compras_cuotas_repository.py` | 246 | `ComprasCuotasRepository.actualizar()` (rama `conn=None`) — retorna `True` hardcodeado |
| `repositories/compras_cuotas_repository.py` | 276 | `ComprasCuotasRepository.cancelar()` (rama `conn=None`) — sin return |
| `repositories/cuotas_credito_repository.py` | 191 | `CuotasCreditoRepository.marcar_estado()` (rama `conn=None`) — sin return |
| `repositories/resumenes_tarjeta_repository.py` | 219 | `ResumenesTarjetaRepository.actualizar_totales()` (rama `conn=None`) — sin return |
| `repositories/resumenes_tarjeta_repository.py` | 230 | `ResumenesTarjetaRepository.marcar_cerrado()` — sin return |
| `repositories/resumenes_tarjeta_repository.py` | 253 | `ResumenesTarjetaRepository.marcar_pagado()` (rama `conn=None`) — sin return |
| `db/database.py` | 281 | `DatabaseManager.modificar_cuenta()` — retorna `True` hardcodeado. Sin callers activos. |
| `db/database.py` | 286 | `DatabaseManager.archivar_cuenta()` — sin return. Sin callers activos. |
| `db/database.py` | 431 | `DatabaseManager.modificar_transaccion()` — retorna `True` hardcodeado. Sin callers activos. |
| `db/database.py` | 598 | `DatabaseManager.marcar_cuota_en_resumen()` — sin return. Sin callers activos. |
| `db/database.py` | 680 | `DatabaseManager.pagar_resumen_tarjeta()` (UPDATE resumen) — método retorna `t_id` de un INSERT distinto, no depende de este valor. Sin callers activos. |
| `db/database.py` | 689 | `DatabaseManager.pagar_resumen_tarjeta()` (UPDATE cuotas en bloque) — mismo método. Sin callers activos. |
| `db/database.py` | 782 | `DatabaseManager.registrar_pago_deuda()` — sin return. Sin callers activos. |
| `db/database.py` | 949 | `DatabaseManager.aplicar_descuento_programado()` — sin return. Sin callers activos. |
| `services/fees_service.py` | 742 | `FeesService.close_statement()` — sin capturar; el método arma `data`/`message` a partir de la fila leída ANTES del UPDATE (`statement`), no del valor de retorno de `execute()` |

15 de estos 24 están en `repositories/` (código activo, en uso por los
services ya migrados); 8 en `db/database.py` (legacy, sin callers activos
confirmados); 1 en `services/fees_service.py` (activo, pero el valor no se
usa).

## (c) NO AFECTADO — INSERT puro

| Archivo | Línea | Método |
|---|---|---|
| `repositories/cuentas_repository.py` | 37 | `crear()` — INSERT cuentas, capturado y usado como id |
| `repositories/cuentas_repository.py` | 47 | `crear()` — INSERT OR IGNORE cuentas_saldos, no capturado |
| `repositories/categorias_repository.py` | 25 | `crear()` — INSERT categorias, capturado y retornado |
| `repositories/transacciones_repository.py` | 89 | `crear()` (rama sin `conn`) — INSERT transacciones, capturado y retornado |
| `repositories/deudas_repository.py` | 92 | `crear()` — INSERT deudas, capturado y retornado |
| `repositories/compras_cuotas_repository.py` | 105 | `crear()` (rama sin `conn`) — INSERT compras_cuotas, capturado y retornado |
| `repositories/cuotas_credito_repository.py` | 98 | `crear_lote()` (rama sin `conn`, dentro de un loop) — INSERT cuotas_credito, cada id capturado y agregado a la lista de retorno |
| `repositories/resumenes_tarjeta_repository.py` | 77 | `crear()` — INSERT resumenes_tarjeta, capturado y retornado |
| `db/database.py` | 243 | `crear_cuenta()` — INSERT cuentas, capturado y usado |
| `db/database.py` | 254 | `crear_cuenta()` — INSERT OR IGNORE cuentas_saldos, no capturado |
| `db/database.py` | 328 | `crear_categoria()` — INSERT categorias, capturado y retornado |
| `db/database.py` | 396 | `crear_transaccion()` — INSERT transacciones, capturado y retornado |
| `db/database.py` | 470 | `crear_autotransferencia()` — INSERT autotransferencias, no capturado |
| `db/database.py` | 518 | `crear_compra_cuotas()` — INSERT compras_cuotas, capturado y usado |
| `db/database.py` | 537 | `crear_compra_cuotas()` (loop) — INSERT cuotas_credito, no capturado |
| `db/database.py` | 631 | `crear_resumen_tarjeta()` — INSERT resumenes_tarjeta, capturado y retornado |
| `db/database.py` | 736 | `crear_deuda()` — INSERT deudas, capturado y retornado |
| `db/database.py` | 771 | `registrar_pago_deuda()` — INSERT deuda_pagos, no capturado |
| `db/database.py` | 840 | `copiar_presupuesto()` (loop) — INSERT OR IGNORE presupuestos, no capturado |
| `db/database.py` | 894 | `crear_recibo_sueldo()` — INSERT recibos_sueldo, capturado y retornado |
| `db/database.py` | 929 | `crear_descuento_programado()` — INSERT descuentos_programados, capturado y retornado |
| `services/fees_service.py` | 598 | `open_statement()` — INSERT resumenes_tarjeta, capturado y retornado como `stmt_id` |

## Ya corregido (no cuenta como pendiente)

| Archivo | Método | Estado |
|---|---|---|
| `repositories/cuotas_credito_repository.py` | `marcar_estado_por_resumen()` (rama `conn=None`) | Corregido en la tarea anterior: usa `self._db.conn.execute(...)` directo + `cur.rowcount`, ya no pasa por `self._db.execute()`. |
| `repositories/cuotas_credito_repository.py` | `marcar_estado_por_compra()` (rama `conn=None`) | Ídem. |

## Referencia: `conn.execute()` directos en `services/fees_service.py`

No pasan por `DatabaseManager.execute()`, así que no heredan el bug. Se
listan porque el patrón usado en `cancel_purchase()` es exactamente el
correcto (leer `cursor.rowcount` directo sobre un cursor real) — el mismo
que se aplicó para corregir `marcar_estado_por_resumen()`/
`marcar_estado_por_compra()`.

| Línea | Método | Nota |
|---|---|---|
| 311 | `create_purchase()` | INSERT compras_cuotas vía `conn.execute(...).lastrowid` — cursor real, correcto |
| 331 | `create_purchase()` (loop) | INSERT cuotas_credito, no capturado |
| 677 | `confirm_fee()` | UPDATE cuotas_credito, no capturado |
| 688 | `confirm_fee()` | UPDATE resumenes_tarjeta, no capturado |
| 789 | `pay_statement()` | UPDATE cuotas_credito en bloque, no capturado |
| 799 | `pay_statement()` | UPDATE resumenes_tarjeta, no capturado |
| 913 | `cancel_purchase()` | **UPDATE cuotas_credito — `cur.rowcount` SÍ se captura y se usa** (`fees_cancelled = cur.rowcount`, aparece en el mensaje al usuario). Correcto porque es un cursor real, no el wrapper. |
| 923 | `cancel_purchase()` | UPDATE compras_cuotas, no capturado |

## Conclusión final

Los 3 call sites de categoría (a) — `eliminar_transaccion()`,
`upsert_presupuesto()`, `registrar_tipo_cambio()` — están todos en código
muerto: viven únicamente en `DatabaseManager` (`db/database.py`), la clase
legacy que se está desarmando, y ninguno tiene un solo caller activo en el
repo hoy (confirmado por grep). El bug es real pero está dormido — no
produce ningún resultado incorrecto observable en un flujo que se ejecute
actualmente.

Los servicios ya migrados a repositorio — `TransactionService` y
`DebtsService` — **no están afectados**. Ninguno de los dos lee el valor de
retorno de `self._db.execute()` para determinar éxito de un UPDATE/DELETE:
`TransaccionesRepository.eliminar()`/`actualizar()` y
`DeudasRepository.write_off()`/`actualizar()`/`registrar_pago()` caen todos
en categoría (b) (retornan `True` hardcodeado o no retornan nada,
independientemente de lo que `execute()` haya devuelto).

El único caso real donde este bug había producido un resultado incorrecto
en código vivo fue `marcar_estado_por_resumen()`/`marcar_estado_por_compra()`
en `repositories/cuotas_credito_repository.py` — ya corregido en la tarea
anterior (ver sección "Ya corregido" arriba), bypasseando el wrapper y
leyendo `cur.rowcount` directo sobre un cursor real.

**Nota para el futuro**: cuando se elimine `db/database.py` al cerrar la
Fase 2 (una vez que todas las tablas que cubre tengan su repositorio y
service equivalentes), estos tres métodos se eliminan junto con el resto de
la clase — no hace falta portarlos a ningún repositorio nuevo, porque no
tienen callers que dependan de su comportamiento actual (correcto ni
incorrecto).

## Lección para repositorios futuros

Ningún método nuevo en `repositories/` debe usar el valor de retorno de
`self._db.execute()` como conteo de filas afectadas en un UPDATE o DELETE
— ese wrapper devuelve `cur.lastrowid or cur.rowcount`, y `lastrowid` no cae
a `rowcount` para UPDATE/DELETE (ver explicación al principio de este
documento). El patrón correcto, ya aplicado en
`CuotasCreditoRepository.marcar_estado_por_resumen()`/
`marcar_estado_por_compra()`, es ejecutar contra `self._db.conn` (o el
`conn` externo recibido) y leer `cur.rowcount` directo:

```python
if conn is not None:
    return conn.execute(sql, params).rowcount
cur = self._db.conn.execute(sql, params)
self._db.conn.commit()
return cur.rowcount
```
