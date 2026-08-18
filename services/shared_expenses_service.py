"""
DeltaBalance — services/shared_expenses_service.py

Purpose:
    Domain service for households (hogares), their members (hogar_miembros)
    and shared expenses (gastos_compartidos). Data access lives entirely in
    HogaresRepository, HogarMiembrosRepository y GastosCompartidosRepository.

    Fase 2, bloque HOGARES / GASTOS COMPARTIDOS, paso 2: este service se
    crea DESDE CERO. No existe ningún SharedExpensesService previo — el
    diseño sale de los tres repositorios (paso 1) y del schema.

    Generación del código de invitación: vive ACÁ (no en el repositorio,
    ver docstring de HogaresRepository) porque es responsabilidad de la
    capa de servicio según docs/DATA_MODEL_DECISIONS.md sección 2. Usa
    `secrets.choice`, no el módulo `random` — aunque el riesgo real es bajo
    (un código de invitación a datos financieros personales compartidos
    entre pocas personas, no un secreto de autenticación por sí solo), es
    un valor que otorga acceso, así que se usa el generador
    criptográficamente apropiado en vez del PRNG no seguro por default.

    categoria_id no tiene su propia excepción en la lista pedida para este
    service (a diferencia de otros services de esta fase, que sí definen
    CategoryNotFoundError propia) — "categoria_id existe" se valida igual,
    pero levanta la excepción base SharedExpensesError directamente en vez
    de una subclase dedicada, respetando la lista de excepciones cerrada
    que se especificó para este paso.

    Signo de monto_adeudado_minor en add_shared_expense(): coeficiente_deuda
    respeta el CHECK de schema.sql (siempre positivo, 0 < x <= 100) — el
    signo lo hereda exclusivamente monto_base_minor, nunca el coeficiente
    (ver docstring de add_shared_expense()).
"""

import secrets
import sqlite3
import string
from dataclasses import dataclass, field
from typing import Optional

from db.database import DatabaseManager
from utils.money import amount_display
from repositories.hogares_repository import HogaresRepository
from repositories.hogar_miembros_repository import HogarMiembrosRepository
from repositories.gastos_compartidos_repository import GastosCompartidosRepository
from repositories.categorias_repository import CategoriasRepository

# =============================================================
# EXCEPTIONS
# =============================================================

class SharedExpensesError(Exception):
    """Raised when a shared-expenses operation violates a business rule."""


class HogarNotFoundError(SharedExpensesError):
    """Raised when a referenced household does not exist."""


class MiembroNotFoundError(SharedExpensesError):
    """Raised when a referenced household member does not exist."""


class GastoCompartidoNotFoundError(SharedExpensesError):
    """Raised when a referenced shared expense does not exist (or does not belong to the given hogar_id)."""


class CodigoInvitacionInvalidoError(SharedExpensesError):
    """Raised when an invitation code does not match any household."""


class MiembroYaExisteError(SharedExpensesError):
    """
    Raised when join_hogar() is attempted for a usuario_local that is
    already a member of that hogar_id — translates the raw
    sqlite3.IntegrityError of the composite PRIMARY KEY
    (HogarMiembrosRepository.agregar() deja subir esa excepción cruda, ver
    su docstring) a un error de negocio claro, que es trabajo del service.
    """


