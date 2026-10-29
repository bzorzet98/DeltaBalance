"""
DeltaBalance — services/ingresos_service.py

Ingresos esperados de cada mes (tabla ingresos_proyectados, estructura
final de db/schema_migrations.py): concepto, monto estimado, monto real
cobrado, moneda y si se repite todos los meses. Reemplaza a
IngresosProyectadosService (estados pendiente/parcial/cobrado): "cobrado"
ahora es simplemente un real cargado. services/ingresos_proyectados_service.py
queda como reexport para ui/app.py.

Reglas de dominio (acá, no en el repositorio):
- El concepto se normaliza: sin espacios de más y en MAYÚSCULAS (así
  copy_recurrentes() reconoce el mismo concepto en el mes destino).
- Montos enteros (minor units) >= 0; moneda_codigo tiene que existir; mes
  entre 1 y 12.
- Edición y borrado directos: un ingreso no tiene dependencias con estado
  propio (ventana de corrección temprana, CLAUDE.md §4).
- copy_recurrentes() copia los recurrentes del mes origen con real = 0 y
  sin duplicar los conceptos que el destino ya tiene.

Totales: siempre por moneda, nunca mezclados. list_by_month() devuelve las
filas y, al final, una entrada de total por moneda (CLAVE_TOTAL = True) —
las mismas que get_totales().

Los mensajes de error y de IngresoResult ya vienen en MAYÚSCULAS: las
pantallas los muestran tal cual.

Reglas de arquitectura: solo IngresosProyectadosRepository (y
db.obtener_monedas() para validar la moneda) — sin SQL directo.
"""

import sqlite3
from dataclasses import dataclass
from functools import partial
from typing import Any, Optional

from db.database import DatabaseManager
from utils.validaciones import recurring_flag, validate_concept, validate_period
from repositories.ingresos_proyectados_repository import IngresosProyectadosRepository

# update(): campos que se pueden cambiar.
CAMPOS_EDITABLES = ("concepto", "monto_estimado_minor", "monto_real_minor", "moneda_codigo", "es_recurrente", "notas")

# Marca de las entradas de total al final de list_by_month().
CLAVE_TOTAL = "es_total"


# =============================================================
# EXCEPTIONS
# =============================================================

class IngresoError(Exception):
    """Raised when an income operation violates a business rule."""


