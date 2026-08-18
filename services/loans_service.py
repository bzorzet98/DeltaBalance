"""
DeltaBalance — services/loans_service.py

Purpose:
    Domain service for hard loans (prestamos: hipotecario/prendario/
    personal/otro) and their amortization schedule (cuotas_prestamo).
    Data access lives entirely in PrestamosRepository y
    CuotasPrestamoRepository.

    Fase 2, bloque PRESTAMOS, paso 2: este service se crea DESDE CERO. No
    existe ningún LoansService previo — el diseño sale de los dos
    repositorios (paso 1 + paso 1b) y del schema (ver comentario arriba de
    CREATE TABLE cuotas_prestamo en db/schema.sql: "el cálculo de
    amortización... es responsabilidad de la capa de servicio").

    CONVENCIÓN DE TASA (aplica a TODO el cálculo de este módulo):
        tasa_mensual = tasa_anual_bp / 12 / 10000
    Es la tasa NOMINAL anual simple dividida entre 12 — NO tasa efectiva
    anual compuesta. _generar_tabla_amortizacion() es un PUNTO DE PARTIDA
    ESTIMADO: no persigue un cierre exacto contra lo que el banco cobra
    realmente. El ajuste cuota a cuota vive en adjust_installment(), que
    el usuario usa para reconciliar cada cuota contra su resumen real,
    mes a mes — el sistema nunca fuerza ese cierre por su cuenta (ver
    Fase 2, PRESTAMOS paso 1b: CuotasPrestamoRepository.actualizar()).

    moneda_id/cuenta_debito_id no tienen su propia excepción en la lista
    pedida para este service (a diferencia de otros services de esta fase
    que sí definen CurrencyNotFoundError/AccountNotFoundError propias) —
    se validan igual, pero levantan la excepción base LoansError
    directamente en vez de una subclase dedicada, respetando la lista de
    excepciones cerrada que se especificó para este paso (mismo criterio
    ya aplicado en SharedExpensesService con categoria_id).
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from db.database import DatabaseManager
from repositories.prestamos_repository import PrestamosRepository
from repositories.cuotas_prestamo_repository import CuotasPrestamoRepository
from repositories.cuentas_repository import CuentasRepository
from repositories._sentinels import NO_CAMBIAR

# =============================================================
# EXCEPTIONS
# =============================================================

class LoansError(Exception):
    """Raised when a loan operation violates a business rule."""


class PrestamoNotFoundError(LoansError):
    """Raised when a referenced loan does not exist."""


class CuotaPrestamoNotFoundError(LoansError):
    """Raised when a referenced loan installment does not exist."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class LoansResult:
    """Structured result returned by LoansService operations."""
    success:   bool
    entity_id: Optional[int] = None
    data:      dict          = field(default_factory=dict)
    message:   str           = ""


# =============================================================
# SERVICE
# =============================================================

