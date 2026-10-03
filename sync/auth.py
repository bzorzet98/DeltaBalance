"""
DeltaBalance — sync/auth.py

Autenticación contra Supabase Auth (email + contraseña) y el nombre de
display del usuario.

Sesión guardada: .deltabalance_prefs.json, clave "supabase_session"
({access_token, refresh_token, user_id, email, expires_at} — el archivo
está en .gitignore). Así la app no pide login cada vez: con una sesión
guardada, is_logged_in() es True sin tocar la red (se puede trabajar sin
conexión), y recién refresh_session() — que llama SyncEngine antes de cada
sincronización — la carga en el cliente y renueva el token si venció.
Cada renovación automática del cliente (evento TOKEN_REFRESHED) vuelve a
guardar los tokens nuevos.

Nombre de display: prefs, clave "display_name". Local de cada
computadora, no sube a Supabase (pedido explícito).

Usuario local de hogares compartidos: hardcodeado por UUID de Supabase en
USUARIOS_LOCALES. Cada login exitoso lo escribe en prefs, clave
"usuario_local" (la misma que lee ui/components/usuario_local.py); un UUID
que no está en el mapa escribe "".

Los mensajes de AuthResult ya vienen en MAYÚSCULAS: la pantalla de login
los muestra tal cual.
"""

from dataclasses import dataclass
from typing import Any, Optional

from supabase_auth.errors import AuthApiError

from sync.supabase_client import ConfiguracionSupabaseError, get_client
from ui.utils.prefs import escribir_pref, leer_pref

PREF_SESION = "supabase_session"
PREF_NOMBRE = "display_name"
PREF_USUARIO_LOCAL = "usuario_local"
EVENTOS_SESION_NUEVA = ("SIGNED_IN", "TOKEN_REFRESHED")

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


class AuthService:
    """
    Usage:
        auth = AuthService()
        if not auth.is_logged_in():
            resultado = auth.login("bruno@mail.com", "…")
        auth.get_user_id()   # UUID de Supabase Auth
    """

    def __init__(self) -> None:
        guardada = leer_pref(PREF_SESION)
        self._sesion: Optional[dict] = guardada if isinstance(guardada, dict) else None
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
        # Token renovado por el cliente (o sesión nueva): se guardan los tokens nuevos.
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
        escribir_pref(PREF_SESION, self._sesion)

    def _olvidar(self) -> None:
        self._sesion = None
        escribir_pref(PREF_SESION, None)

    # ----------------------------------------------------------
    # API
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
            success=True, user_id=respuesta.session.user.id, email=respuesta.session.user.email,
            mensaje="SESIÓN INICIADA.",
        )

    def logout(self) -> None:
        """Cierra la sesión en Supabase (si hay conexión) y la borra de prefs."""
        try:
            self._cliente().auth.sign_out()
        except Exception:
            pass  # sin conexión: igual se olvida la sesión local
        self._olvidar()

    def get_user_id(self) -> Optional[str]:
        return self._sesion.get("user_id") if self._sesion else None

    def get_email(self) -> Optional[str]:
        return self._sesion.get("email") if self._sesion else None

    def get_usuario_local(self) -> Optional[str]:
        """El usuario local hardcodeado para el UUID de la sesión (USUARIOS_LOCALES), o None."""
        user_id = self.get_user_id()
        return USUARIOS_LOCALES.get(user_id)

    def is_logged_in(self) -> bool:
        """Hay una sesión guardada (no toca la red: ver docstring del módulo)."""
        return bool(self._sesion and self._sesion.get("refresh_token") and self._sesion.get("user_id"))

    def refresh_session(self) -> bool:
        """
        Carga la sesión guardada en el cliente, renovando el token si venció.
        Hace red. False si no hay sesión, o si Supabase la rechaza (token
        revocado o vencido del todo: se borra y hay que volver a iniciar
        sesión). Sin conexión, la excepción sube (SyncEngine la muestra como
        SIN CONEXIÓN).
        """
        if not self.is_logged_in():
            return False
        try:
            respuesta = self._cliente().auth.set_session(self._sesion["access_token"], self._sesion["refresh_token"])
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
        """El nombre de display guardado en prefs ("display_name"), o None."""
        nombre = leer_pref(PREF_NOMBRE)
        return nombre.strip() if isinstance(nombre, str) and nombre.strip() else None

    def set_display_name(self, nombre: str) -> None:
        """Guarda el nombre de display en prefs ("display_name"); vacío lo borra."""
        limpio = " ".join((nombre or "").split())
        escribir_pref(PREF_NOMBRE, limpio or None)
