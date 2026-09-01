"""
DeltaBalance — main.py

Punto de entrada de la aplicación Flet. Inicializa la base de datos real
(data/deltabalance.db) y arranca el shell (ui/app.py).

Correrlo con:
    flet run main.py
"""

import flet as ft

from db.database import DatabaseManager
from ui.app import build_app


def main(page: ft.Page) -> None:
    db = DatabaseManager()
    db.inicializar()
    build_app(page, db)


if __name__ == "__main__":
    
    ft.run(main)
