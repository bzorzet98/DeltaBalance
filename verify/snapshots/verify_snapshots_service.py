"""
verify/snapshots/verify_snapshots_service.py

Verifica SnapshotsService (services/snapshots_service.py) y los tres
repositorios de snapshots de cierre de mes (saldos_mensuales,
deudas_mensuales, compartidos_mensuales), contra una DB temporal. Las
fechas del escenario son relativas a HOY (mes actual y los tres
anteriores), así el script sirve cualquier día que se corra.

Cubre:
  - inicializar() crea las tres tablas (db/schema_migrations.py
    MIGRACIONES_TABLA) y es idempotente (correrlo dos veces no rompe).
  - recalcular_todo(): cantidad de meses (del primero con datos al mes
    anterior al actual), saldo por mes acumulado (saldo inicial + ingresos
    − egresos; una transacción eliminada no cuenta), y que el mes actual
    NUNCA se guarda.
  - get_saldo_anterior_cuenta(): cierre del mes anterior; en el primer mes
    con datos devuelve saldo_inicial_minor; snapshot del mes anterior +
    movimientos del mes actual = saldo actual de la app (vw_balance_cuentas).
  - recalcular_desde() tras editar una transacción de un mes cerrado: cambia
    ese mes y los siguientes, no los anteriores.
  - Sin snapshot (borrados a mano): las lecturas calculan en vivo y dan lo
    mismo.
  - recalcular_todo() dos veces: mismas filas (upsert, sin duplicados).
  - deudas_mensuales: neto con signo por persona (a_favor +, en_contra −),
    "Noe" y "noe " son la misma persona, las deudas del mes actual no
    entran, y un pago de este mes (fila de tipo opuesto) no cambia el
    cierre del mes anterior (libro de movimientos: los meses cerrados
    quedan fijos). Sin snapshot, el cálculo en vivo da lo mismo.
  - compartidos_mensuales: pendiente por (hogar, pagador, moneda), con la
    moneda sacada de la transacción de origen.
  - mes fuera de 1-12: SnapshotsError.

Correlo con:
    python verify/snapshots/verify_snapshots_service.py
"""

from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.saldos_mensuales_repository import SaldosMensualesRepository
from services.accounts_service import AccountsService
from services.debts_service import DebtsService
from services.shared_expenses_service import SharedExpensesService
from services.snapshots_service import SnapshotsError, SnapshotsService
from services.transaction_service import TransactionService


def _meses_atras(n: int) -> tuple[int, int]:
    """(anio, mes) de hace n meses (0 = el actual)."""
    hoy = date.today()
    indice = hoy.year * 12 + (hoy.month - 1) - n
    return indice // 12, indice % 12 + 1


def _fecha(periodo: tuple[int, int]) -> str:
    # Día 1: nunca queda en el futuro, ni siquiera en el mes actual.
    return f"{periodo[0]:04d}-{periodo[1]:02d}-01"


