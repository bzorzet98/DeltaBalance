"""
verify/compras_cuotas/verify_update_purchase.py

Verifica FeesService.update_purchase() y update_purchase_cuotas() —
métodos de la edición inline de compras ya cargadas en
ui/screens/compras_cuotas.py (Concepto, Banco, Categoría, Monto total,
Cuotas, Fecha).

Qué se prueba y por qué:
- Concepto y Categoría se editan siempre (son descriptivos: ninguna fila
  generada depende de ellos de forma que se rompa), incluso con cuotas ya
  en un resumen o con la compra compartida.
- Fecha dentro del mismo mes: siempre (las cuotas guardan mes/año, no el
  día). Fecha a OTRO mes: regenera el cronograma desde el mes nuevo, solo
  si todas las cuotas siguen 'pendiente' y ninguna está compartida.
- Banco: solo si todas las cuotas siguen 'pendiente' (una cuota en un
  resumen pertenece al resumen de ESA tarjeta).
- Moneda: solo si todas las cuotas siguen 'pendiente' y la compra no está
  compartida (gastos_compartidos no tiene moneda propia). Mantiene el
  importe MOSTRADO, igual que el Registro: con otra cantidad de decimales
  (CLP 0) se reescalan total, cuota y cuotas.
- Cuotas (update_purchase_cuotas()): borra y regenera todas las cuotas
  con el mismo total, solo si todas siguen 'pendiente', ninguna está
  compartida y el reparto es el default (una compra compartida en modo
  'total_unico' SÍ se puede, porque ese gasto sale del total, que no
  cambia).
- Monto total aplica la ventana de corrección temprana (CLAUDE.md §4): cada
  cuota de cuotas_credito guarda su propio monto_cuota_minor y su estado,
  así que cambiar el total reescribe la compra Y todas sus cuotas en la
  misma transacción — pero SOLO mientras nada de lo generado tiene estado
  propio. Se bloquea (FeesError) si:
    * alguna cuota ya salió de 'pendiente' (en_resumen/pagado/omitido),
    * la compra está compartida (gastos_compartidos por compra o por cuota),
    * la compra se creó con un amount_per_fee custom (recalcular el reparto
      borraría esa diferencia en silencio).
- Cuando se bloquea, NADA queda escrito a medias (ni el monto ni otro campo
  pasado en la misma llamada).

Correlo con:
    python verify/compras_cuotas/verify_update_purchase.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuentas_repository import CuentasRepository
from services.fees_service import FeesError, FeesService, PurchaseNotFoundError
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
    compras_repo = ComprasCuotasRepository(manager)
    svc = FeesService(manager)
    shared_svc = SharedExpensesService(manager)

    cuenta = cuentas_repo.crear(nombre="Tarjeta Update", tipo="credito", moneda_codigo="ARS")
    cat_a, cat_b = [
        r["id"] for r in manager.fetchall("SELECT id FROM categorias WHERE tipo = 'egreso' ORDER BY id LIMIT 2;")
    ]

    def nueva_compra(concepto: str, **kwargs) -> int:
        # Conceptos distintos en cada compra: CompraDuplicadaError bloquea
        # compras idénticas creadas con menos de 5 segundos de diferencia.
        return svc.create_purchase(
            date_str="2026-03-05", concept=concepto, account_id=cuenta, category_id=cat_a,
            currency_code="ARS", total_amount=kwargs.pop("total_amount", 3000.0),
            total_fees=kwargs.pop("total_fees", 3), **kwargs,
        ).entity_id

    def montos_cuotas(compra_id: int) -> list[int]:
        return [c["monto_cuota_minor"] for c in svc.get_fees_for_purchase(compra_id)]

    # ============================================================
    print("--- Concepto: se edita y se guarda sin espacios sobrantes ---")
    # ============================================================
    compra_1 = nueva_compra("Lavarropas")
    res = svc.update_purchase(compra_1, concepto="  Lavarropas Drean  ")
    caso("update_purchase(concepto=...) devuelve success=True", True, res.success)
    caso("el concepto queda guardado sin espacios", "Lavarropas Drean", svc.get_purchase(compra_1)["concepto"])
    caso("editar el concepto NO toca las cuotas", [100000, 100000, 100000], montos_cuotas(compra_1))

    # ============================================================
    print("\n--- Categoría ---")
    # ============================================================
    res = svc.update_purchase(compra_1, categoria_id=cat_b)
    caso("update_purchase(categoria_id=...) devuelve success=True", True, res.success)
    caso("la categoría nueva queda guardada", cat_b, svc.get_purchase(compra_1)["categoria_id"])

    # ============================================================
    print("\n--- Monto total: compra + todas sus cuotas, en la misma transacción ---")
    # ============================================================
    res = svc.update_purchase(compra_1, monto_total_minor=450000)
    fila = svc.get_purchase(compra_1)
    caso("update_purchase(monto_total_minor=...) devuelve success=True", True, res.success)
    caso("informa 3 cuotas reescritas", 3, res.data["fees_updated"])
    caso("monto_total_minor de la compra = 450000", 450000, fila["monto_total_minor"])
    caso("monto_por_cuota_minor recalculado = 450000 / 3 = 150000", 150000, fila["monto_por_cuota_minor"])
    caso("las 3 cuotas pasan a 150000", [150000, 150000, 150000], montos_cuotas(compra_1))

    res = svc.update_purchase(compra_1, monto_total_minor=100000)
    caso(
        "reparto con redondeo: 100000 / 3 = 33333 por cuota (mismo criterio que create_purchase())",
        [33333, 33333, 33333],
        montos_cuotas(compra_1),
    )

    # ============================================================
    print("\n--- Sin cambios reales ---")
    # ============================================================
    caso("update_purchase() sin campos devuelve success=False", False, svc.update_purchase(compra_1).success)
    caso(
        "update_purchase() con el MISMO monto actual devuelve success=False (no cuenta como cambio)",
        False,
        svc.update_purchase(compra_1, monto_total_minor=100000).success,
    )

    # ============================================================
    print("\n--- Validaciones ---")
    # ============================================================
    caso_excepcion("concepto vacío → FeesError", FeesError, lambda: svc.update_purchase(compra_1, concepto="   "))
    caso_excepcion("categoria_id inexistente → FeesError", FeesError, lambda: svc.update_purchase(compra_1, categoria_id=999999))
    caso_excepcion("monto_total_minor = 0 → FeesError", FeesError, lambda: svc.update_purchase(compra_1, monto_total_minor=0))
    caso_excepcion("monto_total_minor negativo → FeesError", FeesError, lambda: svc.update_purchase(compra_1, monto_total_minor=-500))
    caso_excepcion("compra inexistente → PurchaseNotFoundError", PurchaseNotFoundError, lambda: svc.update_purchase(999999, concepto="X"))

    # ============================================================
    print("\n--- §4: una cuota ya en un resumen bloquea el monto (y solo el monto) ---")
    # ============================================================
    compra_2 = nueva_compra("Heladera")
    resumen = svc.open_statement(account_id=cuenta, month=3, year=2026).entity_id
    primera_cuota = svc.get_fees_for_purchase(compra_2)[0]["id"]
    svc.confirm_fee(fee_id=primera_cuota, statement_id=resumen)

    caso_excepcion(
        "cambiar el monto con una cuota 'en_resumen' → FeesError",
        FeesError,
        lambda: svc.update_purchase(compra_2, monto_total_minor=360000),
    )
    caso("el monto total quedó sin tocar", 300000, svc.get_purchase(compra_2)["monto_total_minor"])
    caso("las cuotas quedaron sin tocar", [100000, 100000, 100000], montos_cuotas(compra_2))

    caso_excepcion(
        "concepto + monto en la misma llamada (bloqueada) → FeesError",
        FeesError,
        lambda: svc.update_purchase(compra_2, concepto="Heladera Gafa", monto_total_minor=360000),
    )
    caso("…y el concepto tampoco se escribió a medias", "Heladera", svc.get_purchase(compra_2)["concepto"])

    res = svc.update_purchase(compra_2, concepto="Heladera Gafa")
    caso("el concepto SÍ se puede corregir con la cuota en resumen", "Heladera Gafa", svc.get_purchase(compra_2)["concepto"])
    caso(
        "pasar el mismo monto actual no dispara el bloqueo (no es un cambio)",
        False,
        svc.update_purchase(compra_2, monto_total_minor=300000).success,
    )

    # ============================================================
    print("\n--- §4: compra compartida en modo 'prorrateado' (un gasto por cuota) ---")
    # ============================================================
    hogar = shared_svc.create_hogar(nombre_creador_local="bruno", nombre_hogar="Hogar Update").entity_id
    compra_3 = nueva_compra("Colchón")
    shared_svc.add_shared_purchase(compra_id=compra_3, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0)

    caso_excepcion(
        "cambiar el monto de una compra compartida por cuota → FeesError",
        FeesError,
        lambda: svc.update_purchase(compra_3, monto_total_minor=390000),
    )
    caso("el monto total quedó sin tocar", 300000, svc.get_purchase(compra_3)["monto_total_minor"])
    caso("la categoría SÍ se puede corregir", True, svc.update_purchase(compra_3, categoria_id=cat_b).success)

    # ============================================================
    print("\n--- §4: compra compartida en modo 'total_unico' (un gasto por la compra) ---")
    # ============================================================
    compra_4 = nueva_compra("Sillón")
    compras_repo.actualizar(compra_4, modo_deuda="total_unico")
    shared_svc.add_shared_purchase(compra_id=compra_4, hogar_id=hogar, pagador="bruno", coeficiente_deuda=50.0)

    caso_excepcion(
        "cambiar el monto de una compra compartida por total → FeesError",
        FeesError,
        lambda: svc.update_purchase(compra_4, monto_total_minor=390000),
    )

    # ============================================================
    print("\n--- Compra creada con amount_per_fee custom (ej. con interés) ---")
    # ============================================================
    compra_5 = nueva_compra("Notebook", amount_per_fee=1100.0)
    caso_excepcion(
        "cambiar el monto de una compra con cuota custom → FeesError (no se pierde el interés en silencio)",
        FeesError,
        lambda: svc.update_purchase(compra_5, monto_total_minor=330000),
    )
    caso("las cuotas custom quedaron sin tocar", [110000, 110000, 110000], montos_cuotas(compra_5))

    # ============================================================
    print("\n--- Compra cancelada (cuotas 'omitido') ---")
    # ============================================================
    compra_6 = nueva_compra("Bicicleta")
    svc.cancel_purchase(compra_6)
    caso_excepcion(
        "cambiar el monto de una compra cancelada → FeesError",
        FeesError,
        lambda: svc.update_purchase(compra_6, monto_total_minor=250000),
    )
    caso("el concepto de una compra cancelada SÍ se puede corregir", True, svc.update_purchase(compra_6, concepto="Bici").success)

    def meses_cuotas(compra_id: int) -> list[tuple[int, int]]:
        return [(c["mes_proyectado"], c["anio_proyectado"]) for c in svc.get_fees_for_purchase(compra_id)]

    # ============================================================
    print("\n--- Banco (cuenta_id) ---")
    # ============================================================
    cuenta_b = cuentas_repo.crear(nombre="Tarjeta Update B", tipo="credito", moneda_codigo="ARS")
    compra_7 = nueva_compra("Televisor")
    res = svc.update_purchase(compra_7, cuenta_id=cuenta_b)
    caso("update_purchase(cuenta_id=...) devuelve success=True", True, res.success)
    caso("la compra queda en la tarjeta nueva", cuenta_b, svc.get_purchase(compra_7)["cuenta_id"])
    caso_excepcion(
        "cambiar la tarjeta con una cuota 'en_resumen' → FeesError (esa cuota es del resumen de la otra tarjeta)",
        FeesError,
        lambda: svc.update_purchase(compra_2, cuenta_id=cuenta_b),
    )
    caso_excepcion("cuenta inexistente → FeesError", FeesError, lambda: svc.update_purchase(compra_7, cuenta_id=999999))

    # ============================================================
    print("\n--- Fecha dentro del mismo mes: no toca las cuotas ---")
    # ============================================================
    res = svc.update_purchase(compra_7, fecha="2026-03-20")
    caso("update_purchase(fecha=mismo mes) devuelve success=True", True, res.success)
    caso("fecha_compra = 2026-03-20", "2026-03-20", svc.get_purchase(compra_7)["fecha_compra"])
    caso("las cuotas siguen en mar/abr/may", [(3, 2026), (4, 2026), (5, 2026)], meses_cuotas(compra_7))
    caso("informa 0 cuotas reescritas", 0, res.data["fees_updated"])
    caso(
        "con una cuota 'en_resumen', corregir el día dentro del mismo mes SÍ se puede",
        True,
        svc.update_purchase(compra_2, fecha="2026-03-28").success,
    )

    # ============================================================
    print("\n--- Fecha a otro mes: el cronograma se regenera desde el mes nuevo ---")
    # ============================================================
    res = svc.update_purchase(compra_7, fecha="2026-05-10")
    caso("update_purchase(fecha=otro mes) devuelve success=True", True, res.success)
    caso("informa 3 cuotas regeneradas", 3, res.data["fees_updated"])
    caso("las cuotas pasan a may/jun/jul", [(5, 2026), (6, 2026), (7, 2026)], meses_cuotas(compra_7))
    caso("los montos de las cuotas no cambian", [100000, 100000, 100000], montos_cuotas(compra_7))

    svc.update_purchase(compra_7, fecha="2026-11-02")
    caso("cruce de año: nov/dic 2026 + ene 2027", [(11, 2026), (12, 2026), (1, 2027)], meses_cuotas(compra_7))

    caso_excepcion(
        "mover a otro mes con una cuota 'en_resumen' → FeesError",
        FeesError,
        lambda: svc.update_purchase(compra_2, fecha="2026-04-05"),
    )
    caso_excepcion(
        "mover a otro mes una compra compartida por cuota → FeesError",
        FeesError,
        lambda: svc.update_purchase(compra_3, fecha="2026-04-05"),
    )
    res = svc.update_purchase(compra_5, fecha="2026-06-05")
    caso("mover a otro mes una compra con cuota custom SÍ se puede", True, res.success)
    caso("…y conserva el monto custom de cada cuota", [110000, 110000, 110000], montos_cuotas(compra_5))
    caso_excepcion("fecha con formato inválido → ValueError", ValueError, lambda: svc.update_purchase(compra_7, fecha="05/10/2026"))

    # ============================================================
    print("\n--- update_purchase_cuotas(): otra cantidad, mismo total ---")
    # ============================================================
    compra_8 = nueva_compra("Aire acondicionado")
    res = svc.update_purchase_cuotas(compra_8, 6)
    fila = svc.get_purchase(compra_8)
    caso("update_purchase_cuotas(6) devuelve success=True", True, res.success)
    caso("total_cuotas = 6", 6, fila["total_cuotas"])
    caso("el monto total se mantiene en 300000", 300000, fila["monto_total_minor"])
    caso("monto_por_cuota_minor = 300000 / 6 = 50000", 50000, fila["monto_por_cuota_minor"])
    caso("hay 6 cuotas de 50000", [50000] * 6, montos_cuotas(compra_8))
    caso("numeradas 1..6", [1, 2, 3, 4, 5, 6], [c["numero_cuota"] for c in svc.get_fees_for_purchase(compra_8)])
    caso("proyectadas mar..ago 2026", [(m, 2026) for m in range(3, 9)], meses_cuotas(compra_8))

    svc.update_purchase_cuotas(compra_8, 7)
    caso("con redondeo: 300000 / 7 = 42857 por cuota", [42857] * 7, montos_cuotas(compra_8))
    caso("la misma cantidad actual devuelve success=False", False, svc.update_purchase_cuotas(compra_8, 7).success)

    caso_excepcion("0 cuotas → FeesError", FeesError, lambda: svc.update_purchase_cuotas(compra_8, 0))
    caso_excepcion("con una cuota 'en_resumen' → FeesError", FeesError, lambda: svc.update_purchase_cuotas(compra_2, 6))
    caso("…y las cuotas de esa compra quedaron intactas", 3, len(svc.get_fees_for_purchase(compra_2)))
    caso_excepcion("compartida por cuota (prorrateado) → FeesError", FeesError, lambda: svc.update_purchase_cuotas(compra_3, 6))
    caso_excepcion("con cuota custom → FeesError", FeesError, lambda: svc.update_purchase_cuotas(compra_5, 6))
    caso_excepcion("compra cancelada (cuotas 'omitido') → FeesError", FeesError, lambda: svc.update_purchase_cuotas(compra_6, 6))
    caso_excepcion("compra inexistente → PurchaseNotFoundError", PurchaseNotFoundError, lambda: svc.update_purchase_cuotas(999999, 6))

    res = svc.update_purchase_cuotas(compra_4, 6)
    caso("compartida por total ('total_unico') SÍ se puede: el total no cambia", True, res.success)
    caso("…y queda con 6 cuotas", 6, len(svc.get_fees_for_purchase(compra_4)))

    # ============================================================
    print("\n--- Moneda: mantiene el importe mostrado ---")
    # ============================================================
    compra_9 = nueva_compra("Zapatillas")
    res = svc.update_purchase(compra_9, moneda_codigo="USD")
    fila = svc.get_purchase(compra_9)
    caso("ARS → USD devuelve success=True", True, res.success)
    caso("la compra queda en USD", "USD", fila["currency_code"])
    caso("mismos decimales (2): el total no cambia (300000 = 3000.00)", 300000, fila["monto_total_minor"])
    caso("…y las cuotas tampoco", [100000, 100000, 100000], montos_cuotas(compra_9))

    res = svc.update_purchase(compra_9, moneda_codigo="CLP")
    fila = svc.get_purchase(compra_9)
    caso("USD → CLP (0 decimales): total reescalado 300000 → 3000 (sigue siendo 3000)", 3000, fila["monto_total_minor"])
    caso("monto_por_cuota_minor recalculado del total nuevo = 3000 / 3 = 1000", 1000, fila["monto_por_cuota_minor"])
    caso("las 3 cuotas pasan a 1000", [1000, 1000, 1000], montos_cuotas(compra_9))
    caso("informa 3 cuotas reescritas", 3, res.data["fees_updated"])

    svc.update_purchase(compra_9, moneda_codigo="ARS")
    fila = svc.get_purchase(compra_9)
    caso("CLP → ARS (2 decimales): total reescalado 3000 → 300000", 300000, fila["monto_total_minor"])
    caso("…y cuotas de 100000", [100000, 100000, 100000], montos_cuotas(compra_9))

    caso("la misma moneda actual devuelve success=False", False, svc.update_purchase(compra_9, moneda_codigo="ARS").success)
    caso_excepcion("moneda inexistente → ValueError", ValueError, lambda: svc.update_purchase(compra_9, moneda_codigo="XYZ"))

    res = svc.update_purchase(compra_9, moneda_codigo="CLP", monto_total_minor=5000)
    fila = svc.get_purchase(compra_9)
    caso(
        "moneda + monto en la misma llamada: el monto se toma ya en la moneda nueva (5000 CLP)",
        ("CLP", 5000),
        (fila["currency_code"], fila["monto_total_minor"]),
    )
    caso("…con cuotas de round(5000 / 3) = 1667", [1667, 1667, 1667], montos_cuotas(compra_9))

    res = svc.update_purchase(compra_5, moneda_codigo="CLP")
    caso("con cuota custom SÍ se puede cambiar la moneda", True, res.success)
    caso("…y la cuota custom se reescala tal cual (110000 → 1100), sin recalcular el reparto",
         [1100, 1100, 1100], montos_cuotas(compra_5))

    compra_10 = nueva_compra("Chicle", total_amount=0.40, total_fees=1)
    caso_excepcion(
        "0.40 ARS → CLP redondea a 0 → FeesError",
        FeesError,
        lambda: svc.update_purchase(compra_10, moneda_codigo="CLP"),
    )
    caso("…y la compra sigue en ARS", "ARS", svc.get_purchase(compra_10)["currency_code"])

    caso_excepcion("con una cuota 'en_resumen' → FeesError", FeesError, lambda: svc.update_purchase(compra_2, moneda_codigo="USD"))
    caso_excepcion("compartida por cuota (prorrateado) → FeesError", FeesError, lambda: svc.update_purchase(compra_3, moneda_codigo="USD"))
    caso_excepcion("compartida por total (total_unico) → FeesError", FeesError, lambda: svc.update_purchase(compra_4, moneda_codigo="USD"))

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
