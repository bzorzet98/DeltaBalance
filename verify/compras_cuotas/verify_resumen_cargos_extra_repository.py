"""
verify/compras_cuotas/verify_resumen_cargos_extra_repository.py

Verifica ResumenCargosExtraRepository
(repositories/resumen_cargos_extra_repository.py): agregar, listar_por_resumen,
eliminar (DELETE físico) y suma_por_resumen — incluyendo un cargo negativo
(ajuste a favor del usuario), confirmando que la suma neta da el resultado
correcto.

Correlo con:
    python verify/compras_cuotas/verify_resumen_cargos_extra_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.resumenes_tarjeta_repository import ResumenesTarjetaRepository
from repositories.resumen_cargos_extra_repository import ResumenCargosExtraRepository


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
    manager.inicializar()
    cuentas_repo = CuentasRepository(manager)
    resumenes_repo = ResumenesTarjetaRepository(manager)
    repo = ResumenCargosExtraRepository(manager)

    cuenta_a = cuentas_repo.crear(nombre="Tarjeta Cargos A", tipo="credito", moneda_codigo="ARS")
    resumen_1 = resumenes_repo.crear(cuenta_id=cuenta_a, mes=1, anio=2026)
    resumen_2 = resumenes_repo.crear(cuenta_id=cuenta_a, mes=2, anio=2026)

    print("--- suma_por_resumen() sin ningún cargo todavía ---")
    caso("suma_por_resumen() de un resumen sin cargos devuelve 0", 0, repo.suma_por_resumen(resumen_1))

    print("\n--- agregar() ---")
    cargo_1 = repo.agregar(resumen_1, concepto="IVA", tipo="impuesto", monto_minor=15000)
    cargo_2 = repo.agregar(resumen_1, concepto="Impuesto sellos", tipo="impuesto", monto_minor=3000)
    cargo_3 = repo.agregar(resumen_1, concepto="Recargo por mora", tipo="recargo", monto_minor=2000)
    cargo_4 = repo.agregar(resumen_1, concepto="Ajuste a favor por error de cobro", tipo="ajuste", monto_minor=-5000)
    caso(
        "agregar() devuelve ids numéricos distintos",
        True,
        len({cargo_1, cargo_2, cargo_3, cargo_4}) == 4 and all(isinstance(x, int) for x in (cargo_1, cargo_2, cargo_3, cargo_4)),
    )

    fila_1_cruda = manager.fetchone("SELECT * FROM resumen_cargos_extra WHERE id = ?;", (cargo_1,))
    caso("agregar() persiste concepto", "IVA", fila_1_cruda["concepto"])
    caso("agregar() persiste tipo", "impuesto", fila_1_cruda["tipo"])
    caso("agregar() persiste monto_minor", 15000, fila_1_cruda["monto_minor"])

    fila_4_cruda = manager.fetchone("SELECT * FROM resumen_cargos_extra WHERE id = ?;", (cargo_4,))
    caso("agregar() persiste monto_minor negativo (ajuste a favor)", -5000, fila_4_cruda["monto_minor"])

    print("\n--- agregar() a otro resumen (aislamiento) ---")
    cargo_otro = repo.agregar(resumen_2, concepto="IVA resumen 2", tipo="impuesto", monto_minor=8000)
    caso("agregar() en resumen_2 no aparece al listar resumen_1", False, cargo_otro in [c["id"] for c in repo.listar_por_resumen(resumen_1)])

    print("\n--- listar_por_resumen() ---")
    cargos_resumen_1 = repo.listar_por_resumen(resumen_1)
    ids_resumen_1 = [c["id"] for c in cargos_resumen_1]
    caso("listar_por_resumen() trae los 4 cargos del resumen_1", True, all(x in ids_resumen_1 for x in (cargo_1, cargo_2, cargo_3, cargo_4)))
    caso("listar_por_resumen() no trae el cargo del resumen_2", False, cargo_otro in ids_resumen_1)
    caso("listar_por_resumen() devuelve exactamente 4 filas para resumen_1", 4, len(cargos_resumen_1))

    print("\n--- suma_por_resumen() — neto con un ajuste negativo ---")
    # 15000 + 3000 + 2000 - 5000 = 15000
    caso("suma_por_resumen(resumen_1) da el neto correcto incluyendo el ajuste negativo", 15000, repo.suma_por_resumen(resumen_1))
    caso("suma_por_resumen(resumen_2) solo cuenta su propio cargo", 8000, repo.suma_por_resumen(resumen_2))

    print("\n--- agregar(conn=...) — participa de una transacción externa ---")
    conn_externo = manager.conn
    with manager.transaction():
        cargo_5 = repo.agregar(resumen_1, concepto="Otro cargo", tipo="otro", monto_minor=1000, conn=conn_externo)
    caso("agregar(conn=...) persiste tras comitear", "Otro cargo", manager.fetchone("SELECT concepto FROM resumen_cargos_extra WHERE id = ?;", (cargo_5,))["concepto"])
    caso("suma_por_resumen(resumen_1) refleja el cargo agregado con conn", 16000, repo.suma_por_resumen(resumen_1))

    print("\n--- agregar(conn=...) — rollback simulado ---")
    cargos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM resumen_cargos_extra;")["n"]
    try:
        with manager.transaction():
            repo.agregar(resumen_1, concepto="Cargo que no debería persistir", tipo="otro", monto_minor=999, conn=conn_externo)
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    cargos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM resumen_cargos_extra;")["n"]
    caso("rollback revierte el INSERT del cargo (no queda huérfano)", cargos_antes, cargos_despues)
    caso("suma_por_resumen(resumen_1) no cambió tras el rollback", 16000, repo.suma_por_resumen(resumen_1))

    print("\n--- eliminar() — DELETE físico ---")
    repo.eliminar(cargo_3)
    caso("eliminar() borra la fila (obtener_por_id equivalente ya no la encuentra)", None, manager.fetchone("SELECT * FROM resumen_cargos_extra WHERE id = ?;", (cargo_3,)))
    caso("eliminar() se refleja en suma_por_resumen (baja el neto en 2000)", 14000, repo.suma_por_resumen(resumen_1))
    caso("eliminar() se refleja en listar_por_resumen (ya no aparece)", False, cargo_3 in [c["id"] for c in repo.listar_por_resumen(resumen_1)])

    print("\n--- eliminar(conn=...) — participa de una transacción externa ---")
    with manager.transaction():
        repo.eliminar(cargo_5, conn=conn_externo)
    caso("eliminar(conn=...) persiste tras comitear", None, manager.fetchone("SELECT * FROM resumen_cargos_extra WHERE id = ?;", (cargo_5,)))
    caso("suma_por_resumen(resumen_1) refleja el eliminar con conn", 13000, repo.suma_por_resumen(resumen_1))

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
