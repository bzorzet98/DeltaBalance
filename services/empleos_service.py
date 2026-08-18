"""
DeltaBalance — services/empleos_service.py

Purpose:
    Domain service for employments (empleos), their monthly salary
    receipts (recibos_sueldo), and scheduled payroll discounts
    (descuentos_programados). Data access lives entirely in
    EmpleosRepository.

    Fase 2, bloque EMPLEOS, paso 2c: este service se crea DESDE CERO. No
    existe ningún EmpleosService previo — el diseño sale del repositorio
    (paso 1) y del schema.

    Cruce deliberado hacia transacciones: create_receipt() puede generar un
    ingreso real en una cuenta (vía TransaccionesRepository) cuando se le
    pasa cuenta_id — decisión ya tomada, no es un acoplamiento accidental.
    Fuera de ese caso puntual, EmpleosService no conoce nada más de
    TransactionService.
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from db.database import DatabaseManager
from repositories.empleos_repository import EmpleosRepository
from repositories.transacciones_repository import TransaccionesRepository
from repositories.cuentas_repository import CuentasRepository
from repositories.categorias_repository import CategoriasRepository

# =============================================================
# EXCEPTIONS
# =============================================================

class EmpleoError(Exception):
    """Raised when an employment/payroll operation violates a business rule."""


class EmpleoNotFoundError(EmpleoError):
    """Raised when a referenced employment does not exist."""


class ReciboNotFoundError(EmpleoError):
    """Raised when a referenced salary receipt does not exist."""


class DescuentoNotFoundError(EmpleoError):
    """Raised when a referenced scheduled discount does not exist."""


class RecibosDuplicadoError(EmpleoError):
    """
    Raised when a receipt already exists for that empleo_id+mes+anio
    (UNIQUE(empleo_id, mes, anio) en recibos_sueldo) — error de negocio
    claro en vez de dejar que se propague un sqlite3.IntegrityError crudo.
    """


class DescuentoYaAplicadoError(EmpleoError):
    """Raised when attempting to apply a scheduled discount that is not 'pendiente'."""


class CurrencyNotFoundError(EmpleoError):
    """
    Raised when a referenced currency does not exist. Definida propia de
    este módulo, colgando de EmpleoError — mismo razonamiento de bajo
    acoplamiento ya aplicado en presupuestos_service.py/
    ingresos_proyectados_service.py: no importar la excepción equivalente
    de otro service.
    """


class AccountNotFoundError(EmpleoError):
    """
    Raised when a referenced account does not exist. Propia de este
    módulo por el mismo motivo que CurrencyNotFoundError — aunque
    create_receipt() SÍ cruza hacia transacciones a propósito (ver
    docstring del módulo), reportar un error de "cuenta no encontrada" no
    necesita depender de la jerarquía de excepciones de TransactionService.
    """


class CategoryNotFoundError(EmpleoError):
    """Raised when a referenced category does not exist. Propia de este módulo, mismo motivo."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class EmpleoResult:
    """Structured result returned by EmpleosService operations."""
    success:   bool
    entity_id: Optional[int] = None
    data:      dict          = field(default_factory=dict)
    message:   str           = ""


# =============================================================
# SERVICE
# =============================================================

