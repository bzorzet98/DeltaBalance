
-- =============================================================
-- DeltaBalance — schema_v2.sql
-- SQLite 3 — Financial-grade schema
-- =============================================================

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
    updated_en          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- CUENTAS SALDOS
-- =============================================================
CREATE TABLE IF NOT EXISTS cuentas_saldos (
    cuenta_id               TEXT NOT NULL,
    moneda_id               INTEGER NOT NULL,

    saldo_inicial_minor     INTEGER NOT NULL DEFAULT 0,

    visible                 INTEGER NOT NULL DEFAULT 1,

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
-- dos tablas se crean en db/schema_migrations.py MIGRACIONES_TABLA —
-- docs/DATA_MODEL_DECISIONS.md secciones 23 y 29).
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

    FOREIGN KEY (cuenta_id) REFERENCES cuentas(id),

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
    updated_en                     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Columnas agregadas a compras_cuotas para GASTOS COMPARTIDOS
-- (monto_reintegro_minor, modo_deuda — ver docs/DATA_MODEL_DECISIONS.md
-- sección 2). NO se agregan acá como ALTER TABLE: SQLite no soporta
-- "ALTER TABLE ... ADD COLUMN IF NOT EXISTS" (a diferencia de CREATE TABLE/
-- INDEX/TRIGGER/VIEW, que sí soportan IF NOT EXISTS), así que un ALTER TABLE
-- suelto acá rompería schema.sql como DDL idempotente — fallaría con
-- "duplicate column name" la segunda vez que se aplicara sobre una base que
-- ya tiene la columna. Estas dos columnas se agregan vía
-- db/schema_migrations.py, que primero chequea PRAGMA table_info antes de
-- alterar. Cualquier columna nueva sobre una tabla existente va ahí, nunca
-- como ALTER TABLE suelto en este archivo; las tablas nuevas sí siguen
-- yendo acá con CREATE TABLE IF NOT EXISTS como siempre.
--
-- compras_cuotas.es_cargo_extra (mismo mecanismo): 1 = cargo/reintegro del
-- resumen de la tarjeta (impuesto, recargo, ajuste) cargado con una
-- categoría especial de TARJETA DE CRÉDITO — una "compra" de 1 cuota cuya
-- cuota ya está en el resumen de su mes. Ver docs/DATA_MODEL_DECISIONS.md
-- sección 28.

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

    UNIQUE(compra_id, numero_cuota)
);

