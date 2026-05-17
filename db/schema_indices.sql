-- =============================================================
-- DeltaBalance — schema.sql
-- Motor: SQLite 3
-- =============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- =============================================================
-- 0. MONEDAS
-- =============================================================
CREATE TABLE IF NOT EXISTS monedas (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo  TEXT NOT NULL UNIQUE -- 'ARS', 'USD', 'USDT', 'BRL'
);

-- =============================================================
-- 3. CATEGORIAS
-- Tabla normalizada de categorias y subcategorias.
-- tipo: 'ingreso' | 'egreso' | 'movimiento'
-- =============================================================
CREATE TABLE IF NOT EXISTS categorias (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    categoria_principal TEXT    NOT NULL,
    subcategoria        TEXT    NOT NULL,
    tipo                TEXT    NOT NULL
                        CHECK(tipo IN ('ingreso','egreso','movimiento')),
    UNIQUE(categoria_principal, subcategoria)
);


-- =============================================================
-- 1. CUENTAS
-- =============================================================
CREATE TABLE IF NOT EXISTS cuentas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT    NOT NULL UNIQUE,
    tipo            TEXT    NOT NULL 
                    CHECK(tipo IN ('debito','credito','efectivo','crypto','inversion')),

    cuenta_pago_id  INTEGER REFERENCES cuentas(id), 
    
    activa          INTEGER NOT NULL DEFAULT 1,
    notas           TEXT,
    creada_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);


-- =============================================================
-- 13. EMPLEOS / CONFIGURACIÓN DE INGRESOS
-- Define las reglas de descuento para cada fuente de ingreso.
-- =============================================================
CREATE TABLE IF NOT EXISTS empleos (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre_empresa      TEXT    NOT NULL,
    puesto              TEXT,
    moneda_id           INTEGER NOT NULL REFERENCES monedas(id),
    
    -- Variables fijas por contrato
    porcentaje_jubilacion REAL    DEFAULT 11.0, -- %
    porcentaje_obra_social REAL    DEFAULT 3.0,  -- %
    porcentaje_gremio     REAL    DEFAULT 0.0,  -- %
    tope_copago_os        REAL    DEFAULT 0.0,  -- Monto máximo que te pueden descontar de OS
    
    activa              INTEGER DEFAULT 1,    -- 0 si ya no trabajás ahí
    creada_en           TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- =============================================================
-- 2. CUENTAS_SALDOS
-- Esta tabla permite que una cuenta (ej. Mercado Pago) tenga 
-- múltiples "bolsillos" de diferentes monedas (ARS, USD, etc.)
-- =============================================================
CREATE TABLE IF NOT EXISTS cuentas_saldos (
    cuenta_id       INTEGER NOT NULL,
    moneda_id       INTEGER NOT NULL,
    saldo_inicial   REAL    NOT NULL DEFAULT 0,
    visible         INTEGER NOT NULL DEFAULT 1, -- Para ocultar cajones que ya no usas
    PRIMARY KEY (cuenta_id, moneda_id),
    FOREIGN KEY (cuenta_id) REFERENCES cuentas(id), -- Eliminamos el CASCADE
    FOREIGN KEY (moneda_id) REFERENCES monedas(id)
);







-- =============================================================
-- 4. transacciones
-- Cash flow real. Solo lo que efectivamente ocurrió en una cuenta.
-- Monto positivo = entra, negativo = sale.
-- NO incluye cuotas individuales — solo el pago total del resumen.
-- =============================================================
CREATE TABLE IF NOT EXISTS transacciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha           TEXT    NOT NULL,                 -- Formato: 'YYYY-MM-DD'
    concepto        TEXT    NOT NULL,
    cuenta_id       INTEGER NOT NULL,
    categoria_id    INTEGER NOT NULL,
    moneda_id       INTEGER NOT NULL,                 -- Cambio: Referencia a tabla monedas
    monto           REAL    NOT NULL,                 -- (+) ingreso, (-) egreso
    tag             TEXT,                             -- Ejemplo: 'Sueldo Mayo'
    notas           TEXT,
    creada_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    
    -- Definimos las relaciones (Foreign Keys) al final para mayor claridad
    FOREIGN KEY (cuenta_id)    REFERENCES cuentas(id),
    FOREIGN KEY (categoria_id) REFERENCES categorias(id),
    FOREIGN KEY (moneda_id)    REFERENCES monedas(id)
);


