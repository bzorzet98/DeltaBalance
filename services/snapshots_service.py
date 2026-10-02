"""
DeltaBalance — services/snapshots_service.py

Purpose:
    Snapshots de cierre de mes (tablas saldos_mensuales, deudas_mensuales y
    compartidos_mensuales — db/schema_migrations.py MIGRACIONES_TABLA): el
    "saldo anterior" de las pantallas sin recorrer toda la historia cada
    vez. Son un CACHÉ derivado: todo lo que guardan sale de otras tablas y
    se puede recalcular cuando se quiera (recalcular_todo() /
    recalcular_desde()). El mes en curso NUNCA se guarda: siempre se
    calcula en vivo. Ver docs/DATA_MODEL_DECISIONS.md sección 21.

Reglas de cálculo — viven una sola vez acá y se usan igual para escribir un
snapshot y para calcular en vivo:

- saldos_mensuales, por (cuenta, moneda) de cuentas_saldos:
  saldo_inicial_minor + las transacciones no eliminadas hasta el último día
  del mes, con el MISMO signo que vw_balance_cuentas (db/schema.sql):
  ingreso suma, egreso resta, 'movimiento' suma. Se calcula mes a mes
  acumulando el anterior. Así, snapshot del mes anterior + movimientos del
  mes en curso = el saldo que muestra la app. Incluye las cuentas
  archivadas (sus meses viejos siguen siendo historia válida).
- deudas_mensuales, por (persona, tab, moneda): SUM(monto_minor) — ya con
  signo — de las filas de `deudas` de ese tab ('me_deben' / 'debo', libro
  de movimientos, docs/DATA_MODEL_DECISIONS.md sección 22) con fecha <=
  último día del mes: el mismo saldo que DebtsService.summary_by_person()
  a esa fecha. Los dos tabs nunca se mezclan (la misma persona puede
  deberte algo y vos deberle otra cosa). La persona se normaliza
  (utils/personas.py): "Noe" y "NOE" son la misma fila.
- compartidos_mensuales, por (hogar, pagador, moneda): el
  monto_pendiente_minor ACTUAL de los gastos 'pendiente' con fecha <=
  último día del mes, tal como está guardado (su signo lo interpreta la
  UI según quién pagó). gastos_compartidos no tiene moneda: sale del
  origen (la transacción, la compra en cuotas, o la compra de la cuota).
  Un gasto cuyo origen ya no existe se saltea (se cuenta en el mensaje).

Rango: desde el primer mes con datos (transacciones, filas de deudas o
gastos pendientes) hasta el mes anterior al actual.

Límite conocido (consecuencia de la regla pedida para compartidos): usan el
pendiente de HOY. Un pago registrado hoy sobre un gasto viejo cambia lo que
"debería" decir un snapshot ya guardado, que queda desactualizado hasta el
próximo recálculo (botón ↻ de las pantallas). Deudas ya no tiene ese
problema: un pago es una fila con su propia fecha, así que un mes cerrado
solo cambia si se carga, edita o borra una fila con fecha de ese mes —
igual que los saldos con una transacción de un mes ya cerrado
(recalcular_desde() de ese mes).

Lecturas de "saldo anterior" (get_saldo_anterior_* por clave, y
get_saldos_anteriores_* de a muchos para las pantallas): el snapshot del
mes anterior si existe; si falta (nunca se recalculó, o empezó un mes
nuevo desde el último recálculo) se calcula en vivo con la misma regla —
en el primer mes de la app eso da saldo_inicial_minor. Las lecturas nunca
escriben.

Reglas de arquitectura: las fuentes se leen solo con sus repositorios
(cuentas, transacciones, deudas, gastos compartidos, compras/cuotas —
SOLO lectura: esas tablas son de sus propios services) y se escribe solo
en los tres repositorios de snapshots. Sin SQL directo (CLAUDE.md §1).
"""

import calendar
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

