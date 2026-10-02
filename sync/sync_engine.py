"""
DeltaBalance — sync/sync_engine.py

Motor de sincronización con Supabase (docs/DATA_MODEL_DECISIONS.md sección
24). Módulo de aplicación: usa el motor de datos (SyncRepository), nunca al
revés.

--- Qué se guarda en Supabase ---

Dos tablas genéricas, una fila remota por fila local, con la fila entera en
`datos` (texto JSON, json.dumps):
- `deltabalance_filas` (uuid, usuario_id, tabla, datos, created_at,
  updated_at): las tablas privadas, con el usuario_id de la sesión.
- `deltabalance_compartidos` (uuid, hogar_uuid, tabla, datos, created_at,
  updated_at): TABLAS_COMPARTIDAS, con el id del hogar de la fila (hogares:
  el propio; gasto_compartido_pagos: el de su gasto).
`uuid` (la clave del upsert) es el id de la fila — ya es un UUID (sección
25), el mismo en toda computadora —, o uuid_compuesto() si la clave
primaria es compuesta (CLAVES_SYNC: cuentas_saldos, hogar_miembros): un
UUID v5 de la tabla y la clave local, el mismo cálculo que
migration/subir_a_supabase.py. Dentro de `datos` viajan dos marcas que no
son columnas (SyncRepository.guardar_fila() las ignora): MARCA_BORRADO (un
borrado local sube como {clave primaria, "_borrado": true}) y
MARCA_MODIFICADO (cuándo cambió la fila en su computadora: el reloj de
last-write-wins). `updated_at` es la hora del servidor de la última
escritura — desde dónde bajar —: la app no la manda, Supabase la tiene que
actualizar en cada upsert. Por qué genéricas y no una tabla remota por
tabla local: el schema local cambia seguido (columnas nuevas vía
db/schema_migrations.py), y con tablas espejo cada columna nueva rompería
la subida hasta tocar Supabase a mano.

--- Qué se sube y qué se baja (decisiones con el usuario) ---

- Se SUBE todo lo pendiente de TABLAS_SINCRONIZADAS: lo que registraron los
  triggers en sync_cambios (altas, ediciones y borrados) y las filas que
  nunca se sincronizaron (sincronizado_en NULL, las anteriores a los
  triggers). Después: sincronizado_en = ahora y fuera de sync_cambios. Una
  fila compartida sin hogar encontrable no se sube (cuenta como error).
- Se BAJA lo PROPIO de deltabalance_filas (usuario_id = el de la sesión) —
  sirve para recuperar la base en otra computadora — y, de
  deltabalance_compartidos, las filas de los hogares que YA están en esta
  base, incluidas las del otro miembro del hogar. Un hogar que todavía no
  está acá no se baja: restaurar en una computadora nueva no trae las
  compartidas. Una fila del otro miembro que apunta a algo que solo existe
  en su base (ej. gasto_compartido_pagos.transaccion_id) falla por FK y se
  cuenta como error.
- Restaurar sobre una base nueva: el seed ya trae categorías y "Caja
  Efectivo" con UUIDs propios. SyncRepository.guardar_fila() las reconoce
  por clave natural (CLAVES_NATURALES) y les pone el id remoto, en vez de
  chocar con el UNIQUE.
- Conflicto (la fila cambió de los dos lados): last-write-wins por
  MARCA_MODIFICADO (o updated_en / creada_en de `datos`) —
  _resolver_conflicto(). Una fila sin fecha conocida (nunca editada desde
  que existen los triggers) pierde siempre.
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

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Optional

from db.database import DatabaseManager
from db.schema_migrations import CLAVES_SYNC, TABLAS_SINCRONIZADAS, claves_primarias
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

TABLA_REMOTA = "deltabalance_filas"  # las privadas
TABLA_COMPARTIDA_REMOTA = "deltabalance_compartidos"
CONFLICTO_REMOTO = "uuid"
LOTE = 500
# No cuentan al comparar una fila bajada con la local (las reescribe la
# propia base: sincronizado_en y el trigger de updated_en).
COLUMNAS_SIN_COMPARAR = ("sincronizado_en", "updated_en")
MAX_ERRORES_EN_MENSAJE = 3

# Marcas dentro de `datos` (no son columnas: guardar_fila() las ignora).
MARCA_BORRADO = "_borrado"
MARCA_MODIFICADO = "_modificado_en"
# Espacio de nombres de uuid_compuesto(). Tiene que ser el mismo que el de
# migration/subir_a_supabase.py (NAMESPACE_URL): con otro, la misma fila
# caería en otra fila remota.
NAMESPACE_CLAVE_COMPUESTA = uuid.NAMESPACE_URL

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
    # Mayor updated_at visto (hora del servidor): desde dónde bajar la próxima vez.
    ultimo_updated_at: Optional[str] = None
    # Borrados bajados: se aplican al final, de hijos a padres (FK).
    borrados: list[tuple[str, dict]] = field(default_factory=list)

    def error(self, texto: str) -> None:
        self.errores += 1
        self.detalle_errores.append(texto)

    def ver_updated_at(self, valor: Optional[str]) -> None:
        if valor and (self.ultimo_updated_at is None or valor > self.ultimo_updated_at):
            self.ultimo_updated_at = valor


class _SinSesion(Exception):
    """No hay sesión (o Supabase la rechazó): hay que iniciar sesión."""


class MarcasEnPrefs:
    """Desde dónde bajar, por usuario: prefs "sync_ultima_bajada" = {usuario_id: updated_at}."""

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


def uuid_compuesto(tabla: str, clave: str) -> str:
    """UUID v5 de una fila con clave primaria compuesta (clave local: '<uuid>|1'). Mismo cálculo que migration/subir_a_supabase.py."""
    return str(uuid.uuid5(NAMESPACE_CLAVE_COMPUESTA, f"deltabalance://{tabla}/{clave}"))


def uuid_remoto(tabla: str, clave: str) -> str:
    """El `uuid` de una fila en Supabase: su id (ya es un UUID), o uuid_compuesto() si su clave primaria es compuesta."""
    return uuid_compuesto(tabla, clave) if tabla in CLAVES_SYNC else clave.lower()


def _tabla_remota(tabla: str) -> str:
    return TABLA_COMPARTIDA_REMOTA if tabla in TABLAS_COMPARTIDAS else TABLA_REMOTA


def _normalizar_remota(tabla: str, remota: dict) -> dict:
    """
    Deja `datos` como dict — la app lo sube como texto JSON (json.dumps);
    migration/subir_a_supabase.py, como objeto — y agrega "clave_local"
    (la clave primaria local como texto, sacada de `datos`; None si `datos`
    no la trae).
    """
    datos = remota.get("datos")
    if isinstance(datos, str):
        try:
            datos = json.loads(datos) if datos else {}
        except ValueError:
            datos = {}
    remota["datos"] = datos if isinstance(datos, dict) else {}
    try:
        remota["clave_local"] = SyncRepository.clave_de(tabla, remota["datos"])
    except KeyError:
        remota["clave_local"] = None
    return remota


def _es_borrado(remota: dict) -> bool:
    return bool(remota["datos"].get(MARCA_BORRADO))


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

    Usage (service_role, sin sesión):
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
        2. Bajar lo propio y lo de mis hogares cambiado en Supabase desde la última sync.
        3. Conflictos: last-write-wins (_resolver_conflicto()).
        En la primera sincronización de esta computadora, al revés: bajar
        primero, con lo remoto ganando (ver docstring del módulo).
        """
        return self._correr(self._completo)

    def sync_fila(self, tabla: str, fila_id: str) -> SyncResult:
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
        Subida inicial: sube TODAS las filas de esas tablas, sin mirar
        conflictos, las marca como sincronizadas y deja la marca de bajada
        (esta computadora ya no hace la "primera sincronización"). Devuelve
        cuántas filas subió por tabla. Lanza la excepción de red o de
        Supabase tal cual, y RuntimeError al final si alguna fila no se pudo
        subir (ej. una compartida sin hogar).
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
        if c.detalle_errores:
            raise RuntimeError(f"{c.errores} fila(s) sin subir: " + " | ".join(c.detalle_errores[:MAX_ERRORES_EN_MENSAJE]))
        self._marcas.escribir(usuario_id, c.ultimo_updated_at or EPOCA)
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
        self._marcas.escribir(usuario_id, max(filter(None, [desde, c.ultimo_updated_at]), default=EPOCA))

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
        Sube las filas pendientes de una tabla. Antes mira lo que ya hay en
        Supabase: si la versión remota es más nueva (o es la primera
        sincronización), gana la remota y se aplica acá en vez de subir.
        Después de subir: sincronizado_en = ahora. Devuelve cuántas filas
        subió.
        """
        leido_en = _ahora_local()
        pendientes = repo.pendientes(tabla)
        if solo_clave is not None:
            pendientes = [p for p in pendientes if p["clave"] == solo_clave]
        subidas = 0
        for lote in _lotes(pendientes, LOTE):
            remotos = self._remotos_por_uuid(tabla, usuario_id, [uuid_remoto(tabla, p["clave"]) for p in lote])
            a_subir: list[dict] = []
            with repo.escritura_sync() as conn:
                for entrada in lote:
                    remoto = remotos.get(uuid_remoto(tabla, entrada["clave"]))
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
                    entrada["hogar_uuid_remoto"] = (remoto or {}).get("hogar_uuid")
                    a_subir.append(entrada)
            subidas += self._subir_entradas(repo, tabla, usuario_id, a_subir, leido_en, c)
        return subidas

    def _subir_entradas(
        self, repo: SyncRepository, tabla: str, usuario_id: str, entradas: list[dict], leido_en: str, c: _Contadores,
    ) -> int:
        """Upsert de esas entradas en Supabase y las marca sincronizadas acá."""
        subidas = 0
        for lote in _lotes(entradas, LOTE):
            pares: list[tuple[dict, dict]] = []
            for entrada in lote:
                remota = self._fila_remota(repo, tabla, usuario_id, entrada)
                if remota is None:
                    c.error(f"{tabla} {entrada['clave']}: no se encontró su hogar")
                    continue
                pares.append((entrada, remota))
            if not pares:
                continue
            respuesta = (
                self._cliente().table(_tabla_remota(tabla))
                .upsert([remota for _, remota in pares], on_conflict=CONFLICTO_REMOTO).execute()
            )
            for fila in respuesta.data or []:
                c.ver_updated_at(fila.get("updated_at"))
            momento = _ahora_local()
            with repo.escritura_sync() as conn:
                for entrada, _ in pares:
                    repo.marcar_sincronizada(conn, tabla, entrada["clave"], entrada["modificado_en"] or leido_en, momento)
            subidas += len(pares)
        return subidas

    def _fila_remota(self, repo: SyncRepository, tabla: str, usuario_id: str, entrada: dict) -> Optional[dict]:
        """La fila para el upsert, o None si es compartida y no se encuentra su hogar."""
        fila = entrada.get("fila")
        clave = entrada["clave"]
        borrado = entrada["operacion"] == "borrado" or fila is None
        if borrado:
            # Viaja la clave primaria: con ella la otra computadora sabe qué
            # fila borrar (el uuid de una clave compuesta no se puede invertir).
            datos = dict(zip(claves_primarias(tabla), repo.valores_de_clave(tabla, clave)))
            datos[MARCA_BORRADO] = True
        else:
            datos = {col: _valor_json(v) for col, v in fila.items() if col not in COLUMNAS_LOCALES}
        marca = self._marca_local(entrada)
        if marca:
            datos[MARCA_MODIFICADO] = marca
        remota: dict[str, Any] = {"uuid": uuid_remoto(tabla, clave), "tabla": tabla, "datos": json.dumps(datos)}
        if tabla not in TABLAS_COMPARTIDAS:
            remota["usuario_id"] = usuario_id
            return remota
        # Borrado: el hogar donde estaba, así el otro miembro ve el borrado.
        hogar_uuid = entrada.get("hogar_uuid_remoto") if borrado else self._hogar_de(repo, tabla, fila)
        if not hogar_uuid:
            return None
        remota["hogar_uuid"] = hogar_uuid
        return remota

    @staticmethod
    def _hogar_de(repo: SyncRepository, tabla: str, fila: dict) -> Optional[str]:
        """El id del hogar de una fila compartida (None si no se encuentra)."""
        if tabla == "hogares":
            return fila.get("id")
        if tabla == "gasto_compartido_pagos":
            gasto = repo.fila("gastos_compartidos", str(fila.get("gasto_compartido_id")))
            return gasto.get("hogar_id") if gasto is not None else None
        return fila.get("hogar_id")

    def _remotos_por_uuid(self, tabla: str, usuario_id: str, uuids: list[str]) -> dict[str, dict]:
        if not uuids:
            return {}
        consulta = self._cliente().table(_tabla_remota(tabla)).select("*").eq("tabla", tabla)
        if tabla not in TABLAS_COMPARTIDAS:
            consulta = consulta.eq("usuario_id", usuario_id)
        respuesta = consulta.in_("uuid", uuids).execute()
        return {str(fila["uuid"]).lower(): _normalizar_remota(tabla, fila) for fila in respuesta.data or []}

    # ----------------------------------------------------------
    # BAJAR
    # ----------------------------------------------------------

    @staticmethod
    def _mis_hogares(repo: SyncRepository) -> list[str]:
        return [hogar["id"] for hogar in repo.todas("hogares")]

    def _filas_remotas(self, repo: SyncRepository, tabla: str, usuario_id: str, desde: Optional[str]) -> Iterator[dict]:
        """Las propias (privadas) o las de mis hogares (compartidas) de esa tabla, con updated_at posterior a `desde`."""
        hogares = self._mis_hogares(repo) if tabla in TABLAS_COMPARTIDAS else []
        if tabla in TABLAS_COMPARTIDAS and not hogares:
            return
        inicio = 0
        while True:
            consulta = self._cliente().table(_tabla_remota(tabla)).select("*").eq("tabla", tabla)
            if tabla in TABLAS_COMPARTIDAS:
                consulta = consulta.in_("hogar_uuid", hogares)
            else:
                consulta = consulta.eq("usuario_id", usuario_id)
            if desde:
                consulta = consulta.gt("updated_at", desde)
            respuesta = consulta.order("updated_at").order("uuid").range(inicio, inicio + LOTE - 1).execute()
            filas = respuesta.data or []
            for fila in filas:
                yield _normalizar_remota(tabla, fila)
            if len(filas) < LOTE:
                return
            inicio += LOTE

    def _bajar_cambios(
        self, repo: SyncRepository, tabla: str, usuario_id: str, desde: Optional[str], c: _Contadores,
        primera: bool = False,
    ) -> int:
        """
        Baja las filas de una tabla escritas después de `desde` (None =
        todas) y las inserta o actualiza acá. Los borrados se guardan para
        el final (_aplicar_borrados()). Devuelve cuántas filas cambió.
        """
        bajadas = 0
        remotas = list(self._filas_remotas(repo, tabla, usuario_id, desde))
        for lote in _lotes(remotas, LOTE):
            with repo.escritura_sync() as conn:
                for remota in lote:
                    c.ver_updated_at(remota.get("updated_at"))
                    if remota["clave_local"] is None:
                        c.error(f"{tabla} {remota.get('uuid')}: `datos` sin clave primaria")
                        continue
                    if _es_borrado(remota):
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
                if repo.fila(tabla, remota["clave_local"]) is None:
                    repo.quitar_cambio(conn, tabla, remota["clave_local"])
                    continue
                if self._debe_aplicarse(repo, tabla, remota, c, primera) and self._aplicar(repo, conn, tabla, remota, c):
                    c.bajadas += 1

    def _debe_aplicarse(self, repo: SyncRepository, tabla: str, remota: dict, c: _Contadores, primera: bool) -> bool:
        """¿La fila bajada pisa la local? No si es igual (el eco de lo que subió esta misma computadora) o si pierde el conflicto."""
        local = repo.fila(tabla, remota["clave_local"])
        cambio = repo.cambio(tabla, remota["clave_local"])
        pendiente = cambio is not None or (local is not None and local.get("sincronizado_en") is None)
        if not pendiente:
            return not self._iguales(local, remota)
        c.conflictos += 1
        entrada = {"modificado_en": (cambio or {}).get("modificado_en"), "fila": local}
        return self._resolver_conflicto(entrada, remota, primera) is remota

    @staticmethod
    def _iguales(local: Optional[dict], remota: dict) -> bool:
        if local is None or _es_borrado(remota):
            return False
        return all(
            local.get(columna) == _valor_json(valor) if columna in local else True
            for columna, valor in remota["datos"].items() if columna not in COLUMNAS_SIN_COMPARAR
        )

    def _aplicar(self, repo: SyncRepository, conn: sqlite3.Connection, tabla: str, remota: dict, c: _Contadores) -> bool:
        """Escribe acá la versión remota (o la borra). False si no se pudo (ej. una FK): se cuenta como error."""
        clave = remota["clave_local"]
        if clave is None:
            c.error(f"{tabla} {remota.get('uuid')}: `datos` sin clave primaria")
            return False
        try:
            if _es_borrado(remota):
                repo.borrar_fila(conn, tabla, clave)
            else:
                repo.guardar_fila(conn, tabla, remota["datos"], _ahora_local())
        except sqlite3.Error as err:
            c.error(f"{tabla} {clave}: {err}")
            return False
        repo.quitar_cambio(conn, tabla, clave)
        return True

    # ----------------------------------------------------------
    # CONFLICTOS
    # ----------------------------------------------------------

    @staticmethod
    def _marca_remota(remota: dict) -> str:
        """Cuándo cambió la fila remota en su computadora: MARCA_MODIFICADO, o updated_en/creada_en de `datos`; "" si no se sabe."""
        datos = remota["datos"]
        return datos.get(MARCA_MODIFICADO) or datos.get("updated_en") or datos.get("creada_en") or ""

    def _resolver_conflicto(self, local: dict, remoto: dict, primera: bool = False) -> dict:
        """
        Last-write-wins: devuelve el que gana — `remoto` si su marca
        (_marca_remota()) es posterior a la de la fila local, si no `local`
        (empate: local, que se vuelve a subir). Una fila sin fecha conocida
        pierde. En la primera sincronización de la computadora gana siempre
        la remota (restauración: ver docstring del módulo).
        """
        if primera:
            return remoto
        return remoto if self._marca_remota(remoto) > self._marca_local(local) else local
