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
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from db.database import DatabaseManager
from repositories.activos_financieros_repository import ActivosFinancierosRepository
from repositories.objetivos_ahorro_repository import ObjetivosAhorroRepository
from repositories.movimientos_activo_repository import MovimientosActivoRepository
from repositories.asignaciones_repository import AsignacionesRepository

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


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class SavingsResult:
    """Structured result returned by SavingsService operations."""
    success:   bool
    entity_id: Optional[int] = None
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

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_activo(self, activo_id: int) -> sqlite3.Row:
        row = self._activos_repo.obtener_por_id(activo_id)
        if row is None:
            raise ActivoNotFoundError(f"Activo financiero id={activo_id} not found.")
        return row

    def _get_objetivo(self, objetivo_id: int) -> sqlite3.Row:
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

    # ----------------------------------------------------------
    # ACTIVOS FINANCIEROS
    # ----------------------------------------------------------

    def create_activo(self, nombre: str, tipo: str, moneda_id: int) -> SavingsResult:
        """Raises: SavingsError si moneda_id no existe."""
        self._get_currency(moneda_id)  # validate existence
        activo_id = self._activos_repo.crear(nombre=nombre, tipo=tipo, moneda_id=moneda_id)
        return SavingsResult(
            success=True,
            entity_id=activo_id,
            data={"nombre": nombre, "tipo": tipo, "moneda_id": moneda_id},
            message=f"Activo financiero '{nombre}' created.",
        )

    def list_activos(self, tipo: Optional[str] = None) -> list[sqlite3.Row]:
        return self._activos_repo.listar(tipo=tipo)

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
        activo_id: int,
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

        Raises:
            ActivoNotFoundError si activo_id no existe.
            ValueError si monto_total_minor <= 0.
            ObjetivoNotFoundError si algún objetivo_id de `asignaciones` no existe.
            AsignacionInvalidaError si la suma de porcentaje supera 100.
        """
        self._get_activo(activo_id)
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
            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="compra", fecha=fecha,
                monto_total_minor=monto_total_minor, cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                dolar_oficial_momento_minor=dolar_oficial_momento_minor,
                notas=notas, conn=conn,
            )
            asignaciones_creadas = []
            for asignacion in asignaciones:
                monto_asignado_minor = round(monto_total_minor * asignacion["porcentaje"] / 100)
                asignacion_id = self._asignaciones_repo.crear(
                    movimiento_id=movimiento_id,
                    objetivo_id=asignacion["objetivo_id"],
                    porcentaje=asignacion["porcentaje"],
                    monto_asignado_minor=monto_asignado_minor,
                    conn=conn,
                )
                asignaciones_creadas.append({
                    "asignacion_id": asignacion_id,
                    "objetivo_id": asignacion["objetivo_id"],
                    "porcentaje": asignacion["porcentaje"],
                    "monto_asignado_minor": monto_asignado_minor,
                })

        return SavingsResult(
            success=True,
            entity_id=movimiento_id,
            data={"asignaciones": asignaciones_creadas, "asignado": bool(asignaciones_creadas)},
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
        activo_id: int,
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

        compras = self._movimientos_repo.listar_por_tipo(activo_id, "compra")
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
        activo_id: int,
        fecha: str,
        monto_total_minor: int,
        objetivo_id: int,
        cantidad: Optional[float] = None,
        precio_unitario_minor: Optional[int] = None,
        dolar_oficial_momento_minor: Optional[int] = None,
        notas: Optional[str] = None,
    ) -> SavingsResult:
        """
        Registra una venta/retiro (movimiento tipo='venta') de activo_id,
        asignada EXPLÍCITAMENTE a un único objetivo_id (porcentaje=100) —
        a diferencia de register_return(), acá el caller elige a mano de
        qué "sobre" sale la plata, no se prorratea automático.

        Raises:
            ActivoNotFoundError si activo_id no existe.
            ObjetivoNotFoundError si objetivo_id no existe.
            ValueError si monto_total_minor <= 0.
        """
        self._get_activo(activo_id)
        self._get_objetivo(objetivo_id)
        if monto_total_minor <= 0:
            raise ValueError(f"monto_total_minor must be positive. Received: {monto_total_minor}.")

        conn = self._db.conn
        with self._db.transaction():
            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="venta", fecha=fecha,
                monto_total_minor=monto_total_minor, cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                dolar_oficial_momento_minor=dolar_oficial_momento_minor,
                notas=notas, conn=conn,
            )
            asignacion_id = self._asignaciones_repo.crear(
                movimiento_id=movimiento_id, objetivo_id=objetivo_id,
                porcentaje=100.0, monto_asignado_minor=monto_total_minor,
                conn=conn,
            )

        return SavingsResult(
            success=True,
            entity_id=movimiento_id,
            data={
                "asignacion_id": asignacion_id,
                "objetivo_id": objetivo_id,
                "monto_asignado_minor": monto_total_minor,
            },
            message=(
                f"Venta de {monto_total_minor} registrada para activo_id={activo_id}, "
                f"asignada 100% a objetivo_id={objetivo_id}."
            ),
        )

    # ----------------------------------------------------------
    # OBJETIVO BALANCE (read-only aggregation)
    # ----------------------------------------------------------

    def get_objetivo_balance(self, objetivo_id: int) -> dict:
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
            if movimiento["tipo"] == "compra":
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
