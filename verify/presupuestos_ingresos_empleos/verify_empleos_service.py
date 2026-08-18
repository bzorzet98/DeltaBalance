"""
verify/presupuestos_ingresos_empleos/verify_empleos_service.py

Verifica EmpleosService (services/empleos_service.py) — creado desde cero
en Fase 2, bloque EMPLEOS paso 2c, sin comportamiento previo que replicar.
Cubre create_employment() (defaults de columna del repositorio
confirmados, no hardcodeados de nuevo en el service), get_employment()/
list_employments(), create_receipt() sin cuenta_id (recibo suelto),
create_receipt() con cuenta_id (atomicidad REAL probada con rollback
simulado vía monkeypatch — ver más abajo), las validaciones de
create_receipt() (cuenta_id sin categoria_id, recibo duplicado, neto
negativo), y schedule_discount()/list_pending_discounts()/apply_discount()
incluyendo el bloqueo de un descuento ya aplicado.

Sobre el rollback simulado de create_receipt(): a diferencia de los verify
de repositorio (que abren su propia transacción y raisean a mano), acá se
prueba la atomicidad REAL del método público del service. Se monkeypatchea
temporalmente EmpleosRepository.crear_recibo() para que falle DESPUÉS de
que create_receipt() ya insertó la transacción de sueldo (dentro del mismo
`with self._db.transaction():`), y se confirma que ni la transacción ni el
recibo quedan persistidos — no es una simulación aislada, es el código real
del service fallando y recuperándose solo.

Correlo con:
    python verify/presupuestos_ingresos_empleos/verify_empleos_service.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.empleos_repository import EmpleosRepository
from services.empleos_service import (
    EmpleosService,
    EmpleoNotFoundError,
    ReciboNotFoundError,
    DescuentoNotFoundError,
    RecibosDuplicadoError,
    DescuentoYaAplicadoError,
    CurrencyNotFoundError,
    AccountNotFoundError,
    CategoryNotFoundError,
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
    svc = EmpleosService(manager)

    cuentas_repo = CuentasRepository(manager)
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cuenta_id = cuentas_repo.crear(nombre="Cuenta Sueldo Service", tipo="debito", moneda_codigo="ARS")
    categoria_sueldo = manager.fetchone(
        "SELECT id FROM categorias WHERE subcategoria = 'Sueldo';"
    )["id"]

    print("--- create_employment() — defaults de columna del repositorio, sin pasar porcentajes ---")
    res_empleo_1 = svc.create_employment(nombre_empresa="Acme SA", puesto="Backend", moneda_id=moneda_ars)
    caso("create_employment() devuelve success=True", True, res_empleo_1.success)
    empleo_1 = res_empleo_1.entity_id

    fila_empleo_1 = manager.fetchone("SELECT * FROM empleos WHERE id = ?;", (empleo_1,))
    caso("create_employment() sin porcentajes: usa el default del repositorio (1100)", 1100, fila_empleo_1["porcentaje_jubilacion"])
    caso("create_employment() sin porcentajes: usa el default del repositorio (300)", 300, fila_empleo_1["porcentaje_obra_social"])
    caso("create_employment() sin porcentajes: usa el default del repositorio (0 gremio)", 0, fila_empleo_1["porcentaje_gremio"])
    caso("create_employment() sin porcentajes: usa el default del repositorio (0 tope copago)", 0, fila_empleo_1["tope_copago_os_minor"])

    print("\n--- create_employment() — con porcentajes custom ---")
    res_empleo_2 = svc.create_employment(
        nombre_empresa="Beta SRL", moneda_id=moneda_ars,
        porcentaje_jubilacion=1200, porcentaje_obra_social=350, porcentaje_gremio=100, tope_copago_os_minor=5000,
    )
    empleo_2 = res_empleo_2.entity_id
    fila_empleo_2 = manager.fetchone("SELECT * FROM empleos WHERE id = ?;", (empleo_2,))
    caso("create_employment() con porcentajes custom: se respetan (no el default)", 1200, fila_empleo_2["porcentaje_jubilacion"])

    caso_excepcion(
        "create_employment() con moneda_id inexistente lanza CurrencyNotFoundError",
        CurrencyNotFoundError,
        lambda: svc.create_employment(nombre_empresa="X", moneda_id=999999),
    )

    print("\n--- get_employment() / list_employments() ---")
    caso("get_employment() encuentra el empleo 1", "Acme SA", svc.get_employment(empleo_1)["nombre_empresa"])
    caso("get_employment() de un id inexistente devuelve None", None, svc.get_employment(999999))
    ids_activos = [e["id"] for e in svc.list_employments(solo_activos=True)]
    caso("list_employments() incluye ambos empleos (activos por default)", True, empleo_1 in ids_activos and empleo_2 in ids_activos)

    print("\n--- create_receipt() SIN cuenta_id — recibo suelto ---")
    res_recibo_1 = svc.create_receipt(
        empleo_id=empleo_1, mes=1, anio=2026,
        sueldo_bruto_minor=100000000, desc_jubilacion_minor=11000000, desc_obra_social_minor=3000000,
    )
    caso("create_receipt() sin cuenta_id devuelve success=True", True, res_recibo_1.success)
    caso("create_receipt() sin cuenta_id calcula el neto correcto", 86000000, res_recibo_1.data["monto_neto_final_minor"])
    caso("create_receipt() sin cuenta_id deja transaccion_id=None", None, res_recibo_1.data["transaccion_id"])

    fila_recibo_1 = manager.fetchone("SELECT * FROM recibos_sueldo WHERE id = ?;", (res_recibo_1.entity_id,))
    caso("create_receipt() sin cuenta_id persiste transaccion_id NULL en la fila", None, fila_recibo_1["transaccion_id"])

    print("\n--- create_receipt() — validaciones de negocio ---")
    caso_excepcion(
        "create_receipt() sobre empleo_id inexistente lanza EmpleoNotFoundError",
        EmpleoNotFoundError,
        lambda: svc.create_receipt(empleo_id=999999, mes=1, anio=2026, sueldo_bruto_minor=100, desc_jubilacion_minor=0, desc_obra_social_minor=0),
    )
    caso_excepcion(
        "create_receipt() duplicado sobre el mismo período lanza RecibosDuplicadoError",
        RecibosDuplicadoError,
        lambda: svc.create_receipt(empleo_id=empleo_1, mes=1, anio=2026, sueldo_bruto_minor=100, desc_jubilacion_minor=0, desc_obra_social_minor=0),
    )
    caso_excepcion(
        "create_receipt() con descuentos que dejan el neto en negativo lanza ValueError",
        ValueError,
        lambda: svc.create_receipt(
            empleo_id=empleo_1, mes=2, anio=2026,
            sueldo_bruto_minor=100000, desc_jubilacion_minor=50000, desc_obra_social_minor=60000,
        ),
    )
    caso_excepcion(
        "create_receipt() con cuenta_id pero SIN categoria_id lanza ValueError",
        ValueError,
        lambda: svc.create_receipt(
            empleo_id=empleo_1, mes=3, anio=2026,
            sueldo_bruto_minor=100000000, desc_jubilacion_minor=11000000, desc_obra_social_minor=3000000,
            cuenta_id=cuenta_id,
        ),
    )
    caso_excepcion(
        "create_receipt() con cuenta_id inexistente lanza AccountNotFoundError",
        AccountNotFoundError,
        lambda: svc.create_receipt(
            empleo_id=empleo_1, mes=3, anio=2026,
            sueldo_bruto_minor=100000000, desc_jubilacion_minor=11000000, desc_obra_social_minor=3000000,
            cuenta_id=999999, categoria_id=categoria_sueldo,
        ),
    )
    caso_excepcion(
        "create_receipt() con categoria_id inexistente lanza CategoryNotFoundError",
        CategoryNotFoundError,
        lambda: svc.create_receipt(
            empleo_id=empleo_1, mes=3, anio=2026,
            sueldo_bruto_minor=100000000, desc_jubilacion_minor=11000000, desc_obra_social_minor=3000000,
            cuenta_id=cuenta_id, categoria_id=999999,
        ),
    )

    print("\n--- create_receipt() CON cuenta_id — atomicidad real (camino exitoso) ---")
    res_recibo_2 = svc.create_receipt(
        empleo_id=empleo_1, mes=3, anio=2026,
        sueldo_bruto_minor=100000000, desc_jubilacion_minor=11000000, desc_obra_social_minor=3000000,
        cuenta_id=cuenta_id, categoria_id=categoria_sueldo,
    )
    caso("create_receipt() con cuenta_id devuelve success=True", True, res_recibo_2.success)
    caso("create_receipt() con cuenta_id devuelve un transaccion_id numérico", True, isinstance(res_recibo_2.data["transaccion_id"], int))

    fila_transaccion = manager.fetchone("SELECT * FROM transacciones WHERE id = ?;", (res_recibo_2.data["transaccion_id"],))
    caso("la transacción creada tiene tipo_movimiento='ingreso'", "ingreso", fila_transaccion["tipo_movimiento"])
    caso("la transacción creada tiene el monto neto del recibo", 86000000, fila_transaccion["monto_minor"])
    caso("la transacción creada usa la categoria_id pasada", categoria_sueldo, fila_transaccion["categoria_id"])

    fila_recibo_2 = manager.fetchone("SELECT * FROM recibos_sueldo WHERE id = ?;", (res_recibo_2.entity_id,))
    caso("el recibo creado queda vinculado a la transacción", res_recibo_2.data["transaccion_id"], fila_recibo_2["transaccion_id"])

    print("\n--- create_receipt() CON cuenta_id — atomicidad REAL con rollback simulado ---")
    transacciones_antes = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    recibos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM recibos_sueldo;")["n"]

    metodo_original = EmpleosRepository.crear_recibo

    def crear_recibo_que_falla(self, *args, **kwargs):
        raise RuntimeError("Fallo simulado en crear_recibo(), a mitad de la transacción externa")

    EmpleosRepository.crear_recibo = crear_recibo_que_falla
    try:
        caso_excepcion(
            "create_receipt() con cuenta_id propaga el fallo simulado de crear_recibo()",
            RuntimeError,
            lambda: svc.create_receipt(
                empleo_id=empleo_1, mes=4, anio=2026,
                sueldo_bruto_minor=100000000, desc_jubilacion_minor=11000000, desc_obra_social_minor=3000000,
                cuenta_id=cuenta_id, categoria_id=categoria_sueldo,
            ),
        )
    finally:
        EmpleosRepository.crear_recibo = metodo_original

    transacciones_despues = manager.fetchone("SELECT COUNT(*) AS n FROM transacciones;")["n"]
    recibos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM recibos_sueldo;")["n"]
    caso("rollback revierte el INSERT de la transacción de sueldo (no queda huérfana)", transacciones_antes, transacciones_despues)
    caso("rollback revierte el INSERT del recibo (ninguno de los dos persiste)", recibos_antes, recibos_despues)
    caso(
        "tras el rollback, no existe ningún recibo para empleo_1 en el período 4/2026",
        None,
        svc.get_receipt(empleo_1, 4, 2026),
    )

    print("\n--- get_receipt() ---")
    caso("get_receipt() encuentra el recibo 2", res_recibo_2.entity_id, svc.get_receipt(empleo_1, 3, 2026)["id"])
    caso("get_receipt() de un período sin recibo devuelve None", None, svc.get_receipt(empleo_2, 1, 2026))

    print("\n--- schedule_discount() ---")
    res_desc_1 = svc.schedule_discount(empleo_id=empleo_1, concepto="Anticipo", monto_minor=500000, mes_aplicacion=1, anio_aplicacion=2026)
    caso("schedule_discount() devuelve success=True", True, res_desc_1.success)
    desc_1 = res_desc_1.entity_id

    caso_excepcion(
        "schedule_discount() sobre empleo_id inexistente lanza EmpleoNotFoundError",
        EmpleoNotFoundError,
        lambda: svc.schedule_discount(empleo_id=999999, concepto="X", monto_minor=100, mes_aplicacion=1, anio_aplicacion=2026),
    )
    caso_excepcion(
        "schedule_discount() con monto_minor<=0 lanza ValueError",
        ValueError,
        lambda: svc.schedule_discount(empleo_id=empleo_1, concepto="X", monto_minor=0, mes_aplicacion=1, anio_aplicacion=2026),
    )

    print("\n--- list_pending_discounts() ---")
    pendientes = svc.list_pending_discounts(1, 2026)
    caso("list_pending_discounts(1, 2026) incluye el descuento recién creado", True, desc_1 in [d["id"] for d in pendientes])

    print("\n--- apply_discount() — camino feliz ---")
    res_apply = svc.apply_discount(descuento_id=desc_1, recibo_id=res_recibo_1.entity_id)
    caso("apply_discount() devuelve success=True", True, res_apply.success)
    fila_desc_1 = manager.fetchone("SELECT * FROM descuentos_programados WHERE id = ?;", (desc_1,))
    caso("apply_discount() deja estado='aplicado'", "aplicado", fila_desc_1["estado"])
    caso("apply_discount() vincula recibo_id", res_recibo_1.entity_id, fila_desc_1["recibo_id"])

    pendientes_tras_aplicar = svc.list_pending_discounts(1, 2026)
    caso("el descuento aplicado ya no aparece en list_pending_discounts()", False, desc_1 in [d["id"] for d in pendientes_tras_aplicar])

    print("\n--- apply_discount() — validaciones de negocio ---")
    caso_excepcion(
        "apply_discount() sobre un descuento ya aplicado lanza DescuentoYaAplicadoError",
        DescuentoYaAplicadoError,
        lambda: svc.apply_discount(descuento_id=desc_1, recibo_id=res_recibo_1.entity_id),
    )
    caso_excepcion(
        "apply_discount() sobre descuento_id inexistente lanza DescuentoNotFoundError",
        DescuentoNotFoundError,
        lambda: svc.apply_discount(descuento_id=999999, recibo_id=res_recibo_1.entity_id),
    )
    res_desc_2 = svc.schedule_discount(empleo_id=empleo_1, concepto="Otro descuento", monto_minor=1000, mes_aplicacion=1, anio_aplicacion=2026)
    caso_excepcion(
        "apply_discount() sobre recibo_id inexistente lanza ReciboNotFoundError",
        ReciboNotFoundError,
        lambda: svc.apply_discount(descuento_id=res_desc_2.entity_id, recibo_id=999999),
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
