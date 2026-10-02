"""
DeltaBalance — main.py
Punto de entrada de la aplicación Flet. Abre la base que corresponde a cómo
corre la app (db.database.get_db_path(): data/deltabalanceBZ.db desde el
código; la carpeta de datos de la app cuando está empaquetada con
flet build) y arranca el shell (ui/app.py).
Correrlo con:
    flet run main.py
"""
import flet as ft
from db.database import DatabaseManager, get_db_path
from ui.app import build_app

def main(page: ft.Page) -> None:
    db = DatabaseManager(get_db_path())
    db.inicializar()
    build_app(page, db)

if __name__ == "__main__":
    ft.run(main)
