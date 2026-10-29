-- =============================================================
-- DeltaBalance — db/schema.sql
-- SQLite 3 — foto del schema v2 (PRAGMA user_version = 2)
-- =============================================================
-- Este archivo es el schema REAL completo de la última versión:
-- - v1: exactamente lo que dejaba en una base la cadena anterior (schema +
--   MIGRACIONES_COLUMNA + aplicar_migraciones_tabla()), con las columnas en
--   el mismo orden (docs/DATA_MODEL_DECISIONS.md sección 37);
-- - v2: sin las tablas sin uso tipos_cambio, indices_inflacion,
--   resumen_cargos_extra y activo_objetivos (sección 38).
--
-- Solo se aplica a una base NUEVA (sin tablas): DatabaseManager.inicializar()
-- lo corre, siembra los brokers, crea la sync (preparar_sync()), carga
-- seed.sql y marca user_version = VERSION_SCHEMA. Una base existente ya NO
-- lo reaplica:
--   - user_version = 0 → migración baseline (db/schema_migrations.py,
--     run_migrations(); usa db/schema_v0.sql, el schema anterior congelado);
--   - user_version >= 1 → solo las migraciones numeradas que falten.
-- Un cambio de schema nuevo va como migración numerada en
-- db/schema_migrations.py (MIGRACIONES_VERSIONADAS) Y acá, subiendo
-- VERSION_SCHEMA: este archivo tiene que seguir siendo la foto de la última
-- versión.
--
-- Lo que NO está acá: los 63 triggers trg_sync_* de la sincronización con
-- Supabase. Los genera preparar_sync() (db/schema_migrations.py) en cada
-- arranque, fuera del versionado.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- IDs: UUID v4 en texto ('8f14e45f-ceea-4a7b-9c3e-…') en TODAS las tablas,
-- salvo monedas (datos de referencia iguales en toda base: sigue con su id
-- entero, y moneda_id / moneda_*_id siguen siendo INTEGER). Así una fila
-- tiene la misma identidad en cualquier computadora (sincronización,
-- docs/DATA_MODEL_DECISIONS.md sección 25). Los repositorios generan el
-- UUID en Python (uuid.uuid4()) y lo devuelven; el DEFAULT de cada `id` es
-- la red de seguridad para un INSERT sin id (seed.sql, scripts de
-- migration/). NOT NULL explícito: en SQLite una PRIMARY KEY que no es
-- INTEGER acepta NULL si no se lo prohíbe. Una base con ids enteros se
-- convierte con migration/migrar_a_uuid_pk.py.

-- =============================================================
-- MONEDAS
-- =============================================================
CREATE TABLE IF NOT EXISTS monedas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo          TEXT NOT NULL UNIQUE,
    simbolo         TEXT,
    decimales       INTEGER NOT NULL DEFAULT 2
);

-- =============================================================
-- CUENTAS
-- =============================================================
CREATE TABLE IF NOT EXISTS cuentas (
    id                  TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    nombre              TEXT NOT NULL UNIQUE,

    tipo                TEXT NOT NULL
                        CHECK(tipo IN (
                            'debito',
                            'credito',
                            'efectivo',
                            'crypto',
                            'inversion'
                        )),

    cuenta_pago_id      TEXT REFERENCES cuentas(id),

    activa              INTEGER NOT NULL DEFAULT 1,
    notas               TEXT,

    creada_en           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    color_hex           TEXT DEFAULT '#5F5E5A',
    sincronizado_en     TEXT DEFAULT NULL
);

-- =============================================================
-- CUENTAS SALDOS
-- =============================================================
CREATE TABLE IF NOT EXISTS cuentas_saldos (
    cuenta_id               TEXT NOT NULL,
    moneda_id               INTEGER NOT NULL,

    saldo_inicial_minor     INTEGER NOT NULL DEFAULT 0,

    visible                 INTEGER NOT NULL DEFAULT 1,

    sincronizado_en         TEXT DEFAULT NULL,

    PRIMARY KEY (cuenta_id, moneda_id),

    FOREIGN KEY (cuenta_id) REFERENCES cuentas(id),
    FOREIGN KEY (moneda_id) REFERENCES monedas(id)
);

-- =============================================================
-- CATEGORIAS
-- =============================================================
CREATE TABLE IF NOT EXISTS categorias (
    id                      TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    categoria_principal     TEXT NOT NULL,
    subcategoria            TEXT NOT NULL,

    tipo                    TEXT NOT NULL
                            CHECK(tipo IN (
                                'ingreso',
                                'egreso',
                                'movimiento'
                            )),

    -- Soft-delete (docs/DATA_MODEL_DECISIONS.md sección 11).
    activa                  INTEGER NOT NULL DEFAULT 1,
    sincronizado_en         TEXT DEFAULT NULL,

    UNIQUE(categoria_principal, subcategoria)
);

