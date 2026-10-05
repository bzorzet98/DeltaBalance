"""
verify/compras_cuotas/verify_tarjetas_resumenes.py

Verifica las fechas reales de cierre / vencimiento por resumen
(tarjetas_resumenes, docs/DATA_MODEL_DECISIONS.md sección 29) en
FeesService:

- Sin fecha real, get_fecha_cierre() / get_fecha_vencimiento() dan la
  calculada desde los días default de tarjetas_config (igual que antes).
- set_fechas_resumen() guarda la real de un resumen, cada fecha por
  separado (una no pisa a la otra), y vacía vuelve a la calculada. Rechaza
  un vencimiento que no es posterior al cierre sin guardar nada.
- La fecha real manda en card_cycle_dates() / card_cycle_periods() (qué
  resumen es el ACTUAL) y en suggest_first_fee() (en qué resumen cae una
  compra → su 1ª cuota).
- Una tarjeta sin días default puede tener fechas reales sueltas.
- Se sincroniza: está en TABLAS_SINCRONIZADAS, tiene sincronizado_en y sus
  triggers, y cada fecha guardada queda pendiente de subir (sync_cambios);
  una que se rechaza no deja nada pendiente.
- Regresión: guardar dos veces los días default de una tarjeta sin
  sincronizar en el medio ya no rompe con "UNIQUE constraint failed:
  sync_cambios.tabla, sync_cambios.clave" (triggers de sync con DELETE +
  INSERT, db/schema_migrations.py _ddl_triggers_sync()).

El comportamiento sin fechas reales lo sigue cubriendo
verify_tarjetas_y_cronograma.py.

Correlo con:
    python verify/compras_cuotas/verify_tarjetas_resumenes.py
"""

