# DeltaBalance — Flujo de Pantallas

## Estructura general

La app tiene un sidebar fijo con 14 elementos. El sidebar se puede colapsar con la flecha al pie.

**Orden del sidebar (de arriba a abajo):**

1. Dashboard
2. Registro de Transacciones
3. Compras y Movimientos en Cuotas
4. Gastos Compartidos del Hogar
5. Deudas
6. Ingresos
7. Presupuestos
8. Ahorros e Inversiones
9. Estadísticas *(a eliminar — PRD)*
10. Cuentas
11. Categorías
12. Sincronización
13. Cuenta
14. Flecha para colapsar sidebar

---

## Pantalla 0 — Login

**Cuándo aparece:** solo si no hay sesión guardada (primera vez o logout manual).

**Elementos:**

- Campo email
- Campo contraseña
- Botón iniciar sesión
- Opción “trabajo sin conexión”

**Flujo:** sesión guardada → salta directo al Dashboard.

---

## Pantalla 1 — Dashboard

**Propósito:** ver el disponible del mes y navegar entre meses.

**Elementos actuales:**

- Selector de moneda (ARS / USD)
- Navegador de mes
- Header con DISPONIBLE HOY / PROYECTADO
- Casilla de ajuste manual (simular egresos)
- Checkboxes por fila para incluir/excluir del cálculo
- Secciones: Ingresos, Egresos fijos, Presupuestos, Cuotas, Deudas, Compartidos, Registro mensual

**Problemas identificados:**

- Labels redundantes (círculo de moneda duplicado)
- Demasiado detalle visible por defecto
- Los checkboxes solo son útiles para deudas (fecha de cobro incierta)

**Estado según PRD:** ⚠️ Simplificar. Ver fórmula del disponible en PRD.

**Dirección de mejora:**

- Tablas colapsables por sección — el usuario ve los totales y despliega el detalle si quiere
- Mantener checkboxes solo para la sección de Deudas
- Eliminar labels redundantes

---

## Pantalla 2 — Registro de Transacciones

**Propósito:** cargar y consultar todos los movimientos de cuenta.

**Elementos:**

- Buscador
- Navegador de rango de fechas
- Header con saldo por cuenta (selector de moneda)
- Botón de refresh para actualizar datos históricos
- Tabla con columnas: Concepto, Banco, Categoría, Tag, Monto, Fecha, Moneda
- Fila de alta inline (primera fila editable)
- Selección individual o masiva de filas
- Barra de acción al seleccionar: Eliminar, Compartir, Suma total de selección
- Íconos por fila: gasto compartido del hogar 🏠, deuda 🤝

**Diálogo de deuda (al apretar 🤝):**

- Campo persona (buscador de nombres existentes o nuevo)
- Tipo: ME DEBEN / DEBO
- Monto / Porcentaje / Coeficiente (los tres disponibles)
- Botón REGISTRAR
- **Problema:** diálogo demasiado largo para los pocos campos que tiene

**Diálogo de compartido (al apretar 🏠):**

- **Problema:** mismo problema de altura excesiva

**Mejoras identificadas:**

- Agregar ícono de eliminar directo por fila
- Diálogo de deuda: reducir altura, agregar buscador de personas existentes
- Diálogo de compartido: reducir altura

---

## Pantalla 3 — Compras y Movimientos en Cuotas

**Propósito:** registrar compras en cuotas y ver el total a pagar por tarjeta en el mes.

**Elementos:**

- Buscador
- Navegador de rango de fechas
- Header con total a pagar por tarjeta (desglosado por tarjeta)
- Selector de moneda
- Botón de ocultamiento de cargos extra *(a eliminar — PRD: los cargos extra se ingresan como movimientos normales)*
- Ruedita de configuración → abre diálogo de fechas de cierre y vencimiento por tarjeta
- Tabla con columnas: Concepto, Banco, Categoría, Tag, Monto, Cuotas, 1ª Cuota, Fecha, Moneda
- Fila de alta inline

**Diálogo de configuración de tarjetas:**

- Muestra cierre y vencimiento anterior, actual y próximo por tarjeta
- Es solo informativo — el usuario anota manualmente las fechas
- **Problema actual:** interfaz críptica
- **Dirección de mejora:** simplificar, dejar solo las fechas del próximo resumen a pagar
- A futuro: alerta X días antes del vencimiento

**Mejoras según PRD:**

- Agregar segunda tab: Tab 1 — listado de compras (actual). Tab 2 — doble columna real / pagado para anotar lo pagado por tarjeta (alimenta el dashboard)

---

## Pantalla 4 — Gastos Compartidos del Hogar

**Propósito:** ver y registrar gastos compartidos entre los miembros del hogar.

**Elementos:**

- Selector de hogar (dropdown) — hoy aparecen dos hogares ZORZET-RIVERA, uno en desuso
- Ruedita de configuración del hogar
- Header: TE DEBEN / DEBÉS con saldo neto en ARS
- Selector de moneda
- Botón de refresh
- Tabla con columnas: Concepto, Pagador, Categoría, Monto Base, Coeficiente, Adeudado, Moneda, Fecha, Estado
- Fila de alta: el concepto se elige desde un movimiento de transacciones existente

**Problemas identificados:**

- La columna Estado es inútil — no se saldan gastos individuales sino totales
- El flujo de vinculación de usuarios al hogar no funciona correctamente — hoy está hardcodeado
- Hay dos hogares en Supabase, el duplicado solo se puede eliminar desde Supabase directamente
- El alta solo permite elegir un movimiento existente — no permite ajustes manuales

**Mejoras identificadas:**

- Eliminar columna Estado
- Permitir alta manual además de desde movimiento existente (para ajustes)
- Corregir el flujo de vinculación de usuarios a hogares (versión futura)

