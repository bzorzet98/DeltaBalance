# verify/

Scripts de verificación manual. No es una suite de tests automatizada (no pytest, no
CI). Cada script se corre a mano, uno a la vez, cuando querés confirmar que una
funcionalidad hace lo que tiene que hacer.

## Convención

- Archivo: `verify_<dominio>.py` (ej. `verify_gastos_compartidos_repository.py`) — la
  convención de nombre no cambia, solo cambia en qué subcarpeta vive cada uno (ver
  "Subcarpetas" abajo).
- Usa el motor de datos real (`services/`, `repositories/`) contra una base de datos
  temporal creada por el propio script — nunca contra `data/deltabalance.db`.
- Imprime en consola cada caso probado, con el resultado esperado y el obtenido,
  marcando ✅ o ❌. Pensado para que una persona lo lea, no para integrarlo a un pipeline.
- Al final del script, un resumen: cuántos casos pasaron y cuántos no.

## Subcarpetas

Los scripts están agrupados en subcarpetas por bloque de dominio — el mismo
agrupamiento que se fue usando fase a fase al construir el motor de datos (Fase 2).
`_dummy_db.py` y este `README.md` quedan sueltos en la raíz de `verify/` porque son
compartidos por todas las subcarpetas, no pertenecen a un dominio puntual.

| Subcarpeta                          | Bloque de dominio                                              |
|--------------------------------------|-----------------------------------------------------------------|
| `schema/`                            | Schema general, migraciones de columna, soft-delete de categorías |
| `cuentas_categorias/`                 | Cuentas y categorías                                            |
| `transacciones/`                      | Transacciones (repositorio + service)                           |
| `deudas/`                             | Deudas informales entre personas                                 |
| `compras_cuotas/`                     | Compras en cuotas, cuotas de crédito, resúmenes de tarjeta y sus cargos extra |
| `presupuestos_ingresos_empleos/`      | Presupuestos, ingresos proyectados, empleos/recibos de sueldo/descuentos |
| `ahorros/`                            | Activos financieros, objetivos de ahorro, movimientos y asignaciones |
| `hogares_gastos_compartidos/`         | Hogares, miembros de hogar, gastos compartidos                   |
| `prestamos/`                          | Préstamos (hipotecario/prendario/personal) y su cronograma de cuotas |
| `dashboard/`                          | Agregaciones de solo lectura para el dashboard (patrimonio, gasto por categoría, comparación vs. presupuesto) |
| `utils/`                              | Módulos de utils/ sin dominio propio (ej. calculadora_segura.py) — sin services/repositories que probar, solo la función pura |

Un script nuevo va en la subcarpeta del bloque de dominio al que pertenece; si abre un
bloque nuevo que todavía no tiene subcarpeta, se crea una.

## Cómo correrlo

```bash
python verify/<subcarpeta>/verify_<dominio>.py
```

Ejemplo:

```bash
python verify/prestamos/verify_loans_service.py
```

Nadie más que vos ejecuta estos scripts — Claude Code los crea pero no los corre.
