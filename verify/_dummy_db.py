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
# El schema y el seed anteriores al versionado (congelados): para armar una
# base VIEJA, en user_version = 0, y probar las migraciones sobre ella.
SCHEMA_V0_PATH = BASE_DIR / "db" / "schema_v0.sql"
SEED_V0_PATH = BASE_DIR / "db" / "seed_v0.sql"


def _crear(path: Path | None, schema: Path, seed: Path, prefijo: str) -> Path:
    if path is None:
        fd, tmp_name = tempfile.mkstemp(prefix=prefijo, suffix=".db")
        os.close(fd)
        path = Path(tmp_name)

    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(schema.read_text(encoding="utf-8"))
        conn.executescript(seed.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()

    return path


def crear_dummy_db(path: Path | None = None) -> Path:
    """
    Crea (o reutiliza) un archivo SQLite temporal con schema.sql y seed.sql
    aplicados, y devuelve su Path. Queda en la versión actual del schema
    (schema.sql marca su PRAGMA user_version): DatabaseManager.inicializar()
    no le corre ninguna migración.

    Args:
        path: ruta explícita donde crear la DB. Si es None (caso normal), se
            genera un archivo temporal nuevo con tempfile.

    Returns:
        Path al archivo SQLite listo para usar.
    """
    return _crear(path, SCHEMA_PATH, SEED_PATH, "deltabalance_dummy_")


def crear_dummy_db_v0(path: Path | None = None) -> Path:
    """
    Igual que crear_dummy_db(), pero con db/schema_v0.sql + db/seed_v0.sql:
    una base VIEJA (user_version = 0, moneda_id entero, presupuestos e
    ingresos_proyectados con su estructura anterior…), para armar a mano un
    estado anterior con sqlite3 crudo y después llamar a
    DatabaseManager.inicializar(), que le corre TODAS las migraciones
    (db/schema_migrations.py run_migrations()). Los repositorios y services
    no funcionan sobre ella hasta inicializar().
    """
    return _crear(path, SCHEMA_V0_PATH, SEED_V0_PATH, "deltabalance_dummy_v0_")