---

## Pantalla 5 — Deudas

**Propósito:** registrar deudas informales entre personas (ME DEBEN / DEBO).

**Elementos:**

- Tabs: ME DEBEN / DEBO
- Header: SALDO NETO POR PERSONA
- Selector de moneda
- Botón de refresh
- Tabla con columnas: Persona, Concepto, Tag, Monto, Moneda, Fecha
- Fila de alta inline

**Problema identificado:**

- El sistema de tabs (ME DEBEN / DEBO) es diferente al de otras pantallas — conviene unificar el patrón de tabs en toda la app

---

## Pantalla 6 — Ingresos

**Propósito:** registrar ingresos proyectados con doble columna estimado/real.

**Elementos:**

- Buscador
- Navegador de rango de fechas
- Selector de moneda (ARS / USD)
- Botón copiar del mes anterior (copia los ingresos recurrentes)
- Tabla con columnas: Concepto, Estimado, Real, Moneda, Recurrente
- Fila de alta inline
- Fila de TOTAL al pie

**Problemas identificados:**

- La columna Real no acepta valores negativos — debería permitirlos para restar
- El nombre "Real" no es el más adecuado — la idea es anotar lo que efectivamente se cobró, podría llamarse "Cobrado" o "Ejecutado"

---

## Pantalla 7 — Presupuestos

**Propósito:** definir presupuestos por categoría con doble columna estimado/real automático.

**Elementos:**

- Buscador
- Navegador de rango de fechas
- Selector de moneda
- Botón copiar del mes anterior (copia los egresos recurrentes)
- Dos secciones: FIJOS y VARIABLES
- FIJOS: Concepto, Estimado, Real, Moneda, Recurrente
- VARIABLES: Categoría, Estimado, Real\* (automático desde transacciones), Moneda, Recurrente
- Fila de TOTAL por sección

**Notas:**

- El Real de VARIABLES se calcula automáticamente desde el Registro de Transacciones
- El Real de FIJOS se anota manualmente
- Mismo problema que Ingresos: el nombre "Real" podría ser más descriptivo

**Problema de consistencia:**

- El selector de moneda está en posiciones distintas entre pantallas — conviene unificarlo. A futuro, si hay más monedas, reemplazar por un selector dropdown manteniendo la visualización rápida entre las dos monedas principales

---

## Pantalla 8 — Ahorros e Inversiones

**Propósito:** gestionar instrumentos de ahorro e inversión organizados por tipo y objetivo.

**Estructura:**

- Tabs por tipo de instrumento: RESUMEN / FCI / ACCIONES / CEDEARS / PLAZO FIJO / PLAZO FLEX / OTROS
- Botón + NUEVO OBJETIVO (arriba a la derecha)

**Tab RESUMEN:**

- Toggle de vista: POR INSTRUMENTO / POR OBJETIVO
- POR INSTRUMENTO: lista de instrumentos con saldo y distribución por objetivo
- POR OBJETIVO: lista de objetivos con sus instrumentos asignados (desde acá se editan los objetivos)

**Tabs de instrumento (ej: FCI):**

- Botón + NUEVO FCI
- Cards por instrumento: nombre, moneda, saldo, objetivos asignados con porcentaje
- Acciones por card: + RENDIMIENTO / + APORTE / - RETIRO
- Tabla de movimientos: Fecha, Activo, Movimiento, Monto, Comisión, Moneda, Objetivos, Notas
- Fila de alta inline en la tabla
- Estructura similar para todos los tipos de instrumento

---

## Pantalla 9 — Cuentas

**Propósito:** crear y gestionar cuentas bancarias, tarjetas y billeteras.

**Elementos:**

- Botón + Agregar cuenta
- Lista de cuentas con: nombre, tipo, saldo por moneda, íconos de editar / archivar / borrar

**Diálogo Agregar/Editar cuenta:**

- Campo nombre
- Selector de tipo: Débito / Crédito / Efectivo / Cripto / Inversión
- Si tipo = Crédito → debe asociarse a un banco (débito existente) — se mantiene
- Monedas habilitadas + Agregar moneda
- Selector de color
- Notas (opcional)

**Problema identificado:**

- Los diálogos de alta y edición son demasiado altos para los campos que tienen — mismo problema que en otras pantallas

---

## Pantalla 10 — Categorías

**Propósito:** crear y gestionar categorías para clasificar transacciones.

**Elementos:**

- Botón + Agregar categoría
- Lista agrupada por tipo (EGRESOS / INGRESOS / MOVIMIENTOS)
- Por categoría: nombre, tipo, estado (activa / inactiva), íconos de editar / archivar

**Diálogo Agregar categoría:**

- Campo categoría principal
- Campo subcategoría
- Selector de tipo

**Problemas identificados:**

- El campo subcategoría está atado a la categoría principal pero termina siendo la misma información que el tipo — esquema a repensar
- Editar una categoría existente debería actualizar toda la DB (nombre en transacciones históricas)
- Las restricciones actuales sobre categorías especiales son excesivas — ver PRD

---

## Botón 11 — Sincronización

**Propósito:** disparar manualmente la sincronización con Supabase. Es un botón en el sidebar, no una pantalla completa.

**Comportamiento:**

- El ícono cambia de color mientras sincroniza
- Muestra indicador de estado: sincronizado / sincronizando / error
- La sync también corre automáticamente al abrir la app y cada 5 minutos para tablas compartidas

---

## Botón 12 — Cuenta

**Propósito:** ver el usuario logueado y cerrar sesión. Es un botón en el sidebar, no una pantalla completa.

**Elementos:**

- Nombre de usuario
- Email
- Botón CERRAR SESIÓN