from pathlib import Path
import sqlite3
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from db.schema_migrations import TABLAS_SINCRONIZADAS
from repositories.cuentas_repository import CuentasRepository
from services.fees_service import FeesError, FeesService


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

    def filas_de(cuenta_id: str) -> int:
        return manager.fetchone("SELECT COUNT(*) AS n FROM tarjetas_resumenes WHERE cuenta_id = ?;", (cuenta_id,))["n"]

    def pendientes_de_subir() -> int:
        return manager.fetchone("SELECT COUNT(*) AS n FROM sync_cambios WHERE tabla = 'tarjetas_resumenes';")["n"]

    tablas = {f["name"] for f in manager.fetchall("SELECT name FROM sqlite_master WHERE type = 'table';")}
    caso("inicializar() crea tarjetas_resumenes", True, "tarjetas_resumenes" in tablas)

    visa = cuentas_repo.crear(nombre="VISA", tipo="credito", moneda_codigo="ARS")
    svc.set_card_config(visa, 15, 5)

    # ============================================================
    print("\n--- Sin fecha real: la calculada desde el día default ---")
    # ============================================================
    caso("cierre de 09/2026: el 15", "2026-09-15", svc.get_fecha_cierre(visa, 9, 2026))
    caso("vencimiento de 09/2026: el 5 del mes siguiente", "2026-10-05", svc.get_fecha_vencimiento(visa, 9, 2026))
    caso("card_cycle_dates() mantiene su forma", {"cierre": "2026-09-15", "vencimiento": "2026-10-05"},
         svc.card_cycle_dates(visa, "2026-09-30")["actual"])
    actual = svc.card_cycle_periods(visa, "2026-09-30")["actual"]
    caso("card_cycle_periods(): el ACTUAL es 09/2026, con fechas calculadas", (9, 2026, False, False), (
        actual["mes"], actual["anio"], actual["cierre_especifica"], actual["vencimiento_especifica"],
    ))
    caso("el 13/09 el ACTUAL todavía es el de agosto (cierra el 15)", "2026-08-15",
         svc.card_cycle_dates(visa, "2026-09-13")["actual"]["cierre"])
    caso("una compra del 14/10 → 1ª cuota en 11/2026 (cierra el 15/10)", (11, 2026),
         svc.suggest_first_fee(visa, "2026-10-14"))

    # ============================================================
    print("\n--- Fecha real: overridea la calculada ---")
    # ============================================================
    caso("set_fechas_resumen(cierre 12/09) → success", True,
         svc.set_fechas_resumen(visa, 9, 2026, closing_date="2026-09-12").success)
    caso("get_fecha_cierre() devuelve la real", "2026-09-12", svc.get_fecha_cierre(visa, 9, 2026))
    caso("el vencimiento sigue calculado (el 5 después del cierre real)", "2026-10-05",
         svc.get_fecha_vencimiento(visa, 9, 2026))
    actual = svc.card_cycle_periods(visa, "2026-09-30")["actual"]
    caso("card_cycle_periods() la marca como real (el vencimiento no)", ("2026-09-12", True, False),
         (actual["cierre"], actual["cierre_especifica"], actual["vencimiento_especifica"]))
    caso("con el cierre real del 12/09, el 13/09 el ACTUAL ya es el de septiembre", "2026-09-12",
         svc.card_cycle_dates(visa, "2026-09-13")["actual"]["cierre"])

    svc.set_fechas_resumen(visa, 9, 2026, due_date="2026-10-07")
    caso("guardar el vencimiento no pisa el cierre real", ("2026-09-12", "2026-10-07"),
         (svc.get_fecha_cierre(visa, 9, 2026), svc.get_fecha_vencimiento(visa, 9, 2026)))
    caso("… y es una sola fila para ese resumen", 1, filas_de(visa))
    caso("los otros resúmenes siguen calculados", ("2026-10-15", "2026-11-05"),
         (svc.get_fecha_cierre(visa, 10, 2026), svc.get_fecha_vencimiento(visa, 10, 2026)))

    svc.set_fechas_resumen(visa, 9, 2026, closing_date=None)
    caso("cierre vacío (None): vuelve al calculado; el vencimiento real queda", ("2026-09-15", "2026-10-07"),
         (svc.get_fecha_cierre(visa, 9, 2026), svc.get_fecha_vencimiento(visa, 9, 2026)))

    # ============================================================
    print("\n--- Validaciones ---")
    # ============================================================
    pendientes_antes = pendientes_de_subir()
    caso("vencimiento antes del cierre (10/10 con cierre el 15/10) → FeesError", True,
         lanza(lambda: svc.set_fechas_resumen(visa, 10, 2026, due_date="2026-10-10"), FeesError))
    caso("… y no guardó nada (sigue la calculada, sin fila nueva)", ("2026-11-05", 1),
         (svc.get_fecha_vencimiento(visa, 10, 2026), filas_de(visa)))
    caso("… ni dejó nada pendiente de subir", pendientes_antes, pendientes_de_subir())
    caso("fecha inválida → ValueError", True,
         lanza(lambda: svc.set_fechas_resumen(visa, 10, 2026, closing_date="2026-13-01"), ValueError))
    caso("mes 13 → FeesError", True, lanza(lambda: svc.set_fechas_resumen(visa, 13, 2026, closing_date=None), FeesError))
    debito = cuentas_repo.crear(nombre="DEBITO", tipo="debito", moneda_codigo="ARS")
    caso("una cuenta que no es tarjeta de crédito → FeesError", True,
         lanza(lambda: svc.set_fechas_resumen(debito, 10, 2026, closing_date="2026-10-12"), FeesError))

    # ============================================================
    print("\n--- La 1ª cuota sugerida usa la fecha real ---")
    # ============================================================
    svc.set_fechas_resumen(visa, 10, 2026, closing_date="2026-10-12")
    caso("cierre real 12/10: una compra del 14/10 ya cae en el resumen de noviembre → 12/2026", (12, 2026),
         svc.suggest_first_fee(visa, "2026-10-14"))
    caso("… una del mismo 12/10, todavía en el de octubre → 11/2026", (11, 2026),
         svc.suggest_first_fee(visa, "2026-10-12"))
    svc.set_fechas_resumen(visa, 11, 2026, closing_date="2026-11-18")
    caso("cierre real 18/11 (default 15): una compra del 17/11 entra en noviembre → 12/2026", (12, 2026),
         svc.suggest_first_fee(visa, "2026-11-17"))
    svc.set_fechas_resumen(visa, 12, 2026, closing_date="2027-01-02")
    caso("el resumen de diciembre cierra el 02/01: una compra del 01/01 entra ahí → 01/2027", (1, 2027),
         svc.suggest_first_fee(visa, "2027-01-01"))

    # ============================================================
    print("\n--- Tarjeta sin días default ---")
    # ============================================================
    master = cuentas_repo.crear(nombre="MASTER", tipo="credito", moneda_codigo="ARS")
    caso("sin config ni fecha real: None", (None, None),
         (svc.get_fecha_cierre(master, 10, 2026), svc.get_fecha_vencimiento(master, 10, 2026)))
    svc.set_fechas_resumen(master, 10, 2026, closing_date="2026-10-20")
    caso("con una fecha real suelta: esa, y el vencimiento sin calcular", ("2026-10-20", None),
         (svc.get_fecha_cierre(master, 10, 2026), svc.get_fecha_vencimiento(master, 10, 2026)))
    caso("la sugerencia la usa (compra del 19/10 → 11/2026)", (11, 2026), svc.suggest_first_fee(master, "2026-10-19"))
    caso("sin días default no hay ciclo que mostrar", None, svc.card_cycle_dates(master))

    # ============================================================
    print("\n--- Sincronización con Supabase ---")
    # ============================================================
    caso("tarjetas_resumenes está en TABLAS_SINCRONIZADAS, después de tarjetas_config", True,
         TABLAS_SINCRONIZADAS.index("tarjetas_resumenes") == TABLAS_SINCRONIZADAS.index("tarjetas_config") + 1)
    columnas = {f["name"] for f in manager.fetchall("PRAGMA table_info(tarjetas_resumenes);")}
    caso("tiene la columna sincronizado_en", True, "sincronizado_en" in columnas)
    triggers = manager.fetchone(
        "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'trg_sync_tarjetas_resumenes_%';",
    )["n"]
    caso("y sus 3 triggers de sync (alta, cambio, borrado)", 3, triggers)
    svc.set_fechas_resumen(visa, 3, 2027, closing_date="2027-03-13")
    fila = manager.fetchone(
        "SELECT id, sincronizado_en FROM tarjetas_resumenes WHERE cuenta_id = ? AND mes = 3 AND anio = 2027;", (visa,),
    )
    caso("una fecha recién guardada queda con sincronizado_en NULL", None, fila["sincronizado_en"])
    caso("… y anotada en sync_cambios para la próxima subida", "guardado", (manager.fetchone(
        "SELECT operacion FROM sync_cambios WHERE tabla = 'tarjetas_resumenes' AND clave = ?;", (fila["id"],),
    ) or {"operacion": None})["operacion"])

    # ============================================================
    print("\n--- Guardar dos veces sin sincronizar en el medio ---")
    # ============================================================
    # El error real: GUARDAR DÍAS DEFAULT sobre una tarjeta cuyo cambio
    # anterior todavía no subió → "UNIQUE constraint failed:
    # sync_cambios.tabla, sync_cambios.clave". El upsert de tarjetas_config
    # (ON CONFLICT DO UPDATE) le imponía ABORT al INSERT OR REPLACE del
    # trigger; ahora el trigger hace DELETE + INSERT.
    config_id = manager.fetchone("SELECT id FROM tarjetas_config WHERE cuenta_id = ?;", (visa,))["id"]

    def anotaciones_config() -> list[str]:
        return [f["operacion"] for f in manager.fetchall(
            "SELECT operacion FROM sync_cambios WHERE tabla = 'tarjetas_config' AND clave = ?;", (config_id,),
        )]

    def guardar_dias_de_nuevo() -> Any:
        try:
            return svc.set_card_config(visa, 16, 6).success
        except sqlite3.IntegrityError as err:
            return f"IntegrityError: {err}"

    caso("los días default de VISA tienen un cambio pendiente de subir", ["guardado"], anotaciones_config())
    caso("volver a guardarlos (upsert sobre una fila con cambio pendiente) no rompe", True, guardar_dias_de_nuevo())
    caso("… quedan los días nuevos", (16, 6), (
        svc.get_card_config(visa)["dia_cierre"], svc.get_card_config(visa)["dia_vencimiento"],
    ))
    caso("… y sigue habiendo UNA anotación pendiente", ["guardado"], anotaciones_config())
    sql_trigger = manager.fetchone(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = 'trg_sync_tarjetas_config_update';",
    )["sql"]
    caso("el trigger de la base ya es el nuevo (sin INSERT OR REPLACE)", False, "INSERT OR REPLACE" in sql_trigger)

    manager.desconectar()
    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
