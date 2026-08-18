# Arquitectura de DeltaBalance

Documento de referencia de qué va en cada carpeta y por qué. Si una carpeta no está
descripta acá, no debería crearse sin agregarla primero a este documento.

## Estado de las carpetas heredadas

`dal/` y `logic/` existían en el MVP anterior como intento de separar capas, pero
quedaron con todos sus archivos vacíos (nunca se implementaron). Se eliminan y su
función queda cubierta por `repositories/` (ver más abajo). Si en algún commit viejo
aparecen referencias a `dal.` o `logic.`, son residuales y hay que quitarlas.

## Árbol de carpetas y responsabilidad de cada una

```
DeltaBalance/
├── db/
│   ├── schema.sql            # única fuente de verdad de tablas NUEVAS (CREATE ... IF NOT EXISTS)
│   ├── schema_migrations.py  # columnas nuevas sobre tablas EXISTENTES — ALTER TABLE
│   │                          # idempotente vía PRAGMA table_info (SQLite no soporta
│   │                          # ADD COLUMN IF NOT EXISTS; ver DATA_MODEL_DECISIONS.md §2)
│   ├── seed.sql               # datos semilla (monedas, categorías base)
│   ├── database.py            # clase DatabaseManager — conexión/transacciones/
│   │                          # inicialización + fetchall/fetchone/execute crudos.
│   │                          # Ya NO tiene CRUD por entidad (se migró todo a
│   │                          # repositories/, bloque por bloque durante la Fase 2);
│   │                          # cumple hoy el rol de connection.py de abajo. Quedan
│   │                          # solo un puñado de métodos de lectura sin repositorio
│   │                          # propio (monedas, tipo de cambio, un par de vistas
│   │                          # agregadas) — no repetir ese patrón en código nuevo,
│   │                          # una entidad nueva va en un repositorio dedicado.
│   ├── connection.py          # (futuro, todavía no existe) reemplazo de database.py:
│   │                          # apertura/cierre de conexión, init, transacciones — la
│   │                          # única pieza que abre una conexión a mano
│   └── query_builder.py       # helper genérico de queries (ya existente, se conserva)
│
├── repositories/             # una clase por tabla/agregado — SOLO acceso a datos.
│   │                          # 19 repositorios concretos (Fase 2, completada bloque
│   │                          # por bloque). Agrupados abajo por bloque de dominio —
│   │                          # mismo agrupamiento que verify/ (ver su README.md).
│   ├── cuentas_repository.py
│   ├── categorias_repository.py
│   ├── transacciones_repository.py
│   ├── deudas_repository.py             # incluye deuda_pagos
│   ├── compras_cuotas_repository.py
│   ├── cuotas_credito_repository.py
│   ├── resumenes_tarjeta_repository.py
│   ├── resumen_cargos_extra_repository.py
│   ├── presupuestos_repository.py
│   ├── ingresos_proyectados_repository.py
│   ├── empleos_repository.py            # incluye recibos_sueldo + descuentos_programados
│   ├── activos_financieros_repository.py
│   ├── objetivos_ahorro_repository.py
│   ├── movimientos_activo_repository.py
│   ├── asignaciones_repository.py
│   ├── hogares_repository.py
│   ├── hogar_miembros_repository.py
│   ├── gastos_compartidos_repository.py
│   ├── prestamos_repository.py
│   ├── cuotas_prestamo_repository.py
│   └── _sentinels.py                    # NO_CAMBIAR — no es un repositorio, es el
│                                          # sentinel compartido que varios usan
│
├── services/                # lógica de negocio — orquesta repositorios
│   ├── transaction_service.py
│   ├── debts_service.py
│   ├── fees_service.py
│   ├── presupuestos_service.py
│   ├── ingresos_proyectados_service.py
│   ├── empleos_service.py
│   ├── savings_service.py
│   ├── shared_expenses_service.py
│   └── loans_service.py
│
├── verify/                   # scripts de verificación manual (no pytest)
│   ├── _dummy_db.py            # helper compartido: crea una DB temporal descartable
│   │                           # con schema + schema_migrations + seed ya aplicados.
│   ├── README.md               # convención de nombre + cómo correr los scripts
│   └── <subcarpeta>/           # cada subcarpeta agrupa los verify_<dominio>.py de un
│                               # bloque de dominio (schema/, cuentas_categorias/,
│                               # transacciones/, deudas/, compras_cuotas/,
│                               # presupuestos_ingresos_empleos/, ahorros/,
│                               # hogares_gastos_compartidos/, prestamos/) — ver
│                               # verify/README.md para el detalle de cada una.
│
├── lab/                      # sandbox de investigación — bienestar, análisis futuros.
│                              # Solo lee del motor de datos, nunca lo modifica desde acá
│                              # salvo que se indique lo contrario explícitamente.
│                              # Vacía por ahora (solo .gitkeep).
│
├── migration/                 # scripts de importación del sistema viejo (futuro)
│                              # Vacía por ahora (solo .gitkeep).
│
├── sync/                      # cliente de sincronización familiar vía Supabase (futuro)
│                              # Vacía por ahora (solo .gitkeep).
│
├── ui/                        # aplicación Flet (futuro, próxima sesión)
│   ├── theme/                  # design tokens: colores, spacing, tipografía
│   │                           # Vacía por ahora (solo __init__.py).
│   └── screens/
│                               # Vacía por ahora (solo __init__.py).
│
├── utils/
│   └── money.py               # to_minor / from_minor y formateo de montos
│
└── data/
    └── deltabalance.db        # base real del usuario — nunca se toca a mano
```

## Regla de dependencia

`ui/` y `sync/` pueden importar de `services/`, `repositories/` y `db/`.
`services/` puede importar de `repositories/` y `db/`, nunca de `ui/` ni `sync/`.
`repositories/` solo importa de `db/`.
`lab/` importa de `services/`/`repositories/` para leer, igual que `ui/`.

Ninguna flecha va "hacia abajo": si `repositories/` necesitara importar algo de
`services/`, es señal de que la responsabilidad está mal ubicada.
