"""
DeltaBalance — services/savings_service.py

Purpose:
    Domain service for savings with multiple destinations: financial assets
    (activos_financieros), their movements (compra/venta/rendimiento) and
    allocation to savings goals (objetivos_ahorro) via asignaciones. Data
    access lives entirely in ActivosFinancierosRepository,
    ObjetivosAhorroRepository, MovimientosActivoRepository y
    AsignacionesRepository.

    Fase 2, bloque AHORROS, paso 2: este service se crea DESDE CERO. No
    existe ningún SavingsService previo — el diseño sale de los cuatro
    repositorios (paso 1) y del schema (ver comentario sobre
    CREATE TABLE asignaciones en db/schema.sql y
    docs/DATA_MODEL_DECISIONS.md sección 4).

    register_purchase() deja una compra "libre" (sin asignaciones) cuando no
    se le pasa `asignaciones` — asignarla después queda para un método
    aparte que esta fase no implementa (ver docstring de register_purchase).
    MovimientoNotFoundError queda definida para ese método futuro, que
    necesitará resolver un movimiento_id explícito — ninguno de los cuatro
    métodos de esta fase lo usa todavía.

    Tarea 6b (docs/PROXIMOS_PASOS.md): register_purchase()/register_sale()
    ganaron un parámetro opcional cuenta_id: cuando se pasaba, creaban
    además — dentro de la MISMA transacción atómica que ya arma el
    movimiento (+ asignaciones) — una transacción real vía
    TransaccionesRepository que descuenta/acredita esa cuenta, y vinculaban
    su id en movimientos_activo.transaccion_id. La Tarea 6g (ver más abajo)
    reemplaza ese parámetro explícito por resolución automática desde
    activo["cuenta_id"] — el mecanismo de "crear la transacción real
    vinculada dentro de la misma transacción atómica" sigue exactamente
    igual, solo cambia de dónde sale cuenta_id.

    Tarea 1b (docs/PROXIMOS_PASOS.md): get_or_create_reserved_cash_asset()
    resuelve (o crea si no existía) el activo_financiero genérico tipo='otro'
    que representa "Efectivo reservado en <cuenta>" — uno por cuenta,
    reutilizado en cargas futuras. Vive acá (no en la UI) para que sea
    testeable como motor de datos puro (ver docs/DATA_MODEL_DECISIONS.md
    sección 17) y reusable fuera de ui/components/registro_transacciones.py
    si algún día hace falta (ej. un script de migration/). Desde la Tarea
    6g busca/crea por (cuenta_id, tipo='otro') en vez de por el nombre
    construido — ver su docstring para el detalle.

    Tarea 6g (docs/PROXIMOS_PASOS.md): activos_financieros gana columna
    cuenta_id (migración en db/schema_migrations.py), opcional — vincula un
    activo a la cuenta real desde la que se lo opera. create_activo() la
    recibe opcionalmente. register_purchase()/register_sale() YA NO reciben
    cuenta_id/categoria_id como parámetros explícitos: cuenta_id se resuelve
    solo desde activo["cuenta_id"] (si el activo no tiene cuenta vinculada,
    el movimiento queda informal, igual que cuando antes no se pasaba
    cuenta_id) y categoria_id siempre es el id de la categoría protegida
    'MOVIMIENTO CAPITAL · Ahorro/Inversión' (resuelto por nombre vía
    _get_categoria_ahorro_inversion_id(), nunca hardcodeado ni elegido por
    el caller). Objetivo: simplificar la carga — la cuenta y la categoría
    de un ahorro/inversión son propiedades del activo, no algo para repetir
    en cada movimiento.

    Tarea 6d (docs/PROXIMOS_PASOS.md, rediseño de la pantalla de Ahorros a
    formato Registro):
    - register_sale() cambia de firma: el parámetro `objetivo_id: str`
      obligatorio se reemplaza por `asignaciones: list[dict]` (mismo
      formato que register_purchase()) — una venta ya no fuerza un único
      objetivo al 100%, puede repartirse entre varios. A diferencia de
      register_purchase(), acá `asignaciones` sigue siendo obligatorio y
      no puede quedar vacío (ValueError si lo está) — un retiro siempre
      sale de al menos un objetivo, no existe el equivalente de "compra
      libre" para una venta. Cambio de comportamiento YA probado en
      verify_savings_service.py — actualizado en la misma tarea (los
      casos de "venta a un objetivo único" pasan a expresarse como
      `asignaciones=[{"objetivo_id": ..., "porcentaje": 100.0}]`, más un
      caso nuevo de venta repartida entre dos objetivos).
    - get_balance_por_tipo()/get_balance_por_activo(): dos agregaciones
      de solo lectura nuevas, NINGUNA pasa por asignaciones/
      objetivos_ahorro — son el total real de movimientos_activo
      agrupado por tipo de activo o por activo_id, sin segregar por
      objetivo (a diferencia de get_objetivo_balance()/
      get_balance_por_cuenta(), que sí lo hacen). Pensadas para el
      dashboard de ui/screens/ahorros.py ("Por tipo de ahorro"/"Por
      activo"), ver esos métodos para el detalle completo.

    Rediseño de Ahorros e Inversiones (pedido explícito; schema en
    db/schema_migrations.py — ampliar_tipos_ahorro() — y db/schema.sql):
    - Brokers (get_brokers()) y activos con broker_id y comisiones por
      defecto (create_activo()). Tipos nuevos: 'cedear' y 'plazo_flex'.
    - (Reemplazado, ver "Objetivos por movimiento" más abajo: el reparto
      VIGENTE de cada activo entre objetivos — activo_objetivos,
      assign_objetivo()/remove_objetivo() — del que cada movimiento copiaba
      sus asignaciones.)
    - Movimiento 'aporte' (CHECK ampliado de movimientos_activo.tipo — en
      vez de la columna tipo_movimiento del pedido, decisión del usuario):
      plata que entra a un FCI / plazo, sin unidades. Las agregaciones de
      siempre lo cuentan igual que una compra.
    - registrar_compra()/registrar_venta() (acciones, CEDEARs: cantidad ×
      precio ± comisión), registrar_aporte()/registrar_retiro() (FCI,
      plazos: monto — no estaban en el pedido, son los movimientos de esos
      tipos en el diálogo) y registrar_rendimiento(). Todos pueden vincular
      una transacción del Registro ya existente (transaccion_id); si no, y
      el activo tiene cuenta_id, crean la transacción real como siempre
      (Tarea 6g). Comparten _registrar_movimiento().
    - get_resumen_por_tipo()/get_resumen_por_objetivo(): para la pestaña
      RESUMEN y las tarjetas de cada tipo.
    - Tabla de movimientos por pestaña (pedido explícito: cargar a mano los
      datos históricos, sin transacción asociada sí o sí): registrar_*()
      aceptan crear_transaccion=False (movimiento informal aunque el activo
      tenga cuenta) y update_movement() corrige un movimiento ya cargado
      (solo las notas si está vinculado a una transacción, CLAUDE.md §4).
    Los mensajes nuevos (los de estos métodos) ya vienen en MAYÚSCULAS;
    los de los métodos anteriores siguen en inglés.

    Objetivos por movimiento (pedido del usuario, docs/DATA_MODEL_DECISIONS.md
    sección 31 — vuelve al criterio de la sección 4): cada movimiento dice
    a qué objetivos va y en qué porcentaje, sin un reparto fijo por activo.
    - registrar_*() reciben `asignaciones` ([{"objetivo_id", "porcentaje"}],
      validadas por _porcentajes() antes de escribir; None o [] = sin
      objetivos). Un rendimiento sin asignaciones (None) se reparte según
      get_reparto_proporcional(): lo que cada objetivo tenía en el activo
      antes de esa fecha. Retiros / ventas: solo se valida el total del
      activo (decisión del usuario), un objetivo puede quedar en negativo.
    - update_asignaciones() cambia los objetivos de un movimiento ya cargado.
    - get_resumen_por_tipo()/get_resumen_por_objetivo() calculan la parte de
      cada objetivo sumando lo asignado en cada movimiento
      (_partes_por_objetivo()), no saldo × porcentaje vigente.
    La tabla activo_objetivos queda sin uso (no se borra: puede tener
    datos) — mismo criterio que resumen_cargos_extra. Solo delete_objetivo()
    borra sus filas del objetivo que elimina (la FK lo exige).

    Moneda por movimiento (docs/DATA_MODEL_DECISIONS.md sección 33):
    movimientos_activo.moneda_id. En acciones / CEDEARs un movimiento puede
    ir en otra moneda que el activo (comprar en ARS, vender en USD); en FCI,
    plazos y el resto, siempre la del activo (decisión del usuario: un FCI
    en dólares es otro activo) — _moneda_movimiento(). Como las acciones /
    CEDEARs se cuentan en unidades, lo único en plata que se muestra de
    ellas, el precio promedio, va por moneda (_precios_promedio()). La
    transacción vinculada del Registro va en la moneda del movimiento.
    Compra / venta se cargan por MONTO bruto (cantidad × precio, sin la
    comisión — decisión del usuario), ya no por precio unitario: el precio
    se guarda calculado (monto / cantidad).

    Editar / eliminar / ocultar un instrumento (docs/DATA_MODEL_DECISIONS.md
    sección 35, CLAUDE.md §4): update_activo() cambia nombre, broker,
    cuenta, comisiones por defecto y — solo si todavía no tiene
    movimientos — la moneda; el tipo nunca. delete_activo() solo borra un
    instrumento SIN movimientos; uno con historial se oculta
    (ocultar_activo(), activa = 0) si ya no tiene tenencia (0 unidades en
    acciones / CEDEARs, saldo 0 en el resto), y reactivar_activo() lo
    vuelve a mostrar. get_resumen_por_tipo(incluir_ocultos=True) los trae
    para la pantalla, con activa / movimientos / con_tenencia.

    Edición y eliminación de objetivos (docs/DATA_MODEL_DECISIONS.md sección
    32): update_objetivo() cambia nombre, meta y fecha meta;
    delete_objetivo() lo borra pasando antes su parte de cada instrumento a
    los objetivos que elige el usuario (get_partes_de_objetivo() dice
    cuáles son), lo que no se reparte queda sin asignar.
"""

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from db.database import DatabaseManager
from utils.categorias import clave_categoria
from repositories.activo_objetivos_repository import ActivoObjetivosRepository
from repositories.activos_financieros_repository import ActivosFinancierosRepository
from repositories.brokers_repository import BrokersRepository
from repositories.objetivos_ahorro_repository import ObjetivosAhorroRepository
from repositories.movimientos_activo_repository import MovimientoDuplicadoError, MovimientosActivoRepository
from repositories.asignaciones_repository import AsignacionesRepository
from repositories._sentinels import NO_CAMBIAR
from repositories.transacciones_repository import TransaccionesRepository
from repositories.cuentas_repository import CuentasRepository

# Tipos de activo, en el orden de las pestañas de ui/screens/ahorros.py
# (CHECK de activos_financieros.tipo, db/schema_migrations.py).
TIPOS_ACTIVO = ("fci", "accion", "cedear", "plazo_fijo", "plazo_flex", "cripto", "otro")
# Los que se cuentan en unidades (cantidad × precio); el resto, en plata (monto).
TIPOS_POR_UNIDADES = ("accion", "cedear")
# get_resumen_por_objetivo(): la parte de los activos que no está repartida.
OBJETIVO_SIN_ASIGNAR = "SIN ASIGNAR"
# Transacciones del Registro que se ofrecen para vincular (list_transacciones_vinculables()).
MAX_TRANSACCIONES_VINCULABLES = 500
# update_movement(): lo que se puede corregir de un movimiento ya cargado
# (el precio unitario no: es calculado, monto / cantidad — sección 33).
CAMPOS_EDITABLES_MOVIMIENTO = ("fecha", "cantidad", "comision_minor", "monto_minor", "moneda_id", "notas")
# Suma de porcentajes que se considera 100 / <= 100 (REAL: 33.33 + 33.33 + 33.34).
TOLERANCIA_PORCENTAJE = 1e-6
# Una parte de un objetivo por debajo de esto es cero (unidades × porcentaje dan floats).
TOLERANCIA_CANTIDAD = 1e-9
# get_reparto_proporcional(): decimales de cada porcentaje (_redondear_porcentajes()).
DECIMALES_PORCENTAJE = 4

# =============================================================
# EXCEPTIONS
# =============================================================

class SavingsError(Exception):
    """Raised when a savings operation violates a business rule."""


class ActivoNotFoundError(SavingsError):
    """Raised when a referenced financial asset does not exist."""


class ObjetivoNotFoundError(SavingsError):
    """Raised when a referenced savings goal does not exist."""


class MovimientoNotFoundError(SavingsError):
    """
    Raised when a referenced asset movement does not exist. No la usa
    ningún método de esta fase (ninguno recibe un movimiento_id como
    parámetro) — queda reservada para un futuro método que asigne a
    posteriori una compra "libre" (ver docstring del módulo y de
    register_purchase()).
    """


class AsignacionInvalidaError(SavingsError):
    """Raised when the sum of `porcentaje` for a movement's allocations would exceed 100%."""


class AccountNotFoundError(SavingsError):
    """
    Raised when a referenced account does not exist. Propia de este módulo
    (mismo motivo que EmpleosService.AccountNotFoundError). Desde la Tarea
    6g (docs/PROXIMOS_PASOS.md) se lanza en create_activo()/
    get_or_create_reserved_cash_asset() al validar cuenta_id — antes se
    lanzaba en register_purchase()/register_sale(), que ya no reciben ese
    parámetro (lo resuelven internamente desde activo.cuenta_id, ya
    validado al crear el activo).
    """


class CategoryNotFoundError(SavingsError):
    """
    Raised (defensivamente) cuando la categoría protegida 'MOVIMIENTO
    CAPITAL · Ahorro/Inversión' no se encuentra por nombre (Tarea 6g,
    docs/PROXIMOS_PASOS.md) — no debería pasar nunca contra una DB
    inicializada normalmente (la categoría viene del seed, ver
    db/seed.sql), pero se reporta como error de negocio en vez de dejar
    un TypeError crudo si algún día falta."""


