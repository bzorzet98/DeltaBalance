"""
DeltaBalance — sync/supabase_client.py

Cliente Supabase singleton para DeltaBalance. Credenciales en `.env` (en la
raíz del proyecto, fuera de git): SUPABASE_URL, SUPABASE_ANON_KEY y, solo
para migraciones, SUPABASE_SERVICE_KEY.

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

load_dotenv()  # carga .env si existe, no falla si no existe

SUPABASE_URL = os.environ.get(
    "SUPABASE_URL",
    "https://cytvrechqghsbaaxckxy.supabase.co/"  # reemplazar con la URL real
)
SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY", 
    "sb_publishable_9dOsUYsdDo2KsXQ7iUdZSg_3mUUZ-bv"  # reemplazar con la anon key real
)

def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
load_dotenv(Path(__file__).parent.parent / ".env")


class ConfiguracionSupabaseError(Exception):
    """Falta alguna variable de Supabase en .env."""


def _variable(nombre: str) -> str:
    valor = os.environ.get(nombre)
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
