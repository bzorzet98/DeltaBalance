"""
DeltaBalance — sync/supabase_client.py

Cliente Supabase singleton para DeltaBalance. Credenciales en `.env` (en la
raíz del proyecto, fuera de git): SUPABASE_URL, SUPABASE_ANON_KEY y, solo
para migraciones, SUPABASE_SERVICE_KEY.

App empaquetada (flet build): el .env no viaja en el paquete (pyproject.toml
lo excluye: tiene la service key). Ahí la URL y la anon (publishable) key
salen de VALORES_POR_DEFECTO — son públicas por diseño, lo que protege los
datos es RLS. Una variable de entorno o del .env siempre manda sobre el
default. load_dotenv() va con la ruta explícita: sin ruta llama a
find_dotenv(), que busca el .py que lo llamó en el disco y en el paquete
(solo .pyc, en una carpeta temporal) no encuentra ninguno y falla con
AssertionError.

Un único cliente por proceso (functools.lru_cache): la sesión del usuario
vive en el cliente (supabase-py actualiza el header Authorization de
PostgREST al iniciar sesión o refrescar el token), así que AuthService y
SyncEngine tienen que usar el MISMO — con dos clientes, el de la sync
haría las consultas sin sesión y RLS no le devolvería nada.
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

# Con ruta explícita: sin ruta, load_dotenv() llama a find_dotenv(), que en
# la app empaquetada falla (ver docstring). Si el archivo no existe, no pasa nada.
load_dotenv(Path(__file__).parent.parent / ".env")

# Para la app empaquetada, que no lleva .env (ver docstring). Solo lo
# público: la service key nunca tiene default.
VALORES_POR_DEFECTO = {
    "SUPABASE_URL": "https://cytvrechqghsbaaxckxy.supabase.co",
    "SUPABASE_ANON_KEY": "sb_publishable_9dOsUYsdDo2KsXQ7iUdZSg_3mUUZ-bv",
}


class ConfiguracionSupabaseError(Exception):
    """Falta alguna variable de Supabase en .env."""


def _variable(nombre: str) -> str:
    valor = os.environ.get(nombre) or VALORES_POR_DEFECTO.get(nombre)
    if not valor:
        raise ConfiguracionSupabaseError(f"Falta {nombre} en el archivo .env de la raíz del proyecto.")
    return valor


@lru_cache(maxsize=None)
def get_client() -> Client:
    """El cliente de la app (anon key + la sesión del usuario): siempre el mismo."""
    return create_client(_variable("SUPABASE_URL"), _variable("SUPABASE_ANON_KEY"))


@lru_cache(maxsize=None)
def get_service_client() -> Client:
    """Cliente con service_role key — bypass RLS. Solo para migraciones (nunca desde la app)."""
    return create_client(_variable("SUPABASE_URL"), _variable("SUPABASE_SERVICE_KEY"))
