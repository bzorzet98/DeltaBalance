"""
verify/ahorros/verify_editar_eliminar_objetivos.py

Verifica la edición y eliminación de objetivos de ahorro en
services/savings_service.py (docs/DATA_MODEL_DECISIONS.md sección 32).

Escenario: un objetivo VIEJO con plata en tres instrumentos —
- un FCI: aporte 100.000 (todo VIEJO), aporte 50.000 (mitad VIEJO, mitad
  MOTO) y retiro 20.000 (todo VIEJO) → VIEJO tiene 105.000, MOTO 25.000;
- un CEDEAR: 10 unidades de VIEJO y 4 de TERRENEITOR;
- un plazo fijo ya retirado: VIEJO en cero (solo historial).

1. update_objetivo(): nombre / meta / fecha meta, y sus validaciones.
2. get_partes_de_objetivo(): los tres instrumentos, el plazo fijo "en cero".
3. delete_objetivo() con errores (reparto hacia sí mismo, suma > 100,
   destino inexistente): no cambia nada.
4. delete_objetivo() repartiendo FCI 60% MOTO / 40% AHORRO GENERAL, CEDEAR
   100% TERRENEITOR, plazo fijo sin asignar: asignaciones de cada
   movimiento, resumen por objetivo y el objetivo borrado.
5. Reparto parcial: lo que no llega al 100% queda SIN ASIGNAR.
6. Objetivo sin movimientos, y con filas en activo_objetivos (deprecated):
   se borra igual.

Correlo con:
    python verify/ahorros/verify_editar_eliminar_objetivos.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.activo_objetivos_repository import ActivoObjetivosRepository
from services.savings_service import (
    OBJETIVO_SIN_ASIGNAR,
    AsignacionInvalidaError,
    ObjetivoNotFoundError,
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
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]

    viejo = svc.create_objetivo(nombre="VIEJO").entity_id
    moto = svc.create_objetivo(nombre="MOTO").entity_id
    terreno = svc.create_objetivo(nombre="TERRENEITOR").entity_id
    general = svc.create_objetivo(nombre="AHORRO GENERAL").entity_id

    def nombre_de(objetivo_id: str) -> str:
        fila = manager.fetchone("SELECT nombre FROM objetivos_ahorro WHERE id = ?;", (objetivo_id,))
        return fila["nombre"] if fila else "(BORRADO)"

    def todo_a(objetivo_id: str, porcentaje: float = 100.0) -> list[dict]:
        return [{"objetivo_id": objetivo_id, "porcentaje": porcentaje}]

    def asignaciones(movimiento_id: str) -> list[tuple[str, float, int]]:
        """(objetivo, porcentaje, monto) del movimiento, el mayor porcentaje primero (empates por nombre)."""
        filas = manager.fetchall(
            "SELECT objetivo_id, porcentaje, monto_asignado_minor FROM asignaciones WHERE movimiento_id = ?;",
            (movimiento_id,),
        )
        tuplas = [(nombre_de(f["objetivo_id"]), f["porcentaje"], f["monto_asignado_minor"]) for f in filas]
        return sorted(tuplas, key=lambda t: (-t[1], t[0]))

    def parte(objetivo: str, activo_id: str, clave: str = "saldo_minor"):
        partes = svc.get_resumen_por_objetivo().get(objetivo, [])
        return next((p[clave] for p in partes if p["activo_id"] == activo_id), None)

    def existe(objetivo_id: str) -> bool:
        return manager.fetchone("SELECT 1 AS si FROM objetivos_ahorro WHERE id = ?;", (objetivo_id,)) is not None

    fci = svc.create_activo(nombre="FCI VERIFY", tipo="fci", moneda_id=moneda_ars).entity_id
    cedear = svc.create_activo(nombre="CEDEAR VERIFY", tipo="cedear", moneda_id=moneda_ars).entity_id
    plazo = svc.create_activo(nombre="PLAZO FIJO VERIFY", tipo="plazo_fijo", moneda_id=moneda_ars).entity_id

    a1 = svc.registrar_aporte(fci, 10000000, "2026-01-10", crear_transaccion=False, asignaciones=todo_a(viejo)).entity_id
    a2 = svc.registrar_aporte(fci, 5000000, "2026-01-11", crear_transaccion=False, asignaciones=[
        {"objetivo_id": viejo, "porcentaje": 50.0}, {"objetivo_id": moto, "porcentaje": 50.0},
    ]).entity_id
    r1 = svc.registrar_retiro(fci, 2000000, "2026-01-12", crear_transaccion=False, asignaciones=todo_a(viejo)).entity_id
    c1 = svc.registrar_compra(cedear, 10, 1000000, 0, "2026-01-10", crear_transaccion=False, asignaciones=todo_a(viejo)).entity_id
    svc.registrar_compra(cedear, 4, 400000, 0, "2026-01-10", crear_transaccion=False, asignaciones=todo_a(terreno))
    p1 = svc.registrar_aporte(plazo, 3000000, "2025-10-01", crear_transaccion=False, asignaciones=todo_a(viejo)).entity_id
    p2 = svc.registrar_retiro(plazo, 3000000, "2025-11-01", crear_transaccion=False, asignaciones=todo_a(viejo)).entity_id

    print("--- 1. update_objetivo(): nombre, meta, fecha meta ---")
    resultado = svc.update_objetivo(viejo, "  VIEJITO  ", 50000000, "2027-01-01")
    caso("update: success", True, resultado.success)
    fila = manager.fetchone("SELECT nombre, monto_meta_minor, fecha_meta FROM objetivos_ahorro WHERE id = ?;", (viejo,))
    caso("update: nombre sin espacios de más", "VIEJITO", fila["nombre"])
    caso("update: meta 500.000", 50000000, fila["monto_meta_minor"])
    caso("update: fecha meta", "2027-01-01", fila["fecha_meta"])
    svc.update_objetivo(viejo, "VIEJITO", None, None)
    fila = manager.fetchone("SELECT monto_meta_minor, fecha_meta FROM objetivos_ahorro WHERE id = ?;", (viejo,))
    caso("update con None: sin meta y sin fecha meta", (None, None), (fila["monto_meta_minor"], fila["fecha_meta"]))
    caso("las asignaciones siguen apuntando al objetivo (por id)", [("VIEJITO", 100.0, 10000000)], asignaciones(a1))
    caso_excepcion("nombre vacío → SavingsError", SavingsError, lambda: svc.update_objetivo(viejo, "   ", None, None))
    caso_excepcion("meta 0 → SavingsError", SavingsError, lambda: svc.update_objetivo(viejo, "VIEJITO", 0, None))
    caso_excepcion("fecha meta DD/MM/AAAA → SavingsError", SavingsError,
                   lambda: svc.update_objetivo(viejo, "VIEJITO", None, "01/01/2027"))
    caso_excepcion("objetivo inexistente → ObjetivoNotFoundError", ObjetivoNotFoundError,
                   lambda: svc.update_objetivo("no-existe", "X", None, None))
    caso_excepcion("create_objetivo valida igual: nombre vacío → SavingsError", SavingsError,
                   lambda: svc.create_objetivo(nombre=""))

    print("\n--- 2. get_partes_de_objetivo(): lo que hay que repartir ---")
    partes = svc.get_partes_de_objetivo(viejo)
    caso("tres instrumentos, en el orden del resumen (FCI, CEDEAR, PLAZO FIJO)",
         ["FCI VERIFY", "CEDEAR VERIFY", "PLAZO FIJO VERIFY"], [p["activo"] for p in partes])
    caso("FCI: 100.000 + 25.000 − 20.000 = 105.000", 10500000, partes[0]["saldo_minor"])
    caso("CEDEAR: 10 unidades", 10.0, partes[1]["unidades"])
    caso("en_cero: solo el plazo fijo", [False, False, True], [p["en_cero"] for p in partes])

    print("\n--- 3. delete_objetivo() con errores: no cambia nada ---")
    caso_excepcion("repartir hacia sí mismo → AsignacionInvalidaError", AsignacionInvalidaError,
                   lambda: svc.delete_objetivo(viejo, {fci: todo_a(viejo)}))
    caso_excepcion("reparto que suma 110% → AsignacionInvalidaError", AsignacionInvalidaError,
                   lambda: svc.delete_objetivo(viejo, {fci: [
                       {"objetivo_id": moto, "porcentaje": 60.0}, {"objetivo_id": general, "porcentaje": 50.0},
                   ]}))
    caso_excepcion("destino inexistente → ObjetivoNotFoundError", ObjetivoNotFoundError,
                   lambda: svc.delete_objetivo(viejo, {fci: todo_a("no-existe")}))
    caso("tras los rechazos el objetivo sigue existiendo", True, existe(viejo))
    caso("tras los rechazos el aporte 2 sigue igual",
         [("MOTO", 50.0, 2500000), ("VIEJITO", 50.0, 2500000)], asignaciones(a2))

    print("\n--- 4. delete_objetivo(): FCI 60/40, CEDEAR a TERRENEITOR, plazo fijo sin asignar ---")
    resultado = svc.delete_objetivo(viejo, {
        fci: [{"objetivo_id": moto, "porcentaje": 60.0}, {"objetivo_id": general, "porcentaje": 40.0}],
        cedear: todo_a(terreno),
        plazo: [],
    })
    caso("delete: success", True, resultado.success)
    caso("delete: 6 movimientos reasignados (3 del FCI, 1 del CEDEAR, 2 del plazo fijo)",
         6, resultado.data["movimientos_reasignados"])
    caso("el objetivo ya no existe", False, existe(viejo))
    caso("aporte 1 (100.000 de VIEJO): 60.000 a MOTO, 40.000 a AHORRO GENERAL",
         [("MOTO", 60.0, 6000000), ("AHORRO GENERAL", 40.0, 4000000)], asignaciones(a1))
    caso("aporte 2 (25.000 de VIEJO): se suma a la mitad de MOTO → MOTO 80%, AHORRO GENERAL 20%",
         [("MOTO", 80.0, 4000000), ("AHORRO GENERAL", 20.0, 1000000)], asignaciones(a2))
    caso("retiro (20.000 de VIEJO): 12.000 de MOTO, 8.000 de AHORRO GENERAL",
         [("MOTO", 60.0, 1200000), ("AHORRO GENERAL", 40.0, 800000)], asignaciones(r1))
    caso("compra del CEDEAR: toda a TERRENEITOR", [("TERRENEITOR", 100.0, 1000000)], asignaciones(c1))
    caso("plazo fijo (reparto vacío): sus movimientos quedan sin objetivos", ([], []), (asignaciones(p1), asignaciones(p2)))
    caso("resumen: MOTO en el FCI = 25.000 + 60% de 105.000", 8800000, parte("MOTO", fci))
    caso("resumen: AHORRO GENERAL en el FCI = 40% de 105.000", 4200000, parte("AHORRO GENERAL", fci))
    caso("resumen: el FCI no tiene nada SIN ASIGNAR", None, parte(OBJETIVO_SIN_ASIGNAR, fci))
    caso("resumen: TERRENEITOR en el CEDEAR = 4 + 10 unidades", 14.0, parte("TERRENEITOR", cedear, "unidades"))
    caso("resumen: VIEJITO ya no aparece", None, svc.get_resumen_por_objetivo().get("VIEJITO"))

    print("\n--- 5. Reparto parcial: lo que no llega al 100% queda SIN ASIGNAR ---")
    parcial = svc.create_objetivo(nombre="PARCIAL").entity_id
    svc.registrar_aporte(fci, 1000000, "2026-03-01", crear_transaccion=False, asignaciones=todo_a(parcial))
    svc.delete_objetivo(parcial, {fci: todo_a(moto, 50.0)})
    caso("MOTO en el FCI: 88.000 + 5.000", 9300000, parte("MOTO", fci))
    caso("SIN ASIGNAR en el FCI: los otros 5.000", 500000, parte(OBJETIVO_SIN_ASIGNAR, fci))

    print("\n--- 6. Sin movimientos, y con filas en activo_objetivos (deprecated) ---")
    vacio = svc.create_objetivo(nombre="VACIO").entity_id
    caso("objetivo sin movimientos: get_partes_de_objetivo() vacío", [], svc.get_partes_de_objetivo(vacio))
    svc.delete_objetivo(vacio)
    caso("objetivo sin movimientos: eliminado sin repartos", False, existe(vacio))
    con_reparto_viejo = svc.create_objetivo(nombre="CON REPARTO VIEJO").entity_id
    ActivoObjetivosRepository(manager).crear(fci, con_reparto_viejo, 50.0)
    svc.delete_objetivo(con_reparto_viejo)
    caso("con una fila en activo_objetivos: eliminado igual (la FK no lo frena)", False, existe(con_reparto_viejo))
    caso("su fila de activo_objetivos también se borró", 0, manager.fetchone(
        "SELECT COUNT(*) AS n FROM activo_objetivos WHERE objetivo_id = ?;", (con_reparto_viejo,),
    )["n"])

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(La DB temporal quedó en {db_path} — no es data/deltabalance.db.)")


if __name__ == "__main__":
    main()
