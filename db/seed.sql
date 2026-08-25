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
--
-- Lista simplificada (21 categorías, antes 27) — propuesta y aprobada por
-- el usuario el 2026-08-25 para reducir categorías redundantes/demasiado
-- específicas para el uso diario. INSERT OR IGNORE solo alcanza a bases
-- NUEVAS (nunca renombra ni fusiona filas ya sembradas con los nombres
-- viejos) — una base ya existente (como data/deltabalance.db) necesita
-- correr migration/migrar_categorias_simplificadas.py para llegar al mismo
-- estado. Ver ese script para el detalle de qué categoría vieja se fusionó
-- en cuál nueva. Las dos categorías protegidas (services/categorias_service.py
-- CATEGORIAS_PROTEGIDAS: INGRESOS · Sueldo, MOVIMIENTO CAPITAL ·
-- Autotransferencia) no cambiaron de nombre.
-- =============================================================

-- INGRESOS
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('INGRESOS', 'Sueldo',               'ingreso'),
    ('INGRESOS', 'Cobro Deuda',          'ingreso'),
    ('INGRESOS', 'Reintegro',            'ingreso');

-- EGRESOS FIJOS
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('EGRESOS FIJOS', 'Alquiler / Vivienda', 'egreso'),
    ('EGRESOS FIJOS', 'Servicios',           'egreso'),
    ('EGRESOS FIJOS', 'Seguros',             'egreso'),
    ('EGRESOS FIJOS', 'Impuestos',           'egreso'),
    ('EGRESOS FIJOS', 'Educacion',           'egreso');

-- EGRESOS VARIABLES
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('EGRESOS VARIABLES', 'Supermercado',        'egreso'),
    ('EGRESOS VARIABLES', 'Hogar',                'egreso'),
    ('EGRESOS VARIABLES', 'Bienestar y Deporte',  'egreso'),
    ('EGRESOS VARIABLES', 'Ocio',                 'egreso'),
    ('EGRESOS VARIABLES', 'Comidas y Bebidas',    'egreso'),
    ('EGRESOS VARIABLES', 'Transporte / Auto',    'egreso'),
    ('EGRESOS VARIABLES', 'Salud',                'egreso'),
    ('EGRESOS VARIABLES', 'Ropa',                 'egreso'),
    ('EGRESOS VARIABLES', 'Regalos y Mascotas',   'egreso');

-- MOVIMIENTO CAPITAL
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('MOVIMIENTO CAPITAL', 'Autotransferencia',  'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Rendimientos',        'ingreso'),
    ('MOVIMIENTO CAPITAL', 'Inversiones',         'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Cambio Moneda',       'movimiento');

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
