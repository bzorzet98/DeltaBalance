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
    - Reparto VIGENTE de cada activo entre objetivos (activo_objetivos:
      assign_objetivo()/remove_objetivo(), suma <= 100). Decisión del
      usuario: cada movimiento de los métodos nuevos (registrar_*())
      genera sus `asignaciones` con esos porcentajes — así
      get_objetivo_balance() y el resto de los saldos por objetivo siguen
      andando. La venta también se prorratea (el pedido no le pone
      objetivo); lo que no está repartido (suma < 100) queda sin asignar.
      Los activos que ya existían arrancan sin reparto.
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
    Los mensajes nuevos (los de estos métodos) ya vienen en MAYÚSCULAS;
    los de los métodos anteriores siguen en inglés.
"""

import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from db.database import DatabaseManager
from repositories.activo_objetivos_repository import TOLERANCIA_PORCENTAJE, ActivoObjetivosRepository
from repositories.activos_financieros_repository import ActivosFinancierosRepository
from repositories.brokers_repository import BrokersRepository
from repositories.objetivos_ahorro_repository import ObjetivosAhorroRepository
from repositories.movimientos_activo_repository import MovimientoDuplicadoError, MovimientosActivoRepository
from repositories.asignaciones_repository import AsignacionesRepository
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
    _CATEGORIA_AHORRO_SUBCATEGORIA = "Ahorro/Inversión"

    def _get_categoria_ahorro_inversion_id(self) -> int:
        """
        Resuelve el id de la categoría protegida 'MOVIMIENTO CAPITAL ·
        Ahorro/Inversión' por nombre — nunca hardcodeado el id numérico,
        que varía entre bases (dummy DB de verify/, DB real del usuario,
        etc.). Viene del seed (ver db/seed.sql), así que se espera activa.
        """
        row = self._db.fetchone(
            "SELECT id FROM categorias WHERE categoria_principal = ? AND subcategoria = ? AND activa = 1;",
            (self._CATEGORIA_AHORRO_PRINCIPAL, self._CATEGORIA_AHORRO_SUBCATEGORIA),
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

    def _crear_transaccion_vinculada(
        self, activo: sqlite3.Row, fecha: str, monto_minor: int, tipo_movimiento: str,
        concepto: str, notas: Optional[str], conn: sqlite3.Connection,
    ) -> Optional[str]:
        """
        Transacción real del movimiento (Tareas 6b/6g): en la cuenta del
        activo, con la categoría protegida 'Ahorro/Inversión' y la moneda
        del activo. None si el activo no tiene cuenta (movimiento informal).
        """
        if activo["cuenta_id"] is None:
            return None
        return self._transacciones_repo.crear(
            fecha=fecha,
            concepto=concepto,
            cuenta_id=activo["cuenta_id"],
            categoria_id=self._get_categoria_ahorro_inversion_id(),
            moneda_id=activo["moneda_id"],
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

    def create_objetivo(
        self,
        nombre: str,
        monto_meta_minor: Optional[int] = None,
        fecha_meta: Optional[str] = None,
    ) -> SavingsResult:
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
            )
            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="compra", fecha=fecha,
                monto_total_minor=monto_total_minor, cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                dolar_oficial_momento_minor=dolar_oficial_momento_minor,
                notas=notas, transaccion_id=transaccion_id, conn=conn,
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
        self._get_activo(activo_id)
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
                    monto_total_minor=monto_total_minor, notas=notas, conn=conn,
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
                monto_total_minor=monto_total_minor, notas=notas, conn=conn,
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
            )
            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="venta", fecha=fecha,
                monto_total_minor=monto_total_minor, cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                dolar_oficial_momento_minor=dolar_oficial_momento_minor,
                notas=notas, transaccion_id=transaccion_id, conn=conn,
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
        activos_financieros.tipo Y moneda_id — nunca mezcla monedas
        distintas en una misma suma, mismo criterio que
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
                af.moneda_id                                                  AS moneda_id,
                SUM(CASE
                        WHEN ma.tipo IN ('compra', 'aporte', 'rendimiento') THEN ma.monto_total_minor
                        WHEN ma.tipo = 'venta' THEN -ma.monto_total_minor
                        ELSE 0
                    END)                                                      AS saldo_neto_minor
            FROM movimientos_activo ma
            JOIN activos_financieros af ON af.id = ma.activo_id
            GROUP BY af.tipo, af.moneda_id
            ORDER BY af.tipo, af.moneda_id;
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
        separadas. moneda_id sale de activos_financieros.moneda_id (la
        moneda de REFERENCIA del activo) — movimientos_activo todavía no
        tiene su propia columna de moneda (eso es Tarea 6c, explícitamente
        fuera de alcance acá).

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
            moneda_id, tipo, fecha, cantidad, monto_total_minor,
            transaccion_id (None si el movimiento es puramente informal,
            ver Tarea 6b),
            asignaciones: [{objetivo_id, objetivo_nombre, porcentaje}]},
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
                af.moneda_id                                                  AS moneda_id,
                ma.tipo                                                       AS tipo,
                ma.fecha                                                      AS fecha,
                ma.cantidad                                                   AS cantidad,
                ma.precio_unitario_minor                                     AS precio_unitario_minor,
                ma.comision_minor                                            AS comision_minor,
                ma.monto_total_minor                                         AS monto_total_minor,
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
    # REPARTO DEL ACTIVO ENTRE OBJETIVOS (activo_objetivos)
    # ----------------------------------------------------------

    def assign_objetivo(self, activo_id: str, objetivo_id: str, porcentaje: float) -> SavingsResult:
        """
        Asigna `porcentaje` del activo al objetivo; si ese objetivo ya
        estaba asignado, le cambia el porcentaje. Afecta a los movimientos
        que se registren DESPUÉS (sus asignaciones salen de este reparto);
        los anteriores conservan las suyas.

        Raises:
            ActivoNotFoundError / ObjetivoNotFoundError si no existen.
            AsignacionInvalidaError si el porcentaje no está entre 0
                (exclusivo) y 100, o si los objetivos del activo sumarían
                más de 100 (no se escribe nada).
        """
        self._get_activo(activo_id)
        objetivo = self._get_objetivo(objetivo_id)
        if not isinstance(porcentaje, (int, float)) or isinstance(porcentaje, bool) or not 0 < porcentaje <= 100:
            raise AsignacionInvalidaError(f"EL PORCENTAJE TIENE QUE ESTAR ENTRE 0 Y 100 (RECIBIDO: {porcentaje!r}).")

        existente = self._activo_objetivos_repo.obtener(activo_id, objetivo_id)
        conn = self._db.conn
        with self._db.transaction():
            if existente is not None:
                self._activo_objetivos_repo.actualizar_porcentaje(existente["id"], float(porcentaje), conn=conn)
                reparto_id = existente["id"]
            else:
                reparto_id = self._activo_objetivos_repo.crear(activo_id, objetivo_id, float(porcentaje), conn=conn)
            # Misma conexión: ve lo recién escrito. Si se pasa de 100, la excepción hace rollback.
            if not self._activo_objetivos_repo.validar_porcentajes(activo_id):
                raise AsignacionInvalidaError("LOS OBJETIVOS DE ESTE ACTIVO SUMARÍAN MÁS DE 100%.")
        return SavingsResult(
            success=True,
            entity_id=reparto_id,
            data={"activo_id": activo_id, "objetivo_id": objetivo_id, "porcentaje": float(porcentaje)},
            message=f"{objetivo['nombre'].upper()}: {porcentaje:g}% DEL ACTIVO.",
        )

    def remove_objetivo(self, activo_id: str, objetivo_id: str) -> SavingsResult:
        """
        Saca al objetivo del reparto del activo (los movimientos ya
        registrados conservan sus asignaciones).

        Raises:
            SavingsError si ese objetivo no estaba asignado al activo.
        """
        existente = self._activo_objetivos_repo.obtener(activo_id, objetivo_id)
        if existente is None:
            raise SavingsError("ESE OBJETIVO NO ESTÁ ASIGNADO A ESTE ACTIVO.")
        self._activo_objetivos_repo.eliminar(existente["id"])
        return SavingsResult(
            success=True, entity_id=existente["id"],
            message=f"{existente['objetivo_nombre'].upper()} YA NO ESTÁ EN {existente['activo_nombre'].upper()}.",
        )

    def _reparto(self, activo_id: str, monto_minor: int) -> list[tuple[str, float, int]]:
        """
        (objetivo_id, porcentaje, monto) de un movimiento según el reparto
        vigente del activo. Cada monto se redondea hacia abajo; si los
        porcentajes suman 100, el resto del redondeo va al objetivo de
        mayor monto (así la suma da exacta), y si suman menos, lo que falta
        queda sin asignar. Montos en 0 no generan asignación.
        """
        repartos = self._activo_objetivos_repo.listar_por_activo(activo_id)
        filas = [
            [r["objetivo_id"], r["porcentaje"], math.floor(monto_minor * r["porcentaje"] / 100)] for r in repartos
        ]
        if filas and abs(sum(r["porcentaje"] for r in repartos) - 100) <= TOLERANCIA_PORCENTAJE:
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
        Lo común a registrar_*(): movimiento + asignaciones según el reparto
        vigente del activo (_reparto()) + transacción del Registro, todo
        atómico. La transacción: la vinculada si se pasa transaccion_id (ya
        validada por el caller); si no, y hay tipo_transaccion, la que crea
        _crear_transaccion_vinculada() (None si el activo no tiene cuenta).
        """
        if monto_total_minor <= 0:
            raise SavingsError(f"EL MONTO DEL MOVIMIENTO TIENE QUE SER MAYOR A 0 (QUEDÓ EN {monto_total_minor}).")
        reparto = self._reparto(activo["id"], monto_total_minor)
        conn = self._db.conn
        try:
            with self._db.transaction():
                if transaccion_id is None and tipo_transaccion is not None:
                    transaccion_id = self._crear_transaccion_vinculada(
                        activo, fecha, monto_total_minor, tipo_transaccion, concepto_transaccion, notas, conn,
                    )
                movimiento_id = self._movimientos_repo.crear(
                    activo_id=activo["id"], tipo=tipo, fecha=fecha, monto_total_minor=monto_total_minor,
                    cantidad=cantidad, precio_unitario_minor=precio_unitario_minor,
                    dolar_oficial_momento_minor=dolar_oficial_momento_minor, notas=notas,
                    transaccion_id=transaccion_id, conn=conn, comision_minor=comision_minor,
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
        precio_unitario_minor: int,
        comision_minor: int,
        fecha: str,
        transaccion_id: Optional[str] = None,
        dolar_momento_minor: Optional[int] = None,
    ) -> SavingsResult:
        """
        Compra de unidades (acciones, CEDEARs): monto_total = cantidad ×
        precio + comisión (lo que sale de la cuenta). En los tipos por
        unidades (TIPOS_POR_UNIDADES) la cantidad tiene que ser entera.
        dolar_momento_minor va a dolar_oficial_momento_minor (la columna ya
        existía con ese nombre).

        Raises:
            ActivoNotFoundError si el activo no existe.
            SavingsError si cantidad/precio no son > 0, la comisión es
                negativa, la fecha no es AAAA-MM-DD o la transacción a
                vincular no existe o ya está vinculada.
        """
        activo = self._get_activo(activo_id)
        self._positivo(cantidad, "LA CANTIDAD")
        if activo["tipo"] in TIPOS_POR_UNIDADES and not float(cantidad).is_integer():
            raise SavingsError("LA CANTIDAD DE ACCIONES / CEDEARS TIENE QUE SER ENTERA.")
        self._positivo(precio_unitario_minor, "EL PRECIO UNITARIO")
        comision_minor = self._comision(comision_minor)
        self._fecha(fecha)
        if transaccion_id is not None:
            self._validar_transaccion_para_vincular(transaccion_id)
        return self._registrar_movimiento(
            activo, "compra", "COMPRA", fecha, round(cantidad * precio_unitario_minor) + comision_minor,
            cantidad=cantidad, precio_unitario_minor=precio_unitario_minor, comision_minor=comision_minor,
            dolar_oficial_momento_minor=dolar_momento_minor, transaccion_id=transaccion_id,
            tipo_transaccion="egreso", concepto_transaccion=f"Compra — {activo['nombre']}",
        )

    def registrar_venta(
        self,
        activo_id: str,
        cantidad: float,
        precio_unitario_minor: int,
        comision_minor: int,
        fecha: str,
        transaccion_id: Optional[str] = None,
    ) -> SavingsResult:
        """
        Venta de unidades: monto_total = cantidad × precio − comisión (lo
        que entra a la cuenta). Se reparte entre los objetivos según el
        reparto vigente del activo (decisión del usuario: el pedido no le
        pone objetivo a la venta).

        Raises:
            ActivoNotFoundError si el activo no existe.
            SavingsError si cantidad/precio no son > 0, la comisión es
                negativa o se come todo el monto, se vende más de lo que
                hay, la fecha no es AAAA-MM-DD o la transacción a vincular
                no existe o ya está vinculada.
        """
        activo = self._get_activo(activo_id)
        self._positivo(cantidad, "LA CANTIDAD")
        if activo["tipo"] in TIPOS_POR_UNIDADES and not float(cantidad).is_integer():
            raise SavingsError("LA CANTIDAD DE ACCIONES / CEDEARS TIENE QUE SER ENTERA.")
        self._positivo(precio_unitario_minor, "EL PRECIO UNITARIO")
        comision_minor = self._comision(comision_minor)
        self._fecha(fecha)
        disponibles = self._estadisticas_por_activo(activo_id).get(activo_id, {}).get("unidades") or 0
        if cantidad > disponibles:
            raise SavingsError(f"NO PODÉS VENDER {cantidad:g}: HAY {disponibles:g}.")
        if transaccion_id is not None:
            self._validar_transaccion_para_vincular(transaccion_id)
        return self._registrar_movimiento(
            activo, "venta", "VENTA", fecha, round(cantidad * precio_unitario_minor) - comision_minor,
            cantidad=cantidad, precio_unitario_minor=precio_unitario_minor, comision_minor=comision_minor,
            transaccion_id=transaccion_id, tipo_transaccion="ingreso", concepto_transaccion=f"Venta — {activo['nombre']}",
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
    ) -> SavingsResult:
        """
        Aporte de plata (FCI, plazo fijo / flex: tipo='aporte', sin
        unidades). No estaba en el pedido: es el movimiento de esos tipos
        en el diálogo. monto_total = monto (lo que queda invertido); la
        comisión se guarda aparte, informativa.

        Raises:
            ActivoNotFoundError si el activo no existe.
            SavingsError si el monto no es > 0, la comisión es negativa,
                la fecha no es AAAA-MM-DD o la transacción a vincular no
                existe o ya está vinculada.
        """
        activo = self._get_activo(activo_id)
        self._positivo(monto_minor, "EL MONTO")
        comision_minor = self._comision(comision_minor)
        self._fecha(fecha)
        if transaccion_id is not None:
            self._validar_transaccion_para_vincular(transaccion_id)
        return self._registrar_movimiento(
            activo, "aporte", "APORTE", fecha, int(monto_minor), comision_minor=comision_minor,
            dolar_oficial_momento_minor=dolar_momento_minor, notas=notas, transaccion_id=transaccion_id,
            tipo_transaccion="egreso", concepto_transaccion=f"Aporte a ahorro — {activo['nombre']}",
        )

    def registrar_retiro(
        self,
        activo_id: str,
        monto_minor: int,
        fecha: str,
        comision_minor: int = 0,
        transaccion_id: Optional[str] = None,
        notas: Optional[str] = None,
    ) -> SavingsResult:
        """
        Retiro de plata de un activo por monto (FCI, plazos): tipo='venta'
        sin unidades, repartido según el reparto vigente. No estaba en el
        pedido (ver registrar_aporte()). No puede superar el saldo.

        Raises:
            ActivoNotFoundError si el activo no existe.
            SavingsError si el monto no es > 0 o supera el saldo, la
                comisión es negativa, la fecha no es AAAA-MM-DD o la
                transacción a vincular no existe o ya está vinculada.
        """
        activo = self._get_activo(activo_id)
        self._positivo(monto_minor, "EL MONTO")
        comision_minor = self._comision(comision_minor)
        self._fecha(fecha)
        saldo = self._estadisticas_por_activo(activo_id).get(activo_id, {}).get("saldo_minor") or 0
        if monto_minor > saldo:
            raise SavingsError("EL RETIRO SUPERA EL SALDO DEL ACTIVO.")
        if transaccion_id is not None:
            self._validar_transaccion_para_vincular(transaccion_id)
        return self._registrar_movimiento(
            activo, "venta", "RETIRO", fecha, int(monto_minor), comision_minor=comision_minor, notas=notas,
            transaccion_id=transaccion_id, tipo_transaccion="ingreso",
            concepto_transaccion=f"Retiro de ahorro — {activo['nombre']}",
        )

    def registrar_rendimiento(
        self, activo_id: str, monto_minor: int, fecha: str, notas: Optional[str] = None,
    ) -> SavingsResult:
        """
        Rendimiento (intereses, dividendos), repartido entre los objetivos
        según el reparto vigente del activo (activo_objetivos) — a
        diferencia de register_return(), que reparte según las compras
        anteriores. Sin transacción del Registro (mismo criterio que
        register_return(), docs/DATA_MODEL_DECISIONS.md sección 16).

        Raises:
            ActivoNotFoundError si el activo no existe.
            SavingsError si el monto no es > 0 o la fecha no es AAAA-MM-DD.
        """
        activo = self._get_activo(activo_id)
        self._positivo(monto_minor, "EL MONTO")
        self._fecha(fecha)
        return self._registrar_movimiento(activo, "rendimiento", "RENDIMIENTO", fecha, int(monto_minor), notas=notas)

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

    def _estadisticas_por_activo(self, activo_id: Optional[str] = None) -> dict[str, dict]:
        """
        activo_id → {saldo_minor, unidades, costo_minor, unidades_costeadas}
        con todos sus movimientos: saldo = entradas − ventas; unidades =
        compras/aportes − ventas (None si nunca se cargó una cantidad);
        costo/unidades_costeadas = las compras con cantidad Y precio (para
        el precio promedio, sin comisiones). Agregación de reporte, mismo
        criterio que get_balance_por_activo().
        """
        where, params = ("WHERE activo_id = ?", (activo_id,)) if activo_id is not None else ("", ())
        filas = self._db.fetchall(
            f"""
            SELECT
                activo_id,
                SUM(CASE WHEN tipo IN ('compra', 'aporte', 'rendimiento') THEN monto_total_minor
                         WHEN tipo = 'venta' THEN -monto_total_minor ELSE 0 END)       AS saldo_minor,
                SUM(CASE WHEN tipo IN ('compra', 'aporte') THEN cantidad
                         WHEN tipo = 'venta' THEN -cantidad END)                       AS unidades,
                SUM(CASE WHEN tipo = 'compra' AND cantidad IS NOT NULL AND precio_unitario_minor IS NOT NULL
                         THEN cantidad * precio_unitario_minor END)                   AS costo_minor,
                SUM(CASE WHEN tipo = 'compra' AND cantidad IS NOT NULL AND precio_unitario_minor IS NOT NULL
                         THEN cantidad END)                                            AS unidades_costeadas
            FROM movimientos_activo
            {where}
            GROUP BY activo_id;
            """,
            params,
        )
        return {fila["activo_id"]: dict(fila) for fila in filas}

    def get_resumen_por_tipo(self) -> dict:
        """
        Los activos activos agrupados por tipo (en el orden de TIPOS_ACTIVO;
        solo los tipos que tienen alguno), cada grupo por broker y nombre:
        {tipo: [{activo_id, activo, tipo, broker_id, broker (nombre o None),
        cuenta_id, moneda_id, moneda (código), simbolo, decimales,
        por_unidades, cantidad, unidades, saldo_minor,
        precio_promedio_minor, comision_compra_minor, comision_venta_minor,
        objetivos: [{objetivo_id, nombre, porcentaje}]}]}.

        cantidad: unidades en acciones / CEDEARs (TIPOS_POR_UNIDADES); en el
        resto, el saldo en la moneda del activo (float: 285432.50).
        precio_promedio_minor: de las compras con cantidad y precio, o None.
        """
        estadisticas = self._estadisticas_por_activo()
        brokers = {b["id"]: b for b in self._brokers_repo.listar(solo_activos=False)}
        monedas = {m["id"]: m for m in self._db.obtener_monedas()}
        repartos: dict[str, list[dict]] = {}
        for r in self._activo_objetivos_repo.listar():
            repartos.setdefault(r["activo_id"], []).append(
                {"objetivo_id": r["objetivo_id"], "nombre": r["objetivo_nombre"], "porcentaje": r["porcentaje"]}
            )

        grupos: dict[str, list[dict]] = {}
        for activo in self._activos_repo.listar():
            stats = estadisticas.get(activo["id"], {})
            moneda = monedas.get(activo["moneda_id"])
            decimales = moneda["decimales"] if moneda else 2
            broker = brokers.get(activo["broker_id"]) if activo["broker_id"] else None
            saldo = stats.get("saldo_minor") or 0
            unidades = stats.get("unidades")
            costeadas = stats.get("unidades_costeadas")
            por_unidades = activo["tipo"] in TIPOS_POR_UNIDADES
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
                "precio_promedio_minor": round(stats["costo_minor"] / costeadas) if costeadas else None,
                "comision_compra_minor": activo["comision_compra_minor"] or 0,
                "comision_venta_minor": activo["comision_venta_minor"] or 0,
                "objetivos": repartos.get(activo["id"], []),
            })

        orden = {tipo: indice for indice, tipo in enumerate(TIPOS_ACTIVO)}
        return {
            tipo: sorted(grupos[tipo], key=lambda e: ((e["broker"] or "").upper(), e["activo"].upper()))
            for tipo in sorted(grupos, key=lambda t: orden.get(t, len(orden)))
        }

    @staticmethod
    def _parte_de_activo(entrada: dict, porcentaje: float, objetivo_id: Optional[str]) -> dict:
        """La parte `porcentaje` de una entrada de get_resumen_por_tipo() (para get_resumen_por_objetivo())."""
        unidades = entrada["unidades"] * porcentaje / 100 if entrada["unidades"] is not None else None
        saldo = round(entrada["saldo_minor"] * porcentaje / 100)
        return {
            "objetivo_id": objetivo_id,
            "tipo": entrada["tipo"],
            "activo": entrada["activo"],
            "activo_id": entrada["activo_id"],
            "broker": entrada["broker"],
            "porcentaje": porcentaje,
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
        Lo de cada objetivo según el reparto vigente de los activos (saldo /
        unidades del activo × porcentaje): {objetivo (nombre): [{objetivo_id,
        tipo, activo, activo_id, broker, porcentaje, por_unidades, cantidad,
        unidades, saldo_minor, moneda, simbolo, decimales}]}, por nombre de
        objetivo. Al final, OBJETIVO_SIN_ASIGNAR con la parte no repartida
        de los activos que tienen algo (no estaba en el pedido: sin esto,
        esa plata no aparecería en esta vista).
        """
        por_objetivo: dict[str, list[dict]] = {}
        for entradas in self.get_resumen_por_tipo().values():
            for entrada in entradas:
                asignado = 0.0
                for objetivo in entrada["objetivos"]:
                    por_objetivo.setdefault(objetivo["nombre"], []).append(
                        self._parte_de_activo(entrada, objetivo["porcentaje"], objetivo["objetivo_id"])
                    )
                    asignado += objetivo["porcentaje"]
                libre = 100 - asignado
                if libre > TOLERANCIA_PORCENTAJE and (entrada["saldo_minor"] or entrada["unidades"]):
                    por_objetivo.setdefault(OBJETIVO_SIN_ASIGNAR, []).append(self._parte_de_activo(entrada, libre, None))
        claves = sorted((clave for clave in por_objetivo if clave != OBJETIVO_SIN_ASIGNAR), key=str.upper)
        if OBJETIVO_SIN_ASIGNAR in por_objetivo:
            claves.append(OBJETIVO_SIN_ASIGNAR)
        return {clave: por_objetivo[clave] for clave in claves}
