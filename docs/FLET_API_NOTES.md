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

## Autocompletado / filtro al escribir

Confirmado por documentación oficial (docs.flet.dev/controls/dropdown,
docs.flet.dev/controls/autocomplete):

- `ft.Dropdown(enable_filter=True, editable=True)`: dropdown que se filtra
  escribiendo, comparación case-insensitive contra el texto de las opciones ya
  cargadas. Es el control correcto para campos que deben referenciar una fila
  existente (categoría, cuenta) pero se quieren escribir en vez de solo
  scrollear una lista.
  ⚠️ Bug conocido (issue #5338 del repo de Flet): seleccionar una opción con
  teclado (flechas + Enter) puede no disparar `on_change` ni actualizar
  `.value`, aunque el texto visible sí cambie — la selección con click de mouse
  sí funciona bien. Si se necesita que Enter confirme la selección de forma
  confiable, probarlo primero; si falla, manejar la confirmación por otro
  evento en vez de asumir que `on_change` se disparó.
- `ft.AutoComplete`: control separado con `suggestions` (lista de
  `AutoCompleteSuggestion(key=, value=)`), `on_change`/`on_select`. Para texto
  libre con sugerencias, no para forzar una selección de una lista cerrada.

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