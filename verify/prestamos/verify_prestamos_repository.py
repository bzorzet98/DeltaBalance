"""
verify/prestamos/verify_prestamos_repository.py

Verifica PrestamosRepository (repositories/prestamos_repository.py):
crear(), obtener_por_id()/obtener_enriquecida() (JOIN a monedas + LEFT
JOIN a cuentas — confirma que un préstamo SIN cuenta_debito_id no se
pierde), listar()/listar_enriquecida() con filtros cruzados (estado,
tipo), actualizar() con el sentinel NO_CAMBIAR, cambiar_estado(), y la
atomicidad real de crear() + CuotasPrestamoRepository.crear_lote() (éxito
y rollback simulado a mitad de camino — mismo patrón que ya probamos en
compras_cuotas).

Correlo con:
    python verify/prestamos/verify_prestamos_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.prestamos_repository import PrestamosRepository
from repositories.cuotas_prestamo_repository import CuotasPrestamoRepository


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
    repo = PrestamosRepository(manager)
    cuotas_repo = CuotasPrestamoRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cuenta_1 = manager.fetchone("SELECT id FROM cuentas LIMIT 1;")["id"]

    print("--- crear() ---")
    prestamo_1 = repo.crear(
        entidad="Banco Galicia", tipo="hipotecario", capital_original_minor=5000000,
        tasa_anual_bp=4500, sistema_amortizacion="frances", moneda_id=moneda_ars,
        fecha_inicio="2026-01-01", plazo_meses=120, cuenta_debito_id=cuenta_1,
        notas="Hipoteca casa",
    )
    prestamo_2 = repo.crear(
        entidad="Banco Nación", tipo="prendario", capital_original_minor=2000000,
        tasa_anual_bp=3800, sistema_amortizacion="aleman", moneda_id=moneda_ars,
        fecha_inicio="2026-02-01", plazo_meses=48,
    )  # sin cuenta_debito_id, sin notas
    prestamo_3 = repo.crear(
        entidad="Banco Galicia", tipo="personal", capital_original_minor=500000,
        tasa_anual_bp=6000, sistema_amortizacion="frances", moneda_id=moneda_ars,
        fecha_inicio="2026-03-01", plazo_meses=12,
    )
    caso("crear() devuelve ids numéricos distintos", True, len({prestamo_1, prestamo_2, prestamo_3}) == 3)

    fila_1 = repo.obtener_por_id(prestamo_1)
    caso("crear() persiste entidad", "Banco Galicia", fila_1["entidad"])
    caso("crear() persiste capital_original_minor", 5000000, fila_1["capital_original_minor"])
    caso("crear() persiste tasa_anual_bp", 4500, fila_1["tasa_anual_bp"])
    caso("crear() deja estado='activo' por default de columna", "activo", fila_1["estado"])
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    fila_2 = repo.obtener_por_id(prestamo_2)
    caso("crear() sin cuenta_debito_id lo deja NULL", None, fila_2["cuenta_debito_id"])
    caso("crear() sin notas las deja NULL", None, fila_2["notas"])

    print("\n--- obtener_enriquecida() — JOIN a monedas + LEFT JOIN a cuentas ---")
    enriquecida_1 = repo.obtener_enriquecida(prestamo_1)
    caso("obtener_enriquecida() trae currency_code", "ARS", enriquecida_1["currency_code"])
    caso("obtener_enriquecida() trae account_name cuando SÍ hay cuenta_debito_id", True, enriquecida_1["account_name"] is not None)

    enriquecida_2 = repo.obtener_enriquecida(prestamo_2)
    caso("obtener_enriquecida() con cuenta_debito_id NULL: LEFT JOIN no pierde el préstamo", "Banco Nación", enriquecida_2["entidad"])
    caso("obtener_enriquecida() con cuenta_debito_id NULL: account_name es None", None, enriquecida_2["account_name"])
    caso("obtener_enriquecida() con cuenta_debito_id NULL: currency_code sigue presente (JOIN normal a monedas)", "ARS", enriquecida_2["currency_code"])
    caso("obtener_enriquecida() de un id inexistente devuelve None", None, repo.obtener_enriquecida(999999))

    print("\n--- listar() / listar_enriquecida() — filtros cruzados ---")
    solo_hipotecarios = repo.listar(tipo="hipotecario")
    caso("listar(tipo='hipotecario') trae exactamente prestamo_1", [prestamo_1], [p["id"] for p in solo_hipotecarios])

    todos_activos = repo.listar(estado="activo")
    caso(
        "listar(estado='activo') trae los 3 (default de columna)",
        True,
        all(p in [x["id"] for x in todos_activos] for p in (prestamo_1, prestamo_2, prestamo_3)),
    )

    enriquecida_hipotecarios = repo.listar_enriquecida(tipo="hipotecario")
    caso("listar_enriquecida(tipo='hipotecario') trae currency_code en la fila", "ARS", enriquecida_hipotecarios[0]["currency_code"])

    print("\n--- actualizar() — sentinel NO_CAMBIAR ---")
    repo.actualizar(prestamo_2, notas="Nota agregada después")
    fila_2_v2 = repo.obtener_por_id(prestamo_2)
    caso("actualizar(notas=...) reescribe notas", "Nota agregada después", fila_2_v2["notas"])
    caso("actualizar(notas=...) no toca cuenta_debito_id (sigue NULL)", None, fila_2_v2["cuenta_debito_id"])

    repo.actualizar(prestamo_2, cuenta_debito_id=cuenta_1)
    fila_2_v3 = repo.obtener_por_id(prestamo_2)
    caso("actualizar(cuenta_debito_id=...) vincula la cuenta", cuenta_1, fila_2_v3["cuenta_debito_id"])

    repo.actualizar(prestamo_2, cuenta_debito_id=None)
    fila_2_v4 = repo.obtener_por_id(prestamo_2)
    caso("actualizar(cuenta_debito_id=None) escribe NULL explícito (desvincula)", None, fila_2_v4["cuenta_debito_id"])
    caso("actualizar(cuenta_debito_id=None) no toca notas", "Nota agregada después", fila_2_v4["notas"])

    sin_cambios = repo.actualizar(prestamo_3)
    caso("actualizar() sin ningún campo devuelve False", False, sin_cambios)

    conn_externo = manager.conn
    with manager.transaction():
        repo.actualizar(prestamo_3, notas="Vía conn externo", conn=conn_externo)
    caso("actualizar(conn=...) persiste tras comitear", "Vía conn externo", repo.obtener_por_id(prestamo_3)["notas"])

    print("\n--- cambiar_estado() ---")
    repo.cambiar_estado(prestamo_3, "finalizado")
    caso("cambiar_estado() cambia el estado", "finalizado", repo.obtener_por_id(prestamo_3)["estado"])

    solo_finalizados = repo.listar(estado="finalizado")
    caso("listar(estado='finalizado') trae exactamente prestamo_3", [prestamo_3], [p["id"] for p in solo_finalizados])

    with manager.transaction():
        repo.cambiar_estado(prestamo_2, "cancelado", conn=conn_externo)
    caso("cambiar_estado(conn=...) persiste tras comitear", "cancelado", repo.obtener_por_id(prestamo_2)["estado"])

    # ============================================================
    # crear() + CuotasPrestamoRepository.crear_lote() — atomicidad real
    # ============================================================
    print("\n--- crear() + crear_lote() — atomicidad exitosa ---")
    with manager.transaction():
        prestamo_d = repo.crear(
            entidad="Banco Ciudad", tipo="personal", capital_original_minor=300000,
            tasa_anual_bp=5500, sistema_amortizacion="frances", moneda_id=moneda_ars,
            fecha_inicio="2026-04-01", plazo_meses=3, conn=conn_externo,
        )
        cuotas_ids = cuotas_repo.crear_lote(
            prestamo_id=prestamo_d,
            cuotas=[
                {"numero_cuota": 1, "monto_capital_minor": 95000, "monto_interes_minor": 5000, "monto_total_minor": 100000, "mes": 4, "anio": 2026},
                {"numero_cuota": 2, "monto_capital_minor": 96500, "monto_interes_minor": 3500, "monto_total_minor": 100000, "mes": 5, "anio": 2026},
                {"numero_cuota": 3, "monto_capital_minor": 98000, "monto_interes_minor": 2000, "monto_total_minor": 100000, "mes": 6, "anio": 2026},
            ],
            conn=conn_externo,
        )
    caso("crear() con conn en transacción exitosa: el préstamo persiste", "Banco Ciudad", repo.obtener_por_id(prestamo_d)["entidad"])
    caso("crear_lote() con conn en transacción exitosa: las 3 cuotas persisten", 3, len(cuotas_repo.listar_por_prestamo(prestamo_d)))
    caso("crear_lote() devuelve 3 ids", 3, len(cuotas_ids))

    print("\n--- crear() + crear_lote() — rollback simulado a mitad de camino ---")
    prestamos_antes = manager.fetchone("SELECT COUNT(*) AS n FROM prestamos;")["n"]
    cuotas_antes = manager.fetchone("SELECT COUNT(*) AS n FROM cuotas_prestamo;")["n"]
    try:
        with manager.transaction():
            prestamo_e = repo.crear(
                entidad="Préstamo que va a fallar", tipo="otro", capital_original_minor=100000,
                tasa_anual_bp=4000, sistema_amortizacion="aleman", moneda_id=moneda_ars,
                fecha_inicio="2026-05-01", plazo_meses=2, conn=conn_externo,
            )
            cuotas_repo.crear_lote(
                prestamo_id=prestamo_e,
                cuotas=[
                    {"numero_cuota": 1, "monto_capital_minor": 50000, "monto_interes_minor": 2000, "monto_total_minor": 52000, "mes": 5, "anio": 2026},
                ],
                conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    prestamos_despues = manager.fetchone("SELECT COUNT(*) AS n FROM prestamos;")["n"]
    cuotas_despues = manager.fetchone("SELECT COUNT(*) AS n FROM cuotas_prestamo;")["n"]
    caso("rollback revierte el INSERT del préstamo (no queda huérfano)", prestamos_antes, prestamos_despues)
    caso("rollback revierte también la cuota del lote (no queda huérfana)", cuotas_antes, cuotas_despues)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
