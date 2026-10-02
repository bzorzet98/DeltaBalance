"""
DeltaBalance — repositories/_ids.py

Ids de fila: UUID v4 en texto, generados en Python al crear cada fila (ver
el comentario de cabecera de db/schema.sql y docs/DATA_MODEL_DECISIONS.md
sección 25). Los repositorios lo pasan explícito en el INSERT y lo
devuelven — NO usan cursor.lastrowid: en una tabla con PRIMARY KEY de texto
eso es el rowid interno (un entero), no el id.

monedas es la excepción: sigue con id entero (dato de referencia igual en
toda base).
"""

import uuid


def nuevo_id() -> str:
    return str(uuid.uuid4())
