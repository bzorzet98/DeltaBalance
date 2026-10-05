"""
verify/ahorros/verify_editar_instrumento.py

Verifica SavingsService.update_activo() (docs/DATA_MODEL_DECISIONS.md
sección 35):

1. Editar nombre, broker, cuenta, comisiones y moneda (sin movimientos) se
   guarda; el tipo no cambia nunca (no es un parámetro).
2. None desvincula el broker / la cuenta; lo que no se pasa no cambia; sin
   nada que cambiar, "SIN CAMBIOS.".
3. Con movimientos, la moneda NO se cambia (decisión del usuario); pedir la
   misma moneda que ya tiene no es un cambio, y lo demás se sigue editando.
4. Validaciones: nombre vacío, broker / cuenta / moneda / activo
   inexistentes, comisión negativa.

Correlo con:
    python verify/ahorros/verify_editar_instrumento.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.savings_service import (
    AccountNotFoundError,
    ActivoNotFoundError,
    BrokerNotFoundError,
    SavingsError,
    SavingsService,
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
        except tipo_esperado as e:
            casos_ok += 1
            print(f"✅ {descripcion} — lanzó {tipo_esperado.__name__}: {e}")
        except Exception as e:
            print(f"❌ {descripcion} — esperaba {tipo_esperado.__name__}, se lanzó {type(e).__name__}: {e!r}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    svc = SavingsService(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    brokers = {b["nombre"]: b["id"] for b in svc.get_brokers()}
    cuentas = CuentasRepository(manager)
    cuenta_a = cuentas.crear(nombre="COCOS", tipo="debito", moneda_codigo="ARS")
    cuenta_b = cuentas.crear(nombre="BANCO", tipo="debito", moneda_codigo="ARS")

    def fila(activo_id: str):
        return manager.fetchone("SELECT * FROM activos_financieros WHERE id = ?;", (activo_id,))

    def campos(activo_id: str) -> tuple:
        f = fila(activo_id)
        return (f["nombre"], f["tipo"], f["broker_id"], f["cuenta_id"], f["moneda_id"],
                f["comision_compra_minor"], f["comision_venta_minor"])

    activo = svc.create_activo(
        nombre="COCOS AHORRO", tipo="fci", moneda_id=ars, cuenta_id=cuenta_a, broker_id=brokers["COCOS"],
    ).entity_id

    print("--- 1. Editar todo (sin movimientos) ---")
    resultado = svc.update_activo(
        activo, nombre="  COCOS PLUS  ", broker_id=brokers["BULL MARKET"], moneda_id=usd, cuenta_id=cuenta_b,
        comision_compra_minor=100, comision_venta_minor=250,
    )
    caso("update_activo(): success", True, resultado.success)
    caso("nombre (sin espacios), broker, cuenta, moneda y comisiones nuevos; el tipo sigue siendo fci",
         ("COCOS PLUS", "fci", brokers["BULL MARKET"], cuenta_b, usd, 100, 250), campos(activo))

    print("\n--- 2. Desvincular, no tocar, sin cambios ---")
    svc.update_activo(activo, broker_id=None, cuenta_id=None)
    caso("broker y cuenta en None: desvinculados", (None, None), (fila(activo)["broker_id"], fila(activo)["cuenta_id"]))
    svc.update_activo(activo, nombre="COCOS DÓLAR")
    caso("solo el nombre: lo demás queda como estaba",
         ("COCOS DÓLAR", "fci", None, None, usd, 100, 250), campos(activo))
    caso("sin nada que cambiar: SIN CAMBIOS.", "SIN CAMBIOS.", svc.update_activo(activo).message)

    print("\n--- 3. Con movimientos la moneda no se cambia ---")
    svc.registrar_aporte(activo, 100000, "2026-01-05", crear_transaccion=False)
    caso_excepcion("cambiar la moneda con un movimiento → SavingsError", SavingsError,
                   lambda: svc.update_activo(activo, moneda_id=ars))
    caso("tras el rechazo sigue en USD", usd, fila(activo)["moneda_id"])
    svc.update_activo(activo, nombre="COCOS USD", moneda_id=usd)
    caso("la misma moneda no es un cambio: el nombre se edita igual", ("COCOS USD", usd),
         (fila(activo)["nombre"], fila(activo)["moneda_id"]))
    svc.update_activo(activo, broker_id=brokers["COCOS"], cuenta_id=cuenta_a)
    caso("broker y cuenta se siguen editando con movimientos", (brokers["COCOS"], cuenta_a),
         (fila(activo)["broker_id"], fila(activo)["cuenta_id"]))

    print("\n--- 4. Validaciones (no se escribe nada) ---")
    antes = campos(activo)
    caso_excepcion("nombre vacío → SavingsError", SavingsError, lambda: svc.update_activo(activo, nombre="   "))
    caso_excepcion("broker inexistente → BrokerNotFoundError", BrokerNotFoundError,
                   lambda: svc.update_activo(activo, broker_id="no-existe"))
    caso_excepcion("cuenta inexistente → AccountNotFoundError", AccountNotFoundError,
                   lambda: svc.update_activo(activo, cuenta_id="no-existe"))
    caso_excepcion("comisión negativa → SavingsError", SavingsError,
                   lambda: svc.update_activo(activo, comision_compra_minor=-1))
    caso("tras los rechazos, nada cambió", antes, campos(activo))
    otro = svc.create_activo(nombre="SIN MOVIMIENTOS", tipo="plazo_flex", moneda_id=ars).entity_id
    caso_excepcion("moneda inexistente → SavingsError", SavingsError, lambda: svc.update_activo(otro, moneda_id=999999))
    caso_excepcion("activo inexistente → ActivoNotFoundError", ActivoNotFoundError,
                   lambda: svc.update_activo("no-existe", nombre="X"))
    caso("el tipo del otro tampoco cambió", "plazo_flex", fila(otro)["tipo"])

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(La DB temporal quedó en {db_path} — no es data/deltabalance.db.)")


if __name__ == "__main__":
    main()
