"""
DeltaBalance — ui/utils/prefs.py

Preferencias locales de la app en un archivo JSON (CLAUDE.md §12):
`Path.cwd() / ".deltabalance_prefs.json"`. ft.SharedPreferences no persiste
entre sesiones en Flet 0.86.5 desktop, así que todo lo que tenga que
sobrevivir a un reinicio (ej. los anchos de columna del Registro) va acá.

Un único diccionario por archivo, una clave por preferencia. escribir_pref()
lee, modifica y reescribe el archivo completo, así que las claves que
guarda otra parte de la app nunca se pisan. La escritura va a un archivo
temporal y después se renombra: un corte a mitad de la escritura no deja el
JSON a medias.

Nunca lanza: un archivo que falta, está corrupto o no se puede escribir se
trata como "sin preferencias guardadas" — perder una preferencia de
interfaz no debe romper ninguna pantalla.

Módulo de aplicación (CLAUDE.md §2): no lo usa el motor de datos.
"""

import json
from pathlib import Path
from typing import Any

NOMBRE_ARCHIVO_PREFS = ".deltabalance_prefs.json"


def ruta_prefs() -> Path:
    return Path.cwd() / NOMBRE_ARCHIVO_PREFS


def leer_prefs() -> dict:
    """Todas las preferencias guardadas ({} si no hay archivo o no se puede leer)."""
    try:
        with ruta_prefs().open(encoding="utf-8") as archivo:
            datos = json.load(archivo)
    except (OSError, ValueError):
        return {}
    return datos if isinstance(datos, dict) else {}


def escribir_prefs(prefs: dict) -> None:
    """Reemplaza el archivo completo por `prefs`."""
    ruta = ruta_prefs()
    temporal = ruta.with_name(ruta.name + ".tmp")
    try:
        with temporal.open("w", encoding="utf-8") as archivo:
            json.dump(prefs, archivo, ensure_ascii=False, indent=2)
        temporal.replace(ruta)
    except (OSError, TypeError, ValueError):
        pass


def leer_pref(clave: str, default: Any = None) -> Any:
    return leer_prefs().get(clave, default)


def escribir_pref(clave: str, valor: Any) -> None:
    """Guarda una sola clave sin tocar las demás."""
    prefs = leer_prefs()
    prefs[clave] = valor
    escribir_prefs(prefs)