-- =============================================================
-- 5. COMPRAS EN CUOTAS
-- El hecho de la compra. De aquí se generan las filas de cuotas_credito.
-- cuenta_id = la tarjeta de crédito usada.
-- =============================================================
CREATE TABLE IF NOT EXISTS compras_cuotas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_compra    TEXT    NOT NULL,
    concepto        TEXT    NOT NULL,                 -- 'Lavarropas Whirlpool'
    cuenta_id       INTEGER NOT NULL REFERENCES cuentas(id),
    categoria_id    INTEGER NOT NULL REFERENCES categorias(id),
    monto_total     REAL    NOT NULL,
    total_cuotas    INTEGER NOT NULL DEFAULT 1,
    monto_por_cuota REAL    NOT NULL,                 -- monto_total / total_cuotas (puede diferir por intereses)
    moneda_id          INTEGER    NOT NULL REFERENCES monedas(id),
    estado          TEXT    NOT NULL DEFAULT 'activa'
                    CHECK(estado IN ('activa','cancelada','completada')),
    notas           TEXT,
    creada_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);


-- =============================================================
-- 7. RESÚMENES DE TARJETA
-- Consolida el pago mensual y los costos impositivos/administrativos.
-- =============================================================
CREATE TABLE IF NOT EXISTS resumenes_tarjeta (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id           INTEGER NOT NULL,
    mes                 INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio                INTEGER NOT NULL,
    
    -- Totales que vienen del banco
    monto_consumos      REAL NOT NULL DEFAULT 0, -- Suma de cuotas + consumos un pago
    monto_impuestos     REAL NOT NULL DEFAULT 0, -- El valor real en plata de sellos/impuestos
    
    -- Tu idea del coeficiente
    porcentaje_impuesto REAL NOT NULL DEFAULT 0, -- Ejemplo: 1.2 (para 1.2%)
    
    monto_total_pagado  REAL NOT NULL,           -- monto_consumos + monto_impuestos
    
    fecha_pago          TEXT,                    -- YYYY-MM-DD
    estado              TEXT NOT NULL DEFAULT 'abierto' 
                        CHECK(estado IN ('abierto', 'cerrado', 'pagado')),
                        
    FOREIGN KEY (cuenta_id) REFERENCES cuentas(id),
    UNIQUE(cuenta_id, mes, anio) -- Evita duplicar el resumen del mismo mes
);


-- =============================================================
-- 8. DEUDAS (Préstamos y Compromisos)
-- Maneja tanto lo que te deben (Activo) como lo que debés (Pasivo).
-- =============================================================
CREATE TABLE IF NOT EXISTS deudas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entidad_persona TEXT    NOT NULL,
    tipo            TEXT    NOT NULL 
                    CHECK(tipo IN ('a_favor', 'en_contra')),
    
    -- (+) me deben (Activo), (-) debo (Pasivo)
    monto_original  REAL    NOT NULL,
    monto_pendiente REAL    NOT NULL, 
    
    moneda_id       INTEGER NOT NULL,
    fecha_inicio    TEXT    NOT NULL,
    fecha_vencimiento TEXT,
    estado          TEXT    NOT NULL DEFAULT 'activa'
                    CHECK(estado IN ('activa', 'saldada', 'incobrable')),
    
    origen_tipo     TEXT    DEFAULT 'manual',
    origen_id       INTEGER,
    notas           TEXT,
    creada_en       TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
    
    FOREIGN KEY (moneda_id) REFERENCES monedas(id),

    -- REGLA DE ORO: Validamos que el signo coincida con el tipo
    CONSTRAINT check_signos_deuda CHECK (
        (tipo = 'a_favor' AND monto_original > 0 AND monto_pendiente >= 0) OR
        (tipo = 'en_contra' AND monto_original < 0 AND monto_pendiente <= 0)
    )
);



