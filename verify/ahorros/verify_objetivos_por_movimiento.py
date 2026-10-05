"""
verify/ahorros/verify_objetivos_por_movimiento.py

Verifica los objetivos POR MOVIMIENTO de services/savings_service.py
(docs/DATA_MODEL_DECISIONS.md sección 31): cada movimiento dice a qué
objetivos va y en qué porcentaje, sin un reparto fijo por activo.

Escenario inspirado en la planilla de ahorros del usuario, con números
redondos para que los esperados sean exactos:

1. FCI (plata): aportes a MOTO y a TERRENEITOR en distintos días.
   - Rendimiento sin asignaciones = PROPORCIONAL a lo que cada objetivo
     tenía ANTES de esa fecha (un aporte del mismo día no cuenta).
   - Rendimiento con asignaciones a mano (todo a MOTO, como la planilla).
   - Retiro asignado a un objetivo.
   - Resumen por objetivo = suma de lo asignado en cada movimiento.
2. Lo que no llega al 100% queda SIN ASIGNAR; update_asignaciones() lo
   corrige después.
3. Un retiro puede dejar a un objetivo en negativo (solo se valida el total
   del activo, decisión del usuario); el reparto proporcional lo saltea.
4. CEDEAR (unidades), como NVDA en la planilla: 253 de AHORRO GENERAL, 14 +
   20 de TERRENEITOR, venta de 150 de AHORRO GENERAL → 103 / 34. Dividendo
   proporcional por unidades. Una compra sin objetivos queda SIN ASIGNAR.
5. Movimiento vinculado a una transacción del Registro: sus objetivos sí se
   cambian.
6. Errores: suma > 100, objetivo repetido, porcentaje 0, objetivo y
   movimiento inexistentes — sin escribir nada.

Correlo con:
    python verify/ahorros/verify_objetivos_por_movimiento.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.savings_service import (
    OBJETIVO_SIN_ASIGNAR,
    AsignacionInvalidaError,
    MovimientoNotFoundError,
    ObjetivoNotFoundError,
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
    cuentas_repo = CuentasRepository(manager)
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]

    moto = svc.create_objetivo(nombre="MOTO").entity_id
    terreno = svc.create_objetivo(nombre="TERRENEITOR").entity_id
    general = svc.create_objetivo(nombre="AHORRO GENERAL").entity_id
    nombres = {moto: "MOTO", terreno: "TERRENEITOR", general: "AHORRO GENERAL"}

    def todo_a(objetivo_id: str, porcentaje: float = 100.0) -> list[dict]:
        return [{"objetivo_id": objetivo_id, "porcentaje": porcentaje}]

    def asignaciones(movimiento_id: str) -> list[tuple[str, float, int]]:
        """(objetivo, porcentaje, monto) del movimiento, el mayor porcentaje primero (empates por nombre)."""
        filas = manager.fetchall(
            "SELECT objetivo_id, porcentaje, monto_asignado_minor FROM asignaciones WHERE movimiento_id = ?;",
            (movimiento_id,),
        )
        tuplas = [(nombres[f["objetivo_id"]], f["porcentaje"], f["monto_asignado_minor"]) for f in filas]
        return sorted(tuplas, key=lambda t: (-t[1], t[0]))

    def movimientos_totales() -> int:
        return manager.fetchone("SELECT COUNT(*) AS n FROM movimientos_activo;")["n"]

    def entrada(activo_id: str) -> dict:
        return next(e for entradas in svc.get_resumen_por_tipo().values() for e in entradas if e["activo_id"] == activo_id)

    def reparto_redondeado(activo_id: str) -> list[tuple[str, float]]:
        """El reparto calculado de la tarjeta (get_resumen_por_tipo()), con 2 decimales como en pantalla."""
        return [(o["nombre"], round(o["porcentaje"], 2)) for o in entrada(activo_id)["objetivos"]]

    def parte(objetivo: str, activo_id: str, clave: str = "saldo_minor"):
        """Lo de un objetivo en un activo según get_resumen_por_objetivo() (None si no aparece)."""
        partes = svc.get_resumen_por_objetivo().get(objetivo, [])
        return next((p[clave] for p in partes if p["activo_id"] == activo_id), None)

    def proporcional(activo_id: str, fecha: str) -> list[tuple[str, float]]:
        return [(r["nombre"], r["porcentaje"]) for r in svc.get_reparto_proporcional(activo_id, fecha)]

    fci = svc.create_activo(nombre="COCOS PESOS PLUS", tipo="fci", moneda_id=moneda_ars).entity_id

    print("--- 1. FCI: aportes con objetivo, rendimientos proporcionales y a mano, retiro ---")
    aporte_moto = svc.registrar_aporte(fci, 29900000, "2026-02-20", crear_transaccion=False, asignaciones=todo_a(moto))
    caso("aporte 299.000 a MOTO: todo a MOTO", [("MOTO", 100.0, 29900000)], asignaciones(aporte_moto.entity_id))

    rend_1 = svc.registrar_rendimiento(fci, 200000, "2026-03-02")
    caso("rendimiento 2.000 sin asignaciones: proporcional, solo MOTO tenía algo → 100% MOTO",
         [("MOTO", 100.0, 200000)], asignaciones(rend_1.entity_id))

    svc.registrar_aporte(fci, 10100000, "2026-06-04", crear_transaccion=False, asignaciones=todo_a(terreno))
    rend_mismo_dia = svc.registrar_rendimiento(fci, 200000, "2026-06-04")
    caso("rendimiento el MISMO día que el aporte de TERRENEITOR: ese aporte no cuenta → 100% MOTO",
         [("MOTO", 100.0, 200000)], asignaciones(rend_mismo_dia.entity_id))

    caso("reparto proporcional al 2026-07-03: MOTO 303.000 / TERRENEITOR 101.000 → 75 / 25",
         [("MOTO", 75.0), ("TERRENEITOR", 25.0)], proporcional(fci, "2026-07-03"))
    rend_prop = svc.registrar_rendimiento(fci, 400000, "2026-07-03")
    caso("rendimiento 4.000 proporcional: 3.000 a MOTO, 1.000 a TERRENEITOR",
         [("MOTO", 75.0, 300000), ("TERRENEITOR", 25.0, 100000)], asignaciones(rend_prop.entity_id))

    rend_mano = svc.registrar_rendimiento(fci, 100000, "2026-07-03", asignaciones=todo_a(moto))
    caso("rendimiento 1.000 con asignaciones a mano: todo a MOTO (como la planilla)",
         [("MOTO", 100.0, 100000)], asignaciones(rend_mano.entity_id))

    retiro = svc.registrar_retiro(fci, 700000, "2026-08-01", crear_transaccion=False, asignaciones=todo_a(moto))
    caso("retiro 7.000 de MOTO", [("MOTO", 100.0, 700000)], asignaciones(retiro.entity_id))

    caso("resumen por objetivo: MOTO = 299.000 + 2.000 + 2.000 + 3.000 + 1.000 − 7.000",
         30000000, parte("MOTO", fci))
    caso("resumen por objetivo: TERRENEITOR = 101.000 + 1.000", 10200000, parte("TERRENEITOR", fci))
    caso("resumen por objetivo: el FCI no tiene nada SIN ASIGNAR", None, parte(OBJETIVO_SIN_ASIGNAR, fci))
    caso("tarjeta: reparto calculado 300.000 / 402.000 y 102.000 / 402.000",
         [("MOTO", 74.63), ("TERRENEITOR", 25.37)], reparto_redondeado(fci))
    caso("tarjeta: saldo del FCI = 402.000", 40200000, entrada(fci)["saldo_minor"])

    print("\n--- 2. Lo que no llega al 100% queda SIN ASIGNAR; update_asignaciones() lo corrige ---")
    aporte_mitad = svc.registrar_aporte(
        fci, 1000000, "2026-08-02", crear_transaccion=False, asignaciones=todo_a(moto, 50.0),
    )
    caso("aporte 10.000 con MOTO 50%: solo 5.000 asignados", [("MOTO", 50.0, 500000)], asignaciones(aporte_mitad.entity_id))
    caso("resumen por objetivo: SIN ASIGNAR del FCI = 5.000", 500000, parte(OBJETIVO_SIN_ASIGNAR, fci))
    caso("tarjeta: sin_asignar del FCI = 5.000", 500000, entrada(fci)["sin_asignar"]["saldo_minor"])

    resultado = svc.update_asignaciones(
        aporte_mitad.entity_id,
        [{"objetivo_id": moto, "porcentaje": 50.0}, {"objetivo_id": general, "porcentaje": 50.0}],
    )
    caso("update_asignaciones: success", True, resultado.success)
    caso("update_asignaciones: MOTO 50% + AHORRO GENERAL 50%",
         [("AHORRO GENERAL", 50.0, 500000), ("MOTO", 50.0, 500000)], asignaciones(aporte_mitad.entity_id))
    caso("resumen por objetivo: ya no hay SIN ASIGNAR en el FCI", None, parte(OBJETIVO_SIN_ASIGNAR, fci))
    caso("resumen por objetivo: AHORRO GENERAL = 5.000", 500000, parte("AHORRO GENERAL", fci))
    caso("update_asignaciones no toca el rendimiento proporcional ya cargado",
         [("MOTO", 75.0, 300000), ("TERRENEITOR", 25.0, 100000)], asignaciones(rend_prop.entity_id))

    print("\n--- 3. Objetivo en negativo: se permite (solo se valida el total del activo) ---")
    svc.registrar_retiro(fci, 10300000, "2026-08-03", crear_transaccion=False, asignaciones=todo_a(terreno))
    caso("retiro 103.000 de TERRENEITOR (tenía 102.000): TERRENEITOR = −1.000", -100000, parte("TERRENEITOR", fci))
    caso("el total del activo sigue cerrando: nada SIN ASIGNAR", None, parte(OBJETIVO_SIN_ASIGNAR, fci))
    caso("reparto proporcional después: TERRENEITOR (negativo) no entra; MOTO 305.000 / AHORRO GENERAL 5.000",
         [("MOTO", 98.3871), ("AHORRO GENERAL", 1.6129)], proporcional(fci, "2026-08-04"))

    print("\n--- 4. CEDEAR por unidades (NVDA de la planilla) ---")
    nvda = svc.create_activo(nombre="NVDA", tipo="cedear", moneda_id=moneda_ars).entity_id
    svc.registrar_compra(nvda, 253, 253000000, 0, "2025-09-14", notas="TENENCIA INICIAL", crear_transaccion=False,
                         asignaciones=todo_a(general))
    svc.registrar_compra(nvda, 14, 15337056, 0, "2025-09-14", crear_transaccion=False, asignaciones=todo_a(terreno))
    svc.registrar_compra(nvda, 20, 23644780, 0, "2025-09-17", crear_transaccion=False, asignaciones=todo_a(terreno))
    venta = svc.registrar_venta(nvda, 150, 162149700, 0, "2025-12-15", crear_transaccion=False, asignaciones=todo_a(general))
    caso("venta de 150 asignada a AHORRO GENERAL", [("AHORRO GENERAL", 100.0, 162149700)], asignaciones(venta.entity_id))
    caso("unidades del activo: 253 + 14 + 20 − 150", 137, entrada(nvda)["unidades"])
    caso("AHORRO GENERAL: 253 − 150 = 103 unidades", 103.0, parte("AHORRO GENERAL", nvda, "unidades"))
    caso("TERRENEITOR: 14 + 20 = 34 unidades", 34.0, parte("TERRENEITOR", nvda, "unidades"))
    caso("tarjeta: reparto por unidades 103 / 137 y 34 / 137",
         [("AHORRO GENERAL", 75.18), ("TERRENEITOR", 24.82)], reparto_redondeado(nvda))

    dividendo = svc.registrar_rendimiento(nvda, 137000, "2026-01-10")
    caso("dividendo 1.370 proporcional a las unidades (75.1825% / 24.8175%, el resto del redondeo al mayor)",
         [("AHORRO GENERAL", 75.1825, 103001), ("TERRENEITOR", 24.8175, 33999)], asignaciones(dividendo.entity_id))

    compra_libre = svc.registrar_compra(nvda, 5, 6000000, 0, "2026-02-01", crear_transaccion=False)
    caso("compra sin asignaciones: sin objetivos", [], asignaciones(compra_libre.entity_id))
    caso("resumen por objetivo: SIN ASIGNAR de NVDA = 5 unidades", 5.0, parte(OBJETIVO_SIN_ASIGNAR, nvda, "unidades"))

    print("\n--- 5. Movimiento vinculado a una transacción del Registro: los objetivos sí se cambian ---")
    cuenta = cuentas_repo.crear(
        nombre="Banco (verify objetivos por movimiento)", tipo="debito", moneda_codigo="ARS", saldo_inicial=100000.0,
    )
    fci_cuenta = svc.create_activo(nombre="FCI CON CUENTA", tipo="fci", moneda_id=moneda_ars, cuenta_id=cuenta).entity_id
    vinculado = svc.registrar_aporte(fci_cuenta, 5000000, "2026-09-01", asignaciones=todo_a(moto))
    caso("aporte con cuenta: creó la transacción del Registro", True, vinculado.data["transaccion_id"] is not None)
    svc.update_asignaciones(vinculado.entity_id, todo_a(terreno))
    caso("update_asignaciones en un vinculado: ahora todo a TERRENEITOR",
         [("TERRENEITOR", 100.0, 5000000)], asignaciones(vinculado.entity_id))
    caso("la transacción sigue vinculada", vinculado.data["transaccion_id"],
         manager.fetchone("SELECT transaccion_id FROM movimientos_activo WHERE id = ?;", (vinculado.entity_id,))["transaccion_id"])

    print("\n--- 6. Errores: no se escribe nada ---")
    antes = movimientos_totales()
    caso_excepcion("objetivos que suman 110% → AsignacionInvalidaError", AsignacionInvalidaError,
                   lambda: svc.registrar_aporte(fci, 100000, "2026-09-02", crear_transaccion=False, asignaciones=[
                       {"objetivo_id": moto, "porcentaje": 60.0}, {"objetivo_id": terreno, "porcentaje": 50.0},
                   ]))
    caso_excepcion("el mismo objetivo dos veces → AsignacionInvalidaError", AsignacionInvalidaError,
                   lambda: svc.registrar_aporte(fci, 100000, "2026-09-02", crear_transaccion=False, asignaciones=[
                       {"objetivo_id": moto, "porcentaje": 30.0}, {"objetivo_id": moto, "porcentaje": 30.0},
                   ]))
    caso_excepcion("porcentaje 0 → AsignacionInvalidaError", AsignacionInvalidaError,
                   lambda: svc.registrar_rendimiento(fci, 100000, "2026-09-02", asignaciones=todo_a(moto, 0)))
    caso_excepcion("objetivo inexistente → ObjetivoNotFoundError", ObjetivoNotFoundError,
                   lambda: svc.registrar_aporte(fci, 100000, "2026-09-02", crear_transaccion=False,
                                                asignaciones=todo_a("no-existe")))
    caso("ningún movimiento nuevo tras los rechazos", antes, movimientos_totales())
    caso_excepcion("update_asignaciones con suma > 100 → AsignacionInvalidaError", AsignacionInvalidaError,
                   lambda: svc.update_asignaciones(retiro.entity_id, [
                       {"objetivo_id": moto, "porcentaje": 80.0}, {"objetivo_id": general, "porcentaje": 30.0},
                   ]))
    caso("tras el rechazo, el retiro conserva sus objetivos", [("MOTO", 100.0, 700000)], asignaciones(retiro.entity_id))
    caso_excepcion("update_asignaciones de un movimiento inexistente → MovimientoNotFoundError", MovimientoNotFoundError,
                   lambda: svc.update_asignaciones("no-existe", []))

    print(f"\n{casos_ok}/{casos_total} casos OK")
    print(f"(La DB temporal quedó en {db_path} — no es data/deltabalance.db.)")


if __name__ == "__main__":
    main()
