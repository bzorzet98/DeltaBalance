"""
DeltaBalance — services/debts_service.py

Deudas informales como LIBRO DE MOVIMIENTOS en dos tabs (estructura final,
docs/DATA_MODEL_DECISIONS.md sección 22):
    'me_deben'  lo que te deben (le prestaste, pagaste algo por esa persona)
    'debo'      lo que debés (te prestaron, alguien pagó algo por vos)
Cada fila tiene monto_minor CON SIGNO: positivo = entrada (la deuda
crece), negativo = salida (un pago que la baja). El saldo de una persona en
un tab es la suma de sus filas: en 'me_deben', positivo = todavía te debe y
negativo = te pagó de más; en 'debo', positivo = todavía le debés y
negativo = le pagaste de más. No hay pendiente, estado ni pagos aparte:
registrar un pago es crear una fila negativa en el mismo tab.
register_payment()/write_off()/mark_uncollectable()/apply_payment() ya no
existen.

Reglas de dominio (acá, no en el repositorio):
- La persona se normaliza (utils/personas.py): "Noe" y "NOE" son la misma.
- tab 'me_deben' o 'debo'; monto_minor entero distinto de 0 (el signo se
  guarda tal cual); fecha 'YYYY-MM-DD'; moneda_id tiene que existir.
- Edición y borrado directos: una fila no tiene dependencias con estado
  propio (ventana de corrección temprana, CLAUDE.md §4 — sin dependencias,
  sin fricción).
- Un doble-click que crearía una fila idéntica (DeudaDuplicadaError del
  repositorio) sube como DebtError.

Reglas de arquitectura: solo DeudasRepository (y db.obtener_monedas() para
validar la moneda) — sin SQL directo.
"""

import calendar
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._sentinels import NO_CAMBIAR
# TABS también lo importan las pantallas desde acá (ui/ no importa de repositories/).
from repositories.deudas_repository import TABS, DeudaDuplicadaError, DeudasRepository
from utils.personas import normalizar_persona

# update(): campos que se pueden cambiar.
CAMPOS_EDITABLES = ("entidad_persona", "concepto", "tab", "monto_minor", "moneda_id", "fecha", "notas", "tag")


# =============================================================
# EXCEPTIONS
# =============================================================

class DebtError(Exception):
    """Raised when a debt operation violates a business rule."""


