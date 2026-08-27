# Notas de la API de Flet 0.80+ (incluye 0.86.5, la versión instalada)

Este proyecto usa Flet 0.86.5. Esa versión pertenece al ciclo "1.0 Beta" (0.80.0 en
adelante), que introdujo cambios de ruptura deliberados respecto a versiones
anteriores. El conocimiento de Flet de un LLM puede estar basado en versiones previas
a este salto — este documento es la referencia autoritativa para no tener que adivinar.
Fuente: https://github.com/flet-dev/flet/issues/5238 (lista oficial de breaking changes).

## Cambios confirmados que aplican a este proyecto

- **Entry point**: `ft.app(target=main)` → `ft.run(main)`. Usar siempre `ft.run()`.
- **Alignment**: `ft.alignment.center` (minúscula, módulo) → `ft.Alignment.CENTER`
  (mayúscula, constante de clase). Mismo patrón para el resto de las constantes de
  alignment.
- **Animation**: `ft.animation.Animation(...)` → `ft.Animation(...)` directo.
- **Padding / Margin**: ahora requieren argumentos con nombre, no posicionales.
  Ej.: `ft.Padding(vertical=0, horizontal=10)`, no `ft.Padding.symmetric(0, 10)`.
- **Diálogos**: `page.open(dialog)` → `page.show_dialog(dialog)`. Para cerrar:
  `page.pop_dialog()`.
- **NavigationDrawer**: usar la propiedad `position`, no `page.drawer`/`page.end_drawer`.
- **Botones**: ya no tienen propiedad `text` — usar `content`.
- **NavigationRailDestination**: no tiene `label_content` — usar `label`.
- **SafeArea**: `.left/.top/.right/.bottom` → `.avoid_intrusions_left/_top/_right/_bottom`.
- **FilePicker**: ahora es un servicio, se agrega a `page.services`. Solo expone
  métodos async que devuelven el resultado directo — ya no existe `on_result`.
- **ImageFit**: renombrado a `BoxFit` en varios controles (confirmado por reportes de
  la comunidad, revisar si aplica al control específico que se esté usando).

## Gráficos: paquete separado

Confirmado (búsqueda + `dir(ft)` vacío para "chart"/"pie"): desde el ciclo 0.80+,
los controles de gráficos NO vienen en el paquete `flet` principal. Viven en un
paquete aparte, **`flet-charts`**, que hay que instalar explícitamente:

```
pip install flet-charts
```

y agregarlo a `environment.yml`/`requirements.txt` como dependencia — no es
opcional ni viene arrastrado por `flet`.

Controles que expone (import como `import flet_charts as fc` o el alias que se
prefiera, confirmar la forma de import real corriendo la app, no asumir):
`BarChart`, `CandlestickChart`, `LineChart`, `MatplotlibChart`, `PieChart`,
`PlotlyChart`, `RadarChart`, `ScatterChart`. Documentación:
https://flet-charts.docs.flet.dev/

Para el gasto por categoría del dashboard, el control relevante es `PieChart`.
Para futuras pantallas de Estadísticas (línea temporal de gastos/ingresos a lo
largo del tiempo), `LineChart` o `BarChart` son los candidatos.

## Autocompletado / filtro al escribir — Dropdown Y AutoComplete descartados para este caso de uso

**Veredicto (después de probar los dos controles nativos en la práctica, en
`ui/components/registro_transacciones.py` y `ui/screens/compras_cuotas.py`):
ninguno de los dos sirve para un campo de "muchas opciones + necesita
filtro" (Banco, Categoría) en esta versión de Flet. Se reemplazaron ambos
por un componente propio, `ui/components/campo_filtrable.py`
(`CampoFiltrable`) — TextField + lista de sugerencias propia, sin
Dropdown ni AutoComplete. No volver a intentar ninguno de los dos para
este caso de uso sin resolver primero los problemas de abajo.**