-- =============================================================
-- RESUMEN CARGOS EXTRA
-- =============================================================
-- Cargos que componen monto_impuestos_minor de un resumen al cerrarlo
-- (impuestos, recargos, ajustes). Ver docs/DATA_MODEL_DECISIONS.md sobre el
-- rediseño de cómo se calculan los totales de resumenes_tarjeta.
--
-- DEPRECADA (docs/DATA_MODEL_DECISIONS.md sección 28): los cargos extra se
-- guardan en compras_cuotas con es_cargo_extra = 1 (esta tabla no se
-- sincroniza). db/schema_migrations.py migrar_cargos_extra_a_compras() mueve
-- las filas existentes; solo quedan acá las que no se pudieron mover (tipo
-- 'otro', sin categoría especial). Se sigue creando para no romper bases ni
-- scripts que todavía la leen.
CREATE TABLE IF NOT EXISTS resumen_cargos_extra (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    resumen_id                      TEXT NOT NULL REFERENCES resumenes_tarjeta(id),

    concepto                        TEXT NOT NULL,

    tipo                            TEXT NOT NULL
                                    CHECK(tipo IN (
                                        'impuesto',
                                        'recargo',
                                        'ajuste',
                                        'otro'
                                    )),

    monto_minor                     INTEGER NOT NULL,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
-- en db/schema_migrations.py (reestructurar_deudas()), que corre DESPUÉS de
-- este archivo. Por eso acá no hay índices, triggers ni vistas sobre las
-- columnas nuevas: en una base todavía sin convertir, un CREATE INDEX
-- sobre `tab` fallaría antes de llegar a la migración.
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
    creada_en       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- PRESUPUESTOS
-- =============================================================
CREATE TABLE IF NOT EXISTS presupuestos (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    categoria_id                    TEXT NOT NULL,
    moneda_id                       INTEGER NOT NULL,

    mes                             INTEGER NOT NULL
                                    CHECK(mes BETWEEN 1 AND 12),

    anio                            INTEGER NOT NULL,

    monto_estimado_minor            INTEGER NOT NULL,
    monto_ejecutado_minor           INTEGER NOT NULL DEFAULT 0,

    es_recurrente                   INTEGER DEFAULT 0,

    notas                           TEXT,

    FOREIGN KEY (categoria_id) REFERENCES categorias(id),
    FOREIGN KEY (moneda_id) REFERENCES monedas(id),
    UNIQUE(categoria_id, mes, anio)
);

-- =============================================================
-- INGRESOS PROYECTADOS
-- =============================================================
CREATE TABLE IF NOT EXISTS ingresos_proyectados (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    concepto                        TEXT NOT NULL,

    mes                             INTEGER NOT NULL
                                    CHECK(mes BETWEEN 1 AND 12),

    anio                            INTEGER NOT NULL,

    monto_estimado_minor            INTEGER NOT NULL,
    monto_percibido_minor           INTEGER NOT NULL DEFAULT 0,

    moneda_id                       INTEGER NOT NULL,

    estado                          TEXT NOT NULL DEFAULT 'pendiente'
                                    CHECK(estado IN (
                                        'pendiente',
                                        'cobrado',
                                        'parcial'
                                    )),

    FOREIGN KEY (moneda_id) REFERENCES monedas(id)
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
-- TIPOS DE CAMBIO
-- =============================================================
CREATE TABLE IF NOT EXISTS tipos_cambio (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    fecha                           TEXT NOT NULL
                                    CHECK(fecha GLOB '????-??-??'),

    moneda_origen_id               INTEGER NOT NULL,
    moneda_destino_id              INTEGER NOT NULL,

    tasa_minor                     INTEGER NOT NULL,

    fuente                         TEXT,

    FOREIGN KEY (moneda_origen_id) REFERENCES monedas(id),
    FOREIGN KEY (moneda_destino_id) REFERENCES monedas(id),

    UNIQUE(fecha, moneda_origen_id, moneda_destino_id)
);

-- =============================================================
-- BROKERS
-- =============================================================
-- Dónde se opera un activo financiero (COCOS, BULL MARKET, IOL…). Los
-- iniciales los siembra db/schema_migrations.py (BROKERS_INICIALES) en toda
-- base, nueva o existente — no db/seed.sql, que solo corre en bases nuevas.
CREATE TABLE IF NOT EXISTS brokers (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    nombre                          TEXT NOT NULL UNIQUE,
    tipo                            TEXT,

    activo                          INTEGER NOT NULL DEFAULT 1,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- ACTIVOS FINANCIEROS
-- =============================================================
-- broker_id, comision_compra_minor, comision_venta_minor y cuenta_id se
-- agregan vía db/schema_migrations.py. Una base que ya tenía la tabla con
-- el CHECK de `tipo` anterior (sin cedear/plazo_flex) la reconstruye
-- ampliar_tipos_ahorro() de ese mismo módulo.
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
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- MOVIMIENTOS DE ACTIVO
-- =============================================================
-- transaccion_id y comision_minor se agregan vía db/schema_migrations.py.
-- 'aporte' (plata que entra a un FCI / plazo, sin cantidad de unidades):
-- una base con el CHECK anterior la reconstruye ampliar_tipos_ahorro().
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

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

    UNIQUE(movimiento_id, objetivo_id)
);

-- =============================================================
-- ACTIVO OBJETIVOS (reparto vigente de un activo entre objetivos)
-- =============================================================
-- DEPRECATED (docs/DATA_MODEL_DECISIONS.md sección 31): los objetivos son
-- de cada movimiento (`asignaciones`), ya no hay un reparto fijo por
-- activo. Nada la lee ni la escribe (salvo borrar las filas de un objetivo
-- que se elimina, por la FK); queda por las bases que ya tienen filas.
-- Qué porcentaje de un activo es de cada objetivo de ahorro. Cada
-- movimiento nuevo del activo generaba sus `asignaciones` con estos
-- porcentajes (SavingsService). Misma limitación que asignaciones: la suma
-- por activo_id (<= 100) no entra en un CHECK — la validaba el service.
CREATE TABLE IF NOT EXISTS activo_objetivos (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    activo_id                       TEXT NOT NULL REFERENCES activos_financieros(id),
    objetivo_id                     TEXT NOT NULL REFERENCES objetivos_ahorro(id),

    porcentaje                      REAL NOT NULL
                                    CHECK(porcentaje > 0 AND porcentaje <= 100),

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(activo_id, objetivo_id)
);

-- =============================================================
-- HOGARES
-- =============================================================
CREATE TABLE IF NOT EXISTS hogares (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    codigo_invitacion               TEXT NOT NULL UNIQUE,
    nombre                          TEXT,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- GASTO COMPARTIDO PAGOS
-- =============================================================
-- Nació como espejo de la vieja tabla deuda_pagos (eliminada en la
-- reestructuración de deudas, docs/DATA_MODEL_DECISIONS.md sección 22),
-- pero para pago parcial de gastos_compartidos (Tarea 9, Parte A — docs/PROXIMOS_PASOS.md). La
-- columna gastos_compartidos.monto_pendiente_minor que este pago reduce se
-- agrega vía db/schema_migrations.py, no acá (mismo motivo que el resto de
-- las columnas de esa lista: gastos_compartidos ya es una tabla existente).
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

    creada_en                        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
-- INDICES DE INFLACION
-- =============================================================
CREATE TABLE IF NOT EXISTS indices_inflacion (
    id                              TEXT PRIMARY KEY NOT NULL DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('89ab', 1 + (random() & 3), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),

    mes                             INTEGER NOT NULL
                                    CHECK(mes BETWEEN 1 AND 12),
    anio                            INTEGER NOT NULL,

    -- Número índice, no porcentaje. Convención: base 100 en un mes de
    -- referencia arbitrario, o el valor directo del índice de precios que se
    -- cargue (ej. IPC de INDEC). El ajuste de series históricas a moneda
    -- constante (dividir/multiplicar por el índice correspondiente) es
    -- lógica de servicio o de lab/, no del schema.
    valor_indice                    REAL NOT NULL,

    fuente                          TEXT,

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(mes, anio)
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

CREATE INDEX IF NOT EXISTS idx_resumen_cargos_extra_resumen
ON resumen_cargos_extra(resumen_id);

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

CREATE INDEX IF NOT EXISTS idx_activo_objetivos_objetivo
ON activo_objetivos(objetivo_id);

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

CREATE INDEX IF NOT EXISTS idx_indices_inflacion_periodo
ON indices_inflacion(anio, mes);

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
-- DROP VIEW IF EXISTS + CREATE VIEW IF NOT EXISTS (en vez de solo el
-- segundo, como el resto de las vistas de este archivo): CREATE VIEW IF
-- NOT EXISTS NO reemplaza una vista que ya existe con una definición
-- vieja (a diferencia de CREATE TABLE, donde el problema es al revés —
-- una tabla existente nunca pierde columnas por reaplicar el CREATE, por
-- eso las columnas nuevas van por db/schema_migrations.py). Como
-- db/schema.sql se reaplica completo en cada DatabaseManager.inicializar()
-- (no solo la primera vez), una base ya inicializada con la definición
-- vieja (SUM(monto_adeudado_minor)) se hubiera quedado con esa definición
-- para siempre sin el DROP. Las demás vistas de este archivo no lo
-- necesitan HOY porque ninguna cambió de definición todavía — si en el
-- futuro alguna otra vista necesita una definición nueva, va a necesitar
-- el mismo patrón DROP+CREATE, no alcanza con editar el SELECT acá.
DROP VIEW IF EXISTS vw_saldo_neto_hogar;
CREATE VIEW IF NOT EXISTS vw_saldo_neto_hogar AS
SELECT
    gc.hogar_id,
    SUM(gc.monto_pendiente_minor) AS saldo_neto_minor
FROM gastos_compartidos gc
WHERE gc.estado = 'pendiente'
GROUP BY gc.hogar_id;
