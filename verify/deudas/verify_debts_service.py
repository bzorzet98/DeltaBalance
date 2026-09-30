"""
verify/deudas/verify_debts_service.py

Verifica DebtsService sobre la estructura FINAL de deudas
(docs/DATA_MODEL_DECISIONS.md sección 22): un libro de movimientos en dos
tabs — 'me_deben' / 'debo' — con monto_minor CON SIGNO (positivo = la deuda
crece, negativo = un pago). El saldo de una persona en un tab es la suma de
sus filas.

Cubre:
  - create(): normaliza la persona ("  noe " → "NOE"), concepto/notas
    vacíos → NULL, un monto negativo se guarda negativo (un pago), y
    rechaza con DebtError persona vacía, tab inválido, monto 0 o no
    entero, fecha mal formada, moneda inexistente y un doble-click (fila
    idéntica recién creada).
  - get() enriquecido con la moneda; list_by_tab() por mes (bordes), por
    año y completo, sin mezclar tabs; mes=13, mes sin anio y tab inválido
    → DebtError; list_by_person() normaliza el nombre y filtra por tab.
  - update(): persona (normalizada), concepto, fecha, monto negativo (se
    guarda tal cual), tab, moneda; campo no editable → DebtError; id
    inexistente → DebtNotFoundError; sin campos → success=False.
  - delete() y delete() de un id inexistente.
  - summary_by_person(): suma con signo por tab (un pago la baja, hasta
    dejarla en 0 — el saldado sigue apareciendo), hasta_fecha deja afuera
    lo posterior, por default hasta el último día del mes actual, los dos
    tabs no se mezclan (la misma persona en los dos), trae la moneda.
  - register_payment()/write_off()/mark_uncollectable()/apply_payment() ya
    no existen.

Correlo con:
    python verify/deudas/verify_debts_service.py
"""

