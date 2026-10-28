# DeltaBalance — PRD

Oct 22, 2026 · @Bruno Jose Zorzet

Este documento define qué entra en el MVP de DeltaBalance, qué queda afuera y por qué. Lo que queda fuera es tan importante como lo que entra.

## Criterio de éxito

El MVP está resuelto cuando Bruno y Noelia usan DeltaBalance como reemplazo completo de Excel para la gestión financiera mensual, sin necesidad de cruzar datos manualmente entre los dos.

En concreto:

- Los dos usuarios cargan sus movimientos en la app, no en Excel
- Los gastos compartidos se liquidan automáticamente, sin intercambio manual de información
- Al inicio de cada mes, ambos pueden ver con claridad cuánto tienen disponible y cuánto proyectan gastar

## Principio de diseño

**Menos restricciones, más responsabilidad del usuario.**

La app no intenta proteger al usuario de sí mismo. Prefiere darle control total y asumir que sabe lo que hace, antes que agregar validaciones y restricciones que complican el flujo sin agregar valor real. Cuando hay que elegir entre una validación que previene un error poco probable y una interfaz más simple, gana la simplicidad.

Este principio aplica a todas las decisiones de diseño del MVP: si una restricción no previene un problema concreto y frecuente, se elimina.

## Dentro del MVP

| Módulo | Estado | Notas |
| --- | --- | --- |
| Registro de transacciones | ✅ Completo | Ingreso, egreso, transferencia, cambio de moneda. Múltiples monedas y cuentas. |
| Compras en cuotas | ✅ Completo | Cronograma automático, cargos extra como cuota de una sola cuota. Se agrega una segunda tab a la pantalla: Tab 1 — listado de todas las compras y cuotas del registro. Tab 2 — doble columna con el total a pagar del mes (estimado) vs lo que el usuario registró como pagado (real). Esta segunda tab es la que alimenta la sección de cuotas del dashboard. |
| Gastos compartidos | ✅ Completo | Selección múltiple de filas con coeficiente uniforme. Originados desde registro o desde compras en cuotas. Solo el usuario que creó el gasto compartido puede editarlo o borrarlo. |
| Presupuestos y egresos fijos | ✅ Completo | Seguimiento en tiempo real de lo gastado vs lo presupuestado por categoría. |
| Ahorros e inversiones | ✅ Completo | Objetivos, instrumentos (FCI, CEDEARs, etc.), aportes y distribución proporcional de rendimientos. |
| Categorías y cuentas | ✅ Completo | Creación de categorías, cuentas de banco y tarjetas de crédito asociadas a un banco. |
| Autotransferencia | ✅ Completo | Menú desplegable para seleccionar cuenta destino — se mantiene. |
| Sincronización | ✅ Completo | Base de datos propia por usuario sincronizada en Supabase. Gastos compartidos visibles entre los dos usuarios. |
| Dashboard | ⚠️ Simplificar | Se replantea como tabla resumen por sección, cada una con su propia lógica: **Ingresos** — doble columna: Estimado (+) / Real (−) **Egresos fijos** — doble columna: Estimado (+) / Real (−) **Presupuestos** — doble columna: Estimado (+) / Real (−) **Cuotas** — doble columna: Estimado (total a pagar el mes) / Real (pagado por el usuario). Sin dependencia de categorías. **Deudas informales** — suma de filas con signo: + me deben, − debo. Sin doble columna. **Gastos compartidos** — saldo neto del hogar (TE DEBEN ya calculado). Sin doble columna. **Registro mensual** — sumatoria de todas las transacciones del mes con su signo original, filtradas por moneda. Sin exclusiones por categoría. AUTOTRANSFERENCIA suma cero (sirve como control). CAMBIO MONEDA aparece con el signo de cómo se registró — depende de la dirección de la operación, no de la moneda. **Fórmula del disponible:** Disponible =\
  + (ingresos.estimado + ingresos.real)\
  - (egresos\_fijos.estimado + egresos\_fijos.real)\
  - max(presupuestos.estimado + presupuestos.real, 0)\
  - (cuotas.estimado + cuotas.real)\
  + saldo\_neto\_deudas\_informales\
  + saldo\_neto\_gastos\_compartidos\
  + flujo\_registro\_mensual La excepción de presupuestos (`max(..., 0)`) evita castigar dos veces cuando el real supera el estimado — el exceso ya está en el registro mensual. **Ahorros:** no tienen sección propia en el dashboard. El usuario los gestiona como egreso fijo o categoría de presupuesto. El patrimonio neto queda para una versión futura. |
| Archivado de cuentas | ⚠️ Corregir | Debe permitirse siempre, independientemente del saldo. Decisión del usuario. |

## Fuera del MVP

| Qué | Decisión | Justificación |
| --- | --- | --- |
| Diálogo automático al seleccionar categorías de deuda (Cobro deuda, etc.) | ❌ Eliminar | El usuario anota el movimiento manualmente y va a Deudas informales a registrarlo. Más simple, menos acoplamiento. |
| Restricciones al crear o borrar categorías | ❌ Simplificar | Las restricciones actuales son excesivas. Una categoría puede ser ingreso, egreso o movimiento — el usuario elige sin validaciones forzadas. |
| Lógica de resúmenes de tarjeta (fechas de cierre y vencimiento) | ❌ Simplificar | La lógica de resumen quedó obsoleta. Solo se conservan las fechas de cierre y vencimiento como referencia visible, sin lógica de negocio asociada. |
| Pantalla de Estadísticas | ❌ Eliminar | Queda absorbida en el Dashboard simplificado. |
| Ocultamiento de cuentas con movimientos | ⚠️ Corregir comportamiento | Solo del selector de nuevas transacciones — el historial no se toca. Lo mismo aplica para categorías ocultas. |
| Soporte UVA/CER en préstamos | 🔜 Versión futura | No es una necesidad actual. |
| Precio de mercado de instrumentos de ahorro | 🔜 Versión futura | Por ahora se valúa a costo histórico. |
| Múltiples usuarios / hogares | 🔜 Ejercicio técnico | La app es para dos usuarios fijos. Se documentará qué requeriría el modelo para escalarlo, sin implementarlo. |
| Vinculación dinámica de hogares | ⚠️ Corregir | Al iniciar sesión en una instalación nueva, la app debe consultar a Supabase los hogares del usuario en lugar de asumirlos por UUID hardcodeado. Prerequisito para distribuir la app a nuevos usuarios. |
## Preguntas abiertas

| # | Pregunta | Respuesta |
| --- | --- | --- |
| P1 | ¿El Dashboard mantiene HOY / PROYECTADO o se unifica? | Se replantea: una tabla resumen por sección (ingresos, egresos fijos, presupuestos, cuotas, registro mensual). Los ingresos proyectados y los registrados no se suman — son filas separadas. |
| P2 | ¿Las fechas de cierre y vencimiento se muestran en alguna pantalla? | Sí, accesibles desde un botón dentro de la sección de compras en cuotas. Sin lógica de negocio asociada. |
| P3 | ¿Ocultar una cuenta la oculta del historial también? | No — solo del selector. Lo mismo aplica para categorías ocultas. |
