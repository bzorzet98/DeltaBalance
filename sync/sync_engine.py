"""
DeltaBalance — sync/sync_engine.py

Motor de sincronización con Supabase (docs/DATA_MODEL_DECISIONS.md sección
24). Módulo de aplicación: usa el motor de datos (SyncRepository), nunca al
revés.

--- Qué se guarda en Supabase ---

Una tabla genérica, `deltabalance_filas` (sync/supabase_schema.sql): una
fila remota por fila local, con clave (usuario_id, tabla, clave) — la clave
local como texto ('12', o '3|1' si la clave primaria es compuesta) — y la
fila entera en `datos` (jsonb). Por qué genérica y no una tabla remota por
tabla local: el schema local cambia seguido (columnas nuevas vía
db/schema_migrations.py), y con tablas espejo cada columna nueva rompería
la subida hasta tocar Supabase a mano. Además: `borrado` (un borrado local
viaja como marca), `actualizado_local` (cuándo cambió la fila en su
computadora: el reloj de last-write-wins), `hogar_codigo` (solo tablas
compartidas: el codigo_invitacion del hogar, para que RLS deje ver la fila
a los miembros del hogar) y `subido_en` (hora del servidor: desde dónde
bajar). La pertenencia a un hogar vive en `deltabalance_hogar_miembros`
(codigo, usuario_id), que se llena al subir cada hogar.

--- Qué se sube y qué se baja (decisiones con el usuario) ---

- Se SUBE todo lo pendiente de TABLAS_SINCRONIZADAS: lo que registraron los
  triggers en sync_cambios (altas, ediciones y borrados) y las filas que
  nunca se sincronizaron (sincronizado_en NULL, las anteriores a los
  triggers). Después: sincronizado_en = ahora y fuera de sync_cambios.
- Se BAJA solo lo PROPIO (usuario_id = el de la sesión): sirve para
  recuperar la base en otra computadora. Las filas del otro miembro del
  hogar NO se bajan todavía: los ids son locales de cada base (el gasto #12
  de uno no es el #12 del otro, y apunta a una transacción que solo existe
  en la otra base) — eso necesita ids globales y cambios en la pantalla de
  Compartidos (otra tarea).
- Conflicto (la fila cambió de los dos lados): last-write-wins por
  actualizado_local — _resolver_conflicto(). Una fila sin fecha conocida
  (nunca editada desde que existen los triggers) pierde siempre.
- PRIMERA sincronización de un usuario en una computadora (sin marca en
  prefs "sync_ultima_bajada"): primero se baja y lo remoto GANA todo
  conflicto — es una restauración; si no, las categorías que carga el seed
  de una base nueva (recién creadas: más nuevas) pisarían las de Supabase.
  Después se sube lo que no estaba. migration/subir_a_supabase.py deja la
  marca, así su computadora nunca pasa por este caso.

--- Hilos ---

Las sincronizaciones hacen red: se llaman FUERA del hilo de la UI
(page.run_thread / asyncio.to_thread). Cada una abre su PROPIA conexión
SQLite (DatabaseManager con el mismo archivo) en su hilo — sqlite3 no deja
usar una conexión desde otro hilo — y la cierra al terminar. Un lock evita
dos sincronizaciones a la vez. El estado (estado / ultimo_resultado) se
avisa a los oyentes (escuchar()) desde el hilo de la sync: el oyente tiene
que pasar a la UI con page.run_task().
"""

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Optional

from db.database import DatabaseManager
from db.schema_migrations import TABLAS_SINCRONIZADAS
from repositories.sync_repository import COLUMNAS_LOCALES, SyncRepository
from sync.auth import AuthService
from sync.supabase_client import get_client
from ui.utils.prefs import escribir_pref, leer_pref

