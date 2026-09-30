"""
DeltaBalance — services/debts_service.py

Deudas informales como LIBRO DE MOVIMIENTOS (reestructuración de deudas,
docs/DATA_MODEL_DECISIONS.md sección 22): cada fila de `deudas` es un monto
con dirección —
    'a_favor'   te deben más (le prestaste, pagaste algo por esa persona)
    'en_contra' debés más, o te pagaron (un pago que recibiste)
— y el saldo con una persona es la suma con signo de sus filas (a_favor
suma, en_contra resta): positivo = te debe, negativo = le debés. No hay
pendiente, ni estado, ni pagos aparte: registrar un pago es crear una fila
de tipo opuesto. register_payment()/write_off()/mark_uncollectable() ya no
existen.

Reglas de dominio (acá, no en el repositorio):
- La persona se normaliza (utils/personas.py): "Noe" y "NOE" son la misma.
- monto_minor distinto de 0. Negativo = la dirección contraria: se guarda
  en positivo y se invierte `tipo` (así "acepta negativos" sin tener dos
  formas de guardar lo mismo).
- fecha 'YYYY-MM-DD'; moneda_id tiene que existir.
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
from repositories.deudas_repository import DeudaDuplicadaError, DeudasRepository
from utils.personas import normalizar_persona

TIPOS = ("a_favor", "en_contra")
OPUESTO = {"a_favor": "en_contra", "en_contra": "a_favor"}
# update(): campos que se pueden cambiar.
CAMPOS_EDITABLES = ("entidad_persona", "concepto", "tipo", "monto_minor", "moneda_id", "fecha", "notas")


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
    entity_id: Optional[int] = None
    message: str = ""


# =============================================================
# SERVICE
# =============================================================

class DebtsService:
    """
    Usage:
        svc = DebtsService(db)
        svc.create("Noe", "Préstamo", "a_favor", 4000000, moneda_id=1, fecha="2026-03-01")
        svc.create("Noe", "Me devolvió una parte", "en_contra", 1000000, moneda_id=1, fecha="2026-03-15")
        svc.get_saldo_neto()   # [{"entidad_persona": "NOE", "moneda_id": 1, "saldo_minor": 3000000}]
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
    def _tipo(tipo: str) -> str:
        if tipo not in TIPOS:
            raise DebtError(f"Invalid debt type '{tipo}'. Must be 'a_favor' or 'en_contra'.")
        return tipo

    @staticmethod
    def _monto_y_tipo(monto_minor: int, tipo: str) -> tuple[int, str]:
        """Monto negativo = dirección contraria: (abs(monto), tipo opuesto). 0 no vale."""
        if not isinstance(monto_minor, int) or isinstance(monto_minor, bool):
            raise DebtError(f"monto_minor must be an integer (minor units). Received: {monto_minor!r}.")
        if monto_minor == 0:
            raise DebtError("Amount cannot be 0.")
        return (monto_minor, tipo) if monto_minor > 0 else (-monto_minor, OPUESTO[tipo])

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
        """concepto/notas: sin espacios de más; vacío = NULL."""
        return (valor or "").strip() or None

    def _obtener(self, deuda_id: int) -> sqlite3.Row:
        fila = self._repo.obtener_por_id(deuda_id)
        if fila is None:
            raise DebtNotFoundError(f"Debt id={deuda_id} not found.")
        return fila

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def create(
        self,
        entidad_persona: str,
        concepto: Optional[str],
        tipo: str,
        monto_minor: int,
        moneda_id: int,
        fecha: str,
        notas: Optional[str] = None,
        origen_tipo: str = "manual",
        origen_id: Optional[int] = None,
    ) -> DebtResult:
        """
        Crea una fila del libro. monto_minor negativo invierte `tipo` (ver
        docstring del módulo).

        Raises:
            DebtError si la persona está vacía, el tipo no es válido, el
            monto es 0 o no es entero, la fecha no es 'YYYY-MM-DD', la
            moneda no existe, o es un doble-click (fila idéntica recién
            creada).
        """
        persona = self._persona(entidad_persona)
        monto, tipo_final = self._monto_y_tipo(monto_minor, self._tipo(tipo))
        if not origen_tipo or not origen_tipo.strip():
            raise DebtError("origen_tipo cannot be empty.")
        try:
            deuda_id = self._repo.crear(
                entidad_persona=persona,
                concepto=self._texto(concepto),
                tipo=tipo_final,
                monto_minor=monto,
                moneda_id=self._moneda(moneda_id),
                fecha=self._fecha(fecha),
                notas=self._texto(notas),
                origen_tipo=origen_tipo.strip(),
                origen_id=origen_id,
            )
        except DeudaDuplicadaError as err:
            raise DebtError(str(err)) from err
        return DebtResult(success=True, entity_id=deuda_id, message=f"Debt #{deuda_id} ({tipo_final}) created for '{persona}'.")

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def get(self, id: int) -> Optional[sqlite3.Row]:
        """La fila enriquecida con la moneda (currency_code, currency_symbol, decimales), o None."""
        return self._repo.obtener_por_id(id)

    def list_by_period(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """
        Filas con fecha dentro de ese mes/año, la más nueva primero.

        Raises:
            DebtError si mes no está entre 1 y 12.
        """
        if not isinstance(mes, int) or not 1 <= mes <= 12:
            raise DebtError(f"mes must be between 1 and 12. Received: {mes!r}.")
        ultimo_dia = calendar.monthrange(anio, mes)[1]
        return self._repo.listar_por_periodo(f"{anio:04d}-{mes:02d}-01", f"{anio:04d}-{mes:02d}-{ultimo_dia:02d}")

    def list_all(self) -> list[sqlite3.Row]:
        """Todas las filas, la más nueva primero."""
        return self._repo.listar_todo()

    # ----------------------------------------------------------
    # UPDATE / DELETE
    # ----------------------------------------------------------

    def update(self, id: int, **kwargs: Any) -> DebtResult:
        """
        Update parcial: solo los campos pasados (CAMPOS_EDITABLES). Mismas
        validaciones que create(). monto_minor negativo invierte el tipo —
        el que se pase en la misma llamada o, si no, el actual.

        Returns:
            DebtResult con success=False si no se pasó ningún campo.

        Raises:
            DebtNotFoundError si la fila no existe.
            DebtError si un campo no es editable o no es válido.
        """
        fila = self._obtener(id)
        desconocidos = set(kwargs) - set(CAMPOS_EDITABLES)
        if desconocidos:
            raise DebtError(f"Fields cannot be updated: {', '.join(sorted(desconocidos))}.")
        if not kwargs:
            return DebtResult(success=False, entity_id=id, message="No fields to update were provided.")

        campos: dict[str, Any] = {}
        if "entidad_persona" in kwargs:
            campos["entidad_persona"] = self._persona(kwargs["entidad_persona"])
        if "concepto" in kwargs:
            campos["concepto"] = self._texto(kwargs["concepto"])
        if "notas" in kwargs:
            campos["notas"] = self._texto(kwargs["notas"])
        if "tipo" in kwargs:
            campos["tipo"] = self._tipo(kwargs["tipo"])
        if "monto_minor" in kwargs:
            monto, tipo_final = self._monto_y_tipo(kwargs["monto_minor"], campos.get("tipo", fila["tipo"]))
            campos["monto_minor"] = monto
            campos["tipo"] = tipo_final
        if "moneda_id" in kwargs:
            campos["moneda_id"] = self._moneda(kwargs["moneda_id"])
        if "fecha" in kwargs:
            campos["fecha"] = self._fecha(kwargs["fecha"])

        actualizado = self._repo.actualizar(id, **{k: campos.get(k, NO_CAMBIAR) for k in CAMPOS_EDITABLES})
        return DebtResult(
            success=actualizado, entity_id=id,
            message=f"Debt #{id} updated ({len(campos)} field(s))." if actualizado else f"Debt #{id}: nothing changed.",
        )

    def delete(self, id: int) -> DebtResult:
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

    def get_saldo_neto(self, hasta_fecha: Optional[str] = None) -> list[dict]:
        """
        Saldo neto por persona y moneda con las filas de fecha <= hasta_fecha
        (None = hoy): [{entidad_persona, moneda_id, saldo_minor}], positivo =
        te debe, negativo = le debés. Incluye los saldos en 0.

        Raises:
            DebtError si hasta_fecha no es 'YYYY-MM-DD'.
        """
        tope = self._fecha(hasta_fecha) if hasta_fecha is not None else date.today().isoformat()
        return [
            {"entidad_persona": fila["entidad_persona"], "moneda_id": fila["moneda_id"], "saldo_minor": fila["saldo"]}
            for fila in self._repo.get_saldo_neto_por_persona(tope)
        ]

    def summary_by_person(self, hasta_fecha: Optional[str] = None) -> list[dict]:
        """
        Para la barra de saldo de la pantalla: get_saldo_neto() + la moneda
        (moneda_codigo, moneda_simbolo, decimales).
        """
        monedas = {fila["id"]: fila for fila in self._db.obtener_monedas()}
        resumen = []
        for entrada in self.get_saldo_neto(hasta_fecha):
            moneda = monedas.get(entrada["moneda_id"])
            resumen.append({
                **entrada,
                "moneda_codigo": moneda["codigo"] if moneda else "",
                "moneda_simbolo": (moneda["simbolo"] or "") if moneda else "",
                "decimales": moneda["decimales"] if moneda else 2,
            })
        return resumen
