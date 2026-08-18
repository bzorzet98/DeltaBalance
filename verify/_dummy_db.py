"""
Helper para scripts de verify/.

Crea una base de datos SQLite temporal real (nunca ':memory:', nunca
data/deltabalance.db) con el schema y el seed completos aplicados, lista para
usar en un script de verify/.

Uso típico al principio de un script verify/<subcarpeta>/verify_<dominio>.py
(los scripts viven en subcarpetas por bloque de dominio — ver verify/README.md
— así que quedan dos niveles bajo la raíz del proyecto, no uno):

    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

    from verify._dummy_db import crear_dummy_db
    from db.database import DatabaseManager

    db_path = crear_dummy_db()
    manager = DatabaseManager(db_path=db_path)
    conn = manager.conectar()
    ...

Notas:
- Esta función NO instancia DatabaseManager ni ninguna otra clase de db/ —
  aplica el schema con sqlite3 standalone para no acoplar verify/ a la
  implementación interna de la capa de datos. Quien llama decide después cómo
  conectarse (DatabaseManager, sqlite3.connect directo, etc).
- Cada corrida de un script de verify/ debe pedir su propia base de datos
  nueva llamando a crear_dummy_db() sin argumentos — no reutilices la dummy DB
  de una corrida anterior ni la compartas entre scripts. La única razón válida
  para pasar un `path` explícito es necesitar persistencia *dentro* de la
  misma corrida (por ejemplo, crear la DB en un paso del script y volver a
  abrirla en un paso posterior del mismo script).
- El archivo se crea con tempfile en el directorio temporal del sistema y no
  se borra automáticamente al terminar el script — es responsabilidad de
  quien lo usa limpiarlo si le importa, pero al ser un archivo temporal fuera
  de data/ nunca contamina la base real.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"
SEED_PATH = BASE_DIR / "db" / "seed.sql"


def crear_dummy_db(path: Path | None = None) -> Path:
    """
    Crea (o reutiliza) un archivo SQLite temporal con schema.sql y seed.sql
    aplicados, y devuelve su Path.

    Args:
        path: ruta explícita donde crear la DB. Si es None (caso normal), se
            genera un archivo temporal nuevo con tempfile.

    Returns:
        Path al archivo SQLite listo para usar.
    """
    if path is None:
        fd, tmp_name = tempfile.mkstemp(prefix="deltabalance_dummy_", suffix=".db")
        os.close(fd)
        path = Path(tmp_name)

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.executescript(SEED_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()

    return path
