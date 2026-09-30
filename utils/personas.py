"""
DeltaBalance — utils/personas.py

Normalización del nombre de una persona de deudas (deudas.entidad_persona,
deudas_mensuales.entidad_persona): sin espacios de más y en mayúsculas, así
"Noe", "noe " y "NOE" son la misma persona al agrupar saldos. Un solo lugar
para la migración de db/schema_migrations.py, DebtsService,
SnapshotsService y migration/migrar_deudas.py.

En Python y no con UPPER()/TRIM() de SQLite: UPPER() de SQLite solo pasa a
mayúscula ASCII ("Iñaki" quedaría "IñAKI" y no coincidiría con "IÑAKI").
"""


def normalizar_persona(nombre: str | None) -> str:
    return " ".join((nombre or "").split()).upper()
