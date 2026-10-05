"""
DeltaBalance — utils/categorias.py

Clave de una categoría por su NOMBRE — (categoria_principal, subcategoria)
sin espacios de más y en mayúsculas —, para el código que reconoce
categorías especiales por nombre y no por id (el id varía entre bases):
CATEGORIAS_PROTEGIDAS (services/categorias_service.py),
CATEGORIAS_CARGO_EXTRA (services/fees_service.py), el routing del Registro
(ui/components/registro_transacciones.py), la categoría de ahorro
(services/savings_service.py) y la migración de cargos extra
(db/schema_migrations.py).

Comparar las claves y no los textos tal cual hace que "Impuesto tarjeta" y
"IMPUESTO TARJETA" sean la misma categoría: el catálogo pasó a mayúsculas
(migration/reestructurar_categorias.py, docs/DATA_MODEL_DECISIONS.md
sección 30) y el código reconoce igual una base todavía sin migrar o la de
otro miembro del hogar.

En Python y no con UPPER() de SQLite: solo pasa a mayúscula ASCII
("Inversión" quedaría "INVERSIóN" y no coincidiría con "INVERSIÓN"). Mismo
criterio que utils/personas.py.
"""


def clave_categoria(categoria_principal: str | None, subcategoria: str | None) -> tuple[str, str]:
    """('EGRESOS', 'SUPERMERCADO') para ' egresos', 'Supermercado' — ver docstring del módulo."""
    return (
        " ".join((categoria_principal or "").split()).upper(),
        " ".join((subcategoria or "").split()).upper(),
    )