-- =============================================================
-- EMPLEOS
-- =============================================================
CREATE TABLE IF NOT EXISTS empleos (
    id                          TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    nombre_empresa              TEXT NOT NULL,
    puesto                      TEXT,

    moneda_id                   INTEGER NOT NULL REFERENCES monedas(id),

    porcentaje_jubilacion       INTEGER DEFAULT 1100,
    porcentaje_obra_social      INTEGER DEFAULT 300,
    porcentaje_gremio           INTEGER DEFAULT 0,

    tope_copago_os_minor        INTEGER DEFAULT 0,

    activa                      INTEGER DEFAULT 1,

    creada_en                   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- TRANSACCIONES
-- =============================================================
CREATE TABLE IF NOT EXISTS transacciones (
    id                          TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    fecha                       TEXT NOT NULL
                                CHECK(fecha GLOB '????-??-??'),

    concepto                    TEXT NOT NULL,

    cuenta_id                   TEXT NOT NULL,
    categoria_id                TEXT NOT NULL,
    moneda_id                   INTEGER NOT NULL,

    tipo_movimiento             TEXT NOT NULL
                                CHECK(tipo_movimiento IN (
                                    'ingreso',
                                    'egreso',
                                    'movimiento'
                                )),

    monto_minor                 INTEGER NOT NULL CHECK(monto_minor >= 0),

    tag                         TEXT,
    notas                       TEXT,

    creada_en                   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at                      TEXT,
    sincronizado_en             TEXT DEFAULT NULL,

    FOREIGN KEY (cuenta_id) REFERENCES cuentas(id),
    FOREIGN KEY (categoria_id) REFERENCES categorias(id),
    FOREIGN KEY (moneda_id) REFERENCES monedas(id)
);

-- =============================================================
-- AUTOTRANSFERENCIAS
-- =============================================================
-- Vínculo formal entre las dos filas de `transacciones` (egreso en origen,
-- ingreso en destino) que genera TransactionService.create_transfer().
-- Agregada recién ahora (ver docs/DATA_MODEL_DECISIONS.md) — el código
-- legacy de db/database.py ya asumía su existencia e insertaba contra
-- ella, pero nunca había sido creada en este schema.
CREATE TABLE IF NOT EXISTS autotransferencias (
    id                          TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    transaccion_salida_id       TEXT NOT NULL REFERENCES transacciones(id),
    transaccion_entrada_id      TEXT NOT NULL REFERENCES transacciones(id),

    notas                       TEXT,

    creada_en                   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(transaccion_salida_id, transaccion_entrada_id),
    CHECK(transaccion_salida_id != transaccion_entrada_id)
);

-- =============================================================
-- RESUMENES TARJETA
-- =============================================================
-- Las fechas reales de cierre / vencimiento de un resumen, cuando no son
-- las que dan los días de tarjetas_config, van en tarjetas_resumenes (las
-- dos tablas están más abajo — docs/DATA_MODEL_DECISIONS.md secciones 23 y
-- 29).
CREATE TABLE IF NOT EXISTS resumenes_tarjeta (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    cuenta_id                       TEXT NOT NULL,

    mes                             INTEGER NOT NULL
                                    CHECK(mes BETWEEN 1 AND 12),

    anio                            INTEGER NOT NULL,

    monto_consumos_minor            INTEGER NOT NULL DEFAULT 0,
    monto_impuestos_minor           INTEGER NOT NULL DEFAULT 0,

    porcentaje_impuesto_bp          INTEGER NOT NULL DEFAULT 0,

    monto_total_pagado_minor        INTEGER NOT NULL,

    fecha_pago                      TEXT
                                    CHECK(
                                        fecha_pago IS NULL OR
                                        fecha_pago GLOB '????-??-??'
                                    ),

    estado                          TEXT NOT NULL DEFAULT 'abierto'
                                    CHECK(estado IN (
                                        'abierto',
                                        'cerrado',
                                        'pagado'
                                    )),

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sincronizado_en                 TEXT DEFAULT NULL,

    FOREIGN KEY (cuenta_id) REFERENCES cuentas(id),

    UNIQUE(cuenta_id, mes, anio)
);

-- =============================================================
-- TARJETAS CONFIG
-- =============================================================
-- Días de cierre y vencimiento de cada tarjeta de crédito: con ellos
-- FeesService calcula las fechas de los resúmenes y sugiere el mes de la 1ª
-- cuota. Una fila por tarjeta (UNIQUE cuenta_id); updated_en lo escribe el
-- repositorio en cada upsert (sin trigger). Ver
-- docs/DATA_MODEL_DECISIONS.md sección 23.
CREATE TABLE IF NOT EXISTS tarjetas_config (
    id                  TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    cuenta_id           TEXT NOT NULL REFERENCES cuentas(id) UNIQUE,
    dia_cierre          INTEGER NOT NULL CHECK(dia_cierre BETWEEN 1 AND 31),
    dia_vencimiento     INTEGER NOT NULL CHECK(dia_vencimiento BETWEEN 1 AND 31),
    creada_en           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sincronizado_en     TEXT DEFAULT NULL
);

-- =============================================================
-- TARJETAS RESUMENES
-- =============================================================
-- Fecha real de cierre / vencimiento de UN resumen, cuando no es la que dan
-- los días de tarjetas_config (feriados, cambios del banco). Una fila por
-- (tarjeta, mes, anio) — el mes del cierre calculado con el día default, el
-- mismo que identifica cada resumen en FeesService.card_cycle_dates(). Cada
-- fecha es opcional: NULL = la calculada. Ver docs/DATA_MODEL_DECISIONS.md
-- sección 29.
CREATE TABLE IF NOT EXISTS tarjetas_resumenes (
    id                  TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    cuenta_id           TEXT NOT NULL REFERENCES cuentas(id),
    mes                 INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio                INTEGER NOT NULL,
    fecha_cierre        TEXT CHECK(fecha_cierre IS NULL OR fecha_cierre GLOB '????-??-??'),
    fecha_vence         TEXT CHECK(fecha_vence IS NULL OR fecha_vence GLOB '????-??-??'),
    creada_en           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sincronizado_en     TEXT DEFAULT NULL,
    UNIQUE(cuenta_id, mes, anio)
);

-- =============================================================
-- COMPRAS CUOTAS
-- =============================================================
CREATE TABLE IF NOT EXISTS compras_cuotas (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    fecha_compra                   TEXT NOT NULL
                                    CHECK(fecha_compra GLOB '????-??-??'),

    concepto                       TEXT NOT NULL,

    cuenta_id                      TEXT NOT NULL REFERENCES cuentas(id),
    categoria_id                   TEXT NOT NULL REFERENCES categorias(id),
    moneda_id                      INTEGER NOT NULL REFERENCES monedas(id),

    monto_total_minor              INTEGER NOT NULL,
    total_cuotas                   INTEGER NOT NULL DEFAULT 1,
    monto_por_cuota_minor          INTEGER NOT NULL,

    estado                         TEXT NOT NULL DEFAULT 'activa'
                                    CHECK(estado IN (
                                        'activa',
                                        'cancelada',
                                        'completada'
                                    )),

    notas                          TEXT,

    creada_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Gastos compartidos (docs/DATA_MODEL_DECISIONS.md sección 2).
    monto_reintegro_minor          INTEGER DEFAULT 0,
    modo_deuda                     TEXT DEFAULT 'prorrateado' CHECK(modo_deuda IN ('total_unico', 'prorrateado')),

    -- Etiqueta libre, igual que transacciones.tag (sección 26).
    tag                            TEXT,

    -- 1 = cargo/reintegro del resumen de la tarjeta (impuesto, recargo,
    -- ajuste) cargado con una categoría especial de TARJETA DE CRÉDITO — una
    -- "compra" de 1 cuota cuya cuota ya está en el resumen de su mes
    -- (sección 28).
    es_cargo_extra                 INTEGER NOT NULL DEFAULT 0,

    sincronizado_en                TEXT DEFAULT NULL
);

-- =============================================================
-- CUOTAS CREDITO
-- =============================================================
CREATE TABLE IF NOT EXISTS cuotas_credito (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    compra_id                       TEXT NOT NULL REFERENCES compras_cuotas(id),
    resumen_id                      TEXT REFERENCES resumenes_tarjeta(id),

    numero_cuota                    INTEGER NOT NULL,

    mes_proyectado                  INTEGER NOT NULL
                                    CHECK(mes_proyectado BETWEEN 1 AND 12),

    anio_proyectado                 INTEGER NOT NULL,

    mes_real_pago                   INTEGER
                                    CHECK(
                                        mes_real_pago BETWEEN 1 AND 12
                                    ),

    anio_real_pago                  INTEGER,

    monto_cuota_minor               INTEGER NOT NULL,

    estado                          TEXT NOT NULL DEFAULT 'pendiente'
                                    CHECK(estado IN (
                                        'pendiente',
                                        'en_resumen',
                                        'pagado',
                                        'omitido'
                                    )),

    notas                           TEXT,

    sincronizado_en                 TEXT DEFAULT NULL,

    UNIQUE(compra_id, numero_cuota)
);

-- =============================================================
-- DEUDAS
-- =============================================================
-- Libro de movimientos (docs/DATA_MODEL_DECISIONS.md sección 22): cada fila
-- es un movimiento con una persona dentro de un `tab` — 'me_deben' (lo que
-- te deben) o 'debo' (lo que debés) — y monto_minor CON SIGNO: positivo =
-- entrada (la deuda crece), negativo = salida (un pago que la baja). El
-- saldo de una persona en un tab es SUM(monto_minor). No hay pendiente,
-- estado, vencimiento ni tabla de pagos aparte (deuda_pagos ya no existe).
--
-- Las bases con alguna estructura anterior de `deudas` se convierten a esta
-- en la migración baseline v0 → v1 (db/schema_migrations.py,
-- reestructurar_deudas()). En v1 la tabla no tiene índices: esta foto
-- reproduce exactamente lo que dejaba esa migración.
CREATE TABLE IF NOT EXISTS deudas (
    id              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    entidad_persona TEXT NOT NULL,
    concepto        TEXT,
    tab             TEXT NOT NULL CHECK(tab IN ('me_deben', 'debo')),
    monto_minor     INTEGER NOT NULL,  -- positivo = entrada, negativo = salida
    moneda_id       INTEGER NOT NULL REFERENCES monedas(id),
    fecha           TEXT NOT NULL CHECK(fecha GLOB '????-??-??'),
    notas           TEXT,
    origen_tipo     TEXT DEFAULT 'manual',
    origen_id       TEXT,
    sincronizado_en TEXT DEFAULT NULL,
    creada_en       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tag             TEXT  -- etiqueta libre, igual que transacciones.tag (sección 26)
);

-- =============================================================
-- PRESUPUESTOS
-- =============================================================
-- Dos tipos (reestructurar_presupuestos_ingresos() en
-- db/schema_migrations.py convirtió la estructura anterior): 'fijo'
-- (concepto libre, con su real cargado a mano en monto_real_minor) y
-- 'variable' (una categoría del Registro, con el real calculado de las
-- transacciones, o el ítem especial COMPARTIDOS: categoria_id NULL y
-- concepto 'COMPARTIDOS' — ver services/presupuestos_service.py). Un
-- variable por categoría y mes lo controla el service, no un UNIQUE.
CREATE TABLE IF NOT EXISTS presupuestos (
    id                   TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    tipo                 TEXT NOT NULL CHECK(tipo IN ('fijo', 'variable')),
    concepto             TEXT,
    categoria_id         TEXT REFERENCES categorias(id),
    monto_estimado_minor INTEGER NOT NULL DEFAULT 0,
    monto_real_minor     INTEGER NOT NULL DEFAULT 0,
    moneda_id            INTEGER NOT NULL REFERENCES monedas(id),
    mes                  INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio                 INTEGER NOT NULL,
    es_recurrente        INTEGER NOT NULL DEFAULT 0,
    sincronizado_en      TEXT DEFAULT NULL,
    creada_en            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- INGRESOS PROYECTADOS
-- =============================================================
-- Estructura final de reestructurar_presupuestos_ingresos(): "cobrado" es
-- monto_real_minor > 0 (ya no hay `estado` ni monto_percibido_minor).
CREATE TABLE IF NOT EXISTS ingresos_proyectados (
    id                   TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    concepto             TEXT NOT NULL,
    monto_estimado_minor INTEGER NOT NULL DEFAULT 0,
    monto_real_minor     INTEGER NOT NULL DEFAULT 0,
    moneda_id            INTEGER NOT NULL REFERENCES monedas(id),
    mes                  INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio                 INTEGER NOT NULL,
    es_recurrente        INTEGER NOT NULL DEFAULT 0,
    notas                TEXT,
    sincronizado_en      TEXT DEFAULT NULL,
    creada_en            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- RECIBOS SUELDO
-- =============================================================
CREATE TABLE IF NOT EXISTS recibos_sueldo (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    empleo_id                       TEXT NOT NULL REFERENCES empleos(id),

    mes                             INTEGER NOT NULL,
    anio                            INTEGER NOT NULL,

    sueldo_bruto_minor              INTEGER NOT NULL,

    desc_jubilacion_minor           INTEGER NOT NULL,
    desc_obra_social_minor          INTEGER NOT NULL,
    desc_copagos_os_minor           INTEGER DEFAULT 0,
    desc_otros_minor                INTEGER DEFAULT 0,

    monto_neto_final_minor          INTEGER NOT NULL,

    transaccion_id                  TEXT REFERENCES transacciones(id),

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(empleo_id, mes, anio)
);

-- =============================================================
-- DESCUENTOS PROGRAMADOS
-- =============================================================
CREATE TABLE IF NOT EXISTS descuentos_programados (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    concepto                        TEXT NOT NULL,

    monto_minor                     INTEGER NOT NULL,

    mes_aplicacion                  INTEGER NOT NULL
                                    CHECK(mes_aplicacion BETWEEN 1 AND 12),

    anio_aplicacion                 INTEGER NOT NULL,

    recibo_id                       TEXT REFERENCES recibos_sueldo(id),

    estado                          TEXT NOT NULL DEFAULT 'pendiente'
                                    CHECK(estado IN (
                                        'pendiente',
                                        'aplicado',
                                        'cancelado'
                                    )),

    notas                           TEXT,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- BROKERS
-- =============================================================
-- Dónde se opera un activo financiero (COCOS, BULL MARKET, IOL…). Los
-- iniciales los siembra db/schema_migrations.py (sembrar_brokers(),
-- BROKERS_INICIALES) al crear una base nueva y en la migración baseline
-- v0 → v1 — no db/seed.sql.
CREATE TABLE IF NOT EXISTS brokers (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    nombre                          TEXT NOT NULL UNIQUE,
    tipo                            TEXT,

    activo                          INTEGER NOT NULL DEFAULT 1,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sincronizado_en                 TEXT DEFAULT NULL
);

-- =============================================================
-- ACTIVOS FINANCIEROS
-- =============================================================
-- El CHECK de `tipo` es el ampliado (cedear / plazo_flex): una base con el
-- anterior la reconstruyó ampliar_tipos_ahorro() en la migración baseline.
CREATE TABLE IF NOT EXISTS activos_financieros (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    nombre                          TEXT NOT NULL,

    tipo                            TEXT NOT NULL
                                    CHECK(tipo IN (
                                        'accion',
                                        'fci',
                                        'plazo_fijo',
                                        'cripto',
                                        'otro',
                                        'cedear',
                                        'plazo_flex'
                                    )),

    moneda_id                       INTEGER NOT NULL REFERENCES monedas(id),

    activa                          INTEGER NOT NULL DEFAULT 1,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    cuenta_id                       TEXT REFERENCES cuentas(id),
    broker_id                       TEXT REFERENCES brokers(id),
    comision_compra_minor           INTEGER DEFAULT 0,
    comision_venta_minor            INTEGER DEFAULT 0,

    sincronizado_en                 TEXT DEFAULT NULL
);

-- =============================================================
-- MOVIMIENTOS DE ACTIVO
-- =============================================================
-- 'aporte' (plata que entra a un FCI / plazo, sin cantidad de unidades):
-- una base con el CHECK anterior la reconstruyó ampliar_tipos_ahorro() en la
-- migración baseline.
CREATE TABLE IF NOT EXISTS movimientos_activo (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    activo_id                       TEXT NOT NULL REFERENCES activos_financieros(id),

    tipo                            TEXT NOT NULL
                                    CHECK(tipo IN (
                                        'compra',
                                        'venta',
                                        'rendimiento',
                                        'aporte'
                                    )),

    fecha                           TEXT NOT NULL
                                    CHECK(fecha GLOB '????-??-??'),

    cantidad                        REAL,

    precio_unitario_minor           INTEGER,
    monto_total_minor               INTEGER NOT NULL,

    dolar_oficial_momento_minor     INTEGER,

    notas                           TEXT,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    transaccion_id                  TEXT REFERENCES transacciones(id),
    comision_minor                  INTEGER DEFAULT 0,

    -- Moneda de cada movimiento (sección 33). Nullable como la dejó el
    -- ALTER TABLE de la migración; SavingsService la escribe siempre.
    moneda_id                       INTEGER REFERENCES monedas(id),

    sincronizado_en                 TEXT DEFAULT NULL
);

-- =============================================================
-- OBJETIVOS DE AHORRO
-- =============================================================
CREATE TABLE IF NOT EXISTS objetivos_ahorro (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    nombre                          TEXT NOT NULL,

    monto_meta_minor                INTEGER,

    fecha_meta                      TEXT
                                    CHECK(fecha_meta IS NULL OR fecha_meta GLOB '????-??-??'),

    estado                          TEXT NOT NULL DEFAULT 'activo'
                                    CHECK(estado IN (
                                        'activo',
                                        'cumplido',
                                        'cancelado'
                                    )),

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sincronizado_en                 TEXT DEFAULT NULL
);

-- =============================================================
-- ASIGNACIONES (puente movimientos_activo <-> objetivos_ahorro)
-- =============================================================
-- NOTA: SQLite no puede expresar con un CHECK de columna la regla de negocio
-- "la suma de porcentaje de todas las asignaciones de un mismo movimiento_id
-- no puede superar 100%", porque un CHECK solo ve la fila que se está
-- insertando/actualizando, no el resto de las filas de la tabla. Esa
-- validación queda a cargo de la capa de servicio (SavingsService, fase
-- futura) antes de insertar/actualizar en esta tabla.
CREATE TABLE IF NOT EXISTS asignaciones (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    movimiento_id                   TEXT NOT NULL REFERENCES movimientos_activo(id),
    objetivo_id                     TEXT NOT NULL REFERENCES objetivos_ahorro(id),

    porcentaje                      REAL NOT NULL
                                    CHECK(porcentaje > 0 AND porcentaje <= 100),

    monto_asignado_minor            INTEGER NOT NULL,

    sincronizado_en                 TEXT DEFAULT NULL,

    UNIQUE(movimiento_id, objetivo_id)
);

-- =============================================================
-- HOGARES
-- =============================================================
CREATE TABLE IF NOT EXISTS hogares (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    codigo_invitacion               TEXT NOT NULL UNIQUE,
    nombre                          TEXT,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sincronizado_en                 TEXT DEFAULT NULL
);

-- =============================================================
-- HOGAR MIEMBROS
-- =============================================================
CREATE TABLE IF NOT EXISTS hogar_miembros (
    hogar_id                        TEXT NOT NULL,

    -- String simple, no FK a una tabla de usuarios: todavía no existe auth
    -- real (eso llega con sync/ + Supabase Auth, fase futura). Ver nota en
    -- docs/DATA_MODEL_DECISIONS.md sección 2.
    usuario_local                   TEXT NOT NULL,

    porcentaje_default              REAL,

    sincronizado_en                 TEXT DEFAULT NULL,

    PRIMARY KEY (hogar_id, usuario_local),

    FOREIGN KEY (hogar_id) REFERENCES hogares(id)
);

-- =============================================================
-- GASTOS COMPARTIDOS
-- =============================================================
CREATE TABLE IF NOT EXISTS gastos_compartidos (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    hogar_id                        TEXT NOT NULL REFERENCES hogares(id),

    -- String simple, no FK a una tabla de usuarios real todavía (ídem
    -- hogar_miembros.usuario_local). Ver nota en DATA_MODEL_DECISIONS.md #2.
    pagador                         TEXT NOT NULL,

    -- origen_tipo/origen_id apuntan de forma polimórfica a transacciones.id,
    -- compras_cuotas.id o cuotas_credito.id según el valor de origen_tipo —
    -- no se declara FOREIGN KEY porque una sola columna no puede referenciar
    -- tablas distintas según el caso. La integridad referencial la garantiza
    -- la capa de servicio (SavingsService/DebtsService-equivalente futuro),
    -- no el schema.
    origen_tipo                     TEXT NOT NULL
                                    CHECK(origen_tipo IN (
                                        'transaccion',
                                        'compra_cuotas',
                                        'cuota_credito'
                                    )),
    origen_id                       TEXT NOT NULL,

    categoria_id                    TEXT NOT NULL REFERENCES categorias(id),

    -- Monto base ya con el reintegro descontado (si aplica).
    monto_base_minor                INTEGER NOT NULL,

    coeficiente_deuda               REAL NOT NULL
                                    CHECK(coeficiente_deuda >= 0 AND coeficiente_deuda <= 100),

    -- Puede ser NEGATIVO: si el reintegro de una cuota supera su monto, la
    -- deuda se invierte y es el propio pagador quien termina debiendo, no al
    -- revés. Ver docs/DATA_MODEL_DECISIONS.md sección 2.
    monto_adeudado_minor            INTEGER NOT NULL,

    -- Para origen_tipo = 'cuota_credito', es la fecha de vencimiento de ESA
    -- cuota puntual, no la fecha de la compra original.
    fecha                           TEXT NOT NULL
                                    CHECK(fecha GLOB '????-??-??'),

    descripcion                     TEXT,

    estado                          TEXT NOT NULL DEFAULT 'pendiente'
                                    CHECK(estado IN (
                                        'pendiente',
                                        'saldado'
                                    )),

    -- Queda NULL hasta que exista sync/ con Supabase.
    sincronizado_en                 TEXT,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Lo que todavía falta pagar (sección 20): arranca igual a
    -- monto_adeudado_minor y lo reducen los gasto_compartido_pagos.
    monto_pendiente_minor           INTEGER NOT NULL DEFAULT 0
);

-- =============================================================
-- GASTO COMPARTIDO PAGOS
-- =============================================================
-- Nació como espejo de la vieja tabla deuda_pagos (eliminada en la
-- reestructuración de deudas, docs/DATA_MODEL_DECISIONS.md sección 22),
-- pero para pago parcial de gastos_compartidos (Tarea 9, Parte A — docs/PROXIMOS_PASOS.md):
-- cada pago reduce gastos_compartidos.monto_pendiente_minor.
CREATE TABLE IF NOT EXISTS gasto_compartido_pagos (
    id                               TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    gasto_compartido_id              TEXT NOT NULL REFERENCES gastos_compartidos(id),
    transaccion_id                   TEXT REFERENCES transacciones(id),

    monto_aplicado_minor             INTEGER NOT NULL,

    tipo_pago                        TEXT NOT NULL
                                     CHECK(tipo_pago IN (
                                         'transaccion',
                                         'compensacion',
                                         'ajuste'
                                     )),

    notas                            TEXT,

    fecha                            TEXT NOT NULL
                                     CHECK(fecha GLOB '????-??-??'),

    creada_en                        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sincronizado_en                  TEXT DEFAULT NULL
);

-- =============================================================
-- PRESTAMOS
-- =============================================================
CREATE TABLE IF NOT EXISTS prestamos (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    entidad                         TEXT NOT NULL,

    tipo                            TEXT NOT NULL
                                    CHECK(tipo IN (
                                        'hipotecario',
                                        'prendario',
                                        'personal',
                                        'otro'
                                    )),

    capital_original_minor          INTEGER NOT NULL,

    -- Tasa anual en basis points, mismo patrón que
    -- resumenes_tarjeta.porcentaje_impuesto_bp.
    tasa_anual_bp                   INTEGER NOT NULL,

    sistema_amortizacion            TEXT NOT NULL
                                    CHECK(sistema_amortizacion IN (
                                        'frances',
                                        'aleman'
                                    )),

    moneda_id                       INTEGER NOT NULL REFERENCES monedas(id),

    fecha_inicio                    TEXT NOT NULL
                                    CHECK(fecha_inicio GLOB '????-??-??'),

    plazo_meses                     INTEGER NOT NULL
                                    CHECK(plazo_meses > 0),

    cuenta_debito_id                TEXT REFERENCES cuentas(id),

    estado                          TEXT NOT NULL DEFAULT 'activo'
                                    CHECK(estado IN (
                                        'activo',
                                        'cancelado',
                                        'finalizado'
                                    )),

    notas                           TEXT,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- CUOTAS PRESTAMO
-- =============================================================
-- El cálculo de amortización (francesa/alemana) que genera estas filas es
-- responsabilidad de la capa de servicio (LoansService, fase futura) — el
-- schema solo almacena el resultado ya calculado, nunca calcula nada.
CREATE TABLE IF NOT EXISTS cuotas_prestamo (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    prestamo_id                     TEXT NOT NULL REFERENCES prestamos(id),

    numero_cuota                    INTEGER NOT NULL,

    mes                             INTEGER NOT NULL
                                    CHECK(mes BETWEEN 1 AND 12),
    anio                            INTEGER NOT NULL,

    monto_capital_minor             INTEGER NOT NULL,
    monto_interes_minor             INTEGER NOT NULL,
    monto_total_minor               INTEGER NOT NULL,

    estado                          TEXT NOT NULL DEFAULT 'pendiente'
                                    CHECK(estado IN (
                                        'pendiente',
                                        'pagado'
                                    )),

    fecha_pago                      TEXT
                                    CHECK(fecha_pago IS NULL OR fecha_pago GLOB '????-??-??'),

    UNIQUE(prestamo_id, numero_cuota)
);

-- =============================================================
-- SNAPSHOTS DE CIERRE DE MES (docs/DATA_MODEL_DECISIONS.md sección 21)
-- =============================================================
-- Tablas derivadas: caché recalculable de datos que ya están en otras
-- tablas (services/snapshots_service.py). Una fila por (clave, mes, anio);
-- el mes en curso nunca se guarda (siempre se calcula en vivo). Los
-- repositorios hacen el upsert con INSERT OR REPLACE sobre el UNIQUE. Id
-- entero (no UUID): no se sincronizan.
CREATE TABLE IF NOT EXISTS saldos_mensuales (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id    TEXT NOT NULL REFERENCES cuentas(id),
    moneda_id    INTEGER NOT NULL REFERENCES monedas(id),
    mes          INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio         INTEGER NOT NULL,
    saldo_minor  INTEGER NOT NULL DEFAULT 0,
    calculado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cuenta_id, moneda_id, mes, anio)
);

-- Uno por (persona, tab, moneda): el saldo de 'me_deben' y el de 'debo' con
-- la misma persona van por separado (sección 22).
CREATE TABLE IF NOT EXISTS deudas_mensuales (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entidad_persona TEXT NOT NULL,
    tab             TEXT NOT NULL CHECK(tab IN ('me_deben', 'debo')),
    moneda_id       INTEGER NOT NULL REFERENCES monedas(id),
    mes             INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio            INTEGER NOT NULL,
    monto_minor     INTEGER NOT NULL DEFAULT 0,
    calculado_en    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entidad_persona, tab, moneda_id, mes, anio)
);

CREATE TABLE IF NOT EXISTS compartidos_mensuales (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    hogar_id     TEXT NOT NULL REFERENCES hogares(id),
    pagador      TEXT NOT NULL,
    moneda_id    INTEGER NOT NULL REFERENCES monedas(id),
    mes          INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio         INTEGER NOT NULL,
    monto_minor  INTEGER NOT NULL DEFAULT 0,
    calculado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(hogar_id, pagador, moneda_id, mes, anio)
);

-- =============================================================
-- SINCRONIZACIÓN CON SUPABASE (docs/DATA_MODEL_DECISIONS.md sección 24)
-- =============================================================
-- sync_cambios: qué filas cambiaron desde la última sincronización. La
-- llenan los triggers trg_sync_* que crea preparar_sync()
-- (db/schema_migrations.py) en cada arranque — no están en este archivo.
-- sync_estado: marcas de la propia sincronización (MARCA_ESCRITURA_SYNC,
-- tablas ya bajadas, etc.).
CREATE TABLE IF NOT EXISTS sync_cambios (
    tabla         TEXT NOT NULL,
    clave         TEXT NOT NULL,
    operacion     TEXT NOT NULL CHECK(operacion IN ('guardado', 'borrado')),
    modificado_en TEXT NOT NULL,
    PRIMARY KEY (tabla, clave)
);

CREATE TABLE IF NOT EXISTS sync_estado (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

-- =============================================================
-- INDICES
-- =============================================================

CREATE INDEX IF NOT EXISTS idx_transacciones_fecha
ON transacciones(fecha);

CREATE INDEX IF NOT EXISTS idx_transacciones_cuenta
ON transacciones(cuenta_id);

CREATE INDEX IF NOT EXISTS idx_transacciones_categoria
ON transacciones(categoria_id);

CREATE INDEX IF NOT EXISTS idx_transacciones_moneda
ON transacciones(moneda_id);

CREATE INDEX IF NOT EXISTS idx_transacciones_tipo
ON transacciones(tipo_movimiento);

CREATE INDEX IF NOT EXISTS idx_autotransferencias_salida
ON autotransferencias(transaccion_salida_id);

CREATE INDEX IF NOT EXISTS idx_autotransferencias_entrada
ON autotransferencias(transaccion_entrada_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_compra
ON cuotas_credito(compra_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_resumen
ON cuotas_credito(resumen_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_estado
ON cuotas_credito(estado);

CREATE INDEX IF NOT EXISTS idx_resumenes_periodo
ON resumenes_tarjeta(anio, mes);

CREATE INDEX IF NOT EXISTS idx_recibos_periodo
ON recibos_sueldo(anio, mes);

CREATE INDEX IF NOT EXISTS idx_descuentos_periodo
ON descuentos_programados(anio_aplicacion, mes_aplicacion);

CREATE INDEX IF NOT EXISTS idx_movimientos_activo_activo
ON movimientos_activo(activo_id);

CREATE INDEX IF NOT EXISTS idx_asignaciones_movimiento
ON asignaciones(movimiento_id);

CREATE INDEX IF NOT EXISTS idx_asignaciones_objetivo
ON asignaciones(objetivo_id);

CREATE INDEX IF NOT EXISTS idx_gastos_compartidos_hogar
ON gastos_compartidos(hogar_id);

CREATE INDEX IF NOT EXISTS idx_gastos_compartidos_origen
ON gastos_compartidos(origen_tipo, origen_id);

CREATE INDEX IF NOT EXISTS idx_gastos_compartidos_estado
ON gastos_compartidos(estado);

CREATE INDEX IF NOT EXISTS idx_gasto_compartido_pagos_gasto
ON gasto_compartido_pagos(gasto_compartido_id);

CREATE INDEX IF NOT EXISTS idx_hogar_miembros_hogar
ON hogar_miembros(hogar_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_prestamo_prestamo
ON cuotas_prestamo(prestamo_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_prestamo_periodo
ON cuotas_prestamo(anio, mes);

CREATE INDEX IF NOT EXISTS idx_cuotas_prestamo_estado
ON cuotas_prestamo(estado);

CREATE INDEX IF NOT EXISTS idx_prestamos_estado
ON prestamos(estado);

-- =============================================================
-- TRIGGERS UPDATED_EN
-- =============================================================

CREATE TRIGGER IF NOT EXISTS trg_cuentas_updated
AFTER UPDATE ON cuentas
BEGIN
    UPDATE cuentas
    SET updated_en = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_transacciones_updated
AFTER UPDATE ON transacciones
BEGIN
    UPDATE transacciones
    SET updated_en = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_empleos_updated
AFTER UPDATE ON empleos
BEGIN
    UPDATE empleos
    SET updated_en = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_activos_financieros_updated
AFTER UPDATE ON activos_financieros
BEGIN
    UPDATE activos_financieros
    SET updated_en = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_objetivos_ahorro_updated
AFTER UPDATE ON objetivos_ahorro
BEGIN
    UPDATE objetivos_ahorro
    SET updated_en = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_gastos_compartidos_updated
AFTER UPDATE ON gastos_compartidos
BEGIN
    UPDATE gastos_compartidos
    SET updated_en = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_prestamos_updated
AFTER UPDATE ON prestamos
BEGIN
    UPDATE prestamos
    SET updated_en = CURRENT_TIMESTAMP
    WHERE id = NEW.id;
END;

-- =============================================================
-- VIEWS
-- =============================================================

CREATE VIEW IF NOT EXISTS vw_balance_cuentas AS
SELECT
    c.id AS cuenta_id,
    c.nombre,
    m.codigo AS moneda,

    (
        COALESCE(cs.saldo_inicial_minor, 0)
        +
        COALESCE(SUM(
            CASE
                WHEN t.tipo_movimiento = 'ingreso'
                    THEN t.monto_minor

                WHEN t.tipo_movimiento = 'egreso'
                    THEN -t.monto_minor

                ELSE t.monto_minor
            END
        ), 0)
    ) AS saldo_minor

FROM cuentas c

JOIN cuentas_saldos cs
    ON cs.cuenta_id = c.id

JOIN monedas m
    ON m.id = cs.moneda_id

LEFT JOIN transacciones t
    ON t.cuenta_id = c.id
    AND t.moneda_id = m.id
    AND t.deleted_at IS NULL

GROUP BY c.id, m.id;

CREATE VIEW IF NOT EXISTS vw_cuotas_pendientes AS
SELECT
    cc.concepto,
    qc.numero_cuota,
    qc.anio_proyectado,
    qc.mes_proyectado,
    qc.monto_cuota_minor,
    qc.estado
FROM cuotas_credito qc
JOIN compras_cuotas cc
    ON cc.id = qc.compra_id
WHERE qc.estado != 'pagado';

-- Saldo neto por hogar: un único número (ver DATA_MODEL_DECISIONS.md #2 y
-- #20). Positivo = al pagador le deben plata en conjunto (SUM de
-- monto_pendiente_minor de sus gastos_compartidos pendientes); negativo =
-- el pagador termina debiendo en conjunto. Solo considera
-- gastos_compartidos.estado = 'pendiente'.
--
-- Suma monto_pendiente_minor, NO monto_adeudado_minor (cambiado en Tarea 9
-- Parte A, corrección posterior): un pago parcial vía
-- SharedExpensesService.aplicar_pago() reduce monto_pendiente_minor pero
-- deja monto_adeudado_minor sin tocar (es el monto ORIGINAL de la deuda,
-- inmutable — ver docstring de gastos_compartidos_repository.py) — sumar
-- monto_adeudado_minor acá ignoraba los pagos parciales ya aplicados hasta
-- que el gasto llegaba a 'saldado'. Sin pagos parciales,
-- monto_pendiente_minor arranca igual a monto_adeudado_minor (ver
-- GastosCompartidosRepository.crear()), así que este cambio no altera el
-- resultado en ningún escenario que no use aplicar_pago() todavía.
--
-- Una base que tenía la definición vieja (SUM(monto_adeudado_minor)) la
-- recibió corregida en la migración baseline (db/schema_v0.sql hace DROP +
-- CREATE). Si una vista cambia de definición en el futuro, va en una
-- migración numerada: este archivo solo corre en bases nuevas.
CREATE VIEW IF NOT EXISTS vw_saldo_neto_hogar AS
SELECT
    gc.hogar_id,
    SUM(gc.monto_pendiente_minor) AS saldo_neto_minor
FROM gastos_compartidos gc
WHERE gc.estado = 'pendiente'
GROUP BY gc.hogar_id;
