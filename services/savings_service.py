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
    - register_sale() cambia de firma: el parámetro `objetivo_id: int`
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
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from db.database import DatabaseManager
from repositories.activos_financieros_repository import ActivosFinancierosRepository
from repositories.objetivos_ahorro_repository import ObjetivosAhorroRepository
from repositories.movimientos_activo_repository import MovimientosActivoRepository
from repositories.asignaciones_repository import AsignacionesRepository
from repositories.transacciones_repository import TransaccionesRepository
from repositories.cuentas_repository import CuentasRepository

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
        self._transacciones_repo = TransaccionesRepository(db)
        self._cuentas_repo = CuentasRepository(db)

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

    def _get_account(self, account_id: int) -> sqlite3.Row:
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

    # ----------------------------------------------------------
    # ACTIVOS FINANCIEROS
    # ----------------------------------------------------------

    def create_activo(
        self, nombre: str, tipo: str, moneda_id: int, cuenta_id: Optional[int] = None,
    ) -> SavingsResult:
        """
        cuenta_id (Tarea 6g, docs/PROXIMOS_PASOS.md): vincula el activo a
        una cuenta real, opcional. register_purchase()/register_sale() la
        usan después para resolver sola qué cuenta descontar/acreditar,
        sin que el caller tenga que pasarla en cada movimiento — un activo
        sin cuenta_id sigue generando movimientos puramente informales
        (transaccion_id NULL), igual que antes de esta tarea.

        Raises:
            SavingsError si moneda_id no existe.
            AccountNotFoundError si cuenta_id se pasa y no existe.
        """
        self._get_currency(moneda_id)  # validate existence
        if cuenta_id is not None:
            self._get_account(cuenta_id)  # validate existence
        activo_id = self._activos_repo.crear(nombre=nombre, tipo=tipo, moneda_id=moneda_id, cuenta_id=cuenta_id)
        return SavingsResult(
            success=True,
            entity_id=activo_id,
            data={"nombre": nombre, "tipo": tipo, "moneda_id": moneda_id, "cuenta_id": cuenta_id},
            message=f"Activo financiero '{nombre}' created.",
        )

    def list_activos(self, tipo: Optional[str] = None) -> list[sqlite3.Row]:
        return self._activos_repo.listar(tipo=tipo)

    def get_or_create_reserved_cash_asset(self, cuenta_id: int, moneda_id: int) -> SavingsResult:
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

        cuenta_id = activo["cuenta_id"]
        categoria_id = self._get_categoria_ahorro_inversion_id() if cuenta_id is not None else None

        conn = self._db.conn
        with self._db.transaction():
            transaccion_id = None
            if cuenta_id is not None:
                transaccion_id = self._transacciones_repo.crear(
                    fecha=fecha,
                    concepto=f"Aporte a ahorro — {activo['nombre']}",
                    cuenta_id=cuenta_id,
                    categoria_id=categoria_id,
                    moneda_id=activo["moneda_id"],
                    tipo_movimiento="egreso",
                    monto_minor=monto_total_minor,
                    notas=notas,
                    conn=conn,
                )

            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="compra", fecha=fecha,
                monto_total_minor=monto_total_minor, cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                dolar_oficial_momento_minor=dolar_oficial_momento_minor,
                notas=notas, transaccion_id=transaccion_id, conn=conn,
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

        cuenta_id = activo["cuenta_id"]
        categoria_id = self._get_categoria_ahorro_inversion_id() if cuenta_id is not None else None

        conn = self._db.conn
        with self._db.transaction():
            transaccion_id = None
            if cuenta_id is not None:
                transaccion_id = self._transacciones_repo.crear(
                    fecha=fecha,
                    concepto=f"Retiro de ahorro — {activo['nombre']}",
                    cuenta_id=cuenta_id,
                    categoria_id=categoria_id,
                    moneda_id=activo["moneda_id"],
                    tipo_movimiento="ingreso",
                    monto_minor=monto_total_minor,
                    notas=notas,
                    conn=conn,
                )

            movimiento_id = self._movimientos_repo.crear(
                activo_id=activo_id, tipo="venta", fecha=fecha,
                monto_total_minor=monto_total_minor, cantidad=cantidad,
                precio_unitario_minor=precio_unitario_minor,
                dolar_oficial_momento_minor=dolar_oficial_momento_minor,
                notas=notas, transaccion_id=transaccion_id, conn=conn,
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

    # ----------------------------------------------------------
    # BALANCE POR CUENTA (read-only aggregation, Tarea 6b)
    # ----------------------------------------------------------

    def get_balance_por_cuenta(self, objetivo_id: int) -> list[dict]:
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
                SUM(CASE WHEN ma.tipo = 'compra'
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
                        WHEN ma.tipo IN ('compra', 'rendimiento') THEN ma.monto_total_minor
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
                        WHEN ma.tipo = 'compra' THEN ma.cantidad
                        WHEN ma.tipo = 'venta' THEN -ma.cantidad
                        ELSE NULL
                    END)                                                      AS cantidad_neta,
                SUM(CASE
                        WHEN ma.tipo IN ('compra', 'rendimiento') THEN ma.monto_total_minor
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
        objetivo_id: Optional[int] = None,
        tipo_activo: Optional[str] = None,
        tipo_movimiento: Optional[str] = None,
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
                ma.monto_total_minor                                         AS monto_total_minor,
                ma.transaccion_id                                            AS transaccion_id
            FROM movimientos_activo ma
            JOIN activos_financieros af ON af.id = ma.activo_id
            {where_sql}
            ORDER BY ma.fecha DESC, ma.id DESC;
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
        movimiento_id: int,
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
