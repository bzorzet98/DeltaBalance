"""
verify/compras_cuotas/verify_resumen_por_tarjeta.py

Verifica FeesService.resumen_por_tarjeta(mes, anio) — método de agregación
nuevo (Tarea 4 de docs/PROXIMOS_PASOS.md: desglose del dashboard por
tarjeta de crédito, cuotas + cargos extra del resumen). Escenario:

- Tarjeta A y Tarjeta B: ambas con cuotas venciendo en 2026-06 → deben
  aparecer las dos.
- Tarjeta C: sin ninguna cuota venciendo en 2026-06 (solo tiene una compra
  que vence en otro mes) → NO debe aparecer en el resultado de 2026-06
  (detección dinámica, no una lista fija de cuentas).
- Tarjeta A además tiene un resumen abierto para 2026-06 con dos cargos
  extra cargados → monto_total_minor debe ser cuotas + cargos.
- Tarjeta B no tiene ningún resumen abierto para 2026-06 →
  monto_cargos_extra_minor debe quedar en 0, no romper nada.
- Una compra cancelada en Tarjeta A dentro del mismo mes/tarjeta →
  cancel_purchase() marca sus cuotas 'omitido' → NO deben sumarse al
  total (distinto de las cuotas 'pendiente'/'en_resumen'/'pagado', que sí
  cuentan).

Correlo con:
    python verify/compras_cuotas/verify_resumen_por_tarjeta.py
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

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    cuentas_repo = CuentasRepository(manager)
    svc = FeesService(manager)

    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    tarjeta_a = cuentas_repo.crear(nombre="Tarjeta A", tipo="credito", moneda_codigo="ARS")
    tarjeta_b = cuentas_repo.crear(nombre="Tarjeta B", tipo="credito", moneda_codigo="ARS")
    tarjeta_c = cuentas_repo.crear(nombre="Tarjeta C", tipo="credito", moneda_codigo="ARS")

    print("--- Escenario: dos tarjetas con actividad en 2026-06, una sin actividad ese mes ---")

    # Tarjeta A: compra en 3 cuotas arrancando en junio 2026 (100000 c/u).
    compra_a = svc.create_purchase(
        date_str="2026-06-05", concept="Notebook", account_id=tarjeta_a,
        category_id=cat_egreso, currency_code="ARS", total_amount=300000.0, total_fees=3,
    )

    # Tarjeta B: compra en 1 cuota en junio 2026 (50000).
    svc.create_purchase(
        date_str="2026-06-10", concept="Zapatillas", account_id=tarjeta_b,
        category_id=cat_egreso, currency_code="ARS", total_amount=50000.0, total_fees=1,
    )

    # Tarjeta C: compra que vence en MAYO 2026, no en junio — no debe
    # aparecer en el resultado de 2026-06.
    svc.create_purchase(
        date_str="2026-05-01", concept="Compra de mayo", account_id=tarjeta_c,
        category_id=cat_egreso, currency_code="ARS", total_amount=10000.0, total_fees=1,
    )

    resumen_junio = svc.resumen_por_tarjeta(mes=6, anio=2026)
    por_cuenta = {r["cuenta_id"]: r for r in resumen_junio}

    caso("resumen_por_tarjeta(6, 2026) incluye Tarjeta A", True, tarjeta_a in por_cuenta)
    caso("resumen_por_tarjeta(6, 2026) incluye Tarjeta B", True, tarjeta_b in por_cuenta)
    caso("resumen_por_tarjeta(6, 2026) NO incluye Tarjeta C (sin actividad ese mes)", False, tarjeta_c in por_cuenta)
    caso("resumen_por_tarjeta(6, 2026) devuelve exactamente 2 tarjetas", 2, len(resumen_junio))

    caso("Tarjeta A: monto_cuotas_minor = 100000 (una cuota de las 3 vence en junio)", 100000, por_cuenta[tarjeta_a]["monto_cuotas_minor"])
    caso("Tarjeta B: monto_cuotas_minor = 50000", 50000, por_cuenta[tarjeta_b]["monto_cuotas_minor"])
    caso("Tarjeta A sin resumen abierto todavía: monto_cargos_extra_minor = 0", 0, por_cuenta[tarjeta_a]["monto_cargos_extra_minor"])
    caso("Tarjeta A sin resumen abierto todavía: monto_total_minor = monto_cuotas_minor", 100000, por_cuenta[tarjeta_a]["monto_total_minor"])
    caso("Tarjeta B: cargos_extra_multiples_monedas = False (una sola moneda)", False, por_cuenta[tarjeta_b]["cargos_extra_multiples_monedas"])

    print("\n--- Tarjeta A: resumen abierto en 2026-06 con cargos extra ---")
    res_stmt_a = svc.open_statement(account_id=tarjeta_a, month=6, year=2026)
    svc.add_extra_charge(res_stmt_a.entity_id, concept="IVA", charge_type="impuesto", amount_minor=15000)
    svc.add_extra_charge(res_stmt_a.entity_id, concept="Ajuste a favor", charge_type="ajuste", amount_minor=-5000)

    resumen_junio_v2 = svc.resumen_por_tarjeta(mes=6, anio=2026)
    por_cuenta_v2 = {r["cuenta_id"]: r for r in resumen_junio_v2}

    caso("Tarjeta A: monto_cargos_extra_minor = 15000 - 5000 = 10000", 10000, por_cuenta_v2[tarjeta_a]["monto_cargos_extra_minor"])
    caso("Tarjeta A: monto_total_minor = 100000 cuotas + 10000 cargos = 110000", 110000, por_cuenta_v2[tarjeta_a]["monto_total_minor"])
    caso("Tarjeta B sigue sin cargos extra (no se le abrió resumen)", 0, por_cuenta_v2[tarjeta_b]["monto_cargos_extra_minor"])

    print("\n--- Tarjeta B: se abre resumen SIN cargar ningún cargo extra todavía ---")
    svc.open_statement(account_id=tarjeta_b, month=6, year=2026)
    resumen_junio_v3 = svc.resumen_por_tarjeta(mes=6, anio=2026)
    por_cuenta_v3 = {r["cuenta_id"]: r for r in resumen_junio_v3}
    caso("Tarjeta B con resumen abierto pero sin cargos: monto_cargos_extra_minor sigue en 0", 0, por_cuenta_v3[tarjeta_b]["monto_cargos_extra_minor"])

    print("\n--- Compra cancelada en Tarjeta A dentro del mismo mes: sus cuotas 'omitido' no cuentan ---")
    compra_cancelable = svc.create_purchase(
        date_str="2026-06-15", concept="Compra que se cancela", account_id=tarjeta_a,
        category_id=cat_egreso, currency_code="ARS", total_amount=20000.0, total_fees=1,
    )
    resumen_antes_cancelar = svc.resumen_por_tarjeta(mes=6, anio=2026)
    monto_antes = {r["cuenta_id"]: r["monto_cuotas_minor"] for r in resumen_antes_cancelar}[tarjeta_a]
    caso("Antes de cancelar: Tarjeta A ya suma la cuota de la compra cancelable (100000 + 20000)", 120000, monto_antes)

    svc.cancel_purchase(compra_cancelable.entity_id)
    resumen_despues_cancelar = svc.resumen_por_tarjeta(mes=6, anio=2026)
    monto_despues = {r["cuenta_id"]: r["monto_cuotas_minor"] for r in resumen_despues_cancelar}[tarjeta_a]
    caso("Después de cancelar: la cuota 'omitido' ya NO se suma (vuelve a 100000)", 100000, monto_despues)

    print("\n--- Mes sin ninguna tarjeta con actividad ---")
    resumen_vacio = svc.resumen_por_tarjeta(mes=12, anio=2030)
    caso("resumen_por_tarjeta() de un mes sin actividad devuelve lista vacía", [], resumen_vacio)

    print("\n--- Validación de mes fuera de rango ---")
    caso_excepcion_mes_invalido = False
    try:
        svc.resumen_por_tarjeta(mes=13, anio=2026)
    except ValueError:
        caso_excepcion_mes_invalido = True
    caso("resumen_por_tarjeta() con mes fuera de rango lanza ValueError", True, caso_excepcion_mes_invalido)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