class DebtNotFoundError(DebtError):
    """Raised when a referenced debt row does not exist."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class DebtResult:
    success: bool
    entity_id: Optional[str] = None
    message: str = ""


# =============================================================
# SERVICE
# =============================================================

class DebtsService:
    """
    Usage:
        svc = DebtsService(db)
        svc.create("Noe", "Préstamo", "me_deben", 4000000, moneda_id=1, fecha="2026-03-01")
        svc.create("Noe", "Me devolvió una parte", "me_deben", -1000000, moneda_id=1, fecha="2026-03-15")
        svc.summary_by_person("me_deben")   # [{"entidad_persona": "NOE", "saldo_minor": 3000000, ...}]
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._repo = DeudasRepository(db)

    # ----------------------------------------------------------
    # VALIDACIONES
    # ----------------------------------------------------------

    @staticmethod
    def _persona(entidad_persona: Optional[str]) -> str:
        persona = normalizar_persona(entidad_persona)
        if not persona:
            raise DebtError("Person name cannot be empty.")
        return persona

    @staticmethod
    def _tab(tab: str) -> str:
        if tab not in TABS:
            raise DebtError(f"Invalid tab '{tab}'. Must be 'me_deben' or 'debo'.")
        return tab

    @staticmethod
    def _monto(monto_minor: int) -> int:
        """Entero distinto de 0; el signo se guarda tal cual (positivo = entrada, negativo = salida)."""
        if not isinstance(monto_minor, int) or isinstance(monto_minor, bool):
            raise DebtError(f"monto_minor must be an integer (minor units). Received: {monto_minor!r}.")
        if monto_minor == 0:
            raise DebtError("Amount cannot be 0.")
        return monto_minor

    @staticmethod
    def _fecha(fecha: str) -> str:
        try:
            datetime.strptime(fecha, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise DebtError(f"Invalid date '{fecha}'. Expected 'YYYY-MM-DD'.") from None
        return fecha

    def _moneda(self, moneda_id: int) -> int:
        if moneda_id not in {fila["id"] for fila in self._db.obtener_monedas()}:
            raise DebtError(f"Currency id={moneda_id} not found.")
        return moneda_id

    @staticmethod
    def _texto(valor: Optional[str]) -> Optional[str]:
        """concepto/notas/tag: sin espacios de más; vacío = NULL."""
        return (valor or "").strip() or None

    def _obtener(self, deuda_id: str) -> sqlite3.Row:
        fila = self._repo.obtener_por_id(deuda_id)
        if fila is None:
            raise DebtNotFoundError(f"Debt id={deuda_id} not found.")
        return fila

    @staticmethod
    def _fin_de_mes_actual() -> str:
        hoy = date.today()
        return f"{hoy.year:04d}-{hoy.month:02d}-{calendar.monthrange(hoy.year, hoy.month)[1]:02d}"

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def create(
        self,
        entidad_persona: str,
        concepto: Optional[str],
        tab: str,
        monto_minor: int,
        moneda_id: int,
        fecha: str,
        notas: Optional[str] = None,
        origen_tipo: str = "manual",
        origen_id: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> DebtResult:
        """
        Crea una fila del libro en ese tab. monto_minor positivo = la deuda
        crece; negativo = un pago que la baja.
        tag: etiqueta libre (como transacciones.tag); vacía = NULL.

        Raises:
            DebtError si la persona está vacía, el tab no es válido, el monto
            es 0 o no es entero, la fecha no es 'YYYY-MM-DD', la moneda no
            existe, o es un doble-click (fila idéntica recién creada).
        """
        persona = self._persona(entidad_persona)
        tab = self._tab(tab)
        if not origen_tipo or not origen_tipo.strip():
            raise DebtError("origen_tipo cannot be empty.")
        try:
            deuda_id = self._repo.crear(
                entidad_persona=persona,
                concepto=self._texto(concepto),
                tab=tab,
                monto_minor=self._monto(monto_minor),
                moneda_id=self._moneda(moneda_id),
                fecha=self._fecha(fecha),
                notas=self._texto(notas),
                origen_tipo=origen_tipo.strip(),
                origen_id=origen_id,
                tag=self._texto(tag),
            )
        except DeudaDuplicadaError as err:
            raise DebtError(str(err)) from err
        return DebtResult(success=True, entity_id=deuda_id, message=f"Debt #{deuda_id} ({tab}) created for '{persona}'.")

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def get(self, id: str) -> Optional[sqlite3.Row]:
        """La fila enriquecida con la moneda (currency_code, currency_symbol, decimales), o None."""
        return self._repo.obtener_por_id(id)

    def list_by_tab(self, tab: str, mes: Optional[int] = None, anio: Optional[int] = None) -> list[sqlite3.Row]:
        """
        Filas de un tab, la más nueva primero. Con mes y anio: solo las de
        ese mes; con anio solo: las de ese año; sin ninguno: todas.

        Raises:
            DebtError si el tab no es válido, mes no está entre 1 y 12, o se
            pasa mes sin anio.
        """
        tab = self._tab(tab)
        if mes is not None:
            if not isinstance(mes, int) or isinstance(mes, bool) or not 1 <= mes <= 12:
                raise DebtError(f"mes must be between 1 and 12. Received: {mes!r}.")
            if anio is None:
                raise DebtError("mes requires anio.")
        return self._repo.listar_por_tab(tab, mes, anio)

    def list_by_person(self, entidad_persona: str, tab: Optional[str] = None) -> list[sqlite3.Row]:
        """
        Filas de una persona (el nombre se normaliza igual que al guardar),
        de un tab o de los dos; la más nueva primero.

        Raises:
            DebtError si la persona está vacía o el tab no es válido.
        """
        return self._repo.listar_por_persona(self._persona(entidad_persona), self._tab(tab) if tab is not None else None)

    # ----------------------------------------------------------
    # UPDATE / DELETE
    # ----------------------------------------------------------

    def update(self, id: str, **kwargs: Any) -> DebtResult:
        """
        Update parcial: solo los campos pasados (CAMPOS_EDITABLES). Mismas
        validaciones que create(); el signo de monto_minor se guarda tal cual.

        Returns:
            DebtResult con success=False si no se pasó ningún campo.

        Raises:
            DebtNotFoundError si la fila no existe.
            DebtError si un campo no es editable o no es válido.
        """
        self._obtener(id)
        desconocidos = set(kwargs) - set(CAMPOS_EDITABLES)
        if desconocidos:
            raise DebtError(f"Fields cannot be updated: {', '.join(sorted(desconocidos))}.")
        if not kwargs:
            return DebtResult(success=False, entity_id=id, message="No fields to update were provided.")

        validadores = {
            "entidad_persona": self._persona,
            "concepto": self._texto,
            "notas": self._texto,
            "tag": self._texto,
            "tab": self._tab,
            "monto_minor": self._monto,
            "moneda_id": self._moneda,
            "fecha": self._fecha,
        }
        campos = {campo: validadores[campo](valor) for campo, valor in kwargs.items()}
        actualizado = self._repo.actualizar(id, **{k: campos.get(k, NO_CAMBIAR) for k in CAMPOS_EDITABLES})
        return DebtResult(
            success=actualizado, entity_id=id,
            message=f"Debt #{id} updated ({len(campos)} field(s))." if actualizado else f"Debt #{id}: nothing changed.",
        )

    def delete(self, id: str) -> DebtResult:
        """
        DELETE físico (sin dependencias con estado propio, CLAUDE.md §4).

        Raises:
            DebtNotFoundError si la fila no existe.
        """
        self._obtener(id)
        self._repo.eliminar(id)
        return DebtResult(success=True, entity_id=id, message=f"Debt #{id} deleted.")

    # ----------------------------------------------------------
    # SALDOS
    # ----------------------------------------------------------

    def summary_by_person(self, tab: str, hasta_fecha: Optional[str] = None) -> list[dict]:
        """
        Saldo neto por persona y moneda en ese tab, con las filas de fecha <=
        hasta_fecha (None = último día del mes actual): [{entidad_persona,
        moneda_id, saldo_minor, moneda_codigo, moneda_simbolo, decimales}],
        por persona. Incluye los saldos en 0 (saldado).

        Cómo leer saldo_minor (verde si > 0, rojo si < 0):
            'me_deben': positivo = te deben, negativo = ya te pagaron de más.
            'debo':     positivo = todavía debés, negativo = pagaste de más.

        Raises:
            DebtError si el tab no es válido o hasta_fecha no es 'YYYY-MM-DD'.
        """
        tab = self._tab(tab)
        tope = self._fecha(hasta_fecha) if hasta_fecha is not None else self._fin_de_mes_actual()
        monedas = {fila["id"]: fila for fila in self._db.obtener_monedas()}
        resumen = []
        for fila in self._repo.get_saldo_neto_por_persona(tab, tope):
            moneda = monedas.get(fila["moneda_id"])
            resumen.append({
                "entidad_persona": fila["entidad_persona"],
                "moneda_id": fila["moneda_id"],
                "saldo_minor": fila["saldo"],
                "moneda_codigo": moneda["codigo"] if moneda else "",
                "moneda_simbolo": (moneda["simbolo"] or "") if moneda else "",
                "decimales": moneda["decimales"] if moneda else 2,
            })
        return resumen