class BrokerNotFoundError(SavingsError):
    """Raised when a referenced broker does not exist."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class SavingsResult:
    """Structured result returned by SavingsService operations."""
    success:   bool
    entity_id: Optional[str] = None
    data:      dict          = field(default_factory=dict)
    message:   str           = ""


# =============================================================
# SERVICE
# =============================================================

class SavingsService:
    """
    Entry point for savings operations (activos, objetivos, movimientos,
    asignaciones).

    Usage:
        db  = DatabaseManager()
        svc = SavingsService(db)

        svc.register_purchase(
            activo_id=1, fecha="2026-01-05", monto_total_minor=100000000,
            asignaciones=[{"objetivo_id": 1, "porcentaje": 60.0}, {"objetivo_id": 2, "porcentaje": 40.0}],
        )
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._activos_repo = ActivosFinancierosRepository(db)
        self._objetivos_repo = ObjetivosAhorroRepository(db)
        self._movimientos_repo = MovimientosActivoRepository(db)
        self._asignaciones_repo = AsignacionesRepository(db)
        self._transacciones_repo = TransaccionesRepository(db)
        self._cuentas_repo = CuentasRepository(db)
        self._brokers_repo = BrokersRepository(db)
        # Tabla deprecated (sección 31): solo para borrar las filas de un objetivo que se elimina.
        self._activo_objetivos_repo = ActivoObjetivosRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_activo(self, activo_id: str) -> sqlite3.Row:
        row = self._activos_repo.obtener_por_id(activo_id)
        if row is None:
            raise ActivoNotFoundError(f"Activo financiero id={activo_id} not found.")
        return row

    def _get_objetivo(self, objetivo_id: str) -> sqlite3.Row:
        row = self._objetivos_repo.obtener_por_id(objetivo_id)
        if row is None:
            raise ObjetivoNotFoundError(f"Objetivo de ahorro id={objetivo_id} not found.")
        return row

    def _get_currency(self, currency_id: int) -> sqlite3.Row:
        """No MonedasRepository exists yet in this codebase — mismo patrón
        que en los demás services de esta fase (consulta directa a `monedas`)."""
        row = self._db.fetchone("SELECT * FROM monedas WHERE id = ?;", (currency_id,))
        if row is None:
            raise SavingsError(f"Currency id={currency_id} not found.")
        return row

    def _get_account(self, account_id: str) -> sqlite3.Row:
        row = self._cuentas_repo.obtener_por_id(account_id)
        if row is None:
            raise AccountNotFoundError(f"Account id={account_id} not found.")
        return row

    # Categoría protegida usada por register_purchase()/register_sale()
    # cuando el activo tiene cuenta_id (Tarea 6g) — clave (categoria_
    # principal, subcategoria) tal como está en la fila, mismo criterio de
    # "matchear por nombre, no por id fijo" que
    # services/categorias_service.py CATEGORIAS_PROTEGIDAS (el id varía
    # entre bases, el par de nombres es la identidad estable).
    _CATEGORIA_AHORRO_PRINCIPAL = "MOVIMIENTO CAPITAL"
    _CATEGORIA_AHORRO_SUBCATEGORIA = "AHORRO/INVERSIÓN"

    def _get_categoria_ahorro_inversion_id(self) -> int:
        """
        Resuelve el id de la categoría protegida 'MOVIMIENTO CAPITAL ·
        AHORRO/INVERSIÓN' por nombre — nunca hardcodeado el id numérico,
        que varía entre bases (dummy DB de verify/, DB real del usuario,
        etc.). Viene del seed (ver db/seed.sql), así que se espera activa.
        Sin distinguir mayúsculas (utils/categorias.py clave_categoria(),
        en Python: UPPER() de SQLite no pasa la Ó): una base todavía sin
        reestructurar ("Ahorro/Inversión") se reconoce igual.
        """
        clave = (self._CATEGORIA_AHORRO_PRINCIPAL, self._CATEGORIA_AHORRO_SUBCATEGORIA)
        row = next(
            (
                c for c in self._db.fetchall(
                    "SELECT id, categoria_principal, subcategoria FROM categorias WHERE activa = 1;",
                )
                if clave_categoria(c["categoria_principal"], c["subcategoria"]) == clave
            ),
            None,
        )
        if row is None:
            raise CategoryNotFoundError(
                f"No se encontró la categoría protegida '{self._CATEGORIA_AHORRO_PRINCIPAL} · "
                f"{self._CATEGORIA_AHORRO_SUBCATEGORIA}' (activa) — se esperaba que existiera "
                "desde el seed (ver db/seed.sql)."
            )
        return row["id"]

    def _get_broker(self, broker_id: str) -> sqlite3.Row:
        row = self._brokers_repo.obtener_por_id(broker_id)
        if row is None:
            raise BrokerNotFoundError(f"EL BROKER {broker_id} NO EXISTE.")
        return row

    def _moneda_movimiento(self, activo: sqlite3.Row, moneda_id: Optional[int]) -> int:
        """
        La moneda de un movimiento (sección 33): None = la del activo. Otra
        distinta solo en acciones / CEDEARs (TIPOS_POR_UNIDADES: se compra
        en pesos y se vende en dólares); en FCI, plazos y el resto, siempre
        la del activo (decisión del usuario: un FCI en dólares es otro
        activo).

        Raises:
            SavingsError si la moneda no existe o el activo no admite otra.
        """
        if moneda_id is None or moneda_id == activo["moneda_id"]:
            return activo["moneda_id"]
        self._get_currency(moneda_id)  # validate existence
        if activo["tipo"] not in TIPOS_POR_UNIDADES:
            raise SavingsError(
                "SOLO LAS ACCIONES Y LOS CEDEARS ADMITEN MOVIMIENTOS EN OTRA MONEDA: "
                "EN FCI Y PLAZOS LA MONEDA ES SIEMPRE LA DEL ACTIVO."
            )
        return moneda_id

    @staticmethod
    def _monto_bruto(movimiento) -> int:
        """
        El MONTO que carga y ve el usuario (sección 33): en una compra / venta
        con cantidad, sin la comisión (compra: total − comisión; venta:
        total + comisión); en el resto, el total (la comisión de un aporte /
        retiro es informativa, no se suma).
        """
        total = movimiento["monto_total_minor"]
        if movimiento["tipo"] not in ("compra", "venta") or movimiento["cantidad"] is None:
            return total
        comision = movimiento["comision_minor"] or 0
        return total - comision if movimiento["tipo"] == "compra" else total + comision

    def _crear_transaccion_vinculada(
        self, activo: sqlite3.Row, fecha: str, monto_minor: int, tipo_movimiento: str,
        concepto: str, notas: Optional[str], conn: sqlite3.Connection, moneda_id: int,
    ) -> Optional[str]:
        """
        Transacción real del movimiento (Tareas 6b/6g): en la cuenta del
        activo, con la categoría protegida 'Ahorro/Inversión' y la moneda
        del movimiento (sección 33; si la cuenta todavía no operaba en esa
        moneda, TransaccionesRepository le crea el saldo en 0). None si el
        activo no tiene cuenta (movimiento informal).
        """
        if activo["cuenta_id"] is None:
            return None
        return self._transacciones_repo.crear(
            fecha=fecha,
            concepto=concepto,
            cuenta_id=activo["cuenta_id"],
            categoria_id=self._get_categoria_ahorro_inversion_id(),
            moneda_id=moneda_id,
            tipo_movimiento=tipo_movimiento,
            monto_minor=monto_minor,
            notas=notas,
            conn=conn,
        )

    def _crear_asignaciones(
        self, movimiento_id: str, repartos: list[tuple[str, float, int]], conn: sqlite3.Connection,
    ) -> list[dict]:
        """Una asignación por (objetivo_id, porcentaje, monto_asignado_minor), dentro de la transacción del caller."""
        creadas = []
        for objetivo_id, porcentaje, monto_asignado_minor in repartos:
            asignacion_id = self._asignaciones_repo.crear(
                movimiento_id=movimiento_id,
                objetivo_id=objetivo_id,
                porcentaje=porcentaje,
                monto_asignado_minor=monto_asignado_minor,
                conn=conn,
            )
            creadas.append({
                "asignacion_id": asignacion_id,
                "objetivo_id": objetivo_id,
                "porcentaje": porcentaje,
                "monto_asignado_minor": monto_asignado_minor,
            })
        return creadas

    # ----------------------------------------------------------
    # ACTIVOS FINANCIEROS
    # ----------------------------------------------------------

    def create_activo(
        self,
        nombre: str,
        tipo: str,
        moneda_id: int,
        cuenta_id: Optional[str] = None,
        broker_id: Optional[str] = None,
        comision_compra_minor: int = 0,
        comision_venta_minor: int = 0,
    ) -> SavingsResult:
        """
        cuenta_id (Tarea 6g, docs/PROXIMOS_PASOS.md): vincula el activo a
        una cuenta real, opcional. register_purchase()/register_sale() la
        usan después para resolver sola qué cuenta descontar/acreditar,
        sin que el caller tenga que pasarla en cada movimiento — un activo
        sin cuenta_id sigue generando movimientos puramente informales
        (transaccion_id NULL), igual que antes de esta tarea.

        broker_id / comisiones (rediseño de Ahorros e Inversiones): dónde
        se opera el activo (opcional) y sus comisiones de compra/venta por
        defecto (el diálogo de movimiento las precarga). Van al final de la
        firma — el pedido ponía broker_id antes de moneda_id — para no
        romper a los callers que ya pasan moneda_id por posición.

        Raises:
            SavingsError si el nombre está vacío, el tipo no es uno de
                TIPOS_ACTIVO, una comisión es negativa o moneda_id no existe.
            AccountNotFoundError si cuenta_id se pasa y no existe.
            BrokerNotFoundError si broker_id se pasa y no existe.
        """
        if not (nombre or "").strip():
            raise SavingsError("EL NOMBRE DEL ACTIVO NO PUEDE ESTAR VACÍO.")
        if tipo not in TIPOS_ACTIVO:
            raise SavingsError(f"TIPO DE ACTIVO INVÁLIDO: '{tipo}'.")
        if comision_compra_minor < 0 or comision_venta_minor < 0:
            raise SavingsError("LAS COMISIONES NO PUEDEN SER NEGATIVAS.")
        self._get_currency(moneda_id)  # validate existence
        if cuenta_id is not None:
            self._get_account(cuenta_id)  # validate existence
        if broker_id is not None:
            self._get_broker(broker_id)  # validate existence
        activo_id = self._activos_repo.crear(
            nombre=nombre.strip(), tipo=tipo, moneda_id=moneda_id, cuenta_id=cuenta_id, broker_id=broker_id,
            comision_compra_minor=comision_compra_minor, comision_venta_minor=comision_venta_minor,
        )
        return SavingsResult(
            success=True,
            entity_id=activo_id,
            data={
                "nombre": nombre.strip(), "tipo": tipo, "moneda_id": moneda_id, "cuenta_id": cuenta_id,
                "broker_id": broker_id,
            },
            message=f"ACTIVO '{nombre.strip().upper()}' CREADO.",
        )

    def get_brokers(self) -> list[dict]:
        """Los brokers activos, por nombre."""
        return [dict(fila) for fila in self._brokers_repo.listar()]

    # ----------------------------------------------------------
    # EDITAR / ELIMINAR / OCULTAR UN INSTRUMENTO (sección 35)
    # ----------------------------------------------------------

    @staticmethod
    def _hay_tenencia(tipo: str, saldo_minor: Optional[int], unidades: Optional[float]) -> bool:
        """¿Todavía tiene algo? Unidades en acciones / CEDEARs (su saldo en plata puede mezclar monedas), saldo en el resto."""
        valor = unidades if tipo in TIPOS_POR_UNIDADES else saldo_minor
        return abs(valor or 0) > TOLERANCIA_CANTIDAD

    def _con_tenencia(self, activo: sqlite3.Row) -> bool:
        stats = self._estadisticas_por_activo(activo["id"]).get(activo["id"], {})
        return self._hay_tenencia(activo["tipo"], stats.get("saldo_minor"), stats.get("unidades"))

    def update_activo(
        self,
        activo_id: str,
        nombre: object = NO_CAMBIAR,
        broker_id: object = NO_CAMBIAR,
        moneda_id: object = NO_CAMBIAR,
        cuenta_id: object = NO_CAMBIAR,
        comision_compra_minor: object = NO_CAMBIAR,
        comision_venta_minor: object = NO_CAMBIAR,
    ) -> SavingsResult:
        """
        Edita un instrumento. Default NO_CAMBIAR = no tocar ese campo; None
        en broker_id / cuenta_id lo desvincula. El tipo no se edita (no es
        un parámetro). Edición directa (CLAUDE.md §4): ningún dato guardado
        depende del nombre, el broker, la cuenta ni las comisiones por
        defecto — la cuenta nueva vale para los movimientos que se carguen
        desde ahora; las transacciones ya vinculadas quedan en la suya.

        La moneda, en cambio, solo cambia si el instrumento todavía no tiene
        movimientos (decisión del usuario): cada movimiento guarda su moneda
        (sección 33) y en FCI y plazos tiene que ser la del activo. Pedir la
        misma moneda que ya tiene no es un cambio.

        Raises:
            ActivoNotFoundError si no existe.
            BrokerNotFoundError / AccountNotFoundError si el broker o la
                cuenta pedidos no existen.
            SavingsError si el nombre queda vacío, la moneda no existe o el
                instrumento ya tiene movimientos, o una comisión no es un
                entero >= 0.
        """
        activo = self._get_activo(activo_id)
        valores: dict = {}
        if nombre is not NO_CAMBIAR:
            nombre = (nombre or "").strip()
            if not nombre:
                raise SavingsError("EL NOMBRE DEL ACTIVO NO PUEDE ESTAR VACÍO.")
            valores["nombre"] = nombre
        if broker_id is not NO_CAMBIAR:
            if broker_id is not None:
                self._get_broker(broker_id)  # validate existence
            valores["broker_id"] = broker_id
        if cuenta_id is not NO_CAMBIAR:
            if cuenta_id is not None:
                self._get_account(cuenta_id)  # validate existence
            valores["cuenta_id"] = cuenta_id
        if moneda_id is not NO_CAMBIAR and moneda_id != activo["moneda_id"]:
            self._get_currency(moneda_id)  # validate existence
            if self._movimientos_repo.listar_por_activo(activo_id):
                raise SavingsError("ESTE INSTRUMENTO YA TIENE MOVIMIENTOS: SU MONEDA NO SE PUEDE CAMBIAR.")
            valores["moneda_id"] = moneda_id
        for clave, valor in (("comision_compra_minor", comision_compra_minor), ("comision_venta_minor", comision_venta_minor)):
            if valor is not NO_CAMBIAR:
                valores[clave] = self._comision(valor)
        if not valores:
            return SavingsResult(success=True, entity_id=activo_id, message="SIN CAMBIOS.")
        self._activos_repo.actualizar(activo_id, **valores)
        return SavingsResult(
            success=True, entity_id=activo_id, data=valores,
            message=f"INSTRUMENTO '{valores.get('nombre', activo['nombre']).upper()}' ACTUALIZADO.",
        )

    def delete_activo(self, activo_id: str) -> SavingsResult:
        """
        Borra un instrumento SOLO si no tiene ningún movimiento (CLAUDE.md
        §4: sin dependencias, borrado directo). Con historial — aunque el
        saldo sea 0 — se rechaza: se oculta con ocultar_activo(). Borra
        también sus filas de activo_objetivos (deprecated, sección 31: la FK
        lo exige), todo en una transacción.

        Raises:
            ActivoNotFoundError si no existe.
            SavingsError si tiene movimientos.
        """
        activo = self._get_activo(activo_id)
        if self._movimientos_repo.listar_por_activo(activo_id):
            raise SavingsError(
                "ESTE INSTRUMENTO TIENE MOVIMIENTOS: NO SE PUEDE ELIMINAR. "
                "SI YA NO TIENE SALDO, OCULTALO."
            )
        conn = self._db.conn
        with self._db.transaction():
            self._activo_objetivos_repo.eliminar_por_activo(activo_id, conn=conn)
            self._activos_repo.eliminar(activo_id, conn=conn)
        return SavingsResult(
            success=True, entity_id=activo_id, message=f"INSTRUMENTO '{activo['nombre'].upper()}' ELIMINADO.",
        )

    def ocultar_activo(self, activo_id: str) -> SavingsResult:
        """
        Oculta un instrumento (activa = 0): no borra nada — su historial
        queda — y se puede reactivar. Solo si ya no tiene tenencia (0
        unidades en acciones / CEDEARs, saldo 0 en el resto): uno con plata
        adentro no se esconde.

        Raises:
            ActivoNotFoundError si no existe.
            SavingsError si todavía tiene saldo / unidades.
        """
        activo = self._get_activo(activo_id)
        if self._con_tenencia(activo):
            raise SavingsError("ESTE INSTRUMENTO TODAVÍA TIENE SALDO (O UNIDADES): NO SE PUEDE OCULTAR.")
        self._activos_repo.desactivar(activo_id)
        return SavingsResult(
            success=True, entity_id=activo_id, message=f"INSTRUMENTO '{activo['nombre'].upper()}' OCULTO.",
        )

    def reactivar_activo(self, activo_id: str) -> SavingsResult:
        """
        Vuelve a mostrar un instrumento oculto (activa = 1).

        Raises:
            ActivoNotFoundError si no existe.
        """
        activo = self._get_activo(activo_id)
        self._activos_repo.activar(activo_id)
        return SavingsResult(
            success=True, entity_id=activo_id, message=f"INSTRUMENTO '{activo['nombre'].upper()}' REACTIVADO.",
        )

    def list_activos(self, tipo: Optional[str] = None) -> list[sqlite3.Row]:
        return self._activos_repo.listar(tipo=tipo)

    def get_or_create_reserved_cash_asset(self, cuenta_id: str, moneda_id: int) -> SavingsResult:
        """
        Busca el activo_financiero tipo='otro' vinculado a cuenta_id. Si
        ya existe, lo reusa tal cual (result.data["creado"] = False) — sin
        importar su estado activa (busca contra listar(solo_activos=False),
        no reactiva nada si estuviera desactivado, eso queda fuera de
        alcance). Si no existe, lo crea con moneda_id y cuenta_id
        (result.data["creado"] = True).

        Identidad = (cuenta_id, tipo='otro') desde la Tarea 6g
        (docs/PROXIMOS_PASOS.md) — antes (Tarea 1b) la identidad era el
        nombre exacto "Efectivo reservado en <cuenta_nombre>" construido a
        mano, frágil ante un rename de la cuenta después de creado el
        activo (activos_financieros no tenía columna cuenta_id todavía,
        ver docs/DATA_MODEL_DECISIONS.md sección 17 para el estado
        anterior). El nombre generado se sigue guardando igual, sigue
        siendo útil para mostrarlo — pero ya no es la clave de búsqueda.

        Args:
            cuenta_id: cuenta real para la que se reserva efectivo — el
                       nombre del activo (si hay que crearlo) es
                       f"Efectivo reservado en {cuenta.nombre}".
            moneda_id: Moneda del activo SI hay que crearlo (se ignora si
                       ya existía uno para esta cuenta — moneda_id no se
                       reescribe en el camino de reuso, mismo criterio que
                       PresupuestosRepository.upsert() no reescribe
                       moneda_id en su UPDATE).

        Raises:
            AccountNotFoundError si cuenta_id no existe.
            SavingsError si moneda_id no existe Y hace falta crear el activo.
        """
        cuenta = self._get_account(cuenta_id)  # validate existence
        existente = next(
            (a for a in self._activos_repo.listar(tipo="otro", solo_activos=False) if a["cuenta_id"] == cuenta_id),
            None,
        )
        if existente is not None:
            return SavingsResult(
                success=True,
                entity_id=existente["id"],
                data={"nombre": existente["nombre"], "creado": False},
                message=f"Activo '{existente['nombre']}' ya existía (id={existente['id']}) — reusado.",
            )

        self._get_currency(moneda_id)  # validate existence
        nombre = f"Efectivo reservado en {cuenta['nombre']}"
        activo_id = self._activos_repo.crear(nombre=nombre, tipo="otro", moneda_id=moneda_id, cuenta_id=cuenta_id)
        return SavingsResult(
            success=True,
            entity_id=activo_id,
            data={"nombre": nombre, "creado": True},
            message=f"Activo '{nombre}' creado (id={activo_id}).",
        )

    # ----------------------------------------------------------
    # OBJETIVOS DE AHORRO
    # ----------------------------------------------------------

    @classmethod
    def _datos_objetivo(
        cls, nombre: str, monto_meta_minor: Optional[int], fecha_meta: Optional[str],
    ) -> tuple[str, Optional[int], Optional[str]]:
        """
        (nombre sin espacios de más, meta, fecha meta) validados — los
        comparten create_objetivo() y update_objetivo(). Meta y fecha meta
        son opcionales (None).

        Raises:
            SavingsError si el nombre está vacío, la meta no es un entero > 0
                o la fecha meta no es AAAA-MM-DD.
        """
        nombre = (nombre or "").strip()
        if not nombre:
            raise SavingsError("EL NOMBRE DEL OBJETIVO NO PUEDE ESTAR VACÍO.")
        if monto_meta_minor is not None and (
            not isinstance(monto_meta_minor, int) or isinstance(monto_meta_minor, bool) or monto_meta_minor <= 0
        ):
            raise SavingsError(f"LA META TIENE QUE SER MAYOR A 0 (RECIBIDO: {monto_meta_minor!r}).")
        if fecha_meta is not None:
            cls._fecha(fecha_meta)
        return nombre, monto_meta_minor, fecha_meta

    def create_objetivo(
        self,
        nombre: str,
        monto_meta_minor: Optional[int] = None,
        fecha_meta: Optional[str] = None,
    ) -> SavingsResult:
        """
        Raises:
            SavingsError: ver _datos_objetivo().
        """
        nombre, monto_meta_minor, fecha_meta = self._datos_objetivo(nombre, monto_meta_minor, fecha_meta)
        objetivo_id = self._objetivos_repo.crear(
            nombre=nombre, monto_meta_minor=monto_meta_minor, fecha_meta=fecha_meta,
        )
        return SavingsResult(
            success=True,
            entity_id=objetivo_id,
            data={"nombre": nombre, "monto_meta_minor": monto_meta_minor, "fecha_meta": fecha_meta},
            message=f"Objetivo de ahorro '{nombre}' created.",
        )

    def list_objetivos(self, estado: Optional[str] = None) -> list[sqlite3.Row]:
        return self._objetivos_repo.listar(estado=estado)

    def update_objetivo(
        self, objetivo_id: str, nombre: str, monto_meta_minor: Optional[int], fecha_meta: Optional[str],
    ) -> SavingsResult:
        """
        Edita un objetivo (sección 32): nombre, meta y fecha meta, los tres
        juntos (None = sin meta / sin fecha meta). Edición directa: sus
        asignaciones lo referencian por id, ningún dato depende del nombre
        ni de la meta (CLAUDE.md §4). El estado no se toca.

        Raises:
            ObjetivoNotFoundError si no existe.
            SavingsError: ver _datos_objetivo().
        """
        self._get_objetivo(objetivo_id)
        nombre, monto_meta_minor, fecha_meta = self._datos_objetivo(nombre, monto_meta_minor, fecha_meta)
        self._objetivos_repo.actualizar(
            objetivo_id, nombre=nombre, monto_meta_minor=monto_meta_minor, fecha_meta=fecha_meta,
        )
        return SavingsResult(
            success=True, entity_id=objetivo_id,
            data={"nombre": nombre, "monto_meta_minor": monto_meta_minor, "fecha_meta": fecha_meta},
            message=f"OBJETIVO '{nombre.upper()}' ACTUALIZADO.",
        )

    def get_partes_de_objetivo(self, objetivo_id: str) -> list[dict]:
        """
        Los instrumentos donde el objetivo tiene asignaciones — lo que hay
        que repartir al eliminarlo (delete_objetivo()) —, también los que le
        quedaron en cero (solo historial): [{activo_id, activo, tipo, broker,
        moneda, simbolo, decimales, por_unidades, saldo_minor, unidades,
        en_cero}], en el orden de get_resumen_por_tipo(). saldo_minor /
        unidades: la parte del objetivo (ver _partes_por_objetivo()).

        Raises:
            ObjetivoNotFoundError si no existe.
        """
        self._get_objetivo(objetivo_id)
        partes: dict[str, dict] = {}
        for activo_id, lista in self._partes_por_objetivo().items():
            for parte in lista:
                if parte["objetivo_id"] == objetivo_id:
                    partes[activo_id] = parte
        entradas = [e for grupo in self.get_resumen_por_tipo().values() for e in grupo]
        # Un activo dado de baja no está en el resumen: igual hay que repartir lo suyo.
        vistos = {e["activo_id"] for e in entradas}
        monedas = {m["id"]: m for m in self._db.obtener_monedas()}
        for activo_id in partes.keys() - vistos:
            activo = self._get_activo(activo_id)
            moneda = monedas.get(activo["moneda_id"])
            entradas.append({
                "activo_id": activo_id, "activo": activo["nombre"], "tipo": activo["tipo"], "broker": None,
                "moneda": moneda["codigo"] if moneda else "", "simbolo": (moneda["simbolo"] or "") if moneda else "",
                "decimales": moneda["decimales"] if moneda else 2, "por_unidades": activo["tipo"] in TIPOS_POR_UNIDADES,
            })

        resultado = []
        for entrada in entradas:
            parte = partes.get(entrada["activo_id"])
            if parte is None:
                continue
            clave = "unidades" if entrada["por_unidades"] else "saldo_minor"
            resultado.append({
                **{k: entrada[k] for k in (
                    "activo_id", "activo", "tipo", "broker", "moneda", "simbolo", "decimales", "por_unidades",
                )},
                "saldo_minor": parte["saldo_minor"] or 0,
                "unidades": parte["unidades"],
                "en_cero": abs(parte[clave] or 0) <= TOLERANCIA_CANTIDAD,
            })
        return resultado

    def delete_objetivo(self, objetivo_id: str, repartos: Optional[dict[str, list[dict]]] = None) -> SavingsResult:
        """
        Elimina un objetivo (sección 32) pasando antes su parte de cada
        instrumento a otros objetivos. `repartos`: activo_id →
        [{"objetivo_id", "porcentaje"}] (suma <= 100); un instrumento sin
        entrada, o con [], queda sin asignar en lo que era de este objetivo.

        Cada asignación del objetivo en un instrumento se reparte con esos
        porcentajes (se suma a la que el destino ya tuviera en ese mismo
        movimiento): así la parte que tenía en el instrumento — saldo o
        unidades — pasa entera y en esa proporción, y el historial de cada
        movimiento queda coherente con el resumen. Los montos, con el mismo
        redondeo que el alta (_montos_por_porcentaje()). Todo en una
        transacción: también borra las filas del objetivo en
        activo_objetivos (deprecated, la FK lo exige) y el objetivo (DELETE
        físico: la tabla no tiene soft-delete; nada más lo referencia).

        Raises:
            ObjetivoNotFoundError si el objetivo o un destino no existen.
            AsignacionInvalidaError si un reparto no es válido
                (_porcentajes()) o incluye al mismo objetivo.
        """
        objetivo = self._get_objetivo(objetivo_id)
        porcentajes_por_activo: dict[str, list[tuple[str, float]]] = {}
        for activo_id, asignaciones in (repartos or {}).items():
            porcentajes = self._porcentajes(asignaciones)
            if any(destino == objetivo_id for destino, _ in porcentajes):
                raise AsignacionInvalidaError("NO SE PUEDE REPARTIR UN OBJETIVO HACIA SÍ MISMO.")
            porcentajes_por_activo[activo_id] = porcentajes

        # Las asignaciones nuevas de cada movimiento afectado, armadas antes de escribir nada.
        nuevas: dict[str, list[tuple[str, float, int]]] = {}
        for asignacion in self._asignaciones_repo.listar_por_objetivo(objetivo_id):
            movimiento_id = asignacion["movimiento_id"]
            activo_id = self._movimientos_repo.obtener_por_id(movimiento_id)["activo_id"]
            resto = {
                fila["objetivo_id"]: [fila["porcentaje"], fila["monto_asignado_minor"]]
                for fila in self._asignaciones_repo.listar_por_movimiento(movimiento_id)
                if fila["objetivo_id"] != objetivo_id
            }
            for destino, porcentaje, monto in self._montos_por_porcentaje(
                porcentajes_por_activo.get(activo_id, []), asignacion["monto_asignado_minor"],
            ):
                parte = resto.setdefault(destino, [0.0, 0])
                # El total del movimiento no cambia: nunca pasa de 100 (min() contra el error de los floats).
                parte[0] = min(parte[0] + asignacion["porcentaje"] * porcentaje / 100, 100.0)
                parte[1] += monto
            nuevas[movimiento_id] = [(destino, p, m) for destino, (p, m) in resto.items()]

        conn = self._db.conn
        with self._db.transaction():
            for movimiento_id, filas in nuevas.items():
                self._asignaciones_repo.eliminar_por_movimiento(movimiento_id, conn=conn)
                self._crear_asignaciones(movimiento_id, filas, conn)
            self._activo_objetivos_repo.eliminar_por_objetivo(objetivo_id, conn=conn)
            self._objetivos_repo.eliminar(objetivo_id, conn=conn)
        return SavingsResult(
            success=True, entity_id=objetivo_id, data={"movimientos_reasignados": len(nuevas)},
            message=f"OBJETIVO '{objetivo['nombre'].upper()}' ELIMINADO.",
        )

    # ----------------------------------------------------------
    # REGISTER PURCHASE
    # ----------------------------------------------------------

    def register_purchase(
        self,
        activo_id: str,
        fecha: str,
        monto_total_minor: int,
        dolar_oficial_momento_minor: Optional[int] = None,
        cantidad: Optional[float] = None,
        precio_unitario_minor: Optional[int] = None,
        asignaciones: Optional[list[dict]] = None,
        notas: Optional[str] = None,
    ) -> SavingsResult:
        """
        Registra una compra (movimiento tipo='compra') de activo_id, con
        asignación opcional a uno o más objetivos de ahorro.

        Si `asignaciones` es None o vacía, el movimiento queda sin asignar
        (compra "libre") — puede asignarse después con un método aparte que
        esta fase no implementa (ver docstring del módulo).

        Si se pasa `asignaciones` (lista de {"objetivo_id": int,
        "porcentaje": float}), se valida CADA objetivo_id contra
        ObjetivoNotFoundError y que la suma de porcentaje no supere 100
        (AsignacionInvalidaError si supera) ANTES de escribir nada. El
        movimiento y sus asignaciones se crean atómicos en una única
        self._db.transaction() — monto_asignado_minor de cada asignación
        se calcula como round(monto_total_minor * porcentaje / 100).

        cuenta_id/categoria_id (Tarea 6g, docs/PROXIMOS_PASOS.md): ya NO
        son parámetros de este método — se resuelven solos. cuenta_id sale
        de activo["cuenta_id"] (ver SavingsService.create_activo()): si el
        activo tiene cuenta vinculada, además del movimiento (+
        asignaciones) se crea dentro de la MISMA transacción atómica una
        transacción real tipo='egreso' (vía TransaccionesRepository) que
        descuenta esa cuenta por monto_total_minor, en la moneda del
        activo (activos_financieros.moneda_id — el monto de un movimiento
        de ahorro está denominado en la moneda del activo, no hay otra
        moneda disponible en este método), y vincula su id en
        movimientos_activo.transaccion_id. categoria_id siempre es el id
        de la categoría protegida 'MOVIMIENTO CAPITAL · Ahorro/Inversión'
        (resuelta por nombre, nunca hardcodeada — ver
        _get_categoria_ahorro_inversion_id()), nunca elegida por el
        caller. Si el activo NO tiene cuenta_id, comportamiento previo sin
        cambios: movimiento informal, transaccion_id queda NULL.

        Raises:
            ActivoNotFoundError si activo_id no existe.
            ValueError si monto_total_minor <= 0.
            ObjetivoNotFoundError si algún objetivo_id de `asignaciones` no existe.
            AsignacionInvalidaError si la suma de porcentaje supera 100.
        """
        activo = self._get_activo(activo_id)
        if monto_total_minor <= 0:
            raise ValueError(f"monto_total_minor must be positive. Received: {monto_total_minor}.")

        asignaciones = asignaciones or []
        suma_porcentaje = 0.0
        for asignacion in asignaciones:
            self._get_objetivo(asignacion["objetivo_id"])  # validate existence
            suma_porcentaje += asignacion["porcentaje"]
        if suma_porcentaje > 100:
            raise AsignacionInvalidaError(
                f"La suma de porcentaje de las asignaciones ({suma_porcentaje}) supera 100."
            )

        conn = self._db.conn
        with self._db.transaction():
            transaccion_id = self._crear_transaccion_vinculada(
                activo, fecha, monto_total_minor, "egreso", f"Aporte a ahorro — {activo['nombre']}", notas, conn,
                moneda_id=activo["moneda_id"],
            )
            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="compra", fecha=fecha,
                monto_total_minor=monto_total_minor, cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                dolar_oficial_momento_minor=dolar_oficial_momento_minor,
                notas=notas, transaccion_id=transaccion_id, conn=conn, moneda_id=activo["moneda_id"],
            )
            asignaciones_creadas = self._crear_asignaciones(
                movimiento_id,
                [
                    (a["objetivo_id"], a["porcentaje"], round(monto_total_minor * a["porcentaje"] / 100))
                    for a in asignaciones
                ],
                conn,
            )

        return SavingsResult(
            success=True,
            entity_id=movimiento_id,
            data={
                "asignaciones": asignaciones_creadas,
                "asignado": bool(asignaciones_creadas),
                "transaccion_id": transaccion_id,
            },
            message=(
                f"Compra de {monto_total_minor} registrada para activo_id={activo_id}, "
                f"{len(asignaciones_creadas)} asignación(es)."
                if asignaciones_creadas else
                f"Compra de {monto_total_minor} registrada para activo_id={activo_id}, sin asignar."
            ),
        )

    # ----------------------------------------------------------
    # REGISTER RETURN
    # ----------------------------------------------------------

    def register_return(
        self,
        activo_id: str,
        fecha: str,
        monto_total_minor: int,
        notas: Optional[str] = None,
    ) -> SavingsResult:
        """
        Registra un rendimiento (movimiento tipo='rendimiento') de
        activo_id, repartido AUTOMÁTICAMENTE entre los objetivos según la
        proporción AGREGADA de sus asignaciones en TODAS las compras
        previas de ese activo (no según una compra puntual) — el caller no
        elige el reparto a mano.

        Si ninguna compra de este activo tiene asignaciones todavía (total
        agregado = 0), el movimiento se crea igual pero SIN ninguna
        asignación — queda "sin repartir" (data["repartido"] = False en el
        resultado).

        Manejo de redondeo (método del resto mayor): cada monto se calcula
        con división entera (monto_total_minor * peso_objetivo //
        total_agregado); el resto que sobra por redondeo se le suma al
        objetivo con mayor monto calculado, para que la suma de las
        asignaciones del rendimiento dé EXACTO monto_total_minor. Objetivos
        cuyo monto calculado quede en 0 no generan fila de asignación (el
        schema exige porcentaje > 0) — no afecta la suma total, un monto en
        0 no aporta nada a repartir.

        Raises:
            ActivoNotFoundError si activo_id no existe.
            ValueError si monto_total_minor <= 0.
        """
        activo = self._get_activo(activo_id)
        if monto_total_minor <= 0:
            raise ValueError(f"monto_total_minor must be positive. Received: {monto_total_minor}.")

        # 'aporte' (rediseño de Ahorros e Inversiones) cuenta igual que una compra.
        compras = [
            *self._movimientos_repo.listar_por_tipo(activo_id, "compra"),
            *self._movimientos_repo.listar_por_tipo(activo_id, "aporte"),
        ]
        totales_por_objetivo: dict[int, int] = {}
        for compra in compras:
            for asignacion in self._asignaciones_repo.listar_por_movimiento(compra["id"]):
                objetivo_id = asignacion["objetivo_id"]
                totales_por_objetivo[objetivo_id] = (
                    totales_por_objetivo.get(objetivo_id, 0) + asignacion["monto_asignado_minor"]
                )

        total_agregado = sum(totales_por_objetivo.values())
        conn = self._db.conn

        if total_agregado == 0:
            with self._db.transaction():
                movimiento_id = self._movimientos_repo.crear(
                    activo_id=activo_id, tipo="rendimiento", fecha=fecha,
                    monto_total_minor=monto_total_minor, notas=notas, conn=conn, moneda_id=activo["moneda_id"],
                )
            return SavingsResult(
                success=True,
                entity_id=movimiento_id,
                data={"asignaciones": [], "repartido": False},
                message=(
                    f"Rendimiento de {monto_total_minor} registrado para activo_id={activo_id}, "
                    "sin repartir: ninguna compra de este activo tiene asignaciones todavía."
                ),
            )

        montos = {
            objetivo_id: (monto_total_minor * monto) // total_agregado
            for objetivo_id, monto in totales_por_objetivo.items()
        }
        resto = monto_total_minor - sum(montos.values())
        if resto > 0:
            objetivo_mayor = max(montos, key=lambda oid: montos[oid])
            montos[objetivo_mayor] += resto

        with self._db.transaction():
            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="rendimiento", fecha=fecha,
                monto_total_minor=monto_total_minor, notas=notas, conn=conn, moneda_id=activo["moneda_id"],
            )
            asignaciones_creadas = []
            for objetivo_id, monto_asignado_minor in montos.items():
                if monto_asignado_minor == 0:
                    continue
                porcentaje = monto_asignado_minor * 100 / monto_total_minor
                asignacion_id = self._asignaciones_repo.crear(
                    movimiento_id=movimiento_id, objetivo_id=objetivo_id,
                    porcentaje=porcentaje, monto_asignado_minor=monto_asignado_minor,
                    conn=conn,
                )
                asignaciones_creadas.append({
                    "asignacion_id": asignacion_id,
                    "objetivo_id": objetivo_id,
                    "porcentaje": porcentaje,
                    "monto_asignado_minor": monto_asignado_minor,
                })

        return SavingsResult(
            success=True,
            entity_id=movimiento_id,
            data={"asignaciones": asignaciones_creadas, "repartido": True},
            message=(
                f"Rendimiento de {monto_total_minor} repartido entre "
                f"{len(asignaciones_creadas)} objetivo(s) para activo_id={activo_id}."
            ),
        )

    # ----------------------------------------------------------
    # REGISTER SALE
    # ----------------------------------------------------------

    def register_sale(
        self,
        activo_id: str,
        fecha: str,
        monto_total_minor: int,
        asignaciones: list[dict],
        cantidad: Optional[float] = None,
        precio_unitario_minor: Optional[int] = None,
        dolar_oficial_momento_minor: Optional[int] = None,
        notas: Optional[str] = None,
    ) -> SavingsResult:
        """
        Registra una venta/retiro (movimiento tipo='venta') de activo_id,
        asignada a uno o más objetivos vía `asignaciones` (Tarea 6d,
        docs/PROXIMOS_PASOS.md — mismo formato que register_purchase():
        [{"objetivo_id": int, "porcentaje": float}, ...]). Reemplaza la
        firma anterior de un único `objetivo_id` obligatorio al 100% —
        ese caso sigue siendo válido, solo que ahora se expresa como
        `asignaciones=[{"objetivo_id": ..., "porcentaje": 100.0}]` en vez
        de un parámetro aparte.

        A diferencia de register_purchase(), acá `asignaciones` NO puede
        quedar vacío ni ausente (ValueError si lo está): un retiro
        siempre tiene que salir de al menos un objetivo — no existe el
        equivalente de "compra libre" para una venta. Se valida CADA
        objetivo_id (ObjetivoNotFoundError) y que la suma de porcentaje
        no supere 100 (AsignacionInvalidaError) ANTES de escribir nada,
        mismo criterio que register_purchase() — acá SÍ se permite que la
        suma sea menor a 100 (no se exige que dé exacto), mismo criterio
        que register_purchase() otra vez, aunque el caso de uso típico de
        la UI (ver ui/screens/ahorros.py) sea repartir el 100% del
        retiro.

        cuenta_id/categoria_id (Tarea 6g, docs/PROXIMOS_PASOS.md): ya NO
        son parámetros de este método — mismo mecanismo de resolución
        automática que register_purchase() (ver su docstring): cuenta_id
        sale de activo["cuenta_id"]; si hay cuenta, se crea una
        transacción real tipo='ingreso' (el retiro de ahorro es plata que
        vuelve a estar disponible en esa cuenta) dentro de la MISMA
        transacción atómica que el movimiento + asignaciones, vinculada en
        movimientos_activo.transaccion_id, con categoria_id siempre el id
        de la categoría protegida 'MOVIMIENTO CAPITAL · Ahorro/Inversión'.
        Si el activo no tiene cuenta_id, comportamiento previo sin
        cambios.

        Raises:
            ActivoNotFoundError si activo_id no existe.
            ValueError si monto_total_minor <= 0 o si `asignaciones` está vacío.
            ObjetivoNotFoundError si algún objetivo_id de `asignaciones` no existe.
            AsignacionInvalidaError si la suma de porcentaje supera 100.
        """
        activo = self._get_activo(activo_id)
        if monto_total_minor <= 0:
            raise ValueError(f"monto_total_minor must be positive. Received: {monto_total_minor}.")
        if not asignaciones:
            raise ValueError(
                "asignaciones cannot be empty for a sale — a withdrawal must come from at least one objetivo."
            )

        suma_porcentaje = 0.0
        for asignacion in asignaciones:
            self._get_objetivo(asignacion["objetivo_id"])  # validate existence
            suma_porcentaje += asignacion["porcentaje"]
        if suma_porcentaje > 100:
            raise AsignacionInvalidaError(
                f"La suma de porcentaje de las asignaciones ({suma_porcentaje}) supera 100."
            )

        conn = self._db.conn
        with self._db.transaction():
            transaccion_id = self._crear_transaccion_vinculada(
                activo, fecha, monto_total_minor, "ingreso", f"Retiro de ahorro — {activo['nombre']}", notas, conn,
                moneda_id=activo["moneda_id"],
            )
            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="venta", fecha=fecha,
                monto_total_minor=monto_total_minor, cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                dolar_oficial_momento_minor=dolar_oficial_momento_minor,
                notas=notas, transaccion_id=transaccion_id, conn=conn, moneda_id=activo["moneda_id"],
            )
            asignaciones_creadas = self._crear_asignaciones(
                movimiento_id,
                [
                    (a["objetivo_id"], a["porcentaje"], round(monto_total_minor * a["porcentaje"] / 100))
                    for a in asignaciones
                ],
                conn,
            )

        return SavingsResult(
            success=True,
            entity_id=movimiento_id,
            data={
                "asignaciones": asignaciones_creadas,
                "transaccion_id": transaccion_id,
            },
            message=(
                f"Venta de {monto_total_minor} registrada para activo_id={activo_id}, "
                f"{len(asignaciones_creadas)} asignación(es)."
            ),
        )

    # ----------------------------------------------------------
    # OBJETIVO BALANCE (read-only aggregation)
    # ----------------------------------------------------------

    def get_objetivo_balance(self, objetivo_id: str) -> dict:
        """
        Agregación de solo lectura del saldo de un objetivo de ahorro,
        separado por tipo de movimiento: compra y rendimiento aportan
        positivo, venta resta.

        Candidato legítimo a vivir en el service, no en un repositorio —
        mismo criterio que monthly_summary()/summary_by_person() de otros
        bloques (agregación de reporte, no acceso a datos crudo).

        Raises:
            ObjetivoNotFoundError si objetivo_id no existe.
        """
        self._get_objetivo(objetivo_id)  # validate existence
        asignaciones = self._asignaciones_repo.listar_por_objetivo(objetivo_id)

        invertido_minor = 0
        rendimiento_minor = 0
        retirado_minor = 0

        for asignacion in asignaciones:
            movimiento = self._movimientos_repo.obtener_por_id(asignacion["movimiento_id"])
            if movimiento is None:
                # No debería pasar con PRAGMA foreign_keys=ON activado en
                # DatabaseManager.conn — asignaciones.movimiento_id
                # referencia movimientos_activo(id). Defensivo: si algún día
                # se corre contra una DB con FKs deshabilitadas, se reporta
                # como error de negocio en vez de un TypeError crudo al
                # indexar None.
                raise MovimientoNotFoundError(
                    f"Movimiento id={asignacion['movimiento_id']} referenced by "
                    f"asignacion id={asignacion['id']} not found."
                )
            if movimiento["tipo"] in ("compra", "aporte"):
                invertido_minor += asignacion["monto_asignado_minor"]
            elif movimiento["tipo"] == "rendimiento":
                rendimiento_minor += asignacion["monto_asignado_minor"]
            elif movimiento["tipo"] == "venta":
                retirado_minor += asignacion["monto_asignado_minor"]

        return {
            "objetivo_id": objetivo_id,
            "invertido_minor": invertido_minor,
            "rendimiento_minor": rendimiento_minor,
            "retirado_minor": retirado_minor,
            "saldo_neto_minor": invertido_minor + rendimiento_minor - retirado_minor,
        }

    # ----------------------------------------------------------
    # BALANCE POR CUENTA (read-only aggregation, Tarea 6b)
    # ----------------------------------------------------------

    def get_balance_por_cuenta(self, objetivo_id: str) -> list[dict]:
        """
        Agregación de solo lectura: de todo lo aportado/retirado a
        objetivo_id, cuánto pasó realmente por cada cuenta real — sólo
        cuenta movimientos con movimientos_activo.transaccion_id vinculado
        (Tarea 6b); un movimiento informal (transaccion_id NULL) no aporta
        nada acá, aunque sí cuenta en get_objetivo_balance().

        Un JOIN cruzando asignaciones → movimientos_activo →
        transacciones → cuentas es agregación de reporte, no acceso a
        datos crudo de una sola tabla — mismo criterio ya usado para vivir
        en el service en vez de en un repositorio (ver
        DashboardService.get_gasto_por_categoria()).

        rendimiento nunca aparece acá: register_return() no acepta
        cuenta_id (fuera de alcance de esta tarea, ver docstring del
        módulo) — un movimiento tipo='rendimiento' nunca tiene
        transaccion_id, así que el INNER JOIN lo excluye solo.

        Agrupa por (cuenta_id, moneda_id) — nunca mezcla monedas distintas
        en una misma suma, mismo criterio que el resto del dashboard (ver
        DashboardService.get_gasto_por_categoria()): si la misma cuenta
        aportó a este objetivo en más de una moneda, aparece como una
        entrada por cada moneda.

        Returns:
            Lista de dicts {cuenta_id, cuenta_nombre, moneda_id,
            aportado_minor, retirado_minor, saldo_minor}, uno por cuenta
            (y moneda) involucrada. Lista vacía si el objetivo no tiene
            ningún movimiento vinculado a una cuenta real todavía.

        Raises:
            ObjetivoNotFoundError si objetivo_id no existe.
        """
        self._get_objetivo(objetivo_id)  # validate existence

        filas = self._db.fetchall(
            """
            SELECT
                t.cuenta_id                                                    AS cuenta_id,
                c.nombre                                                       AS cuenta_nombre,
                t.moneda_id                                                    AS moneda_id,
                SUM(CASE WHEN ma.tipo IN ('compra', 'aporte')
                         THEN a.monto_asignado_minor ELSE 0 END)               AS aportado_minor,
                SUM(CASE WHEN ma.tipo = 'venta'
                         THEN a.monto_asignado_minor ELSE 0 END)               AS retirado_minor
            FROM asignaciones a
            JOIN movimientos_activo ma ON ma.id = a.movimiento_id
            JOIN transacciones t       ON t.id = ma.transaccion_id
            JOIN cuentas c              ON c.id = t.cuenta_id
            WHERE a.objetivo_id = ?
            GROUP BY t.cuenta_id, t.moneda_id
            ORDER BY t.cuenta_id, t.moneda_id;
            """,
            (objetivo_id,),
        )

        return [
            {
                "cuenta_id": fila["cuenta_id"],
                "cuenta_nombre": fila["cuenta_nombre"],
                "moneda_id": fila["moneda_id"],
                "aportado_minor": fila["aportado_minor"],
                "retirado_minor": fila["retirado_minor"],
                "saldo_minor": fila["aportado_minor"] - fila["retirado_minor"],
            }
            for fila in filas
        ]

    # ----------------------------------------------------------
    # BALANCE POR TIPO DE ACTIVO (read-only aggregation, Tarea 6d)
    # ----------------------------------------------------------

    def get_balance_por_tipo(self) -> list[dict]:
        """
        Agregación de solo lectura: saldo neto de TODOS los movimientos
        (compra/rendimiento suman, venta resta) agrupado por
        activos_financieros.tipo Y la moneda de cada MOVIMIENTO (sección 33:
        una venta de CEDEAR en USD va al grupo USD aunque el activo sea en
        ARS) — nunca mezcla monedas distintas en una misma suma, mismo criterio que
        get_balance_por_cuenta() (agrupa por cuenta_id, moneda_id). A
        diferencia de get_objetivo_balance(), esto NO pasa por
        asignaciones/objetivos_ahorro en absoluto: es el total real de
        movimientos_activo agrupado por tipo de activo, sin importar a
        qué objetivo(s) esté repartido (o no) cada movimiento.

        Candidato legítimo a vivir en el service, no en un repositorio —
        mismo criterio que get_balance_por_cuenta()/
        DashboardService.get_gasto_por_categoria() (agregación de
        reporte, no acceso a datos crudo de una sola tabla).

        Returns:
            Lista de dicts {tipo, moneda_id, saldo_neto_minor}, una fila
            por combinación (tipo, moneda) con al menos un movimiento —
            sin entradas para tipos sin actividad todavía.
        """
        filas = self._db.fetchall(
            """
            SELECT
                af.tipo                                                       AS tipo,
                COALESCE(ma.moneda_id, af.moneda_id)                          AS moneda_id,
                SUM(CASE
                        WHEN ma.tipo IN ('compra', 'aporte', 'rendimiento') THEN ma.monto_total_minor
                        WHEN ma.tipo = 'venta' THEN -ma.monto_total_minor
                        ELSE 0
                    END)                                                      AS saldo_neto_minor
            FROM movimientos_activo ma
            JOIN activos_financieros af ON af.id = ma.activo_id
            GROUP BY af.tipo, COALESCE(ma.moneda_id, af.moneda_id)
            ORDER BY af.tipo, COALESCE(ma.moneda_id, af.moneda_id);
            """
        )
        return [
            {
                "tipo": fila["tipo"],
                "moneda_id": fila["moneda_id"],
                "saldo_neto_minor": fila["saldo_neto_minor"],
            }
            for fila in filas
        ]

    # ----------------------------------------------------------
    # BALANCE POR ACTIVO (read-only aggregation, Tarea 6d)
    # ----------------------------------------------------------

    def get_balance_por_activo(self) -> list[dict]:
        """
        Agregación de solo lectura: total REAL agregado por activo_id —
        TODAS las compras/ventas de ese activo, sin segregar por
        objetivo (a diferencia de get_objetivo_balance()) — es el número
        que coincide con lo que muestra la app del broker/banco (ej.
        "3 unidades" de una acción en un solo total, no repartido entre
        objetivos de ahorro).

        cantidad_neta es None si NINGÚN movimiento de compra/venta de ese
        activo cargó nunca una `cantidad` (activos sin concepto de
        unidad, ej. tipo='plazo_fijo'/'otro' — SUM() de SQLite sobre un
        conjunto vacío o completamente NULL devuelve NULL, que se
        traduce directo a None acá) — nunca un 0 engañoso que sugiera
        "0 unidades" cuando en realidad ese activo no trackea cantidad.
        rendimiento nunca aporta a cantidad_neta: register_return() no
        acepta `cantidad` en su firma real (confirmado antes de escribir
        esta query), así que un movimiento tipo='rendimiento' siempre
        tiene cantidad NULL en la base — el CASE de abajo ni lo
        contempla, cae solo en el ELSE NULL que SUM() ya ignora.

        Returns:
            Lista de dicts {activo_id, cantidad_neta, saldo_neto_minor},
            una fila por activo con al menos un movimiento — sin
            entradas para activos sin actividad todavía.
        """
        filas = self._db.fetchall(
            """
            SELECT
                ma.activo_id                                                  AS activo_id,
                SUM(CASE
                        WHEN ma.tipo IN ('compra', 'aporte') THEN ma.cantidad
                        WHEN ma.tipo = 'venta' THEN -ma.cantidad
                        ELSE NULL
                    END)                                                      AS cantidad_neta,
                SUM(CASE
                        WHEN ma.tipo IN ('compra', 'aporte', 'rendimiento') THEN ma.monto_total_minor
                        WHEN ma.tipo = 'venta' THEN -ma.monto_total_minor
                        ELSE 0
                    END)                                                      AS saldo_neto_minor
            FROM movimientos_activo ma
            GROUP BY ma.activo_id
            ORDER BY ma.activo_id;
            """
        )
        return [
            {
                "activo_id": fila["activo_id"],
                "cantidad_neta": fila["cantidad_neta"],
                "saldo_neto_minor": fila["saldo_neto_minor"],
            }
            for fila in filas
        ]

    # ----------------------------------------------------------
    # LISTADO DE MOVIMIENTOS (read-only, Tarea 6d)
    # ----------------------------------------------------------

    def list_movimientos(
        self,
        fecha_desde: Optional[str] = None,
        fecha_hasta: Optional[str] = None,
        objetivo_id: Optional[str] = None,
        tipo_activo: Optional[str] = None,
        tipo_movimiento: Optional[str] = None,
        activo_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Lista movimientos_activo de TODOS los activos (a diferencia de
        MovimientosActivoRepository.listar_por_activo()/listar_por_tipo(),
        que exigen un activo_id puntual) — necesario para el "Registro de
        movimientos" de ui/screens/ahorros.py (Tarea 6d), que muestra una
        tabla única con los movimientos de cualquier activo, filtrable por
        período/objetivo/tipo de activo/tipo de movimiento. No existía
        ningún método así antes de esta tarea (ni acá ni en el
        repositorio) — se agrega acá porque es exactamente el tipo de
        query (JOIN + filtros dinámicos) que ya viven en el service, no en
        un repositorio (mismo criterio que get_balance_por_cuenta()).

        Cada movimiento devuelve su(s) asignación(es) ya resueltas (con el
        nombre del objetivo, no solo su id) — la UI necesita mostrar
        varias etiquetas de objetivo en una sola fila cuando un movimiento
        está repartido entre más de uno, nunca fragmentado en filas
        separadas. moneda_id: la del movimiento (sección 33; la del activo
        si es un movimiento viejo sin moneda_id).

        objetivo_id filtra a los movimientos que tengan AL MENOS UNA
        asignación a ese objetivo (no excluye las demás asignaciones del
        mismo movimiento si está repartido con otros objetivos — la fila
        se sigue mostrando completa, con todas sus etiquetas).

        Búsqueda de texto libre NO es responsabilidad de este método —
        mismo criterio que TransaccionesRepository/list_transactions()
        (ver ui/components/registro_transacciones.py): se filtra
        client-side sobre lo ya devuelto acá.

        Returns:
            Lista de dicts {id, activo_id, activo_nombre, activo_tipo,
            activo_activa (0 si el instrumento está oculto, sección 35),
            moneda_id, tipo, fecha, cantidad, precio_unitario_minor,
            comision_minor, monto_total_minor, notas,
            transaccion_id (None si el movimiento es puramente informal,
            ver Tarea 6b),
            asignaciones: [{objetivo_id, objetivo_nombre, porcentaje}],
            monto_minor (el MONTO que ve el usuario: _monto_bruto())},
            ordenada por fecha descendente (más reciente primero).
        """
        condiciones = []
        params: list = []
        if fecha_desde is not None:
            condiciones.append("ma.fecha >= ?")
            params.append(fecha_desde)
        if fecha_hasta is not None:
            condiciones.append("ma.fecha <= ?")
            params.append(fecha_hasta)
        if tipo_activo is not None:
            condiciones.append("af.tipo = ?")
            params.append(tipo_activo)
        if tipo_movimiento is not None:
            condiciones.append("ma.tipo = ?")
            params.append(tipo_movimiento)
        if objetivo_id is not None:
            condiciones.append("ma.id IN (SELECT movimiento_id FROM asignaciones WHERE objetivo_id = ?)")
            params.append(objetivo_id)
        if activo_id is not None:  # "VER MOVIMIENTOS" de un activo (rediseño de Ahorros e Inversiones)
            condiciones.append("ma.activo_id = ?")
            params.append(activo_id)

        where_sql = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
        filas = self._db.fetchall(
            f"""
            SELECT
                ma.id                                                         AS id,
                ma.activo_id                                                  AS activo_id,
                af.nombre                                                     AS activo_nombre,
                af.tipo                                                       AS activo_tipo,
                af.activa                                                     AS activo_activa,
                COALESCE(ma.moneda_id, af.moneda_id)                          AS moneda_id,
                ma.tipo                                                       AS tipo,
                ma.fecha                                                      AS fecha,
                ma.cantidad                                                   AS cantidad,
                ma.precio_unitario_minor                                     AS precio_unitario_minor,
                ma.comision_minor                                            AS comision_minor,
                ma.monto_total_minor                                         AS monto_total_minor,
                ma.notas                                                     AS notas,
                ma.transaccion_id                                            AS transaccion_id
            FROM movimientos_activo ma
            JOIN activos_financieros af ON af.id = ma.activo_id
            {where_sql}
            ORDER BY ma.fecha DESC, ma.rowid DESC;
            """,
            tuple(params),
        )
        movimientos = [dict(fila) for fila in filas]
        if not movimientos:
            return []

        ids = [m["id"] for m in movimientos]
        placeholders = ",".join("?" for _ in ids)
        filas_asignaciones = self._db.fetchall(
            f"""
            SELECT
                a.movimiento_id                                               AS movimiento_id,
                a.objetivo_id                                                 AS objetivo_id,
                oa.nombre                                                     AS objetivo_nombre,
                a.porcentaje                                                  AS porcentaje
            FROM asignaciones a
            JOIN objetivos_ahorro oa ON oa.id = a.objetivo_id
            WHERE a.movimiento_id IN ({placeholders})
            ORDER BY a.movimiento_id, oa.nombre;
            """,
            tuple(ids),
        )
        asignaciones_por_movimiento: dict[int, list[dict]] = {}
        for fila in filas_asignaciones:
            asignaciones_por_movimiento.setdefault(fila["movimiento_id"], []).append({
                "objetivo_id": fila["objetivo_id"],
                "objetivo_nombre": fila["objetivo_nombre"],
                "porcentaje": fila["porcentaje"],
            })

        for movimiento in movimientos:
            movimiento["asignaciones"] = asignaciones_por_movimiento.get(movimiento["id"], [])
            movimiento["monto_minor"] = self._monto_bruto(movimiento)
        return movimientos

    # ----------------------------------------------------------
    # DELETE MOVIMIENTO (borrado por fila, Registro de Ahorros)
    # ----------------------------------------------------------

    def delete_movement(
        self,
        movimiento_id: str,
        eliminar_transaccion_vinculada: bool = False,
    ) -> SavingsResult:
        """
        Borra un movimiento_activo y sus asignaciones asociadas, atómico:
        primero las asignaciones (AsignacionesRepository.
        eliminar_por_movimiento(), evita violar la FK
        asignaciones.movimiento_id), después el movimiento
        (MovimientosActivoRepository.eliminar()) — DELETE físico en ambos
        casos, mismo criterio que un movimiento de ahorro es append-only sin
        historial de edición propio (ver docstring de
        MovimientosActivoRepository).

        Si el movimiento tiene transaccion_id vinculado (Tarea 6b), NO se
        borra automáticamente esa transacción — la decisión es del caller
        (la UI de Ahorros le pregunta explícitamente al usuario). Solo si
        eliminar_transaccion_vinculada=True se soft-elimina también esa
        transacción real (vía TransaccionesRepository.eliminar(), mismo
        soft-delete que usa TransactionService.delete()), dentro de la
        MISMA transacción atómica.

        Args:
            movimiento_id: El movimiento_activo a borrar.
            eliminar_transaccion_vinculada: Si True Y el movimiento tenía
                transaccion_id, también soft-elimina esa transacción real.
                Si el movimiento no tenía transaccion_id, este flag no tiene
                efecto (no hay nada que borrar).

        Returns:
            SavingsResult con data={"transaccion_id_eliminada": <id o None>}.

        Raises:
            MovimientoNotFoundError si movimiento_id no existe.
        """
        movimiento = self._movimientos_repo.obtener_por_id(movimiento_id)
        if movimiento is None:
            raise MovimientoNotFoundError(f"Movimiento id={movimiento_id} not found.")

        transaccion_id = movimiento["transaccion_id"]
        transaccion_eliminada = None

        conn = self._db.conn
        with self._db.transaction():
            self._asignaciones_repo.eliminar_por_movimiento(movimiento_id, conn=conn)
            self._movimientos_repo.eliminar(movimiento_id, conn=conn)

            if eliminar_transaccion_vinculada and transaccion_id is not None:
                self._transacciones_repo.eliminar(transaccion_id, conn=conn)
                transaccion_eliminada = transaccion_id

        return SavingsResult(
            success=True,
            entity_id=movimiento_id,
            data={"transaccion_id_eliminada": transaccion_eliminada},
            message=(
                f"Movimiento #{movimiento_id} eliminado."
                + (
                    f" Transacción vinculada #{transaccion_eliminada} también eliminada (soft-delete)."
                    if transaccion_eliminada is not None
                    else ""
                )
            ),
        )

    # ----------------------------------------------------------
    # OBJETIVOS DE CADA MOVIMIENTO (asignaciones)
    # ----------------------------------------------------------

    def _porcentajes(self, asignaciones: Optional[list[dict]]) -> list[tuple[str, float]]:
        """
        [{"objetivo_id", "porcentaje"}] → [(objetivo_id, porcentaje)],
        validado antes de escribir nada. None o [] = sin objetivos (el
        movimiento queda sin asignar).

        Raises:
            ObjetivoNotFoundError si un objetivo no existe.
            AsignacionInvalidaError si un porcentaje no está entre 0
                (exclusivo) y 100, un objetivo se repite o la suma pasa de 100.
        """
        porcentajes: list[tuple[str, float]] = []
        for asignacion in asignaciones or []:
            objetivo_id = asignacion["objetivo_id"]
            porcentaje = asignacion["porcentaje"]
            self._get_objetivo(objetivo_id)  # validate existence
            if not isinstance(porcentaje, (int, float)) or isinstance(porcentaje, bool) or not 0 < porcentaje <= 100:
                raise AsignacionInvalidaError(f"EL PORCENTAJE TIENE QUE ESTAR ENTRE 0 Y 100 (RECIBIDO: {porcentaje!r}).")
            if any(previo == objetivo_id for previo, _ in porcentajes):
                raise AsignacionInvalidaError("UN OBJETIVO NO PUEDE ESTAR DOS VECES EN EL MISMO MOVIMIENTO.")
            porcentajes.append((objetivo_id, float(porcentaje)))
        if sum(porcentaje for _, porcentaje in porcentajes) > 100 + TOLERANCIA_PORCENTAJE:
            raise AsignacionInvalidaError("LOS OBJETIVOS DEL MOVIMIENTO SUMAN MÁS DE 100%.")
        return porcentajes

    @staticmethod
    def _redondear_porcentajes(valores: list[float]) -> list[float]:
        """
        Porcentajes con DECIMALES_PORCENTAJE decimales sin cambiar la suma
        (método del resto mayor): 33.33333… × 3 → 33.3334 + 33.3333 +
        33.3333. Un reparto que cubría el 100% lo sigue cubriendo exacto, y
        uno que no, no pasa de lo que cubría.
        """
        escala = 10 ** DECIMALES_PORCENTAJE
        crudos = [valor * escala for valor in valores]
        enteros = [math.floor(crudo) for crudo in crudos]
        faltan = round(sum(crudos)) - sum(enteros)
        por_resto = sorted(range(len(crudos)), key=lambda i: crudos[i] - enteros[i], reverse=True)
        for indice in por_resto[:max(faltan, 0)]:
            enteros[indice] += 1
        return [entero / escala for entero in enteros]

    def get_reparto_proporcional(
        self, activo_id: str, fecha: str, excluir_movimiento_id: Optional[str] = None,
    ) -> list[dict]:
        """
        Lo que cada objetivo tenía en el activo ANTES de `fecha` (movimientos
        con fecha anterior, no los del mismo día: un interés que se acredita
        hoy lo generó lo que había hasta ayer), como porcentaje de lo que
        tenía el activo entero: [{objetivo_id, nombre, porcentaje}], el
        mayor primero. Es el reparto de un rendimiento por defecto
        (registrar_rendimiento() sin asignaciones; la UI lo precarga para
        poder cambiarlo). En unidades en acciones / CEDEARs, en plata en el
        resto. Lo que no es de ningún objetivo queda afuera (sin asignar);
        un objetivo en cero o en negativo no entra. [] si a esa fecha el
        activo no tenía nada de ningún objetivo.

        excluir_movimiento_id: para recalcular un rendimiento ya cargado sin
        contarlo a él mismo.

        Raises:
            ActivoNotFoundError si el activo no existe.
            SavingsError si la fecha no es AAAA-MM-DD.
        """
        activo = self._get_activo(activo_id)
        self._fecha(fecha)
        clave = "unidades" if activo["tipo"] in TIPOS_POR_UNIDADES else "saldo_minor"
        filtros = {"activo_id": activo_id, "antes_de": fecha, "excluir_movimiento_id": excluir_movimiento_id}
        total = self._estadisticas_por_activo(**filtros).get(activo_id, {}).get(clave) or 0
        partes = [p for p in self._partes_por_objetivo(**filtros).get(activo_id, []) if (p[clave] or 0) > TOLERANCIA_CANTIDAD]
        base = max(total, sum(p[clave] for p in partes))
        if not partes or base <= 0:
            return []
        porcentajes = self._redondear_porcentajes([p[clave] * 100 / base for p in partes])
        reparto = [
            {"objetivo_id": p["objetivo_id"], "nombre": p["nombre"], "porcentaje": porcentaje}
            for p, porcentaje in zip(partes, porcentajes)
            if porcentaje > 0
        ]
        return sorted(reparto, key=lambda r: r["porcentaje"], reverse=True)

    def update_asignaciones(self, movimiento_id: str, asignaciones: list[dict]) -> SavingsResult:
        """
        Cambia los objetivos de un movimiento ya cargado (celda OBJETIVOS de
        la tabla de ui/screens/ahorros.py): reemplaza sus asignaciones por
        `asignaciones` ([{"objetivo_id", "porcentaje"}]; [] = sin objetivos),
        con los montos sobre el monto del movimiento, mismo redondeo que el
        alta. También en un movimiento vinculado a una transacción del
        Registro: la transacción depende del monto y la fecha, no de los
        objetivos (CLAUDE.md §4). No toca otros movimientos: un rendimiento
        posterior conserva el reparto con el que se cargó.

        Raises:
            MovimientoNotFoundError si no existe.
            ObjetivoNotFoundError / AsignacionInvalidaError: ver _porcentajes().
        """
        movimiento = self._movimientos_repo.obtener_por_id(movimiento_id)
        if movimiento is None:
            raise MovimientoNotFoundError(f"Movimiento id={movimiento_id} not found.")
        porcentajes = self._porcentajes(asignaciones)
        conn = self._db.conn
        with self._db.transaction():
            self._asignaciones_repo.eliminar_por_movimiento(movimiento_id, conn=conn)
            creadas = self._crear_asignaciones(
                movimiento_id, self._montos_por_porcentaje(porcentajes, movimiento["monto_total_minor"]), conn,
            )
        return SavingsResult(
            success=True, entity_id=movimiento_id, data={"asignaciones": creadas},
            message="OBJETIVOS DEL MOVIMIENTO ACTUALIZADOS.",
        )

    @staticmethod
    def _montos_por_porcentaje(porcentajes: list[tuple[str, float]], monto_minor: int) -> list[tuple[str, float, int]]:
        """
        (objetivo_id, porcentaje, monto) de un movimiento: lo comparten el
        alta (_registrar_movimiento()), update_asignaciones() y
        update_movement() (con los porcentajes que ya tenía el movimiento).
        Cada monto se redondea hacia abajo; si los porcentajes suman 100, el
        resto del redondeo va al objetivo de mayor monto (así la suma da
        exacta), y si suman menos, lo que falta queda sin asignar. Montos en
        0 no generan asignación.
        """
        filas = [[objetivo_id, porcentaje, math.floor(monto_minor * porcentaje / 100)] for objetivo_id, porcentaje in porcentajes]
        if filas and abs(sum(porcentaje for _, porcentaje in porcentajes) - 100) <= TOLERANCIA_PORCENTAJE:
            mayor = max(filas, key=lambda fila: fila[2])
            mayor[2] += monto_minor - sum(fila[2] for fila in filas)
        return [(objetivo_id, porcentaje, monto) for objetivo_id, porcentaje, monto in filas if monto > 0]

    # ----------------------------------------------------------
    # MOVIMIENTOS (rediseño de Ahorros e Inversiones)
    # ----------------------------------------------------------

    @staticmethod
    def _fecha(fecha: str) -> str:
        try:
            datetime.strptime(fecha, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise SavingsError(f"FECHA INVÁLIDA '{fecha}': TIENE QUE SER AAAA-MM-DD.") from None
        return fecha

    @staticmethod
    def _positivo(valor: object, nombre: str) -> float:
        if not isinstance(valor, (int, float)) or isinstance(valor, bool) or valor <= 0:
            raise SavingsError(f"{nombre} TIENE QUE SER MAYOR A 0 (RECIBIDO: {valor!r}).")
        return valor

    @staticmethod
    def _comision(comision_minor: object) -> int:
        if not isinstance(comision_minor, int) or isinstance(comision_minor, bool) or comision_minor < 0:
            raise SavingsError(f"LA COMISIÓN TIENE QUE SER UN ENTERO >= 0 EN MINOR UNITS (RECIBIDO: {comision_minor!r}).")
        return comision_minor

    def _validar_transaccion_para_vincular(self, transaccion_id: str) -> None:
        if self._transacciones_repo.obtener_por_id(transaccion_id) is None:
            raise SavingsError(f"LA TRANSACCIÓN {transaccion_id} NO EXISTE.")
        if transaccion_id in self._movimientos_repo.transacciones_vinculadas():
            raise SavingsError("ESA TRANSACCIÓN YA ESTÁ VINCULADA A OTRO MOVIMIENTO DE AHORRO.")

    def _registrar_movimiento(
        self,
        activo: sqlite3.Row,
        tipo: str,
        texto: str,
        fecha: str,
        monto_total_minor: int,
        *,
        porcentajes: list[tuple[str, float]],
        moneda_id: int,
        cantidad: Optional[float] = None,
        precio_unitario_minor: Optional[int] = None,
        comision_minor: int = 0,
        dolar_oficial_momento_minor: Optional[int] = None,
        notas: Optional[str] = None,
        transaccion_id: Optional[str] = None,
        tipo_transaccion: Optional[str] = None,
        concepto_transaccion: str = "",
    ) -> SavingsResult:
        """
        Lo común a registrar_*(): movimiento + asignaciones con
        `porcentajes` (ya validados por el caller con _porcentajes()) +
        transacción del Registro, todo atómico. La transacción: la vinculada
        si se pasa transaccion_id (ya validada por el caller); si no, y hay
        tipo_transaccion, la que crea _crear_transaccion_vinculada() (None
        si el activo no tiene cuenta), en `moneda_id` — la del movimiento,
        ya resuelta por el caller con _moneda_movimiento().
        """
        if monto_total_minor <= 0:
            raise SavingsError(f"EL MONTO DEL MOVIMIENTO TIENE QUE SER MAYOR A 0 (QUEDÓ EN {monto_total_minor}).")
        reparto = self._montos_por_porcentaje(porcentajes, monto_total_minor)
        conn = self._db.conn
        try:
            with self._db.transaction():
                if transaccion_id is None and tipo_transaccion is not None:
                    transaccion_id = self._crear_transaccion_vinculada(
                        activo, fecha, monto_total_minor, tipo_transaccion, concepto_transaccion, notas, conn,
                        moneda_id=moneda_id,
                    )
                movimiento_id = self._movimientos_repo.crear(
                    activo_id=activo["id"], tipo=tipo, fecha=fecha, monto_total_minor=monto_total_minor,
                    cantidad=cantidad, precio_unitario_minor=precio_unitario_minor,
                    dolar_oficial_momento_minor=dolar_oficial_momento_minor, notas=notas,
                    transaccion_id=transaccion_id, conn=conn, comision_minor=comision_minor, moneda_id=moneda_id,
                )
                asignaciones = self._crear_asignaciones(movimiento_id, reparto, conn)
        except MovimientoDuplicadoError as err:
            raise SavingsError(f"MOVIMIENTO DUPLICADO (DOBLE CLICK): {err}") from err
        return SavingsResult(
            success=True,
            entity_id=movimiento_id,
            data={"asignaciones": asignaciones, "transaccion_id": transaccion_id, "monto_total_minor": monto_total_minor},
            message=f"{texto} DE '{activo['nombre'].upper()}' REGISTRADO.",
        )

    def registrar_compra(
        self,
        activo_id: str,
        cantidad: float,
        monto_minor: int,
        comision_minor: int,
        fecha: str,
        transaccion_id: Optional[str] = None,
        dolar_momento_minor: Optional[int] = None,
        notas: Optional[str] = None,
        crear_transaccion: bool = True,
        asignaciones: Optional[list[dict]] = None,
        moneda_id: Optional[int] = None,
    ) -> SavingsResult:
        """
        Compra de unidades (acciones, CEDEARs). monto_minor: el BRUTO,
        cantidad × precio, sin la comisión (sección 33: se carga el monto,
        no el precio unitario; decisión del usuario). monto_total = monto +
        comisión (lo que sale de la cuenta). El precio unitario se guarda
        calculado, round(monto / cantidad), para el PRECIO de la tabla. En
        los tipos por unidades (TIPOS_POR_UNIDADES) la cantidad tiene que
        ser entera. dolar_momento_minor va a dolar_oficial_momento_minor (la
        columna ya existía con ese nombre).

        moneda_id (acá, en registrar_venta() y en registrar_rendimiento()):
        la del movimiento; None = la del activo. Otra distinta solo en
        acciones / CEDEARs (_moneda_movimiento()). Va al último de la firma
        (el pedido la ponía tercera) para no correr los posicionales.

        crear_transaccion=False (acá y en registrar_venta/aporte/retiro): sin
        transaccion_id, NO crea la transacción del Registro aunque el
        activo tenga cuenta — el movimiento queda informal. Para cargar a
        mano datos históricos desde la tabla de ui/screens/ahorros.py
        (pedido explícito: sin transacción asociada sí o sí).

        asignaciones (acá y en registrar_venta/aporte/retiro): a qué
        objetivos va el movimiento, [{"objetivo_id", "porcentaje"}] (suma
        <= 100; lo que falta queda sin asignar). None o [] = sin objetivos.

        Raises:
            ActivoNotFoundError si el activo no existe.
            ObjetivoNotFoundError / AsignacionInvalidaError: ver _porcentajes().
            SavingsError si cantidad/monto no son > 0, la comisión es
                negativa, la fecha no es AAAA-MM-DD, la moneda no
                corresponde o la transacción a vincular no existe o ya está
                vinculada.
        """
        activo = self._get_activo(activo_id)
        self._positivo(cantidad, "LA CANTIDAD")
        if activo["tipo"] in TIPOS_POR_UNIDADES and not float(cantidad).is_integer():
            raise SavingsError("LA CANTIDAD DE ACCIONES / CEDEARS TIENE QUE SER ENTERA.")
        self._positivo(monto_minor, "EL MONTO")
        comision_minor = self._comision(comision_minor)
        self._fecha(fecha)
        moneda_id = self._moneda_movimiento(activo, moneda_id)
        porcentajes = self._porcentajes(asignaciones)
        if transaccion_id is not None:
            self._validar_transaccion_para_vincular(transaccion_id)
        return self._registrar_movimiento(
            activo, "compra", "COMPRA", fecha, int(monto_minor) + comision_minor,
            porcentajes=porcentajes, moneda_id=moneda_id, cantidad=cantidad,
            precio_unitario_minor=round(monto_minor / cantidad), comision_minor=comision_minor,
            dolar_oficial_momento_minor=dolar_momento_minor, notas=notas, transaccion_id=transaccion_id,
            tipo_transaccion="egreso" if crear_transaccion else None,
            concepto_transaccion=f"Compra — {activo['nombre']}",
        )

    def registrar_venta(
        self,
        activo_id: str,
        cantidad: float,
        monto_minor: int,
        comision_minor: int,
        fecha: str,
        transaccion_id: Optional[str] = None,
        notas: Optional[str] = None,
        crear_transaccion: bool = True,
        asignaciones: Optional[list[dict]] = None,
        moneda_id: Optional[int] = None,
    ) -> SavingsResult:
        """
        Venta de unidades. monto_minor: el BRUTO (ver registrar_compra());
        monto_total = monto − comisión (lo que entra a la cuenta). Puede ir
        en otra moneda que la compra (moneda_id, ver registrar_compra()).
        `asignaciones`: de qué objetivos salen (ver registrar_compra()).
        Solo se valida contra las unidades del activo entero, no las de cada
        objetivo (decisión del usuario: un objetivo puede quedar en
        negativo).

        Raises:
            ActivoNotFoundError si el activo no existe.
            ObjetivoNotFoundError / AsignacionInvalidaError: ver _porcentajes().
            SavingsError si cantidad/monto no son > 0, la comisión es
                negativa o se come todo el monto, se vende más de lo que
                hay, la fecha no es AAAA-MM-DD, la moneda no corresponde o
                la transacción a vincular no existe o ya está vinculada.
        """
        activo = self._get_activo(activo_id)
        self._positivo(cantidad, "LA CANTIDAD")
        if activo["tipo"] in TIPOS_POR_UNIDADES and not float(cantidad).is_integer():
            raise SavingsError("LA CANTIDAD DE ACCIONES / CEDEARS TIENE QUE SER ENTERA.")
        self._positivo(monto_minor, "EL MONTO")
        comision_minor = self._comision(comision_minor)
        self._fecha(fecha)
        moneda_id = self._moneda_movimiento(activo, moneda_id)
        porcentajes = self._porcentajes(asignaciones)
        disponibles = self._estadisticas_por_activo(activo_id).get(activo_id, {}).get("unidades") or 0
        if cantidad > disponibles:
            raise SavingsError(f"NO PODÉS VENDER {cantidad:g}: HAY {disponibles:g}.")
        if transaccion_id is not None:
            self._validar_transaccion_para_vincular(transaccion_id)
        return self._registrar_movimiento(
            activo, "venta", "VENTA", fecha, int(monto_minor) - comision_minor,
            porcentajes=porcentajes, moneda_id=moneda_id, cantidad=cantidad,
            precio_unitario_minor=round(monto_minor / cantidad), comision_minor=comision_minor,
            notas=notas, transaccion_id=transaccion_id, tipo_transaccion="ingreso" if crear_transaccion else None,
            concepto_transaccion=f"Venta — {activo['nombre']}",
        )

    def registrar_aporte(
        self,
        activo_id: str,
        monto_minor: int,
        fecha: str,
        comision_minor: int = 0,
        transaccion_id: Optional[str] = None,
        dolar_momento_minor: Optional[int] = None,
        notas: Optional[str] = None,
        crear_transaccion: bool = True,
        asignaciones: Optional[list[dict]] = None,
    ) -> SavingsResult:
        """
        Aporte de plata (FCI, plazo fijo / flex: tipo='aporte', sin
        unidades). No estaba en el pedido: es el movimiento de esos tipos
        en el diálogo. monto_total = monto (lo que queda invertido); la
        comisión se guarda aparte, informativa. `asignaciones`: ver
        registrar_compra().

        Raises:
            ActivoNotFoundError si el activo no existe.
            ObjetivoNotFoundError / AsignacionInvalidaError: ver _porcentajes().
            SavingsError si el monto no es > 0, la comisión es negativa,
                la fecha no es AAAA-MM-DD o la transacción a vincular no
                existe o ya está vinculada.
        """
        activo = self._get_activo(activo_id)
        self._positivo(monto_minor, "EL MONTO")
        comision_minor = self._comision(comision_minor)
        self._fecha(fecha)
        porcentajes = self._porcentajes(asignaciones)
        if transaccion_id is not None:
            self._validar_transaccion_para_vincular(transaccion_id)
        return self._registrar_movimiento(
            activo, "aporte", "APORTE", fecha, int(monto_minor), porcentajes=porcentajes,
            moneda_id=activo["moneda_id"], comision_minor=comision_minor,
            dolar_oficial_momento_minor=dolar_momento_minor, notas=notas, transaccion_id=transaccion_id,
            tipo_transaccion="egreso" if crear_transaccion else None,
            concepto_transaccion=f"Aporte a ahorro — {activo['nombre']}",
        )

    def registrar_retiro(
        self,
        activo_id: str,
        monto_minor: int,
        fecha: str,
        comision_minor: int = 0,
        transaccion_id: Optional[str] = None,
        notas: Optional[str] = None,
        crear_transaccion: bool = True,
        asignaciones: Optional[list[dict]] = None,
    ) -> SavingsResult:
        """
        Retiro de plata de un activo por monto (FCI, plazos): tipo='venta'
        sin unidades. No estaba en el pedido (ver registrar_aporte()). No
        puede superar el saldo del activo entero; el de cada objetivo no se
        valida (ver registrar_venta()). `asignaciones`: de qué objetivos
        sale (ver registrar_compra()).

        Raises:
            ActivoNotFoundError si el activo no existe.
            ObjetivoNotFoundError / AsignacionInvalidaError: ver _porcentajes().
            SavingsError si el monto no es > 0 o supera el saldo, la
                comisión es negativa, la fecha no es AAAA-MM-DD o la
                transacción a vincular no existe o ya está vinculada.
        """
        activo = self._get_activo(activo_id)
        self._positivo(monto_minor, "EL MONTO")
        comision_minor = self._comision(comision_minor)
        self._fecha(fecha)
        porcentajes = self._porcentajes(asignaciones)
        saldo = self._estadisticas_por_activo(activo_id).get(activo_id, {}).get("saldo_minor") or 0
        if monto_minor > saldo:
            raise SavingsError("EL RETIRO SUPERA EL SALDO DEL ACTIVO.")
        if transaccion_id is not None:
            self._validar_transaccion_para_vincular(transaccion_id)
        return self._registrar_movimiento(
            activo, "venta", "RETIRO", fecha, int(monto_minor), porcentajes=porcentajes,
            moneda_id=activo["moneda_id"], comision_minor=comision_minor, notas=notas,
            transaccion_id=transaccion_id, tipo_transaccion="ingreso" if crear_transaccion else None,
            concepto_transaccion=f"Retiro de ahorro — {activo['nombre']}",
        )

    def registrar_rendimiento(
        self, activo_id: str, monto_minor: int, fecha: str, notas: Optional[str] = None,
        asignaciones: Optional[list[dict]] = None, moneda_id: Optional[int] = None,
    ) -> SavingsResult:
        """
        Rendimiento (intereses, dividendos). Sin `asignaciones` (None) se
        reparte según get_reparto_proporcional(): lo que cada objetivo tenía
        en el activo antes de esa fecha (decisión del usuario: proporcional
        por defecto, editable). Con una lista (también []), esa: ver
        registrar_compra(). moneda_id: un dividendo de una acción / CEDEAR
        puede venir en otra moneda (ver registrar_compra()). Sin transacción
        del Registro (mismo criterio que register_return(),
        docs/DATA_MODEL_DECISIONS.md sección 16).

        Raises:
            ActivoNotFoundError si el activo no existe.
            ObjetivoNotFoundError / AsignacionInvalidaError: ver _porcentajes().
            SavingsError si el monto no es > 0, la fecha no es AAAA-MM-DD o
                la moneda no corresponde.
        """
        activo = self._get_activo(activo_id)
        self._positivo(monto_minor, "EL MONTO")
        self._fecha(fecha)
        moneda_id = self._moneda_movimiento(activo, moneda_id)
        if asignaciones is None:
            asignaciones = self.get_reparto_proporcional(activo_id, fecha)
        porcentajes = self._porcentajes(asignaciones)
        return self._registrar_movimiento(
            activo, "rendimiento", "RENDIMIENTO", fecha, int(monto_minor), porcentajes=porcentajes,
            moneda_id=moneda_id, notas=notas,
        )

    def update_movement(self, movimiento_id: str, **cambios: object) -> SavingsResult:
        """
        Corrige un movimiento ya cargado (edición inline de la tabla de
        ui/screens/ahorros.py; ventana de corrección temprana, CLAUDE.md §4).
        `cambios`: solo las claves de CAMPOS_EDITABLES_MOVIMIENTO — fecha,
        cantidad, comision_minor, monto_minor, moneda_id, notas (vacías =
        sin notas). Ni el activo ni el tipo se editan: eso es borrar y
        volver a cargar.

        - Vinculado a una transacción del Registro (transaccion_id): solo
          las notas. El resto lo bloquea — la transacción depende de este
          monto/fecha/moneda y no se reescribe en silencio.
        - monto_minor es el MONTO que ve el usuario (_monto_bruto()). En una
          compra / venta con cantidad es el bruto (sección 33): el total se
          recalcula (monto ± comisión) y el precio unitario también (monto /
          cantidad: no se edita, es calculado). Cambiar la cantidad o la
          comisión deja el monto bruto como estaba.
        - El resto (aporte, retiro, rendimiento): monto y comisión directo
          (un rendimiento no tiene comisión; la de un aporte / retiro es
          informativa).
        - moneda_id: solo en acciones / CEDEARs (_moneda_movimiento()).
        - Si el monto cambia, las asignaciones se rehacen con los MISMOS
          porcentajes que ya tenía el movimiento, mismo redondeo que el
          alta (los objetivos se cambian con update_asignaciones()).
        - No puede dejar la tenencia en negativo: las unidades en acciones /
          CEDEARs, el saldo en el resto (solo si antes no lo estaba ya). Se
          chequea con el cambio ya escrito, dentro de la transacción: si
          falla, rollback.

        Raises:
            MovimientoNotFoundError si no existe.
            SavingsError si se pide editar algo que no se puede (clave
                desconocida — el precio unitario, por ejemplo —, movimiento
                vinculado, comisión de un rendimiento, otra moneda fuera de
                acciones / CEDEARs), un valor no es válido o la tenencia
                quedaría negativa.
        """
        desconocidos = set(cambios) - set(CAMPOS_EDITABLES_MOVIMIENTO)
        if desconocidos:
            raise SavingsError(f"NO SE PUEDE EDITAR: {', '.join(sorted(desconocidos)).upper()}.")
        movimiento = self._movimientos_repo.obtener_por_id(movimiento_id)
        if movimiento is None:
            raise MovimientoNotFoundError(f"Movimiento id={movimiento_id} not found.")
        if movimiento["transaccion_id"] is not None and set(cambios) - {"notas"}:
            raise SavingsError(
                "ESTE MOVIMIENTO ESTÁ VINCULADO A UNA TRANSACCIÓN DEL REGISTRO: SOLO SE PUEDEN EDITAR LAS NOTAS "
                "Y LOS OBJETIVOS. "
                "PARA CORREGIR EL RESTO, BORRALO Y VOLVÉ A CARGARLO."
            )
        activo = self._get_activo(movimiento["activo_id"])

        valores: dict = {}
        if "notas" in cambios:
            valores["notas"] = (str(cambios["notas"] or "")).strip() or None
        if "fecha" in cambios:
            valores["fecha"] = self._fecha(cambios["fecha"])
        if "comision_minor" in cambios:
            if movimiento["tipo"] == "rendimiento":
                raise SavingsError("UN RENDIMIENTO NO TIENE COMISIÓN.")
            valores["comision_minor"] = self._comision(cambios["comision_minor"])
        if "cantidad" in cambios:
            if movimiento["cantidad"] is None:
                raise SavingsError("ESTE MOVIMIENTO NO TIENE CANTIDAD.")
            cantidad = self._positivo(cambios["cantidad"], "LA CANTIDAD")
            if activo["tipo"] in TIPOS_POR_UNIDADES and not float(cantidad).is_integer():
                raise SavingsError("LA CANTIDAD DE ACCIONES / CEDEARS TIENE QUE SER ENTERA.")
            valores["cantidad"] = cantidad
        if "moneda_id" in cambios:
            moneda_id = self._moneda_movimiento(activo, cambios["moneda_id"])
            if moneda_id != movimiento["moneda_id"]:
                valores["moneda_id"] = moneda_id

        bruto = (
            int(self._positivo(cambios["monto_minor"], "EL MONTO")) if "monto_minor" in cambios
            else self._monto_bruto(movimiento)
        )
        if movimiento["tipo"] in ("compra", "venta") and movimiento["cantidad"] is not None:
            # Por monto bruto (sección 33): total y precio salen de él.
            cantidad = valores.get("cantidad", movimiento["cantidad"])
            comision = valores.get("comision_minor", movimiento["comision_minor"] or 0)
            signo = 1 if movimiento["tipo"] == "compra" else -1
            monto_nuevo = bruto + signo * comision
            precio = round(bruto / cantidad)
            if precio != movimiento["precio_unitario_minor"]:
                valores["precio_unitario_minor"] = precio
        else:
            monto_nuevo = bruto
        if monto_nuevo <= 0:
            raise SavingsError(f"EL MONTO DEL MOVIMIENTO TIENE QUE SER MAYOR A 0 (QUEDARÍA EN {monto_nuevo}).")
        if monto_nuevo != movimiento["monto_total_minor"]:
            valores["monto_total_minor"] = monto_nuevo
        if not valores:
            return SavingsResult(success=True, entity_id=movimiento_id, message="SIN CAMBIOS.")

        antes = self._estadisticas_por_activo(activo["id"]).get(activo["id"], {})
        conn = self._db.conn
        with self._db.transaction():
            self._movimientos_repo.actualizar(movimiento_id, conn=conn, **valores)
            if "monto_total_minor" in valores:
                porcentajes = [
                    (a["objetivo_id"], a["porcentaje"]) for a in self._asignaciones_repo.listar_por_movimiento(movimiento_id)
                ]
                self._asignaciones_repo.eliminar_por_movimiento(movimiento_id, conn=conn)
                self._crear_asignaciones(movimiento_id, self._montos_por_porcentaje(porcentajes, monto_nuevo), conn)
            # Misma conexión: ve lo recién escrito. Si queda negativa, la excepción hace rollback.
            despues = self._estadisticas_por_activo(activo["id"]).get(activo["id"], {})
            self._validar_tenencia(activo, antes, despues)
        return SavingsResult(
            success=True,
            entity_id=movimiento_id,
            data={"monto_total_minor": monto_nuevo},
            message=f"MOVIMIENTO DE '{activo['nombre'].upper()}' ACTUALIZADO.",
        )

    @staticmethod
    def _validar_tenencia(activo: sqlite3.Row, antes: dict, despues: dict) -> None:
        """update_movement(): la tenencia no puede pasar a negativa (si ya lo era, no se bloquea)."""
        if activo["tipo"] in TIPOS_POR_UNIDADES:
            clave, mensaje = "unidades", "LA CANTIDAD DEL ACTIVO QUEDARÍA NEGATIVA (MÁS VENTAS QUE COMPRAS)."
        else:
            clave, mensaje = "saldo_minor", "EL SALDO DEL ACTIVO QUEDARÍA NEGATIVO (MÁS RETIROS QUE APORTES)."
        if (antes.get(clave) or 0) >= 0 and (despues.get(clave) or 0) < 0:
            raise SavingsError(mensaje)

    def list_transacciones_vinculables(self, fecha: str) -> list[dict]:
        """
        Transacciones del Registro del mes de `fecha` (sin las eliminadas)
        que todavía no están vinculadas a ningún movimiento de ahorro —
        para el selector "VINCULAR A TRANSACCIÓN" del diálogo de
        movimiento. Shape de TransaccionesRepository.listar_enriquecida(),
        la más nueva primero, hasta MAX_TRANSACCIONES_VINCULABLES.

        Raises:
            SavingsError si la fecha no es AAAA-MM-DD.
        """
        dia = datetime.strptime(self._fecha(fecha), "%Y-%m-%d").date()
        desde = dia.replace(day=1)
        hasta = date(dia.year + (dia.month == 12), dia.month % 12 + 1, 1)  # día 1 del mes siguiente
        vinculadas = self._movimientos_repo.transacciones_vinculadas()
        filas = self._transacciones_repo.listar_enriquecida(
            fecha_desde=desde.isoformat(), fecha_hasta=hasta.isoformat(), por_pagina=MAX_TRANSACCIONES_VINCULABLES,
        )
        return [dict(fila) for fila in filas if fila["id"] not in vinculadas and fila["fecha"] < hasta.isoformat()]

    # ----------------------------------------------------------
    # RESÚMENES (pestañas de ui/screens/ahorros.py)
    # ----------------------------------------------------------

    @staticmethod
    def _filtro_movimientos(
        activo_id: Optional[str], antes_de: Optional[str], excluir_movimiento_id: Optional[str],
    ) -> tuple[str, tuple]:
        """WHERE sobre movimientos_activo (alias ma) de las agregaciones: un activo, antes de una fecha, sin un movimiento."""
        condiciones: list[str] = []
        params: list = []
        if activo_id is not None:
            condiciones.append("ma.activo_id = ?")
            params.append(activo_id)
        if antes_de is not None:
            condiciones.append("ma.fecha < ?")
            params.append(antes_de)
        if excluir_movimiento_id is not None:
            condiciones.append("ma.id <> ?")
            params.append(excluir_movimiento_id)
        return (f"WHERE {' AND '.join(condiciones)}" if condiciones else ""), tuple(params)

    def _estadisticas_por_activo(
        self, activo_id: Optional[str] = None, antes_de: Optional[str] = None,
        excluir_movimiento_id: Optional[str] = None,
    ) -> dict[str, dict]:
        """
        activo_id → {saldo_minor, unidades, movimientos} con todos sus
        movimientos: saldo = entradas − ventas; unidades = compras/aportes −
        ventas (None si nunca se cargó una cantidad); movimientos: cuántos
        hay. Agregación de reporte, mismo criterio
        que get_balance_por_activo(). antes_de / excluir_movimiento_id: ver
        get_reparto_proporcional().

        El saldo suma montos sin mirar su moneda (sección 33): vale en FCI,
        plazos y el resto, que tienen una sola. En acciones / CEDEARs, que
        pueden tener movimientos en varias, lo que cuenta son las unidades
        (y _precios_promedio(), por moneda): ese saldo no se muestra.
        """
        where, params = self._filtro_movimientos(activo_id, antes_de, excluir_movimiento_id)
        filas = self._db.fetchall(
            f"""
            SELECT
                ma.activo_id                                                          AS activo_id,
                SUM(CASE WHEN ma.tipo IN ('compra', 'aporte', 'rendimiento') THEN ma.monto_total_minor
                         WHEN ma.tipo = 'venta' THEN -ma.monto_total_minor ELSE 0 END)   AS saldo_minor,
                SUM(CASE WHEN ma.tipo IN ('compra', 'aporte') THEN ma.cantidad
                         WHEN ma.tipo = 'venta' THEN -ma.cantidad END)                   AS unidades,
                COUNT(*)                                                              AS movimientos
            FROM movimientos_activo ma
            {where}
            GROUP BY ma.activo_id;
            """,
            params,
        )
        return {fila["activo_id"]: dict(fila) for fila in filas}

    def _precios_promedio(self) -> dict[str, list[dict]]:
        """
        activo_id → [{moneda_id, precio_promedio_minor}]: de las compras con
        cantidad, POR MONEDA (sección 33: una compra en ARS y otra en USD no
        se promedian juntas), sin comisiones — monto bruto / unidades
        (_monto_bruto(): total − comisión). La moneda de un movimiento viejo
        sin moneda_id es la del activo.
        """
        filas = self._db.fetchall(
            """
            SELECT
                ma.activo_id                                                          AS activo_id,
                COALESCE(ma.moneda_id, af.moneda_id)                                  AS moneda_id,
                SUM(ma.monto_total_minor - COALESCE(ma.comision_minor, 0))            AS costo_minor,
                SUM(ma.cantidad)                                                      AS unidades
            FROM movimientos_activo ma
            JOIN activos_financieros af ON af.id = ma.activo_id
            WHERE ma.tipo = 'compra' AND ma.cantidad > 0
            GROUP BY ma.activo_id, COALESCE(ma.moneda_id, af.moneda_id);
            """
        )
        precios: dict[str, list[dict]] = {}
        for fila in filas:
            precios.setdefault(fila["activo_id"], []).append(
                {"moneda_id": fila["moneda_id"], "precio_promedio_minor": round(fila["costo_minor"] / fila["unidades"])}
            )
        return precios

    def _partes_por_objetivo(
        self, activo_id: Optional[str] = None, antes_de: Optional[str] = None,
        excluir_movimiento_id: Optional[str] = None,
    ) -> dict[str, list[dict]]:
        """
        activo_id → [{objetivo_id, nombre, saldo_minor, unidades}]: lo de
        cada objetivo en cada activo según las asignaciones de sus
        movimientos — aportes, compras y rendimientos suman lo asignado,
        ventas / retiros lo restan; las unidades, la cantidad del movimiento
        × su porcentaje (None si el activo nunca cargó cantidad). Mismos
        filtros que _estadisticas_por_activo().
        """
        where, params = self._filtro_movimientos(activo_id, antes_de, excluir_movimiento_id)
        filas = self._db.fetchall(
            f"""
            SELECT
                ma.activo_id                                                          AS activo_id,
                a.objetivo_id                                                         AS objetivo_id,
                o.nombre                                                              AS nombre,
                SUM(CASE WHEN ma.tipo IN ('compra', 'aporte', 'rendimiento') THEN a.monto_asignado_minor
                         WHEN ma.tipo = 'venta' THEN -a.monto_asignado_minor ELSE 0 END) AS saldo_minor,
                SUM(CASE WHEN ma.tipo IN ('compra', 'aporte') THEN ma.cantidad * a.porcentaje / 100.0
                         WHEN ma.tipo = 'venta' THEN -ma.cantidad * a.porcentaje / 100.0 END) AS unidades
            FROM asignaciones a
            JOIN movimientos_activo ma ON ma.id = a.movimiento_id
            JOIN objetivos_ahorro o    ON o.id = a.objetivo_id
            {where}
            GROUP BY ma.activo_id, a.objetivo_id
            ORDER BY o.nombre;
            """,
            params,
        )
        partes: dict[str, list[dict]] = {}
        for fila in filas:
            partes.setdefault(fila["activo_id"], []).append(dict(fila))
        return partes

    def get_resumen_por_tipo(self, incluir_ocultos: bool = False) -> dict:
        """
        Los activos activos (también los ocultos con incluir_ocultos=True,
        sección 35) agrupados por tipo (en el orden de TIPOS_ACTIVO; solo
        los tipos que tienen alguno), cada grupo por broker y nombre:
        {tipo: [{activo_id, activo, tipo, broker_id, broker (nombre o None),
        cuenta_id, moneda_id, moneda (código), simbolo, decimales,
        por_unidades, cantidad, unidades, saldo_minor,
        precios_promedio: [{moneda_id, moneda, simbolo, decimales,
        precio_promedio_minor}], comision_compra_minor, comision_venta_minor,
        objetivos: [{objetivo_id, nombre, porcentaje, saldo_minor, unidades}],
        sin_asignar: {porcentaje, saldo_minor, unidades},
        activa, movimientos, con_tenencia}]}.

        activa: False si está oculto. movimientos: cuántos tiene.
        con_tenencia: todavía tiene unidades (acciones / CEDEARs) o saldo
        (el resto) — la regla de ocultar_activo(); sin movimientos se puede
        eliminar (delete_activo()).

        moneda / simbolo / decimales: los del activo (la moneda por defecto
        de sus movimientos, sección 33).
        cantidad: unidades en acciones / CEDEARs (TIPOS_POR_UNIDADES); en el
        resto, el saldo en la moneda del activo (float: 285432.50).
        saldo_minor: en acciones / CEDEARs no se muestra (puede sumar
        monedas distintas: ver _estadisticas_por_activo()).
        precios_promedio: de las compras con cantidad, uno por moneda
        (_precios_promedio()), primero el de la moneda del activo; [] si no
        hay compras.
        objetivos: la parte de cada objetivo que no es cero, la mayor
        primero, sumando las asignaciones de los movimientos
        (_partes_por_objetivo()); sin_asignar: lo que queda. porcentaje:
        sobre las unidades (acciones / CEDEARs) o el saldo del activo; None
        si el activo no tiene nada.
        """
        estadisticas = self._estadisticas_por_activo()
        partes = self._partes_por_objetivo()
        precios = self._precios_promedio()
        brokers = {b["id"]: b for b in self._brokers_repo.listar(solo_activos=False)}
        monedas = {m["id"]: m for m in self._db.obtener_monedas()}

        def _precio_con_moneda(precio: dict) -> dict:
            moneda_precio = monedas.get(precio["moneda_id"])
            return {
                **precio,
                "moneda": moneda_precio["codigo"] if moneda_precio else "",
                "simbolo": (moneda_precio["simbolo"] or "") if moneda_precio else "",
                "decimales": moneda_precio["decimales"] if moneda_precio else 2,
            }

        grupos: dict[str, list[dict]] = {}
        for activo in self._activos_repo.listar(solo_activos=not incluir_ocultos):
            stats = estadisticas.get(activo["id"], {})
            moneda = monedas.get(activo["moneda_id"])
            decimales = moneda["decimales"] if moneda else 2
            broker = brokers.get(activo["broker_id"]) if activo["broker_id"] else None
            saldo = stats.get("saldo_minor") or 0
            unidades = stats.get("unidades")
            precios_activo = sorted(
                (_precio_con_moneda(p) for p in precios.get(activo["id"], [])),
                key=lambda p: (p["moneda_id"] != activo["moneda_id"], p["moneda"]),
            )
            por_unidades = activo["tipo"] in TIPOS_POR_UNIDADES
            objetivos, sin_asignar = self._objetivos_de_activo(
                partes.get(activo["id"], []), "unidades" if por_unidades else "saldo_minor", saldo, unidades,
            )
            grupos.setdefault(activo["tipo"], []).append({
                "activo_id": activo["id"],
                "activo": activo["nombre"],
                "tipo": activo["tipo"],
                "broker_id": activo["broker_id"],
                "broker": broker["nombre"] if broker else None,
                "cuenta_id": activo["cuenta_id"],
                "moneda_id": activo["moneda_id"],
                "moneda": moneda["codigo"] if moneda else "",
                "simbolo": (moneda["simbolo"] or "") if moneda else "",
                "decimales": decimales,
                "por_unidades": por_unidades,
                "cantidad": (unidades or 0) if por_unidades else saldo / 10 ** decimales,
                "unidades": unidades,
                "saldo_minor": saldo,
                "precios_promedio": precios_activo,
                "comision_compra_minor": activo["comision_compra_minor"] or 0,
                "comision_venta_minor": activo["comision_venta_minor"] or 0,
                "objetivos": objetivos,
                "sin_asignar": sin_asignar,
                "activa": bool(activo["activa"]),
                "movimientos": stats.get("movimientos") or 0,
                "con_tenencia": self._hay_tenencia(activo["tipo"], saldo, unidades),
            })

        orden = {tipo: indice for indice, tipo in enumerate(TIPOS_ACTIVO)}
        return {
            tipo: sorted(grupos[tipo], key=lambda e: ((e["broker"] or "").upper(), e["activo"].upper()))
            for tipo in sorted(grupos, key=lambda t: orden.get(t, len(orden)))
        }

    @staticmethod
    def _objetivos_de_activo(
        partes: list[dict], clave: str, saldo_minor: int, unidades: Optional[float],
    ) -> tuple[list[dict], dict]:
        """
        (objetivos, sin_asignar) de una entrada de get_resumen_por_tipo().
        `clave`: "unidades" o "saldo_minor", lo que manda en ese activo
        (qué parte es cero, el orden y el porcentaje).
        """
        total = (unidades or 0) if clave == "unidades" else saldo_minor

        def _porcentaje(valor: Optional[float]) -> Optional[float]:
            return (valor or 0) * 100 / total if total > 0 else None

        objetivos = [
            {
                "objetivo_id": p["objetivo_id"], "nombre": p["nombre"], "porcentaje": _porcentaje(p[clave]),
                "saldo_minor": p["saldo_minor"] or 0, "unidades": p["unidades"],
            }
            for p in partes
            if abs(p[clave] or 0) > TOLERANCIA_CANTIDAD
        ]
        objetivos.sort(key=lambda o: o[clave] or 0, reverse=True)
        # Lo que queda, contra TODAS las partes (también las que en `clave` dan cero).
        resto_unidades = unidades - sum(p["unidades"] or 0 for p in partes) if unidades is not None else None
        resto = {"saldo_minor": saldo_minor - sum(p["saldo_minor"] or 0 for p in partes), "unidades": resto_unidades}
        resto["porcentaje"] = _porcentaje(resto[clave])
        return objetivos, resto

    @staticmethod
    def _parte_de_activo(entrada: dict, parte: dict, objetivo_id: Optional[str]) -> dict:
        """Una parte (de `objetivos` o `sin_asignar`) de una entrada de get_resumen_por_tipo(), para get_resumen_por_objetivo()."""
        saldo = parte["saldo_minor"]
        unidades = parte["unidades"]
        return {
            "objetivo_id": objetivo_id,
            "tipo": entrada["tipo"],
            "activo": entrada["activo"],
            "activo_id": entrada["activo_id"],
            "broker": entrada["broker"],
            "porcentaje": parte["porcentaje"],
            "por_unidades": entrada["por_unidades"],
            "cantidad": (unidades or 0) if entrada["por_unidades"] else saldo / 10 ** entrada["decimales"],
            "unidades": unidades,
            "saldo_minor": saldo,
            "moneda": entrada["moneda"],
            "simbolo": entrada["simbolo"],
            "decimales": entrada["decimales"],
        }

    def get_resumen_por_objetivo(self) -> dict:
        """
        Lo de cada objetivo en cada activo, sumando las asignaciones de los
        movimientos (ver get_resumen_por_tipo()): {objetivo (nombre):
        [{objetivo_id, tipo, activo, activo_id, broker, porcentaje,
        por_unidades, cantidad, unidades, saldo_minor, moneda, simbolo,
        decimales}]}, por nombre de objetivo. Al final, OBJETIVO_SIN_ASIGNAR
        con lo que no es de ningún objetivo (no estaba en el pedido: sin
        esto, esa plata no aparecería en esta vista). Un objetivo puede
        quedar en negativo (retiros que no se validan por objetivo, ver
        registrar_retiro()): se muestra tal cual.
        """
        por_objetivo: dict[str, list[dict]] = {}
        for entradas in self.get_resumen_por_tipo().values():
            for entrada in entradas:
                for objetivo in entrada["objetivos"]:
                    por_objetivo.setdefault(objetivo["nombre"], []).append(
                        self._parte_de_activo(entrada, objetivo, objetivo["objetivo_id"])
                    )
                resto = entrada["sin_asignar"]
                if abs(resto["unidades" if entrada["por_unidades"] else "saldo_minor"] or 0) > TOLERANCIA_CANTIDAD:
                    por_objetivo.setdefault(OBJETIVO_SIN_ASIGNAR, []).append(self._parte_de_activo(entrada, resto, None))
        claves = sorted((clave for clave in por_objetivo if clave != OBJETIVO_SIN_ASIGNAR), key=str.upper)
        if OBJETIVO_SIN_ASIGNAR in por_objetivo:
            claves.append(OBJETIVO_SIN_ASIGNAR)
        return {clave: por_objetivo[clave] for clave in claves}