-- =============================================================
-- 6. CUOTAS DE CRÉDITO
-- Las N cuotas generadas por cada compra.
-- mes_proyectado: cuando DEBERIA caer en el resumen.
-- mes_real_pago:  cuando REALMENTE apareció (puede diferir).
-- =============================================================
CREATE TABLE IF NOT EXISTS cuotas_credito (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    compra_id           INTEGER NOT NULL REFERENCES compras_cuotas(id),
    resumen_id          INTEGER REFERENCES resumenes_tarjeta(id),
    numero_cuota        INTEGER NOT NULL,              -- 1, 2, 3 ... N
    mes_proyectado      INTEGER NOT NULL CHECK(mes_proyectado BETWEEN 1 AND 12),
    anio_proyectado     INTEGER NOT NULL,
    mes_real_pago       INTEGER         CHECK(mes_real_pago BETWEEN 1 AND 12),
    anio_real_pago      INTEGER,
    monto_cuota         REAL    NOT NULL,
    estado              TEXT    NOT NULL DEFAULT 'pendiente'
                        CHECK(estado IN ('pendiente','en_resumen','pagado','omitido')),
    notas               TEXT,                          -- 'cuota duplicada en jun, omitida en may'
    UNIQUE(compra_id, numero_cuota)
);

-- =============================================================
-- 9. DEUDA_PAGOS (Vinculación Transacción <-> Deuda)
-- Permite saber qué transacción pagó qué parte de qué deuda.
-- =============================================================
CREATE TABLE IF NOT EXISTS deuda_pagos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deuda_id        INTEGER NOT NULL,
    transaccion_id  INTEGER,
    
    -- Este monto "neutraliza" la deuda. 
    -- Si la deuda es (-), el pago es (+). Si la deuda es (+), el pago es (-).
    monto_applied   REAL    NOT NULL, 
    
    tipo_pago       TEXT    NOT NULL DEFAULT 'transaccion'
                    CHECK(tipo_pago IN ('transaccion', 'compensacion', 'ajuste')),
    notas           TEXT,
    fecha           TEXT    NOT NULL DEFAULT (date('now','localtime')),
    
    FOREIGN KEY (deuda_id)       REFERENCES deudas(id),
    FOREIGN KEY (transaccion_id) REFERENCES transacciones(id)
);

-- =============================================================
-- 10. PRESUPUESTOS (Gastos previstos y fijos)
-- =============================================================
CREATE TABLE IF NOT EXISTS presupuestos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    categoria_id    INTEGER NOT NULL,
    mes             INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio            INTEGER NOT NULL,
    monto_estimado  REAL    NOT NULL,                 -- (-) Lo que calculo que voy a gastar
    monto_ejecutado REAL    NOT NULL DEFAULT 0,       -- (-) Lo que ya gasté (se llena vía Python)
    moneda_id       INTEGER NOT NULL,
    es_recurrente   INTEGER DEFAULT 0,                -- 1 si se repite todos los meses
    notas           TEXT,                             -- 'Aumento de internet en junio'
    
    FOREIGN KEY (categoria_id) REFERENCES categorias(id),
    FOREIGN KEY (moneda_id)    REFERENCES monedas(id),
    UNIQUE(categoria_id, mes, anio) -- Un solo presupuesto por categoría al mes
);

-- =============================================================
-- 11. INGRESOS_PROYECTADOS (Lo que espero cobrar)
-- =============================================================
CREATE TABLE IF NOT EXISTS ingresos_proyectados (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    concepto        TEXT    NOT NULL,
    mes             INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio            INTEGER NOT NULL,
    monto_estimado  REAL    NOT NULL,                 -- (+) Lo que espero cobrar
    monto_percibido REAL    NOT NULL DEFAULT 0,       -- (+) Lo que ya entró a la cuenta
    moneda_id       INTEGER NOT NULL,
    estado          TEXT    NOT NULL DEFAULT 'pendiente' 
                    CHECK(estado IN ('pendiente', 'cobrado', 'parcial')),
    
    FOREIGN KEY (moneda_id) REFERENCES monedas(id)
);

-- =============================================================
-- 12. RECIBOS_SUELDO (Versión Multi-empleo)
-- =============================================================
CREATE TABLE IF NOT EXISTS recibos_sueldo (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    empleo_id           INTEGER NOT NULL REFERENCES empleos(id),
    mes                 INTEGER NOT NULL,
    anio                INTEGER NOT NULL,
    
    sueldo_bruto        REAL    NOT NULL,
    
    -- Guardamos los montos calculados (no el % ) para auditoría histórica
    desc_jubilacion     REAL    NOT NULL, 
    desc_obra_social    REAL    NOT NULL,
    desc_copagos_os     REAL    DEFAULT 0, -- Aquí entra la lógica de la deuda acumulada
    desc_otros          REAL    DEFAULT 0,
    
    monto_neto_final    REAL    NOT NULL,
    transaccion_id      INTEGER REFERENCES transacciones(id),
    
    UNIQUE(empleo_id, mes, anio) -- Evita duplicar recibos del mismo empleo/mes
);