class IngresoNotFoundError(IngresoError):
    """Raised when a referenced income row does not exist."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class IngresoResult:
    success: bool
    entity_id: Optional[str] = None
    message: str = ""


# =============================================================
# SERVICE
# =============================================================

class IngresosService:
    """
    Usage:
        svc = IngresosService(db)
        svc.create("Beca doctoral", 121739300, 0, moneda_codigo="ARS", mes=10, anio=2026, es_recurrente=1)
        svc.list_by_month(10, 2026)      # [{...fila...}, ..., {"es_total": True, "currency_code": "ARS", ...}]
        svc.copy_recurrentes(10, 2026, 11, 2026)   # 1
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._repo = IngresosProyectadosRepository(db)

    # ----------------------------------------------------------
    # VALIDACIONES
    # ----------------------------------------------------------

    # Compartidos con PresupuestosService (utils/validaciones.py), con IngresoError.
    _concepto = staticmethod(partial(validate_concept, error=IngresoError))
    _periodo = staticmethod(partial(validate_period, error=IngresoError))
    _recurrente = staticmethod(recurring_flag)

    @staticmethod
    def _monto(monto_minor: Any) -> int:
        if not isinstance(monto_minor, int) or isinstance(monto_minor, bool):
            raise IngresoError(f"EL MONTO TIENE QUE SER UN ENTERO EN MINOR UNITS (RECIBIDO: {monto_minor!r}).")
        if monto_minor < 0:
            raise IngresoError("EL MONTO NO PUEDE SER NEGATIVO.")
        return monto_minor

    def _moneda(self, moneda_codigo: Any) -> str:
        if moneda_codigo not in {fila["codigo"] for fila in self._db.obtener_monedas()}:
            raise IngresoError(f"LA MONEDA {moneda_codigo} NO EXISTE.")
        return moneda_codigo

    @staticmethod
    def _notas(valor: Optional[str]) -> Optional[str]:
        return (valor or "").strip() or None

    def _obtener(self, ingreso_id: str) -> sqlite3.Row:
        fila = self._repo.obtener_por_id(ingreso_id)
        if fila is None:
            raise IngresoNotFoundError(f"EL INGRESO {ingreso_id} NO EXISTE.")
        return fila

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def create(
        self,
        concepto: str,
        monto_estimado_minor: int,
        monto_real_minor: int,
        moneda_codigo: str,
        mes: int,
        anio: int,
        es_recurrente: int = 0,
        notas: Optional[str] = None,
    ) -> IngresoResult:
        """
        Crea un ingreso del mes.

        Raises:
            IngresoError si el concepto está vacío, algún monto no es un
            entero >= 0, la moneda no existe o el mes no está entre 1 y 12.
        """
        concepto = self._concepto(concepto)
        mes, anio = self._periodo(mes, anio)
        ingreso_id = self._repo.crear(
            concepto=concepto,
            monto_estimado_minor=self._monto(monto_estimado_minor),
            monto_real_minor=self._monto(monto_real_minor),
            moneda_codigo=self._moneda(moneda_codigo),
            mes=mes,
            anio=anio,
            es_recurrente=self._recurrente(es_recurrente),
            notas=self._notas(notas),
        )
        return IngresoResult(success=True, entity_id=ingreso_id, message=f"INGRESO '{concepto}' AGREGADO.")

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def list_by_month(self, mes: int, anio: int) -> list[dict]:
        """
        Los ingresos de ese mes (dicts, enriquecidos con currency_code,
        currency_symbol y decimales), en orden de alta, y al final una
        entrada de total por moneda: {CLAVE_TOTAL: True, moneda_codigo,
        currency_code, currency_symbol, decimales, total_estimado_minor,
        total_real_minor}.

        Raises:
            IngresoError si el mes no está entre 1 y 12.
        """
        mes, anio = self._periodo(mes, anio)
        filas = [dict(fila) for fila in self._repo.listar_por_mes(mes, anio)]  # CLAUDE.md §11
        totales = [{CLAVE_TOTAL: True, "currency_code": codigo, **total} for codigo, total in self._totales(filas).items()]
        return filas + totales

    def get_totales(self, mes: int, anio: int) -> dict:
        """
        Totales del mes por moneda: {currency_code: {moneda_codigo,
        currency_symbol, decimales, total_estimado_minor, total_real_minor}}.

        Raises:
            IngresoError si el mes no está entre 1 y 12.
        """
        mes, anio = self._periodo(mes, anio)
        return self._totales([dict(fila) for fila in self._repo.listar_por_mes(mes, anio)])

    @staticmethod
    def _totales(filas: list[dict]) -> dict:
        totales: dict[str, dict] = {}
        for fila in filas:
            total = totales.setdefault(fila["currency_code"], {
                "moneda_codigo": fila["moneda_codigo"],
                "currency_symbol": fila["currency_symbol"] or "",
                "decimales": fila["decimales"],
                "total_estimado_minor": 0,
                "total_real_minor": 0,
            })
            total["total_estimado_minor"] += fila["monto_estimado_minor"]
            total["total_real_minor"] += fila["monto_real_minor"]
        return totales

    # ----------------------------------------------------------
    # UPDATE / DELETE
    # ----------------------------------------------------------

    def update(self, id: str, **kwargs: Any) -> IngresoResult:
        """
        Update parcial: solo los campos pasados (CAMPOS_EDITABLES), con las
        mismas validaciones que create().

        Returns:
            IngresoResult con success=False si no se pasó ningún campo.

        Raises:
            IngresoNotFoundError si la fila no existe.
            IngresoError si un campo no es editable o no es válido.
        """
        self._obtener(id)
        desconocidos = set(kwargs) - set(CAMPOS_EDITABLES)
        if desconocidos:
            raise IngresoError(f"CAMPOS NO EDITABLES: {', '.join(sorted(desconocidos))}.")
        if not kwargs:
            return IngresoResult(success=False, entity_id=id, message="NO HAY CAMPOS PARA ACTUALIZAR.")

        validadores = {
            "concepto": self._concepto,
            "monto_estimado_minor": self._monto,
            "monto_real_minor": self._monto,
            "moneda_codigo": self._moneda,
            "es_recurrente": self._recurrente,
            "notas": self._notas,
        }
        campos = {campo: validadores[campo](valor) for campo, valor in kwargs.items()}
        actualizado = self._repo.actualizar(id, **campos)
        return IngresoResult(
            success=actualizado, entity_id=id,
            message="INGRESO ACTUALIZADO." if actualizado else "EL INGRESO NO CAMBIÓ.",
        )

    def delete(self, id: str) -> IngresoResult:
        """
        DELETE físico (sin dependencias con estado propio, CLAUDE.md §4).

        Raises:
            IngresoNotFoundError si la fila no existe.
        """
        self._obtener(id)
        self._repo.eliminar(id)
        return IngresoResult(success=True, entity_id=id, message="INGRESO ELIMINADO.")

    # ----------------------------------------------------------
    # COPIAR RECURRENTES
    # ----------------------------------------------------------

    def copy_recurrentes(self, mes_origen: int, anio_origen: int, mes_destino: int, anio_destino: int) -> int:
        """
        Copia los ingresos recurrentes del mes origen al destino, con real
        = 0 y sin duplicar los conceptos que el destino ya tiene. Devuelve
        cuántos copió.

        Raises:
            IngresoError si algún mes no está entre 1 y 12, o si origen y
            destino son el mismo mes.
        """
        mes_origen, anio_origen = self._periodo(mes_origen, anio_origen)
        mes_destino, anio_destino = self._periodo(mes_destino, anio_destino)
        if (mes_origen, anio_origen) == (mes_destino, anio_destino):
            raise IngresoError("EL MES ORIGEN Y EL DESTINO SON EL MISMO.")
        return self._repo.copiar_recurrentes(mes_origen, anio_origen, mes_destino, anio_destino)
