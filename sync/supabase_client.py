"""
DeltaBalance — sync/supabase_client.py

Cliente Supabase singleton para DeltaBalance. Credenciales en `.env` (en la
raíz del proyecto, fuera de git): SUPABASE_URL, SUPABASE_ANON_KEY y, solo
para migraciones, SUPABASE_SERVICE_KEY.

App empaquetada (flet build): el .env no viaja en el paquete (pyproject.toml
lo excluye: tiene la service key). Ahí la URL y la anon (publishable) key
salen de sync/credenciales_build.py, que NO está en git: lo genera
.github/workflows/build-linux.yml desde los secrets del repo antes de
`flet build`. Para un build local, crearlo a mano con las dos constantes
(SUPABASE_URL = "...", SUPABASE_ANON_KEY = "..."). Son públicas por diseño —
lo que protege los datos es RLS —, pero no van en el código. Una variable de
entorno o del .env siempre manda sobre ese archivo. Si no hay ninguna de las
tres fuentes, el error salta recién al pedir el cliente
(ConfiguracionSupabaseError), no al importar: la app abre igual y el login
avisa. load_dotenv() va con la ruta explícita: sin ruta llama a
find_dotenv(), que busca el .py que lo llamó en el disco y en el paquete
(solo .pyc, en una carpeta temporal) no encuentra ninguno y falla con
AssertionError.

Un único cliente por proceso (functools.lru_cache): la sesión del usuario
vive en el cliente (supabase-py actualiza el header Authorization de
PostgREST al iniciar sesión o refrescar el token), así que AuthService y
SyncEngine tienen que usar el MISMO — con dos clientes, el de la sync
haría las consultas sin sesión y RLS no le devolvería nada.
"""

import logging
import os
import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from supabase import Client, create_client

logger = logging.getLogger(__name__)

# Con ruta explícita: sin ruta, load_dotenv() llama a find_dotenv(), que en
# la app empaquetada falla (ver docstring). Si el archivo no existe, no pasa nada.
load_dotenv(Path(__file__).parent.parent / ".env")

# TODO: DEBUG temporal para diagnosticar la conexión en la app empaquetada —
# sacar todos los logger.debug() de este archivo cuando conecte.
logger.debug("Python: %s", sys.executable)
logger.debug("SUPABASE_URL env: %s", os.environ.get("SUPABASE_URL", "NO ENCONTRADA"))

# Para la app empaquetada, que no lleva .env (ver docstring). Solo lo
# público: la service key nunca va en este archivo.
try:
    from sync import credenciales_build as _build
    VALORES_DEL_BUILD = {
        "SUPABASE_URL": getattr(_build, "SUPABASE_URL", None),
        "SUPABASE_ANON_KEY": getattr(_build, "SUPABASE_ANON_KEY", None),
    }
    logger.debug(
        "credenciales_build cargado OK, URL: %s...", (VALORES_DEL_BUILD["SUPABASE_URL"] or "NONE")[:20],
    )
except ImportError as e:  # desarrollo: no hay archivo generado, las variables salen del .env
    VALORES_DEL_BUILD = {}
    logger.debug("Error cargando credenciales_build: %s", e)

_url_final = os.environ.get("SUPABASE_URL") or VALORES_DEL_BUILD.get("SUPABASE_URL")
_hay_anon_key = bool(os.environ.get("SUPABASE_ANON_KEY") or VALORES_DEL_BUILD.get("SUPABASE_ANON_KEY"))
logger.debug("SUPABASE_URL final: %s...", _url_final[:20] if _url_final else "NONE")
logger.debug("SUPABASE_ANON_KEY presente: %s", _hay_anon_key)


class ConfiguracionSupabaseError(Exception):
    """Falta alguna variable de Supabase (.env o sync/credenciales_build.py)."""


def _variable(nombre: str) -> str:
    valor = os.environ.get(nombre) or VALORES_DEL_BUILD.get(nombre)
    if not valor:
        raise ConfiguracionSupabaseError(
            f"Falta {nombre} (en el .env de la raíz del proyecto o en "
            f"sync/credenciales_build.py, que genera el build)."
        )
    return valor


@lru_cache(maxsize=None)
def get_client() -> Client:
    """El cliente de la app (anon key + la sesión del usuario): siempre el mismo."""
    return create_client(_variable("SUPABASE_URL"), _variable("SUPABASE_ANON_KEY"))


@lru_cache(maxsize=None)
def get_service_client() -> Client:
    """Cliente con service_role key — bypass RLS. Solo para migraciones (nunca desde la app)."""
    return create_client(_variable("SUPABASE_URL"), _variable("SUPABASE_SERVICE_KEY"))