class GastoCompartidoDuplicadoError(SharedExpensesError):
    """Raised when a shared expense already exists for the given origen_tipo + origen_id."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class SharedExpensesResult:
    """Structured result returned by SharedExpensesService operations."""
    success:   bool
    entity_id: Optional[int] = None
    data:      dict          = field(default_factory=dict)
    message:   str           = ""


# =============================================================
# SERVICE
# =============================================================

CODIGO_ALFABETO = string.ascii_uppercase + string.digits
CODIGO_LONGITUD = 10
MAX_INTENTOS_CODIGO = 5


class SharedExpensesService:
    """
    Entry point for household / shared-expense operations.

    Usage:
        db  = DatabaseManager()
        svc = SharedExpensesService(db)

        svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Casa Zorzet")
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._hogares_repo = HogaresRepository(db)
        self._miembros_repo = HogarMiembrosRepository(db)
        self._gastos_repo = GastosCompartidosRepository(db)
        self._categorias_repo = CategoriasRepository(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    def _get_hogar(self, hogar_id: int) -> sqlite3.Row:
        row = self._hogares_repo.obtener_por_id(hogar_id)
        if row is None:
            raise HogarNotFoundError(f"Hogar id={hogar_id} not found.")
        return row

    def _get_categoria(self, categoria_id: int) -> sqlite3.Row:
        """
        No hay CategoryNotFoundError propia en este service (ver docstring
        del módulo) — levanta la base SharedExpensesError directamente.
        """
        row = self._categorias_repo.obtener_por_id(categoria_id)
        if row is None:
            raise SharedExpensesError(f"Categoria id={categoria_id} not found.")
        return row

    # ----------------------------------------------------------
    # CREATE HOGAR
    # ----------------------------------------------------------

    def create_hogar(
        self,
        nombre_creador_local: str,
        nombre_hogar: Optional[str] = None,
    ) -> SharedExpensesResult:
        """
        Genera un código de invitación de 10 caracteres alfanuméricos en
        mayúscula (secrets.choice sobre A-Z + 0-9), crea el hogar y agrega
        a nombre_creador_local como su primer miembro, atómico en una
        única self._db.transaction().

        Si HogaresRepository.crear() falla por colisión de
        codigo_invitacion (sqlite3.IntegrityError — extremadamente
        improbable con 10 caracteres, pero no se asume que nunca pasa), se
        reintenta con un código nuevo hasta MAX_INTENTOS_CODIGO veces. Si
        se agotan los intentos, se levanta SharedExpensesError.

        Raises:
            ValueError si nombre_creador_local está vacío.
            SharedExpensesError si no se pudo generar un código único tras
                                MAX_INTENTOS_CODIGO intentos.
        """
        if not nombre_creador_local or not nombre_creador_local.strip():
            raise ValueError("nombre_creador_local cannot be empty.")
        nombre_creador_local = nombre_creador_local.strip()

        conn = self._db.conn
        ultimo_error: Optional[Exception] = None

        for _ in range(MAX_INTENTOS_CODIGO):
            codigo = "".join(secrets.choice(CODIGO_ALFABETO) for _ in range(CODIGO_LONGITUD))
            try:
                with self._db.transaction():
                    hogar_id = self._hogares_repo.crear(
                        codigo_invitacion=codigo, nombre=nombre_hogar, conn=conn,
                    )
                    self._miembros_repo.agregar(
                        hogar_id=hogar_id, usuario_local=nombre_creador_local, conn=conn,
                    )
            except sqlite3.IntegrityError as e:
                ultimo_error = e
                continue

            return SharedExpensesResult(
                success=True,
                entity_id=hogar_id,
                data={
                    "codigo_invitacion": codigo,
                    "nombre": nombre_hogar,
                    "creador": nombre_creador_local,
                },
                message=f"Hogar creado con código de invitación '{codigo}'.",
            )

        raise SharedExpensesError(
            f"No se pudo generar un código de invitación único tras "
            f"{MAX_INTENTOS_CODIGO} intentos."
        ) from ultimo_error

    # ----------------------------------------------------------
    # JOIN HOGAR
    # ----------------------------------------------------------

    def join_hogar(
        self,
        codigo_invitacion: str,
        nombre_local: str,
        porcentaje_default: Optional[float] = None,
    ) -> SharedExpensesResult:
        """
        Raises:
            ValueError si nombre_local está vacío.
            CodigoInvitacionInvalidoError si codigo_invitacion no
                                          corresponde a ningún hogar.
            MiembroYaExisteError si nombre_local ya es miembro de ese
                                 hogar (traduce el sqlite3.IntegrityError
                                 crudo de HogarMiembrosRepository.agregar()
                                 — ver docstring de esa excepción).
        """
        if not nombre_local or not nombre_local.strip():
            raise ValueError("nombre_local cannot be empty.")
        nombre_local = nombre_local.strip()

        hogar = self._hogares_repo.obtener_por_codigo(codigo_invitacion)
        if hogar is None:
            raise CodigoInvitacionInvalidoError(
                f"Código de invitación '{codigo_invitacion}' no corresponde a ningún hogar."
            )

        try:
            self._miembros_repo.agregar(
                hogar_id=hogar["id"], usuario_local=nombre_local,
                porcentaje_default=porcentaje_default,
            )
        except sqlite3.IntegrityError as e:
            raise MiembroYaExisteError(
                f"'{nombre_local}' ya es miembro del hogar id={hogar['id']}."
            ) from e

        return SharedExpensesResult(
            success=True,
            entity_id=hogar["id"],
            data={
                "hogar_id": hogar["id"],
                "usuario_local": nombre_local,
                "porcentaje_default": porcentaje_default,
            },
            message=f"'{nombre_local}' se unió al hogar id={hogar['id']}.",
        )

    # ----------------------------------------------------------
    # MIEMBROS
    # ----------------------------------------------------------

    def list_miembros(self, hogar_id: int) -> list[sqlite3.Row]:
        return self._miembros_repo.listar_miembros(hogar_id)

    def get_suggested_coefficient(
        self, hogar_id: int, usuario_local_otro_miembro: str,
    ) -> Optional[float]:
        """
        Devuelve el porcentaje_default del OTRO miembro (no del que paga)
        — valor sugerido para precargar coeficiente_deuda en la UI al
        cargar un gasto nuevo; el usuario puede aceptarlo o editarlo antes
        de confirmar (add_shared_expense() nunca lo adivina solo, ver su
        docstring). None si ese miembro no tiene default configurado.

        Raises:
            MiembroNotFoundError si usuario_local_otro_miembro no es
                                 miembro de hogar_id.
        """
        miembro = self._miembros_repo.obtener_miembro(hogar_id, usuario_local_otro_miembro)
        if miembro is None:
            raise MiembroNotFoundError(
                f"'{usuario_local_otro_miembro}' is not a member of hogar id={hogar_id}."
            )
        return miembro["porcentaje_default"]

    # ----------------------------------------------------------
    # ADD SHARED EXPENSE
    # ----------------------------------------------------------

    def add_shared_expense(
        self,
        hogar_id: int,
        pagador: str,
        origen_tipo: str,
        origen_id: int,
        categoria_id: int,
        monto_base_minor: int,
        coeficiente_deuda: float,
        fecha: str,
        descripcion: Optional[str] = None,
    ) -> SharedExpensesResult:
        """
        coeficiente_deuda es SIEMPRE explícito acá — precargar el default
        sugerido es responsabilidad de la UI llamando primero a
        get_suggested_coefficient() y mostrándolo editable; este método
        nunca lo adivina solo.

        coeficiente_deuda respeta el CHECK de schema.sql: siempre positivo,
        0 < coeficiente_deuda <= 100 (porcentaje que le corresponde al otro
        miembro, nunca negativo). El signo de monto_adeudado_minor lo
        hereda EXCLUSIVAMENTE de monto_base_minor — el coeficiente nunca lo
        invierte. El caso real de signo invertido es un reintegro de cuota
        que supera el monto de la cuota (monto_base_minor ya viene negativo
        de ese cálculo, ver docs/DATA_MODEL_DECISIONS.md sección 2); este
        método no hace ese cálculo, solo aplica la fórmula sobre lo que le
        pasan.

        monto_adeudado_minor = round(monto_base_minor * coeficiente_deuda / 100)

        Raises:
            HogarNotFoundError si hogar_id no existe.
            MiembroNotFoundError si pagador no es miembro de hogar_id.
            SharedExpensesError si categoria_id no existe (ver docstring
                                del módulo).
            ValueError si monto_base_minor == 0 o coeficiente_deuda fuera
                       de (0, 100].
            GastoCompartidoDuplicadoError si ya existe un gasto compartido
                                          para ese origen_tipo+origen_id.
        """
        self._get_hogar(hogar_id)  # validate existence

        miembro = self._miembros_repo.obtener_miembro(hogar_id, pagador)
        if miembro is None:
            raise MiembroNotFoundError(f"'{pagador}' is not a member of hogar id={hogar_id}.")

        self._get_categoria(categoria_id)  # validate existence

        if monto_base_minor == 0:
            raise ValueError("monto_base_minor cannot be zero.")
        if not (0 < coeficiente_deuda <= 100):
            raise ValueError(
                f"coeficiente_deuda must be between 0 (exclusive) and 100 (inclusive). "
                f"Received: {coeficiente_deuda}."
            )

        duplicados = self._gastos_repo.listar_por_origen(origen_tipo, origen_id)
        if duplicados:
            raise GastoCompartidoDuplicadoError(
                f"Ya existe un gasto compartido para origen_tipo='{origen_tipo}', "
                f"origen_id={origen_id} (gasto id={duplicados[0]['id']})."
            )

        monto_adeudado_minor = round(monto_base_minor * coeficiente_deuda / 100)

        gasto_id = self._gastos_repo.crear(
            hogar_id=hogar_id, pagador=pagador, origen_tipo=origen_tipo, origen_id=origen_id,
            categoria_id=categoria_id, monto_base_minor=monto_base_minor,
            coeficiente_deuda=coeficiente_deuda, monto_adeudado_minor=monto_adeudado_minor,
            fecha=fecha, descripcion=descripcion,
        )

        return SharedExpensesResult(
            success=True,
            entity_id=gasto_id,
            data={
                "hogar_id": hogar_id,
                "pagador": pagador,
                "monto_base_minor": monto_base_minor,
                "coeficiente_deuda": coeficiente_deuda,
                "monto_adeudado_minor": monto_adeudado_minor,
            },
            message=f"Gasto compartido registrado para hogar_id={hogar_id}, pagador='{pagador}'.",
        )

    # ----------------------------------------------------------
    # LIST / SETTLE
    # ----------------------------------------------------------

    def list_shared_expenses(
        self,
        hogar_id: int,
        estado: Optional[str] = None,
        pagador: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        return self._gastos_repo.listar_enriquecida(hogar_id, estado=estado, pagador=pagador)

    def settle_expense(self, gasto_id: int, hogar_id: int) -> SharedExpensesResult:
        """
        Requiere ambos ids para validar pertenencia — mismo criterio que
        FeesService.remove_extra_charge(): un gasto_id que existe pero
        pertenece a otro hogar_id se rechaza, en vez de saldar el gasto
        equivocado en silencio.

        Raises:
            GastoCompartidoNotFoundError si gasto_id no existe o no
                                         pertenece a hogar_id.
        """
        gasto = self._gastos_repo.obtener_por_id(gasto_id)
        if gasto is None or gasto["hogar_id"] != hogar_id:
            raise GastoCompartidoNotFoundError(
                f"Gasto compartido id={gasto_id} not found on hogar id={hogar_id}."
            )

        self._gastos_repo.marcar_saldado(gasto_id)

        return SharedExpensesResult(
            success=True,
            entity_id=gasto_id,
            data={"gasto_id": gasto_id, "hogar_id": hogar_id},
            message=f"Gasto compartido id={gasto_id} marcado como saldado.",
        )

    # ----------------------------------------------------------
    # NET BALANCE (read-only, con mensaje de presentación)
    # ----------------------------------------------------------

    def get_net_balance(self, hogar_id: int) -> dict:
        """
        Agregación de solo lectura + mensaje de presentación (armado acá,
        no en el repositorio). saldo_neto_minor > 0: te deben. < 0: debés.
        == 0: a mano.

        Raises:
            HogarNotFoundError si hogar_id no existe.
        """
        self._get_hogar(hogar_id)  # validate existence
        saldo_neto_minor = self._gastos_repo.obtener_saldo_neto(hogar_id)

        if saldo_neto_minor > 0:
            mensaje = f"Te deben {amount_display(saldo_neto_minor)}."
        elif saldo_neto_minor < 0:
            mensaje = f"Debés {amount_display(abs(saldo_neto_minor))}."
        else:
            mensaje = "Están a mano."

        return {"saldo_neto_minor": saldo_neto_minor, "mensaje": mensaje}
