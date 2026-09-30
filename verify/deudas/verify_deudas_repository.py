"""
verify/deudas/verify_deudas_repository.py

Verifica DeudasRepository (repositories/deudas_repository.py) sobre la
tabla `deudas` reestructurada como libro de movimientos: crear, lecturas
enriquecidas con la moneda (obtener_por_id, listar_por_persona,
listar_por_periodo, listar_todo, listar_por_origen), actualizar con el
sentinel NO_CAMBIAR vs None explícito, eliminar, y los saldos con signo
(get_saldo_neto_por_persona, get_saldo_persona). Sin reglas de negocio: el
repositorio guarda lo que recibe (la normalización y el signo son del
service).

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

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")
    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()  # reestructura `deudas` (base nueva)
    repo = DeudasRepository(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    print("--- crear() / obtener_por_id() ---")
    a = repo.crear("NOE", "Préstamo", "a_favor", 40000, ars, "2026-03-01", notas="nota")
    b = repo.crear("NOE", "Devolvió", "en_contra", 10000, ars, "2026-03-20")
    c = repo.crear("NOE", "En dólares", "a_favor", 500, usd, "2026-04-05")
    d = repo.crear("KEVIN", "Cena", "en_contra", 5000, ars, "2026-04-10", origen_tipo="transaccion", origen_id=77)
    fila_a = repo.obtener_por_id(a)
    caso("crear() devuelve un id y la fila existe", True, fila_a is not None)
    caso("columnas propias", ("NOE", "Préstamo", "a_favor", 40000, "2026-03-01", "nota", "manual"),
         (fila_a["entidad_persona"], fila_a["concepto"], fila_a["tipo"], fila_a["monto_minor"], fila_a["fecha"],
          fila_a["notas"], fila_a["origen_tipo"]))
    caso("enriquecida con la moneda", ("ARS", "$", 2), (fila_a["currency_code"], fila_a["currency_symbol"], fila_a["decimales"]))
    caso("obtener_por_id() de un id inexistente → None", None, repo.obtener_por_id(999999))

    print("\n--- listados ---")
    caso("listar_por_persona('NOE'): sus 3 filas, la más nueva primero", [c, b, a],
         [f["id"] for f in repo.listar_por_persona("NOE")])
    caso("listar_por_persona('NOE', USD): solo la de dólares", [c], [f["id"] for f in repo.listar_por_persona("NOE", usd)])
    caso("listar_por_periodo(marzo): bordes incluidos", {a, b},
         {f["id"] for f in repo.listar_por_periodo("2026-03-01", "2026-03-31")})
    caso("listar_todo(): las 4", 4, len(repo.listar_todo()))
    caso("listar_por_origen('transaccion', 77)", [d], [f["id"] for f in repo.listar_por_origen("transaccion", 77)])

    print("\n--- saldos con signo ---")
    saldos = {(f["entidad_persona"], f["moneda_id"]): f["saldo"] for f in repo.get_saldo_neto_por_persona("2026-12-31")}
    caso("NOE ARS: 40000 − 10000", 30000, saldos.get(("NOE", ars)))
    caso("NOE USD: aparte (nunca se mezclan monedas)", 500, saldos.get(("NOE", usd)))
    caso("KEVIN ARS: en_contra → negativo", -5000, saldos.get(("KEVIN", ars)))
    caso("get_saldo_persona(NOE, ARS, hasta el 10/03): todavía sin la devolución", 40000,
         repo.get_saldo_persona("NOE", ars, "2026-03-10"))
    caso("get_saldo_persona() sin filas → 0", 0, repo.get_saldo_persona("NADIE", ars, "2026-12-31"))

    print("\n--- actualizar() ---")
    caso("actualizar() sin campos → False", False, repo.actualizar(a))
    caso("actualizar() de un id inexistente → False", False, repo.actualizar(999999, concepto="x"))
    repo.actualizar(a, concepto="Préstamo grande", monto_minor=45000)
    fila_a2 = repo.obtener_por_id(a)
    caso("solo cambian los campos pasados (NO_CAMBIAR en el resto)", ("Préstamo grande", 45000, "nota"),
         (fila_a2["concepto"], fila_a2["monto_minor"], fila_a2["notas"]))
    repo.actualizar(a, notas=None)
    caso("None explícito escribe NULL", None, repo.obtener_por_id(a)["notas"])

    print("\n--- eliminar() ---")
    caso("eliminar() → True", True, repo.eliminar(b))
    caso("la fila ya no está", None, repo.obtener_por_id(b))
    caso("eliminar() de nuevo → False", False, repo.eliminar(b))

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