def main() -> None:
    casos_ok = 0
    casos_total = 0

    def caso(descripcion: str, esperado, obtenido) -> None:
        nonlocal casos_ok, casos_total
        casos_total += 1
        if esperado == obtenido:
            casos_ok += 1
            print(f"✅ {descripcion} — esperado: {esperado!r}, obtenido: {obtenido!r}")
        else:
            print(f"❌ {descripcion} — esperado: {esperado!r}, obtenido: {obtenido!r}")

    def caso_excepcion(descripcion: str, tipo_esperado, callable_) -> None:
        nonlocal casos_ok, casos_total
        casos_total += 1
        try:
            callable_()
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, no se lanzó ninguna excepción")
        except tipo_esperado:
            casos_ok += 1
            print(f"✅ {descripcion} — lanzó {tipo_esperado.__name__} como se esperaba")
        except Exception as e:
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, se lanzó {type(e).__name__}: {e!r}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()

    # ============================================================
    # Tablas nuevas (migraciones de tabla)
    # ============================================================
    print("--- inicializar(): tablas de snapshots ---")
    tablas = {f["name"] for f in manager.fetchall("SELECT name FROM sqlite_master WHERE type = 'table';")}
    for tabla in ("saldos_mensuales", "deudas_mensuales", "compartidos_mensuales"):
        caso(f"existe la tabla {tabla}", True, tabla in tablas)
    manager.inicializar()
    caso("inicializar() dos veces no rompe (CREATE TABLE IF NOT EXISTS)", True, True)

    cuentas = AccountsService(manager)
    transacciones = TransactionService(manager)
    deudas = DebtsService(manager)
    compartidos = SharedExpensesService(manager)
    snapshots = SnapshotsService(manager)
    repo_saldos = SaldosMensualesRepository(manager)

    ars = next(m for m in cuentas.list_currencies() if m["codigo"] == "ARS")
    ars_id = ars["id"]
    cuenta_id = cuentas.create_account(nombre="Banco Snapshot", tipo="debito", monedas=[ars_id]).account_id
    # AccountsService siempre crea el saldo inicial en 0: se fija a mano para el escenario.
    manager.execute(
        "UPDATE cuentas_saldos SET saldo_inicial_minor = ? WHERE cuenta_id = ? AND moneda_id = ?;",
        (10000, cuenta_id, ars_id),
    )
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' ORDER BY id LIMIT 1;")["id"]
    cat_ingreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'ingreso' ORDER BY id LIMIT 1;")["id"]

    m0, m1, m2, m3 = _meses_atras(0), _meses_atras(1), _meses_atras(2), _meses_atras(3)

    def _alta(periodo, monto: float, tipo: str, concepto: str) -> int:
        categoria = cat_ingreso if tipo == "ingreso" else cat_egreso
        return transacciones.create(
            date_str=_fecha(periodo), concept=concepto, account_id=cuenta_id, category_id=categoria,
            currency_code="ARS", amount=monto, movement_type=tipo,
        ).transaction_id

    _alta(m3, 1000.00, "ingreso", "Sueldo viejo")
    t_m2 = _alta(m2, 250.00, "egreso", "Super")
    borrada = _alta(m2, 999.00, "egreso", "Cargada por error")
    transacciones.delete(borrada)
    _alta(m1, 100.00, "egreso", "Luz")
    _alta(m0, 50.00, "ingreso", "Reintegro del mes actual")

    # ============================================================
    # recalcular_todo() — saldos
    # ============================================================
    print("\n--- recalcular_todo(): saldos_mensuales ---")
    resultado = snapshots.recalcular_todo()
    print(f"   {resultado.message}")
    caso("success=True", True, resultado.success)
    caso("meses_calculados: del primer mes con datos (hace 3) al anterior al actual", 3, resultado.meses_calculados)

    def _snapshot(periodo) -> int | None:
        fila = repo_saldos.obtener(cuenta_id, ars_id, periodo[1], periodo[0])
        return fila["saldo_minor"] if fila else None

    caso("cierre hace 3 meses: 100.00 inicial + 1000.00", 110000, _snapshot(m3))
    caso("cierre hace 2 meses: − 250.00 (la eliminada no cuenta)", 85000, _snapshot(m2))
    caso("cierre del mes anterior: − 100.00", 75000, _snapshot(m1))
    caso("el mes actual NO tiene snapshot", None, _snapshot(m0))

    print("\n--- get_saldo_anterior_cuenta() ---")
    caso("mes actual → cierre del mes anterior", 75000, snapshots.get_saldo_anterior_cuenta(cuenta_id, ars_id, m0[1], m0[0]))
    caso("hace 1 mes → cierre de hace 2", 85000, snapshots.get_saldo_anterior_cuenta(cuenta_id, ars_id, m1[1], m1[0]))
    caso("primer mes con datos → saldo_inicial_minor", 10000, snapshots.get_saldo_anterior_cuenta(cuenta_id, ars_id, m3[1], m3[0]))
    saldo_app = next(s for s in cuentas.get_account(cuenta_id)["saldos"] if s["moneda_id"] == ars_id)["saldo_minor"]
    caso("snapshot del mes anterior + movimientos del mes actual (+50.00) = saldo de la app", saldo_app, 75000 + 5000)

    # ============================================================
    # recalcular_desde() tras editar un mes cerrado
    # ============================================================
    print("\n--- recalcular_desde() tras editar una transacción de hace 2 meses ---")
    transacciones.update(t_m2, amount=300.00, currency_code="ARS")
    caso("antes de recalcular, el snapshot sigue viejo (es un caché)", 85000, _snapshot(m2))
    snapshots.recalcular_desde(m2[1], m2[0])
    caso("hace 3 meses no cambia", 110000, _snapshot(m3))
    caso("hace 2 meses: 110000 − 30000", 80000, _snapshot(m2))
    caso("mes anterior: 80000 − 10000", 70000, _snapshot(m1))

    # ============================================================
    # Sin snapshot: cálculo en vivo
    # ============================================================
    print("\n--- lecturas sin snapshot (borrados a mano) ---")
    repo_saldos.eliminar_desde(m3[1], m3[0])
    caso("se borraron los snapshots", None, _snapshot(m1))
    caso("get_saldo_anterior_cuenta() en vivo da lo mismo", 70000, snapshots.get_saldo_anterior_cuenta(cuenta_id, ars_id, m0[1], m0[0]))
    en_lote = {
        (e["cuenta_id"], e["moneda_id"]): e["saldo_minor"]
        for e in snapshots.get_saldos_anteriores_cuentas(m0[1], m0[0])
    }
    caso("get_saldos_anteriores_cuentas() en vivo da lo mismo", 70000, en_lote.get((cuenta_id, ars_id)))
    caso("… y trae el código de moneda", "ARS", next(
        e["moneda_codigo"] for e in snapshots.get_saldos_anteriores_cuentas(m0[1], m0[0]) if e["cuenta_id"] == cuenta_id
    ))

    snapshots.recalcular_todo()
    filas_1 = manager.fetchone("SELECT COUNT(*) AS n FROM saldos_mensuales;")["n"]
    snapshots.recalcular_todo()
    filas_2 = manager.fetchone("SELECT COUNT(*) AS n FROM saldos_mensuales;")["n"]
    caso("recalcular_todo() dos veces: mismas filas (upsert, sin duplicados)", filas_1, filas_2)

    # ============================================================
    # deudas_mensuales
    # ============================================================
    print("\n--- deudas_mensuales (libro de movimientos) ---")
    deudas.create("Noe", "Préstamo", "a_favor", 40000, ars_id, _fecha(m2))
    deudas.create("noe ", "Me pagó de más", "en_contra", 10000, ars_id, _fecha(m1))
    deudas.create("Kevin", "Cena", "en_contra", 5000, ars_id, _fecha(m3))
    deudas.create("Pedro", "Del mes actual", "a_favor", 7000, ars_id, _fecha(m0))
    snapshots.recalcular_todo()

    caso("NOE al cierre del mes anterior: 400.00 − 100.00 (misma persona)", 30000,
         snapshots.get_saldo_anterior_deuda("Noe", ars_id, m0[1], m0[0]))
    caso("KEVIN: le debés 50.00 → negativo", -5000,
         snapshots.get_saldo_anterior_deuda("KEVIN", ars_id, m0[1], m0[0]))
    caso("NOE hace 2 meses: todavía no existía la de 100.00", 40000,
         snapshots.get_saldo_anterior_deuda("NOE", ars_id, m1[1], m1[0]))
    personas_mes_anterior = {e["entidad_persona"] for e in snapshots.get_saldos_anteriores_deudas(m0[1], m0[0])}
    caso("PEDRO (deuda del mes actual) no está en el saldo anterior", False, "PEDRO" in personas_mes_anterior)

    # Un pago es una fila de tipo opuesto con su propia fecha: uno de este
    # mes no cambia el cierre del mes anterior (el mes cerrado queda fijo).
    deudas.create("Kevin", "Le pagué", "a_favor", 5000, ars_id, _fecha(m0))
    snapshots.recalcular_todo()
    caso("pago a Kevin de este mes: el cierre del mes anterior sigue en −50.00", -5000,
         snapshots.get_saldo_anterior_deuda("Kevin", ars_id, m0[1], m0[0]))
    caso("… y el saldo al día queda en 0 (DebtsService.get_saldo_neto())", 0, next(
        e["saldo_minor"] for e in deudas.get_saldo_neto() if e["entidad_persona"] == "KEVIN"
    ))
    manager.execute("DELETE FROM deudas_mensuales;")
    caso("sin snapshot: get_saldo_anterior_deuda() en vivo da lo mismo", 30000,
         snapshots.get_saldo_anterior_deuda("NOE", ars_id, m0[1], m0[0]))


    # ============================================================
    # compartidos_mensuales
    # ============================================================
    print("\n--- compartidos_mensuales ---")
    hogar_id = compartidos.create_hogar(nombre_creador_local="bruno", nombre_hogar="Casa").entity_id
    compartidos.add_shared_expense(
        hogar_id=hogar_id, pagador="bruno", origen_tipo="transaccion", origen_id=t_m2,
        categoria_id=cat_egreso, monto_base_minor=30000, coeficiente_deuda=50.0, fecha=_fecha(m2),
    )
    snapshots.recalcular_todo()
    caso("pendiente de bruno (ARS, de la transacción de origen) al cierre del mes anterior", 15000,
         snapshots.get_saldo_anterior_compartidos(hogar_id, "bruno", ars_id, m0[1], m0[0]))
    caso("hace 3 meses todavía no existía el gasto", 0,
         snapshots.get_saldo_anterior_compartidos(hogar_id, "bruno", ars_id, m2[1], m2[0]))
    en_lote_hogar = snapshots.get_saldos_anteriores_compartidos(hogar_id, m0[1], m0[0])
    caso("get_saldos_anteriores_compartidos(): una fila (bruno, ARS)", [("bruno", "ARS", 15000)],
         [(e["pagador"], e["moneda_codigo"], e["monto_minor"]) for e in en_lote_hogar])

    # ============================================================
    # Validación
    # ============================================================
    print("\n--- validación ---")
    caso_excepcion("mes=13 → SnapshotsError", SnapshotsError,
                   lambda: snapshots.get_saldo_anterior_cuenta(cuenta_id, ars_id, 13, m0[0]))
    caso_excepcion("recalcular_desde(mes=0) → SnapshotsError", SnapshotsError,
                   lambda: snapshots.recalcular_desde(0, m0[0]))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
