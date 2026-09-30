"""
verify/compras_cuotas/verify_list_purchases_due_in_month.py

Verifica FeesService.list_purchases_due_in_month() — el filtro por período
de la pantalla Compras en cuotas: las compras con al menos una cuota que
VENCE en ese mes (cuotas_credito.mes_proyectado/anio_proyectado), no las
compradas en ese mes, con el monto de la cuota de ese mes.

Cubre:
  - Una compra de marzo en 3 cuotas aparece en marzo, abril y mayo (cuota
    1, 2 y 3) y no en febrero ni en junio.
  - monto_cuota_mes_minor es el de la cuota de ese mes, no el total.
  - Una compra de abril en 1 cuota aparece solo en abril, con su total.
  - Un reintegro (total negativo) trae la cuota con signo negativo.
  - Orden: fecha_compra más nueva primero.
  - Trae el shape enriquecido (currency_symbol, account_name).
  - mes fuera de 1-12: ValueError.

Correlo con:
    python verify/compras_cuotas/verify_list_purchases_due_in_month.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.fees_service import FeesService


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
    svc = FeesService(manager)
    cuenta = CuentasRepository(manager).crear(nombre="Tarjeta Vencimientos", tipo="credito", moneda_codigo="ARS")
    categoria = manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso' ORDER BY id LIMIT 1;")[0]["id"]

    heladera = svc.create_purchase(
        date_str="2026-03-05", concept="Heladera", account_id=cuenta, category_id=categoria,
        currency_code="ARS", total_amount=3000.0, total_fees=3,
    ).entity_id
    zapatillas = svc.create_purchase(
        date_str="2026-04-10", concept="Zapatillas", account_id=cuenta, category_id=categoria,
        currency_code="ARS", total_amount=800.0, total_fees=1,
    ).entity_id
    # create_purchase() solo acepta positivos: el reintegro se carga y se le
    # da vuelta el signo con update_purchase() (acepta totales negativos).
    devolucion = svc.create_purchase(
        date_str="2026-04-20", concept="Devolución", account_id=cuenta, category_id=categoria,
        currency_code="ARS", total_amount=200.0, total_fees=1,
    ).entity_id
    svc.update_purchase(devolucion, monto_total_minor=-20000)

    def ids(mes: int) -> list[int]:
        return [c["id"] for c in svc.list_purchases_due_in_month(mes, 2026)]

    def de(mes: int, compra_id: int) -> dict:
        return next(c for c in svc.list_purchases_due_in_month(mes, 2026) if c["id"] == compra_id)

    print("--- Compra de marzo en 3 cuotas ---")
    caso("febrero: no aparece (todavía no vence nada)", [], ids(2))
    caso("marzo: aparece", [heladera], ids(3))
    caso("marzo: monto de la cuota (1000.00), no el total", 100000, de(3, heladera)["monto_cuota_mes_minor"])
    caso("marzo: es la cuota 1", [1], de(3, heladera)["numeros_cuota_mes"])
    caso("abril: la cuota 2", [2], de(4, heladera)["numeros_cuota_mes"])
    caso("mayo: la cuota 3, estado pendiente", ([3], ["pendiente"]),
         (de(5, heladera)["numeros_cuota_mes"], de(5, heladera)["estados_cuota_mes"]))
    caso("junio: ya no aparece", [], ids(6))

    print("\n--- Abril: compras en 1 cuota + la cuota 2 de la heladera ---")
    caso("orden: fecha_compra más nueva primero", [devolucion, zapatillas, heladera], ids(4))
    caso("zapatillas: la única cuota es el total", 80000, de(4, zapatillas)["monto_cuota_mes_minor"])
    caso("devolución: cuota negativa (reintegro)", -20000, de(4, devolucion)["monto_cuota_mes_minor"])
    caso("shape enriquecido: trae currency_symbol y account_name",
         ("$", "Tarjeta Vencimientos"), (de(4, zapatillas)["currency_symbol"], de(4, zapatillas)["account_name"]))

    print("\n--- Validación ---")
    caso_excepcion("mes=13 → ValueError", ValueError, lambda: svc.list_purchases_due_in_month(13, 2026))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
