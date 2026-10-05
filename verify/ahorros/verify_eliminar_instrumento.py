"""
verify/ahorros/verify_eliminar_instrumento.py

Verifica eliminar / ocultar / reactivar un instrumento de ahorro
(services/savings_service.py; docs/DATA_MODEL_DECISIONS.md sección 35,
CLAUDE.md §4):

1. Sin movimientos → delete_activo() lo borra del todo (también sus filas
   del reparto deprecated activo_objetivos, que la FK exige borrar antes).
2. Con historial y saldo 0 → delete_activo() lo RECHAZA (tiene
   dependencias); ocultar_activo() lo esconde sin borrar nada:
   get_resumen_por_tipo() ya no lo trae, con incluir_ocultos=True sí
   (activa=False), y list_movimientos() marca sus movimientos;
   reactivar_activo() lo vuelve a mostrar.
3. Con saldo → ni eliminar ni ocultar.
4. Acciones / CEDEARs: manda la CANTIDAD — 0 unidades se puede ocultar
   aunque el saldo en plata no sea 0 (vendió más caro de lo que compró);
   con unidades, no.
5. Los datos de la tarjeta (movimientos, con_tenencia) y un id inexistente.

Correlo con:
    python verify/ahorros/verify_eliminar_instrumento.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.activo_objetivos_repository import ActivoObjetivosRepository
from services.savings_service import ActivoNotFoundError, SavingsError, SavingsService


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

    def existe(activo_id: str) -> bool:
        return manager.fetchone("SELECT 1 AS si FROM activos_financieros WHERE id = ?;", (activo_id,)) is not None

    def activa(activo_id: str) -> int:
        return manager.fetchone("SELECT activa FROM activos_financieros WHERE id = ?;", (activo_id,))["activa"]

    def entrada(activo_id: str, incluir_ocultos: bool = True):
        resumen = svc.get_resumen_por_tipo(incluir_ocultos=incluir_ocultos)
        return next((e for grupo in resumen.values() for e in grupo if e["activo_id"] == activo_id), None)

    def fci(nombre: str) -> str:
        return svc.create_activo(nombre=nombre, tipo="fci", moneda_id=ars).entity_id

    print("--- 1. Sin movimientos: se elimina ---")
    vacio = fci("FCI SIN MOVIMIENTOS")
    datos = entrada(vacio)
    caso("tarjeta: 0 movimientos y sin tenencia (la UI ofrece ELIMINAR)", (0, False), (datos["movimientos"], datos["con_tenencia"]))
    resultado = svc.delete_activo(vacio)
    caso("delete_activo(): success", True, resultado.success)
    caso("el instrumento ya no existe", False, existe(vacio))

    con_reparto = fci("FCI CON REPARTO VIEJO")
    objetivo = svc.create_objetivo(nombre="MOTO").entity_id
    ActivoObjetivosRepository(manager).crear(con_reparto, objetivo, 50.0)
    svc.delete_activo(con_reparto)
    caso("con una fila en activo_objetivos (deprecated): se elimina igual", False, existe(con_reparto))
    caso("su fila de activo_objetivos también se borró", 0, manager.fetchone(
        "SELECT COUNT(*) AS n FROM activo_objetivos WHERE activo_id = ?;", (con_reparto,),
    )["n"])

    print("\n--- 2. Con historial y saldo 0: no se elimina, se oculta ---")
    historial = fci("FCI EN CERO")
    svc.registrar_aporte(historial, 10000000, "2026-01-05", crear_transaccion=False)
    svc.registrar_retiro(historial, 10000000, "2026-02-05", crear_transaccion=False)
    datos = entrada(historial)
    caso("tarjeta: 2 movimientos y sin tenencia (la UI ofrece OCULTAR)", (2, False), (datos["movimientos"], datos["con_tenencia"]))
    caso_excepcion("delete_activo() con historial → SavingsError", SavingsError, lambda: svc.delete_activo(historial))
    caso("tras el rechazo sigue existiendo, con sus 2 movimientos", (True, 2),
         (existe(historial), len(svc.list_movimientos(activo_id=historial))))

    resultado = svc.ocultar_activo(historial)
    caso("ocultar_activo(): success", True, resultado.success)
    caso("activa = 0", 0, activa(historial))
    caso("get_resumen_por_tipo() ya no lo trae", None, entrada(historial, incluir_ocultos=False))
    caso("con incluir_ocultos=True sí, con activa=False", False, entrada(historial)["activa"])
    caso("sus movimientos siguen ahí, marcados como de un oculto", [0, 0],
         [m["activo_activa"] for m in svc.list_movimientos(activo_id=historial)])

    svc.reactivar_activo(historial)
    caso("reactivar_activo(): activa = 1", 1, activa(historial))
    caso("vuelve a get_resumen_por_tipo()", True, entrada(historial, incluir_ocultos=False) is not None)

    print("\n--- 3. Con saldo: ni eliminar ni ocultar ---")
    con_saldo = fci("FCI CON SALDO")
    svc.registrar_aporte(con_saldo, 5000000, "2026-01-05", crear_transaccion=False)
    caso("tarjeta: con tenencia (la UI no ofrece nada)", True, entrada(con_saldo)["con_tenencia"])
    caso_excepcion("delete_activo() → SavingsError", SavingsError, lambda: svc.delete_activo(con_saldo))
    caso_excepcion("ocultar_activo() → SavingsError", SavingsError, lambda: svc.ocultar_activo(con_saldo))
    caso("sigue existiendo y visible", (True, 1), (existe(con_saldo), activa(con_saldo)))

    print("\n--- 4. CEDEARs: manda la cantidad ---")
    vendido = svc.create_activo(nombre="CEDEAR VENDIDO", tipo="cedear", moneda_id=ars).entity_id
    svc.registrar_compra(vendido, 10, 1000000, 0, "2026-01-05", crear_transaccion=False)
    svc.registrar_venta(vendido, 10, 1500000, 0, "2026-03-05", crear_transaccion=False)
    datos = entrada(vendido)
    caso("0 unidades aunque el saldo en plata dé −5.000 (vendió más caro)", (0, -500000, False),
         (datos["unidades"], datos["saldo_minor"], datos["con_tenencia"]))
    svc.ocultar_activo(vendido)
    caso("0 unidades: se oculta", 0, activa(vendido))

    con_unidades = svc.create_activo(nombre="CEDEAR CON UNIDADES", tipo="cedear", moneda_id=ars).entity_id
    svc.registrar_compra(con_unidades, 5, 500000, 0, "2026-01-05", crear_transaccion=False)
    caso_excepcion("con 5 unidades: ocultar_activo() → SavingsError", SavingsError,
                   lambda: svc.ocultar_activo(con_unidades))

    print("\n--- 5. Id inexistente ---")
    caso_excepcion("delete_activo() → ActivoNotFoundError", ActivoNotFoundError, lambda: svc.delete_activo("no-existe"))
    caso_excepcion("ocultar_activo() → ActivoNotFoundError", ActivoNotFoundError, lambda: svc.ocultar_activo("no-existe"))
    caso_excepcion("reactivar_activo() → ActivoNotFoundError", ActivoNotFoundError,
                   lambda: svc.reactivar_activo("no-existe"))

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(La DB temporal quedó en {db_path} — no es data/deltabalance.db.)")


if __name__ == "__main__":
    main()