class LoansService:
    """
    Entry point for hard-loan operations (create with full amortization
    schedule, read, adjust individual installments, mark paid, cancel).

    Usage:
        db  = DatabaseManager()
        svc = LoansService(db)

        svc.create_loan(
            entidad="Banco Galicia", tipo="hipotecario",
            capital_original_minor=1200000, tasa_anual_bp=1200,
            sistema_amortizacion="frances", moneda_id=1,
            fecha_inicio="2026-01-01", plazo_meses=12,
        )
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._prestamos_repo = PrestamosRepository(db)
        self._cuotas_repo = CuotasPrestamoRepository(db)
        self._cuentas_repo = CuentasRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_loan(self, prestamo_id: int) -> sqlite3.Row:
        row = self._prestamos_repo.obtener_por_id(prestamo_id)
        if row is None:
            raise PrestamoNotFoundError(f"Préstamo id={prestamo_id} not found.")
        return row

    def _get_currency(self, currency_id: int) -> sqlite3.Row:
        """No MonedasRepository exists yet in this codebase — mismo patrón que en los demás services."""
        row = self._db.fetchone("SELECT * FROM monedas WHERE id = ?;", (currency_id,))
        if row is None:
            raise LoansError(f"Currency id={currency_id} not found.")
        return row

    def _get_account(self, account_id: int) -> sqlite3.Row:
        row = self._cuentas_repo.obtener_por_id(account_id)
        if row is None:
            raise LoansError(f"Account id={account_id} not found.")
        return row

    # ----------------------------------------------------------
    # AMORTIZATION (cálculo puro, no escribe nada)
    # ----------------------------------------------------------

    def _generar_tabla_amortizacion(
        self,
        capital_original_minor: int,
        tasa_anual_bp: int,
        plazo_meses: int,
        sistema_amortizacion: str,
    ) -> list[dict]:
        """
        Calcula la tabla de amortización completa — CÁLCULO PURO, no
        escribe nada en la base. Ver docstring del módulo para la
        convención de tasa (nominal anual simple / 12, no compuesta) y el
        criterio de "punto de partida estimado".

        Devuelve una lista de dicts con las claves que
        CuotasPrestamoRepository.crear_lote() espera: numero_cuota,
        monto_capital_minor, monto_interes_minor, monto_total_minor.

        Raises:
            ValueError si sistema_amortizacion no es 'frances' ni 'aleman'.
        """
        i = tasa_anual_bp / 12 / 10000
        n = plazo_meses
        tabla: list[dict] = []

        if sistema_amortizacion == "frances":
            # Cuota TOTAL fija en las n cuotas. Fórmula estándar de
            # amortización francesa:
            #     cuota = capital * (i * (1+i)^n) / ((1+i)^n - 1)
            # interés de cada cuota = saldo_pendiente * i (decreciente);
            # capital de cada cuota = cuota_total - interés (creciente).
            # Redondeo simple: cuota_total se redondea UNA sola vez y se
            # reutiliza igual en las n filas (así queda genuinamente fija,
            # no "aproximadamente fija" por redondeos independientes); el
            # interés se redondea por cuota y el capital sale como resto
            # (cuota_total - interés), así capital+interés siempre suman
            # exactamente el total de esa fila. Sin lógica de "resto en la
            # última cuota" — el usuario reconcilia a mano después.
            #
            # CASO BORDE — tasa_anual_bp=0 (i=0): la fórmula estándar
            # divide por ((1+i)^n - 1) = 0, indeterminado. Un préstamo sin
            # interés es simplemente capital repartido en partes iguales.
            if i == 0:
                cuota_total_minor = round(capital_original_minor / n)
            else:
                factor = (1 + i) ** n
                cuota_total = capital_original_minor * (i * factor) / (factor - 1)
                cuota_total_minor = round(cuota_total)

            saldo_pendiente = capital_original_minor
            for numero_cuota in range(1, n + 1):
                interes_minor = round(saldo_pendiente * i)
                capital_minor = cuota_total_minor - interes_minor
                saldo_pendiente -= capital_minor
                tabla.append({
                    "numero_cuota": numero_cuota,
                    "monto_capital_minor": capital_minor,
                    "monto_interes_minor": interes_minor,
                    "monto_total_minor": cuota_total_minor,
                })

        elif sistema_amortizacion == "aleman":
            # Capital FIJO en las n cuotas: capital_original_minor // n. El
            # resto de esa división entera se suma a la PRIMERA cuota, para
            # no perder centavos en el punto de partida (ajustable después
            # igual). Interés de cada cuota = saldo_pendiente * i
            # (decreciente) — no hay caso borde de división por cero acá
            # con tasa_anual_bp=0, a diferencia del sistema francés: si
            # i=0, el interés de cada cuota simplemente da 0 y el total de
            # cada cuota queda igual al capital fijo.
            capital_fijo_minor = capital_original_minor // n
            resto_minor = capital_original_minor - (capital_fijo_minor * n)

            saldo_pendiente = capital_original_minor
            for numero_cuota in range(1, n + 1):
                capital_minor = capital_fijo_minor + (resto_minor if numero_cuota == 1 else 0)
                interes_minor = round(saldo_pendiente * i)
                total_minor = capital_minor + interes_minor
                saldo_pendiente -= capital_minor
                tabla.append({
                    "numero_cuota": numero_cuota,
                    "monto_capital_minor": capital_minor,
                    "monto_interes_minor": interes_minor,
                    "monto_total_minor": total_minor,
                })

        else:
            raise ValueError(f"sistema_amortizacion inválido: {sistema_amortizacion!r}.")

        return tabla

    # ----------------------------------------------------------
    # CREATE LOAN
    # ----------------------------------------------------------

    def create_loan(
        self,
        entidad: str,
        tipo: str,
        capital_original_minor: int,
        tasa_anual_bp: int,
        sistema_amortizacion: str,
        moneda_id: int,
        fecha_inicio: str,
        plazo_meses: int,
        cuenta_debito_id: Optional[int] = None,
        notas: Optional[str] = None,
    ) -> LoansResult:
        """
        Calcula la tabla de amortización completa
        (_generar_tabla_amortizacion()) y crea el préstamo + el lote de
        cuotas, atómico en una única self._db.transaction().

        mes/anio de cada cuota se calculan a partir de fecha_inicio: la
        cuota 1 cae en el mes de fecha_inicio, cada cuota siguiente avanza
        un mes, con rollover de año (ej. fecha_inicio en noviembre, cuota 3
        cae en enero del año siguiente).

        Raises:
            ValueError si capital_original_minor <= 0, tasa_anual_bp < 0,
                       o plazo_meses <= 0.
            LoansError si moneda_id no existe, o si cuenta_debito_id se
                       pasa y no existe.
        """
        if capital_original_minor <= 0:
            raise ValueError(f"capital_original_minor must be positive. Received: {capital_original_minor}.")
        if tasa_anual_bp < 0:
            raise ValueError(f"tasa_anual_bp cannot be negative. Received: {tasa_anual_bp}.")
        if plazo_meses <= 0:
            raise ValueError(f"plazo_meses must be positive. Received: {plazo_meses}.")

        self._get_currency(moneda_id)  # validate existence
        if cuenta_debito_id is not None:
            self._get_account(cuenta_debito_id)  # validate existence

        tabla = self._generar_tabla_amortizacion(
            capital_original_minor, tasa_anual_bp, plazo_meses, sistema_amortizacion,
        )

        anio_inicio, mes_inicio = (int(parte) for parte in fecha_inicio.split("-")[:2])
        cuotas = []
        for fila in tabla:
            offset = fila["numero_cuota"] - 1
            total_meses = (mes_inicio - 1) + offset
            mes = (total_meses % 12) + 1
            anio = anio_inicio + (total_meses // 12)
            cuotas.append({**fila, "mes": mes, "anio": anio})

        conn = self._db.conn
        with self._db.transaction():
            prestamo_id = self._prestamos_repo.crear(
                entidad=entidad, tipo=tipo, capital_original_minor=capital_original_minor,
                tasa_anual_bp=tasa_anual_bp, sistema_amortizacion=sistema_amortizacion,
                moneda_id=moneda_id, fecha_inicio=fecha_inicio, plazo_meses=plazo_meses,
                cuenta_debito_id=cuenta_debito_id, notas=notas, conn=conn,
            )
            cuotas_ids = self._cuotas_repo.crear_lote(prestamo_id=prestamo_id, cuotas=cuotas, conn=conn)

        return LoansResult(
            success=True,
            entity_id=prestamo_id,
            data={
                "entidad": entidad,
                "sistema_amortizacion": sistema_amortizacion,
                "cuotas_creadas": len(cuotas_ids),
            },
            message=f"Préstamo creado para '{entidad}' con {len(cuotas_ids)} cuotas ({sistema_amortizacion}).",
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def get_loan(self, prestamo_id: int) -> Optional[sqlite3.Row]:
        return self._prestamos_repo.obtener_por_id(prestamo_id)

    def list_loans(self, estado: Optional[str] = None, tipo: Optional[str] = None) -> list[sqlite3.Row]:
        return self._prestamos_repo.listar(estado=estado, tipo=tipo)

    def get_schedule(self, prestamo_id: int) -> list[sqlite3.Row]:
        return self._cuotas_repo.listar_por_prestamo(prestamo_id)

    # ----------------------------------------------------------
    # ADJUST INSTALLMENT
    # ----------------------------------------------------------

    def adjust_installment(
        self,
        cuota_id: int,
        monto_capital_minor: Optional[int] = None,
        monto_interes_minor: Optional[int] = None,
        monto_total_minor: Optional[int] = None,
    ) -> LoansResult:
        """
        Reconcilia una cuota individual ya generada contra lo que el banco
        realmente cobra. Al menos uno de los tres montos debe pasarse.

        Si se pasan capital e interés pero NO total, se calcula como la
        suma (conveniencia — no obliga al caller a sumar a mano). Si se
        pasa total explícito, se respeta tal cual (no se fuerza que
        capital+interes lo repliquen).

        Raises:
            CuotaPrestamoNotFoundError si cuota_id no existe.
            ValueError si los tres montos son None.
        """
        cuota = self._cuotas_repo.obtener_por_id(cuota_id)
        if cuota is None:
            raise CuotaPrestamoNotFoundError(f"Cuota de préstamo id={cuota_id} not found.")

        if monto_capital_minor is None and monto_interes_minor is None and monto_total_minor is None:
            raise ValueError(
                "At least one of monto_capital_minor/monto_interes_minor/monto_total_minor must be provided."
            )

        if (
            monto_total_minor is None
            and monto_capital_minor is not None
            and monto_interes_minor is not None
        ):
            monto_total_minor = monto_capital_minor + monto_interes_minor

        kwargs: dict = {}
        if monto_capital_minor is not None:
            kwargs["monto_capital_minor"] = monto_capital_minor
        if monto_interes_minor is not None:
            kwargs["monto_interes_minor"] = monto_interes_minor
        if monto_total_minor is not None:
            kwargs["monto_total_minor"] = monto_total_minor

        self._cuotas_repo.actualizar(cuota_id, **kwargs)

        return LoansResult(
            success=True,
            entity_id=cuota_id,
            data=kwargs,
            message=f"Cuota id={cuota_id} ajustada.",
        )

    # ----------------------------------------------------------
    # MARK INSTALLMENT PAID
    # ----------------------------------------------------------

    def mark_installment_paid(self, cuota_id: int, fecha_pago: str) -> LoansResult:
        """
        Raises:
            CuotaPrestamoNotFoundError si cuota_id no existe.
        """
        cuota = self._cuotas_repo.obtener_por_id(cuota_id)
        if cuota is None:
            raise CuotaPrestamoNotFoundError(f"Cuota de préstamo id={cuota_id} not found.")

        self._cuotas_repo.marcar_pagada(cuota_id, fecha_pago)

        return LoansResult(
            success=True,
            entity_id=cuota_id,
            data={"cuota_id": cuota_id, "fecha_pago": fecha_pago},
            message=f"Cuota id={cuota_id} marcada como pagada.",
        )

    # ----------------------------------------------------------
    # UPDATE LOAN
    # ----------------------------------------------------------

    def update_loan(
        self,
        prestamo_id: int,
        notas: Any = NO_CAMBIAR,
        cuenta_debito_id: Any = NO_CAMBIAR,
    ) -> LoansResult:
        """
        Raises:
            PrestamoNotFoundError si prestamo_id no existe.
            LoansError si cuenta_debito_id se pasa (distinto de NO_CAMBIAR
                       y de None) y no existe.
        """
        self._get_loan(prestamo_id)  # validate existence

        if cuenta_debito_id is not NO_CAMBIAR and cuenta_debito_id is not None:
            self._get_account(cuenta_debito_id)  # validate existence

        actualizado = self._prestamos_repo.actualizar(
            prestamo_id, notas=notas, cuenta_debito_id=cuenta_debito_id,
        )

        return LoansResult(
            success=True,
            entity_id=prestamo_id,
            data={"actualizado": actualizado},
            message=(
                f"Préstamo id={prestamo_id} actualizado."
                if actualizado else
                f"Préstamo id={prestamo_id}: no había campos para actualizar."
            ),
        )

    # ----------------------------------------------------------
    # CANCEL LOAN
    # ----------------------------------------------------------

    def cancel_loan(self, prestamo_id: int) -> LoansResult:
        """
        Marca el préstamo como 'cancelado'. NO borra las cuotas ya
        generadas — quedan como registro histórico, mismo criterio de
        trazabilidad que el resto del proyecto (ej. cuotas_credito
        'omitido' en vez de DELETE).

        Raises:
            PrestamoNotFoundError si prestamo_id no existe.
        """
        self._get_loan(prestamo_id)  # validate existence

        self._prestamos_repo.cambiar_estado(prestamo_id, "cancelado")

        return LoansResult(
            success=True,
            entity_id=prestamo_id,
            data={"estado": "cancelado"},
            message=f"Préstamo id={prestamo_id} cancelado. Las cuotas ya generadas quedan como registro histórico.",
        )
