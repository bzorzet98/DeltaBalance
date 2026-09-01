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
-- 'Ahorro/Inversión' (Tarea 1b de docs/PROXIMOS_PASOS.md, ver
-- services/categorias_service.py CATEGORIAS_PROTEGIDAS) es categoría
-- especial protegida: elegirla en la fila de alta del Registro de
-- transacciones (ui/components/registro_transacciones.py) rutea a un
-- aporte de ahorro (SavingsService.register_purchase()) en vez de crear
-- una transacción simple. NUEVA en una base ya existente: INSERT OR
-- IGNORE acá no alcanza a data/deltabalance.db si ya existía antes de
-- este cambio — correr migration/agregar_categoria_ahorro_inversion.py a
-- mano en ese caso.
-- 'Deuda' (nueva, docs/PROXIMOS_PASOS.md — corrección posterior a la
-- Tarea 9 Parte B): elegirla en la fila de alta del Registro NO reemplaza
-- la transacción normal (a diferencia de las otras dos especiales de
-- este bloque) — primero crea la transacción real de siempre, y DESPUÉS
-- abre un mini-diálogo que vincula una deuda informal
-- (DebtsService.create(origen_tipo='transaccion', origen_id=<esa
-- transacción>)) a esa misma transacción. Misma advertencia de INSERT OR
-- IGNORE: correr migration/agregar_categoria_deuda.py a mano contra una
-- base ya existente.
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('MOVIMIENTO CAPITAL', 'Autotransferencia',  'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Rendimientos',        'ingreso'),
    ('MOVIMIENTO CAPITAL', 'Inversiones',         'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Cambio Moneda',       'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Ahorro/Inversión',    'movimiento'),
    ('MOVIMIENTO CAPITAL', 'Deuda',               'movimiento');

-- TARJETA DE CRÉDITO — categorías especiales protegidas (agregadas Tarea 3
-- de docs/PROXIMOS_PASOS.md, ver services/categorias_service.py
-- CATEGORIAS_PROTEGIDAS y services/fees_service.py CATEGORIAS_CARGO_EXTRA):
-- elegir una de estas tres en la fila de alta de Compras en cuotas
-- (ui/screens/compras_cuotas.py) NO crea una compra en cuotas — rutea a un
-- cargo extra del resumen de tarjeta (resumen_cargos_extra) del tipo
-- correspondiente. tipo='egreso' por default aunque el monto de un
-- ajuste/reintegro puede ser negativo (la clasificación de categoría es
-- egreso igual, el signo lo define el monto tipeado, no la categoría).
-- NUEVA en una base ya existente: INSERT OR IGNORE acá NO alcanza a
-- data/deltabalance.db si ya existía antes de este cambio — correr
-- migration/agregar_categorias_tarjeta.py a mano en ese caso.
INSERT OR IGNORE INTO categorias (categoria_principal, subcategoria, tipo) VALUES
    ('TARJETA DE CRÉDITO', 'Impuesto tarjeta',            'egreso'),
    ('TARJETA DE CRÉDITO', 'Recargo tarjeta',              'egreso'),
    ('TARJETA DE CRÉDITO', 'Ajuste/Reintegro tarjeta',     'egreso');

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
