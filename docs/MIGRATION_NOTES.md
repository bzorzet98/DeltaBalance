# Notas de migración del sistema anterior

Estado: **placeholder**. No implementar scripts de migración todavía, salvo pedido
explícito. Este documento existe para que el diseño de tablas y servicios nuevos no
ignore que en algún momento va a tener que poblarse con años de historial.

## Lo que se sabe hoy
- El sistema anterior es un conjunto de planillas de cálculo (Excel), con hojas
  separadas para: registro de gastos, estimado vs. real por categoría, rendición de
  gastos compartidos con la pareja, y savings/reintegros de tarjeta.
- El volumen es de varios años de registro personal — no es un dataset chico.
- La estructura exacta de columnas de esas planillas todavía no se relevó en detalle.

## Principios para cuando se aborde (fase futura)
1. **Import por lote, no por UI.** Cada tabla nueva debe poder poblarse desde un script
   en `migration/` sin pasar clic por clic por la aplicación.
2. **Trazabilidad del origen.** Los registros importados deberían poder marcarse de
   alguna forma como provenientes de la migración (ej. usando el patrón
   `origen_tipo`/`origen_id` que ya existe en `deudas`, o un campo `notas` con un tag
   reconocible) para poder auditar o revertir un import si algo salió mal.
2. **Nunca fecha implícita = hoy.** Todo alta en repositorios/servicios debe aceptar
   fecha explícita, porque la migración va a insertar con fechas históricas reales.
3. **Validación tolerante, no estricta al extremo.** Los datos viejos van a tener
   inconsistencias (categorías con nombres levemente distintos, montos con formato raro).
   El script de migración deberá tener su propia capa de normalización antes de llamar
   a los repositorios — los repositorios no deberían relajar sus validaciones para
   acomodar datos sucios.
4. **Ejecutable en partes.** Preferible poder migrar "solo transacciones", "solo
   deudas", etc. por separado, no un único script monolítico todo-o-nada.

## Próximo paso cuando se retome
Relevar la estructura real de las planillas (columnas, hojas, convenciones de fecha)
antes de escribir cualquier script de `migration/`.
