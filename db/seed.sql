-- =============================================================
-- DeltaBalance — seed.sql
-- Datos iniciales: monedas, categorías y cuenta efectivo ARS
-- =============================================================

-- =============================================================
-- MONEDAS
-- decimales: cantidad de decimales de la unidad menor
-- Ej: ARS tiene 2 → 1 ARS = 100 minor units
-- =============================================================
INSERT OR IGNORE INTO monedas (codigo, simbolo, decimales) VALUES
    ('ARS',  '$',    2),
    ('USD',  'U$S',  2),
    ('USDT', 'USDT', 2),
    ('USDC', 'USDC', 2),
    ('BTC',  'BTC',  8),
    ('CLP',  'CLP$', 0),
    ('BRL',  'R$',   2);

-- =============================================================
-- CATEGORIAS
-- tipo: 'ingreso' | 'egreso' | 'movimiento'
-- =============================================================

-- INGRESOS
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('INGRESOS', 'Sueldo',               'ingreso'),
    ('INGRESOS', 'Cobro Deuda',          'ingreso'),
    ('INGRESOS', 'Reintegro',            'ingreso'),
    ('INGRESOS', 'Reintegro Promocion',  'ingreso');

-- EGRESOS FIJOS
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('EGRESOS FIJOS', 'Alquiler / Vivienda', 'egreso'),
    ('EGRESOS FIJOS', 'Servicios',           'egreso'),
    ('EGRESOS FIJOS', 'Seguros',             'egreso'),
    ('EGRESOS FIJOS', 'Impuestos',           'egreso'),
    ('EGRESOS FIJOS', 'Educacion',           'egreso');

-- EGRESOS VARIABLES
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('EGRESOS VARIABLES', 'Supermercado',          'egreso'),
    ('EGRESOS VARIABLES', 'Hogar: Mantenimiento',  'egreso'),
    ('EGRESOS VARIABLES', 'Vivero y Jardin',        'egreso'),
    ('EGRESOS VARIABLES', 'Bienestar y Deporte',   'egreso'),
    ('EGRESOS VARIABLES', 'Ocio: Salidas',          'egreso'),
    ('EGRESOS VARIABLES', 'Ocio: Entretenimiento', 'egreso'),
    ('EGRESOS VARIABLES', 'Comidas y Bebidas',     'egreso'),
    ('EGRESOS VARIABLES', 'Transporte / Auto',     'egreso'),
    ('EGRESOS VARIABLES', 'Salud',                 'egreso'),
    ('EGRESOS VARIABLES', 'Ropa',                  'egreso'),
    ('EGRESOS VARIABLES', 'Regalos',               'egreso'),
    ('EGRESOS VARIABLES', 'Mascotas',              'egreso');

-- MOVIMIENTO CAPITAL
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('MOVIMIENTO CAPITAL', 'Autotransferencia',  'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Rendimientos',        'ingreso'),
    ('MOVIMIENTO CAPITAL', 'Inversiones',         'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Cambio Moneda',       'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Salud: Obra Social',  'egreso'),
    ('MOVIMIENTO CAPITAL', 'Sinking Funds',       'movimiento');

-- =============================================================
-- CUENTA INICIAL: Efectivo ARS
-- La única cuenta que existe antes de configurar el sistema.
-- El usuario agrega sus cuentas bancarias desde la UI.
-- =============================================================
INSERT OR IGNORE INTO cuentas (nombre, tipo, notas) VALUES
    ('Caja Efectivo', 'efectivo', 'Cuenta inicial por defecto. Representa el dinero en mano.');

-- Saldo inicial en ARS = 0 para la cuenta efectivo (id=1, moneda ARS id=1)
INSERT OR IGNORE INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor) VALUES
    (1, 1, 0);
