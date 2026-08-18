"""
verify/cuentas_categorias/verify_cuentas_repository.py

Verifica CuentasRepository (repositories/cuentas_repository.py): que el CRUD
extraído de db/database.py se comporta igual que los métodos viejos
(obtener_cuentas, crear_cuenta, modificar_cuenta, archivar_cuenta,
obtener_saldo_cuenta) — crear, obtener por id, listar respetando el filtro
de activas, actualizar, archivar (soft-delete, la fila sigue existiendo) y
calcular el saldo vía vw_balance_cuentas.

Correlo con:
    python verify/cuentas_categorias/verify_cuentas_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository


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
    repo = CuentasRepository(manager)

    print("--- crear() ---")
    cuenta_id = repo.crear(
        nombre="Cuenta Test Repo",
        tipo="efectivo",
        moneda_codigo="ARS",
        saldo_inicial=100.0,
        notas="creada por verify_cuentas_repository",
    )
    caso("crear() devuelve un id numérico", True, isinstance(cuenta_id, int) and cuenta_id > 0)

    print("\n--- obtener_por_id() ---")
    fila = repo.obtener_por_id(cuenta_id)
    caso("obtener_por_id() encuentra la cuenta recién creada", "Cuenta Test Repo", fila["nombre"] if fila else None)
    caso("la cuenta nace activa = 1", 1, fila["activa"] if fila else None)

    print("\n--- listar() ---")
    listado_activas = repo.listar(solo_activas=True)
    caso(
        "listar(solo_activas=True) incluye la cuenta recién creada",
        True,
        cuenta_id in [r["id"] for r in listado_activas],
    )

    print("\n--- actualizar() ---")
    actualizado = repo.actualizar(cuenta_id, notas="nota actualizada")
    caso("actualizar() devuelve True cuando hay campos para actualizar", True, actualizado)
    fila_actualizada = repo.obtener_por_id(cuenta_id)
    caso("actualizar() persiste el cambio de notas", "nota actualizada", fila_actualizada["notas"])

    sin_cambios = repo.actualizar(cuenta_id)
    caso("actualizar() sin campos devuelve False", False, sin_cambios)

    print("\n--- archivar() (soft-delete) ---")
    repo.archivar(cuenta_id)
    listado_tras_archivar = repo.listar(solo_activas=True)
    caso(
        "tras archivar(), la cuenta desaparece del listado default (solo_activas=True)",
        False,
        cuenta_id in [r["id"] for r in listado_tras_archivar],
    )
    listado_todas = repo.listar(solo_activas=False)
    caso(
        "listar(solo_activas=False) sigue mostrando la cuenta archivada",
        True,
        cuenta_id in [r["id"] for r in listado_todas],
    )
    fila_archivada = repo.obtener_por_id(cuenta_id)
    caso("la fila sigue existiendo en la tabla tras archivar() (no es un DELETE)", True, fila_archivada is not None)
    caso("la fila archivada quedó con activa = 0", 0, fila_archivada["activa"] if fila_archivada else None)

    print("\n--- obtener_saldo() ---")
    cuenta_saldo_id = repo.crear(nombre="Cuenta Saldo Repo", tipo="efectivo", moneda_codigo="ARS", saldo_inicial=0.0)

    categoria = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'ingreso' LIMIT 1;")
    moneda = manager.fetchone("SELECT id, decimales FROM monedas WHERE codigo = 'ARS';")
    monto_ingreso = 500.0
    monto_minor = round(monto_ingreso * (10 ** moneda["decimales"]))
    manager.execute(
        """
        INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        ("2026-01-15", "Ingreso de prueba", cuenta_saldo_id, categoria["id"], moneda["id"], "ingreso", monto_minor),
    )

    saldo = repo.obtener_saldo(cuenta_saldo_id, "ARS")
    caso("obtener_saldo() refleja el movimiento insertado a mano", monto_ingreso, saldo)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
