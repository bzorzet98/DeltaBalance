"""
verify/compras_cuotas/verify_categorias_cargo_extra.py

Verifica el routing de cargos extra por categoría especial (Tarea 3 de
docs/PROXIMOS_PASOS.md): las tres categorías nuevas de db/seed.sql
("TARJETA DE CRÉDITO · Impuesto tarjeta / Recargo tarjeta /
Ajuste-Reintegro tarjeta"), su protección en
services/categorias_service.py CATEGORIAS_PROTEGIDAS, y el mapeo
services/fees_service.py CATEGORIAS_CARGO_EXTRA.

La decisión de ROUTEAR (create_purchase() vs. open_statement()+
add_extra_charge()) vive en ui/screens/compras_cuotas.py, no en ningún
service — este script no puede ejercitar ese código de UI directo (no es
un service), así que en su lugar:

1. Confirma que las 3 categorías quedaron sembradas por db/seed.sql (vía
   verify/_dummy_db.py, que siempre aplica seed.sql completo).
2. Confirma que las 3 están en CATEGORIAS_PROTEGIDAS (no se pueden
   renombrar/desactivar) y que CATEGORIAS_CARGO_EXTRA resuelve el
   charge_type correcto para cada una.
3. Para cada una de las 3, simula EXACTAMENTE la secuencia que
   ui/screens/compras_cuotas.py ejecuta cuando el usuario elige esa
   categoría en la fila de alta (open_statement() + add_extra_charge() con
   el charge_type mapeado) y confirma que el resumen se crea si no
   existía (open_statement() idempotente) y que el cargo queda con el
   tipo correcto.
4. Confirma que una categoría normal (no está en CATEGORIAS_CARGO_EXTRA)
   sigue creando una compra en cuotas normal vía create_purchase(), sin
   ningún cambio de comportamiento.
5. Confirma que create_purchase() sigue rechazando total_amount<=0
   (incluye negativos) con FeesError — el mismo guardrail de la capa de
   datos que ya existía antes de esta tarea, y que respalda la validación
   de UI de "monto negativo con categoría normal" (esa validación puntual
   vive en ui/screens/compras_cuotas.py, no es ejercitable acá, pero esta
   verificación confirma que aunque se la saltee, la capa de datos igual
   nunca deja pasar una compra con monto<=0).

Correlo con:
    python verify/compras_cuotas/verify_categorias_cargo_extra.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from services.categorias_service import CATEGORIAS_PROTEGIDAS
from services.fees_service import CATEGORIAS_CARGO_EXTRA, FeesError, FeesService

CATEGORIAS_ESPECIALES = [
    ("TARJETA DE CRÉDITO", "Impuesto tarjeta", "impuesto"),
    ("TARJETA DE CRÉDITO", "Recargo tarjeta", "recargo"),
    ("TARJETA DE CRÉDITO", "Ajuste/Reintegro tarjeta", "ajuste"),
]


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
            print(f"✅ {descripcion}")
        except Exception as err:
            print(f"❌ {descripcion} — se lanzó {type(err).__name__} en vez de {tipo_esperado.__name__}: {err}")

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    cuentas_repo = CuentasRepository(manager)
    svc = FeesService(manager)

    print("--- Parte A: categorías especiales sembradas + protegidas + mapeadas ---")

    ids_especiales: dict[str, int] = {}
    for principal, sub, charge_type_esperado in CATEGORIAS_ESPECIALES:
        fila = manager.fetchone(
            "SELECT id, tipo FROM categorias WHERE categoria_principal = ? AND subcategoria = ?;",
            (principal, sub),
        )
        caso(f"db/seed.sql sembró '{principal} · {sub}'", True, fila is not None)
        if fila is None:
            continue
        ids_especiales[sub] = fila["id"]
        caso(f"'{principal} · {sub}' es tipo='egreso'", "egreso", fila["tipo"])
        caso(f"'{principal} · {sub}' está en CATEGORIAS_PROTEGIDAS", True, (principal, sub) in CATEGORIAS_PROTEGIDAS)
        caso(
            f"CATEGORIAS_CARGO_EXTRA mapea '{principal} · {sub}' → '{charge_type_esperado}'",
            charge_type_esperado,
            CATEGORIAS_CARGO_EXTRA.get((principal, sub)),
        )

    cat_normal = manager.fetchone(
        "SELECT id FROM categorias WHERE categoria_principal = 'EGRESOS VARIABLES' AND subcategoria = 'Supermercado';"
    )["id"]
    caso(
        "Una categoría normal (Supermercado) NO está en CATEGORIAS_CARGO_EXTRA",
        None,
        CATEGORIAS_CARGO_EXTRA.get(("EGRESOS VARIABLES", "Supermercado")),
    )

    print("\n--- Parte B: routing simulado (lo que ui/screens/compras_cuotas.py ejecuta) ---")

    tarjeta = cuentas_repo.crear(nombre="Tarjeta Test", tipo="credito", moneda_codigo="ARS")

    # Las 3 categorías especiales caen en la MISMA cuenta/mes/año a
    # propósito: confirma que open_statement() resuelve/crea el resumen la
    # PRIMERA vez y reusa el mismo resumen (idempotente) las siguientes —
    # exactamente lo que pasaría si el usuario cargara varios cargos
    # extra distintos en el mismo resumen desde la fila de alta.
    statement_id_compartido = None
    for i, (principal, sub, charge_type) in enumerate(CATEGORIAS_ESPECIALES):
        print(f"\n  · {sub} (charge_type='{charge_type}')")
        # Mismo llamado exacto que ui/screens/compras_cuotas.py hace al
        # confirmar la fila de alta con esta categoría elegida.
        res_stmt = svc.open_statement(account_id=tarjeta, month=7, year=2026)
        ya_existia_esperado = i > 0
        caso(
            f"    open_statement() → already_existed={ya_existia_esperado} ({'reusa' if ya_existia_esperado else 'crea'} el resumen)",
            ya_existia_esperado,
            res_stmt.data["already_existed"],
        )
        if statement_id_compartido is None:
            statement_id_compartido = res_stmt.entity_id
        else:
            caso("    reusa el MISMO statement_id (no crea uno nuevo por cada cargo)", statement_id_compartido, res_stmt.entity_id)

        monto_prueba = 5000 if charge_type != "ajuste" else -2000
        res_cargo = svc.add_extra_charge(
            statement_id=res_stmt.entity_id,
            concept=f"Prueba {sub}",
            charge_type=charge_type,
            amount_minor=monto_prueba,
        )
        caso(f"    add_extra_charge() con charge_type='{charge_type}' devuelve success=True", True, res_cargo.success)

        cargos = svc.list_extra_charges(res_stmt.entity_id)
        cargo_creado = next((c for c in cargos if c["id"] == res_cargo.entity_id), None)
        caso(f"    el cargo creado tiene tipo='{charge_type}'", charge_type, cargo_creado["tipo"] if cargo_creado else None)
        caso(f"    el cargo creado tiene monto_minor={monto_prueba} (signo tal cual)", monto_prueba, cargo_creado["monto_minor"] if cargo_creado else None)

    print("\n--- Parte C: categoría normal sigue creando una compra ---")

    res_compra = svc.create_purchase(
        date_str="2026-07-10", concept="Compra normal", account_id=tarjeta,
        category_id=cat_normal, currency_code="ARS", total_amount=45000.0, total_fees=3,
    )
    caso("create_purchase() con categoría normal devuelve success=True", True, res_compra.success)
    compra = svc.get_purchase(res_compra.entity_id)
    caso("la compra quedó con la categoría normal elegida", cat_normal, compra["categoria_id"])
    caso("la compra generó 3 cuotas", 3, len(svc.get_fees_for_purchase(res_compra.entity_id)))

    print("\n--- Parte D: create_purchase() sigue rechazando total_amount<=0 (incluye negativos) ---")

    caso_excepcion(
        "create_purchase() con total_amount negativo sigue lanzando FeesError",
        FeesError,
        lambda: svc.create_purchase(
            date_str="2026-07-10", concept="Compra inválida", account_id=tarjeta,
            category_id=cat_normal, currency_code="ARS", total_amount=-100.0, total_fees=1,
        ),
    )
    caso_excepcion(
        "create_purchase() con total_amount=0 sigue lanzando FeesError",
        FeesError,
        lambda: svc.create_purchase(
            date_str="2026-07-10", concept="Compra inválida", account_id=tarjeta,
            category_id=cat_normal, currency_code="ARS", total_amount=0.0, total_fees=1,
        ),
    )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
