"""
verify/schema/verify_schema_completo.py

Auditoría de cierre de la Fase 1: no agrega nada nuevo, solo confirma que
db/schema.sql + db/schema_migrations.py + db/seed.sql, aplicados juntos vía
DatabaseManager.inicializar(), producen una base de datos consistente:

- Todas las tablas de la lista mínima existen realmente en la dummy DB ya
  inicializada (se consulta sqlite_master directamente sobre la conexión, no
  se parsea el .sql crudo con regex — evita la clase de bug de falsos
  positivos/negativos que un parser de texto puede introducir, ej. matchear
  una palabra suelta dentro de un comentario).
- schema.sql + seed.sql se aplican sin romper ninguna CHECK constraint.
- PRAGMA foreign_keys está ON.
- PRAGMA foreign_key_check no encuentra violaciones de integridad referencial.
- Las columnas agregadas vía schema_migrations.py están presentes.
- inicializar() es idempotente de punta a punta (schema + migraciones + seed
  juntos), no solo el caso puntual de compras_cuotas que ya prueba
  verify_schema_migrations.py.
- Las vistas existen y son consultables.

Correlo con:
    python verify/schema/verify_schema_completo.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from db.schema_migrations import MIGRACIONES_COLUMNA

# Lista mínima pedida explícitamente. Se mantiene a mano — si se agregan
# tablas nuevas al schema, hay que agregarlas también acá. Se compara contra
# sqlite_master de la dummy DB ya inicializada, nunca contra un parseo del
# .sql crudo.
TABLAS_MINIMAS = [
    "monedas", "cuentas", "cuentas_saldos", "categorias", "empleos",
    "transacciones", "resumenes_tarjeta", "compras_cuotas", "cuotas_credito",
    "deudas", "presupuestos", "ingresos_proyectados",
    "recibos_sueldo", "descuentos_programados",
    "activos_financieros", "movimientos_activo", "objetivos_ahorro",
    "asignaciones", "hogares", "hogar_miembros", "gastos_compartidos",
    "prestamos", "cuotas_prestamo",
]  # tipos_cambio e indices_inflacion se eliminaron en v2 (DATA_MODEL_DECISIONS.md sección 38)

VISTAS_ESPERADAS = [
    "vw_balance_cuentas", "vw_cuotas_pendientes",
    "vw_saldo_neto_hogar",
]


def nombres_tablas_reales(conn) -> set[str]:
    filas = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';"
    ).fetchall()
    return {fila[0] for fila in filas}


def nombres_columnas(conn, tabla: str) -> list[str]:
    return [fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla});")]


# MIGRACIONES_COLUMNA es parte del baseline (v0 → v1) y describe columnas con
# su nombre de ESE momento. Migraciones numeradas posteriores renombraron o
# borraron algunas, así que en una base en la versión actual se chequea lo que
# quedó de cada una:
# - v2 borró la tabla resumen_cargos_extra (DATA_MODEL_DECISIONS.md sección 38)
#   → sus columnas ya no existen, se saltean.
# - v3 renombró toda columna moneda_id (INTEGER → monedas.id) a moneda_codigo
#   (TEXT → monedas.codigo) → se chequea moneda_codigo.
TABLAS_BORRADAS_POST_BASELINE = {"resumen_cargos_extra"}
COLUMNAS_RENOMBRADAS_POST_BASELINE = {"moneda_id": "moneda_codigo"}


def columnas_migradas_actuales() -> list[tuple[str, str]]:
    """(tabla, columna) de cada entrada de MIGRACIONES_COLUMNA, con el nombre
    que tiene en el schema actual; omite las de tablas ya borradas."""
    resultado = []
    for migracion in MIGRACIONES_COLUMNA:
        if migracion.tabla in TABLAS_BORRADAS_POST_BASELINE:
            continue
        columna = COLUMNAS_RENOMBRADAS_POST_BASELINE.get(migracion.columna, migracion.columna)
        resultado.append((migracion.tabla, columna))
    return resultado


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

    print("--- Creación de la dummy DB (schema.sql + seed.sql) ---")
    try:
        db_path = crear_dummy_db()
        creacion_ok = True
        creacion_excepcion = None
    except Exception as e:
        creacion_ok = False
        creacion_excepcion = e
        db_path = None

    caso(
        "schema.sql + seed.sql se aplican sin romper ninguna CHECK constraint",
        True,
        creacion_ok,
    )
    if not creacion_ok:
        print(f"   Excepción: {creacion_excepcion!r}")
        print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")
        return

    print(f"Dummy DB creada en: {db_path}\n")

    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    conn = manager.conn

    print("--- Tablas esperadas ---")
    tablas_reales = nombres_tablas_reales(conn)

    for tabla in TABLAS_MINIMAS:
        caso(f"tabla '{tabla}' existe (lista mínima pedida en la tarea)", True, tabla in tablas_reales)

    print("\n--- PRAGMA foreign_keys ---")
    fk_on = conn.execute("PRAGMA foreign_keys;").fetchone()[0]
    caso("PRAGMA foreign_keys está ON en la conexión", 1, fk_on)

    print("\n--- Integridad referencial (PRAGMA foreign_key_check) ---")
    violaciones_fk = conn.execute("PRAGMA foreign_key_check;").fetchall()
    caso("PRAGMA foreign_key_check no devuelve violaciones", [], violaciones_fk)

    print("\n--- Columnas agregadas vía schema_migrations.py ---")
    for tabla, columna in columnas_migradas_actuales():
        columnas = nombres_columnas(conn, tabla)
        caso(
            f"{tabla}.{columna} existe (agregada vía schema_migrations.py)",
            True,
            columna in columnas,
        )

    print("\n--- Segunda llamada a inicializar() (idempotencia completa: schema + migraciones + seed) ---")
    try:
        manager.inicializar()
        segunda_ok = True
        excepcion = None
    except Exception as e:
        segunda_ok = False
        excepcion = e
    caso("inicializar() no lanza excepción en la segunda llamada", True, segunda_ok)
    if not segunda_ok:
        print(f"   Excepción obtenida: {excepcion!r}")

    print("\n--- Tablas y columnas siguen consistentes tras la segunda inicializar() ---")
    tablas_reales_2 = nombres_tablas_reales(conn)
    for tabla in TABLAS_MINIMAS:
        caso(f"tabla '{tabla}' sigue existiendo tras la 2da inicializar()", True, tabla in tablas_reales_2)
    for tabla, columna in columnas_migradas_actuales():
        columnas_2 = nombres_columnas(conn, tabla)
        caso(
            f"{tabla}.{columna} sigue existiendo una sola vez tras la 2da inicializar()",
            1,
            columnas_2.count(columna),
        )

    print("\n--- Vistas consultables ---")
    for vista in VISTAS_ESPERADAS:
        try:
            conn.execute(f"SELECT * FROM {vista} LIMIT 1;").fetchall()
            consultable = True
        except Exception as e:
            consultable = False
            print(f"   Excepción consultando {vista}: {e!r}")
        caso(f"vista '{vista}' es consultable sin error", True, consultable)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