- `ft.Dropdown(enable_filter=True, editable=True)`: documentado por
  docs.flet.dev/controls/dropdown como el control para filtrar
  escribiendo (comparación case-insensitive contra `options` ya
  cargadas). Problemas reales confirmados en este proyecto:
  - ⚠️ Bug conocido (issue #5338 del repo de Flet): seleccionar una opción
    con teclado (flechas + Enter) puede no disparar `on_change` ni
    actualizar `.value`, aunque el texto visible sí cambie — la selección
    con click de mouse sí funciona bien.
  - ⚠️ El primer Tab hacia el campo enfoca el selector CERRADO, no un
    campo de texto listo para escribir — hace falta un segundo Tab/Enter
    para poder tipear. Confirmado en la práctica, no solo en teoría.
  - Confirmado además por lectura del código fuente real instalado
    (`flet/controls/material/dropdown.py`, Flet 0.86.5): `Dropdown` NO
    tiene `on_submit` (solo `TextField` lo tiene) — cualquier código que
    le asigne `dropdown.on_submit = ...` se ejecuta sin error pero nunca
    dispara nada; usar `on_select` para confirmar por teclado/click.

- `ft.AutoComplete`: documentado por docs.flet.dev/controls/autocomplete
  como control separado con `suggestions` (lista de
  `AutoCompleteSuggestion(key=, value=)`) y `on_change`/`on_select`, para
  texto libre con sugerencias. Problema real confirmado en este proyecto:
  - ⚠️ **No mostraba ninguna lista de sugerencias al escribir, en ninguna
    pantalla** — confirmado por el usuario corriendo la app, con la
    consola del navegador sin errores (no es una excepción atrapada). NO
    se investigó la causa real a fondo (se decidió reemplazar el control
    en vez de seguir depurando) — como hipótesis sin confirmar, podría
    ser que `suggestions` no llegue a poblarse del lado del cliente en
    esta versión, o que el popup se recorte por un contenedor padre con
    overflow/clip; ninguna de las dos se verificó.
  - Confirmado por lectura del código fuente real instalado
    (`flet/controls/material/auto_complete.py`, Flet 0.86.5) — no por
    documentación externa, que no refleja esto: en esta versión
    `AutoComplete` es un control muy desnudo. Sus únicos campos son
    `value`, `suggestions`, `suggestions_max_height`, `on_select`,
    `on_change`, más `width`/`height` (heredados de `LayoutControl`). NO
    tiene `label`, `hint_text`, `dense`, `text_size`, `text_style`,
    `border`, `focus()` NI `on_submit` — a diferencia de `Dropdown` y
    `TextField`, que sí heredan todo eso de `FormFieldControl`. Esto por
    sí solo ya lo dejaba sin decoración visual propia y sin poder
    participar de un encadenado de foco por Enter, aunque no explica por
    sí solo el problema de las sugerencias que no aparecen.

## `ft.Row`/`ft.Column` con `wrap=True` no soportan hijos `expand=True`

Confirmado por un bug real (reportado por el usuario corriendo `flet run --web`,
sin ningún error en la consola del navegador — el fallo es puramente del lado
de Flutter, nunca llega a la terminal de Python ni a la consola JS de forma
visible). `wrap=True` hace que Flet renderice ese Row/Column como un `Wrap` de
Flutter, y `Wrap` no soporta hijos flexibles (`Expanded`, que es lo que produce
`expand=True` en un control hijo). El síntoma es un rectángulo gris sólido,
exactamente del tamaño que hubiera ocupado el widget roto — es el
`ErrorWidget` default de Flutter en un build release, no un placeholder
puesto a propósito ni un color hardcodeado.

Regla: nunca combinar `expand=True` en un hijo con `wrap=True` en su
Row/Column contenedor. Si hace falta un "spacer" que empuje contenido a los
extremos de una fila, usar `alignment=ft.MainAxisAlignment.SPACE_BETWEEN` (sin
`wrap`) o sacar el `wrap=True` de esa fila en particular.

## Patrones nuevos sin confirmar (agregados en la tarea de "Compartir" del Registro)

- `page.client_storage.get(clave)` / `.set(clave, valor)`: usado en
  ui/components/compartir_gasto.py para recordar el nombre local del
  usuario en hogares compartidos (la app todavía no tiene auth real). No
  usado en ningún otro lugar del proyecto todavía, así que no hay
  precedente confirmado — el código lo envuelve en try/except por las
  dudas. Confirmar corriendo la app y sacar esta nota si anda.
- `ft.Container(on_hover=...)` con `e.data` normalizado como
  `str(e.data).lower() == "true"`: usado para mostrar el ícono "Compartir"
  solo al pasar el mouse por una fila del Registro. Tampoco tiene
  precedente en este proyecto. Confirmar corriendo la app.
- `ui/components/campo_filtrable.py` (`CampoFiltrable`, reemplazo de
  Dropdown/AutoComplete, ver sección de arriba): usa un `on_blur` ASYNC
  (`async def`) con `await asyncio.sleep(0.2)` adentro, para dar tiempo a
  que un click en una sugerencia (que también dispara blur en el
  TextField) se termine de procesar antes de esconder/validar — técnica
  de "delayed blur", no específica de Flet. Sin precedente en este
  proyecto: Flet soporta handlers async de forma general (no es un
  patrón nuevo del ciclo 0.80+), pero esta combinación puntual (blur
  async + sleep + esconder una lista de sugerencias construida a mano)
  no se había probado acá. Si al clickear una sugerencia el campo queda
  vacío/en error en vez de tomar la selección, es la primera señal de que
  este mecanismo necesita ajustarse (aumentar el delay, o revisar el
  orden real de los eventos blur/click en la versión instalada).

- **Arreglo de Tab en `CampoFiltrable` (reportado tras la ronda anterior):**
  al confirmar un valor por click en una sugerencia o por auto-selección al
  perder foco, Tab no avanzaba al siguiente campo de la fila. Causa más
  probable (no confirmable al 100% sin correr la app — la implementación
  real de `visible` vive en el lado Flutter/Dart compilado, no en el
  paquete Python instalado que se puede leer): `_contenedor_sugerencias`
  quedaba siempre montado en `self.control.controls` con `visible=False`
  en vez de sacarse del árbol — probablemente seguía siendo alcanzable por
  el recorrido de foco/tab de Flutter aunque no se viera, y Tab desde el
  TextField caía ahí en vez de saltar al siguiente control del Row del
  caller. Arreglo aplicado (no depende de adivinar la semántica real de
  `visible=False`): `_mostrar_sugerencias()`/`_ocultar_sugerencias()`
  ahora agregan/sacan `_contenedor_sugerencias` de
  `self.control.controls` directamente, así que cuando está oculto no
  existe en el árbol, sin ambigüedad. Además, `_on_click_sugerencia()`
  restaura el foco al TextField explícitamente después de una selección
  por click (el click deja el foco "parado" sobre el ítem recién sacado
  del árbol). **Pendiente de confirmar corriendo la app.** Si Tab sigue
  sin avanzar después de este cambio, el siguiente paso sería interceptar
  la tecla Tab a mano vía `page.on_keyboard_event()` en el caller y
  encadenar foco manualmente — no implementado preventivamente para no
  apilar un tercer mecanismo especulativo sin evidencia de que hiciera
  falta.

## Regla para trabajar en este proyecto

1. Si un cambio de API está en esta lista, aplicalo con confianza — no hace falta
   volver a dudar cada vez que aparece.
2. Si aparece un patrón de Flet que NO está en esta lista y no estás seguro de si
   cambió en 0.80+, no lo reescribas por conjetura. Dejalo como está y avisá en el
   resumen de la tarea que ese punto puntual necesita confirmarse corriendo la app —
   el usuario es quien ejecuta `flet run main.py` y puede confirmar contra la
   versión real instalada, vos no podés ejecutar nada.
3. Si el usuario reporta un error real de Flet al correr la app, ese mensaje de
   error es la fuente de verdad — vale más que cualquier suposición basada en
   entrenamiento previo. Aplicá el fix que el error indica y, si el patrón es
   reusable, agregalo a este documento para no tener que redescubrirlo.
4. No re-visites código que ya funciona (confirmado por el usuario corriendo la
   app) para "corregirlo" de nuevo salvo que una tarea nueva lo toque
   directamente — evitar el vaivén de reescribir lo mismo varias veces.