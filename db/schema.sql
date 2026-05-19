
-- =============================================================
-- DeltaBalance — schema_v2.sql
-- SQLite 3 — Financial-grade schema
-- =============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

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
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre              TEXT NOT NULL UNIQUE,

    tipo                TEXT NOT NULL
                        CHECK(tipo IN (
                            'debito',
                            'credito',
                            'efectivo',
                            'crypto',
                            'inversion'
                        )),

    cuenta_pago_id      INTEGER REFERENCES cuentas(id),

    activa              INTEGER NOT NULL DEFAULT 1,
    notas               TEXT,

    creada_en           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- =============================================================
-- CUENTAS SALDOS
-- =============================================================
CREATE TABLE IF NOT EXISTS cuentas_saldos (
    cuenta_id               INTEGER NOT NULL,
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
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,

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
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,

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
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,

    fecha                       TEXT NOT NULL
                                CHECK(fecha GLOB '????-??-??'),

    concepto                    TEXT NOT NULL,

    cuenta_id                   INTEGER NOT NULL,
    categoria_id                INTEGER NOT NULL,
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
-- RESUMENES TARJETA
-- =============================================================
CREATE TABLE IF NOT EXISTS resumenes_tarjeta (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    cuenta_id                       INTEGER NOT NULL,

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
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    fecha_compra                   TEXT NOT NULL
                                    CHECK(fecha_compra GLOB '????-??-??'),

    concepto                       TEXT NOT NULL,

    cuenta_id                      INTEGER NOT NULL REFERENCES cuentas(id),
    categoria_id                   INTEGER NOT NULL REFERENCES categorias(id),
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

-- =============================================================
-- CUOTAS CREDITO
-- =============================================================
CREATE TABLE IF NOT EXISTS cuotas_credito (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    compra_id                       INTEGER NOT NULL REFERENCES compras_cuotas(id),
    resumen_id                      INTEGER REFERENCES resumenes_tarjeta(id),

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
-- DEUDAS
-- =============================================================
CREATE TABLE IF NOT EXISTS deudas (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    entidad_persona                TEXT NOT NULL,

    tipo                           TEXT NOT NULL
                                   CHECK(tipo IN (
                                        'a_favor',
                                        'en_contra'
                                   )),

    monto_original_minor           INTEGER NOT NULL,
    monto_pendiente_minor          INTEGER NOT NULL,

    moneda_id                      INTEGER NOT NULL,

    fecha_inicio                   TEXT NOT NULL
                                   CHECK(fecha_inicio GLOB '????-??-??'),

    fecha_vencimiento              TEXT
                                   CHECK(
                                        fecha_vencimiento IS NULL OR
                                        fecha_vencimiento GLOB '????-??-??'
                                   ),

    estado                         TEXT NOT NULL DEFAULT 'activa'
                                   CHECK(estado IN (
                                        'activa',
                                        'saldada',
                                        'incobrable'
                                   )),

    origen_tipo                    TEXT DEFAULT 'manual',
    origen_id                      INTEGER,

    notas                          TEXT,

    creada_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (moneda_id) REFERENCES monedas(id)
);

-- =============================================================
-- DEUDA PAGOS
-- =============================================================
CREATE TABLE IF NOT EXISTS deuda_pagos (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    deuda_id                        INTEGER NOT NULL,
    transaccion_id                  INTEGER,
    concepto                        TEXT,   -- 'Paid back half', 'Cash at dinner', etc.

    monto_applied_minor             INTEGER NOT NULL,

    tipo_pago                       TEXT NOT NULL DEFAULT 'transaccion'
                                    CHECK(tipo_pago IN (
                                        'transaccion',
                                        'compensacion',
                                        'ajuste'
                                    )),

    notas                           TEXT,

    fecha                           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (deuda_id) REFERENCES deudas(id),
    FOREIGN KEY (transaccion_id) REFERENCES transacciones(id)
);

-- =============================================================
-- PRESUPUESTOS
-- =============================================================
CREATE TABLE IF NOT EXISTS presupuestos (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    categoria_id                    INTEGER NOT NULL,
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
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

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
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    empleo_id                       INTEGER NOT NULL REFERENCES empleos(id),

    mes                             INTEGER NOT NULL,
    anio                            INTEGER NOT NULL,

    sueldo_bruto_minor              INTEGER NOT NULL,

    desc_jubilacion_minor           INTEGER NOT NULL,
    desc_obra_social_minor          INTEGER NOT NULL,
    desc_copagos_os_minor           INTEGER DEFAULT 0,
    desc_otros_minor                INTEGER DEFAULT 0,

    monto_neto_final_minor          INTEGER NOT NULL,

    transaccion_id                  INTEGER REFERENCES transacciones(id),

    creada_en                       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_en                      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(empleo_id, mes, anio)
);

-- =============================================================
-- DESCUENTOS PROGRAMADOS
-- =============================================================
CREATE TABLE IF NOT EXISTS descuentos_programados (
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

    concepto                        TEXT NOT NULL,

    monto_minor                     INTEGER NOT NULL,

    mes_aplicacion                  INTEGER NOT NULL
                                    CHECK(mes_aplicacion BETWEEN 1 AND 12),

    anio_aplicacion                 INTEGER NOT NULL,

    recibo_id                       INTEGER REFERENCES recibos_sueldo(id),

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
    id                              INTEGER PRIMARY KEY AUTOINCREMENT,

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

CREATE INDEX IF NOT EXISTS idx_cuotas_compra
ON cuotas_credito(compra_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_resumen
ON cuotas_credito(resumen_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_estado
ON cuotas_credito(estado);

CREATE INDEX IF NOT EXISTS idx_resumenes_periodo
ON resumenes_tarjeta(anio, mes);

CREATE INDEX IF NOT EXISTS idx_deudas_estado
ON deudas(estado);

CREATE INDEX IF NOT EXISTS idx_deuda_pagos_deuda
ON deuda_pagos(deuda_id);

CREATE INDEX IF NOT EXISTS idx_recibos_periodo
ON recibos_sueldo(anio, mes);

CREATE INDEX IF NOT EXISTS idx_descuentos_periodo
ON descuentos_programados(anio_aplicacion, mes_aplicacion);

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

CREATE TRIGGER IF NOT EXISTS trg_deudas_updated
AFTER UPDATE ON deudas
BEGIN
    UPDATE deudas
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

CREATE VIEW IF NOT EXISTS vw_deudas_activas AS
SELECT
    d.id,
    d.entidad_persona,
    d.tipo,
    d.monto_pendiente_minor,
    m.codigo AS moneda,
    d.estado
FROM deudas d
JOIN monedas m
    ON m.id = d.moneda_id
WHERE d.estado = 'activa';

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
