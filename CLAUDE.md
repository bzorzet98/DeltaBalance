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
- **Todo texto visible al usuario en MAYÚSCULAS.** Labels, headers de tabla, opciones de
  dropdown, títulos de sección, botones, tooltips — todo en mayúsculas. Aplicar
  `.upper()` o escribir directamente en mayúsculas en los strings de UI.

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

- **Sin dependencias con estado propio generado** → editar y borrar directo, sin fricción.
- **Con dependencias con estado propio generado** → bloquear el edit/delete directo.
  La corrección se hace con un movimiento de ajuste explícito, nunca reescribiendo en
  silencio un registro del que ya dependen otras filas con estado.
- **Categorías**: nunca DELETE físico si tienen transacciones asociadas. Usar
  soft-delete (columna `activa`).

## 5. Carpeta `verify/`

En vez de tests automatizados, cada script de `verify/` es un programa Python que:

1. Se puede correr a mano (`python verify/verify_x.py`) — vos no lo ejecutás, el usuario sí.
2. Arma un escenario realista usando el motor de datos real contra una DB temporal,
   nunca contra `data/deltabalance.db` directamente.
3. Imprime en consola, de forma legible, qué se esperaba vs. qué se obtuvo, con ✅/❌.
4. No usa `assert` silencioso ni pytest — es un script narrativo pensado para que una
   persona lo lea y entienda qué se está probando y por qué.

## 6. Migración de datos viejos

Existe un sistema anterior (planillas Excel) con años de registros. La migración en sí
es una fase futura — no la implementes salvo que se pida explícitamente. Ver
`docs/MIGRATION_NOTES.md` para más contexto.

## 7. Antes de terminar cualquier tarea

- Revisá que no dejaste código duplicado entre un service nuevo y uno existente.
- Si tocaste `schema.sql`, actualizá `docs/DATA_MODEL_DECISIONS.md` en la misma tarea.
- Si agregaste un service nuevo, crea (o dejá pedido explícitamente) su script en `verify/`.
- No toques `ui/` a menos que el prompt sea específicamente sobre UI.

## 8. Convenciones de layout en `ui/`

Toda pantalla en `ui/screens/` y componente en `ui/components/` debe:

- Definir TODOS sus valores de layout propios como constantes en MAYÚSCULAS al principio
  del archivo bajo `# --- Configuración de layout ---`.
- Importar y usar `LayoutTokens` y `TypographyTokens` de `ui/theme/tokens.py` para
  valores compartidos entre pantallas.
- **Nunca usar números mágicos** en medio del código de widgets.

### Tokens disponibles en `ui/theme/tokens.py`

```python
class TypographyTokens:
    PAGE_TITLE_SIZE / PAGE_TITLE_WEIGHT       # título de página
    SECTION_TITLE_SIZE / SECTION_TITLE_WEIGHT # título de sección/tarjeta
    TABLE_HEADER_SIZE / TABLE_HEADER_WEIGHT   # header de columna de tabla
    TABLE_CONTENT_SIZE / TABLE_CONTENT_WEIGHT / TABLE_CONTENT_WEIGHT_REGULAR
    METADATA_SIZE / LABEL_SIZE
    FILTER_SIZE                               # controles de barra de filtros

class LayoutTokens:
    ALTURA_FILA_TABLA = 36   # alto fijo de cada fila — lectura Y edición
    PADDING_CELDA = 4        # padding interno de celda en modo lectura
    CELDA_DENSE = True       # dense=True en todos los TextField/Dropdown inline
```

## 9. Campos de Monto

Todo campo que reciba un monto debe usar `ui/components/campo_monto.py` (`CampoMonto`),
nunca un `TextField` crudo con validación numérica manual. `CampoMonto` soporta
fórmulas con `=` (ej. `=950000+50000`) — esto debe estar disponible en **todos** los
campos de monto de la app sin excepción. Usar siempre `persistir_formula=True` cuando
el campo muestra un valor ya guardado que el usuario puede querer ajustar con una fórmula.

## 10. Patrón de celda editable inline

El patrón estándar de edición en tablas es **click para editar**, igual en todas las
pantallas. Una celda en modo lectura muestra texto plano; al hacer click se convierte en
un campo editable; al confirmar (Enter o blur) vuelve a modo lectura. La altura de la
celda NO cambia entre modos — usar `LayoutTokens.ALTURA_FILA_TABLA` en el contenedor.

```python
# Estructura estándar de celda editable
contenedor = ft.Container(
    width=ANCHO_COL_X,
    height=LayoutTokens.ALTURA_FILA_TABLA,
    padding=LayoutTokens.PADDING_CELDA,
)

def _mostrar() -> None:
    contenedor.content = ft.Container(
        content=_texto_celda(texto_actual),
        on_click=lambda e: _editar(),
        ink=True,
        padding=LayoutTokens.PADDING_CELDA,
    )
    page.update()

def _editar() -> None:
    campo = ft.TextField(dense=LayoutTokens.CELDA_DENSE, autofocus=True, ...)
    # o CampoMonto / CampoFiltrable según el tipo de dato
    contenedor.content = campo
    page.update()

_mostrar()
```

Para campos de monto, usar `CampoMonto` con `persistir_formula=True`.
Para campos de selección (banco, categoría), usar `CampoFiltrable`.
Para dropdowns simples (moneda), usar `ft.Dropdown(dense=True)`.

## 11. `sqlite3.Row` — nunca usar `.get()`

`sqlite3.Row` NO tiene método `.get()`. Para acceder con valor default usar:

```python
# MAL
valor = fila.get("campo", "default")

# BIEN
valor = fila["campo"] if "campo" in fila.keys() else "default"

# O convertir a dict primero si se va a acceder muchas veces
fila_dict = dict(fila)
valor = fila_dict.get("campo", "default")
```

Siempre que un service o repositorio devuelva `sqlite3.Row`, convertir a `dict` con
`dict(fila)` antes de pasarlo a la UI para evitar este error.

## 12. Persistencia local del usuario

`ft.SharedPreferences` **no persiste entre sesiones** en Flet 0.86.5 desktop. La
persistencia local del nombre de usuario y hogar se hace con un archivo JSON en
`Path.cwd() / ".deltabalance_prefs.json"` vía las funciones `obtener_usuario_local()`
y `guardar_usuario_local()` en `ui/components/usuario_local.py`. No usar
`page.client_storage` ni `ft.SharedPreferences` para ningún dato que deba sobrevivir
al reinicio de la app.