# Las del pedido + las que dependen de ellas (sin sus cuotas / saldos, una
# compra o una cuenta llegaría incompleta a Supabase).
TABLAS_PRIVADAS = [
    "transacciones", "cuentas", "categorias", "deudas",
    "compras_cuotas", "presupuestos", "ingresos_proyectados",
    "cuentas_saldos", "cuotas_credito", "resumenes_tarjeta", "tarjetas_config",
]
TABLAS_COMPARTIDAS = [
    "gastos_compartidos", "hogares", "hogar_miembros", "gasto_compartido_pagos",
]

TABLA_REMOTA = "deltabalance_filas"
TABLA_MIEMBROS_REMOTA = "deltabalance_hogar_miembros"
CONFLICTO_REMOTO = "usuario_id,tabla,clave"
CONFLICTO_MIEMBROS = "codigo,usuario_id"
LOTE = 500
# No cuentan al comparar una fila bajada con la local (las reescribe la
# propia base: sincronizado_en y el trigger de updated_en).
COLUMNAS_SIN_COMPARAR = ("sincronizado_en", "updated_en")
MAX_ERRORES_EN_MENSAJE = 3

PREF_ULTIMA_BAJADA = "sync_ultima_bajada"
# Marca de "ya sincronizó" cuando no se vio ninguna fila remota todavía.
EPOCA = "1970-01-01T00:00:00+00:00"

ESTADO_SINCRONIZADO = "sincronizado"
ESTADO_SINCRONIZANDO = "sincronizando"
ESTADO_SIN_CONEXION = "sin_conexion"
ESTADO_SIN_SESION = "sin_sesion"


# =============================================================
# RESULTADO
# =============================================================

@dataclass
class SyncResult:
    success: bool
    subidas: int
    bajadas: int
    conflictos: int
    errores: int
    mensaje: str
    duracion_segundos: float


@dataclass
class _Contadores:
    subidas: int = 0
    bajadas: int = 0
    conflictos: int = 0
    errores: int = 0
    detalle_errores: list[str] = field(default_factory=list)
    # Mayor subido_en visto (hora del servidor): desde dónde bajar la próxima vez.
    ultimo_subido_en: Optional[str] = None
    # Borrados bajados: se aplican al final, de hijos a padres (FK).
    borrados: list[tuple[str, dict]] = field(default_factory=list)

    def error(self, texto: str) -> None:
        self.errores += 1
        self.detalle_errores.append(texto)

    def ver_subido_en(self, valor: Optional[str]) -> None:
        if valor and (self.ultimo_subido_en is None or valor > self.ultimo_subido_en):
            self.ultimo_subido_en = valor


class _SinSesion(Exception):
    """No hay sesión (o Supabase la rechazó): hay que iniciar sesión."""


class MarcasEnPrefs:
    """Desde dónde bajar, por usuario: prefs "sync_ultima_bajada" = {usuario_id: subido_en}."""

    def leer(self, usuario_id: str) -> Optional[str]:
        marcas = leer_pref(PREF_ULTIMA_BAJADA, {})
        return marcas.get(usuario_id) if isinstance(marcas, dict) else None

    def escribir(self, usuario_id: str, valor: str) -> None:
        marcas = leer_pref(PREF_ULTIMA_BAJADA, {})
        marcas = marcas if isinstance(marcas, dict) else {}
        marcas[usuario_id] = valor
        escribir_pref(PREF_ULTIMA_BAJADA, marcas)


