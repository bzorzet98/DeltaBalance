# DeltaBalance — Relevamiento

Oct 22, 2026 · @Bruno Jose Zorzet

Este documento describe el problema que DeltaBalance resuelve, para quién y bajo qué restricciones. No menciona tecnología — las decisiones técnicas van en el Architecture Doc.

## El problema

Durante años, Bruno y Noelia gestionaban sus finanzas personales en archivos Excel separados en Google Drive. Cada uno mantenía su propio registro mensual con movimientos de cuenta, compras en cuotas, deudas y ahorros. En diferentes momentos de cada mes había que cruzar manualmente la información de los dos archivos para liquidar los gastos compartidos del hogar — un proceso tedioso y propenso a errores.

Los tres problemas principales que motivaron DeltaBalance:

1. **Velocidad:** Excel con cuatro años de registros y múltiples hojas con tablas dinámicas se volvió lento para la carga y consulta diaria.
2. **Cruce manual de gastos compartidos:** determinar cuánto le debía uno al otro requería comparar dos archivos, hacer cálculos a mano y comunicarse para acordar el resultado. Cualquier error de uno afectaba al otro sin que hubiera forma de detectarlo automáticamente.
3. **Dispersión de la información:** los detalles de cuotas, ahorros, deudas y presupuestos vivían en hojas separadas sin conexión entre sí. Ver el panorama completo del mes requería abrir y cruzar múltiples hojas manualmente.

## Usuarios

Dos usuarios, convivientes, que comparten gastos del hogar:

| Usuario | Rol |
| --- | --- |
| Bruno | Usuario principal. Creó y desarrolla la app. Registra sus propios movimientos y los gastos compartidos que él paga. |
| Noelia | Segunda usuaria. Registra sus propios movimientos y los gastos compartidos que ella paga. |

No hay usuarios administradores ni roles diferenciados dentro de la app — ambos tienen acceso completo a su propia información y a los gastos compartidos del hogar. La información privada de cada uno (movimientos de cuenta, ahorros, deudas personales) no es visible para el otro.

## Flujo de procesos

**Antes — Excel en Google Drive:**

Cada usuario mantenía un archivo Excel propio con una hoja de registro mensual (concepto, banco, categoría, monto, fecha) y una tabla dinámica que agrupaba por banco y categoría. En el encabezado se cargaba el saldo del mes anterior para determinar con cuánto arrancaba cada cuenta. Hojas adicionales detallaban compras en cuotas, deudas, ahorros y presupuesto. Al cierre de cada mes, los gastos compartidos se liquidaban cruzando los dos archivos manualmente.

**Hoy — DeltaBalance:**

Cada usuario carga sus movimientos en su propia instalación de la app. Los gastos marcados como compartidos se sincronizan automáticamente con la app del otro usuario, que ve su parte de la deuda sin necesidad de intercambiar información manualmente. El dashboard muestra en tiempo real el disponible del mes y la proyección al cierre, integrando cuentas, cuotas pendientes, deudas y presupuesto en una sola pantalla.

## Sistemas externos

| Sistema | Para qué se usa |
| --- | --- |
| Supabase (Postgres + Auth) | Sincronización de datos entre las dos instalaciones y autenticación de usuarios |
| Google Drive (legacy) | Archivos Excel originales — solo como referencia histórica, ya no forma parte del flujo activo |

## Restricciones

| Restricción | Impacto en el diseño |
| --- | --- |
| App de escritorio en Linux y Windows | No puede ser una app web ni mobile. El build tiene que correr en ambos sistemas operativos. |
| Sin servidor propio | No hay backend dedicado. La sincronización usa Supabase como intermediario. |
| Dos usuarios únicos y conocidos | No hay registro público ni gestión de usuarios. Los usuarios están hardcodeados por UUID. |
| Datos financieros sensibles | La base de datos se sube a Supabase, protegidos por RLS. |
| Conexión a internet opcional | La app tiene que funcionar sin conexión. La sincronización ocurre cuando hay internet disponible. |
| Un desarrollador | Las decisiones técnicas priorizan simplicidad de mantenimiento sobre escalabilidad. |

## Requerimientos identificados

**Registro de movimientos**

- Registrar ingresos y egresos de cuenta con concepto, categoría, monto, fecha y banco
- Registrar transferencias entre cuentas propias (AUTOTRANSFERENCIAS)
- Registrar cambios de moneda (compra/venta de divisas)
- Soporte para múltiples monedas (ARS, USD, BTC, etc.)

**Compras en cuotas**

- Registrar una compra y generar automáticamente el cronograma de cuotas mensuales
- Calcular el total de cuotas a pagar en el mes corriente
- Soportar cargos extra (impuestos, recargos, reintegros), de una manera sencilla como una "compra en cuota" de una sola cuota.&#32;

**Deudas**

- Registrar deudas informales entre personas (me deben / debo)

**Ahorros e inversiones**

- Registrar instrumentos de ahorro con movimientos (compra, venta, aporte, rendimiento)
- Organizar los ahorros en objetivos
- Soporte para FCI, CEDEARs y otros instrumentos

**Gastos compartidos del hogar**

- Marcar un gasto como compartido y definir el porcentaje/monto/coeficiente que le corresponde al otro
- Sincronizar automáticamente los gastos compartidos con la app del otro usuario
- Registrar pagos parciales de deudas compartidas. Esto puede ser como una deuda nueva pero con signo contrario. No es necesario decidir esto ya fue saldado, esto no. Quizas en versiones futuras si alguno se vuelve "prestamista".

**Presupuesto e ingresos**

- Definir presupuestos por categoría, y egresos fijos como lo pueden ser servicios, impuestos, ahorros, etc.
- Registrar ingresos proyectados fijos y variables
- Comparar estimado vs real al cierre del mes, no solamente al cierre del mes sino que tambien durante.&#32;

**Dashboard**

- Mostrar el disponible real del mes (ingresos reales menos egresos reales)
- Mostrar la proyección al cierre del mes
- Filtrar por moneda

## Preguntas abiertas

| # | Pregunta | Impacto | Respuesta |
| --- | --- | --- | --- |
| P1 | ¿La información privada de cada usuario (movimientos, ahorros, deudas) es completamente invisible para el otro, o hay algún nivel de visibilidad compartida? | Modelo de datos y RLS en Supabase | Solo los gastos compartidos son visibles entre usuarios. Cada usuario tiene su propia base de datos privada sincronizada en Supabase. Un gasto compartido puede originarse desde el registro mensual o desde una compra en cuotas |
| P2 | ¿El precio de mercado de los instrumentos de ahorro (CEDEARs, FCI) se va a incorporar en algún momento, o siempre se valúa a costo histórico? | Modelo de datos de ahorros | Solamente por el momento se colocaran cantidades, fechas y perecio de ese momento de la transaccion.&#32; |
| P3 | ¿Se contempla soporte para UVA o CER en préstamos? | Módulo de préstamos | El modulo prestamos es algo muy futuro, ya que no serian deudas informales.&#32; |
| P4 | ¿El build de Windows es una prioridad activa o es a futuro? | Pipeline de CI/CD | Es prioridad porque ahora tengo una pc con windows |
| P5 | ¿La sincronización seguirá siendo solo para gastos compartidos, o en algún momento se sincronizarán también otros datos entre los dos usuarios? | Arquitectura de sync | La app está diseñada para dos usuarios fijos (Bruno y Noelia) dentro del plan gratuito de Supabase. No se planea abrir el registro a otros usuarios. Como ejercicio técnico se documentará qué cambios requeriría el modelo para soportar múltiples hogares y usuarios, pero queda fuera del alcance del MVP. |
