"""
DeltaBalance — services/fees_service.py

Purpose:
    Manages all credit installment operations: purchases made in N installments
    (compras en cuotas), their automatically generated fee schedule (cuotas_credito),
    and the monthly credit card statement (resumenes_tarjeta) that groups fees
    into a single payable amount.

    Owns these tables exclusively:
        - compras_cuotas
        - cuotas_credito
        - resumenes_tarjeta
        - tarjetas_config (closing / due day of each credit card —
          docs/DATA_MODEL_DECISIONS.md section 23)

    Does NOT create transactions. The actual cash outflow (paying the credit card
    statement) is handled by TransactionService. FeesService only manages the
    credit-side tracking.

    Reads (never writes) gastos_compartidos, only to know whether a purchase
    is already shared before letting update_purchase() change its total —
    same read-only cross-domain pattern SharedExpensesService already uses
    over compras_cuotas/cuotas_credito. Also reads (never writes) deudas,
    only so delete_purchase() never deletes a purchase a debt points to.

    Key workflows:
        1. create_purchase()      → creates compras_cuotas + auto-generates N cuotas_credito,
                                    starting on the purchase month or on an explicit
                                    first-fee month (suggest_first_fee())
           update_purchase()      → early-correction edit (CLAUDE.md §4): concepto /
                                    categoria_id / same-month fecha always; cuenta_id,
                                    another-month fecha, monto_total_minor and moneda
                                    only while the fees they feed have no state of
                                    their own yet. Another-month fecha shifts every
                                    fee by the same number of months.
           update_purchase_cuotas() → regenerates the fee schedule with another number
                                    of fees, same total, same §4 rule, starting on the
                                    month the first fee has now
           reschedule_fees()      → moves pending fees to other months by hand (no two
                                    fees of a purchase in the same month)
        Card config: set_card_config() / get_card_config() (tarjetas_config),
        card_cycle_dates() (previous / current / next statement dates) and
        suggest_first_fee() (next month, or two months ahead after the closing day).
           delete_purchase()      → physical delete of a purchase + its fees while
                                    nothing depends on them (§4); otherwise
                                    cancel_purchase()
        2. open_statement()       → creates or fetches a resumenes_tarjeta for a month
        3. confirm_fee()          → marks a cuota as 'en_resumen', linking it to a statement
        4. close_statement()      → marks 'cerrado' AND consolidates real totals
                                    (monto_consumos_minor, monto_impuestos_minor via
                                    resumen_cargos_extra, and derived porcentaje_impuesto_bp)
        5. pay_statement()        → marks it 'pagado', writes the paid amount
                                    (monto_pagado_minor, now required); caller
                                    creates the transaction separately

    Fee projection:
        fees_by_month()           → returns pending fees grouped by month/account/currency
                                    (equivalent to your original Google Sheets monthly table)
"""

import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from db.database import DatabaseManager, to_minor, from_minor
from db.query_builder import QueryBuilder
from utils.money import amount_display
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuotas_credito_repository import CuotasCreditoRepository
from repositories.deudas_repository import DeudasRepository
from repositories.gastos_compartidos_repository import GastosCompartidosRepository
from repositories.resumenes_tarjeta_repository import ResumenesTarjetaRepository
from repositories.resumen_cargos_extra_repository import ResumenCargosExtraRepository
from repositories.tarjetas_config_repository import TarjetasConfigRepository

# Mapeo nombre de categoría especial (categoria_principal, subcategoria —
# ver services/categorias_service.py CATEGORIAS_PROTEGIDAS, que las
# protege de renombre/desactivación por esta misma dependencia) →
# charge_type de resumen_cargos_extra (Tarea 3 de
# docs/PROXIMOS_PASOS.md). Vive acá (motor de datos) y no en ui/ porque es
# una regla de negocio real — "elegir esta categoría significa esto es un
# cargo extra de tipo X, no una compra" — que un futuro script de lab/ que
# importe datos históricos también podría necesitar (CLAUDE.md §2), no un
# detalle de presentación. Keyed por (categoria_principal, subcategoria) y
# no por id, mismo motivo que CATEGORIAS_PROTEGIDAS: el id varía entre
# bases (dummy DB de verify/, DB real del usuario), el par de nombres es
# la identidad estable. Quien consuma esto (ui/screens/compras_cuotas.py)
# resuelve el id real una sola vez contra la lista de categorías ya
# cargada en pantalla.
CATEGORIAS_CARGO_EXTRA: dict[tuple[str, str], str] = {
    ("TARJETA DE CRÉDITO", "Impuesto tarjeta"): "impuesto",
    ("TARJETA DE CRÉDITO", "Recargo tarjeta"): "recargo",
    ("TARJETA DE CRÉDITO", "Ajuste/Reintegro tarjeta"): "ajuste",
}

# =============================================================
# EXCEPTIONS
# =============================================================

class FeesError(Exception):
    """Raised when a fees operation violates a business rule."""


class PurchaseNotFoundError(FeesError):
    """Raised when a referenced purchase does not exist."""


class StatementNotFoundError(FeesError):
    """Raised when a referenced statement does not exist."""


class FeeNotFoundError(FeesError):
    """Raised when a referenced fee does not exist."""


class StatementAlreadyPaidError(FeesError):
    """Raised when attempting to modify an already paid statement."""


class StatementAlreadyClosedError(FeesError):
    """Raised when attempting to modify a closed (but not yet paid) statement."""


