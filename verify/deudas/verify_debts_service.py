"""
verify/deudas/verify_debts_service.py

Verifica DebtsService sobre el LIBRO DE MOVIMIENTOS (reestructuración de
deudas, docs/DATA_MODEL_DECISIONS.md sección 22): cada fila es un monto con
dirección (a_favor / en_contra) y el saldo con una persona es la suma con
signo de sus filas. Un pago es una fila de tipo opuesto.

Cubre:
  - create(): normaliza la persona ("  noe " → "NOE"), concepto vacío →
    NULL, monto negativo = tipo contrario (guarda el positivo), y rechaza
    con DebtError persona vacía, tipo inválido, monto 0, fecha mal
    formada, moneda inexistente y un doble-click (fila idéntica recién
    creada).
  - get() enriquecido con la moneda; list_by_period() (bordes del mes);
    list_all().
  - update(): cada campo, monto negativo invierte el tipo, campo no
    editable → DebtError, id inexistente → DebtNotFoundError, sin campos →
    success=False.
  - delete() y delete() de un id inexistente.
  - get_saldo_neto(): suma con signo, un pago (fila opuesta) lo baja,
    hasta_fecha deja afuera lo posterior; summary_by_person() trae la moneda.
  - register_payment()/write_off() ya no existen.

Correlo con:
    python verify/deudas/verify_debts_service.py
"""

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
    manager.inicializar()  # reestructura `deudas` (base nueva, sin datos)
    svc = DebtsService(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    # ============================================================
    print("--- create() ---")
    # ============================================================
    prestamo = svc.create("  noe ", "Préstamo", "a_favor", 40000, ars, "2026-03-01")
    caso("create() devuelve success e id", (True, True), (prestamo.success, prestamo.entity_id is not None))
    fila = svc.get(prestamo.entity_id)
    caso("persona normalizada: '  noe ' → 'NOE'", "NOE", fila["entidad_persona"])
    caso("get() trae la moneda (currency_code/decimales)", ("ARS", 2), (fila["currency_code"], fila["decimales"]))

    devolucion = svc.create("Noe", "", "a_favor", -10000, ars, "2026-03-15", notas="  ")
    fila_dev = svc.get(devolucion.entity_id)
    caso("monto negativo: se guarda positivo…", 10000, fila_dev["monto_minor"])
    caso("… con el tipo contrario (en_contra)", "en_contra", fila_dev["tipo"])
    caso("concepto y notas vacíos → NULL", (None, None), (fila_dev["concepto"], fila_dev["notas"]))

    caso_excepcion("persona vacía → DebtError", DebtError, lambda: svc.create("  ", "x", "a_favor", 100, ars, "2026-03-01"))
    caso_excepcion("tipo inválido → DebtError", DebtError, lambda: svc.create("A", "x", "prestado", 100, ars, "2026-03-01"))
    caso_excepcion("monto 0 → DebtError", DebtError, lambda: svc.create("A", "x", "a_favor", 0, ars, "2026-03-01"))
    caso_excepcion("monto no entero → DebtError", DebtError, lambda: svc.create("A", "x", "a_favor", 10.5, ars, "2026-03-01"))
    caso_excepcion("fecha mal formada → DebtError", DebtError, lambda: svc.create("A", "x", "a_favor", 100, ars, "01/03/2026"))
    caso_excepcion("moneda inexistente → DebtError", DebtError, lambda: svc.create("A", "x", "a_favor", 100, 99999, "2026-03-01"))
    caso_excepcion(
        "doble-click (fila idéntica recién creada) → DebtError", DebtError,
        lambda: svc.create("Noe", "Préstamo", "a_favor", 40000, ars, "2026-03-01"),
    )

    # ============================================================
    print("\n--- list_by_period() / list_all() ---")
    # ============================================================
    borde_inicio = svc.create("Kevin", "Cena", "en_contra", 5000, ars, "2026-04-01").entity_id
    borde_fin = svc.create("Kevin", "Taxi", "en_contra", 1500, ars, "2026-04-30").entity_id
    svc.create("Kevin", "Mayo", "en_contra", 999, ars, "2026-05-01")
    caso("abril: incluye el 1 y el 30, no el 1 de mayo", {borde_inicio, borde_fin},
         {f["id"] for f in svc.list_by_period(4, 2026)})
    caso_excepcion("mes=13 → DebtError", DebtError, lambda: svc.list_by_period(13, 2026))
    caso("list_all(): todas las filas", 5, len(svc.list_all()))

    # ============================================================
    print("\n--- update() ---")
    # ============================================================
    res = svc.update(borde_inicio, entidad_persona="kevin r", concepto="Cena de cumple", fecha="2026-04-02")
    fila_upd = svc.get(borde_inicio)
    caso("update() de persona (normalizada), concepto y fecha", ("KEVIN R", "Cena de cumple", "2026-04-02"),
         (fila_upd["entidad_persona"], fila_upd["concepto"], fila_upd["fecha"]))
    caso("update() devuelve success=True", True, res.success)
    svc.update(borde_inicio, monto_minor=-6000)
    fila_neg = svc.get(borde_inicio)
    caso("monto negativo en update(): invierte el tipo actual (en_contra → a_favor)", ("a_favor", 6000),
         (fila_neg["tipo"], fila_neg["monto_minor"]))
    svc.update(borde_inicio, tipo="a_favor", monto_minor=-6000)
    caso("tipo + monto negativo en la misma llamada: vale el tipo pasado, invertido", "en_contra",
         svc.get(borde_inicio)["tipo"])
    svc.update(borde_inicio, moneda_id=usd)
    caso("update() de moneda", "USD", svc.get(borde_inicio)["currency_code"])
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
    print("\n--- get_saldo_neto() / summary_by_person() ---")
    # ============================================================
    def saldo(persona: str, hasta: str | None = None) -> int | None:
        return next(
            (e["saldo_minor"] for e in svc.get_saldo_neto(hasta) if e["entidad_persona"] == persona and e["moneda_id"] == ars),
            None,
        )

    caso("NOE: préstamo 400.00 − devolución 100.00", 30000, saldo("NOE", "2026-12-31"))
    caso("NOE hasta el 10/03: todavía sin la devolución", 40000, saldo("NOE", "2026-03-10"))
    svc.create("Noe", "Me devolvió el resto", "en_contra", 30000, ars, "2026-06-01")
    caso("un pago (fila de tipo opuesto) deja a NOE en 0", 0, saldo("NOE", "2026-12-31"))
    caso("KEVIN (en_contra): negativo", -999, saldo("KEVIN", "2026-12-31"))
    caso_excepcion("hasta_fecha mal formada → DebtError", DebtError, lambda: svc.get_saldo_neto("31/12/2026"))
    resumen = {e["entidad_persona"]: e for e in svc.summary_by_person("2026-12-31") if e["moneda_id"] == ars}
    caso("summary_by_person() trae moneda_codigo y símbolo", ("ARS", "$"),
         (resumen["KEVIN"]["moneda_codigo"], resumen["KEVIN"]["moneda_simbolo"]))

    print("\n--- API vieja ---")
    caso("register_payment() ya no existe", False, hasattr(svc, "register_payment"))
    caso("write_off() ya no existe", False, hasattr(svc, "write_off"))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
