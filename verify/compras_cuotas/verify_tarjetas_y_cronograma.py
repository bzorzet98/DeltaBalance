"""
verify/compras_cuotas/verify_tarjetas_y_cronograma.py

Verifica lo agregado a FeesService en la tarea "Mejoras en Compras y
Movimientos en Cuotas" (docs/DATA_MODEL_DECISIONS.md sección 23), contra
una DB temporal:

  - tarjetas_config: inicializar() crea la tabla; set_card_config() crea y
    actualiza (una fila por tarjeta), get_card_config(); rechaza una cuenta
    que no es tarjeta y días fuera de 1-31 o no enteros.
  - card_cycle_dates(): ANTERIOR / ACTUAL / PRÓXIMO (ACTUAL = el último que
    cerró); antes del día de cierre, ACTUAL es el del mes pasado; un 31 en
    febrero cae el 28; vencimiento < o = cierre → el mes siguiente,
    vencimiento > cierre → el mismo mes; sin configuración → None.
  - suggest_first_fee(): el mes siguiente; dos meses después si la compra
    es posterior al día de cierre (el mismo día de cierre, no); cruce de
    año; sin tarjeta o sin configuración → el mes siguiente.
  - create_purchase() con 1ª cuota: el cronograma arranca ahí; sin
    pasarla, en el mes de compra (como siempre); solo uno de los dos
    parámetros, un mes inválido o una 1ª cuota anterior al mes de compra
    → FeesError.
  - reschedule_fees(): mueve una cuota pendiente; dos en el mismo mes →
    FeesError y nada cambia; una cuota que no está pendiente → FeesError;
    una cuota de otra compra → FeeNotFoundError; sin cambios →
    success=False; una compra compartida por cuota → FeesError.
  - update_purchase_cuotas() arranca el cronograma nuevo desde el mes que
    tiene hoy la 1ª cuota; update_purchase(fecha a otro mes) corre cada
    cuota los mismos meses (se conservan la 1ª cuota elegida y los
    movimientos hechos a mano).

Correlo con:
    python verify/compras_cuotas/verify_tarjetas_y_cronograma.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.fees_service import FeeNotFoundError, FeesError, FeesService
from services.shared_expenses_service import SharedExpensesService


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
        except tipo_esperado as e:
            casos_ok += 1
            print(f"✅ {descripcion} — lanzó {tipo_esperado.__name__} como se esperaba ({e})")
        except Exception as e:
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, se lanzó {type(e).__name__}: {e!r}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")
    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    cuentas_repo = CuentasRepository(manager)
    svc = FeesService(manager)
    shared_svc = SharedExpensesService(manager)

    tarjeta = cuentas_repo.crear(nombre="Nacion Mastercard", tipo="credito", moneda_codigo="ARS")
    tarjeta_sin_config = cuentas_repo.crear(nombre="Visa Sin Config", tipo="credito", moneda_codigo="ARS")
    debito = cuentas_repo.crear(nombre="Caja de ahorro", tipo="debito", moneda_codigo="ARS")
    categoria = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso' ORDER BY id LIMIT 1;")[0]["id"]

    def nueva_compra(concepto: str, fecha: str = "2026-03-05", cuotas: int = 3, **kwargs) -> int:
        # Conceptos distintos: CompraDuplicadaError bloquea compras idénticas seguidas.
        return svc.create_purchase(
            date_str=fecha, concept=concepto, account_id=tarjeta, category_id=categoria,
            currency_code="ARS", total_amount=3000.0, total_fees=cuotas, **kwargs,
        ).entity_id

    def meses(compra_id: int) -> list[tuple[int, int]]:
        return [(c["mes_proyectado"], c["anio_proyectado"]) for c in svc.get_fees_for_purchase(compra_id)]

    def ids_cuotas(compra_id: int) -> list[int]:
        return [c["id"] for c in svc.get_fees_for_purchase(compra_id)]

    # ============================================================
    print("--- tarjetas_config: set_card_config() / get_card_config() ---")
    # ============================================================
    tablas = {f["name"] for f in manager.fetchall("SELECT name FROM sqlite_master WHERE type = 'table';")}
    caso("inicializar() crea tarjetas_config", True, "tarjetas_config" in tablas)
    caso("una tarjeta sin configurar → None", None, svc.get_card_config(tarjeta))
    caso("set_card_config(15, 5) → success", True, svc.set_card_config(tarjeta, 15, 5).success)
    config = svc.get_card_config(tarjeta)
    caso("get_card_config() devuelve los días", (15, 5), (config["dia_cierre"], config["dia_vencimiento"]))
    svc.set_card_config(tarjeta, 20, 10)
    config = svc.get_card_config(tarjeta)
    caso("volver a guardar actualiza (upsert)", (20, 10), (config["dia_cierre"], config["dia_vencimiento"]))
    caso("… sin duplicar la fila", 1,
         manager.fetchone("SELECT COUNT(*) AS n FROM tarjetas_config WHERE cuenta_id = ?;", (tarjeta,))["n"])
    caso_excepcion("una cuenta que no es tarjeta → FeesError", FeesError, lambda: svc.set_card_config(debito, 15, 5))
    caso_excepcion("día de cierre 0 → FeesError", FeesError, lambda: svc.set_card_config(tarjeta, 0, 5))
    caso_excepcion("día de vencimiento 32 → FeesError", FeesError, lambda: svc.set_card_config(tarjeta, 15, 32))
    caso_excepcion("día no entero → FeesError", FeesError, lambda: svc.set_card_config(tarjeta, "15", 5))
    svc.set_card_config(tarjeta, 15, 5)

    # ============================================================
    print("\n--- card_cycle_dates() ---")
    # ============================================================
    ciclo = svc.card_cycle_dates(tarjeta, "2026-09-30")
    caso("ANTERIOR: cerró 15/08, vence 05/09",
         {"cierre": "2026-08-15", "vencimiento": "2026-09-05"}, ciclo["anterior"])
    caso("ACTUAL (el último que cerró): cerró 15/09, vence 05/10",
         {"cierre": "2026-09-15", "vencimiento": "2026-10-05"}, ciclo["actual"])
    caso("PRÓXIMO: cierra 15/10, vence 05/11",
         {"cierre": "2026-10-15", "vencimiento": "2026-11-05"}, ciclo["proximo"])
    caso("el día de cierre ya cuenta como cerrado (hoy = 15/09)", "2026-09-15",
         svc.card_cycle_dates(tarjeta, "2026-09-15")["actual"]["cierre"])
    caso("antes del cierre (10/09): ACTUAL es el de agosto", "2026-08-15",
         svc.card_cycle_dates(tarjeta, "2026-09-10")["actual"]["cierre"])
    caso("cruce de año (10/01/2027): ANTERIOR es el de noviembre", "2026-11-15",
         svc.card_cycle_dates(tarjeta, "2027-01-10")["anterior"]["cierre"])
    svc.set_card_config(tarjeta, 31, 10)
    caso("cierre 31 en febrero → el 28", "2026-02-28", svc.card_cycle_dates(tarjeta, "2026-03-10")["actual"]["cierre"])
    svc.set_card_config(tarjeta, 5, 20)
    caso("vencimiento > cierre → el mismo mes", {"cierre": "2026-09-05", "vencimiento": "2026-09-20"},
         svc.card_cycle_dates(tarjeta, "2026-09-10")["actual"])
    svc.set_card_config(tarjeta, 15, 15)
    caso("vencimiento = cierre → el mes siguiente", "2026-10-15",
         svc.card_cycle_dates(tarjeta, "2026-09-20")["actual"]["vencimiento"])
    caso("una tarjeta sin configuración → None", None, svc.card_cycle_dates(tarjeta_sin_config))
    svc.set_card_config(tarjeta, 15, 5)

    # ============================================================
    print("\n--- suggest_first_fee() ---")
    # ============================================================
    caso("antes del cierre (10/09) → el mes siguiente", (10, 2026), svc.suggest_first_fee(tarjeta, "2026-09-10"))
    caso("el mismo día de cierre (15/09) → el mes siguiente", (10, 2026), svc.suggest_first_fee(tarjeta, "2026-09-15"))
    caso("después del cierre (20/09) → dos meses después", (11, 2026), svc.suggest_first_fee(tarjeta, "2026-09-20"))
    caso("cruce de año después del cierre (20/12) → 02/2027", (2, 2027), svc.suggest_first_fee(tarjeta, "2026-12-20"))
    caso("tarjeta sin configuración → el mes siguiente", (10, 2026),
         svc.suggest_first_fee(tarjeta_sin_config, "2026-09-20"))
    caso("sin tarjeta elegida → el mes siguiente", (1, 2027), svc.suggest_first_fee(None, "2026-12-20"))
    caso_excepcion("fecha inválida → ValueError", ValueError, lambda: svc.suggest_first_fee(tarjeta, "20/09/2026"))

    # ============================================================
    print("\n--- create_purchase() con 1ª cuota ---")
    # ============================================================
    con_primera = nueva_compra("Lentes optica", first_fee_month=4, first_fee_year=2026)
    caso("el cronograma arranca en la 1ª cuota pedida", [(4, 2026), (5, 2026), (6, 2026)], meses(con_primera))
    sin_primera = nueva_compra("Sin primera")
    caso("sin 1ª cuota: arranca en el mes de compra (como siempre)", [(3, 2026), (4, 2026), (5, 2026)],
         meses(sin_primera))
    caso("cruce de año", [(12, 2026), (1, 2027)],
         meses(nueva_compra("Cruce", fecha="2026-11-20", cuotas=2, first_fee_month=12, first_fee_year=2026)))
    caso_excepcion("solo first_fee_month → FeesError", FeesError, lambda: nueva_compra("X1", first_fee_month=4))
    caso_excepcion("mes 13 → FeesError", FeesError, lambda: nueva_compra("X2", first_fee_month=13, first_fee_year=2026))
    caso_excepcion("1ª cuota anterior al mes de compra → FeesError", FeesError,
                   lambda: nueva_compra("X3", first_fee_month=2, first_fee_year=2026))

    # ============================================================
    print("\n--- reschedule_fees() ---")
    # ============================================================
    cuota_1, cuota_2, cuota_3 = ids_cuotas(con_primera)
    res = svc.reschedule_fees(con_primera, {cuota_3: (8, 2026)})
    caso("mover la cuota 3 a 08/2026 → success", (True, 1), (res.success, res.data["fees_moved"]))
    caso("solo cambia esa cuota", [(4, 2026), (5, 2026), (8, 2026)], meses(con_primera))
    caso_excepcion("la cuota 2 al mes de la 1 → FeesError", FeesError,
                   lambda: svc.reschedule_fees(con_primera, {cuota_2: (4, 2026)}))
    caso("… y nada cambió", [(4, 2026), (5, 2026), (8, 2026)], meses(con_primera))
    caso("intercambiar dos meses en la misma llamada sí se puede", True,
         svc.reschedule_fees(con_primera, {cuota_1: (5, 2026), cuota_2: (4, 2026)}).success)
    caso("… quedan intercambiadas", [(5, 2026), (4, 2026), (8, 2026)], meses(con_primera))
    caso("pasarle el mismo mes que ya tiene → success=False", False,
         svc.reschedule_fees(con_primera, {cuota_3: (8, 2026)}).success)
    caso_excepcion("mes 0 → FeesError", FeesError, lambda: svc.reschedule_fees(con_primera, {cuota_3: (0, 2026)}))
    caso_excepcion("una cuota de otra compra → FeeNotFoundError", FeeNotFoundError,
                   lambda: svc.reschedule_fees(con_primera, {ids_cuotas(sin_primera)[0]: (9, 2026)}))
    manager.execute("UPDATE cuotas_credito SET estado = 'pagado' WHERE id = ?;", (cuota_1,))
    caso_excepcion("mover una cuota pagada → FeesError", FeesError,
                   lambda: svc.reschedule_fees(con_primera, {cuota_1: (9, 2026)}))
    caso("las demás pendientes se siguen moviendo", True,
         svc.reschedule_fees(con_primera, {cuota_3: (9, 2026)}).success)

    hogar = shared_svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Cronograma").entity_id
    compartida = nueva_compra("Compartida por cuota")
    shared_svc.add_shared_purchase(compra_id=compartida, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0)
    caso_excepcion("mover una cuota compartida (un gasto por cuota) → FeesError", FeesError,
                   lambda: svc.reschedule_fees(compartida, {ids_cuotas(compartida)[2]: (9, 2026)}))

    # ============================================================
    print("\n--- Editar la compra conserva la 1ª cuota ---")
    # ============================================================
    editable = nueva_compra("Heladera", first_fee_month=5, first_fee_year=2026)
    svc.update_purchase_cuotas(editable, 4)
    caso("update_purchase_cuotas(4): arranca en la 1ª cuota de antes (05/2026), no en el mes de compra",
         [(5, 2026), (6, 2026), (7, 2026), (8, 2026)], meses(editable))
    ultima = ids_cuotas(editable)[3]
    svc.reschedule_fees(editable, {ultima: (10, 2026)})
    res = svc.update_purchase(editable, fecha="2026-05-05")
    caso("update_purchase(fecha +2 meses) → las 4 cuotas se corren", 4, res.data["fees_updated"])
    caso("… cada una 2 meses (también la movida a mano)", [(7, 2026), (8, 2026), (9, 2026), (12, 2026)], meses(editable))
    svc.update_purchase(editable, fecha="2026-05-20")
    caso("cambiar el día dentro del mismo mes no mueve nada", [(7, 2026), (8, 2026), (9, 2026), (12, 2026)],
         meses(editable))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
