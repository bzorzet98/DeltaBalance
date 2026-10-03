"""
DeltaBalance — services/presupuestos_service.py

Presupuestos de cada mes (tabla presupuestos, estructura final de
db/schema_migrations.py), en dos tipos:
    'fijo'      concepto libre (ALQUILER, SEGURO, LUZ…) con estimado y real
                cargados a mano (monto_real_minor).
    'variable'  una categoría de egreso del Registro, con el real CALCULADO
                de las transacciones del mes (get_real_variable()) — nunca
                se guarda —, o el ítem especial COMPARTIDOS.

COMPARTIDOS no es una categoría del Registro: es una fila 'variable' con
categoria_id NULL y concepto CONCEPTO_COMPARTIDOS. Quien llama lo pide con
categoria_id=CATEGORIA_COMPARTIDOS (los ids reales son UUID: no chocan) y
este service lo traduce. Su real (get_real_variable_compartidos(), decisión
del usuario): lo que el usuario DEBE por los gastos compartidos del mes que
pagó el OTRO miembro del hogar — suma de monto_adeudado_minor de los gastos
con pagador ≠ usuario_local (nombres comparados normalizados,
utils/personas.py). Lo que pagó el usuario ya cuenta en su categoría, con
solo su parte (ver abajo). Sin usuario_local no se puede saber qué pagó
cada uno: el real de COMPARTIDOS queda en None.

Real de una categoría (get_real_variable()): egresos del Registro de ese
mes, categoría y moneda (sin las eliminadas, mismo criterio que
DashboardService.get_gasto_por_categoria()). Si una transacción está
compartida (gastos_compartidos con origen_tipo='transaccion'), cuenta solo
la parte del usuario: el monto menos monto_adeudado_minor (lo que le debe
el otro miembro — una transacción del Registro la pagó el usuario).

Moneda de un gasto compartido (gastos_compartidos no tiene moneda): la de
su origen (transacción, compra en cuotas o la compra de la cuota). Si el
origen no está en esta base — es una transacción del otro miembro, que no
se sincroniza —, MONEDA_SIN_ORIGEN_CODIGO, el mismo default que muestra la
pantalla de Gastos compartidos.

Reglas de dominio (acá, no en el repositorio):
- Concepto de un fijo: sin espacios de más y en MAYÚSCULAS, no vacío.
- Un solo variable por categoría (y un solo COMPARTIDOS) por mes — la
  regla del viejo UNIQUE(categoria_id, mes, anio): PresupuestoDuplicadoError.
- Montos enteros (minor units) >= 0; moneda_id tiene que existir; mes entre
  1 y 12. El real de un variable no se edita (se calcula).
- Edición y borrado directos: un presupuesto no tiene dependencias con
  estado propio (ventana de corrección temprana, CLAUDE.md §4).
- copy_recurrentes(): los recurrentes del mes origen, con real = 0 y sin
  duplicar lo que el destino ya tiene.

Totales: siempre por moneda, nunca mezclados.

SQL propio solo en get_real_variable() / get_real_variable_compartidos():
son reportes agregados sobre transacciones y gastos_compartidos (tablas de
otros dominios), mismo criterio que DashboardService.get_gasto_por_categoria()
— no son CRUD de una tabla y no le corresponden a un repositorio. El resto
pasa por PresupuestosRepository / CategoriasRepository.

list_budgets() se mantiene para DashboardService.get_comparacion_presupuesto()
(pantalla Estadísticas): los variables de categoría, como antes.

Los mensajes de error y de PresupuestoResult ya vienen en MAYÚSCULAS: las
pantallas los muestran tal cual.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

from db.database import DatabaseManager
from repositories.categorias_repository import CategoriasRepository
from repositories.presupuestos_repository import PresupuestosRepository
from utils.personas import normalizar_persona

# Ítem especial COMPARTIDOS (ver docstring del módulo).
CATEGORIA_COMPARTIDOS = "COMPARTIDOS"
CONCEPTO_COMPARTIDOS = "COMPARTIDOS"

# Moneda de un gasto compartido cuyo origen no está en esta base.
MONEDA_SIN_ORIGEN_CODIGO = "ARS"

# update(): campos que se pueden cambiar según el tipo.
CAMPOS_EDITABLES = {
    "fijo": ("concepto", "monto_estimado_minor", "monto_real_minor", "moneda_id", "es_recurrente"),
    "variable": ("categoria_id", "monto_estimado_minor", "moneda_id", "es_recurrente"),
}


# =============================================================
# EXCEPTIONS
# =============================================================

class PresupuestoError(Exception):
    """Raised when a budget operation violates a business rule."""


class PresupuestoNotFoundError(PresupuestoError):
    """Raised when a referenced budget row does not exist."""


class CategoryNotFoundError(PresupuestoError):
    """
    Raised when a referenced category does not exist. Propia de este
    dominio (no la de TransactionService): dos services de dominios
    distintos no dependen el uno del otro para reportar un error.
    """


class CurrencyNotFoundError(PresupuestoError):
    """Raised when a referenced currency does not exist."""


class PresupuestoDuplicadoError(PresupuestoError):
    """Raised when the month already has a variable budget for that category (or COMPARTIDOS)."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class PresupuestoResult:
    success: bool
    entity_id: Optional[str] = None
    message: str = ""