from db.database import DatabaseManager
from repositories.compartidos_mensuales_repository import CompartidosMensualesRepository
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuentas_repository import CuentasRepository
from repositories.cuotas_credito_repository import CuotasCreditoRepository
from repositories.deudas_mensuales_repository import DeudasMensualesRepository
from repositories.deudas_repository import TABS, DeudasRepository
from repositories.gastos_compartidos_repository import GastosCompartidosRepository
from repositories.saldos_mensuales_repository import SaldosMensualesRepository
from repositories.transacciones_repository import TransaccionesRepository
from utils.personas import normalizar_persona

# (anio, mes): así los períodos se comparan y se ordenan como tuplas.
Periodo = tuple[int, int]

LOTE_LECTURA = 1000
# recalcular_todo() borra desde acá: todos los snapshots, sean del mes que sean.
PRIMER_PERIODO_POSIBLE: Periodo = (1, 1)


# =============================================================
# EXCEPTIONS
# =============================================================

class SnapshotsError(Exception):
    """Raised when a snapshot operation receives invalid input (ej. un mes fuera de 1-12)."""


# =============================================================
# RESULT TYPE
# =============================================================

@dataclass
class SnapshotResult:
    success: bool
    meses_calculados: int
    tiempo_segundos: float
    message: str


# =============================================================
# SERVICE
# =============================================================

