"""
verify/deudas/verify_debts_service.py

Verifica DebtsService COMPLETO (no el repositorio pelado) después de la
migración a DeudasRepository (Fase 2, bloque DEUDAS paso 2): que
create()/get()/list_debts()/register_payment()/update()/write_off() siguen
funcionando end-to-end con las validaciones y excepciones de negocio
intactas (DebtNotFoundError, DebtAlreadySettledError,
PaymentExceedsBalanceError).

El caso que más importa de esta migración es el fix del bug donde
update(concept=..., notes=...) pisaba ambos valores en la misma columna
`notas` — acá se prueba explícitamente que, con valores DISTINTOS entre sí,
get() después devuelve concepto y notas por separado, cada uno con su
propio valor.

Correlo con:
    python verify/deudas/verify_debts_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.debts_service import (
    DebtsService,
    DebtError,
    DebtNotFoundError,
    DebtAlreadySettledError,
    PaymentExceedsBalanceError,
)


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
    svc = DebtsService(manager)

    print("--- create() — camino feliz ---")
    resultado = svc.create(
        person="Noe",
        debt_type="a_favor",
        amount=5000.0,
        currency_code="ARS",
        date_str="2026-01-10",
        concept="Shared dinner",
        notes="Cena del viernes",
    )
    caso("create() devuelve success=True", True, resultado.success)
    caso("create() devuelve un debt_id numérico", True, isinstance(resultado.debt_id, int))
    debt_id = resultado.debt_id

    print("\n--- create() — validaciones de negocio intactas ---")
    caso_excepcion(
        "create() con amount<=0 sigue lanzando ValueError",
        ValueError,
        lambda: svc.create(
            person="Noe", debt_type="a_favor", amount=0.0, currency_code="ARS",
            date_str="2026-01-10", concept="X",
        ),
    )
    caso_excepcion(
        "create() con person vacío sigue lanzando DebtError",
        DebtError,
        lambda: svc.create(
            person="   ", debt_type="a_favor", amount=10.0, currency_code="ARS",
            date_str="2026-01-10", concept="X",
        ),
    )
    caso_excepcion(
        "create() con concept vacío sigue lanzando DebtError",
        DebtError,
        lambda: svc.create(
            person="Noe", debt_type="a_favor", amount=10.0, currency_code="ARS",
            date_str="2026-01-10", concept="   ",
        ),
    )
    caso_excepcion(
        "create() con debt_type inválido sigue lanzando ValueError",
        ValueError,
        lambda: svc.create(
            person="Noe", debt_type="ninguno", amount=10.0, currency_code="ARS",
            date_str="2026-01-10", concept="X",
        ),
    )
    caso_excepcion(
        "create() con currency_code inexistente sigue lanzando ValueError",
        ValueError,
        lambda: svc.create(
            person="Noe", debt_type="a_favor", amount=10.0, currency_code="XYZ",
            date_str="2026-01-10", concept="X",
        ),
    )

    print("\n--- get() — enriquecido con JOIN a monedas ---")
    fila = svc.get(debt_id)
    caso("get() encuentra la deuda", "Noe", fila["entidad_persona"] if fila else None)
    caso("get() trae currency_code vía JOIN", "ARS", fila["currency_code"] if fila else None)
    caso("get() trae currency_symbol vía JOIN", "$", fila["currency_symbol"] if fila else None)
    caso("get() ahora persiste y devuelve concepto (antes se descartaba)", "Shared dinner", fila["concepto"] if fila else None)
    caso("get() sigue trayendo notas", "Cena del viernes", fila["notas"] if fila else None)
    caso("get() de un id inexistente devuelve None", None, svc.get(999999))

    print("\n--- list_debts() — filtros + shape enriquecido intactos ---")
    listado = svc.list_debts(person="Noe")
    ids_listado = [r["id"] for r in listado]
    caso("list_debts(person='Noe') incluye la deuda creada", True, debt_id in ids_listado)
    fila_lista = next((r for r in listado if r["id"] == debt_id), None)
    caso("list_debts() trae currency_code enriquecido", True, "currency_code" in fila_lista.keys() if fila_lista else False)

    print("\n--- register_payment() — pago parcial ---")
    res_pago_1 = svc.register_payment(
        debt_id=debt_id, amount=2000.0, currency_code="ARS", date_str="2026-01-20",
    )
    caso("register_payment() parcial devuelve success=True", True, res_pago_1.success)
    caso("register_payment() parcial no salda la deuda", False, res_pago_1.data["settled"])
    caso("register_payment() parcial deja pending_amount correcto", 3000.0, res_pago_1.data["pending_amount"])

    print("\n--- register_payment() — validaciones de negocio intactas ---")
    caso_excepcion(
        "register_payment() sobre id inexistente sigue lanzando DebtNotFoundError",
        DebtNotFoundError,
        lambda: svc.register_payment(debt_id=999999, amount=100.0, currency_code="ARS", date_str="2026-01-20"),
    )
    caso_excepcion(
        "register_payment() que excede el saldo pendiente sigue lanzando PaymentExceedsBalanceError",
        PaymentExceedsBalanceError,
        lambda: svc.register_payment(debt_id=debt_id, amount=999999.0, currency_code="ARS", date_str="2026-01-20"),
    )
    caso_excepcion(
        "register_payment() con amount<=0 sigue lanzando ValueError",
        ValueError,
        lambda: svc.register_payment(debt_id=debt_id, amount=0.0, currency_code="ARS", date_str="2026-01-20"),
    )
    caso_excepcion(
        "register_payment() con payment_type inválido sigue lanzando ValueError",
        ValueError,
        lambda: svc.register_payment(debt_id=debt_id, amount=100.0, currency_code="ARS", date_str="2026-01-20", payment_type="invalido"),
    )

    print("\n--- register_payment() — pago que salda la deuda ---")
    res_pago_2 = svc.register_payment(
        debt_id=debt_id, amount=3000.0, currency_code="ARS", date_str="2026-01-25",
    )
    caso("register_payment() final salda la deuda (settled=True)", True, res_pago_2.data["settled"])
    caso("get() refleja estado 'saldada' tras saldar", "saldada", svc.get(debt_id)["estado"])

    caso_excepcion(
        "register_payment() sobre una deuda ya saldada sigue lanzando DebtAlreadySettledError",
        DebtAlreadySettledError,
        lambda: svc.register_payment(debt_id=debt_id, amount=1.0, currency_code="ARS", date_str="2026-01-26"),
    )

    print("\n--- update() — FIX DEL BUG: concept y notes ya no se pisan ---")
    resultado_2 = svc.create(
        person="Papi",
        debt_type="en_contra",
        amount=1000.0,
        currency_code="ARS",
        date_str="2026-02-01",
        concept="Concepto original",
        notes="Notas originales",
    )
    debt_id_2 = resultado_2.debt_id

    res_update = svc.update(debt_id_2, concept="Concepto ACTUALIZADO", notes="Notas ACTUALIZADAS")
    caso("update(concept=X, notes=Y) devuelve success=True", True, res_update.success)

    fila_2 = svc.get(debt_id_2)
    caso(
        "update(concept=X, notes=Y): concepto queda X — no pisado por Y",
        "Concepto ACTUALIZADO",
        fila_2["concepto"],
    )
    caso(
        "update(concept=X, notes=Y): notas queda Y — no pisado por X",
        "Notas ACTUALIZADAS",
        fila_2["notas"],
    )
    caso(
        "concept y notes son valores DISTINTOS entre sí (prueba real de que no colapsan a uno solo)",
        True,
        fila_2["concepto"] != fila_2["notas"],
    )

    print("\n--- update() — solo un campo, el otro no se toca ---")
    svc.update(debt_id_2, person="Papi Nuevo")
    fila_3 = svc.get(debt_id_2)
    caso("update(person=...) solo: person cambia", "Papi Nuevo", fila_3["entidad_persona"])
    caso("update(person=...) solo: concepto mantiene su valor previo", "Concepto ACTUALIZADO", fila_3["concepto"])
    caso("update(person=...) solo: notas mantiene su valor previo", "Notas ACTUALIZADAS", fila_3["notas"])

    print("\n--- update() — validaciones de negocio intactas ---")
    caso_excepcion(
        "update() sobre id inexistente sigue lanzando DebtNotFoundError",
        DebtNotFoundError,
        lambda: svc.update(999999, person="X"),
    )
    caso_excepcion(
        "update() con person vacío sigue lanzando DebtError",
        DebtError,
        lambda: svc.update(debt_id_2, person="   "),
    )

    print("\n--- write_off() ---")
    resultado_3 = svc.create(
        person="Vecino",
        debt_type="a_favor",
        amount=500.0,
        currency_code="ARS",
        date_str="2026-03-01",
        concept="Préstamo herramienta",
    )
    debt_id_3 = resultado_3.debt_id

    res_write_off = svc.write_off(debt_id_3, notes="Se perdió contacto, se da de baja")
    caso("write_off() devuelve success=True", True, res_write_off.success)
    fila_write_off = svc.get(debt_id_3)
    caso("write_off() deja estado 'incobrable'", "incobrable", fila_write_off["estado"])

    caso_excepcion(
        "write_off() sobre una deuda ya incobrable sigue lanzando DebtAlreadySettledError",
        DebtAlreadySettledError,
        lambda: svc.write_off(debt_id_3, notes="segundo intento"),
    )
    caso_excepcion(
        "update() sobre una deuda ya incobrable sigue lanzando DebtAlreadySettledError",
        DebtAlreadySettledError,
        lambda: svc.update(debt_id_3, person="Otro"),
    )
    caso_excepcion(
        "write_off() sobre id inexistente sigue lanzando DebtNotFoundError",
        DebtNotFoundError,
        lambda: svc.write_off(999999),
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