class FeeAlreadyConfirmedError(FeesError):
    """Raised when a fee is already linked to a statement."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class FeesResult:
    """
    Structured result returned by FeesService operations.
    """
    success:      bool
    entity_id:    Optional[str]   = None
    data:         dict            = field(default_factory=dict)
    message:      str             = ""


# =============================================================
# SERVICE
# =============================================================

class FeesService:
    """
    Entry point for all installment and credit card statement operations.

    Usage:
        db  = DatabaseManager()
        svc = FeesService(db)

        # Register a purchase in 12 installments
        result = svc.create_purchase(
            date_str="2026-05-01",
            concept="Washing machine",
            account_id=2,           # the credit card account
            category_id=8,
            currency_code="ARS",
            total_amount=360000.0,
            total_fees=12,
        )
        # → automatically generates 12 cuotas_credito rows, one per month

        # Monthly per-card breakdown for the dashboard (Tarea 4)
        svc.resumen_por_tarjeta(month=5, year=2026)
        # → [{"cuenta_id": 2, "monto_total_minor": ..., ...}, ...] — only
        #   cards with actual cuotas due that month.
    """

    def __init__(self, db: DatabaseManager):
        """
        Initializes the service with an active DatabaseManager instance.

        Args:
            db: An initialized DatabaseManager. Schema + seed must already be applied.
        """
        self._db = db
        self._compras_repo = ComprasCuotasRepository(db)
        self._cuotas_repo = CuotasCreditoRepository(db)
        self._resumenes_repo = ResumenesTarjetaRepository(db)
        self._cargos_repo = ResumenCargosExtraRepository(db)
        self._tarjetas_repo = TarjetasConfigRepository(db)
        # Read-only — see module docstring (update_purchase()).
        self._gastos_repo = GastosCompartidosRepository(db)
        # Read-only — see module docstring (delete_purchase()).
        self._deudas_repo = DeudasRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_currency(self, code: str) -> sqlite3.Row:
        """
        Fetches a currency row by code. Raises ValueError if not found.

        Args:
            code: Currency code. E.g. 'ARS', 'USD'.

        Returns:
            sqlite3.Row with id, codigo, decimales.
        """
        row = self._db.fetchone(
            "SELECT * FROM monedas WHERE codigo = ?;", (code.upper(),)
        )
        if row is None:
            raise ValueError(f"Currency '{code}' not found.")
        return row

    def _get_account(self, account_id: str) -> sqlite3.Row:
        """
        Fetches an active account by ID. Raises FeesError if not found or inactive.

        Args:
            account_id: Primary key of the cuentas table.

        Returns:
            sqlite3.Row for the account.
        """
        row = self._db.fetchone(
            "SELECT * FROM cuentas WHERE id = ? AND activa = 1;", (account_id,)
        )
        if row is None:
            raise FeesError(f"Account id={account_id} not found or is inactive.")
        return row

    def _get_category(self, category_id: str) -> sqlite3.Row:
        """
        Fetches a category by ID. Raises FeesError if not found.

        Args:
            category_id: Primary key of the categorias table.

        Returns:
            sqlite3.Row for the category.
        """
        row = self._db.fetchone("SELECT * FROM categorias WHERE id = ?;", (category_id,))
        if row is None:
            raise FeesError(f"Category id={category_id} not found.")
        return row

    def _get_purchase(self, purchase_id: str) -> sqlite3.Row:
        """
        Fetches a purchase row by ID. Raises PurchaseNotFoundError if not found.

        Args:
            purchase_id: Primary key of the compras_cuotas table.

        Returns:
            sqlite3.Row for the purchase.
        """
        row = self._compras_repo.obtener_por_id(purchase_id)
        if row is None:
            raise PurchaseNotFoundError(f"Purchase id={purchase_id} not found.")
        return row

    def _get_statement(self, statement_id: str) -> sqlite3.Row:
        """
        Fetches a credit card statement by ID.
        Raises StatementNotFoundError if not found.

        Args:
            statement_id: Primary key of the resumenes_tarjeta table.

        Returns:
            sqlite3.Row for the statement.
        """
        row = self._resumenes_repo.obtener_por_id(statement_id)
        if row is None:
            raise StatementNotFoundError(f"Statement id={statement_id} not found.")
        return row

    @staticmethod
    def _validate_date(date_str) -> str:
        """
        Validates and normalizes a date to 'YYYY-MM-DD'.
        Accepts date/datetime objects or strings.

        Args:
            date_str: Date as string 'YYYY-MM-DD' or date/datetime object.

        Returns:
            Validated string in 'YYYY-MM-DD' format.

        Raises:
            ValueError for invalid formats.
        """
        if isinstance(date_str, (date, datetime)):
            return date_str.strftime("%Y-%m-%d")
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return date_str
        except ValueError:
            raise ValueError(
                f"Invalid date format: '{date_str}'. Expected 'YYYY-MM-DD'."
            )

    @staticmethod
    def _advance_month(month: int, year: int) -> tuple[int, int]:
        """
        Advances a month/year pair by one month, wrapping December → January.

        Args:
            month: Current month (1–12).
            year:  Current year.

        Returns:
            Tuple (next_month, next_year).
        """
        month += 1
        if month > 12:
            month = 1
            year += 1
        return month, year

    @staticmethod
    def _add_months(month: int, year: int, delta: int) -> tuple[int, int]:
        """(month, year) moved `delta` months (negative = backwards). E.g. (11, 2026) + 3 → (2, 2027)."""
        indice = year * 12 + (month - 1) + delta
        return indice % 12 + 1, indice // 12

    @staticmethod
    def _validate_period(month: Any, year: Any, label: str) -> tuple[int, int]:
        """A month 1–12 and a positive year, both ints. Raises FeesError otherwise."""
        for valor in (month, year):
            if not isinstance(valor, int) or isinstance(valor, bool):
                raise FeesError(f"{label}: month and year must be integers. Received: {month!r}/{year!r}.")
        if not 1 <= month <= 12 or year < 1:
            raise FeesError(f"{label}: invalid period {month:02d}/{year}.")
        return month, year

    @classmethod
    def _build_fee_schedule(cls, first_month: int, first_year: int, total_fees: int, per_fee_minor: int) -> list[dict]:
        """
        Fee rows for CuotasCreditoRepository.crear_lote(): N fees projected
        month by month, the first one on first_month/first_year. Shared by
        create_purchase() and the fee regeneration of update_purchase_cuotas().

        Args:
            first_month:   Month (1–12) of fee #1.
            first_year:    Year of fee #1.
            total_fees:    Number of fees (>= 1).
            per_fee_minor: Amount of each fee, in minor units.
        """
        month = first_month
        year  = first_year

        cuotas = []
        for n in range(1, total_fees + 1):
            cuotas.append({
                "numero_cuota": n,
                "mes_proyectado": month,
                "anio_proyectado": year,
                "monto_cuota_minor": per_fee_minor,
            })
            month, year = cls._advance_month(month, year)
        return cuotas

    # ----------------------------------------------------------
    # CREATE PURCHASE
    # ----------------------------------------------------------

    def create_purchase(
        self,
        date_str:         str,
        concept:          str,
        account_id:       str,
        category_id:      str,
        currency_code:    str,
        total_amount:     float,
        total_fees:       int,
        amount_per_fee:   Optional[float] = None,
        notes:            Optional[str]   = None,
        first_fee_month:  Optional[int]   = None,
        first_fee_year:   Optional[int]   = None,
    ) -> FeesResult:
        """
        Creates a purchase in installments and auto-generates all N fee rows.
        The fees are projected month by month starting from the purchase
        month — or from first_fee_month/first_fee_year when given (the
        screen passes suggest_first_fee() or what the user picked).

        If amount_per_fee is not provided, it is calculated as total_amount / total_fees.
        Pass a custom amount_per_fee when the bank applies interest and the per-fee
        amount differs from a simple division (e.g. 12 fees with CFT).

        Args:
            date_str:       Purchase date in 'YYYY-MM-DD'. First fee falls on this month
                            unless first_fee_month/first_fee_year say otherwise.
            concept:        Description. E.g. 'Washing machine', 'iPhone 15'.
            account_id:     The credit card account used for the purchase.
            category_id:    Category for classification.
            currency_code:  Currency of the purchase.
            total_amount:   Full purchase price as a positive float.
            total_fees:     Number of installments (>= 1).
            amount_per_fee: Optional per-fee amount. If None, computed automatically.
            notes:          Optional description.
            first_fee_month / first_fee_year: Optional month (1–12) and year of
                            fee #1. Both or neither; never before the purchase
                            month. None = the purchase month.

        Returns:
            FeesResult with the purchase id and a summary of generated fees.

        Raises:
            FeesError for invalid inputs (including a first fee before the
                purchase month, or only one of first_fee_month/first_fee_year).
            ValueError for invalid date or currency.
        """
        if total_amount <= 0:
            raise FeesError(f"Total amount must be positive. Received: {total_amount}.")
        if total_fees < 1:
            raise FeesError(f"Total fees must be >= 1. Received: {total_fees}.")
        if not concept or not concept.strip():
            raise FeesError("Concept cannot be empty.")

        validated_date = self._validate_date(date_str)
        purchase_month, purchase_year = int(validated_date[5:7]), int(validated_date[:4])
        if first_fee_month is None and first_fee_year is None:
            first_month, first_year = purchase_month, purchase_year
        elif first_fee_month is None or first_fee_year is None:
            raise FeesError("first_fee_month and first_fee_year must be passed together.")
        else:
            first_month, first_year = self._validate_period(first_fee_month, first_fee_year, "First fee")
            if (first_year, first_month) < (purchase_year, purchase_month):
                raise FeesError(
                    f"The first fee ({first_month:02d}/{first_year}) cannot be before the purchase month "
                    f"({purchase_month:02d}/{purchase_year})."
                )
        currency       = self._get_currency(currency_code)
        dec            = currency["decimales"]

        self._get_account(account_id)    # validate existence
        self._get_category(category_id)  # validate existence

        total_minor     = to_minor(total_amount, dec)
        per_fee_amount  = amount_per_fee if amount_per_fee is not None else total_amount / total_fees
        per_fee_minor   = to_minor(per_fee_amount, dec)

        conn = self._db.conn
        with self._db.transaction():
            # Create the purchase record
            purchase_id = self._compras_repo.crear(
                fecha_compra=validated_date,
                concepto=concept.strip(),
                cuenta_id=account_id,
                categoria_id=category_id,
                moneda_id=currency["id"],
                monto_total_minor=total_minor,
                total_cuotas=total_fees,
                monto_por_cuota_minor=per_fee_minor,
                notas=notes,
                conn=conn,
            )

            # Auto-generate N fee rows projected month by month
            cuotas = self._build_fee_schedule(first_month, first_year, total_fees, per_fee_minor)
            self._cuotas_repo.crear_lote(compra_id=purchase_id, cuotas=cuotas, conn=conn)

        return FeesResult(
            success=True,
            entity_id=purchase_id,
            data={
                "purchase_id":    purchase_id,
                "concept":        concept.strip(),
                "total_amount":   total_amount,
                "total_fees":     total_fees,
                "amount_per_fee": per_fee_amount,
                "currency":       currency_code.upper(),
                "account_id":     account_id,
                "first_fee":      (first_month, first_year),
            },
            message=(
                f"Purchase '{concept.strip()}' created with {total_fees} fee(s) "
                f"of {per_fee_amount:.2f} {currency_code.upper()} each."
            ),
        )

    # ----------------------------------------------------------
    # READ PURCHASES
    # ----------------------------------------------------------

    def get_purchase(self, purchase_id: str) -> Optional[sqlite3.Row]:
        """
        Fetches a single purchase enriched with account, category, and currency info.

        Args:
            purchase_id: Primary key in compras_cuotas.

        Returns:
            sqlite3.Row if found, None if not.
        """
        return self._compras_repo.obtener_enriquecida(purchase_id)

    def list_purchases(
        self,
        account_id:    Optional[str] = None,
        estado:        Optional[str] = None,
        currency_code: Optional[str] = None,
        page:          int = 1,
        per_page:      int = 50,
    ) -> list[sqlite3.Row]:
        """
        Returns a paginated list of purchases with optional filters.

        Args:
            account_id:    Filter by credit card account. None = all.
            estado:        Filter by 'activa', 'completada', 'cancelada'. None = all.
            currency_code: Filter by currency. None = all.
            page:          Page number, 1-indexed.
            per_page:      Rows per page.

        Returns:
            List of sqlite3.Row ordered by fecha_compra DESC.
        """
        currency_id = None
        if currency_code:
            currency_id = self._get_currency(currency_code)["id"]

        return self._compras_repo.listar_enriquecida(
            cuenta_id=account_id,
            estado=estado,
            moneda_id=currency_id,
            pagina=page,
            por_pagina=per_page,
        )

    def list_purchases_due_in_month(self, month: int, year: int) -> list[dict]:
        """
        Purchases with at least one fee due in month/year
        (cuotas_credito.mes_proyectado/anio_proyectado) — the period filter
        of the Compras en cuotas screen: what is charged that month, not
        what was bought that month. Every state is included (a cancelled
        purchase shows up if one of its fees falls in that month).

        The cuotas_credito ↔ compras_cuotas join is done here over two
        existing repository reads (CuotasCreditoRepository.listar_por_mes()
        + ComprasCuotasRepository.obtener_enriquecida()), without SQL in
        the service: one read per purchase due that month.

        Args:
            month: 1–12.
            year:  e.g. 2026.

        Returns:
            One dict per purchase, newest fecha_compra first: the same
            enriched shape as get_purchase() (account_name, category_name,
            currency_code, currency_symbol, decimales, …) plus
              monto_cuota_mes_minor — the fee(s) due that month (sum, with
                                      the purchase's sign: negative for a
                                      refund),
              numeros_cuota_mes     — their numero_cuota, ascending,
              estados_cuota_mes     — their estado, same order.

        Raises:
            ValueError if month is not 1–12.
        """
        if not 1 <= month <= 12:
            raise ValueError(f"Month must be between 1 and 12. Received: {month}.")
        cuotas_por_compra: dict[int, list[sqlite3.Row]] = {}
        for cuota in self._cuotas_repo.listar_por_mes(month, year):
            cuotas_por_compra.setdefault(cuota["compra_id"], []).append(cuota)

        compras: list[dict] = []
        for compra_id, cuotas in cuotas_por_compra.items():
            compra = self._compras_repo.obtener_enriquecida(compra_id)
            if compra is None:
                continue
            fila = dict(compra)
            fila["monto_cuota_mes_minor"] = sum(c["monto_cuota_minor"] for c in cuotas)
            fila["numeros_cuota_mes"] = [c["numero_cuota"] for c in cuotas]
            fila["estados_cuota_mes"] = [c["estado"] for c in cuotas]
            compras.append(fila)
        compras.sort(key=lambda f: (f["fecha_compra"], f["id"]), reverse=True)
        return compras

    def get_fees_for_purchase(self, purchase_id: str) -> list[sqlite3.Row]:
        """
        Returns all fee rows for a given purchase, ordered by fee number.

        Args:
            purchase_id: Primary key in compras_cuotas.

        Returns:
            List of cuotas_credito rows ordered by numero_cuota ASC.
        """
        self._get_purchase(purchase_id)  # validate existence
        return self._cuotas_repo.listar_por_compra(purchase_id)

    # ----------------------------------------------------------
    # FEE PROJECTION (the Google Sheets equivalent)
    # ----------------------------------------------------------

    # NO migrado (Fase 2, COMPRAS_CUOTAS paso 2): es un reporte agregado
    # (GROUP BY cuenta+moneda, con COUNT/SUM y JOINs a compras_cuotas/
    # cuentas/monedas) — mismo criterio que monthly_summary()/
    # summary_by_person() en los bloques anteriores. NO es lo mismo que
    # CuotasCreditoRepository.listar_por_mes(), que devuelve filas planas de
    # cuotas_credito sin agregación ni JOINs; ese método no cubre lo que
    # fees_by_month() necesita.
    def fees_by_month(
        self,
        month:         int,
        year:          int,
        account_id:    Optional[str] = None,
        currency_code: Optional[str] = None,
        estado:        str = "pendiente",
    ) -> list[sqlite3.Row]:
        """
        Returns pending fees for a given month grouped by account and currency.
        This is the direct equivalent of your Google Sheets monthly credit table:
        one row per (bank, currency) with the total amount due.

        Args:
            month:         Target month (1–12).
            year:          Target year.
            account_id:    Optionally filter to a single credit card account.
            currency_code: Optionally filter to a single currency.
            estado:        Fee status to include. Default 'pendiente'.
                           Use 'en_resumen' to see fees already in a statement.

        Returns:
            List of rows with: account_name, currency_code, fee_count, total_minor.
        """
        if not (1 <= month <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {month}.")

        currency_id = None
        if currency_code:
            currency_id = self._get_currency(currency_code)["id"]

        return (
            QueryBuilder("cuotas_credito qc", include_deleted=True)
            .select(
                "c.id AS account_id",
                "c.nombre AS account_name",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
                "COUNT(*) AS fee_count",
                "SUM(qc.monto_cuota_minor) AS total_minor",
            )
            .join("compras_cuotas pc", "pc.id = qc.compra_id")
            .join("cuentas c",         "c.id = pc.cuenta_id")
            .join("monedas m",         "m.id = pc.moneda_id")
            .where("qc.mes_proyectado",  month)
            .where("qc.anio_proyectado", year)
            .where("qc.estado",          estado)
            .where("pc.cuenta_id",       account_id)
            .where("pc.moneda_id",       currency_id)
            .group_by("c.id", "m.id")
            .order("c.nombre")
            .ejecutar(self._db.conn)
        )

    # NO migrado: no accede a ninguna tabla directamente — solo orquesta N
    # llamadas a fees_by_month() (que tampoco migró, ver nota arriba). No
    # hay SQL propio acá para mover a un repositorio.
    def fees_projection(
        self,
        months:        int = 6,
        currency_code: str = "ARS",
    ) -> list[dict]:
        """
        Projects total fees due for the next N months across all accounts.
        Useful for the dashboard projection panel.

        Args:
            months:        How many months ahead to project. Default 6.
            currency_code: Currency to project. Default 'ARS'.

        Returns:
            List of dicts: [{"month": 5, "year": 2026, "total_minor": 123456}, ...]
        """
        today = datetime.today()
        month, year = today.month, today.year
        result = []

        for _ in range(months):
            rows  = self.fees_by_month(month, year, currency_code=currency_code)
            total = sum(r["total_minor"] for r in rows)
            result.append({"month": month, "year": year, "total_minor": total})
            month, year = self._advance_month(month, year)

        return result

    # ----------------------------------------------------------
    # DESGLOSE POR TARJETA (Tarea 4 del dashboard — cuotas + cargos extra)
    # ----------------------------------------------------------

    def resumen_por_tarjeta(self, mes: int, anio: int) -> list[dict]:
        """
        Desglose del total a pagar este mes por cada tarjeta de crédito
        (cuentas.tipo='credito') CON ACTIVIDAD REAL ese período: solo
        tarjetas con al menos una cuotas_credito venciendo (mes_proyectado/
        anio_proyectado) en mes/anio — detección dinámica vía el INNER
        JOIN de la query de abajo, nunca una lista fija de todas las
        tarjetas existentes (mismo criterio que
        DashboardService.get_movimientos_por_cuenta()).

        Para cada grupo (cuenta_id, moneda_id):
        - monto_cuotas_minor: suma de monto_cuota_minor de esas cuotas.
          Excluye estado='omitido' (cuotas de compras canceladas vía
          cancel_purchase() — no son "a pagar"). Incluye 'pendiente',
          'en_resumen' y 'pagado': lo que importa acá es qué vencía ese
          mes, no si ya se confirmó/pagó.
        - monto_cargos_extra_minor: suma de resumen_cargos_extra del
          resumen de esa cuenta/mes/año (vía
          ResumenesTarjetaRepository.obtener_por_periodo() +
          ResumenCargosExtraRepository.listar_por_resumen()) — 0 si el
          resumen todavía no se abrió o no tiene cargos cargados (hoy no
          hay UI para cargarlos, Tarea 3 pendiente — el cálculo ya los
          suma para cuando esa UI exista).
        - monto_total_minor: monto_cuotas_minor + monto_cargos_extra_minor.

        Ambigüedad de moneda de los cargos extra (ver
        docs/DATA_MODEL_DECISIONS.md sección 15): `resumen_cargos_extra`
        no tiene columna de moneda propia — un resumen es por
        cuenta/mes/año, no por cuenta/mes/año/moneda. Si la MISMA tarjeta
        tuviera cuotas venciendo en más de una moneda el mismo mes (caso
        límite que el schema permite pero no ocurre en el uso real: una
        tarjeta física factura en una sola moneda), no hay forma de saber
        a cuál de los dos totales sumar los cargos extra sin inventar una
        convención no documentada — en ese caso monto_cargos_extra_minor
        queda en 0 en TODOS los grupos de esa tarjeta ese mes, y
        cargos_extra_multiples_monedas=True lo señala en vez de adivinar.
        Con una sola moneda por tarjeta ese mes (caso normal) se suman sin
        ambigüedad.

        Args:
            mes:  Mes 1–12.
            anio: Año de 4 dígitos.

        Returns:
            Lista de dicts {cuenta_id, account_name, moneda_id,
            currency_code, currency_symbol, decimales, monto_cuotas_minor,
            monto_cargos_extra_minor, monto_total_minor,
            cargos_extra_multiples_monedas}, ordenada por account_name.

        Raises:
            ValueError si mes está fuera de rango.
        """
        if not (1 <= mes <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {mes}.")

        filas_cuotas = (
            QueryBuilder("cuotas_credito qc", include_deleted=True)
            .select(
                "c.id AS cuenta_id",
                "c.nombre AS account_name",
                "m.id AS moneda_id",
                "m.codigo AS currency_code",
                "m.simbolo AS currency_symbol",
                "m.decimales",
                "SUM(qc.monto_cuota_minor) AS monto_cuotas_minor",
            )
            .join("compras_cuotas pc", "pc.id = qc.compra_id")
            .join("cuentas c",         "c.id = pc.cuenta_id")
            .join("monedas m",         "m.id = pc.moneda_id")
            .where("qc.mes_proyectado",  mes)
            .where("qc.anio_proyectado", anio)
            .where("qc.estado", "omitido", "!=")
            .where("c.tipo", "credito")
            .group_by("c.id", "m.id")
            .order("c.nombre")
            .ejecutar(self._db.conn)
        )

        monedas_por_cuenta: dict[int, set] = {}
        for fila in filas_cuotas:
            monedas_por_cuenta.setdefault(fila["cuenta_id"], set()).add(fila["moneda_id"])

        resultado = []
        for fila in filas_cuotas:
            cuenta_id = fila["cuenta_id"]
            ambiguo = len(monedas_por_cuenta[cuenta_id]) > 1

            cargos_extra_minor = 0
            if not ambiguo:
                resumen = self._resumenes_repo.obtener_por_periodo(cuenta_id, mes, anio)
                if resumen:
                    cargos = self._cargos_repo.listar_por_resumen(resumen["id"])
                    cargos_extra_minor = sum(c["monto_minor"] for c in cargos)

            resultado.append({
                "cuenta_id":                       cuenta_id,
                "account_name":                    fila["account_name"],
                "moneda_id":                       fila["moneda_id"],
                "currency_code":                   fila["currency_code"],
                "currency_symbol":                 fila["currency_symbol"],
                "decimales":                       fila["decimales"],
                "monto_cuotas_minor":              fila["monto_cuotas_minor"],
                "monto_cargos_extra_minor":        cargos_extra_minor,
                "monto_total_minor":               fila["monto_cuotas_minor"] + cargos_extra_minor,
                "cargos_extra_multiples_monedas":  ambiguo,
            })

        return resultado

    # ----------------------------------------------------------
    # STATEMENT MANAGEMENT
    # ----------------------------------------------------------

    def open_statement(
        self,
        account_id:     str,
        month:          int,
        year:           int,
        tax_percentage_bp: int = 0,
    ) -> FeesResult:
        """
        Creates a new credit card statement for the given account/month/year,
        or returns the existing one if already created (idempotent).

        The monto_consumos_minor is initialized to 0 and grows as fees are
        confirmed via confirm_fee(). monto_total_pagado_minor is recalculated
        automatically on each confirm_fee() call.

        Args:
            account_id:         The credit card account.
            month:              Statement month (1–12).
            year:               Statement year.
            tax_percentage_bp:  Tax on purchases in basis points.
                                E.g. 3000 = 30% (Bienes Personales / PAIS).

        Returns:
            FeesResult with the statement id.
        """
        if not (1 <= month <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {month}.")
        self._get_account(account_id)

        # Idempotent — return existing if already created
        existing = self._resumenes_repo.obtener_por_periodo(account_id, month, year)
        if existing:
            return FeesResult(
                success=True,
                entity_id=existing["id"],
                data={"statement_id": existing["id"], "already_existed": True},
                message=f"Statement for {month:02d}/{year} already exists (id={existing['id']}).",
            )

        stmt_id = self._resumenes_repo.crear(
            cuenta_id=account_id,
            mes=month,
            anio=year,
            porcentaje_impuesto_bp=tax_percentage_bp,
        )

        return FeesResult(
            success=True,
            entity_id=stmt_id,
            data={
                "statement_id":      stmt_id,
                "account_id":        account_id,
                "month":             month,
                "year":              year,
                "tax_percentage_bp": tax_percentage_bp,
                "already_existed":   False,
            },
            message=f"Statement created for {month:02d}/{year} (id={stmt_id}).",
        )

    def confirm_fee(
        self,
        fee_id:       str,
        statement_id: str,
        real_month:   Optional[int] = None,
        real_year:    Optional[int] = None,
        notes:        Optional[str] = None,
    ) -> FeesResult:
        """
        Confirms that a fee appeared in a specific credit card statement.
        Marks the fee as 'en_resumen' and links it to the statement.
        Updates the statement's monto_consumos_minor automatically.

        real_month / real_year allow recording when a fee was skipped by the bank
        and appeared a month later than projected (your 'omitida/duplicada' tracking).

        Args:
            fee_id:       The cuotas_credito row to confirm.
            statement_id: The resumenes_tarjeta to link it to.
            real_month:   Actual month it appeared. Defaults to projected month.
            real_year:    Actual year it appeared. Defaults to projected year.
            notes:        Optional note. E.g. 'Omitted in May, appeared in June'.

        Returns:
            FeesResult with updated statement totals.

        Raises:
            FeeNotFoundError if the fee does not exist.
            FeeAlreadyConfirmedError if already linked to a statement.
            StatementNotFoundError if the statement does not exist.
            StatementAlreadyPaidError if the statement is already paid.
        """
        fee = self._cuotas_repo.obtener_por_id(fee_id)
        if fee is None:
            raise FeeNotFoundError(f"Fee id={fee_id} not found.")

        if fee["estado"] in ("en_resumen", "pagado"):
            raise FeeAlreadyConfirmedError(
                f"Fee id={fee_id} is already '{fee['estado']}'."
            )

        statement = self._get_statement(statement_id)
        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid and cannot be modified."
            )

        actual_month = real_month or fee["mes_proyectado"]
        actual_year  = real_year  or fee["anio_proyectado"]

        # Same arithmetic as before (SQLite's integer "/" truncates like
        # Python's "//" for positive operands): monto_total_pagado_minor
        # keeps being an incremental estimate updated on every confirm_fee()
        # call, using whatever porcentaje_impuesto_bp the statement already
        # has (still the manual value from open_statement() — the DERIVED
        # bp only replaces it at close_statement() time, see
        # ResumenesTarjetaRepository.marcar_cerrado()). Preserved exactly,
        # not one of the two sanctioned behavior changes for this task.
        new_consumos = statement["monto_consumos_minor"] + fee["monto_cuota_minor"]
        new_total = (new_consumos * (10000 + statement["porcentaje_impuesto_bp"])) // 10000

        conn = self._db.conn
        with self._db.transaction():
            # Link fee to statement
            self._cuotas_repo.marcar_estado(
                fee_id, "en_resumen",
                resumen_id=statement_id,
                mes_real_pago=actual_month,
                anio_real_pago=actual_year,
                notas=notes,
                conn=conn,
            )

            # Update statement consumos total
            self._resumenes_repo.actualizar_totales(
                statement_id,
                monto_consumos_minor=new_consumos,
                monto_total_pagado_minor=new_total,
                conn=conn,
            )

        updated_stmt = self._get_statement(statement_id)
        return FeesResult(
            success=True,
            entity_id=statement_id,
            data={
                "fee_id":            fee_id,
                "statement_id":      statement_id,
                "fee_minor":         fee["monto_cuota_minor"],
                "statement_total":   updated_stmt["monto_total_pagado_minor"],
                "real_month":        actual_month,
                "real_year":         actual_year,
            },
            message=f"Fee #{fee_id} confirmed on statement #{statement_id}.",
        )

    # ----------------------------------------------------------
    # EXTRA CHARGES (resumen_cargos_extra)
    # ----------------------------------------------------------

    _EXTRA_CHARGE_TYPES = ("impuesto", "recargo", "ajuste", "otro")

    def add_extra_charge(
        self,
        statement_id: str,
        concept:      str,
        charge_type:  str,
        amount_minor: int,
    ) -> FeesResult:
        """
        Adds an extra charge (tax, surcharge, adjustment) to an OPEN
        statement. Does NOT touch resumenes_tarjeta.monto_impuestos_minor /
        porcentaje_impuesto_bp — those are only recalculated for real by
        close_statement() (see its docstring and
        docs/DATA_MODEL_DECISIONS.md sección 12). Adding a charge here only
        writes to resumen_cargos_extra.

        Args:
            statement_id: The statement to add the charge to. Must be
                         'abierto' — 'cerrado'/'pagado' statements reject
                         further charges.
            concept:      Description. E.g. 'IVA', 'Impuesto PAIS'.
            charge_type:  One of 'impuesto', 'recargo', 'ajuste', 'otro'.
            amount_minor: The charge amount in minor units. Can be negative
                         (e.g. an adjustment in the user's favor).

        Returns:
            FeesResult with the new charge id.

        Raises:
            StatementNotFoundError if the statement does not exist.
            FeesError if concept is empty.
            ValueError if charge_type is not one of the valid types.
            StatementAlreadyPaidError if the statement is already paid.
            StatementAlreadyClosedError if the statement is closed (but not paid).
        """
        statement = self._get_statement(statement_id)

        if not concept or not concept.strip():
            raise FeesError("Concept cannot be empty.")
        if charge_type not in self._EXTRA_CHARGE_TYPES:
            raise ValueError(
                f"Invalid charge_type: '{charge_type}'. "
                f"Expected one of {self._EXTRA_CHARGE_TYPES}."
            )

        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid and cannot be modified."
            )
        if statement["estado"] == "cerrado":
            raise StatementAlreadyClosedError(
                f"Statement id={statement_id} is closed and cannot be modified."
            )

        charge_id = self._cargos_repo.agregar(
            statement_id, concept.strip(), charge_type, amount_minor,
        )

        return FeesResult(
            success=True,
            entity_id=charge_id,
            data={
                "charge_id":    charge_id,
                "statement_id": statement_id,
                "concept":      concept.strip(),
                "charge_type":  charge_type,
                "amount_minor": amount_minor,
            },
            message=f"Extra charge '{concept.strip()}' ({charge_type}) added to statement #{statement_id}.",
        )

    def list_extra_charges(self, statement_id: str) -> list[sqlite3.Row]:
        """
        Returns all extra charges of a statement.

        Args:
            statement_id: Primary key in resumenes_tarjeta.

        Returns:
            List of resumen_cargos_extra rows.

        Raises:
            StatementNotFoundError if the statement does not exist.
        """
        self._get_statement(statement_id)  # validate existence
        return self._cargos_repo.listar_por_resumen(statement_id)

    def remove_extra_charge(self, charge_id: str, statement_id: str) -> FeesResult:
        """
        Removes an extra charge from an OPEN statement. Requires both ids
        so the charge's ownership can be validated — a charge_id that
        exists but belongs to a different statement is rejected, instead
        of silently deleting the wrong row.

        Does NOT touch resumenes_tarjeta.monto_impuestos_minor /
        porcentaje_impuesto_bp — same reasoning as add_extra_charge().

        Args:
            charge_id:    The resumen_cargos_extra row to remove.
            statement_id: The statement it must belong to.

        Returns:
            FeesResult with success=True.

        Raises:
            StatementNotFoundError if the statement does not exist.
            FeesError if the charge does not exist or belongs to a
                      different statement.
            StatementAlreadyPaidError if the statement is already paid.
            StatementAlreadyClosedError if the statement is closed (but not paid).
        """
        statement = self._get_statement(statement_id)

        charge = self._cargos_repo.obtener_por_id(charge_id)
        if charge is None or charge["resumen_id"] != statement_id:
            raise FeesError(
                f"Extra charge id={charge_id} not found on statement id={statement_id}."
            )

        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid and cannot be modified."
            )
        if statement["estado"] == "cerrado":
            raise StatementAlreadyClosedError(
                f"Statement id={statement_id} is closed and cannot be modified."
            )

        self._cargos_repo.eliminar(charge_id)

        return FeesResult(
            success=True,
            entity_id=charge_id,
            data={"charge_id": charge_id, "statement_id": statement_id},
            message=f"Extra charge #{charge_id} removed from statement #{statement_id}.",
        )

    def close_statement(self, statement_id: str) -> FeesResult:
        """
        Marks a statement as 'cerrado' (reviewed, ready to pay) and
        CONSOLIDATES its totals for real: monto_consumos_minor is
        recalculated as the sum of every cuotas_credito row linked to this
        statement, monto_impuestos_minor as the sum of every
        resumen_cargos_extra row of this statement, and
        porcentaje_impuesto_bp is derived from both
        (monto_impuestos_minor*10000 // monto_consumos_minor, 0 if there are
        no consumos). This is one of the two intentionally sanctioned
        behavior changes of this migration (see
        docs/DATA_MODEL_DECISIONS.md sección 12) — before this, closing a
        statement only flipped `estado`, it never touched the totals.

        Note: this does NOT touch monto_total_pagado_minor — that field is
        only written by pay_statement() (see docstring there).

        Args:
            statement_id: The statement to close.

        Returns:
            FeesResult with the three consolidated totals in `data`.

        Raises:
            StatementNotFoundError if not found.
            StatementAlreadyPaidError if already paid.
        """
        statement = self._get_statement(statement_id)
        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid."
            )

        totales = self._resumenes_repo.marcar_cerrado(statement_id)

        return FeesResult(
            success=True,
            entity_id=statement_id,
            data={
                "monto_consumos_minor":   totales["monto_consumos_minor"],
                "monto_impuestos_minor":  totales["monto_impuestos_minor"],
                "porcentaje_impuesto_bp": totales["porcentaje_impuesto_bp"],
            },
            message=(
                f"Statement #{statement_id} closed. "
                f"Consumos: {totales['monto_consumos_minor']} minor, "
                f"Impuestos: {totales['monto_impuestos_minor']} minor, "
                f"Tax: {totales['porcentaje_impuesto_bp']} bp."
            ),
        )

    def pay_statement(
        self,
        statement_id:       str,
        payment_date:       str,
        monto_pagado_minor: int,
        transaction_id:     Optional[str] = None,
    ) -> FeesResult:
        """
        Marks a statement as 'pagado' and all its linked fees as 'pagado'.
        The actual cash transaction (egreso from your bank account) must be
        created separately via TransactionService and its id passed here.

        CAMBIO DE FIRMA (Fase 2, COMPRAS_CUOTAS paso 2, sanctioned behavior
        change): monto_pagado_minor is now a required parameter, written to
        monto_total_pagado_minor. Before this migration, pay_statement()
        never wrote monto_total_pagado_minor at all — it stayed whatever
        confirm_fee() had last accumulated using the statement's manual
        porcentaje_impuesto_bp (see confirm_fee() docstring). Now the
        caller passes the real amount paid explicitly.

        Args:
            statement_id:       The statement being paid.
            payment_date:       Payment date in 'YYYY-MM-DD'.
            monto_pagado_minor: The real amount paid, in minor units. Written
                                to monto_total_pagado_minor.
            transaction_id:     Optional ID from TransactionService.create()
                                that represents the actual cash outflow.

        Returns:
            FeesResult with success=True.

        Raises:
            StatementNotFoundError if not found.
            StatementAlreadyPaidError if already paid.
        """
        statement    = self._get_statement(statement_id)
        validated_dt = self._validate_date(payment_date)

        if statement["estado"] == "pagado":
            raise StatementAlreadyPaidError(
                f"Statement id={statement_id} is already paid."
            )

        conn = self._db.conn
        with self._db.transaction():
            # Mark all fees in this statement as paid
            self._cuotas_repo.marcar_estado_por_resumen(
                statement_id, "en_resumen", "pagado", conn=conn,
            )

            # Mark the statement itself as paid
            self._resumenes_repo.marcar_pagado(
                statement_id,
                fecha_pago=validated_dt,
                monto_pagado_minor=monto_pagado_minor,
                conn=conn,
            )

        return FeesResult(
            success=True,
            entity_id=statement_id,
            data={
                "statement_id":  statement_id,
                "payment_date":  validated_dt,
                "total_paid":    monto_pagado_minor,
                "transaction_id": transaction_id,
            },
            message=(
                f"Statement #{statement_id} marked as paid on {validated_dt}."
                + (f" Linked to transaction #{transaction_id}." if transaction_id else "")
            ),
        )

    # ----------------------------------------------------------
    # READ STATEMENTS
    # ----------------------------------------------------------

    def get_statement(self, statement_id: str) -> Optional[sqlite3.Row]:
        """
        Fetches a single statement enriched with account name.

        Args:
            statement_id: Primary key in resumenes_tarjeta.

        Returns:
            sqlite3.Row if found, None if not.
        """
        return self._resumenes_repo.obtener_enriquecida(statement_id)

    def list_statements(
        self,
        account_id: Optional[str] = None,
        estado:     Optional[str] = None,
        year:       Optional[int] = None,
        page:       int = 1,
        per_page:   int = 24,
    ) -> list[sqlite3.Row]:
        """
        Returns a paginated list of credit card statements.

        Args:
            account_id: Filter by account. None = all.
            estado:     Filter by 'abierto', 'cerrado', 'pagado'. None = all.
            year:       Filter by year. None = all years.
            page:       Page number, 1-indexed.
            per_page:   Rows per page. Default 24 (2 years of monthly statements).

        Returns:
            List of sqlite3.Row ordered by year DESC, month DESC.
        """
        return self._resumenes_repo.listar_enriquecida(
            cuenta_id=account_id,
            estado=estado,
            anio=year,
            pagina=page,
            por_pagina=per_page,
        )

    # ----------------------------------------------------------
    # UPDATE PURCHASE
    # ----------------------------------------------------------

    # Same shape as TransactionService.update(): None = no change, one
    # repository kwarg per field that actually changes. Early-correction
    # rule (CLAUDE.md §4) applied per field — cuotas_credito and
    # gastos_compartidos are generated rows with state of their own, so a
    # field that feeds them is editable only while nothing downstream has
    # left its initial state; otherwise blocked, and the correction goes
    # through an explicit adjustment instead of silently rewriting history:
    #   - concepto / categoria_id: descriptive only — always editable.
    #   - fecha, same month: descriptive only (fees store month/year, not
    #     the day) — always editable.
    #   - fecha, another month: the fee schedule shifts (regenerated) →
    #     every fee still 'pendiente' and none shared per fee.
    #   - cuenta_id: a fee 'en_resumen'/'pagado' belongs to THAT card's
    #     statement → every fee still 'pendiente'.
    #   - monto_total_minor: every fee carries its own amount and shared
    #     expenses were computed from the total → every fee 'pendiente',
    #     nothing shared, default split. Otherwise: an 'ajuste' extra
    #     charge on the statement.
    #   - moneda_codigo: a fee in a statement is part of that statement's
    #     currency total, and gastos_compartidos has no currency of its own
    #     (a shared purchase switching currency would mix currencies in the
    #     household balance) → every fee 'pendiente', nothing shared.
    # The number of fees has its own method: update_purchase_cuotas().
    def update_purchase(
        self,
        purchase_id:       str,
        concepto:          Optional[str] = None,
        categoria_id:      Optional[str] = None,
        monto_total_minor: Optional[int] = None,
        cuenta_id:         Optional[str] = None,
        fecha:             Optional[str] = None,
        moneda_codigo:     Optional[str] = None,
    ) -> FeesResult:
        """
        Updates one or more fields of an existing purchase.
        Only the fields explicitly passed (non-None) are modified. Every
        check runs before any write — a blocked field leaves the whole call
        unwritten, including the other fields passed with it.

        Changing monto_total_minor recalculates monto_por_cuota_minor as
        round(monto_total_minor / total_cuotas) — same default split as
        create_purchase() — and rewrites every fee with that amount.
        Moving fecha to another month shifts every fee by the same number of
        months (a hand-picked first fee and any fee moved with
        reschedule_fees() keep their distance to the purchase). Both happen
        in the same transaction as the purchase row.

        Changing moneda_codigo keeps the DISPLAYED amount (3000.00 ARS →
        3000.00 USD), same criterion as the Registro, where
        TransactionService.update() receives amount + currency together:
        when the new currency has other decimals (CLP 0, BTC 8) every
        amount in minor units is rescaled — total, per-fee (recalculated
        from the new total if the split is the default one), every fee and
        monto_reintegro_minor. If monto_total_minor is passed in the same
        call, it is taken as already expressed in the new currency.

        Args:
            purchase_id:       The purchase to modify.
            concepto:          New description. None = no change.
            categoria_id:      New category ID. None = no change.
            monto_total_minor: New non-zero total, in minor units — negative for a
                               refund/reintegro (e.g. rows migrated from the old
                               spreadsheet), so a mistyped refund can be fixed
                               too. None = no change.
            cuenta_id:         New credit card account ID. None = no change.
            fecha:             New purchase date 'YYYY-MM-DD'. None = no change.
            moneda_codigo:     New currency code, e.g. 'USD'. None = no change.

        Returns:
            FeesResult with success=True if at least one field changed.

        Raises:
            PurchaseNotFoundError if the purchase does not exist.
            ValueError for an invalid date format or an unknown currency.
            FeesError if concepto is empty, categoria_id / cuenta_id does not
                exist (or the account is inactive), monto_total_minor == 0,
                an amount would round to 0 in the new currency, or the field
                can no longer be edited directly (see the early-correction
                note above).
        """
        purchase = self._get_purchase(purchase_id)
        cuotas = self._cuotas_repo.listar_por_compra(purchase_id)
        campos_repo: dict[str, Any] = {}

        if concepto is not None:
            if not concepto.strip():
                raise FeesError("Concept cannot be empty.")
            campos_repo["concepto"] = concepto.strip()

        if categoria_id is not None:
            self._get_category(categoria_id)  # validate existence
            campos_repo["categoria_id"] = categoria_id

        if cuenta_id is not None and cuenta_id != purchase["cuenta_id"]:
            self._get_account(cuenta_id)  # validate existence (active)
            self._require_fees_pending(purchase_id, cuotas, "its card can no longer be changed")
            campos_repo["cuenta_id"] = cuenta_id

        meses_a_correr = 0
        if fecha is not None:
            fecha_validada = self._validate_date(fecha)
            if fecha_validada != purchase["fecha_compra"]:
                if fecha_validada[:7] != purchase["fecha_compra"][:7]:
                    accion = "its date can no longer move to another month"
                    self._require_fees_pending(purchase_id, cuotas, accion)
                    self._require_fees_not_shared(purchase_id, cuotas, accion)
                    meses_a_correr = (
                        (int(fecha_validada[:4]) * 12 + int(fecha_validada[5:7]))
                        - (int(purchase["fecha_compra"][:4]) * 12 + int(purchase["fecha_compra"][5:7]))
                    )
                campos_repo["fecha_compra"] = fecha_validada

        nueva_moneda = None
        if moneda_codigo is not None:
            moneda = self._get_currency(moneda_codigo)  # ValueError if unknown
            if moneda["id"] != purchase["moneda_id"]:
                accion = "its currency can no longer be changed"
                self._require_fees_pending(purchase_id, cuotas, accion)
                self._require_purchase_not_shared(purchase_id, accion)
                self._require_fees_not_shared(purchase_id, cuotas, accion)
                nueva_moneda = moneda
                campos_repo["moneda_id"] = moneda["id"]

        nuevo_monto_cuota_minor = None
        if monto_total_minor is not None:
            # Negative = refund/reintegro (see docstring); only 0 is invalid.
            if monto_total_minor == 0:
                raise FeesError("Total amount cannot be 0.")
            if monto_total_minor != purchase["monto_total_minor"]:
                accion = "its total can no longer be edited directly"
                self._require_fees_pending(
                    purchase_id, cuotas,
                    f"{accion}; register the difference as an 'ajuste' extra charge on the statement instead",
                )
                self._require_purchase_not_shared(purchase_id, accion)
                self._require_fees_not_shared(purchase_id, cuotas, accion)
                self._require_default_split(purchase, "its total cannot be recalculated")
                nuevo_monto_cuota_minor = round(monto_total_minor / purchase["total_cuotas"])
                campos_repo["monto_total_minor"] = monto_total_minor
                campos_repo["monto_por_cuota_minor"] = nuevo_monto_cuota_minor

        # New currency with other decimals and no new total in the same
        # call: rescale every amount so the displayed value stays the same
        # (see docstring). Same decimals → amounts untouched.
        if nueva_moneda is not None and monto_total_minor is None:
            decimales_actuales = self.get_purchase(purchase_id)["decimales"]
            decimales_nuevos = nueva_moneda["decimales"]
            if decimales_nuevos != decimales_actuales:
                def _reescalar(minor: int) -> int:
                    return self._rescale_minor(minor, decimales_actuales, decimales_nuevos)

                nuevo_total = _reescalar(purchase["monto_total_minor"])
                nuevo_monto_cuota_minor = (
                    round(nuevo_total / purchase["total_cuotas"])
                    if self._is_default_split(purchase)
                    else _reescalar(purchase["monto_por_cuota_minor"])
                )
                if nuevo_total == 0 or nuevo_monto_cuota_minor == 0:
                    raise FeesError(
                        f"Purchase id={purchase_id}: its amount rounds to 0 in "
                        f"{nueva_moneda['codigo']} ({decimales_nuevos} decimals)."
                    )
                campos_repo["monto_total_minor"] = nuevo_total
                campos_repo["monto_por_cuota_minor"] = nuevo_monto_cuota_minor
                if purchase["monto_reintegro_minor"]:
                    campos_repo["monto_reintegro_minor"] = _reescalar(purchase["monto_reintegro_minor"])

        if not campos_repo:
            return FeesResult(
                success=False,
                entity_id=purchase_id,
                message="No fields to update were provided.",
            )

        fees_updated = 0
        conn = self._db.conn
        with self._db.transaction():
            self._compras_repo.actualizar(purchase_id, conn=conn, **campos_repo)
            if meses_a_correr:
                # Every fee moves the same number of months as the date (all
                # of them are 'pendiente': checked above).
                for cuota in cuotas:
                    mes, anio = self._add_months(cuota["mes_proyectado"], cuota["anio_proyectado"], meses_a_correr)
                    fees_updated += self._cuotas_repo.actualizar_periodo(cuota["id"], mes, anio, conn=conn)
            if nuevo_monto_cuota_minor is not None:
                fees_updated = max(fees_updated, self._cuotas_repo.actualizar_monto_por_compra(
                    purchase_id, "pendiente", nuevo_monto_cuota_minor, conn=conn,
                ))

        return FeesResult(
            success=True,
            entity_id=purchase_id,
            data={"fields_changed": list(campos_repo), "fees_updated": fees_updated},
            message=f"Purchase #{purchase_id} updated ({len(campos_repo)} field(s) changed).",
        )

    # The whole fee schedule is regenerated (delete + recreate in one
    # transaction), so it follows the same §4 rule as moving the date to
    # another month, plus the default-split rule of the total. A purchase
    # shared as 'total_unico' is NOT blocked: that shared expense was
    # computed from the total, which does not change here.
    def update_purchase_cuotas(self, compra_id: str, nueva_cantidad: int) -> FeesResult:
        """
        Changes the number of fees of a purchase, keeping its total:
        deletes every fee and recreates nueva_cantidad of them, projected
        month by month from the month fee #1 has now (a hand-picked first
        fee is kept; later fees moved by hand go back to consecutive
        months), each of round(monto_total_minor / nueva_cantidad).

        Args:
            compra_id:      The purchase to modify.
            nueva_cantidad: New number of fees (>= 1).

        Returns:
            FeesResult with success=False if nueva_cantidad is the current one.

        Raises:
            PurchaseNotFoundError if the purchase does not exist.
            FeesError if nueva_cantidad < 1, a fee already left 'pendiente',
                a fee is shared, or the purchase was created with a custom
                per-fee amount.
        """
        purchase = self._get_purchase(compra_id)
        if nueva_cantidad < 1:
            raise FeesError(f"Total fees must be >= 1. Received: {nueva_cantidad}.")
        if nueva_cantidad == purchase["total_cuotas"]:
            return FeesResult(success=False, entity_id=compra_id, message="Number of fees unchanged.")

        cuotas = self._cuotas_repo.listar_por_compra(compra_id)
        accion = "its number of fees can no longer be changed"
        self._require_fees_pending(compra_id, cuotas, accion)
        self._require_fees_not_shared(compra_id, cuotas, accion)
        self._require_default_split(purchase, "its fees cannot be recalculated")

        monto_cuota_minor = round(purchase["monto_total_minor"] / nueva_cantidad)
        conn = self._db.conn
        with self._db.transaction():
            self._compras_repo.actualizar(
                compra_id, total_cuotas=nueva_cantidad, monto_por_cuota_minor=monto_cuota_minor, conn=conn,
            )
            self._regenerate_fees(compra_id, purchase["fecha_compra"], cuotas, nueva_cantidad, monto_cuota_minor, conn)

        return FeesResult(
            success=True,
            entity_id=compra_id,
            data={"total_fees": nueva_cantidad, "monto_por_cuota_minor": monto_cuota_minor},
            message=f"Purchase #{compra_id} now has {nueva_cantidad} fee(s).",
        )

    # --- Early-correction checks (CLAUDE.md §4) shared by the edits above ---

    def _require_fees_pending(self, purchase_id: str, cuotas: list[sqlite3.Row], accion: str) -> None:
        no_pendientes = [c for c in cuotas if c["estado"] != "pendiente"]
        if no_pendientes:
            raise FeesError(
                f"Purchase id={purchase_id} has {len(no_pendientes)} fee(s) already in a "
                f"statement, paid or omitted — {accion}."
            )

    def _require_fees_not_shared(self, purchase_id: str, cuotas: list[sqlite3.Row], accion: str) -> None:
        if any(self._gastos_repo.listar_por_origen("cuota_credito", c["id"]) for c in cuotas):
            raise FeesError(
                f"Purchase id={purchase_id} is shared per fee — {accion}; "
                f"delete the shared expense(s) first."
            )

    def _require_purchase_not_shared(self, purchase_id: str, accion: str) -> None:
        # Shared as a whole ('total_unico' → origen_tipo 'compra_cuotas').
        if self._gastos_repo.listar_por_origen("compra_cuotas", purchase_id):
            raise FeesError(
                f"Purchase id={purchase_id} is shared — {accion}; delete the shared expense(s) first."
            )

    @staticmethod
    def _is_default_split(purchase: sqlite3.Row) -> bool:
        # A per-fee amount that differs from total / total_cuotas by more
        # than rounding (at most half a minor unit per fee) means the
        # purchase was created with a custom amount_per_fee (e.g. interest).
        total_cuotas = purchase["total_cuotas"]
        diferencia = abs(purchase["monto_por_cuota_minor"] * total_cuotas - purchase["monto_total_minor"])
        return diferencia <= total_cuotas

    def _require_default_split(self, purchase: sqlite3.Row, accion: str) -> None:
        # Recalculating the split of a custom per-fee amount would silently drop it.
        if not self._is_default_split(purchase):
            raise FeesError(
                f"Purchase id={purchase['id']} was created with a custom per-fee amount — "
                f"{accion} automatically."
            )

    @staticmethod
    def _rescale_minor(minor: int, decimales_desde: int, decimales_hasta: int) -> int:
        """
        Same displayed amount expressed with other decimals:
        300000 (2 dec, 3000.00) → 3000 (0 dec) / → 300000000000 (8 dec).
        Exact when gaining decimals; rounded when losing them.
        """
        if decimales_hasta >= decimales_desde:
            return minor * 10 ** (decimales_hasta - decimales_desde)
        return round(minor / 10 ** (decimales_desde - decimales_hasta))

    def _regenerate_fees(
        self,
        purchase_id:       str,
        fecha_compra:      str,
        cuotas_actuales:   list[sqlite3.Row],
        total_cuotas:      int,
        monto_cuota_minor: int,
        conn:              sqlite3.Connection,
    ) -> int:
        """
        Deletes every fee of the purchase and recreates the schedule,
        starting on the month fee #1 has now (the purchase month if it has
        no fees). Returns the new fee count.
        """
        primera = min(cuotas_actuales, key=lambda c: c["numero_cuota"], default=None)
        if primera is not None:
            first_month, first_year = primera["mes_proyectado"], primera["anio_proyectado"]
        else:
            first_month, first_year = int(fecha_compra[5:7]), int(fecha_compra[:4])
        self._cuotas_repo.eliminar_por_compra(purchase_id, conn=conn)
        ids = self._cuotas_repo.crear_lote(
            compra_id=purchase_id,
            cuotas=self._build_fee_schedule(first_month, first_year, total_cuotas, monto_cuota_minor),
            conn=conn,
        )
        return len(ids)

    # ----------------------------------------------------------
    # DELETE PURCHASE (early-correction window, CLAUDE.md §4)
    # ----------------------------------------------------------

    def delete_purchase(self, purchase_id: str) -> FeesResult:
        """
        Physically deletes a purchase and every one of its fees — only while
        nothing generated from it has state of its own (CLAUDE.md §4): the
        typical case is a purchase loaded by mistake a few days ago. Fees
        'pendiente' or 'omitido' don't block it (a purchase cancelled by
        mistake can still be deleted afterwards); a fee already
        'en_resumen' or 'pagado' does — that purchase belongs to a
        statement and has to go through cancel_purchase() instead.

        Also blocked while a shared expense points to the purchase (as a
        whole or to any fee) or a debt was generated from it
        (deudas.origen_tipo='compra_cuotas'): those rows depend on it.

        Args:
            purchase_id: The purchase to delete.

        Returns:
            FeesResult with the count of fees deleted.

        Raises:
            PurchaseNotFoundError if not found.
            FeesError if a fee is already in a statement or paid, the
                purchase or a fee is shared, or a debt points to it.
        """
        self._get_purchase(purchase_id)
        cuotas = self._cuotas_repo.listar_por_compra(purchase_id)
        accion = "it cannot be deleted; cancel it instead"

        procesadas = [c for c in cuotas if c["estado"] in ("en_resumen", "pagado")]
        if procesadas:
            raise FeesError(
                f"Purchase id={purchase_id} has {len(procesadas)} fee(s) already in a "
                f"statement or paid — {accion}."
            )
        self._require_purchase_not_shared(purchase_id, accion)
        self._require_fees_not_shared(purchase_id, cuotas, accion)
        if self._deudas_repo.listar_por_origen("compra_cuotas", purchase_id):
            raise FeesError(f"Purchase id={purchase_id} has a debt linked to it — {accion}.")

        conn = self._db.conn
        with self._db.transaction():
            fees_deleted = self._cuotas_repo.eliminar_por_compra(purchase_id, conn=conn)
            self._compras_repo.eliminar(purchase_id, conn=conn)

        return FeesResult(
            success=True,
            entity_id=purchase_id,
            data={"fees_deleted": fees_deleted},
            message=f"Purchase #{purchase_id} deleted ({fees_deleted} fee(s)).",
        )

    # ----------------------------------------------------------
    # CANCEL PURCHASE
    # ----------------------------------------------------------

    def cancel_purchase(self, purchase_id: str, notes: Optional[str] = None) -> FeesResult:
        """
        Cancels a purchase and marks all its pending fees as 'omitido'.
        Only fees still in 'pendiente' state are affected — fees already
        in a statement (en_resumen / pagado) are left untouched.

        Args:
            purchase_id: The purchase to cancel.
            notes:       Optional reason for cancellation.

        Returns:
            FeesResult with the count of fees cancelled.

        Raises:
            PurchaseNotFoundError if not found.
            FeesError if already cancelled or completed.
        """
        purchase = self._get_purchase(purchase_id)
        if purchase["estado"] in ("cancelada", "completada"):
            raise FeesError(
                f"Purchase id={purchase_id} is already '{purchase['estado']}'."
            )

        conn = self._db.conn
        with self._db.transaction():
            fees_cancelled = self._cuotas_repo.marcar_estado_por_compra(
                purchase_id, "pendiente", "omitido", notas=notes, conn=conn,
            )
            self._compras_repo.cancelar(purchase_id, notas=notes, conn=conn)

        return FeesResult(
            success=True,
            entity_id=purchase_id,
            data={"fees_cancelled": fees_cancelled},
            message=(
                f"Purchase #{purchase_id} cancelled. "
                f"{fees_cancelled} pending fee(s) marked as omitido."
            ),
        )

    # ----------------------------------------------------------
    # RESCHEDULE FEES ("Editar cronograma")
    # ----------------------------------------------------------

    def reschedule_fees(self, purchase_id: str, new_periods: dict[int, tuple[int, int]]) -> FeesResult:
        """
        Moves fees of a purchase to other months by hand:
        new_periods = {fee_id: (month, year)}. A fee passed with the month it
        already has is ignored.

        Rules (early-correction window, CLAUDE.md §4):
        - only 'pendiente' fees can move (en_resumen / pagado / omitido have
          state of their own);
        - a fee shared on its own (a gastos_compartidos row per fee, dated on
          the fee's month) cannot move — delete the shared expense first,
          same rule as moving the purchase date to another month;
        - no two fees of the purchase may end up in the same month.

        Returns:
            FeesResult with data["fees_moved"]; success=False if no fee
            changes month.

        Raises:
            PurchaseNotFoundError if the purchase does not exist.
            FeeNotFoundError if an id is not a fee of this purchase.
            FeesError for an invalid month/year, a fee that is not pending
                or is shared, or two fees in the same month.
        """
        self._get_purchase(purchase_id)
        cuotas = self._cuotas_repo.listar_por_compra(purchase_id)
        por_id = {c["id"]: c for c in cuotas}

        cambios: dict[int, tuple[int, int]] = {}
        for fee_id, periodo in new_periods.items():
            cuota = por_id.get(fee_id)
            if cuota is None:
                raise FeeNotFoundError(f"Fee id={fee_id} is not a fee of purchase id={purchase_id}.")
            month, year = periodo
            month, year = self._validate_period(month, year, f"Fee #{cuota['numero_cuota']}")
            if (month, year) == (cuota["mes_proyectado"], cuota["anio_proyectado"]):
                continue
            if cuota["estado"] != "pendiente":
                raise FeesError(
                    f"Fee #{cuota['numero_cuota']} of purchase id={purchase_id} is '{cuota['estado']}' — "
                    f"only pending fees can be moved."
                )
            cambios[fee_id] = (month, year)

        if not cambios:
            return FeesResult(
                success=False, entity_id=purchase_id, data={"fees_moved": 0}, message="No fee changes month.",
            )
        self._require_fees_not_shared(purchase_id, [por_id[i] for i in cambios], "its fees can no longer be moved")

        ocupados: dict[tuple[int, int], int] = {}
        for cuota in cuotas:
            month, year = cambios.get(cuota["id"], (cuota["mes_proyectado"], cuota["anio_proyectado"]))
            if (year, month) in ocupados:
                raise FeesError(
                    f"Fees #{ocupados[(year, month)]} and #{cuota['numero_cuota']} of purchase "
                    f"id={purchase_id} would both fall on {month:02d}/{year}."
                )
            ocupados[(year, month)] = cuota["numero_cuota"]

        conn = self._db.conn
        with self._db.transaction():
            for fee_id, (month, year) in cambios.items():
                self._cuotas_repo.actualizar_periodo(fee_id, month, year, conn=conn)

        return FeesResult(
            success=True,
            entity_id=purchase_id,
            data={"fees_moved": len(cambios)},
            message=f"{len(cambios)} fee(s) of purchase #{purchase_id} moved.",
        )

    # ----------------------------------------------------------
    # CARD CONFIG (tarjetas_config: closing / due day of each card)
    # ----------------------------------------------------------

    @staticmethod
    def _day_in_month(day: int, month: int, year: int) -> date:
        """That day of the month, or the month's last day when it is shorter (31 → 30, 29 → 28…)."""
        return date(year, month, min(day, calendar.monthrange(year, month)[1]))

    def _due_date(self, closing: date, closing_day: int, due_day: int) -> date:
        """
        Due date of the statement that closes on `closing`: due_day of the
        same month, or of the next one when due_day <= closing_day (it can
        never be due before — or on — the day it closes).
        """
        month, year = closing.month, closing.year
        if due_day <= closing_day:
            month, year = self._add_months(month, year, 1)
        return self._day_in_month(due_day, month, year)

    def get_card_config(self, account_id: str) -> Optional[dict]:
        """{"cuenta_id", "dia_cierre", "dia_vencimiento", ...} of a card, or None if it has no config yet."""
        fila = self._tarjetas_repo.obtener(account_id)
        return dict(fila) if fila is not None else None

    def set_card_config(self, account_id: str, closing_day: int, due_day: int) -> FeesResult:
        """
        Creates or updates the closing / due day of a credit card.

        Raises:
            FeesError if the account does not exist, is inactive or is not
                a credit card, or a day is not an integer from 1 to 31.
        """
        cuenta = self._get_account(account_id)
        if cuenta["tipo"] != "credito":
            raise FeesError(f"Account id={account_id} is not a credit card.")
        for nombre, dia in (("closing_day", closing_day), ("due_day", due_day)):
            if not isinstance(dia, int) or isinstance(dia, bool) or not 1 <= dia <= 31:
                raise FeesError(f"{nombre} must be an integer from 1 to 31. Received: {dia!r}.")
        self._tarjetas_repo.upsert(account_id, closing_day, due_day)
        return FeesResult(
            success=True,
            entity_id=account_id,
            data={"dia_cierre": closing_day, "dia_vencimiento": due_day},
            message=f"Card #{account_id}: closes on day {closing_day}, due on day {due_day}.",
        )

    def card_cycle_dates(self, account_id: str, today: Optional[Any] = None) -> Optional[dict]:
        """
        Previous / current / next statement of a card, from its config:
            {"dia_cierre": 15, "dia_vencimiento": 5,
             "anterior": {"cierre": "2026-08-15", "vencimiento": "2026-09-05"},
             "actual":   {"cierre": "2026-09-15", "vencimiento": "2026-10-05"},
             "proximo":  {"cierre": "2026-10-15", "vencimiento": "2026-11-05"}}
        "actual" is the statement that closed most recently (closing date
        <= today), "anterior" the one before it, "proximo" the one that
        closes next. Closing = dia_cierre of each month (the month's last day
        when it is shorter); due date: see _due_date(). None if the card has
        no config.

        Args:
            today: 'YYYY-MM-DD' or a date; None = today.

        Raises:
            ValueError for an invalid `today`.
        """
        config = self._tarjetas_repo.obtener(account_id)
        if config is None:
            return None
        hoy = date.fromisoformat(self._validate_date(today)) if today is not None else date.today()
        dia_cierre, dia_vencimiento = config["dia_cierre"], config["dia_vencimiento"]
        month, year = hoy.month, hoy.year
        if self._day_in_month(dia_cierre, month, year) > hoy:
            month, year = self._add_months(month, year, -1)  # this month's statement has not closed yet

        def _resumen(delta: int) -> dict:
            m, a = self._add_months(month, year, delta)
            cierre = self._day_in_month(dia_cierre, m, a)
            return {
                "cierre": cierre.isoformat(),
                "vencimiento": self._due_date(cierre, dia_cierre, dia_vencimiento).isoformat(),
            }

        return {
            "dia_cierre": dia_cierre,
            "dia_vencimiento": dia_vencimiento,
            "anterior": _resumen(-1),
            "actual": _resumen(0),
            "proximo": _resumen(1),
        }

    def suggest_first_fee(self, account_id: Optional[str], date_str: Any) -> tuple[int, int]:
        """
        Suggested (month, year) of fee #1 for a purchase made on date_str:
        the month after the purchase; two months after when the card has a
        closing day configured and the purchase is after it that month (it
        goes into the next statement). account_id None (no card chosen yet)
        or a card without config → the month after.

        Raises:
            ValueError for an invalid date.
        """
        compra = date.fromisoformat(self._validate_date(date_str))
        meses = 1
        config = self._tarjetas_repo.obtener(account_id) if account_id is not None else None
        if config is not None and compra > self._day_in_month(config["dia_cierre"], compra.month, compra.year):
            meses = 2
        return self._add_months(compra.month, compra.year, meses)

