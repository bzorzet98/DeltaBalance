# DeltaBalance — Instrucciones para Claude Code

Este archivo define cómo trabajar en este repositorio. Leelo por completo antes de tocar
cualquier archivo. Si algo de un prompt puntual contradice este documento, este documento
gana, salvo que el usuario diga explícitamente lo contrario en ese prompt.

## 0. Reglas no negociables

1. **Nunca ejecutes código.** No corras `python`, `pytest`, `sqlite3`, ni ningún script,
   ni siquiera "para verificar que funciona". Solo creás y editás archivos. La ejecución
   la hace el usuario a mano, siempre.
2. **No generes tests con pytest/unittest.** El proyecto usa **scripts de verificación
   manual** en `verify/`, no un framework de testing. Ver sección 5.
3. **No borres ni sobrescribas `data/deltabalance.db`** ni ningún archivo bajo `data/`.
   Es la base de datos real del usuario.
4. **No inventes tablas, columnas o convenciones** que no estén en `db/schema.sql` o en
   `docs/DATA_MODEL_DECISIONS.md`. Si falta algo para completar la tarea, preguntá o
   dejá un `TODO` explícito en vez de asumir.
5. Cada tarea se hace en el alcance que pide el prompt. No "aproveches" para refactorizar
   código no relacionado en el mismo cambio.

## 1. Filosofía de arquitectura

- **Responsabilidad única por clase y por archivo.** Un repositorio = una entidad de la
  base de datos. Un servicio = un dominio de negocio. Nada de clases "manager" que tocan
  quince tablas (el proyecto ya tuvo ese problema en `db/database.py` — se está
  desarmando activamente, no repetir el patrón en código nuevo).
- **Alta cohesión, bajo acoplamiento.** Los repositorios no conocen reglas de negocio.
  Los servicios no escriben SQL directo — llaman a repositorios.
- **`db/connection.py`** es la única pieza que sabe abrir/cerrar la conexión SQLite,
  aplicar `schema.sql`/`seed.sql`, y manejar transacciones (`commit`/`rollback`). Nadie
  más abre una conexión a mano.
- **Plata siempre en minor units (enteros).** Nunca floats para montos. Seguir la
  convención ya establecida en el schema (`monto_minor`, `to_minor`/`from_minor`).
- **Nomenclatura de dominio en español**, siguiendo lo ya existente en `schema.sql`
  (`transacciones`, `cuentas`, `deudas`, etc.). Nombres de clases/métodos en el código
  Python pueden ser en inglés si ya es la convención del archivo que estás tocando (los
  services existentes usan inglés) — no mezclar los dos estilos dentro de un mismo
  archivo nuevo, elegir uno y ser consistente.
- **Excepciones propias por dominio**, siguiendo el patrón ya usado en
  `services/debts_service.py` y `services/fees_service.py` (una clase base de error del
  dominio + subclases específicas).

## 2. Qué es "motor de datos" vs. qué es "de aplicación"

Esta distinción importa porque el motor de datos lo van a consultar después scripts de
`lab/` (métrica de bienestar, análisis futuros) sin pasar por la UI.

- **Motor de datos** (`db/`, `repositories/`, `services/`): toda la lógica financiera y
  contable. Debe poder usarse desde un script de `lab/`, desde `verify/`, o desde la UI
  sin ningún cambio. No debe importar nada de `ui/`.
- **De aplicación** (`ui/`, `sync/`): estado de pantalla, tema visual, credenciales de
  Drive/Supabase, preferencias del usuario. Esto sí puede depender del motor de datos,
  nunca al revés.

Si no estás seguro de dónde va algo, preguntate: "¿un script de análisis en `lab/` lo
necesitaría?" — si sí, va en el motor de datos.

## 3. Patrón de repositorio

Cada archivo en `repositories/` expone una clase con métodos CRUD explícitos para **una
sola tabla o agregado estrechamente relacionado** (ej. `compras_cuotas` +
`cuotas_credito` pueden ir juntos en `compras_cuotas_repository.py` porque una nunca
existe sin la otra). Ejemplo de forma esperada:

```python
class TransaccionesRepository:
    def __init__(self, connection: DatabaseConnection):
        self._conn = connection

    def crear(self, ...) -> int: ...
    def obtener_por_id(self, id: int) -> Optional[sqlite3.Row]: ...
    def listar(self, filtros...) -> list[sqlite3.Row]: ...
    def actualizar(self, id: int, ...) -> None: ...
    def eliminar(self, id: int) -> None: ...
```

