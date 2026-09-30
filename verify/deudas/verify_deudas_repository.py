"""
verify/deudas/verify_deudas_repository.py

Verifica DeudasRepository (repositories/deudas_repository.py) sobre la
estructura final de `deudas` (tabs 'me_deben' / 'debo' + monto_minor con
signo): crear, lecturas enriquecidas con la moneda (obtener_por_id,
listar_por_tab por mes / año / completo, listar_por_persona,
listar_por_origen), el saldo por persona de un tab
(get_saldo_neto_por_persona), actualizar con el sentinel NO_CAMBIAR vs None
explícito, y eliminar. Sin reglas de negocio: el repositorio guarda lo que
recibe (la normalización y las validaciones son del service).

Correlo con:
    python verify/deudas/verify_deudas_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.deudas_repository import DeudasRepository


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
    repo = DeudasRepository(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    print("--- crear() / obtener_por_id() ---")
    a = repo.crear("NOE", "Préstamo", "me_deben", 40000, ars, "2026-03-01", notas="nota")
    b = repo.crear("NOE", "Devolvió", "me_deben", -10000, ars, "2026-03-20")
    c = repo.crear("NOE", "En dólares", "me_deben", 500, usd, "2026-04-05")
    d = repo.crear("NOE", "Le debo el cine", "debo", 1200, ars, "2026-04-06")
    e = repo.crear("KEVIN", "Cena", "debo", 5000, ars, "2027-01-10", origen_tipo="transaccion", origen_id=77)
    fila_a = repo.obtener_por_id(a)
    caso("crear() devuelve un id y la fila existe", True, fila_a is not None)
    caso("columnas propias", ("NOE", "Préstamo", "me_deben", 40000, "2026-03-01", "nota", "manual"),
         (fila_a["entidad_persona"], fila_a["concepto"], fila_a["tab"], fila_a["monto_minor"], fila_a["fecha"],
          fila_a["notas"], fila_a["origen_tipo"]))
    caso("un monto negativo se guarda tal cual", -10000, repo.obtener_por_id(b)["monto_minor"])
    caso("enriquecida con la moneda", ("ARS", "$", 2), (fila_a["currency_code"], fila_a["currency_symbol"], fila_a["decimales"]))
    caso("obtener_por_id() de un id inexistente → None", None, repo.obtener_por_id(999999))

    print("\n--- listados ---")
    caso("listar_por_tab('me_deben'): las 3 de NOE, la más nueva primero", [c, b, a],
         [f["id"] for f in repo.listar_por_tab("me_deben")])
    caso("listar_por_tab('me_deben', 3, 2026): marzo, bordes incluidos", {a, b},
         {f["id"] for f in repo.listar_por_tab("me_deben", 3, 2026)})
    caso("listar_por_tab('debo', anio=2026): solo ese año", [d], [f["id"] for f in repo.listar_por_tab("debo", anio=2026)])
    caso_excepcion("listar_por_tab() con mes sin anio → ValueError", ValueError, lambda: repo.listar_por_tab("debo", 4))
    caso("listar_por_persona('NOE'): los dos tabs", 4, len(repo.listar_por_persona("NOE")))
    caso("listar_por_persona('NOE', 'debo')", [d], [f["id"] for f in repo.listar_por_persona("NOE", "debo")])
    caso("listar_por_origen('transaccion', 77)", [e], [f["id"] for f in repo.listar_por_origen("transaccion", 77)])

    print("\n--- get_saldo_neto_por_persona() ---")

    def saldos(tab: str, hasta: str) -> dict:
        return {(f["entidad_persona"], f["moneda_id"]): f["saldo"] for f in repo.get_saldo_neto_por_persona(tab, hasta)}

    me_deben = saldos("me_deben", "2026-12-31")
    caso("ME DEBEN — NOE ARS: 40000 − 10000", 30000, me_deben.get(("NOE", ars)))
    caso("ME DEBEN — NOE USD: aparte (nunca se mezclan monedas)", 500, me_deben.get(("NOE", usd)))
    caso("ME DEBEN no incluye lo de DEBO", False, any(persona == "KEVIN" for persona, _ in me_deben))
    caso("DEBO — NOE: 1200, aparte de lo que te debe", 1200, saldos("debo", "2026-12-31").get(("NOE", ars)))
    caso("DEBO hasta fin de 2026: KEVIN (enero 2027) todavía no está", None, saldos("debo", "2026-12-31").get(("KEVIN", ars)))
    caso("ME DEBEN hasta el 10/03: todavía sin la devolución", 40000, saldos("me_deben", "2026-03-10").get(("NOE", ars)))

    print("\n--- actualizar() ---")
    caso("actualizar() sin campos → False", False, repo.actualizar(a))
    caso("actualizar() de un id inexistente → False", False, repo.actualizar(999999, concepto="x"))
    repo.actualizar(a, concepto="Préstamo grande", monto_minor=45000)
    fila_a2 = repo.obtener_por_id(a)
    caso("solo cambian los campos pasados (NO_CAMBIAR en el resto)", ("Préstamo grande", 45000, "nota", "me_deben"),
         (fila_a2["concepto"], fila_a2["monto_minor"], fila_a2["notas"], fila_a2["tab"]))
    repo.actualizar(a, notas=None)
    caso("None explícito escribe NULL", None, repo.obtener_por_id(a)["notas"])
    repo.actualizar(a, tab="debo", entidad_persona="NOELIA", moneda_id=usd)
    fila_a3 = repo.obtener_por_id(a)
    caso("tab, persona y moneda también se actualizan", ("debo", "NOELIA", "USD"),
         (fila_a3["tab"], fila_a3["entidad_persona"], fila_a3["currency_code"]))

    print("\n--- eliminar() ---")
    caso("eliminar() → True", True, repo.eliminar(b))
    caso("la fila ya no está", None, repo.obtener_por_id(b))
    caso("eliminar() de nuevo → False", False, repo.eliminar(b))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