# =============================================================
# SERVICE
# =============================================================

class PresupuestosService:
    """
    Usage:
        svc = PresupuestosService(db)
        svc.create_fijo("Alquiler", 15000000, moneda_id=1, mes=10, anio=2026, es_recurrente=1)
        svc.create_variable(categoria_super, 8000000, moneda_id=1, mes=10, anio=2026)
        svc.create_variable(CATEGORIA_COMPARTIDOS, 7500000, moneda_id=1, mes=10, anio=2026)
        svc.list_by_month(10, 2026, usuario_local="BRUNO")   # {"fijos": [...], "variables": [...]}
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._repo = PresupuestosRepository(db)
        self._categorias_repo = CategoriasRepository(db)

    # ----------------------------------------------------------
    # VALIDACIONES
    # ----------------------------------------------------------

    @staticmethod
    def _concepto(concepto: Optional[str]) -> str:
        limpio = " ".join((concepto or "").split()).upper()
        if not limpio:
            raise PresupuestoError("EL CONCEPTO NO PUEDE ESTAR VACÍO.")
        return limpio

    @staticmethod
    def _monto(monto_minor: Any) -> int:
        if not isinstance(monto_minor, int) or isinstance(monto_minor, bool):
            raise PresupuestoError(f"EL MONTO TIENE QUE SER UN ENTERO EN MINOR UNITS (RECIBIDO: {monto_minor!r}).")
        if monto_minor < 0:
            raise PresupuestoError("EL MONTO NO PUEDE SER NEGATIVO.")
        return monto_minor

    @staticmethod
    def _periodo(mes: Any, anio: Any) -> tuple[int, int]:
        for valor in (mes, anio):
            if not isinstance(valor, int) or isinstance(valor, bool):
                raise PresupuestoError(f"MES Y AÑO TIENEN QUE SER ENTEROS (RECIBIDO: {valor!r}).")
        if not 1 <= mes <= 12:
            raise PresupuestoError(f"EL MES TIENE QUE ESTAR ENTRE 1 Y 12 (RECIBIDO: {mes}).")
        return mes, anio

    def _moneda(self, moneda_id: Any) -> int:
        if moneda_id not in {fila["id"] for fila in self._db.obtener_monedas()}:
            raise CurrencyNotFoundError(f"LA MONEDA id={moneda_id} NO EXISTE.")
        return moneda_id

    @staticmethod
    def _recurrente(valor: Any) -> int:
        return 1 if valor else 0

    def _destino_variable(self, categoria_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """categoria_id pedido → (categoria_id, concepto) a guardar: el ítem COMPARTIDOS o una categoría existente."""
        if categoria_id == CATEGORIA_COMPARTIDOS:
            return None, CONCEPTO_COMPARTIDOS
        if not categoria_id or self._categorias_repo.obtener_por_id(categoria_id) is None:
            raise CategoryNotFoundError(f"LA CATEGORÍA {categoria_id} NO EXISTE.")
        return categoria_id, None

    def _sin_duplicado(
        self, mes: int, anio: int, categoria_id: Optional[str], concepto: Optional[str], excepto_id: Optional[str] = None,
    ) -> None:
        """Un solo variable por categoría (o un solo COMPARTIDOS) en el mes."""
        for fila in self._repo.listar_por_mes(mes, anio):
            if (
                fila["tipo"] == "variable" and fila["id"] != excepto_id
                and fila["categoria_id"] == categoria_id and fila["concepto"] == concepto
            ):
                nombre = fila["subcategoria"] or fila["concepto"] or ""
                raise PresupuestoDuplicadoError(f"'{nombre.upper()}' YA TIENE UN PRESUPUESTO EN {mes:02d}/{anio}.")

    def _obtener(self, presupuesto_id: str) -> sqlite3.Row:
        fila = self._repo.obtener_por_id(presupuesto_id)
        if fila is None:
            raise PresupuestoNotFoundError(f"EL PRESUPUESTO {presupuesto_id} NO EXISTE.")
        return fila

    @staticmethod
    def _rango_mes(mes: int, anio: int) -> tuple[str, str]:
        """[inicio, fin) del mes como 'YYYY-MM-DD' — mismo criterio que DashboardService._rango_mes()."""
        siguiente = date(anio + 1, 1, 1) if mes == 12 else date(anio, mes + 1, 1)
        return f"{anio:04d}-{mes:02d}-01", siguiente.isoformat()

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def create_fijo(
        self,
        concepto: str,
        monto_estimado_minor: int,
        moneda_id: int,
        mes: int,
        anio: int,
        es_recurrente: int = 0,
        monto_real_minor: int = 0,
    ) -> PresupuestoResult:
        """
        Crea un presupuesto fijo (concepto libre, real cargado a mano).

        Raises:
            PresupuestoError si el concepto está vacío, algún monto no es un
            entero >= 0 o el mes no está entre 1 y 12.
            CurrencyNotFoundError si la moneda no existe.
        """
        concepto = self._concepto(concepto)
        mes, anio = self._periodo(mes, anio)
        presupuesto_id = self._repo.crear(
            tipo="fijo",
            moneda_id=self._moneda(moneda_id),
            mes=mes,
            anio=anio,
            monto_estimado_minor=self._monto(monto_estimado_minor),
            concepto=concepto,
            es_recurrente=self._recurrente(es_recurrente),
            monto_real_minor=self._monto(monto_real_minor),
        )
        return PresupuestoResult(success=True, entity_id=presupuesto_id, message=f"FIJO '{concepto}' AGREGADO.")

    def create_variable(
        self,
        categoria_id: str,
        monto_estimado_minor: int,
        moneda_id: int,
        mes: int,
        anio: int,
        es_recurrente: int = 0,
    ) -> PresupuestoResult:
        """
        Crea un presupuesto variable: de una categoría del Registro, o el
        ítem COMPARTIDOS con categoria_id=CATEGORIA_COMPARTIDOS. Su real se
        calcula (list_by_month()), no se carga.

        Raises:
            CategoryNotFoundError si la categoría no existe.
            PresupuestoDuplicadoError si el mes ya tiene esa categoría (o COMPARTIDOS).
            PresupuestoError si el monto no es un entero >= 0 o el mes no
            está entre 1 y 12.
            CurrencyNotFoundError si la moneda no existe.
        """
        mes, anio = self._periodo(mes, anio)
        categoria, concepto = self._destino_variable(categoria_id)
        self._sin_duplicado(mes, anio, categoria, concepto)
        presupuesto_id = self._repo.crear(
            tipo="variable",
            moneda_id=self._moneda(moneda_id),
            mes=mes,
            anio=anio,
            monto_estimado_minor=self._monto(monto_estimado_minor),
            concepto=concepto,
            categoria_id=categoria,
            es_recurrente=self._recurrente(es_recurrente),
        )
        return PresupuestoResult(success=True, entity_id=presupuesto_id, message="PRESUPUESTO VARIABLE AGREGADO.")

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def list_by_month(self, mes: int, anio: int, usuario_local: Optional[str] = None) -> dict:
        """
        Los presupuestos del mes, separados: {"fijos": [...], "variables":
        [...]}, cada uno en orden de alta. Cada fila es un dict con sus
        columnas, la moneda (currency_code, currency_symbol, decimales) y:
            nombre          concepto (fijo / COMPARTIDOS) o la subcategoría.
            es_compartidos  True solo para el ítem COMPARTIDOS.
            real_minor      fijo: monto_real_minor; variable: calculado
                            (get_real_variable() / _compartidos()); None
                            en COMPARTIDOS si no hay usuario_local.

        Raises:
            PresupuestoError si el mes no está entre 1 y 12.
        """
        mes, anio = self._periodo(mes, anio)
        listado: dict[str, list[dict]] = {"fijos": [], "variables": []}
        for fila in self._repo.listar_por_mes(mes, anio):
            p = dict(fila)  # CLAUDE.md §11
            if p["tipo"] == "fijo":
                p.update(nombre=p["concepto"] or "", es_compartidos=False, real_minor=p["monto_real_minor"])
                listado["fijos"].append(p)
                continue
            es_compartidos = p["categoria_id"] is None
            if es_compartidos:
                real = (
                    self.get_real_variable_compartidos(p["moneda_id"], mes, anio, usuario_local)
                    if usuario_local else None
                )
            else:
                real = self.get_real_variable(p["categoria_id"], p["moneda_id"], mes, anio)
            p.update(
                nombre=(p["concepto"] if es_compartidos else p["subcategoria"]) or "",
                es_compartidos=es_compartidos,
                real_minor=real,
            )
            listado["variables"].append(p)
        return listado

    def list_budgets(self, mes: int, anio: int) -> list[dict]:
        """
        Los variables de categoría del mes (sin COMPARTIDOS ni fijos), con
        subcategoria/categoria_principal, ordenados por categoria_principal
        y subcategoria — lo que usa DashboardService.get_comparacion_presupuesto().
        """
        mes, anio = self._periodo(mes, anio)
        variables = [
            dict(fila) for fila in self._repo.listar_por_mes(mes, anio)
            if fila["tipo"] == "variable" and fila["categoria_id"] is not None
        ]
        return sorted(variables, key=lambda p: (p["categoria_principal"] or "", p["subcategoria"] or ""))

    # ----------------------------------------------------------
    # REAL CALCULADO DE LOS VARIABLES (ver docstring del módulo)
    # ----------------------------------------------------------

    def get_real_variable(self, categoria_id: str, moneda_id: int, mes: int, anio: int) -> int:
        """
        Egresos del Registro de ese mes en esa categoría y moneda (sin las
        transacciones eliminadas). Una transacción compartida cuenta solo
        la parte del usuario: su monto menos monto_adeudado_minor.

        Raises:
            PresupuestoError si el mes no está entre 1 y 12.
        """
        mes, anio = self._periodo(mes, anio)
        inicio, fin = self._rango_mes(mes, anio)
        fila = self._db.fetchone(
            """
            SELECT COALESCE(SUM(t.monto_minor - COALESCE(g.monto_adeudado_minor, 0)), 0) AS real_minor
            FROM transacciones t
            LEFT JOIN gastos_compartidos g
                   ON g.origen_tipo = 'transaccion' AND g.origen_id = t.id
            WHERE t.categoria_id = ?
              AND t.moneda_id = ?
              AND t.tipo_movimiento = 'egreso'
              AND t.fecha >= ?
              AND t.fecha < ?
              AND t.deleted_at IS NULL;
            """,
            (categoria_id, moneda_id, inicio, fin),
        )
        return fila["real_minor"]

    def get_real_variable_compartidos(
        self, moneda_id: int, mes: int, anio: int, usuario_local: Optional[str] = None,
    ) -> int:
        """
        Lo que el usuario debe por los gastos compartidos del mes (por su
        fecha) que pagó el otro miembro: suma de monto_adeudado_minor de los
        gastos con pagador ≠ usuario_local, en esa moneda (la del origen;
        MONEDA_SIN_ORIGEN_CODIGO si el origen no está en esta base).

        Raises:
            PresupuestoError si falta usuario_local (sin él no se sabe qué
            pagó cada uno) o el mes no está entre 1 y 12.
        """
        yo = normalizar_persona(usuario_local)
        if not yo:
            raise PresupuestoError("FALTA EL USUARIO LOCAL: NO SE PUEDE SABER QUÉ GASTOS COMPARTIDOS PAGÓ EL OTRO.")
        mes, anio = self._periodo(mes, anio)
        inicio, fin = self._rango_mes(mes, anio)
        sin_origen = self._db.obtener_moneda_por_codigo(MONEDA_SIN_ORIGEN_CODIGO)
        filas = self._db.fetchall(
            """
            SELECT g.pagador, g.monto_adeudado_minor,
                   COALESCE(t.moneda_id, cc.moneda_id, ccq.moneda_id, ?) AS moneda_id
            FROM gastos_compartidos g
            LEFT JOIN transacciones t    ON g.origen_tipo = 'transaccion'   AND t.id = g.origen_id
            LEFT JOIN compras_cuotas cc  ON g.origen_tipo = 'compra_cuotas' AND cc.id = g.origen_id
            LEFT JOIN cuotas_credito q   ON g.origen_tipo = 'cuota_credito' AND q.id = g.origen_id
            LEFT JOIN compras_cuotas ccq ON ccq.id = q.compra_id
            WHERE g.fecha >= ? AND g.fecha < ?;
            """,
            (sin_origen["id"] if sin_origen is not None else None, inicio, fin),
        )
        return sum(
            fila["monto_adeudado_minor"] for fila in filas
            if fila["moneda_id"] == moneda_id and normalizar_persona(fila["pagador"]) != yo
        )

    # ----------------------------------------------------------
    # TOTALES
    # ----------------------------------------------------------

    def get_totales(self, mes: int, anio: int, usuario_local: Optional[str] = None) -> dict:
        """
        Totales del mes por moneda (ver totales_de()).

        Raises:
            PresupuestoError si el mes no está entre 1 y 12.
        """
        return self.totales_de(self.list_by_month(mes, anio, usuario_local))

    @staticmethod
    def totales_de(listado: dict) -> dict:
        """
        Totales por moneda de un resultado de list_by_month() ya pedido (así
        quien ya tiene el listado no recalcula los reales): {currency_code:
        {moneda_id, currency_symbol, decimales, fijos_estimado, fijos_real,
        variables_estimado, variables_real}}. Un real None (COMPARTIDOS sin
        usuario_local) no suma.
        """
        totales: dict[str, dict] = {}
        for seccion, filas in (("fijos", listado["fijos"]), ("variables", listado["variables"])):
            for p in filas:
                total = totales.setdefault(p["currency_code"], {
                    "moneda_id": p["moneda_id"],
                    "currency_symbol": p["currency_symbol"] or "",
                    "decimales": p["decimales"],
                    "fijos_estimado": 0,
                    "fijos_real": 0,
                    "variables_estimado": 0,
                    "variables_real": 0,
                })
                total[f"{seccion}_estimado"] += p["monto_estimado_minor"]
                total[f"{seccion}_real"] += p["real_minor"] or 0
        return totales

    # ----------------------------------------------------------
    # UPDATE / DELETE
    # ----------------------------------------------------------

    def update(self, id: str, **kwargs: Any) -> PresupuestoResult:
        """
        Update parcial: solo los campos pasados, que dependen del tipo
        (CAMPOS_EDITABLES): un fijo edita concepto y real; un variable,
        su categoría (o CATEGORIA_COMPARTIDOS) — su real no se edita. Mismas
        validaciones que create_fijo() / create_variable().

        Returns:
            PresupuestoResult con success=False si no se pasó ningún campo.

        Raises:
            PresupuestoNotFoundError si la fila no existe.
            PresupuestoError (o una subclase) si un campo no es editable en
            ese tipo o no es válido.
        """
        fila = self._obtener(id)
        tipo = fila["tipo"]
        desconocidos = set(kwargs) - set(CAMPOS_EDITABLES[tipo])
        if desconocidos:
            raise PresupuestoError(f"CAMPOS NO EDITABLES EN UN PRESUPUESTO {tipo.upper()}: {', '.join(sorted(desconocidos))}.")
        if not kwargs:
            return PresupuestoResult(success=False, entity_id=id, message="NO HAY CAMPOS PARA ACTUALIZAR.")

        validadores = {
            "concepto": self._concepto,
            "monto_estimado_minor": self._monto,
            "monto_real_minor": self._monto,
            "moneda_id": self._moneda,
            "es_recurrente": self._recurrente,
        }
        campos = {campo: validadores[campo](valor) for campo, valor in kwargs.items() if campo != "categoria_id"}
        if "categoria_id" in kwargs:
            categoria, concepto = self._destino_variable(kwargs["categoria_id"])
            self._sin_duplicado(fila["mes"], fila["anio"], categoria, concepto, excepto_id=id)
            campos.update(categoria_id=categoria, concepto=concepto)
        actualizado = self._repo.actualizar(id, **campos)
        return PresupuestoResult(
            success=actualizado, entity_id=id,
            message="PRESUPUESTO ACTUALIZADO." if actualizado else "EL PRESUPUESTO NO CAMBIÓ.",
        )

    def delete(self, id: str) -> PresupuestoResult:
        """
        DELETE físico (sin dependencias con estado propio, CLAUDE.md §4).

        Raises:
            PresupuestoNotFoundError si la fila no existe.
        """
        self._obtener(id)
        self._repo.eliminar(id)
        return PresupuestoResult(success=True, entity_id=id, message="PRESUPUESTO ELIMINADO.")

    # ----------------------------------------------------------
    # COPIAR RECURRENTES
    # ----------------------------------------------------------

    def copy_recurrentes(self, mes_origen: int, anio_origen: int, mes_destino: int, anio_destino: int) -> int:
        """
        Copia los presupuestos recurrentes (fijos y variables) del mes
        origen al destino, con real = 0 y sin duplicar lo que el destino ya
        tiene. Devuelve cuántos copió.

        Raises:
            PresupuestoError si algún mes no está entre 1 y 12, o si origen
            y destino son el mismo mes.
        """
        mes_origen, anio_origen = self._periodo(mes_origen, anio_origen)
        mes_destino, anio_destino = self._periodo(mes_destino, anio_destino)
        if (mes_origen, anio_origen) == (mes_destino, anio_destino):
            raise PresupuestoError("EL MES ORIGEN Y EL DESTINO SON EL MISMO.")
        return self._repo.copiar_recurrentes(mes_origen, anio_origen, mes_destino, anio_destino)