class EmpleosService:
    """
    Entry point for employment/payroll operations.

    Usage:
        db  = DatabaseManager()
        svc = EmpleosService(db)

        svc.create_employment(nombre_empresa="Acme SA", puesto="Backend", moneda_id=1)
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._empleos_repo = EmpleosRepository(db)
        self._transacciones_repo = TransaccionesRepository(db)
        self._cuentas_repo = CuentasRepository(db)
        self._categorias_repo = CategoriasRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_employment(self, empleo_id: int) -> sqlite3.Row:
        row = self._empleos_repo.obtener_empleo_por_id(empleo_id)
        if row is None:
            raise EmpleoNotFoundError(f"Empleo id={empleo_id} not found.")
        return row

    def _get_currency(self, currency_id: int) -> sqlite3.Row:
        """No MonedasRepository exists yet in this codebase — mismo patrón
        que en los demás services de esta fase (consulta directa a `monedas`)."""
        row = self._db.fetchone("SELECT * FROM monedas WHERE id = ?;", (currency_id,))
        if row is None:
            raise CurrencyNotFoundError(f"Currency id={currency_id} not found.")
        return row

    def _get_account(self, account_id: int) -> sqlite3.Row:
        row = self._cuentas_repo.obtener_por_id(account_id)
        if row is None:
            raise AccountNotFoundError(f"Account id={account_id} not found.")
        return row

    def _get_category(self, category_id: int) -> sqlite3.Row:
        row = self._categorias_repo.obtener_por_id(category_id)
        if row is None:
            raise CategoryNotFoundError(f"Category id={category_id} not found.")
        return row

    # ----------------------------------------------------------
    # EMPLEOS
    # ----------------------------------------------------------

    def create_employment(
        self,
        nombre_empresa: str,
        moneda_id: int,
        puesto: Optional[str] = None,
        porcentaje_jubilacion: Optional[int] = None,
        porcentaje_obra_social: Optional[int] = None,
        porcentaje_gremio: Optional[int] = None,
        tope_copago_os_minor: Optional[int] = None,
    ) -> EmpleoResult:
        """
        Creates an employment. If a porcentaje_*/tope_copago_os_minor is
        None (default), it's simply NOT passed to
        EmpleosRepository.crear_empleo() — the repository's own Python
        defaults (1100/300/0/0, which mirror the schema's column defaults,
        see su docstring) take effect. Nunca se vuelven a hardcodear esos
        números acá.

        Raises:
            CurrencyNotFoundError si moneda_id no existe.
        """
        if not nombre_empresa or not nombre_empresa.strip():
            raise ValueError("nombre_empresa cannot be empty.")
        self._get_currency(moneda_id)  # validate existence

        kwargs = {}
        if porcentaje_jubilacion is not None:
            kwargs["porcentaje_jubilacion"] = porcentaje_jubilacion
        if porcentaje_obra_social is not None:
            kwargs["porcentaje_obra_social"] = porcentaje_obra_social
        if porcentaje_gremio is not None:
            kwargs["porcentaje_gremio"] = porcentaje_gremio
        if tope_copago_os_minor is not None:
            kwargs["tope_copago_os_minor"] = tope_copago_os_minor

        empleo_id = self._empleos_repo.crear_empleo(
            nombre_empresa=nombre_empresa.strip(),
            puesto=puesto,
            moneda_id=moneda_id,
            **kwargs,
        )

        return EmpleoResult(
            success=True,
            entity_id=empleo_id,
            data={"nombre_empresa": nombre_empresa.strip(), "puesto": puesto, "moneda_id": moneda_id},
            message=f"Employment '{nombre_empresa.strip()}' created.",
        )

    def get_employment(self, empleo_id: int) -> Optional[sqlite3.Row]:
        return self._empleos_repo.obtener_empleo_por_id(empleo_id)

    def list_employments(self, solo_activos: bool = True) -> list[sqlite3.Row]:
        return self._empleos_repo.listar_empleos(solo_activos=solo_activos)

    # ----------------------------------------------------------
    # RECIBOS DE SUELDO
    # ----------------------------------------------------------

    def create_receipt(
        self,
        empleo_id: int,
        mes: int,
        anio: int,
        sueldo_bruto_minor: int,
        desc_jubilacion_minor: int,
        desc_obra_social_minor: int,
        desc_copagos_os_minor: int = 0,
        desc_otros_minor: int = 0,
        cuenta_id: Optional[int] = None,
        categoria_id: Optional[int] = None,
    ) -> EmpleoResult:
        """
        Creates a salary receipt for empleo_id/mes/anio.

        monto_neto_final_minor = sueldo_bruto_minor - desc_jubilacion_minor
        - desc_obra_social_minor - desc_copagos_os_minor - desc_otros_minor,
        validado > 0.

        Si cuenta_id es None: solo inserta el recibo (recibo suelto, sin
        transaccion_id).

        Si cuenta_id NO es None: categoria_id es OBLIGATORIO (no se asume
        ninguna categoría default — el schema tiene una categoría seed
        'Sueldo' en INGRESOS, pero este método no la busca automáticamente
        a propósito; el caller la elige explícitamente). Crea la
        transacción de ingreso (TransaccionesRepository.crear()) y el
        recibo (EmpleosRepository.crear_recibo(transaccion_id=...)) de
        forma atómica dentro de una única self._db.transaction() — si
        cualquiera de las dos escrituras falla, ninguna persiste.

        Raises:
            EmpleoNotFoundError si empleo_id no existe.
            RecibosDuplicadoError si ya existe un recibo para ese
                                  empleo_id+mes+anio.
            ValueError si el neto calculado no es positivo, o si se pasa
                       cuenta_id sin categoria_id.
            AccountNotFoundError si cuenta_id se pasa y no existe.
            CategoryNotFoundError si categoria_id se pasa y no existe.
        """
        empleo = self._get_employment(empleo_id)

        existente = self._empleos_repo.obtener_recibo_por_periodo(empleo_id, mes, anio)
        if existente is not None:
            raise RecibosDuplicadoError(
                f"A receipt already exists for empleo_id={empleo_id} on "
                f"{mes:02d}/{anio} (id={existente['id']})."
            )

        monto_neto_final_minor = (
            sueldo_bruto_minor - desc_jubilacion_minor - desc_obra_social_minor
            - desc_copagos_os_minor - desc_otros_minor
        )
        if monto_neto_final_minor <= 0:
            raise ValueError(
                f"Net salary must be positive. Computed monto_neto_final_minor="
                f"{monto_neto_final_minor} from sueldo_bruto_minor={sueldo_bruto_minor} "
                f"minus total deductions={sueldo_bruto_minor - monto_neto_final_minor}."
            )

        if cuenta_id is None:
            recibo_id = self._empleos_repo.crear_recibo(
                empleo_id=empleo_id,
                mes=mes,
                anio=anio,
                sueldo_bruto_minor=sueldo_bruto_minor,
                desc_jubilacion_minor=desc_jubilacion_minor,
                desc_obra_social_minor=desc_obra_social_minor,
                desc_copagos_os_minor=desc_copagos_os_minor,
                desc_otros_minor=desc_otros_minor,
                monto_neto_final_minor=monto_neto_final_minor,
            )
            return EmpleoResult(
                success=True,
                entity_id=recibo_id,
                data={
                    "recibo_id": recibo_id,
                    "monto_neto_final_minor": monto_neto_final_minor,
                    "transaccion_id": None,
                },
                message=(
                    f"Receipt created for empleo_id={empleo_id} on {mes:02d}/{anio} "
                    f"(no linked transaction)."
                ),
            )

        # cuenta_id fue provisto: cruce a transacciones, atómico.
        self._get_account(cuenta_id)
        if categoria_id is None:
            raise ValueError(
                "categoria_id is required when cuenta_id is provided — "
                "no default category is assumed (see docstring)."
            )
        self._get_category(categoria_id)

        fecha = f"{anio:04d}-{mes:02d}-01"
        conn = self._db.conn
        with self._db.transaction():
            transaccion_id = self._transacciones_repo.crear(
                fecha=fecha,
                concepto=f"Sueldo {mes:02d}/{anio} - {empleo['nombre_empresa']}",
                cuenta_id=cuenta_id,
                categoria_id=categoria_id,
                moneda_id=empleo["moneda_id"],
                tipo_movimiento="ingreso",
                monto_minor=monto_neto_final_minor,
                conn=conn,
            )
            recibo_id = self._empleos_repo.crear_recibo(
                empleo_id=empleo_id,
                mes=mes,
                anio=anio,
                sueldo_bruto_minor=sueldo_bruto_minor,
                desc_jubilacion_minor=desc_jubilacion_minor,
                desc_obra_social_minor=desc_obra_social_minor,
                desc_copagos_os_minor=desc_copagos_os_minor,
                desc_otros_minor=desc_otros_minor,
                monto_neto_final_minor=monto_neto_final_minor,
                transaccion_id=transaccion_id,
                conn=conn,
            )

        return EmpleoResult(
            success=True,
            entity_id=recibo_id,
            data={
                "recibo_id": recibo_id,
                "monto_neto_final_minor": monto_neto_final_minor,
                "transaccion_id": transaccion_id,
            },
            message=(
                f"Receipt created for empleo_id={empleo_id} on {mes:02d}/{anio}, "
                f"linked to transaction #{transaccion_id}."
            ),
        )

    def get_receipt(self, empleo_id: int, mes: int, anio: int) -> Optional[sqlite3.Row]:
        return self._empleos_repo.obtener_recibo_por_periodo(empleo_id, mes, anio)

    # ----------------------------------------------------------
    # DESCUENTOS PROGRAMADOS
    # ----------------------------------------------------------

    def schedule_discount(
        self,
        empleo_id: int,
        concepto: str,
        monto_minor: int,
        mes_aplicacion: int,
        anio_aplicacion: int,
    ) -> EmpleoResult:
        """
        Raises:
            EmpleoNotFoundError si empleo_id no existe.
            ValueError si concepto está vacío o monto_minor <= 0.
        """
        self._get_employment(empleo_id)  # validate existence
        if not concepto or not concepto.strip():
            raise ValueError("Concept cannot be empty.")
        if monto_minor <= 0:
            raise ValueError(f"monto_minor must be positive. Received: {monto_minor}.")

        descuento_id = self._empleos_repo.crear_descuento_programado(
            concepto=concepto.strip(),
            monto_minor=monto_minor,
            mes_aplicacion=mes_aplicacion,
            anio_aplicacion=anio_aplicacion,
        )

        return EmpleoResult(
            success=True,
            entity_id=descuento_id,
            data={
                "concepto": concepto.strip(),
                "monto_minor": monto_minor,
                "mes_aplicacion": mes_aplicacion,
                "anio_aplicacion": anio_aplicacion,
            },
            message=f"Discount '{concepto.strip()}' scheduled for {mes_aplicacion:02d}/{anio_aplicacion}.",
        )

    def list_pending_discounts(self, mes: int, anio: int) -> list[sqlite3.Row]:
        return self._empleos_repo.listar_descuentos_pendientes(mes, anio)

    def apply_discount(self, descuento_id: int, recibo_id: int) -> EmpleoResult:
        """
        Marks a scheduled discount as 'aplicado', linked to recibo_id.

        IMPORTANTE — esto NO ajusta ningún monto de recibos_sueldo
        automáticamente (ver hallazgo del paso 1:
        EmpleosRepository.marcar_descuento_aplicado() es una escritura de
        UNA sola tabla, descuentos_programados). Es responsabilidad de
        quien arma el recibo incluir este descuento en desc_otros_minor
        ANTES de llamar a create_receipt() — este método solo deja
        constancia de que el descuento ya fue tenido en cuenta en algún
        recibo, no recalcula nada.

        Raises:
            DescuentoNotFoundError si descuento_id no existe.
            DescuentoYaAplicadoError si el descuento no está 'pendiente'.
            ReciboNotFoundError si recibo_id no existe.
        """
        descuento = self._empleos_repo.obtener_descuento_por_id(descuento_id)
        if descuento is None:
            raise DescuentoNotFoundError(f"Descuento programado id={descuento_id} not found.")
        if descuento["estado"] != "pendiente":
            raise DescuentoYaAplicadoError(
                f"Descuento programado id={descuento_id} is already '{descuento['estado']}'."
            )

        recibo = self._empleos_repo.obtener_recibo_por_id(recibo_id)
        if recibo is None:
            raise ReciboNotFoundError(f"Recibo id={recibo_id} not found.")

        self._empleos_repo.marcar_descuento_aplicado(descuento_id, recibo_id)

        return EmpleoResult(
            success=True,
            entity_id=descuento_id,
            data={"descuento_id": descuento_id, "recibo_id": recibo_id},
            message=(
                f"Descuento programado id={descuento_id} marked as applied to recibo "
                f"id={recibo_id}. NOTE: recibos_sueldo amounts were NOT recalculated."
            ),
        )