class SnapshotsService:
    """
    Usage:
        svc = SnapshotsService(db)
        svc.recalcular_todo()
        svc.get_saldo_anterior_cuenta(cuenta_id=1, moneda_id=1, mes=10, anio=2026)
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._saldos_repo = SaldosMensualesRepository(db)
        self._deudas_repo = DeudasMensualesRepository(db)
        self._compartidos_repo = CompartidosMensualesRepository(db)
        # Solo LECTURA: las fuentes de los snapshots (ver docstring del módulo).
        self._cuentas_repo = CuentasRepository(db)
        self._transacciones_repo = TransaccionesRepository(db)
        self._deudas_fuente_repo = DeudasRepository(db)
        self._gastos_repo = GastosCompartidosRepository(db)
        self._compras_repo = ComprasCuotasRepository(db)
        self._cuotas_repo = CuotasCreditoRepository(db)

    # ----------------------------------------------------------
    # PERÍODOS
    # ----------------------------------------------------------

    @staticmethod
    def _periodo(mes: int, anio: int) -> Periodo:
        if not isinstance(mes, int) or not 1 <= mes <= 12:
            raise SnapshotsError(f"mes must be between 1 and 12. Received: {mes!r}.")
        if not isinstance(anio, int) or anio < 1:
            raise SnapshotsError(f"anio must be a positive integer. Received: {anio!r}.")
        return (anio, mes)

    @staticmethod
    def _anterior(periodo: Periodo) -> Periodo:
        anio, mes = periodo
        return (anio - 1, 12) if mes == 1 else (anio, mes - 1)

    @staticmethod
    def _siguiente(periodo: Periodo) -> Periodo:
        anio, mes = periodo
        return (anio + 1, 1) if mes == 12 else (anio, mes + 1)

    @staticmethod
    def _fin_de_mes(periodo: Periodo) -> str:
        anio, mes = periodo
        return f"{anio:04d}-{mes:02d}-{calendar.monthrange(anio, mes)[1]:02d}"

    @staticmethod
    def _periodo_de_fecha(fecha: str) -> Periodo:
        return (int(fecha[:4]), int(fecha[5:7]))

    def _meses(self, desde: Periodo, hasta: Periodo) -> list[Periodo]:
        """Todos los meses de `desde` a `hasta`, inclusive (vacío si desde > hasta)."""
        meses = []
        actual = desde
        while actual <= hasta:
            meses.append(actual)
            actual = self._siguiente(actual)
        return meses

    def _ultimo_mes_cerrado(self) -> Periodo:
        hoy = date.today()
        return self._anterior((hoy.year, hoy.month))

    @staticmethod
    def _persona(nombre: Optional[str]) -> str:
        """Clave de persona de deudas_mensuales (utils/personas.py, igual que DebtsService)."""
        return normalizar_persona(nombre)

    @staticmethod
    def _tab(tab: str) -> str:
        if tab not in TABS:
            raise SnapshotsError(f"tab must be 'me_deben' or 'debo'. Received: {tab!r}.")
        return tab

    # ----------------------------------------------------------
    # LECTURA DE LAS FUENTES (solo repositorios, paginado)
    # ----------------------------------------------------------

    @staticmethod
    def _paginado(leer_pagina: Callable[[int], list]) -> list[dict]:
        filas: list[dict] = []
        pagina = 1
        while True:
            lote = leer_pagina(pagina)
            filas += [dict(f) for f in lote]
            if len(lote) < LOTE_LECTURA:
                return filas
            pagina += 1

    def _transacciones(
        self, fecha_hasta: str, cuenta_id: Optional[str] = None, moneda_id: Optional[int] = None,
    ) -> list[dict]:
        """Transacciones no eliminadas hasta fecha_hasta (inclusive)."""
        return self._paginado(lambda pagina: self._transacciones_repo.listar(
            cuenta_id=cuenta_id, moneda_id=moneda_id, fecha_hasta=fecha_hasta,
            pagina=pagina, por_pagina=LOTE_LECTURA,
        ))

    def _saldos_iniciales(self, cuenta_id: Optional[str] = None) -> dict[tuple[int, int], int]:
        """
        (cuenta_id, moneda_id) → saldo_inicial_minor, una entrada por fila de
        cuentas_saldos (igual que vw_balance_cuentas: sin esa fila no hay
        saldo). Todas las cuentas, también las archivadas.
        """
        if cuenta_id is not None:
            ids = [cuenta_id]
        else:
            ids = [c["id"] for c in self._cuentas_repo.listar(solo_activas=False)]
        return {
            (fila["cuenta_id"], fila["moneda_id"]): fila["saldo_inicial_minor"]
            for id_ in ids
            for fila in self._cuentas_repo.listar_saldos(id_)
        }

    def _filas_deuda(self, fecha_hasta: str) -> list[dict]:
        """Todas las filas del libro de deudas (los dos tabs) con fecha <= fecha_hasta."""
        return [
            dict(d)
            for tab in TABS
            for d in self._deudas_fuente_repo.listar_por_tab(tab)
            if d["fecha"] <= fecha_hasta
        ]

    def _moneda_de_origen(self, origen_tipo: str, origen_id: str, cache: dict) -> Optional[int]:
        """moneda_id del origen de un gasto compartido (gastos_compartidos no tiene moneda propia), o None."""
        clave = (origen_tipo, origen_id)
        if clave not in cache:
            fila = None
            if origen_tipo == "transaccion":
                fila = self._transacciones_repo.obtener_por_id(origen_id, incluir_eliminadas=True)
            elif origen_tipo == "compra_cuotas":
                fila = self._compras_repo.obtener_por_id(origen_id)
            elif origen_tipo == "cuota_credito":
                cuota = self._cuotas_repo.obtener_por_id(origen_id)
                fila = self._compras_repo.obtener_por_id(cuota["compra_id"]) if cuota is not None else None
            cache[clave] = fila["moneda_id"] if fila is not None else None
        return cache[clave]

    def _gastos_pendientes(self, fecha_hasta: str, hogar_id: Optional[str] = None) -> tuple[list[dict], int]:
        """
        Gastos 'pendiente' con fecha <= fecha_hasta, de un hogar o de todos
        (hogar_id=None: QueryBuilder.where() saltea un filtro None), cada
        uno con su moneda_id de origen. Devuelve también cuántos se
        saltearon por no encontrar el origen.
        """
        gastos = self._paginado(lambda pagina: self._gastos_repo.listar(
            hogar_id=hogar_id, estado="pendiente", pagina=pagina, por_pagina=LOTE_LECTURA,
        ))
        cache: dict = {}
        con_moneda: list[dict] = []
        sin_moneda = 0
        for gasto in gastos:
            if gasto["fecha"] > fecha_hasta:
                continue
            moneda_id = self._moneda_de_origen(gasto["origen_tipo"], gasto["origen_id"], cache)
            if moneda_id is None:
                sin_moneda += 1
                continue
            gasto["moneda_id"] = moneda_id
            con_moneda.append(gasto)
        return con_moneda, sin_moneda

    # ----------------------------------------------------------
    # CÁLCULO (el mismo para snapshots y en vivo)
    # ----------------------------------------------------------

    @staticmethod
    def _signo_movimiento(tipo_movimiento: str) -> int:
        # Mismo CASE que vw_balance_cuentas: egreso resta; ingreso y 'movimiento' suman.
        return -1 if tipo_movimiento == "egreso" else 1

    def _saldos_al_cierre(
        self, meses: list[Periodo], transacciones: list[dict], iniciales: dict[tuple[int, int], int],
    ) -> dict[Periodo, dict[tuple[int, int], int]]:
        """
        mes → (cuenta_id, moneda_id) → saldo al cierre. `meses` contiguos y
        ordenados; las transacciones anteriores al primero entran en el
        saldo de arranque.
        """
        deltas: dict[tuple[int, int], dict[Periodo, int]] = defaultdict(lambda: defaultdict(int))
        for t in transacciones:
            par = (t["cuenta_id"], t["moneda_id"])
            if par in iniciales:
                deltas[par][self._periodo_de_fecha(t["fecha"])] += self._signo_movimiento(t["tipo_movimiento"]) * t["monto_minor"]

        resultado: dict[Periodo, dict[tuple[int, int], int]] = {m: {} for m in meses}
        if not meses:
            return resultado
        for par, inicial in iniciales.items():
            por_mes = deltas.get(par, {})
            acumulado = inicial + sum(valor for periodo, valor in por_mes.items() if periodo < meses[0])
            for mes in meses:
                acumulado += por_mes.get(mes, 0)
                resultado[mes][par] = acumulado
        return resultado

    def _acumulado_por_mes(
        self,
        meses: list[Periodo],
        filas: list[dict],
        clave: Callable[[dict], tuple],
        monto: Callable[[dict], int],
        fecha: Callable[[dict], str],
    ) -> dict[Periodo, dict[tuple, int]]:
        """mes → clave → suma de monto(fila) de todas las filas con fecha <= fin de ese mes."""
        resultado: dict[Periodo, dict[tuple, int]] = {m: {} for m in meses}
        ordenadas = sorted(filas, key=fecha)
        acumulado: dict[tuple, int] = defaultdict(int)
        indice = 0
        for mes in meses:
            fin = self._fin_de_mes(mes)
            while indice < len(ordenadas) and fecha(ordenadas[indice]) <= fin:
                acumulado[clave(ordenadas[indice])] += monto(ordenadas[indice])
                indice += 1
            resultado[mes] = dict(acumulado)
        return resultado

    def _clave_deuda(self, deuda: dict) -> tuple[str, str, int]:
        return (self._persona(deuda["entidad_persona"]), deuda["tab"], deuda["moneda_id"])

    def _netos_deudas(self, meses: list[Periodo], deudas: list[dict]) -> dict[Periodo, dict[tuple, int]]:
        # monto_minor ya tiene signo (positivo = entrada, negativo = pago): se suma tal cual.
        return self._acumulado_por_mes(
            meses, deudas, clave=self._clave_deuda, monto=lambda d: d["monto_minor"], fecha=lambda d: d["fecha"],
        )

    def _pendientes_compartidos(
        self, meses: list[Periodo], gastos: list[dict], clave: Callable[[dict], tuple],
    ) -> dict[Periodo, dict[tuple, int]]:
        return self._acumulado_por_mes(
            meses, gastos, clave=clave, monto=lambda g: g["monto_pendiente_minor"], fecha=lambda g: g["fecha"],
        )

    # ----------------------------------------------------------
    # RECALCULAR
    # ----------------------------------------------------------

    def recalcular_todo(self) -> SnapshotResult:
        """
        Recalcula todos los snapshots, desde el primer mes con datos hasta el
        mes anterior al actual. Borra antes TODOS los existentes (así no
        quedan meses viejos de datos que ya no están). El mes actual nunca
        se guarda. Una sola transacción: si algo falla, no cambia nada.
        """
        return self._recalcular(desde=None)

    def recalcular_desde(self, mes: int, anio: int) -> SnapshotResult:
        """
        Recalcula desde mes/anio hasta el mes anterior al actual — ej. tras
        editar una transacción de un mes ya cerrado. Borra antes los
        snapshots existentes desde ese mes. Los meses anteriores quedan
        como están.

        Raises:
            SnapshotsError si mes/anio no son válidos.
        """
        return self._recalcular(desde=self._periodo(mes, anio))

    def _recalcular(self, desde: Optional[Periodo]) -> SnapshotResult:
        inicio = time.perf_counter()
        ultimo = self._ultimo_mes_cerrado()
        fin = self._fin_de_mes(ultimo)

        iniciales = self._saldos_iniciales()
        transacciones = self._transacciones(fecha_hasta=fin)
        deudas = self._filas_deuda(fecha_hasta=fin)
        gastos, sin_moneda = self._gastos_pendientes(fecha_hasta=fin)

        fechas = (
            [t["fecha"] for t in transacciones] + [d["fecha"] for d in deudas] + [g["fecha"] for g in gastos]
        )
        primero = self._periodo_de_fecha(min(fechas)) if fechas else None
        # recalcular_desde(): ese mes, pero nunca antes del primero con datos.
        comienzo = primero if (desde is None or primero is None) else max(primero, desde)
        meses = self._meses(comienzo, ultimo) if comienzo is not None else []

        saldos = self._saldos_al_cierre(meses, transacciones, iniciales)
        netos = self._netos_deudas(meses, deudas)
        compartidos = self._pendientes_compartidos(
            meses, gastos, clave=lambda g: (g["hogar_id"], g["pagador"], g["moneda_id"]),
        )

        anio_borrar, mes_borrar = desde or PRIMER_PERIODO_POSIBLE
        with self._db.transaction() as conn:
            self._saldos_repo.eliminar_desde(mes_borrar, anio_borrar, conn=conn)
            self._deudas_repo.eliminar_desde(mes_borrar, anio_borrar, conn=conn)
            self._compartidos_repo.eliminar_desde(None, mes_borrar, anio_borrar, conn=conn)
            for periodo in meses:
                anio, mes = periodo
                for (cuenta_id, moneda_id), saldo in saldos[periodo].items():
                    self._saldos_repo.upsert(cuenta_id, moneda_id, mes, anio, saldo, conn=conn)
                for (persona, tab, moneda_id), monto in netos[periodo].items():
                    self._deudas_repo.upsert(persona, tab, moneda_id, mes, anio, monto, conn=conn)
                for (hogar_id, pagador, moneda_id), monto in compartidos[periodo].items():
                    self._compartidos_repo.upsert(hogar_id, pagador, moneda_id, mes, anio, monto, conn=conn)

        tiempo = time.perf_counter() - inicio
        if meses:
            (anio_ini, mes_ini), (anio_fin, mes_fin) = meses[0], meses[-1]
            mensaje = (
                f"{len(meses)} mes(es) recalculado(s) ({anio_ini:04d}-{mes_ini:02d} a "
                f"{anio_fin:04d}-{mes_fin:02d}) en {tiempo:.2f} s."
            )
        else:
            mensaje = "No hay meses cerrados con datos para calcular."
        if sin_moneda:
            mensaje += f" {sin_moneda} gasto(s) compartido(s) sin origen encontrado (sin moneda): salteados."
        return SnapshotResult(success=True, meses_calculados=len(meses), tiempo_segundos=tiempo, message=mensaje)

    # ----------------------------------------------------------
    # SALDO ANTERIOR — POR CLAVE
    # ----------------------------------------------------------

    def get_saldo_anterior_cuenta(self, cuenta_id: str, moneda_id: int, mes: int, anio: int) -> int:
        """
        Saldo de (cuenta, moneda) al cierre del mes anterior a mes/anio (con
        mes=1, diciembre del año anterior). Del snapshot si existe; si no,
        en vivo con la misma regla (en el primer mes de la app eso es
        saldo_inicial_minor). Pedido para el mes actual, devuelve el cierre
        del mes anterior: el mes actual no tiene snapshot.

        Raises:
            SnapshotsError si mes/anio no son válidos.
        """
        anterior = self._anterior(self._periodo(mes, anio))
        fila = self._saldos_repo.obtener(cuenta_id, moneda_id, anterior[1], anterior[0])
        if fila is not None:
            return fila["saldo_minor"]
        iniciales = {
            par: saldo for par, saldo in self._saldos_iniciales(cuenta_id).items() if par == (cuenta_id, moneda_id)
        }
        transacciones = self._transacciones(self._fin_de_mes(anterior), cuenta_id=cuenta_id, moneda_id=moneda_id)
        return self._saldos_al_cierre([anterior], transacciones, iniciales)[anterior].get((cuenta_id, moneda_id), 0)

    def get_saldo_anterior_deuda(self, entidad_persona: str, moneda_id: int, tab: str, mes: int, anio: int) -> int:
        """
        Saldo con esa persona en ese tab y esa moneda al cierre del mes
        anterior a mes/anio: SUM(monto_minor) — en 'me_deben', positivo = te
        debe; en 'debo', positivo = le debés. El nombre se normaliza igual
        que al guardar ("Noe" = "NOE"). Snapshot, o en vivo si falta.

        Raises:
            SnapshotsError si mes/anio o el tab no son válidos.
        """
        anterior = self._anterior(self._periodo(mes, anio))
        tab = self._tab(tab)
        persona = self._persona(entidad_persona)
        fila = self._deudas_repo.obtener(persona, tab, moneda_id, anterior[1], anterior[0])
        if fila is not None:
            return fila["monto_minor"]
        return self._netos_deudas_en_vivo(anterior, tab).get((persona, moneda_id), 0)

    def get_saldo_anterior_compartidos(
        self, hogar_id: str, pagador: str, moneda_id: int, mes: int, anio: int,
    ) -> int:
        """
        Pendiente acumulado de los gastos compartidos de ese hogar pagados por
        `pagador`, en esa moneda, al cierre del mes anterior a mes/anio.
        Snapshot, o en vivo si falta.

        Raises:
            SnapshotsError si mes/anio no son válidos.
        """
        anterior = self._anterior(self._periodo(mes, anio))
        fila = self._compartidos_repo.obtener(hogar_id, pagador, moneda_id, anterior[1], anterior[0])
        if fila is not None:
            return fila["monto_minor"]
        return self._compartidos_en_vivo(hogar_id, anterior).get((pagador, moneda_id), 0)

    # ----------------------------------------------------------
    # SALDO ANTERIOR — DE A MUCHOS (fila "SALDO ANTERIOR" de las pantallas)
    # ----------------------------------------------------------

    def get_saldos_anteriores_cuentas(self, mes: int, anio: int) -> list[dict]:
        """
        Una entrada por (cuenta, moneda) de cuentas_saldos con su saldo al
        cierre del mes anterior: {cuenta_id, moneda_id, saldo_minor,
        moneda_codigo, moneda_simbolo, decimales}. Del snapshot; los pares
        que falten (ej. una cuenta nueva, o un mes todavía sin recalcular)
        se calculan en vivo, todos en una sola pasada.

        Raises:
            SnapshotsError si mes/anio no son válidos.
        """
        anterior = self._anterior(self._periodo(mes, anio))
        guardados = {
            (fila["cuenta_id"], fila["moneda_id"]): fila["saldo_minor"]
            for fila in self._saldos_repo.listar_por_periodo(anterior[1], anterior[0])
        }
        iniciales = self._saldos_iniciales()
        if any(par not in guardados for par in iniciales):
            en_vivo = self._saldos_al_cierre(
                [anterior], self._transacciones(self._fin_de_mes(anterior)), iniciales,
            )[anterior]
            guardados = {**en_vivo, **guardados}
        monedas = self._monedas()
        return [
            self._con_moneda(
                {"cuenta_id": cuenta_id, "moneda_id": moneda_id, "saldo_minor": guardados.get((cuenta_id, moneda_id), 0)},
                monedas,
            )
            for cuenta_id, moneda_id in iniciales
        ]

    def get_saldos_anteriores_deudas(self, mes: int, anio: int, tab: str) -> list[dict]:
        """
        Una entrada por (persona, moneda) de ese tab con su saldo al cierre
        del mes anterior: {entidad_persona (normalizada), tab, moneda_id,
        monto_minor, moneda_codigo, moneda_simbolo, decimales}. Del snapshot
        de ese mes; si no hay ninguno para el tab, en vivo.

        Raises:
            SnapshotsError si mes/anio o el tab no son válidos.
        """
        anterior = self._anterior(self._periodo(mes, anio))
        tab = self._tab(tab)
        filas = self._deudas_repo.listar_por_periodo(anterior[1], anterior[0], tab)
        if filas:
            netos = {(fila["entidad_persona"], fila["moneda_id"]): fila["monto_minor"] for fila in filas}
        else:
            netos = self._netos_deudas_en_vivo(anterior, tab)
        monedas = self._monedas()
        return [
            self._con_moneda(
                {"entidad_persona": persona, "tab": tab, "moneda_id": moneda_id, "monto_minor": monto}, monedas,
            )
            for (persona, moneda_id), monto in sorted(netos.items())
        ]

    def get_saldos_anteriores_compartidos(self, hogar_id: str, mes: int, anio: int) -> list[dict]:
        """
        Una entrada por (pagador, moneda) de ese hogar con el pendiente al
        cierre del mes anterior: {pagador, moneda_id, monto_minor,
        moneda_codigo, moneda_simbolo, decimales}. Del snapshot de ese mes;
        si no hay ninguno para el hogar, en vivo.

        Raises:
            SnapshotsError si mes/anio no son válidos.
        """
        anterior = self._anterior(self._periodo(mes, anio))
        filas = self._compartidos_repo.listar_por_hogar_periodo(hogar_id, anterior[1], anterior[0])
        if filas:
            montos = {(fila["pagador"], fila["moneda_id"]): fila["monto_minor"] for fila in filas}
        else:
            montos = self._compartidos_en_vivo(hogar_id, anterior)
        monedas = self._monedas()
        return [
            self._con_moneda({"pagador": pagador, "moneda_id": moneda_id, "monto_minor": monto}, monedas)
            for (pagador, moneda_id), monto in sorted(montos.items())
        ]

    # ----------------------------------------------------------
    # EN VIVO (cuando falta el snapshot) + formato
    # ----------------------------------------------------------

    def _netos_deudas_en_vivo(self, periodo: Periodo, tab: str) -> dict[tuple[str, int], int]:
        """(persona, moneda_id) → SUM(monto_minor) de ese tab al cierre de `periodo` (la suma la hace el repositorio)."""
        netos: dict[tuple[str, int], int] = defaultdict(int)
        for fila in self._deudas_fuente_repo.get_saldo_neto_por_persona(tab, self._fin_de_mes(periodo)):
            # Los nombres ya se guardan normalizados (DebtsService); se normaliza igual por si una fila vieja no.
            netos[(self._persona(fila["entidad_persona"]), fila["moneda_id"])] += fila["saldo"]
        return dict(netos)

    def _compartidos_en_vivo(self, hogar_id: str, periodo: Periodo) -> dict[tuple, int]:
        gastos, _ = self._gastos_pendientes(fecha_hasta=self._fin_de_mes(periodo), hogar_id=hogar_id)
        return self._pendientes_compartidos(
            [periodo], gastos, clave=lambda g: (g["pagador"], g["moneda_id"]),
        )[periodo]

    @staticmethod
    def _con_moneda(entrada: dict[str, Any], monedas: dict[int, Any]) -> dict[str, Any]:
        """Agrega codigo/simbolo/decimales de la moneda (para que la UI muestre el monto sin otra consulta)."""
        moneda = monedas.get(entrada["moneda_id"])
        entrada["moneda_codigo"] = moneda["codigo"] if moneda else ""
        entrada["moneda_simbolo"] = (moneda["simbolo"] or "") if moneda else ""
        entrada["decimales"] = moneda["decimales"] if moneda else 2
        return entrada

    def _monedas(self) -> dict[int, Any]:
        return {fila["id"]: fila for fila in self._db.obtener_monedas()}
