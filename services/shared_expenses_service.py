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

    add_shared_purchase() (agregado después, ronda de "Compras en cuotas
    compartidas"): orquesta compartir una compra en cuotas completa según
    su modo_deuda (total_unico / prorrateado, columnas del schema que
    existían desde hace tiempo pero no tenían ninguna orquestación real
    todavía — confirmado por lectura de este archivo y de fees_service.py
    antes de escribirlo). Lee ComprasCuotasRepository/CuotasCreditoRepository
    (propiedad de FeesService) solo para lectura, nunca escribe esas
    tablas — ver docstring del método para el detalle de ambos modos.
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
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuotas_credito_repository import CuotasCreditoRepository

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


class CompraNotFoundError(SharedExpensesError):
    """Raised when a referenced compras_cuotas (installment purchase) does not exist."""


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
        # Solo LECTURA: add_shared_purchase() necesita leer compras_cuotas/
        # cuotas_credito para armar el/los gasto(s) compartido(s), pero
        # nunca escribe en esas tablas (las posee FeesService) — permitido
        # por CLAUDE.md §2 (services/ puede importar de repositories/).
        self._compras_repo = ComprasCuotasRepository(db)
        self._cuotas_repo = CuotasCreditoRepository(db)

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

    def list_my_hogares(self, usuario_local: str) -> list[dict]:
        """
        Lista los hogares de los que usuario_local es miembro. Agregado para
        la UI del ícono "Compartir" del Registro (necesita saber si el
        usuario ya pertenece a algún hogar antes de ofrecer compartir un
        gasto, y entre cuáles elegir si tiene más de uno). No existía forma
        de responder "a qué hogares pertenezco" — HogarMiembrosRepository
        solo se consultaba en la dirección hogar->miembros
        (listar_miembros()) — así que compone
        HogarMiembrosRepository.listar_hogares_de_usuario() (nueva, agregada
        junto con este método) + HogaresRepository.obtener_por_id() por cada
        fila, mismo patrón de composición ya usado en DashboardService.

        Returns:
            Lista de dicts {hogar_id, nombre, codigo_invitacion,
            porcentaje_default}, ordenada por hogar_id. Lista vacía si
            usuario_local no es miembro de ningún hogar.
        """
        filas_miembro = self._miembros_repo.listar_hogares_de_usuario(usuario_local)
        hogares = []
        for fila in filas_miembro:
            hogar = self._hogares_repo.obtener_por_id(fila["hogar_id"])
            if hogar is None:
                continue
            hogares.append({
                "hogar_id": hogar["id"],
                "nombre": hogar["nombre"],
                "codigo_invitacion": hogar["codigo_invitacion"],
                "porcentaje_default": fila["porcentaje_default"],
            })
        return hogares

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
    # ADD SHARED PURCHASE (compras_cuotas / cuotas_credito)
    # ----------------------------------------------------------

    def add_shared_purchase(
        self,
        compra_id: int,
        hogar_id: int,
        pagador: str,
        coeficiente_deuda: float,
    ) -> SharedExpensesResult:
        """
        Orquesta la creación de gasto(s) compartido(s) a partir de una
        compra en cuotas YA CARGADA (compras_cuotas), según su
        modo_deuda — la pieza que faltaba entre el schema (que ya soporta
        modo_deuda/monto_reintegro_minor desde hace tiempo, ver
        docs/DATA_MODEL_DECISIONS.md sección 2) y la UI, que hasta ahora
        solo podía compartir transacciones sueltas vía add_shared_expense().
        Confirmado por lectura de este archivo y de fees_service.py antes
        de escribir esto: no existía ninguna orquestación de este tipo.

        Vive en SharedExpensesService (no en FeesService) porque el
        resultado son filas de `gastos_compartidos` — tabla que este
        service ya posee — y solo LEE compras_cuotas/cuotas_credito
        (propiedad de FeesService), nunca las escribe.

        - modo_deuda='total_unico': UN gasto_compartido,
          origen_tipo='compra_cuotas', origen_id=compra_id,
          monto_base_minor = monto_total_minor - monto_reintegro_minor.
          Es una única operación (no un lote): si ya existe un gasto
          compartido para esa compra, se aborta con
          GastoCompartidoDuplicadoError — mismo criterio que
          add_shared_expense().

        - modo_deuda='prorrateado': UN gasto_compartido POR CADA
          cuotas_credito de la compra, origen_tipo='cuota_credito',
          origen_id=cuota.id, monto_base_minor = monto_cuota_minor -
          round(monto_reintegro_minor / total_cuotas), fecha = primer día
          del mes/año proyectado de esa cuota específica. cuotas_credito
          no tiene una columna de día exacto de vencimiento (solo
          mes_proyectado/anio_proyectado, ver schema) — "01" es un
          placeholder de presentación para poder completar
          gastos_compartidos.fecha (TEXT NOT NULL), no una columna
          inventada. Acá SÍ se decidió SALTEAR (no abortar todo el lote)
          las cuotas que ya tengan un gasto compartido asociado: permite
          compartir el resto de una compra a la que ya se le compartieron
          algunas cuotas sueltas antes, sin bloquear todo el lote por una
          sola cuota ya cubierta.

        categoria_id se hereda de la compra (compras_cuotas.categoria_id)
        para todas las filas generadas — cuotas_credito no tiene columna
        de categoría propia, la compra es la unidad de clasificación.

        Args:
            compra_id:         La compra en cuotas a compartir.
            hogar_id:          Hogar al que se imputa el/los gasto(s).
            pagador:           Debe ser miembro de hogar_id.
            coeficiente_deuda: Porcentaje (0, 100] del OTRO miembro —
                               mismo contrato que add_shared_expense(),
                               se aplica igual a cada fila generada.

        Returns:
            SharedExpensesResult con entity_id=compra_id y data={
                "modo_deuda", "gastos_creados" (cantidad), "gasto_ids",
                "cuotas_ya_compartidas" (ids de cuota salteados por
                duplicado — lista vacía en modo total_unico)}.

        Raises:
            HogarNotFoundError si hogar_id no existe.
            MiembroNotFoundError si pagador no es miembro de hogar_id.
            CompraNotFoundError si compra_id no existe.
            ValueError si coeficiente_deuda fuera de (0, 100], o si
                       modo_deuda='prorrateado' y la compra no tiene
                       ninguna cuota generada.
            GastoCompartidoDuplicadoError si modo_deuda='total_unico' y
                       ya existe un gasto compartido para esa compra.
        """
        self._get_hogar(hogar_id)  # validate existence

        miembro = self._miembros_repo.obtener_miembro(hogar_id, pagador)
        if miembro is None:
            raise MiembroNotFoundError(f"'{pagador}' is not a member of hogar id={hogar_id}.")

        if not (0 < coeficiente_deuda <= 100):
            raise ValueError(
                f"coeficiente_deuda must be between 0 (exclusive) and 100 (inclusive). "
                f"Received: {coeficiente_deuda}."
            )

        compra = self._compras_repo.obtener_por_id(compra_id)
        if compra is None:
            raise CompraNotFoundError(f"Compra id={compra_id} not found.")

        reintegro_total = compra["monto_reintegro_minor"] or 0

        conn = self._db.conn
        resultado_data: dict = {}
        with self._db.transaction():
            if compra["modo_deuda"] == "total_unico":
                if self._gastos_repo.listar_por_origen("compra_cuotas", compra_id):
                    raise GastoCompartidoDuplicadoError(
                        f"Ya existe un gasto compartido para la compra id={compra_id}."
                    )
                monto_base_minor = compra["monto_total_minor"] - reintegro_total
                monto_adeudado_minor = round(monto_base_minor * coeficiente_deuda / 100)
                gasto_id = self._gastos_repo.crear(
                    hogar_id=hogar_id, pagador=pagador, origen_tipo="compra_cuotas",
                    origen_id=compra_id, categoria_id=compra["categoria_id"],
                    monto_base_minor=monto_base_minor, coeficiente_deuda=coeficiente_deuda,
                    monto_adeudado_minor=monto_adeudado_minor, fecha=compra["fecha_compra"],
                    conn=conn,
                )
                resultado_data = {
                    "modo_deuda": "total_unico",
                    "gastos_creados": 1,
                    "gasto_ids": [gasto_id],
                    "cuotas_ya_compartidas": [],
                }
            else:
                cuotas = self._cuotas_repo.listar_por_compra(compra_id)
                if not cuotas:
                    raise ValueError(f"Compra id={compra_id} no tiene cuotas generadas para prorratear.")

                total_cuotas = compra["total_cuotas"]
                gasto_ids: list[int] = []
                cuotas_ya_compartidas: list[int] = []
                for cuota in cuotas:
                    if self._gastos_repo.listar_por_origen("cuota_credito", cuota["id"]):
                        cuotas_ya_compartidas.append(cuota["id"])
                        continue
                    reintegro_de_cuota = round(reintegro_total / total_cuotas)
                    monto_base_minor = cuota["monto_cuota_minor"] - reintegro_de_cuota
                    monto_adeudado_minor = round(monto_base_minor * coeficiente_deuda / 100)
                    fecha_cuota = f"{cuota['anio_proyectado']:04d}-{cuota['mes_proyectado']:02d}-01"
                    gasto_id = self._gastos_repo.crear(
                        hogar_id=hogar_id, pagador=pagador, origen_tipo="cuota_credito",
                        origen_id=cuota["id"], categoria_id=compra["categoria_id"],
                        monto_base_minor=monto_base_minor, coeficiente_deuda=coeficiente_deuda,
                        monto_adeudado_minor=monto_adeudado_minor, fecha=fecha_cuota,
                        conn=conn,
                    )
                    gasto_ids.append(gasto_id)

                resultado_data = {
                    "modo_deuda": "prorrateado",
                    "gastos_creados": len(gasto_ids),
                    "gasto_ids": gasto_ids,
                    "cuotas_ya_compartidas": cuotas_ya_compartidas,
                }

        if resultado_data["modo_deuda"] == "total_unico":
            mensaje = f"Gasto compartido único registrado para compra id={compra_id}."
        else:
            mensaje = f"{resultado_data['gastos_creados']} gasto(s) compartido(s) registrado(s) para compra id={compra_id}"
            if resultado_data["cuotas_ya_compartidas"]:
                mensaje += f" ({len(resultado_data['cuotas_ya_compartidas'])} cuota(s) ya tenían uno, salteadas)."
            else:
                mensaje += "."

        return SharedExpensesResult(success=True, entity_id=compra_id, data=resultado_data, message=mensaje)

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

    def get_shared_expense_by_origin(self, origen_tipo: str, origen_id: int) -> Optional[sqlite3.Row]:
        """
        Devuelve el gasto compartido asociado a un origen
        (transacción/compra en cuotas/cuota de crédito) si ya existe, o
        None. Agregado para que la UI (ícono "Compartir" por fila del
        Registro) sepa si una transacción ya tiene un gasto compartido
        antes de ofrecer crear uno nuevo — mismo chequeo que
        add_shared_expense() hace internamente para bloquear duplicados con
        GastoCompartidoDuplicadoError, expuesto acá de forma consultable sin
        tener que intentar crear uno para descubrirlo.
        """
        filas = self._gastos_repo.listar_por_origen(origen_tipo, origen_id)
        return filas[0] if filas else None

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