import calendar
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.debts_service import DebtError, DebtNotFoundError, DebtsService


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
    manager.inicializar()  # base nueva: `deudas` ya nace con la estructura final
    svc = DebtsService(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    # ============================================================
    print("--- create() ---")
    # ============================================================
    prestamo = svc.create("  noe ", "Préstamo", "me_deben", 40000, ars, "2026-03-01")
    caso("create() devuelve success e id", (True, True), (prestamo.success, prestamo.entity_id is not None))
    fila = svc.get(prestamo.entity_id)
    caso("persona normalizada: '  noe ' → 'NOE'", "NOE", fila["entidad_persona"])
    caso("tab y monto tal cual", ("me_deben", 40000), (fila["tab"], fila["monto_minor"]))
    caso("get() trae la moneda (currency_code/decimales)", ("ARS", 2), (fila["currency_code"], fila["decimales"]))

    devolucion = svc.create("Noe", "", "me_deben", -10000, ars, "2026-03-15", notas="  ")
    fila_dev = svc.get(devolucion.entity_id)
    caso("monto negativo (un pago): se guarda negativo, en el mismo tab", ("me_deben", -10000),
         (fila_dev["tab"], fila_dev["monto_minor"]))
    caso("concepto y notas vacíos → NULL", (None, None), (fila_dev["concepto"], fila_dev["notas"]))

    caso_excepcion("persona vacía → DebtError", DebtError, lambda: svc.create("  ", "x", "me_deben", 100, ars, "2026-03-01"))
    caso_excepcion("tab inválido (el viejo 'a_favor') → DebtError", DebtError,
                   lambda: svc.create("A", "x", "a_favor", 100, ars, "2026-03-01"))
    caso_excepcion("monto 0 → DebtError", DebtError, lambda: svc.create("A", "x", "me_deben", 0, ars, "2026-03-01"))
    caso_excepcion("monto no entero → DebtError", DebtError, lambda: svc.create("A", "x", "me_deben", 10.5, ars, "2026-03-01"))
    caso_excepcion("fecha mal formada → DebtError", DebtError, lambda: svc.create("A", "x", "me_deben", 100, ars, "01/03/2026"))
    caso_excepcion("moneda inexistente → DebtError", DebtError, lambda: svc.create("A", "x", "me_deben", 100, 99999, "2026-03-01"))
    caso_excepcion(
        "doble-click (fila idéntica recién creada) → DebtError", DebtError,
        lambda: svc.create("Noe", "Préstamo", "me_deben", 40000, ars, "2026-03-01"),
    )

    # ============================================================
    print("\n--- list_by_tab() / list_by_person() ---")
    # ============================================================
    borde_inicio = svc.create("Kevin", "Cena", "debo", 5000, ars, "2026-04-01").entity_id
    borde_fin = svc.create("Kevin", "Taxi", "debo", 1500, ars, "2026-04-30").entity_id
    mayo = svc.create("Kevin", "Mayo", "debo", 999, ars, "2026-05-01").entity_id
    otro_anio = svc.create("Kevin", "Año que viene", "debo", 700, ars, "2027-01-15").entity_id
    kevin_me_debe = svc.create("Kevin", "Le pagué el cine", "me_deben", 1200, ars, "2026-04-10").entity_id
    caso("DEBO de abril: incluye el 1 y el 30, no el 1 de mayo (ni la de ME DEBEN)", {borde_inicio, borde_fin},
         {f["id"] for f in svc.list_by_tab("debo", 4, 2026)})
    caso("DEBO de 2026 (solo anio)", {borde_inicio, borde_fin, mayo}, {f["id"] for f in svc.list_by_tab("debo", anio=2026)})
    caso("DEBO completo", {borde_inicio, borde_fin, mayo, otro_anio}, {f["id"] for f in svc.list_by_tab("debo")})
    caso("ME DEBEN completo: solo las suyas, la más nueva primero",
         [kevin_me_debe, devolucion.entity_id, prestamo.entity_id], [f["id"] for f in svc.list_by_tab("me_deben")])
    caso_excepcion("mes=13 → DebtError", DebtError, lambda: svc.list_by_tab("debo", 13, 2026))
    caso_excepcion("mes sin anio → DebtError", DebtError, lambda: svc.list_by_tab("debo", 4))
    caso_excepcion("tab inválido → DebtError", DebtError, lambda: svc.list_by_tab("en_contra"))
    caso("list_by_person(' kevin '): el nombre se normaliza, los dos tabs", 5, len(svc.list_by_person(" kevin ")))
    caso("list_by_person('Kevin', tab='me_deben')", [kevin_me_debe],
         [f["id"] for f in svc.list_by_person("Kevin", tab="me_deben")])

    # ============================================================
    print("\n--- update() ---")
    # ============================================================
    res = svc.update(borde_inicio, entidad_persona="kevin r", concepto="Cena de cumple", fecha="2026-04-02")
    fila_upd = svc.get(borde_inicio)
    caso("update() de persona (normalizada), concepto y fecha", ("KEVIN R", "Cena de cumple", "2026-04-02"),
         (fila_upd["entidad_persona"], fila_upd["concepto"], fila_upd["fecha"]))
    caso("update() devuelve success=True", True, res.success)
    svc.update(borde_inicio, monto_minor=-6000)
    caso("monto negativo en update(): se guarda tal cual, sin tocar el tab", ("debo", -6000),
         (svc.get(borde_inicio)["tab"], svc.get(borde_inicio)["monto_minor"]))
    svc.update(borde_inicio, tab="me_deben")
    caso("update() de tab", "me_deben", svc.get(borde_inicio)["tab"])
    svc.update(borde_inicio, moneda_id=usd)
    caso("update() de moneda", "USD", svc.get(borde_inicio)["currency_code"])
    caso_excepcion("tab inválido en update() → DebtError", DebtError, lambda: svc.update(borde_inicio, tab="a_favor"))
    caso_excepcion("monto 0 en update() → DebtError", DebtError, lambda: svc.update(borde_inicio, monto_minor=0))
    caso_excepcion("campo no editable (origen_tipo) → DebtError", DebtError,
                   lambda: svc.update(borde_inicio, origen_tipo="transaccion"))
    caso_excepcion("id inexistente → DebtNotFoundError", DebtNotFoundError, lambda: svc.update(999999, concepto="x"))
    caso("update() sin campos → success=False", False, svc.update(borde_inicio).success)

    # ============================================================
    print("\n--- delete() ---")
    # ============================================================
    caso("delete() devuelve success=True", True, svc.delete(borde_fin).success)
    caso("la fila ya no está", None, svc.get(borde_fin))
    caso_excepcion("delete() de un id inexistente → DebtNotFoundError", DebtNotFoundError, lambda: svc.delete(borde_fin))

    # ============================================================
    print("\n--- summary_by_person() ---")
    # ============================================================
    def saldo(tab: str, persona: str, hasta: str | None = "2026-12-31") -> int | None:
        return next(
            (e["saldo_minor"] for e in svc.summary_by_person(tab, hasta)
             if e["entidad_persona"] == persona and e["moneda_id"] == ars),
            None,
        )

    caso("ME DEBEN — NOE: préstamo 400.00 − devolución 100.00", 30000, saldo("me_deben", "NOE"))
    caso("ME DEBEN — NOE hasta el 10/03: todavía sin la devolución", 40000, saldo("me_deben", "NOE", "2026-03-10"))
    svc.create("Noe", "Me devolvió el resto", "me_deben", -30000, ars, "2026-06-01")
    caso("un pago por el resto deja a NOE en 0 (saldado: sigue apareciendo)", 0, saldo("me_deben", "NOE"))
    caso("DEBO — KEVIN: lo que le debés, en positivo (mayo)", 999, saldo("debo", "KEVIN"))
    caso("ME DEBEN — KEVIN: lo que te debe, aparte", 1200, saldo("me_deben", "KEVIN"))
    caso_excepcion("hasta_fecha mal formada → DebtError", DebtError, lambda: svc.summary_by_person("me_deben", "31/12/2026"))
    caso_excepcion("tab inválido → DebtError", DebtError, lambda: svc.summary_by_person("a_favor"))
    resumen = {e["entidad_persona"]: e for e in svc.summary_by_person("debo", "2026-12-31") if e["moneda_id"] == ars}
    caso("summary_by_person() trae moneda_codigo y símbolo", ("ARS", "$"),
         (resumen["KEVIN"]["moneda_codigo"], resumen["KEVIN"]["moneda_simbolo"]))

    # Default: hasta el último día del mes ACTUAL (lo de este mes entra, lo del que viene no).
    hoy = date.today()
    fin_de_mes = date(hoy.year, hoy.month, calendar.monthrange(hoy.year, hoy.month)[1])
    mes_que_viene = date.fromordinal(fin_de_mes.toordinal() + 1)
    svc.create("Pato", "De este mes", "me_deben", 100, ars, fin_de_mes.isoformat())
    svc.create("Pato", "Del mes que viene", "me_deben", 50, ars, mes_que_viene.isoformat())
    caso("sin hasta_fecha: entra lo de fin de este mes, no lo del que viene", 100, saldo("me_deben", "PATO", None))

    print("\n--- API vieja ---")
    for metodo in ("register_payment", "write_off", "mark_uncollectable", "apply_payment", "get_saldo_neto", "list_all"):
        caso(f"{metodo}() ya no existe", False, hasattr(svc, metodo))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
