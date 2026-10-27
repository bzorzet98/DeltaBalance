# DeltaBalance — Architecture Doc

## 1. Contexto

App de escritorio de finanzas personales para dos usuarios (Bruno y Noelia). Reemplaza archivos Excel en Google Drive. El problema central que define la arquitectura: dos usuarios que comparten gastos, con datos financieros sensibles, que necesitan trabajar sin internet.

## 2. Stack y justificación

| Tecnología | Justificación |
| --- | --- |
| Python | Lenguaje del desarrollador. Sin curva de aprendizaje. |
| Flet | Multiplataforma real (Linux, Windows, Android futuro) con un solo código Python. Descartado Electron por requerir JavaScript. |
| SQLite | Base local, sin servidor, funciona offline. WAL mode para escrituras concurrentes (UI + sync en hilos separados). |
| Supabase | Auth + base de datos + RLS sin mantener servidor propio. Solo para sync y backup. |

## 3. Arquitectura — monolito local

DeltaBalance es un monolito local: todo corre en un solo proceso en la máquina del usuario. No hay API web propia. Supabase es el único sistema externo.

```
ui/ → services/ → repositories/ → DatabaseManager → SQLite
                                                         ↕
                                         sync/ → Supabase
```

Capas y responsabilidades:

- `ui/` — pantallas y componentes. No escribe SQL ni tiene lógica de negocio.
- `services/` — reglas de negocio. No escribe SQL directo.
- `repositories/` — acceso a datos. Único lugar donde vive el SQL.
- `db/DatabaseManager` — conexión, transacciones atómicas, migraciones.
- `sync/` — sincronización con Supabase en hilo separado.

## 4. Decisiones de diseño del modelo de datos

- **UUIDs v4** generados en Python para identificación global entre bases
- **Montos en enteros** en unidad menor (centavos) — evita errores de float
- **Tasas y porcentajes en basis points** — mismo principio que los montos
- **Saldos derivados**, nunca guardados — calculados desde transacciones
- **Borrado lógico** — hoy `deleted_at` aplica solo a `transacciones`; el resto usa `activa = 0`. Decisión tomada: unificar todo a `deleted_at` para consistencia y compatibilidad con sync.
- **WAL mode** — escrituras concurrentes seguras entre UI y sync
- **Snapshots mensuales** — caché de saldos para evitar sumar toda la historia
- **Claves de monedas:** hoy las FK usan id entero — la coincidencia entre bases depende del orden del seed. Decisión tomada: migrar a clave natural por código (ARS, USD, BTC).

## 5. Sincronización

Dos etapas:

### Etapa 1 — Sync de cada usuario con Supabase

- **Primera vez** (app nueva o reinstalación): baja TODO desde Supabase. Supabase gana todos los conflictos — es una restauración.
- **Arranques normales**: solo baja lo que cambió desde la última sync usando una marca de tiempo.
- **Conflictos**: last-write-wins por timestamp. Si empatan, gana el local.

### Etapa 2 — Sync de gastos compartidos entre usuarios

- Triggers en 22 tablas anotan cada cambio en `sync_cambios`.
- Al sincronizar, solo se sube lo que está en `sync_cambios` — no se sube todo.
- Los gastos compartidos van a `deltabalance_compartidos` en Supabase, con el UUID del hogar.
- Cada 5 minutos se sincronizan automáticamente las tablas compartidas.
- RLS en Supabase garantiza que cada usuario solo ve los datos de su hogar.

### Traducción de referencias

Cuando un gasto compartido llega a la base del otro usuario, las referencias (categoria\_id, etc.) apuntan a UUIDs que no existen en esa base. El sync traduce esas referencias antes de guardar la fila — mapea los UUIDs del origen a los equivalentes del destino.

**Regla de negocio:** solo el usuario que creó un gasto compartido puede editarlo o borrarlo.

## 6. Decisiones pendientes de revisión

- **Schema desactualizado:** `schema.sql` ≠ realidad. Migrar a `PRAGMA user_version`.
- **SQL directo en services** — mover a repositories.
- **Dashboard acoplado a nombres de categorías** — migrar a `tipo_especial`.
- **Dos APIs de ahorro en paralelo** — unificar.
- **`sync/` importa `ui.utils.prefs`** — dependencia invertida a corregir.
- **Soft delete no unificado:** algunas tablas usan `activa = 0`, otras `deleted_at`. **Decisión tomada:** migrar todo a `deleted_at` para consistencia y compatibilidad con sync.
- **FK de monedas usa id entero** — la coincidencia entre bases depende del orden del seed. **Decisión tomada:** migrar a clave natural por código (ARS, USD, BTC).
- **Tabla `autotransferencias` acoplada innecesariamente. Decisión tomada:** eliminar la tabla. La lógica pasa a ser un diálogo en el Registro que genera dos filas normales en `transacciones` cuando la categoría es AUTOTRANSFERENCIA.
- **Services sin pantalla ni sync** (`empleos`, `recibos_sueldo`, `descuentos_programados`, `prestamos`, `cuotas_prestamo`): repositorios y services existen pero las tablas no se sincronizan. **Decisión tomada:** documentar como versión futura. No se tocan hasta que se decida implementar esas funcionalidades.
- **Borrado lógico incompleto:** `deleted_at` aplica solo a `transacciones`. El resto del modelo usa `activa = 0`. Ver decisión de soft delete arriba.
- **`autotransferencias` no se sincroniza:** en otra computadora las dos patas de una transferencia llegan sin el vínculo que las une. Resuelto por la decisión de eliminar la tabla.