"""
verify/compras_cuotas/verify_cargos_extra_moneda_fecha.py

Verifica que los cargos extra de un resumen (impuestos, recargos,
ajustes/reintegros — categorías especiales de TARJETA DE CRÉDITO) cuenten
siempre en el total por tarjeta, cada uno en su moneda, y se listen para la
tabla de Compras en cuotas (docs/DATA_MODEL_DECISIONS.md sección 28). Los
cargos son filas de compras_cuotas con es_cargo_extra = 1; la migración de
los viejos (resumen_cargos_extra) y la eliminación las cubre
verify_cargos_extra_sync.py.

- add_extra_charge() guarda la moneda y la fecha en la compra; rechaza una
  moneda o una fecha inválidas; sin moneda usa la única de la tarjeta, y en
  una tarjeta con varias pide que se la pasen.
- Tarjeta VISA con cuotas en ARS y en USD el mismo mes: cada cargo suma en
  su moneda (antes los dos quedaban en 0 por la ambigüedad de la sección 15).
- Tarjeta MASTER sin cuotas ese mes: aparece en el total solo por sus cargos.
- list_extra_charges_in_month(): fecha, moneda, tipo y tarjeta de cada cargo.

Correlo con:
    python verify/compras_cuotas/verify_cargos_extra_moneda_fecha.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.fees_service import FeesError, FeesService

MES, ANIO = 7, 2026


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

    def lanza(funcion, excepcion) -> bool:
        try:
            funcion()
        except excepcion:
            return True
        return False

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")
    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    cuentas_repo = CuentasRepository(manager)
    svc = FeesService(manager)

    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    categoria = manager.fetchone("SELECT id FROM categorias WHERE subcategoria = 'Supermercado';")["id"]

    print("--- Migración ---")
    columnas = {f["name"] for f in manager.fetchall("PRAGMA table_info(compras_cuotas);")}
    caso("compras_cuotas tiene es_cargo_extra", True, "es_cargo_extra" in columnas)

    # VISA opera en ARS y USD; MASTER solo en ARS.
    visa = cuentas_repo.crear(nombre="VISA", tipo="credito", moneda_codigo="ARS")
    cuentas_repo.crear_saldo_inicial(visa, usd)
    master = cuentas_repo.crear(nombre="MASTER", tipo="credito", moneda_codigo="ARS")

    # VISA: una cuota en ARS y otra en USD en julio.
    svc.create_purchase(
        date_str="2026-07-05", concept="Heladera", account_id=visa, category_id=categoria,
        currency_code="ARS", total_amount=1000.0, total_fees=1,
    )
    svc.create_purchase(
        date_str="2026-07-06", concept="Suscripción", account_id=visa, category_id=categoria,
        currency_code="USD", total_amount=50.0, total_fees=1,
    )

    print("\n--- add_extra_charge(): guarda moneda y fecha ---")
    resumen_visa = svc.open_statement(visa, MES, ANIO).entity_id
    impuesto = svc.add_extra_charge(
        resumen_visa, concept="IMPUESTO PAIS", charge_type="impuesto", amount_minor=30000,
        currency_code="ARS", date_str="2026-07-20",
    )
    svc.add_extra_charge(
        resumen_visa, concept="REINTEGRO", charge_type="ajuste", amount_minor=-1000,
        currency_code="usd", date_str="2026-07-21",
    )
    guardado = svc.get_purchase(impuesto.entity_id)
    caso("el impuesto quedó con su fecha y su moneda", ("2026-07-20", "ARS"),
         (guardado["fecha_compra"], guardado["currency_code"]))
    caso("el resultado informa la moneda usada", "ARS", impuesto.data["currency_code"])
    caso("moneda inexistente → ValueError", True, lanza(lambda: svc.add_extra_charge(
        resumen_visa, concept="X", charge_type="recargo", amount_minor=1, currency_code="XXX",
    ), ValueError))
    caso("fecha inválida → ValueError", True, lanza(lambda: svc.add_extra_charge(
        resumen_visa, concept="X", charge_type="recargo", amount_minor=1, currency_code="ARS", date_str="2026-13-01",
    ), ValueError))
    caso("sin moneda en una tarjeta con dos (VISA: ARS y USD) → FeesError", True, lanza(lambda: svc.add_extra_charge(
        resumen_visa, concept="X", charge_type="recargo", amount_minor=1,
    ), FeesError))

    resumen_master = svc.open_statement(master, MES, ANIO).entity_id
    svc.add_extra_charge(
        resumen_master, concept="RECARGO MORA", charge_type="recargo", amount_minor=2000, date_str="2026-07-10",
    )

    print("\n--- resumen_por_tarjeta(): cada cargo en su moneda, también sin cuotas ---")
    grupos = {(g["account_name"], g["currency_code"]): g for g in svc.resumen_por_tarjeta(MES, ANIO)}
    caso("VISA ARS: 100.000 de cuotas + 30.000 de impuesto", (100000, 30000, 130000), (
        grupos[("VISA", "ARS")]["monto_cuotas_minor"], grupos[("VISA", "ARS")]["monto_cargos_extra_minor"],
        grupos[("VISA", "ARS")]["monto_total_minor"],
    ))
    caso("VISA USD: 5.000 de cuotas − 1.000 de reintegro", 4000, grupos[("VISA", "USD")]["monto_total_minor"])
    caso("MASTER aparece aunque no tenga cuotas en julio", True, ("MASTER", "ARS") in grupos)
    caso("… con 0 de cuotas y 2.000 de cargos (su moneda: la única de la tarjeta)", (0, 2000), (
        grupos[("MASTER", "ARS")]["monto_cuotas_minor"], grupos[("MASTER", "ARS")]["monto_cargos_extra_minor"],
    ))
    caso("ninguna tarjeta marcada con moneda ambigua", False, any(
        g["cargos_extra_multiples_monedas"] for g in grupos.values()
    ))

    print("\n--- list_extra_charges_in_month(): lo que muestra la tabla ---")
    cargos = {c["concepto"]: c for c in svc.list_extra_charges_in_month(MES, ANIO)}
    caso("lista los 3 cargos del mes (no las compras)", {"IMPUESTO PAIS", "REINTEGRO", "RECARGO MORA"}, set(cargos))
    caso("IMPUESTO PAIS: fecha, moneda, tipo y categoría",
         ("2026-07-20", "ARS", "impuesto", "Impuesto tarjeta"),
         tuple(cargos["IMPUESTO PAIS"][k] for k in ("fecha", "currency_code", "tipo", "category_name")))
    caso("REINTEGRO: monto con signo en USD", (-1000, "USD", 2), tuple(
        cargos["REINTEGRO"][k] for k in ("monto_minor", "currency_code", "decimales")
    ))
    caso("cada cargo trae su tarjeta y su resumen", ("MASTER", resumen_master),
         (cargos["RECARGO MORA"]["account_name"], cargos["RECARGO MORA"]["resumen_id"]))
    caso("mes sin cargos: lista vacía", [], svc.list_extra_charges_in_month(12, 2030))
    caso("mes fuera de rango → ValueError", True, lanza(lambda: svc.list_extra_charges_in_month(13, ANIO), ValueError))

    manager.desconectar()
    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