def _ahora_local() -> str:
    """UTC con milésimas, mismo formato que strftime('%Y-%m-%d %H:%M:%f') de los triggers."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _lotes(elementos: list, tamanio: int) -> Iterator[list]:
    for inicio in range(0, len(elementos), tamanio):
        yield elementos[inicio:inicio + tamanio]


def _valor_json(valor: Any) -> Any:
    return valor.hex() if isinstance(valor, (bytes, bytearray)) else valor


# El motor que usa la app (lo registra ui/app.py): el indicador del Registro
# lo busca acá, sin cambiar la firma de las pantallas.
_MOTOR: Optional["SyncEngine"] = None


def registrar_motor(motor: Optional["SyncEngine"]) -> None:
    global _MOTOR
    _MOTOR = motor


def motor_registrado() -> Optional["SyncEngine"]:
    return _MOTOR


# =============================================================
# MOTOR
# =============================================================

class SyncEngine:
    """
    Usage (app):
        motor = SyncEngine(db, auth)
        page.run_thread(motor.sync_completo)

    Usage (migration/subir_a_supabase.py, service_role, sin sesión):
        motor = SyncEngine(db, None, cliente=get_service_client(), usuario_id=uuid)
        motor.subir_todo(tablas)
    """

    def __init__(
        self,
        db: DatabaseManager,
        auth: Optional[AuthService],
        cliente: Any = None,
        usuario_id: Optional[str] = None,
        marcas: Optional[Any] = None,
    ):
        self._db_path = db.db_path
        self._auth = auth
        # Perezoso: sin .env, la app arranca igual (la sync falla con un mensaje).
        self._client = cliente
        self._usuario_fijo = usuario_id
        self._marcas = marcas if marcas is not None else MarcasEnPrefs()
        self._lock = threading.Lock()
        self._oyentes: dict[str, Callable[["SyncEngine"], None]] = {}
        self.estado = ESTADO_SIN_SESION if not self.hay_sesion() else ESTADO_SINCRONIZANDO
        self.ultimo_resultado: Optional[SyncResult] = None
        self.ultima_sync: Optional[datetime] = None

    # ----------------------------------------------------------
    # ESTADO Y OYENTES
    # ----------------------------------------------------------

    def hay_sesion(self) -> bool:
        return self._usuario_fijo is not None or (self._auth is not None and self._auth.is_logged_in())

    def escuchar(self, clave: str, oyente: Callable[["SyncEngine"], None]) -> None:
        """Registra (o reemplaza, por clave) un oyente de cambios de estado. Se llama desde el hilo de la sync."""
        self._oyentes[clave] = oyente

    def dejar_de_escuchar(self, clave: str) -> None:
        self._oyentes.pop(clave, None)

    def _cambiar_estado(self, estado: str) -> None:
        self.estado = estado
        for oyente in list(self._oyentes.values()):
            try:
                oyente(self)
            except Exception:
                pass  # un oyente roto (ej. una pantalla que ya no está) no corta la sync

    def _cliente(self) -> Any:
        if self._client is None:
            self._client = get_client()
        return self._client

    # ----------------------------------------------------------
    # API PÚBLICA
    # ----------------------------------------------------------

    def sync_completo(self) -> SyncResult:
        """
        1. Subir lo pendiente (sync_cambios + sincronizado_en IS NULL).
        2. Bajar lo propio cambiado en Supabase desde la última sync.
        3. Conflictos: last-write-wins (_resolver_conflicto()).
        En la primera sincronización de esta computadora, al revés: bajar
        primero, con lo remoto ganando (ver docstring del módulo).
        """
        return self._correr(self._completo)

    def sync_fila(self, tabla: str, fila_id: int) -> SyncResult:
        """Sube ya una fila (tabla con clave `id`) si está pendiente — para llamar al guardar/editar."""
        if tabla not in TABLAS_SINCRONIZADAS:
            raise ValueError(f"'{tabla}' no es una tabla sincronizada.")

        def _trabajo(repo: SyncRepository, usuario_id: str, c: _Contadores) -> None:
            c.subidas += self._subir_pendientes(repo, tabla, usuario_id, c, solo_clave=str(fila_id))

        return self._correr(_trabajo)

    def sync_tabla_compartida(self, tabla: str) -> SyncResult:
        """Sube y baja una tabla compartida — la llama la app cada 5 minutos."""
        if tabla not in TABLAS_COMPARTIDAS:
            raise ValueError(f"'{tabla}' no es una tabla compartida.")
        return self.sync_tablas([tabla])

    def sync_tablas(self, tablas: Iterable[str]) -> SyncResult:
        """Sube y baja solo esas tablas (en orden de dependencias). No mueve la marca global de bajada."""
        elegidas = [t for t in TABLAS_SINCRONIZADAS if t in set(tablas)]

        def _trabajo(repo: SyncRepository, usuario_id: str, c: _Contadores) -> None:
            desde = self._marcas.leer(usuario_id)
            if desde is None:
                return  # nunca hubo una sync completa en esta computadora: primero esa
            for tabla in elegidas:
                c.subidas += self._subir_pendientes(repo, tabla, usuario_id, c)
            for tabla in elegidas:
                c.bajadas += self._bajar_cambios(repo, tabla, usuario_id, desde, c)
            self._aplicar_borrados(repo, c)

        return self._correr(_trabajo)

    def contar_pendientes(self) -> int:
        """Filas locales que faltan subir (abre y cierra su propia conexión)."""
        db = DatabaseManager(db_path=self._db_path)
        try:
            return SyncRepository(db).contar_pendientes()
        finally:
            db.desconectar()

    def subir_todo(self, tablas: Iterable[str]) -> dict[str, int]:
        """
        Migración inicial (migration/subir_a_supabase.py): sube TODAS las
        filas de esas tablas, sin mirar conflictos, las marca como
        sincronizadas y deja la marca de bajada (esta computadora ya no hace
        la "primera sincronización"). Devuelve cuántas filas subió por tabla.
        Lanza la excepción de red o de Supabase tal cual.
        """
        usuario_id = self._usuario_para_sync()
        db = DatabaseManager(db_path=self._db_path)
        c = _Contadores()
        subidas: dict[str, int] = {}
        try:
            repo = SyncRepository(db)
            for tabla in [t for t in TABLAS_SINCRONIZADAS if t in set(tablas)]:
                leido_en = _ahora_local()
                entradas = [
                    {"clave": repo.clave_de(tabla, fila), "operacion": "guardado", "modificado_en": None, "fila": fila}
                    for fila in repo.todas(tabla)
                ]
                subidas[tabla] = self._subir_entradas(repo, tabla, usuario_id, entradas, leido_en, c)
        finally:
            db.desconectar()
        self._marcas.escribir(usuario_id, c.ultimo_subido_en or EPOCA)
        return subidas

    # ----------------------------------------------------------
    # EJECUCIÓN (lock, conexión propia, estado)
    # ----------------------------------------------------------

    def _usuario_para_sync(self) -> str:
        if self._usuario_fijo is not None:
            return self._usuario_fijo
        if self._auth is None or not self._auth.is_logged_in():
            raise _SinSesion()
        if not self._auth.refresh_session():  # sin conexión: la excepción sube
            raise _SinSesion()
        usuario_id = self._auth.get_user_id()
        if not usuario_id:
            raise _SinSesion()
        return usuario_id

    def _correr(self, trabajo: Callable[[SyncRepository, str, _Contadores], None]) -> SyncResult:
        inicio = time.perf_counter()
        if not self._lock.acquire(blocking=False):
            return SyncResult(False, 0, 0, 0, 0, "YA HAY UNA SINCRONIZACIÓN EN CURSO.", 0.0)
        c = _Contadores()
        db: Optional[DatabaseManager] = None
        try:
            self._cambiar_estado(ESTADO_SINCRONIZANDO)
            usuario_id = self._usuario_para_sync()
            db = DatabaseManager(db_path=self._db_path)  # conexión propia de este hilo
            trabajo(SyncRepository(db), usuario_id, c)
            estado, exito = ESTADO_SINCRONIZADO, c.errores == 0
            mensaje = (
                f"{c.subidas} SUBIDA(S), {c.bajadas} BAJADA(S), {c.conflictos} CONFLICTO(S), "
                f"{c.errores} ERROR(ES)"
            )
            if c.detalle_errores:
                mensaje += ": " + " | ".join(c.detalle_errores[:MAX_ERRORES_EN_MENSAJE])
            self.ultima_sync = datetime.now()
        except _SinSesion:
            estado, exito, mensaje = ESTADO_SIN_SESION, False, "INICIÁ SESIÓN PARA SINCRONIZAR."
        except Exception as err:
            # Sin internet, Supabase caído, .env incompleto, tabla remota sin crear…
            estado, exito, mensaje = ESTADO_SIN_CONEXION, False, f"SIN CONEXIÓN CON SUPABASE: {err}"
        finally:
            if db is not None:
                db.desconectar()
            self._lock.release()
        resultado = SyncResult(
            success=exito, subidas=c.subidas, bajadas=c.bajadas, conflictos=c.conflictos, errores=c.errores,
            mensaje=mensaje, duracion_segundos=time.perf_counter() - inicio,
        )
        self.ultimo_resultado = resultado
        self._cambiar_estado(estado)
        return resultado

    def _completo(self, repo: SyncRepository, usuario_id: str, c: _Contadores) -> None:
        desde = self._marcas.leer(usuario_id)
        primera = desde is None
        if primera:
            for tabla in TABLAS_SINCRONIZADAS:
                c.bajadas += self._bajar_cambios(repo, tabla, usuario_id, None, c, primera=True)
            self._aplicar_borrados(repo, c, primera=True)
            for tabla in TABLAS_SINCRONIZADAS:
                c.subidas += self._subir_pendientes(repo, tabla, usuario_id, c, primera=True)
        else:
            for tabla in TABLAS_SINCRONIZADAS:
                c.subidas += self._subir_pendientes(repo, tabla, usuario_id, c)
            for tabla in TABLAS_SINCRONIZADAS:
                c.bajadas += self._bajar_cambios(repo, tabla, usuario_id, desde, c)
            self._aplicar_borrados(repo, c)
        self._marcas.escribir(usuario_id, max(filter(None, [desde, c.ultimo_subido_en]), default=EPOCA))

    # ----------------------------------------------------------
    # SUBIR
    # ----------------------------------------------------------

    @staticmethod
    def _marca_local(entrada: dict) -> str:
        """Cuándo cambió la fila local: su modificado_en, o updated_en/creada_en; "" si no se sabe."""
        fila = entrada.get("fila") or {}
        return entrada.get("modificado_en") or fila.get("updated_en") or fila.get("creada_en") or ""

    def _subir_pendientes(
        self,
        repo: SyncRepository,
        tabla: str,
        usuario_id: str,
        c: _Contadores,
        primera: bool = False,
        solo_clave: Optional[str] = None,
    ) -> int:
        """
        Sube las filas pendientes de una tabla (con usuario_id). Antes mira
        lo que ya hay en Supabase: si la versión remota es más nueva (o es
        la primera sincronización), gana la remota y se aplica acá en vez de
        subir. Después de subir: sincronizado_en = ahora. Devuelve cuántas
        filas subió.
        """
        leido_en = _ahora_local()
        pendientes = repo.pendientes(tabla)
        if solo_clave is not None:
            pendientes = [p for p in pendientes if p["clave"] == solo_clave]
        subidas = 0
        for lote in _lotes(pendientes, LOTE):
            remotos = self._remotos_por_clave(tabla, usuario_id, [p["clave"] for p in lote])
            a_subir: list[dict] = []
            with repo.escritura_sync() as conn:
                for entrada in lote:
                    remoto = remotos.get(entrada["clave"])
                    if remoto is not None and self._resolver_conflicto(entrada, remoto, primera) is remoto:
                        # Gana Supabase: se aplica acá, salvo que la fila haya vuelto a cambiar recién.
                        c.conflictos += 1
                        cambio = repo.cambio(tabla, entrada["clave"])
                        if (cambio or {}).get("modificado_en") == entrada["modificado_en"]:
                            if self._aplicar(repo, conn, tabla, remoto, c):
                                c.bajadas += 1
                        continue
                    if entrada["operacion"] == "borrado" and remoto is None:
                        repo.quitar_cambio(conn, tabla, entrada["clave"])  # nunca llegó a subir: no hay nada que borrar
                        continue
                    entrada["hogar_codigo_remoto"] = (remoto or {}).get("hogar_codigo")
                    a_subir.append(entrada)
            subidas += self._subir_entradas(repo, tabla, usuario_id, a_subir, leido_en, c)
        return subidas

    def _subir_entradas(
        self, repo: SyncRepository, tabla: str, usuario_id: str, entradas: list[dict], leido_en: str, c: _Contadores,
    ) -> int:
        """Upsert de esas entradas en Supabase y las marca sincronizadas acá."""
        subidas = 0
        for lote in _lotes(entradas, LOTE):
            filas = [self._fila_remota(repo, tabla, usuario_id, entrada) for entrada in lote]
            respuesta = self._cliente().table(TABLA_REMOTA).upsert(filas, on_conflict=CONFLICTO_REMOTO).execute()
            for fila in respuesta.data or []:
                c.ver_subido_en(fila.get("subido_en"))
            momento = _ahora_local()
            with repo.escritura_sync() as conn:
                for entrada in lote:
                    repo.marcar_sincronizada(conn, tabla, entrada["clave"], entrada["modificado_en"] or leido_en, momento)
            if tabla == "hogares":
                self._subir_membresias(usuario_id, filas)
            subidas += len(lote)
        return subidas

    def _fila_remota(self, repo: SyncRepository, tabla: str, usuario_id: str, entrada: dict) -> dict:
        fila = entrada.get("fila")
        borrado = entrada["operacion"] == "borrado" or fila is None
        datos = {} if borrado else {c: _valor_json(v) for c, v in fila.items() if c not in COLUMNAS_LOCALES}
        if tabla not in TABLAS_COMPARTIDAS:
            hogar_codigo = None
        elif borrado:
            hogar_codigo = entrada.get("hogar_codigo_remoto")  # el del hogar donde estaba: el otro miembro ve el borrado
        else:
            hogar_codigo = repo.hogar_codigo(tabla, fila)
        return {
            "usuario_id": usuario_id,
            "tabla": tabla,
            "clave": entrada["clave"],
            "datos": datos,
            "hogar_codigo": hogar_codigo,
            "borrado": borrado,
            "actualizado_local": self._marca_local(entrada) or None,
        }

    def _subir_membresias(self, usuario_id: str, filas_hogares: list[dict]) -> None:
        """Quien sube un hogar es miembro de ese hogar en Supabase (por su codigo_invitacion)."""
        membresias = [
            {"codigo": fila["hogar_codigo"], "usuario_id": usuario_id}
            for fila in filas_hogares if not fila["borrado"] and fila["hogar_codigo"]
        ]
        if membresias:
            self._cliente().table(TABLA_MIEMBROS_REMOTA).upsert(membresias, on_conflict=CONFLICTO_MIEMBROS).execute()

    def _remotos_por_clave(self, tabla: str, usuario_id: str, claves: list[str]) -> dict[str, dict]:
        if not claves:
            return {}
        respuesta = (
            self._cliente().table(TABLA_REMOTA)
            .select("clave,datos,borrado,actualizado_local,hogar_codigo,subido_en")
            .eq("usuario_id", usuario_id).eq("tabla", tabla).in_("clave", claves)
            .execute()
        )
        return {fila["clave"]: fila for fila in respuesta.data or []}

    # ----------------------------------------------------------
    # BAJAR
    # ----------------------------------------------------------

    def _filas_remotas(self, tabla: str, usuario_id: str, desde: Optional[str]) -> Iterator[dict]:
        inicio = 0
        while True:
            consulta = self._cliente().table(TABLA_REMOTA).select("*").eq("usuario_id", usuario_id).eq("tabla", tabla)
            if desde:
                consulta = consulta.gt("subido_en", desde)
            respuesta = consulta.order("subido_en").order("clave").range(inicio, inicio + LOTE - 1).execute()
            filas = respuesta.data or []
            yield from filas
            if len(filas) < LOTE:
                return
            inicio += LOTE

    def _bajar_cambios(
        self, repo: SyncRepository, tabla: str, usuario_id: str, desde: Optional[str], c: _Contadores,
        primera: bool = False,
    ) -> int:
        """
        Baja las filas propias de una tabla subidas después de `desde` (None
        = todas) y las inserta o actualiza acá. Los borrados se guardan para
        el final (_aplicar_borrados()). Devuelve cuántas filas cambió.
        """
        bajadas = 0
        remotas = list(self._filas_remotas(tabla, usuario_id, desde))
        for lote in _lotes(remotas, LOTE):
            with repo.escritura_sync() as conn:
                for remota in lote:
                    c.ver_subido_en(remota.get("subido_en"))
                    if remota.get("borrado"):
                        c.borrados.append((tabla, remota))
                        continue
                    if self._debe_aplicarse(repo, tabla, remota, c, primera) and self._aplicar(repo, conn, tabla, remota, c):
                        bajadas += 1
        return bajadas

    def _aplicar_borrados(self, repo: SyncRepository, c: _Contadores, primera: bool = False) -> None:
        """Los borrados bajados, de hijos a padres (al revés de TABLAS_SINCRONIZADAS)."""
        orden = {tabla: i for i, tabla in enumerate(TABLAS_SINCRONIZADAS)}
        borrados = sorted(c.borrados, key=lambda par: -orden[par[0]])
        c.borrados = []
        if not borrados:
            return
        with repo.escritura_sync() as conn:
            for tabla, remota in borrados:
                if repo.fila(tabla, remota["clave"]) is None:
                    repo.quitar_cambio(conn, tabla, remota["clave"])
                    continue
                if self._debe_aplicarse(repo, tabla, remota, c, primera) and self._aplicar(repo, conn, tabla, remota, c):
                    c.bajadas += 1

    def _debe_aplicarse(self, repo: SyncRepository, tabla: str, remota: dict, c: _Contadores, primera: bool) -> bool:
        """¿La fila bajada pisa la local? No si es igual (el eco de lo que subió esta misma computadora) o si pierde el conflicto."""
        local = repo.fila(tabla, remota["clave"])
        cambio = repo.cambio(tabla, remota["clave"])
        pendiente = cambio is not None or (local is not None and local.get("sincronizado_en") is None)
        if not pendiente:
            return not self._iguales(local, remota)
        c.conflictos += 1
        entrada = {"modificado_en": (cambio or {}).get("modificado_en"), "fila": local}
        return self._resolver_conflicto(entrada, remota, primera) is remota

    @staticmethod
    def _iguales(local: Optional[dict], remota: dict) -> bool:
        if local is None or remota.get("borrado"):
            return False
        datos = remota.get("datos") or {}
        return all(
            local.get(columna) == _valor_json(valor) if columna in local else True
            for columna, valor in datos.items() if columna not in COLUMNAS_SIN_COMPARAR
        )

    def _aplicar(self, repo: SyncRepository, conn: sqlite3.Connection, tabla: str, remota: dict, c: _Contadores) -> bool:
        """Escribe acá la versión remota (o la borra). False si no se pudo (ej. una FK): se cuenta como error."""
        clave = remota["clave"]
        try:
            if remota.get("borrado"):
                repo.borrar_fila(conn, tabla, clave)
            else:
                repo.guardar_fila(conn, tabla, remota.get("datos") or {}, _ahora_local())
        except sqlite3.Error as err:
            c.error(f"{tabla} {clave}: {err}")
            return False
        repo.quitar_cambio(conn, tabla, clave)
        return True

    # ----------------------------------------------------------
    # CONFLICTOS
    # ----------------------------------------------------------

    def _resolver_conflicto(self, local: dict, remoto: dict, primera: bool = False) -> dict:
        """
        Last-write-wins: devuelve el que gana — `remoto` si su
        actualizado_local es posterior al de la fila local, si no `local`
        (empate: local, que se vuelve a subir). Una fila sin fecha conocida
        pierde. En la primera sincronización de la computadora gana siempre
        la remota (restauración: ver docstring del módulo).
        """
        if primera:
            return remoto
        return remoto if (remoto.get("actualizado_local") or "") > self._marca_local(local) else local