Sin lógica de negocio adentro: no valida reglas de dominio, no decide si algo "se puede"
editar — eso es trabajo del service.

## 4. Regla de edición/borrado (ventana de corrección temprana)

Las correcciones típicas son errores de tipeo o de cuenta mal vinculada, hechas a los
pocos días de la carga — no reescritura de historial arbitrario. Aplicar esta regla en
todos los servicios nuevos y al tocar los existentes:

- **Sin dependencias con estado propio generado** (ninguna `cuota_credito` vencida o
  pagada, ningún `gasto_compartido` sincronizado, ningún `resumen_tarjeta` cerrado) →
  editar y borrar directo, sin fricción.
- **Con dependencias con estado propio generado** → bloquear el edit/delete directo.
  La corrección se hace con un movimiento de ajuste explícito, nunca reescribiendo en
  silencio un registro del que ya dependen otras filas con estado.
- **Categorías**: nunca DELETE físico si tienen transacciones asociadas. Usar
  soft-delete (columna `activa`).

## 5. Carpeta `verify/`

En vez de tests automatizados, cada script de `verify/` es un programa Python que:

1. Se puede correr a mano (`python verify/verify_x.py`) — vos no lo ejecutás, el usuario sí.
2. Arma un escenario realista usando el motor de datos real (contra una copia de la DB
   o una DB temporal, nunca contra `data/deltabalance.db` directamente).
3. Imprime en consola, de forma legible, qué se esperaba vs. qué se obtuvo, con ✅/❌.
4. No usa `assert` silencioso ni pytest — es un script narrativo pensado para que una
   persona lo lea y entienda qué se está probando y por qué.

Un script de `verify/` por servicio o por funcionalidad significativa. Nombre:
`verify_<dominio>.py` (ej. `verify_gastos_compartidos.py`).

## 6. Migración de datos viejos

Existe un sistema anterior (planillas Excel) con años de registros. La migración en sí
es una fase futura — no la implementes salvo que se pida explícitamente. Sí tené en
cuenta, al diseñar cualquier tabla o servicio nuevo, que en algún momento un script en
`migration/` va a necesitar insertar datos históricos masivamente. Por eso:

- Todo alta debe poder hacerse también con fecha pasada (no asumir `fecha = hoy`).
- Las tablas que agreguemos deberían poder poblarse por lote sin depender de que el
  usuario haga clicks en una UI.
- Ver `docs/MIGRATION_NOTES.md` para más contexto de lo que se sabe del sistema viejo.

## 7. Antes de terminar cualquier tarea

- Revisá que no dejaste código duplicado entre un service nuevo y uno existente.
- Si tocaste `schema.sql`, actualizá `docs/DATA_MODEL_DECISIONS.md` en la misma tarea.
- Si agregaste un service nuevo, crea (o dejá pedido explícitamente) su script en
  `verify/`.
- No toques `ui/` a menos que el prompt sea específicamente sobre UI.

## 8. Convenciones de layout en `ui/`

Toda pantalla en `ui/screens/` (y cualquier componente en `ui/components/`) debe
definir TODOS sus valores de layout que no vengan de `theme/tokens.py` (anchos de
columna en píxeles, alturas fijas, cantidades por default, límites de paginación,
etc.) como constantes nombradas en MAYÚSCULAS al principio del archivo, agrupadas
bajo un comentario `# --- Configuración de layout ---` o similar. Nada de números
sueltos en medio del código de construcción de widgets. Esto es además de (no en
reemplazo de) usar los tokens de `theme/tokens.py` para colores/spacing/tipografía
genéricos — es específicamente para los valores particulares de esa pantalla.

Cualquier valor usado para decidir layout según plataforma o tamaño de pantalla
(breakpoints de `page.width`, anchos de sidebar expandida/colapsada, umbrales de
responsive, relaciones de aspecto) debe declararse como variable global al
principio del archivo donde se usa — mismo criterio ya vigente para números
mágicos en general, pero remarcado explícitamente para esto porque estos valores
van a necesitar ajustarse distinto entre web, Android y desktop, y tienen que ser
fáciles de encontrar y tocar sin buscar en medio del código.

## 9. Campos de Monto

Todo campo que reciba un monto en cualquier pantalla nueva o existente debe usar
`ui/components/campo_monto.py` (`CampoMonto`), nunca un `TextField` crudo con
validación numérica manual — así la calculadora de fórmulas queda disponible de
forma consistente en toda la app sin tener que pedirlo pantalla por pantalla.
