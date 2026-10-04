"""
verify/compras_cuotas/verify_cargos_extra_sync.py

Verifica que los cargos extra de un resumen (impuestos, recargos,
ajustes/reintegros) vivan en compras_cuotas con es_cargo_extra = 1 — y por
eso se sincronicen — en vez de en resumen_cargos_extra, que no se
sincroniza (docs/DATA_MODEL_DECISIONS.md sección 28):

- Alta (FeesService.add_extra_charge()): una fila en compras_cuotas con
  es_cargo_extra = 1, total_cuotas = 1, la categoría especial de su tipo, y
  su cuota en cuotas_credito en el mes del resumen, ya incluida en él
  ('en_resumen'). Las dos quedan pendientes de subir (sincronizado_en NULL
  y anotadas en sync_cambios). Nada en resumen_cargos_extra.
- resumen_por_tarjeta() los suma aparte de las compras;
  close_statement() los cuenta como impuestos, no como consumos.
- Migración (db/schema_migrations.py migrar_cargos_extra_a_compras()): los
  cargos que ya estaban en resumen_cargos_extra pasan a compras_cuotas con
  su moneda y su fecha (o las deducidas), su cuota sigue al estado del
  resumen, el tipo 'otro' se queda donde estaba, y correrla de nuevo no
  duplica nada.
- Eliminación (delete_extra_charge()): borra la compra y su cuota mientras
  el resumen está abierto; con el resumen cerrado o pagado, no.

Correlo con:
    python verify/compras_cuotas/verify_cargos_extra_sync.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from db.schema_migrations import migrar_cargos_extra_a_compras
from repositories.cuentas_repository import CuentasRepository
from repositories.resumen_cargos_extra_repository import ResumenCargosExtraRepository
from services.fees_service import (
    FeesError,
    FeesService,
    StatementAlreadyClosedError,
    StatementAlreadyPaidError,
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

    def lanza(funcion, excepcion) -> bool:
        try:
            funcion()
        except excepcion:
            return True
        return False

    db_path = crear_dummy_db()
    print(f"Dummy DB creada en: {db_path}\n")
    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    cuentas_repo = CuentasRepository(manager)
    svc = FeesService(manager)

    usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]
    categoria_normal = manager.fetchone("SELECT id FROM categorias WHERE subcategoria = 'Supermercado';")["id"]

    def compra(compra_id: str) -> dict:
        fila = manager.fetchone(
            """
            SELECT pc.*, cat.subcategoria AS categoria, m.codigo AS moneda
            FROM compras_cuotas pc
            JOIN categorias cat ON cat.id = pc.categoria_id
            JOIN monedas m ON m.id = pc.moneda_id
            WHERE pc.id = ?;
            """,
            (compra_id,),
        )
        return dict(fila) if fila is not None else {}

    def cuotas(compra_id: str) -> list[dict]:
        return [dict(f) for f in manager.fetchall("SELECT * FROM cuotas_credito WHERE compra_id = ?;", (compra_id,))]

    def en_sync_cambios(tabla: str, clave: str) -> bool:
        return manager.fetchone(
            "SELECT 1 FROM sync_cambios WHERE tabla = ? AND clave = ? AND operacion = 'guardado';", (tabla, clave),
        ) is not None

    def cargos_viejos() -> list[str]:
        return [f["id"] for f in manager.fetchall("SELECT id FROM resumen_cargos_extra ORDER BY rowid;")]

    visa = cuentas_repo.crear(nombre="VISA", tipo="credito", moneda_codigo="ARS")
    resumen_julio = svc.open_statement(visa, 7, 2026).entity_id

    # ============================================================
    print("--- Alta de un cargo extra → compras_cuotas ---")
    # ============================================================
    paises = svc.add_extra_charge(
        resumen_julio, concept="IMPUESTO PAIS", charge_type="impuesto", amount_minor=30000,
        currency_code="ARS", date_str="2026-07-20",
    ).entity_id
    fila = compra(paises)
    caso("está en compras_cuotas con es_cargo_extra = 1", 1, fila.get("es_cargo_extra"))
    caso("… 1 cuota, total y cuota = el monto del cargo", (1, 30000, 30000),
         (fila.get("total_cuotas"), fila.get("monto_total_minor"), fila.get("monto_por_cuota_minor")))
    caso("… con la categoría especial de su tipo", "Impuesto tarjeta", fila.get("categoria"))
    caso("… su fecha y su moneda", ("2026-07-20", "ARS"), (fila.get("fecha_compra"), fila.get("moneda")))
    cuotas_paises = cuotas(paises)
    caso("tiene UNA cuota en cuotas_credito", 1, len(cuotas_paises))
    caso("… en el mes del resumen, por el mismo monto", (7, 2026, 30000), (
        cuotas_paises[0]["mes_proyectado"], cuotas_paises[0]["anio_proyectado"], cuotas_paises[0]["monto_cuota_minor"],
    ))
    caso("… ya incluida en el resumen ('en_resumen')", (resumen_julio, "en_resumen"),
         (cuotas_paises[0]["resumen_id"], cuotas_paises[0]["estado"]))
    caso("compra y cuota con sincronizado_en NULL (se suben en la próxima sync)", (None, None),
         (fila.get("sincronizado_en"), cuotas_paises[0]["sincronizado_en"]))
    caso("… y anotadas en sync_cambios", (True, True), (
        en_sync_cambios("compras_cuotas", paises), en_sync_cambios("cuotas_credito", cuotas_paises[0]["id"]),
    ))
    caso("nada en resumen_cargos_extra (deprecada)", [], cargos_viejos())

    sin_moneda = svc.add_extra_charge(resumen_julio, concept="RECARGO", charge_type="recargo", amount_minor=2000).entity_id
    caso("sin moneda: la única de la tarjeta (ARS)", "ARS", compra(sin_moneda).get("moneda"))
    caso("sin fecha: el día 1 del mes del resumen", "2026-07-01", compra(sin_moneda).get("fecha_compra"))
    caso("tipo 'otro' (sin categoría especial) → ValueError", True, lanza(lambda: svc.add_extra_charge(
        resumen_julio, concept="X", charge_type="otro", amount_minor=1,
    ), ValueError))

    print("\n--- Totales: los cargos aparte de las compras ---")
    heladera = svc.create_purchase(
        date_str="2026-07-02", concept="Heladera", account_id=visa, category_id=categoria_normal,
        currency_code="ARS", total_amount=1000.0, total_fees=1, first_fee_month=7, first_fee_year=2026,
    ).entity_id
    grupo = next(g for g in svc.resumen_por_tarjeta(7, 2026) if g["cuenta_id"] == visa)
    caso("resumen_por_tarjeta(): 100.000 de compras, 32.000 de cargos, 132.000 en total", (100000, 32000, 132000),
         (grupo["monto_cuotas_minor"], grupo["monto_cargos_extra_minor"], grupo["monto_total_minor"]))
    caso("list_extra_charges_in_month(): los 2 cargos de julio, con su tipo",
         {paises: "impuesto", sin_moneda: "recargo"},
         {c["id"]: c["tipo"] for c in svc.list_extra_charges_in_month(7, 2026)})
    caso("list_purchases_due_in_month() los trae marcados (la tabla los muestra)", {heladera: 0, paises: 1, sin_moneda: 1},
         {c["id"]: c["es_cargo_extra"] for c in svc.list_purchases_due_in_month(7, 2026)})

    # ============================================================
    print("\n--- Eliminación ---")
    # ============================================================
    svc.delete_extra_charge(sin_moneda)
    caso("con el resumen abierto: se borra la compra…", {}, compra(sin_moneda))
    caso("… y su cuota", [], cuotas(sin_moneda))
    caso("delete_extra_charge() sobre una compra normal → FeesError", True,
         lanza(lambda: svc.delete_extra_charge(heladera), FeesError))

    svc.confirm_fee(svc.get_fees_for_purchase(heladera)[0]["id"], resumen_julio)
    cierre = svc.close_statement(resumen_julio).data
    caso("close_statement(): la heladera es consumo y el cargo, impuesto", (100000, 30000, 3000), (
        cierre["monto_consumos_minor"], cierre["monto_impuestos_minor"], cierre["porcentaje_impuesto_bp"],
    ))
    caso("con el resumen cerrado: StatementAlreadyClosedError", True,
         lanza(lambda: svc.delete_extra_charge(paises), StatementAlreadyClosedError))
    caso("… y el cargo sigue ahí", 1, compra(paises).get("es_cargo_extra"))

    # ============================================================
    print("\n--- Migración de resumen_cargos_extra (cargos de la versión anterior) ---")
    # ============================================================
    viejo_repo = ResumenCargosExtraRepository(manager)
    resumen_agosto = svc.open_statement(visa, 8, 2026).entity_id
    sellos = viejo_repo.agregar(resumen_agosto, "SELLOS", "impuesto", 1500, moneda_id=usd, fecha="2026-08-10")
    reintegro = viejo_repo.agregar(resumen_agosto, "REINTEGRO VIEJO", "ajuste", -800)  # sin moneda ni fecha
    otro = viejo_repo.agregar(resumen_agosto, "OTRO VIEJO", "otro", 100)  # sin categoría especial
    resumen_junio = svc.open_statement(visa, 6, 2026).entity_id
    iva_pagado = viejo_repo.agregar(resumen_junio, "IVA", "impuesto", 2100)
    svc.pay_statement(resumen_junio, payment_date="2026-07-05", monto_pagado_minor=2100)

    migrar_cargos_extra_a_compras(manager.conn)

    fila = compra(sellos)
    caso("SELLOS pasó a compras_cuotas con el MISMO id", 1, fila.get("es_cargo_extra"))
    caso("… con su moneda (USD), su fecha y su categoría", ("USD", "2026-08-10", "Impuesto tarjeta"),
         (fila.get("moneda"), fila.get("fecha_compra"), fila.get("categoria")))
    caso("… y su cuota en agosto, en su resumen", (8, 2026, resumen_agosto, "en_resumen", 1500), tuple(
        cuotas(sellos)[0][k] for k in ("mes_proyectado", "anio_proyectado", "resumen_id", "estado", "monto_cuota_minor")
    ))
    fila = compra(reintegro)
    caso("REINTEGRO VIEJO (sin moneda ni fecha): ARS — la única de la tarjeta — y el día 1 del mes",
         ("ARS", "2026-08-01", -800), (fila.get("moneda"), fila.get("fecha_compra"), fila.get("monto_total_minor")))
    caso("… con la categoría de ajuste", "Ajuste/Reintegro tarjeta", fila.get("categoria"))
    caso("el cargo de un resumen pagado queda con su cuota 'pagado'", "pagado", cuotas(iva_pagado)[0]["estado"])
    caso("los migrados quedan pendientes de subir (sincronizado_en NULL)", (None, None),
         (compra(sellos).get("sincronizado_en"), cuotas(sellos)[0]["sincronizado_en"]))
    caso("… y anotados en sync_cambios", True, en_sync_cambios("compras_cuotas", sellos))
    caso("en resumen_cargos_extra queda solo el de tipo 'otro' (sin categoría especial)", [otro], cargos_viejos())
    caso("el de tipo 'otro' no se migró", {}, compra(otro))

    cantidad = manager.fetchone("SELECT COUNT(*) AS n FROM compras_cuotas WHERE es_cargo_extra = 1;")["n"]
    migrar_cargos_extra_a_compras(manager.conn)
    manager.inicializar()
    caso("correrla de nuevo (y volver a inicializar) no duplica nada", cantidad,
         manager.fetchone("SELECT COUNT(*) AS n FROM compras_cuotas WHERE es_cargo_extra = 1;")["n"])
    caso("los migrados de agosto aparecen en el mes", {sellos, reintegro},
         {c["id"] for c in svc.list_extra_charges_in_month(8, 2026)})
    caso("borrar un migrado de un resumen pagado → StatementAlreadyPaidError", True,
         lanza(lambda: svc.delete_extra_charge(iva_pagado), StatementAlreadyPaidError))
    svc.delete_extra_charge(reintegro)
    caso("borrar un migrado de un resumen abierto: se borra con su cuota", ({}, []), (compra(reintegro), cuotas(reintegro)))

    manager.desconectar()
    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
