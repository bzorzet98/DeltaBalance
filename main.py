"""
DeltaBalance — main.py
Punto de entrada de la aplicación Flet. Abre la base que corresponde a cómo
corre la app (db.database.get_db_path(): data/deltabalance.db desde el
código; la carpeta de datos de la app cuando está empaquetada con
flet build) y arranca el shell (ui/app.py).

Modo fresh (pedido del usuario): arranca como una instalación nueva. La base
se mueve a un backup al lado (data/deltabalance_antes_fresh_<fecha_hora>.db,
con su -wal y -shm: nada se borra) y inicializar() crea una vacía; y se
borra la sesión de Supabase guardada en prefs, así ui/app.py abre en el
login. Al loguearse, la primera sincronización baja todo: SyncEngine trata
a una base que nunca sincronizó como PRIMERA e ignora la marca de bajada
que haya en prefs. Es el default desde el código (python main.py y flet
run; con flet run, cada recarga es un arranque nuevo); --no-fresh conserva
la base y la sesión. La app empaquetada nunca lo usa: mantiene la base y la
sesión entre aperturas.

Correrlo con:
    flet run main.py
    python main.py --no-fresh     (sin empezar de cero)
"""
import argparse
from datetime import datetime
from pathlib import Path

import flet as ft
from db.database import DB_PATH, DatabaseManager, get_db_path
from sync.auth import PREF_SESION
from ui.app import build_app
from ui.utils.prefs import escribir_pref

# El archivo de la base y los que SQLite deja al lado (se mueven juntos: un -wal suelto se
# aplicaría a la base nueva).
SUFIJOS_SQLITE = ("", "-wal", "-shm")
FORMATO_SELLO_BACKUP = "%Y%m%d_%H%M%S"
SUFIJO_BACKUP = "_antes_fresh_"


def _es_empaquetada() -> bool:
    """
    ¿Corre como app empaquetada (flet build)? Mismo criterio que get_db_path(): solo ahí la
    base no es DB_PATH. (FLET_APP_STORAGE_DATA sola no alcanza: `flet run` también la define,
    dentro del proyecto.)
    """
    return get_db_path() != DB_PATH


def _empezar_de_cero(db_path: Path) -> None:
    """Modo fresh (ver docstring): la base, a un backup al lado; la sesión de Supabase, fuera de prefs."""
    sello = datetime.now().strftime(FORMATO_SELLO_BACKUP)
    backup = db_path.with_name(f"{db_path.stem}{SUFIJO_BACKUP}{sello}{db_path.suffix}")
    for sufijo in SUFIJOS_SQLITE:
        archivo = Path(f"{db_path}{sufijo}")
        if archivo.exists():
            archivo.rename(Path(f"{backup}{sufijo}"))
    if backup.exists():
        print(f"[DeltaBalance] MODO FRESH: la base anterior quedó en {backup}")
    escribir_pref(PREF_SESION, None)


def _argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DeltaBalance")
    parser.add_argument(
        "--fresh", action=argparse.BooleanOptionalAction, default=not _es_empaquetada(),
        help="Empezar con la base vacía y login obligatorio (default desde el código; --no-fresh lo apaga).",
    )
    # parse_known_args: Flet puede pasar argumentos propios al script.
    argumentos, _ = parser.parse_known_args()
    return argumentos


def main(page: ft.Page) -> None:
    db = DatabaseManager(get_db_path())
    db.inicializar()
    build_app(page, db)

if __name__ == "__main__":
    # Una sola vez por arranque, antes de abrir la base y de que AuthService lea la sesión.
    if _argumentos().fresh and not _es_empaquetada():
        _empezar_de_cero(get_db_path())
    ft.run(main)
