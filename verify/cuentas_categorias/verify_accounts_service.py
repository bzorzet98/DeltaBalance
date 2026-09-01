"""
verify/cuentas_categorias/verify_accounts_service.py

Verifica AccountsService (services/accounts_service.py). Cubre
create_account() multi-moneda (una y dos monedas, atómico; lista vacía o
sin pasar el argumento — Tarea 6f: crea la cuenta sin ningún saldo_inicial,
sin error; ids repetidos; moneda inexistente; cuenta_pago_id inexistente),
add_currency_to_account() (camino feliz y el bloqueo de duplicado),
list_currencies(), get_account()/list_accounts() con el shape de lista de
saldos, update_account() con el sentinel NO_CAMBIAR, el bloqueo de
auto-referencia y de cambiar tipo/monedas, archive_account() (bloqueado si
hay saldo distinto de 0 en cualquier moneda, permitido si todas están en
0), delete_account() bloqueado en cada una de las tres condiciones
(transacciones asociadas — probado tanto con una activa como con una
soft-deleted, para confirmar que ambas cuentan como "tuvo actividad";
saldo inicial != 0; es cuenta_pago_id de otra cuenta) y permitido cuando
ninguna aplica, get_total_balance() con cuentas en dos monedas distintas
confirmando que no se mezclan, y color_hex (default de columna al no
pasarlo en create_account(), valor explícito, validación de formato
'#RRGGBB' en create_account()/update_account(), y actualización).

Correlo con:
    python verify/cuentas_categorias/verify_accounts_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from services.accounts_service import (
    AccountsService,
    AccountNotFoundError,
    CurrencyNotFoundError,
    ParentAccountNotFoundError,
    AccountsError,
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
    svc = AccountsService(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    moneda_usdt = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USDT';")["id"]
    categoria_ingreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'ingreso' LIMIT 1;")["id"]

    print("--- list_currencies() ---")
    codigos = [m["codigo"] for m in svc.list_currencies()]
    caso("list_currencies() incluye ARS y USD (seed.sql)", True, "ARS" in codigos and "USD" in codigos)

    print("\n--- create_account() — camino feliz, una sola moneda ---")
    resultado = svc.create_account(nombre="Banco Galicia - Caja de ahorro", tipo="debito", monedas=[moneda_ars])
    caso("create_account() devuelve success=True", True, resultado.success)
    caso("create_account() devuelve un account_id numérico", True, isinstance(resultado.account_id, int))
    cuenta_debito = resultado.account_id
    caso("create_account() persiste el nombre", "Banco Galicia - Caja de ahorro", resultado.data["nombre"])
    caso("create_account() nace activa", 1, resultado.data["activa"])
    caso("create_account() con 1 moneda: data['saldos'] tiene 1 entrada", 1, len(resultado.data["saldos"]))
    caso("create_account() arranca con saldo 0 en esa moneda", 0.0, resultado.data["saldos"][0]["saldo"])
    caso("create_account() resuelve moneda_codigo de esa entrada", "ARS", resultado.data["saldos"][0]["moneda_codigo"])
    caso("create_account() sin color_hex usa el default de columna", "#5F5E5A", resultado.data["color_hex"])

    print("\n--- create_account() — color_hex explícito ---")
    resultado_color = svc.create_account(
        nombre="Cuenta con color", tipo="efectivo", monedas=[moneda_ars], color_hex="#1E88E5",
    )
    caso("create_account(color_hex=...) lo persiste tal cual", "#1E88E5", resultado_color.data["color_hex"])
    caso_excepcion(
        "create_account() con color_hex mal formado (sin '#') lanza ValueError",
        ValueError,
        lambda: svc.create_account(nombre="Cuenta color feo 1", tipo="efectivo", monedas=[moneda_ars], color_hex="1E88E5"),
    )
    caso_excepcion(
        "create_account() con color_hex mal formado (largo incorrecto) lanza ValueError",
        ValueError,
        lambda: svc.create_account(nombre="Cuenta color feo 2", tipo="efectivo", monedas=[moneda_ars], color_hex="#1E88"),
    )
    caso_excepcion(
        "create_account() con color_hex mal formado (caracteres inválidos) lanza ValueError",
        ValueError,
        lambda: svc.create_account(nombre="Cuenta color feo 3", tipo="efectivo", monedas=[moneda_ars], color_hex="#GGGGGG"),
    )

    print("\n--- create_account() — camino feliz, dos monedas (atómico) ---")
    resultado_multi = svc.create_account(
        nombre="Galicia Visa", tipo="credito", monedas=[moneda_ars, moneda_usd], cuenta_pago_id=cuenta_debito,
    )
    caso("create_account() con 2 monedas devuelve success=True", True, resultado_multi.success)
    caso("create_account() con 2 monedas: data['saldos'] tiene 2 entradas", 2, len(resultado_multi.data["saldos"]))
    codigos_creados = sorted(s["moneda_codigo"] for s in resultado_multi.data["saldos"])
    caso("create_account() con 2 monedas: ambas quedaron creadas (ARS y USD)", ["ARS", "USD"], codigos_creados)
    caso("create_account() con cuenta_pago_id lo persiste", cuenta_debito, resultado_multi.data["cuenta_pago_id"])
    cuenta_credito = resultado_multi.account_id

    print("\n--- create_account() — validaciones de negocio ---")
    caso_excepcion(
        "create_account() con nombre vacío lanza AccountsError",
        AccountsError,
        lambda: svc.create_account(nombre="   ", tipo="debito", monedas=[moneda_ars]),
    )
    caso_excepcion(
        "create_account() con tipo inválido lanza ValueError",
        ValueError,
        lambda: svc.create_account(nombre="Cuenta rara", tipo="no_existe", monedas=[moneda_ars]),
    )
    resultado_sin_moneda = svc.create_account(nombre="Cuenta sin moneda", tipo="debito", monedas=[])
    caso(
        "create_account() con lista de monedas vacía ya NO lanza error: success=True (Tarea 6f)",
        True,
        resultado_sin_moneda.success,
    )
    caso(
        "create_account() con monedas=[] crea la cuenta sin ningún saldo_inicial todavía",
        0,
        len(resultado_sin_moneda.data["saldos"]),
    )
    resultado_sin_moneda_default = svc.create_account(nombre="Cuenta sin moneda (default)", tipo="debito")
    caso(
        "create_account() sin pasar monedas en absoluto (default None) también crea la cuenta sin saldos",
        0,
        len(resultado_sin_moneda_default.data["saldos"]),
    )
    caso_excepcion(
        "create_account() con monedas repetidas lanza ValueError",
        ValueError,
        lambda: svc.create_account(nombre="Cuenta duplicada", tipo="debito", monedas=[moneda_ars, moneda_ars]),
    )
    caso_excepcion(
        "create_account() con moneda_id inexistente lanza CurrencyNotFoundError",
        CurrencyNotFoundError,
        lambda: svc.create_account(nombre="Cuenta X", tipo="debito", monedas=[999999]),
    )
    caso_excepcion(
        "create_account() con cuenta_pago_id inexistente lanza ParentAccountNotFoundError",
        ParentAccountNotFoundError,
        lambda: svc.create_account(nombre="Otra tarjeta", tipo="credito", monedas=[moneda_ars], cuenta_pago_id=999999),
    )
    caso(
        "create_account() con moneda_id inexistente no crea nada (falla antes de tocar la DB)",
        False,
        "Cuenta X" in [c["nombre"] for c in svc.list_accounts(solo_activas=False)],
    )
    caso_excepcion(
        "create_account() con una moneda válida y otra inexistente en la lista también lanza CurrencyNotFoundError "
        "(todas se validan antes de abrir la transacción, no solo la primera)",
        CurrencyNotFoundError,
        lambda: svc.create_account(nombre="Cuenta Y", tipo="debito", monedas=[moneda_ars, 999999]),
    )
    caso(
        "esa llamada fallida tampoco dejó la cuenta creada a medias — atómico: ninguna moneda válida quedó insertada sola",
        False,
        "Cuenta Y" in [c["nombre"] for c in svc.list_accounts(solo_activas=False)],
    )

    print("\n--- add_currency_to_account() ---")
    res_add = svc.add_currency_to_account(cuenta_debito, moneda_usdt)
    caso("add_currency_to_account() devuelve success=True", True, res_add.success)
    caso("add_currency_to_account() suma una entrada más a saldos", 2, len(res_add.data["saldos"]))
    caso_excepcion(
        "add_currency_to_account() con una moneda ya agregada lanza AccountsError",
        AccountsError,
        lambda: svc.add_currency_to_account(cuenta_debito, moneda_usdt),
    )
    caso_excepcion(
        "add_currency_to_account() sobre cuenta inexistente lanza AccountNotFoundError",
        AccountNotFoundError,
        lambda: svc.add_currency_to_account(999999, moneda_ars),
    )
    caso_excepcion(
        "add_currency_to_account() con moneda inexistente lanza CurrencyNotFoundError",
        CurrencyNotFoundError,
        lambda: svc.add_currency_to_account(cuenta_debito, 999999),
    )

    print("\n--- get_account() / list_accounts() ---")
    caso_excepcion(
        "get_account() de un id inexistente lanza AccountNotFoundError",
        AccountNotFoundError,
        lambda: svc.get_account(999999),
    )

    listado = svc.list_accounts()
    ids_listado = [c["id"] for c in listado]
    caso("list_accounts() incluye ambas cuentas recién creadas", True, cuenta_debito in ids_listado and cuenta_credito in ids_listado)
    caso("list_accounts() trae 'saldos' como lista en cada cuenta", True, all(isinstance(c["saldos"], list) for c in listado))

    print("\n--- update_account() — sentinel NO_CAMBIAR ---")
    caso_excepcion(
        "update_account() sobre id inexistente lanza AccountNotFoundError",
        AccountNotFoundError,
        lambda: svc.update_account(999999, nombre="X"),
    )

    res_update_1 = svc.update_account(cuenta_debito, notas="cuenta principal")
    caso("update_account(notas=...) devuelve success=True", True, res_update_1.success)
    fila_1 = svc.get_account(cuenta_debito)
    caso("update_account(notas=...) solo: notas cambia", "cuenta principal", fila_1["notas"])
    caso("update_account(notas=...) solo: nombre mantiene su valor previo (NO_CAMBIAR)", "Banco Galicia - Caja de ahorro", fila_1["nombre"])

    svc.update_account(cuenta_debito, nombre="Banco Galicia - Caja de ahorro (renombrada)")
    fila_1_v2 = svc.get_account(cuenta_debito)
    caso("update_account(nombre=...): cambia el nombre", "Banco Galicia - Caja de ahorro (renombrada)", fila_1_v2["nombre"])
    caso("update_account(nombre=...): notas mantiene su valor previo (NO_CAMBIAR)", "cuenta principal", fila_1_v2["notas"])

    sin_cambios = svc.update_account(cuenta_debito)
    caso("update_account() sin ningún campo devuelve success=False", False, sin_cambios.success)

    print("\n--- update_account() — color_hex ---")
    res_update_color = svc.update_account(cuenta_debito, color_hex="#43A047")
    caso("update_account(color_hex=...) devuelve success=True", True, res_update_color.success)
    fila_color = svc.get_account(cuenta_debito)
    caso("update_account(color_hex=...) lo persiste", "#43A047", fila_color["color_hex"])
    caso("update_account(color_hex=...) solo: nombre mantiene su valor previo (NO_CAMBIAR)", "Banco Galicia - Caja de ahorro (renombrada)", fila_color["nombre"])
    caso_excepcion(
        "update_account(color_hex=...) mal formado lanza ValueError",
        ValueError,
        lambda: svc.update_account(cuenta_debito, color_hex="no-es-un-color"),
    )
    caso_excepcion(
        "update_account(color_hex=None) lanza ValueError (sin estado NULL legítimo)",
        ValueError,
        lambda: svc.update_account(cuenta_debito, color_hex=None),
    )

    print("\n--- update_account() — bloqueo de auto-referencia ---")
    caso_excepcion(
        "update_account(cuenta_pago_id=<sí misma>) lanza ValueError",
        ValueError,
        lambda: svc.update_account(cuenta_credito, cuenta_pago_id=cuenta_credito),
    )
    caso_excepcion(
        "update_account(cuenta_pago_id=<inexistente>) lanza ParentAccountNotFoundError",
        ParentAccountNotFoundError,
        lambda: svc.update_account(cuenta_credito, cuenta_pago_id=999999),
    )

    print("\n--- update_account() — bloqueo de cambiar tipo/monedas ---")
    caso_excepcion(
        "update_account(tipo=...) lanza ValueError",
        ValueError,
        lambda: svc.update_account(cuenta_debito, tipo="efectivo"),
    )
    caso_excepcion(
        "update_account(monedas=...) lanza ValueError",
        ValueError,
        lambda: svc.update_account(cuenta_debito, monedas=[moneda_usd]),
    )

    print("\n--- archive_account() — bloqueado si el saldo no es 0 en alguna moneda ---")
    manager.execute(
        """
        INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        ("2026-01-15", "Ingreso de prueba", cuenta_debito, categoria_ingreso, moneda_ars, "ingreso", 50000),
    )
    caso_excepcion(
        "archive_account() con saldo != 0 en una de sus monedas lanza AccountsError",
        AccountsError,
        lambda: svc.archive_account(cuenta_debito),
    )

    print("\n--- archive_account() — permitido si el saldo es 0 en todas las monedas ---")
    res_archivo = svc.archive_account(cuenta_credito)
    caso("archive_account() con saldo 0 en todas sus monedas devuelve success=True", True, res_archivo.success)
    caso("archive_account() desaparece de list_accounts() por default", False, cuenta_credito in [c["id"] for c in svc.list_accounts()])
    caso("archive_account() sigue apareciendo con solo_activas=False", True, cuenta_credito in [c["id"] for c in svc.list_accounts(solo_activas=False)])

    print("\n--- delete_account() — bloqueado en cada uno de los tres casos ---")

    # Caso A: tiene transacciones activas (cuenta_debito, insertada arriba)
    caso_excepcion(
        "delete_account() con transacciones activas lanza AccountsError",
        AccountsError,
        lambda: svc.delete_account(cuenta_debito),
    )

    # Caso B: solo tiene transacciones soft-deleted (cuenta nueva, sin saldo, con 1 transacción borrada lógicamente)
    cuenta_soft_deleted = svc.create_account(nombre="Cuenta con transacción borrada", tipo="efectivo", monedas=[moneda_ars]).account_id
    manager.execute(
        """
        INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor, deleted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        ("2026-01-10", "Transacción luego borrada", cuenta_soft_deleted, categoria_ingreso, moneda_ars, "ingreso", 1000, "2026-01-11 00:00:00"),
    )
    caso_excepcion(
        "delete_account() con solo transacciones soft-deleted igual lanza AccountsError (tuvo actividad)",
        AccountsError,
        lambda: svc.delete_account(cuenta_soft_deleted),
    )

    # Caso C: es cuenta_pago_id de otra cuenta
    cuenta_padre_dependida = svc.create_account(nombre="Cuenta padre de una tarjeta", tipo="debito", monedas=[moneda_ars]).account_id
    svc.create_account(nombre="Tarjeta que depende de la anterior", tipo="credito", monedas=[moneda_ars], cuenta_pago_id=cuenta_padre_dependida)
    caso_excepcion(
        "delete_account() sobre una cuenta que es cuenta_pago_id de otra lanza AccountsError",
        AccountsError,
        lambda: svc.delete_account(cuenta_padre_dependida),
    )

    # Caso D: saldo inicial != 0 en cuentas_saldos, sin transacciones ni dependientes
    cuenta_con_saldo_inicial = svc.create_account(nombre="Cuenta con saldo inicial cargado", tipo="efectivo", monedas=[moneda_ars]).account_id
    manager.execute(
        "UPDATE cuentas_saldos SET saldo_inicial_minor = ? WHERE cuenta_id = ? AND moneda_id = ?;",
        (10000, cuenta_con_saldo_inicial, moneda_ars),
    )
    caso_excepcion(
        "delete_account() con saldo_inicial_minor != 0 lanza AccountsError",
        AccountsError,
        lambda: svc.delete_account(cuenta_con_saldo_inicial),
    )

    print("\n--- delete_account() — permitido cuando ninguna de las tres aplica ---")
    cuenta_borrable = svc.create_account(nombre="Cuenta creada por error", tipo="efectivo", monedas=[moneda_ars]).account_id
    res_delete = svc.delete_account(cuenta_borrable)
    caso("delete_account() sin movimientos/saldo/dependientes devuelve success=True", True, res_delete.success)
    caso_excepcion(
        "tras delete_account(), get_account() ya no la encuentra (DELETE físico)",
        AccountNotFoundError,
        lambda: svc.get_account(cuenta_borrable),
    )

    print("\n--- get_total_balance() — cuentas en dos monedas distintas, no se mezclan ---")
    # Se mide como delta antes/después en vez de un total hardcodeado: para
    # este punto del script ya hay otras cuentas activas en ARS de pasos
    # anteriores (cuenta_debito con 50000, cuenta_con_saldo_inicial con
    # 10000 vía UPDATE directo — su delete_account() falló a propósito más
    # arriba, así que sigue activa) — hardcodear el total absoluto sería
    # frágil ante cualquier cambio en los pasos previos.
    totales_antes = svc.get_total_balance()
    ars_antes = totales_antes.get("ARS", 0)
    usd_antes = totales_antes.get("USD", 0)

    cuenta_usd = svc.create_account(nombre="Cuenta USD", tipo="efectivo", monedas=[moneda_usd]).account_id
    manager.execute(
        """
        INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        ("2026-01-16", "Ingreso USD de prueba", cuenta_usd, categoria_ingreso, moneda_usd, "ingreso", 10000),
    )

    totales_despues = svc.get_total_balance()
    caso("get_total_balance() no mueve el total ARS al sumar una cuenta nueva en USD", ars_antes, totales_despues.get("ARS"))
    caso("get_total_balance() suma el nuevo saldo USD a su propia clave", usd_antes + 10000, totales_despues.get("USD"))
    caso(
        "get_total_balance() no mezcla ARS y USD bajo la misma clave (el aumento de USD no afecta a ARS)",
        True,
        totales_despues.get("ARS") == ars_antes and totales_despues.get("USD") == usd_antes + 10000,
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
