"""
DeltaBalance — services/accounts_service.py

Purpose:
    Domain service for accounts (cuentas). Owns the business rules around
    creating/updating/archiving/deleting accounts and computing balances —
    data access lives entirely in CuentasRepository.

    Owns (via CuentasRepository) these tables:
        - cuentas
        - cuentas_saldos

    No MonedasRepository exists yet in this codebase (mismo estado que en
    transaction_service.py/fees_service.py/ingresos_proyectados_service.py,
    que también consultan `monedas` directo) — este service hace lo mismo.

    Key concepts:
        - tipo es estructural: se fija al crear la cuenta y no puede cambiar
          después.
        - Una cuenta puede operar en más de una moneda (ver
          docs/DATA_MODEL_DECISIONS.md sección 14) — schema.sql ya lo
          soportaba desde el diseño original vía cuentas_saldos
          (PK (cuenta_id, moneda_id)), esto solo lo expone desde el
          service. Declarar moneda al crear la cuenta ya NO es obligatorio
          (Tarea 6f, docs/PROXIMOS_PASOS.md): create_account() puede
          crearse sin ninguna, y cada combinación cuenta+moneda se resuelve
          sola (fila en cuentas_saldos con saldo_inicial_minor = 0) la
          primera vez que una transacción real la usa, vía
          TransaccionesRepository.crear() ->
          CuentasRepository.get_or_create_saldo_inicial(). El set de
          monedas de una cuenta (declaradas de antemano o resueltas solas)
          después solo puede CRECER, nunca reducirse — quitar una moneda
          implicaría decidir qué hacer con su historial de transacciones,
          fuera de alcance por ahora.
        - cuenta_pago_id vincula una cuenta (ej. una tarjeta de crédito) a
          la cuenta desde la que efectivamente se paga (ej. la caja de
          ahorro asociada). Debe apuntar a una cuenta existente y nunca a
          sí misma.
        - Una cuenta solo puede archivarse (soft-delete) con saldo 0 en
          TODAS sus monedas — así nunca se "pierde de vista" plata sin
          querer.
        - Una cuenta solo puede eliminarse (DELETE físico) si nunca tuvo
          actividad real: sin transacciones (ni siquiera soft-deleted, ver
          delete_account()), sin saldo inicial cargado en ninguna moneda, y
          sin otras cuentas que dependan de ella como cuenta_pago_id. Es la
          ventana de corrección temprana de CLAUDE.md §4 aplicada a
          cuentas: "la creé mal, todavía no pasó nada" se borra de verdad;
          todo lo demás (con actividad real) se archiva, nunca se borra.
"""

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Optional

from db.database import DatabaseManager, to_minor
from repositories.cuentas_repository import CuentasRepository
from repositories._sentinels import NO_CAMBIAR

_COLOR_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


# =============================================================
# EXCEPTIONS
# =============================================================

class AccountsError(Exception):
    """Raised when an account operation violates a business rule."""


class AccountNotFoundError(AccountsError):
    """Raised when a referenced account does not exist."""


class CurrencyNotFoundError(AccountsError):
    """
    Raised when a referenced currency does not exist.

    Definida propia de este módulo (no importada de otro service) — mismo
    razonamiento que en ingresos_proyectados_service.py/presupuestos_service.py:
    acoplar este dominio a otro por algo tan básico como reportar un error
    violaría alta cohesión / bajo acoplamiento (CLAUDE.md §1).
    """


