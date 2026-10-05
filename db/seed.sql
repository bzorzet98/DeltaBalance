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
-- Catálogo reestructurado (docs/DATA_MODEL_DECISIONS.md sección 30): todo
-- en MAYÚSCULAS, egresos bajo una sola categoría principal EGRESOS. Es el
-- mismo CATEGORIAS_FINALES de migration/reestructurar_categorias.py, que
-- lleva a este catálogo una base ya sembrada con los nombres anteriores
-- (INSERT OR IGNORE solo alcanza a bases NUEVAS: nunca renombra ni fusiona
-- filas existentes).
--
-- Categorías que el código reconoce por nombre (sin distinguir mayúsculas,
-- utils/categorias.py clave_categoria()) — protegidas en
-- services/categorias_service.py CATEGORIAS_PROTEGIDAS:
-- - INGRESOS · SUELDO / BECA.
-- - MOVIMIENTO CAPITAL · AUTOTRANSFERENCIA / AHORRO/INVERSIÓN / DEUDA:
--   routing especial de la fila de alta del Registro
--   (ui/components/registro_transacciones.py).
-- - TARJETA DE CRÉDITO · IMPUESTO TARJETA / RECARGO TARJETA /
--   AJUSTE/REINTEGRO TARJETA: en Compras en cuotas son cargos extra del
--   resumen, no compras (services/fees_service.py CATEGORIAS_CARGO_EXTRA).
-- =============================================================

-- EGRESOS ("EGRESO VARIABLE": transitoria, para los datos migrados sin una
-- categoría más precisa)
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('EGRESOS', 'VIVIENDA',          'egreso'),
    ('EGRESOS', 'SERVICIOS BÁSICOS', 'egreso'),
    ('EGRESOS', 'SEGUROS',           'egreso'),
    ('EGRESOS', 'EDUCACIÓN',         'egreso'),
    ('EGRESOS', 'TRANSPORTE / AUTO', 'egreso'),
    ('EGRESOS', 'SUPERMERCADO',      'egreso'),
    ('EGRESOS', 'ALIMENTOS',         'egreso'),
    ('EGRESOS', 'GASTRONOMÍA',       'egreso'),
    ('EGRESOS', 'SALUD',             'egreso'),
    ('EGRESOS', 'DEPORTE',           'egreso'),
    ('EGRESOS', 'INDUMENTARIA',      'egreso'),
    ('EGRESOS', 'HOGAR',             'egreso'),
    ('EGRESOS', 'MASCOTAS',          'egreso'),
    ('EGRESOS', 'REGALOS',           'egreso'),
    ('EGRESOS', 'OCIO',              'egreso'),
    ('EGRESOS', 'VACACIONES',        'egreso'),
    ('EGRESOS', 'EGRESO VARIABLE',   'egreso');

-- INGRESOS
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('INGRESOS', 'SUELDO / BECA',       'ingreso'),
    ('INGRESOS', 'INGRESO VARIABLE',    'ingreso'),
    ('INGRESOS', 'REINTEGRO',           'ingreso'),
    ('INGRESOS', 'REINTEGRO PROMOCIÓN', 'ingreso'),
    ('INGRESOS', 'RENDIMIENTOS',        'ingreso'),
    ('INGRESOS', 'COBRO DEUDA',         'ingreso');

-- MOVIMIENTO CAPITAL
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('MOVIMIENTO CAPITAL', 'AUTOTRANSFERENCIA', 'movimiento'),
    ('MOVIMIENTO CAPITAL', 'AHORRO/INVERSIÓN',  'movimiento'),
    ('MOVIMIENTO CAPITAL', 'INVERSIONES',       'movimiento'),
    ('MOVIMIENTO CAPITAL', 'CAMBIO MONEDA',     'movimiento'),
    ('MOVIMIENTO CAPITAL', 'DEUDA',             'movimiento');

-- TARJETA DE CRÉDITO (tipo 'egreso' aunque un ajuste/reintegro pueda ser
-- negativo: el signo lo define el monto tipeado, no la categoría)
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('TARJETA DE CRÉDITO', 'IMPUESTO TARJETA',         'egreso'),
    ('TARJETA DE CRÉDITO', 'AJUSTE/REINTEGRO TARJETA', 'egreso'),
    ('TARJETA DE CRÉDITO', 'PAGO TARJETA',             'egreso'),
    ('TARJETA DE CRÉDITO', 'RECARGO TARJETA',          'egreso');

-- =============================================================
-- BROKERS: no van acá. Los siembra db/schema_migrations.py
-- (BROKERS_INICIALES, INSERT OR IGNORE por nombre) en CADA inicializar():
-- este archivo solo corre en bases nuevas, y así también los reciben las
-- bases que ya existían.
-- =============================================================

-- =============================================================
-- CUENTA INICIAL: Efectivo ARS
-- La única cuenta que existe antes de configurar el sistema.
-- El usuario agrega sus cuentas bancarias desde la UI.
-- =============================================================
INSERT OR IGNORE INTO cuentas (nombre, tipo, notas) VALUES
    ('Caja Efectivo', 'efectivo', 'Cuenta inicial por defecto. Representa el dinero en mano.');

-- Saldo inicial en ARS = 0 para la cuenta efectivo. Por nombre y código, no
-- por id: el id de la cuenta es un UUID que genera el DEFAULT de cuentas.id.
INSERT OR IGNORE INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor)
    SELECT c.id, m.id, 0
    FROM cuentas c, monedas m
    WHERE c.nombre = 'Caja Efectivo' AND m.codigo = 'ARS';