-- =============================================================
-- VISTAS
-- =============================================================



-- =============================================================
-- INDICES
-- =============================================================

-- TRANSACCIONES
CREATE INDEX IF NOT EXISTS idx_transacciones_fecha
ON transacciones(fecha);

CREATE INDEX IF NOT EXISTS idx_transacciones_cuenta
ON transacciones(cuenta_id);

CREATE INDEX IF NOT EXISTS idx_transacciones_categoria
ON transacciones(categoria_id);

CREATE INDEX IF NOT EXISTS idx_transacciones_moneda
ON transacciones(moneda_id);

CREATE INDEX IF NOT EXISTS idx_transacciones_tag
ON transacciones(tag);

-- CUENTAS_SALDOS
CREATE INDEX IF NOT EXISTS idx_cuentas_saldos_moneda
ON cuentas_saldos(moneda_id);

-- COMPRAS EN CUOTAS
CREATE INDEX IF NOT EXISTS idx_compras_cuenta
ON compras_cuotas(cuenta_id);

CREATE INDEX IF NOT EXISTS idx_compras_categoria
ON compras_cuotas(categoria_id);

CREATE INDEX IF NOT EXISTS idx_compras_estado
ON compras_cuotas(estado);

-- CUOTAS
CREATE INDEX IF NOT EXISTS idx_cuotas_compra
ON cuotas_credito(compra_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_resumen
ON cuotas_credito(resumen_id);

CREATE INDEX IF NOT EXISTS idx_cuotas_estado
ON cuotas_credito(estado);

CREATE INDEX IF NOT EXISTS idx_cuotas_periodo
ON cuotas_credito(anio_proyectado, mes_proyectado);

-- RESUMENES TARJETA
CREATE INDEX IF NOT EXISTS idx_resumenes_cuenta
ON resumenes_tarjeta(cuenta_id);

CREATE INDEX IF NOT EXISTS idx_resumenes_periodo
ON resumenes_tarjeta(anio, mes);

CREATE INDEX IF NOT EXISTS idx_resumenes_estado
ON resumenes_tarjeta(estado);

-- DEUDAS
CREATE INDEX IF NOT EXISTS idx_deudas_estado
ON deudas(estado);

CREATE INDEX IF NOT EXISTS idx_deudas_tipo
ON deudas(tipo);

CREATE INDEX IF NOT EXISTS idx_deudas_moneda
ON deudas(moneda_id);

-- DEUDA PAGOS
CREATE INDEX IF NOT EXISTS idx_deuda_pagos_deuda
ON deuda_pagos(deuda_id);

CREATE INDEX IF NOT EXISTS idx_deuda_pagos_transaccion
ON deuda_pagos(transaccion_id);

CREATE INDEX IF NOT EXISTS idx_deuda_pagos_fecha
ON deuda_pagos(fecha);

-- PRESUPUESTOS
CREATE INDEX IF NOT EXISTS idx_presupuestos_periodo
ON presupuestos(anio, mes);

CREATE INDEX IF NOT EXISTS idx_presupuestos_categoria
ON presupuestos(categoria_id);

-- INGRESOS PROYECTADOS
CREATE INDEX IF NOT EXISTS idx_ingresos_proyectados_periodo
ON ingresos_proyectados(anio, mes);

CREATE INDEX IF NOT EXISTS idx_ingresos_proyectados_estado
ON ingresos_proyectados(estado);

-- EMPLEOS
CREATE INDEX IF NOT EXISTS idx_empleos_activa
ON empleos(activa);

-- RECIBOS SUELDO
CREATE INDEX IF NOT EXISTS idx_recibos_periodo
ON recibos_sueldo(anio, mes);

CREATE INDEX IF NOT EXISTS idx_recibos_empleo
ON recibos_sueldo(empleo_id);

-- DESCUENTOS PROGRAMADOS
CREATE INDEX IF NOT EXISTS idx_descuentos_periodo
ON descuentos_programados(anio_aplicacion, mes_aplicacion);

CREATE INDEX IF NOT EXISTS idx_descuentos_estado
ON descuentos_programados(estado);