class ParentAccountNotFoundError(AccountsError):
    """Raised when cuenta_pago_id does not reference an existing account."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class AccountsResult:
    """
    Structured result returned by AccountsService write operations.
    Avoids forcing callers to make a second DB call to read what was just
    written.
    """
    success:    bool
    account_id: Optional[int] = None
    data:       dict          = field(default_factory=dict)
    message:    str           = ""


# =============================================================
# SERVICE
# =============================================================

class AccountsService:
    """
    Entry point for all account operations in DeltaBalance.

    Usage:
        db  = DatabaseManager()
        svc = AccountsService(db)

        # Cuenta sin moneda declarada — la más común desde la Tarea 6f: se
        # resuelve sola la primera vez que una transacción real la usa.
        svc.create_account(nombre="Broker nuevo", tipo="inversion")

        # Cuenta que ya sabemos de antemano que opera en ARS y USD
        result = svc.create_account(
            nombre="Banco Galicia - Caja de ahorro",
            tipo="debito",
            monedas=[moneda_ars_id, moneda_usd_id],
        )

        # Tarjeta de crédito vinculada a la cuenta de origen
        svc.create_account(
            nombre="Galicia Visa",
            tipo="credito",
            monedas=[moneda_ars_id],
            cuenta_pago_id=result.account_id,
        )

        # Esa tarjeta empieza a operar también en USD más adelante
        svc.add_currency_to_account(result.account_id, moneda_usd_id)
    """

    TIPOS_VALIDOS = ("debito", "credito", "efectivo", "crypto", "inversion")

    def __init__(self, db: DatabaseManager):
        """
        Initializes the service with an active DatabaseManager instance.

        Args:
            db: An initialized DatabaseManager. Schema + seed must already be applied.
        """
        self._db = db
        self._repo = CuentasRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_cuenta(self, account_id: int) -> sqlite3.Row:
        row = self._repo.obtener_por_id(account_id)
        if row is None:
            raise AccountNotFoundError(f"Account id={account_id} not found.")
        return row

    def _get_currency(self, currency_id: int) -> sqlite3.Row:
        """No hay MonedasRepository — se valida contra la tabla directo."""
        row = self._db.fetchone("SELECT * FROM monedas WHERE id = ?;", (currency_id,))
        if row is None:
            raise CurrencyNotFoundError(f"Currency id={currency_id} not found.")
        return row

    def list_currencies(self) -> list[sqlite3.Row]:
        """
        Lists every available currency. No MonedasRepository exists yet
        (mismo estado que _get_currency() arriba) — se expone acá para que
        la UI pueda poblar un selector de moneda sin tocar db/ directo
        (CLAUDE.md §2: ui/ solo puede depender del motor de datos a través
        de un service).
        """
        return self._db.fetchall("SELECT * FROM monedas ORDER BY codigo;")

    def _validate_tipo(self, tipo: str) -> str:
        normalizado = tipo.lower().strip()
        if normalizado not in self.TIPOS_VALIDOS:
            raise ValueError(
                f"Invalid account type '{tipo}'. "
                f"Must be one of: {', '.join(self.TIPOS_VALIDOS)}."
            )
        return normalizado

    def _validate_color_hex(self, color_hex: str) -> str:
        if not _COLOR_HEX_RE.match(color_hex):
            raise ValueError(
                f"Invalid color_hex '{color_hex}'. Must match '#RRGGBB' (e.g. '#5F5E5A')."
            )
        return color_hex

    def _validate_cuenta_pago(self, cuenta_pago_id: Optional[int], propio_id: Optional[int] = None) -> None:
        """
        Valida cuenta_pago_id: debe existir y no puede ser la propia cuenta
        (propio_id es None en create_account, donde la auto-referencia
        todavía no es alcanzable porque la cuenta no existe; se vuelve
        alcanzable en update_account, donde propio_id es el id de la
        cuenta que se está editando).
        """
        if cuenta_pago_id is None:
            return
        if propio_id is not None and cuenta_pago_id == propio_id:
            raise ValueError(
                f"cuenta_pago_id no puede ser la propia cuenta (id={propio_id})."
            )
        if self._repo.obtener_por_id(cuenta_pago_id) is None:
            raise ParentAccountNotFoundError(
                f"Parent account id={cuenta_pago_id} not found."
            )

    def _enrich(self, row: sqlite3.Row) -> dict:
        """
        Agrega la lista de saldos (uno por moneda operativa de la cuenta,
        cada uno con moneda_id/moneda_codigo/moneda_simbolo/saldo/saldo_minor)
        a una fila de cuenta. Una cuenta puede operar en más de una moneda
        (ver docs/DATA_MODEL_DECISIONS.md sección 14) — por eso "saldos" es
        una lista, nunca un único saldo/moneda_codigo sueltos.
        """
        data = dict(row)
        saldos = []
        for fila in self._repo.listar_saldos(row["id"]):
            saldo = self._repo.obtener_saldo(row["id"], fila["codigo"])
            saldos.append({
                "moneda_id":      fila["moneda_id"],
                "moneda_codigo":  fila["codigo"],
                "moneda_simbolo": fila["simbolo"],
                "saldo":          saldo,
                "saldo_minor":    to_minor(saldo, fila["decimales"]),
            })
        data["saldos"] = saldos
        return data

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def create_account(
        self,
        nombre: str,
        tipo: str,
        monedas: Optional[list[int]] = None,
        cuenta_pago_id: Optional[int] = None,
        notas: Optional[str] = None,
        color_hex: Optional[str] = None,
    ) -> AccountsResult:
        """
        Creates a new account and a cuentas_saldos row (saldo 0) for every
        moneda_id in `monedas`. Atomic: the account and every saldo row are
        written in a single transaction — either all of it lands, or none
        of it does.

        Args:
            nombre:         Account name. Must be unique (schema UNIQUE).
            tipo:           One of 'debito', 'credito', 'efectivo', 'crypto', 'inversion'.
            monedas:        Optional list of moneda_id the account already
                            knows it operates in. Default None/empty: the
                            account is created without declaring any
                            currency yet — no cuentas_saldos row is created
                            at all (Tarea 6f, docs/PROXIMOS_PASOS.md:
                            declarar moneda al crear ya no es obligatorio,
                            se resuelve sola la primera vez que una
                            transacción real usa la cuenta, vía
                            TransaccionesRepository.crear() ->
                            CuentasRepository.get_or_create_saldo_inicial()).
                            If a non-empty list is passed, behavior is
                            unchanged from before: no duplicates, and it's
                            still structural — see class docstring: can only
                            grow later, via add_currency_to_account().
            cuenta_pago_id: Optional parent account (e.g. a credit card's origin
                            account). Must exist and cannot reference itself.
            notas:          Optional free-text notes.
            color_hex:      Optional '#RRGGBB' color for the account (used by
                            the UI to color-code it, e.g. in the transactions
                            table). If not passed, the column keeps its schema
                            default ('#5F5E5A', see db/schema_migrations.py) —
                            not hardcoded again here.

        Returns:
            AccountsResult with the new account's id and its enriched data
            (data["saldos"] has one entry per moneda_id passed in, or is
            empty if monedas was not passed).

        Raises:
            AccountsError if nombre is empty.
            ValueError if tipo is invalid, monedas has duplicates, or
                       color_hex doesn't match '#RRGGBB'.
            CurrencyNotFoundError if any moneda_id does not exist.
            ParentAccountNotFoundError if cuenta_pago_id does not exist.
        """
        monedas = monedas or []
        if not nombre or not nombre.strip():
            raise AccountsError("El nombre de la cuenta no puede estar vacío.")
        if len(monedas) != len(set(monedas)):
            raise ValueError("La lista de monedas no puede tener ids repetidos.")

        tipo_validado = self._validate_tipo(tipo)
        monedas_rows = [self._get_currency(m) for m in monedas]  # valida existencia de cada una
        self._validate_cuenta_pago(cuenta_pago_id)
        if color_hex is not None:
            color_hex = self._validate_color_hex(color_hex)

        with self._db.transaction() as conn:
            account_id = self._repo.crear(
                nombre=nombre.strip(),
                tipo=tipo_validado,
                moneda_codigo=monedas_rows[0]["codigo"] if monedas_rows else None,
                saldo_inicial=0.0,
                cuenta_pago_id=cuenta_pago_id,
                notas=notas,
                color_hex=color_hex,
                conn=conn,
            )
            for moneda in monedas_rows[1:]:
                self._repo.crear_saldo_inicial(account_id, moneda["id"], monto_minor=0, conn=conn)

        return AccountsResult(
            success=True,
            account_id=account_id,
            data=self.get_account(account_id),
            message=f"Cuenta '{nombre.strip()}' creada.",
        )

    def add_currency_to_account(self, account_id: int, moneda_id: int) -> AccountsResult:
        """
        Adds a new currency to an already-existing account (e.g. "this card
        now also operates in USD"), with saldo starting at 0.

        Args:
            account_id: The account to extend.
            moneda_id:  The currency to add.

        Returns:
            AccountsResult with the account's updated enriched data.

        Raises:
            AccountNotFoundError if the account does not exist.
            CurrencyNotFoundError if moneda_id does not exist.
            AccountsError if the account already operates in that currency.
        """
        self._get_cuenta(account_id)
        moneda = self._get_currency(moneda_id)

        ya_existe = any(
            fila["moneda_id"] == moneda_id for fila in self._repo.listar_saldos(account_id)
        )
        if ya_existe:
            raise AccountsError(
                f"La cuenta #{account_id} ya opera en {moneda['codigo']}."
            )

        self._repo.crear_saldo_inicial(account_id, moneda_id, monto_minor=0)

        return AccountsResult(
            success=True,
            account_id=account_id,
            data=self.get_account(account_id),
            message=f"Moneda {moneda['codigo']} agregada a la cuenta #{account_id}.",
        )

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def get_account(self, account_id: int) -> dict:
        """
        Fetches a single account, enriched with its list of saldos (one per
        moneda operativa — ver _enrich()).

        Raises:
            AccountNotFoundError if the account does not exist.
        """
        row = self._get_cuenta(account_id)
        return self._enrich(row)

    def list_accounts(self, solo_activas: bool = True) -> list[dict]:
        """
        Lists accounts, each enriched with its list of saldos.

        Args:
            solo_activas: If True (default), excludes archived accounts.

        Returns:
            List of dicts ordered by nombre (per CuentasRepository.listar()).
        """
        return [self._enrich(row) for row in self._repo.listar(solo_activas=solo_activas)]

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def update_account(
        self,
        account_id: int,
        nombre: Any = NO_CAMBIAR,
        notas: Any = NO_CAMBIAR,
        cuenta_pago_id: Any = NO_CAMBIAR,
        color_hex: Any = NO_CAMBIAR,
        **campos_no_permitidos: Any,
    ) -> AccountsResult:
        """
        Updates editable fields of an account. Default NO_CAMBIAR = no tocar
        ese campo (repositories/_sentinels.py).

        tipo es estructural y NO se puede cambiar una vez creada la cuenta
        (cambiarlo rompería el sentido de los saldos históricos) — pasarlo
        por cualquier vía levanta ValueError en vez de aplicarse en
        silencio. Las monedas de una cuenta tampoco se cambian acá: solo
        pueden crecer, vía add_currency_to_account().

        Args:
            account_id:     The account to update.
            nombre:         New name. NO_CAMBIAR = no change.
            notas:          New notes. Pass '' to clear. NO_CAMBIAR = no change.
            cuenta_pago_id: New parent account id. Must exist and cannot
                            reference the account itself. NO_CAMBIAR = no
                            change. Unlinking (setting it back to NULL) is
                            NOT supported by this method — CuentasRepository.
                            actualizar() treats None as "don't touch this
                            field" for every column (same convention as
                            nombre/tipo/notas/activa there), so it can't
                            distinguish "clear cuenta_pago_id" from "leave
                            it alone". Passing None here raises ValueError
                            instead of silently doing nothing. TODO: if
                            unlinking is needed later, CuentasRepository.
                            actualizar() needs to switch cuenta_pago_id to
                            the NO_CAMBIAR sentinel convention first (see
                            repositories/ingresos_proyectados_repository.py
                            for the pattern already used elsewhere).
            color_hex:      New '#RRGGBB' color. NO_CAMBIAR = no change.
                            Passing None raises ValueError (same reasoning as
                            cuenta_pago_id above — the repo can't tell "clear"
                            from "leave alone" via None either — but unlike
                            cuenta_pago_id, color_hex has no legitimate NULL
                            state to begin with, so this just avoids a raw
                            TypeError leaking out of the regex check).

        Returns:
            AccountsResult with success=True if at least one field changed.

        Raises:
            AccountNotFoundError if the account does not exist.
            ValueError if 'tipo' or 'monedas' are passed, if nombre is
                       empty, if cuenta_pago_id or color_hex is None, if
                       cuenta_pago_id references the account itself, or if
                       color_hex doesn't match '#RRGGBB'.
            ParentAccountNotFoundError if cuenta_pago_id does not exist.
        """
        if "tipo" in campos_no_permitidos or "monedas" in campos_no_permitidos or "moneda_id" in campos_no_permitidos:
            raise ValueError(
                "No se puede cambiar 'tipo' ni las monedas de una cuenta ya "
                "creada con update_account(): 'tipo' es estructural, y las "
                "monedas solo pueden crecer vía add_currency_to_account()."
            )
        if campos_no_permitidos:
            raise TypeError(f"Argumentos desconocidos: {', '.join(campos_no_permitidos)}.")

        self._get_cuenta(account_id)  # validate existence

        campos_repo: dict = {}

        if nombre is not NO_CAMBIAR:
            if not nombre or not nombre.strip():
                raise AccountsError("El nombre de la cuenta no puede estar vacío.")
            campos_repo["nombre"] = nombre.strip()

        if notas is not NO_CAMBIAR:
            campos_repo["notas"] = notas

        if cuenta_pago_id is not NO_CAMBIAR:
            if cuenta_pago_id is None:
                raise ValueError(
                    "No se puede desvincular cuenta_pago_id (poner en NULL) "
                    "con este método hoy — ver docstring de update_account()."
                )
            self._validate_cuenta_pago(cuenta_pago_id, propio_id=account_id)
            campos_repo["cuenta_pago_id"] = cuenta_pago_id

        if color_hex is not NO_CAMBIAR:
            if color_hex is None:
                raise ValueError("color_hex no puede ser None — pasá un '#RRGGBB' válido.")
            campos_repo["color_hex"] = self._validate_color_hex(color_hex)

        if not campos_repo:
            return AccountsResult(
                success=False,
                account_id=account_id,
                message="No fields to update were provided.",
            )

        self._repo.actualizar(account_id, **campos_repo)

        return AccountsResult(
            success=True,
            account_id=account_id,
            data=self.get_account(account_id),
            message=f"Cuenta #{account_id} actualizada ({len(campos_repo)} campo(s) modificado(s)).",
        )

    # ----------------------------------------------------------
    # ARCHIVE
    # ----------------------------------------------------------

    def archive_account(self, account_id: int) -> AccountsResult:
        """
        Archives (soft-deletes) an account. Only allowed when its current
        balance is exactly 0 in EVERY currency it operates in — otherwise
        plata quedaría "perdida de vista" sin querer.

        Args:
            account_id: The account to archive.

        Returns:
            AccountsResult with success=True.

        Raises:
            AccountNotFoundError if the account does not exist.
            AccountsError if any of the account's balances is not 0.
        """
        cuenta = self._enrich(self._get_cuenta(account_id))

        saldos_no_cero = [s for s in cuenta["saldos"] if s["saldo_minor"] != 0]
        if saldos_no_cero:
            detalle = ", ".join(f"{s['saldo']} {s['moneda_codigo']}" for s in saldos_no_cero)
            raise AccountsError(
                f"No se puede archivar la cuenta #{account_id} ('{cuenta['nombre']}'): "
                f"tiene saldo {detalle}. Movés o justificás el saldo antes de archivarla."
            )

        self._repo.archivar(account_id)

        return AccountsResult(
            success=True,
            account_id=account_id,
            message=f"Cuenta #{account_id} ('{cuenta['nombre']}') archivada.",
        )

    # ----------------------------------------------------------
    # DELETE (físico — ventana de corrección temprana, CLAUDE.md §4)
    # ----------------------------------------------------------

    def delete_account(self, account_id: int) -> AccountsResult:
        """
        Physically deletes an account and its cuentas_saldos rows. Only
        allowed when the account never had any real activity:

        1. Zero rows in `transacciones` reference it — including logically
           deleted ones (transacciones.deleted_at IS NOT NULL still counts:
           it means the account *had* activity at some point, even if that
           transaction was later soft-deleted).
        2. Zero cuentas_saldos rows with saldo_inicial_minor != 0.
        3. No other account references it as cuenta_pago_id (deleting it
           would orphan whatever card depends on it as its payment source).

        If any of those fails, raises AccountsError explaining exactly
        which one — "tiene movimientos, archivala en vez de borrarla" or
        equivalent. This is the early-correction window (CLAUDE.md §4): an
        account that never had real activity can be undone outright; one
        that did gets archived instead, never silently rewritten.

        Args:
            account_id: The account to delete.

        Returns:
            AccountsResult with success=True.

        Raises:
            AccountNotFoundError if the account does not exist.
            AccountsError if any of the three conditions above is not met.
        """
        cuenta = self._get_cuenta(account_id)

        total_transacciones = self._db.fetchone(
            "SELECT COUNT(*) AS n FROM transacciones WHERE cuenta_id = ?;", (account_id,)
        )["n"]
        if total_transacciones > 0:
            raise AccountsError(
                f"La cuenta #{account_id} ('{cuenta['nombre']}') tiene movimientos "
                f"asociados (incluidos los eliminados lógicamente): no se puede "
                f"eliminar, archivala en su lugar."
            )

        saldos = self._repo.listar_saldos(account_id)
        if any(s["saldo_inicial_minor"] != 0 for s in saldos):
            raise AccountsError(
                f"La cuenta #{account_id} ('{cuenta['nombre']}') tiene saldo inicial "
                f"distinto de 0 en alguna moneda: no se puede eliminar, archivala "
                f"en su lugar."
            )

        dependientes = self._repo.contar_dependientes(account_id)
        if dependientes > 0:
            raise AccountsError(
                f"La cuenta #{account_id} ('{cuenta['nombre']}') es la cuenta de "
                f"pago de otra(s) cuenta(s) (ej. una tarjeta): no se puede eliminar "
                f"mientras dependan de ella."
            )

        with self._db.transaction() as conn:
            self._repo.eliminar_saldos(account_id, conn=conn)
            self._repo.eliminar(account_id, conn=conn)

        return AccountsResult(
            success=True,
            account_id=account_id,
            message=f"Cuenta #{account_id} ('{cuenta['nombre']}') eliminada.",
        )

    # ----------------------------------------------------------
    # TOTAL BALANCE (dashboard "patrimonio total")
    # ----------------------------------------------------------

    def get_total_balance(self) -> dict:
        """
        Sums the balance of every active account, grouped by currency code
        (ARS and USD, for example, are never mixed into one sum). A
        multi-currency account contributes to every currency it operates in.

        Returns:
            Dict {moneda_codigo: total_minor}, e.g. {"ARS": 1234500, "USD": 20000}.
            Currencies with no active balance are simply absent from the dict.
        """
        totales: dict[str, int] = {}
        for cuenta in self.list_accounts(solo_activas=True):
            for saldo in cuenta["saldos"]:
                codigo = saldo["moneda_codigo"]
                totales[codigo] = totales.get(codigo, 0) + saldo["saldo_minor"]
        return totales
