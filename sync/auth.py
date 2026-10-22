"""
DeltaBalance — sync/auth.py

Autenticación contra Supabase Auth (email + contraseña) y el nombre de
display del usuario.

Sesión guardada: keyring del sistema operativo (GNOME Keyring en Linux,
Windows Credential Manager en Windows, Keychain en macOS), bajo el servicio
"deltabalance" y la clave "supabase_session". Si keyring no está disponible
(entorno sin keychain), cae a .deltabalance_prefs.json como fallback
(comportamiento anterior). El archivo de prefs está en .gitignore.

Así la app no pide login cada vez: con una sesión guardada, is_logged_in()
es True sin tocar la red (se puede trabajar sin conexión), y recién
refresh_session() — que llama SyncEngine antes de cada sincronización — la
carga en el cliente y renueva el token si venció. Cada renovación automática
del cliente (evento TOKEN_REFRESHED) vuelve a guardar los tokens nuevos.

Nombre de display: prefs, clave "display_name". Local de cada
computadora, no sube a Supabase (pedido explícito).

Usuario local de hogares compartidos: hardcodeado por UUID de Supabase en
USUARIOS_LOCALES. Cada login exitoso lo escribe en prefs, clave
"usuario_local" (la misma que lee ui/components/usuario_local.py); un UUID
que no está en el mapa escribe "".

Los mensajes de AuthResult ya vienen en MAYÚSCULAS: la pantalla de login
los muestra tal cual.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import keyring
import keyring.errors
from supabase_auth.errors import AuthApiError

from sync.supabase_client import ConfiguracionSupabaseError, get_client
from ui.utils.prefs import escribir_pref, leer_pref

logger = logging.getLogger(__name__)

PREF_SESION = "supabase_session"
PREF_NOMBRE = "display_name"
PREF_USUARIO_LOCAL = "usuario_local"
EVENTOS_SESION_NUEVA = ("SIGNED_IN", "TOKEN_REFRESHED")

_KEYRING_SERVICIO = "deltabalance"
_KEYRING_CLAVE = "supabase_session"

USUARIOS_LOCALES = {
    "f5ca8a42-a039-4a46-998e-2b01dbb28e9c": "BRUNO",
    "3cb96dd5-e56b-47b6-803f-63410d9c3bdd": "NOELIA",
}


@dataclass
class AuthResult:
    success: bool
    user_id: Optional[str] = None
    email: Optional[str] = None
    mensaje: str = ""


# ----------------------------------------------------------
# ALMACÉN SEGURO — keyring con fallback a prefs
# ----------------------------------------------------------

def _guardar_sesion_segura(sesion: dict) -> None:
    """Guarda el diccionario de sesión en keyring. Si no está disponible, cae a prefs."""
    try:
        keyring.set_password(_KEYRING_SERVICIO, _KEYRING_CLAVE, json.dumps(sesion))
        # Limpiar el valor viejo de prefs si existía
        escribir_pref(PREF_SESION, None)
        logger.debug("Sesión guardada en keyring.")
    except keyring.errors.NoKeyringError:
        logger.warning("Keyring no disponible — sesión guardada en prefs (fallback).")
        escribir_pref(PREF_SESION, sesion)
    except Exception as err:
        logger.warning("Error al guardar en keyring (%s) — usando prefs como fallback.", err)
        escribir_pref(PREF_SESION, sesion)


def _leer_sesion_segura() -> Optional[dict]:
    """Lee el diccionario de sesión desde keyring, con fallback a prefs."""
    try:
        valor = keyring.get_password(_KEYRING_SERVICIO, _KEYRING_CLAVE)
        if valor is not None:
            logger.debug("Sesión leída desde keyring.")
            return json.loads(valor)
    except keyring.errors.NoKeyringError:
        logger.warning("Keyring no disponible — leyendo sesión desde prefs (fallback).")
    except Exception as err:
        logger.warning("Error al leer desde keyring (%s) — usando prefs como fallback.", err)

    # Fallback: leer desde prefs
    guardada = leer_pref(PREF_SESION)
    return guardada if isinstance(guardada, dict) else None


def _borrar_sesion_segura() -> None:
    """Borra la sesión de keyring y de prefs."""
    try:
        keyring.delete_password(_KEYRING_SERVICIO, _KEYRING_CLAVE)
        logger.debug("Sesión borrada de keyring.")
    except keyring.errors.NoKeyringError:
        logger.warning("Keyring no disponible — borrando sesión de prefs.")
    except keyring.errors.PasswordDeleteError:
        pass  # No existía en keyring, no es un error
    except Exception as err:
        logger.warning("Error al borrar de keyring (%s).", err)

    # Siempre limpiar prefs también (por si había fallback previo)
    escribir_pref(PREF_SESION, None)


class AuthService:
    """
    Usage:
        auth = AuthService()
        if not auth.is_logged_in():
            resultado = auth.login("bruno@mail.com", "…")
        auth.get_user_id()   # UUID de Supabase Auth
    """

    def __init__(self) -> None:
        self._sesion: Optional[dict] = _leer_sesion_segura()
        self._escuchando = False

    # ----------------------------------------------------------
    # CLIENTE Y SESIÓN GUARDADA
    # ----------------------------------------------------------

    def _cliente(self) -> Any:
        cliente = get_client()
        if not self._escuchando:
            cliente.auth.on_auth_state_change(self._al_cambiar_sesion)
            self._escuchando = True
        return cliente

    def _al_cambiar_sesion(self, evento: Any, sesion: Any) -> None:
        if str(evento) in EVENTOS_SESION_NUEVA and sesion is not None:
            self._guardar(sesion)

    def _guardar(self, sesion: Any) -> None:
        self._sesion = {
            "access_token": sesion.access_token,
            "refresh_token": sesion.refresh_token,
            "user_id": sesion.user.id,
            "email": sesion.user.email,
            "expires_at": sesion.expires_at,
        }
        _guardar_sesion_segura(self._sesion)

    def _olvidar(self) -> None:
        self._sesion = None
        _borrar_sesion_segura()

    # ----------------------------------------------------------
    # API pública — sin cambios
    # ----------------------------------------------------------

    def login(self, email: str, password: str) -> AuthResult:
        """Inicia sesión y la guarda. Hace red: llamarla fuera del hilo de la UI (asyncio.to_thread)."""
        try:
            respuesta = self._cliente().auth.sign_in_with_password(
                {"email": (email or "").strip(), "password": password or ""},
            )
        except ConfiguracionSupabaseError as err:
            return AuthResult(success=False, mensaje=f"FALTA CONFIGURAR SUPABASE: {err}".upper())
        except AuthApiError:
            return AuthResult(success=False, mensaje="EMAIL O CONTRASEÑA INCORRECTOS.")
        except Exception:
            return AuthResult(success=False, mensaje="NO SE PUDO CONECTAR CON SUPABASE. ¿HAY INTERNET?")
        if respuesta.session is None:
            return AuthResult(success=False, mensaje="NO SE PUDO INICIAR SESIÓN.")
        self._guardar(respuesta.session)
        escribir_pref(PREF_USUARIO_LOCAL, USUARIOS_LOCALES.get(respuesta.session.user.id, ""))
        return AuthResult(
            success=True,
            user_id=respuesta.session.user.id,
            email=respuesta.session.user.email,
            mensaje="SESIÓN INICIADA.",
        )

    def logout(self) -> None:
        """Cierra la sesión en Supabase (si hay conexión) y la borra."""
        try:
            self._cliente().auth.sign_out()
        except Exception:
            pass
        self._olvidar()

    def get_user_id(self) -> Optional[str]:
        return self._sesion.get("user_id") if self._sesion else None

    def get_email(self) -> Optional[str]:
        return self._sesion.get("email") if self._sesion else None

    def get_usuario_local(self) -> Optional[str]:
        user_id = self.get_user_id()
        return USUARIOS_LOCALES.get(user_id)

    def is_logged_in(self) -> bool:
        return bool(self._sesion and self._sesion.get("refresh_token") and self._sesion.get("user_id"))

    def refresh_session(self) -> bool:
        if not self.is_logged_in():
            return False
        try:
            respuesta = self._cliente().auth.set_session(
                self._sesion["access_token"], self._sesion["refresh_token"]
            )
        except AuthApiError:
            self._olvidar()
            return False
        if respuesta.session is None:
            return False
        self._guardar(respuesta.session)
        return True

    # ----------------------------------------------------------
    # NOMBRE DE DISPLAY (local, no sube a Supabase)
    # ----------------------------------------------------------

    def get_display_name(self) -> Optional[str]:
        nombre = leer_pref(PREF_NOMBRE)
        return nombre.strip() if isinstance(nombre, str) and nombre.strip() else None

    def set_display_name(self, nombre: str) -> None:
        limpio = " ".join((nombre or "").split())
        escribir_pref(PREF_NOMBRE, limpio or None)