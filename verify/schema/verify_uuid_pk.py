"""
verify/schema/verify_uuid_pk.py

Verifica los ids UUID (docs/DATA_MODEL_DECISIONS.md sección 25), contra
DBs temporales — nunca contra data/:

A. Base nueva (db/schema.sql actual):
  - todas las tablas salvo monedas y los snapshots tienen `id` TEXT,
    PRIMARY KEY y NOT NULL;
  - el seed nace con UUIDs (DEFAULT de la columna) y "Caja Efectivo" con su
    saldo inicial enganchado por nombre;
  - los services/repositorios devuelven ids UUID v4 (str) y las filas
    hijas apuntan bien (cuotas → compra, deuda → transacción);
  - dos transacciones del mismo día salen en orden de alta (rowid), no al
    azar por UUID;
  - foreign_key_check vacío, integrity_check ok.
B. Guarda: una base con ids enteros frena inicializar() con un mensaje que
   dice qué script correr.
C. migration/migrar_a_uuid_pk.py de punta a punta sobre una base VIEJA
   armada acá (el schema.sql actual con los ids vueltos a INTEGER) con
   datos: mismas filas y sumas, todos los ids UUID, FKs traducidas
   (incluida la de una cuenta a otra), origen polimórfico de deudas y
   gastos compartidos traducido (pago_migrado → NULL), snapshots con su
   cuenta traducida, orden de alta conservado, sincronizado_en en NULL,
   foreign_key_check vacío.

Correlo con:
    python verify/schema/verify_uuid_pk.py
"""

from pathlib import Path
import re
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import SCHEMA_PATH, SEED_PATH, crear_dummy_db
from db.database import DatabaseManager
from db.schema_migrations import aplicar_migraciones_columna
from migration.migrar_a_uuid_pk import crear_destino, migrar, preparar_origen
from services.accounts_service import AccountsService
from services.debts_service import DebtsService
from services.fees_service import FeesService
from services.transaction_service import TransactionService

UUID_V4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
SIN_UUID = {"monedas", "saldos_mensuales", "deudas_mensuales", "compartidos_mensuales", "sync_cambios", "sync_estado"}

# Para armar la base VIEJA: el schema.sql actual con los ids vueltos a enteros.
ID_TEXTO = re.compile(r"^(\s*id\s+)TEXT PRIMARY KEY NOT NULL DEFAULT \(.*\),$", re.MULTILINE)
FK_TEXTO = re.compile(
    r"^(\s*)(cuenta_pago_id|cuenta_id|categoria_id|transaccion_salida_id|transaccion_entrada_id|compra_id|"
    r"resumen_id|origen_id|empleo_id|transaccion_id|recibo_id|activo_id|movimiento_id|objetivo_id|hogar_id|"
    r"gasto_compartido_id|cuenta_debito_id|prestamo_id)(\s+)TEXT\b",
    re.MULTILINE,
)
DDL_SALDOS_MENSUALES_VIEJA = """
CREATE TABLE saldos_mensuales (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    cuenta_id    INTEGER NOT NULL REFERENCES cuentas(id),
    moneda_id    INTEGER NOT NULL REFERENCES monedas(id),
    mes          INTEGER NOT NULL CHECK(mes BETWEEN 1 AND 12),
    anio         INTEGER NOT NULL,
    saldo_minor  INTEGER NOT NULL DEFAULT 0,
    calculado_en TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cuenta_id, moneda_id, mes, anio)
);
"""


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

    def es_uuid(valor) -> bool:
        return isinstance(valor, str) and bool(UUID_V4.match(valor))

    # ============================================================
    print("=== A. Base nueva ===\n")
    # ============================================================
    db_path = crear_dummy_db()
    print(f"Dummy DB: {db_path}\n")
    manager = DatabaseManager(db_path=db_path)
    manager.inicializar()
    tablas = [f["name"] for f in manager.fetchall("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';")]
    malas = []
    for tabla in tablas:
        columnas = {f["name"]: f for f in manager.fetchall(f"PRAGMA table_info({tabla});")}
        if tabla in SIN_UUID or "id" not in columnas:
            continue
        info = columnas["id"]
        if not (info["pk"] and (info["type"] or "").upper() == "TEXT" and info["notnull"]):
            malas.append(tabla)
    caso("todas las tablas con `id` (salvo monedas y snapshots) lo tienen TEXT, PRIMARY KEY y NOT NULL", [], malas)
    caso("monedas sigue con id entero", "INTEGER",
         next(f["type"] for f in manager.fetchall("PRAGMA table_info(monedas);") if f["name"] == "id").upper())
    categorias = manager.fetchall("SELECT id FROM categorias;")
    caso("el seed nace con UUIDs (DEFAULT de categorias.id)", True, bool(categorias) and all(es_uuid(f["id"]) for f in categorias))
    caja = manager.fetchone("SELECT id FROM cuentas WHERE nombre = 'Caja Efectivo';")
    caso("'Caja Efectivo' tiene un UUID", True, caja is not None and es_uuid(caja["id"]))
    caso("… y su saldo inicial apunta a ella (seed por nombre, no por id 1)", 1,
         manager.fetchone("SELECT COUNT(*) AS n FROM cuentas_saldos WHERE cuenta_id = ?;", (caja["id"],))["n"])

    cuentas = AccountsService(manager)
    transacciones = TransactionService(manager)
    fees = FeesService(manager)
    deudas = DebtsService(manager)
    ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    categoria = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]
    tarjeta = cuentas.create_account(nombre="Visa UUID", tipo="credito", monedas=[ars]).account_id
    caso("create_account() devuelve un UUID", True, es_uuid(tarjeta))
    primera = transacciones.create(
        date_str="2026-09-10", concept="Primera", account_id=caja["id"], category_id=categoria,
        currency_code="ARS", amount=100.0, movement_type="egreso",
    ).transaction_id
    segunda = transacciones.create(
        date_str="2026-09-10", concept="Segunda", account_id=caja["id"], category_id=categoria,
        currency_code="ARS", amount=200.0, movement_type="egreso",
    ).transaction_id
    caso("TransactionService.create() devuelve un UUID", True, es_uuid(primera))
    del_dia = [t["id"] for t in transacciones.list_transactions(date_from="2026-09-10", date_to="2026-09-10")]
    caso("mismo día: la más nueva primero (orden de alta, no por UUID)", [segunda, primera], del_dia[:2])
    compra = fees.create_purchase(
        date_str="2026-09-10", concept="Heladera", account_id=tarjeta, category_id=categoria,
        currency_code="ARS", total_amount=3000.0, total_fees=3,
    ).entity_id
    cuotas = fees.get_fees_for_purchase(compra)
    caso("create_purchase(): la compra y sus 3 cuotas con UUID", (True, 3, True),
         (es_uuid(compra), len(cuotas), all(es_uuid(c["id"]) for c in cuotas)))
    caso("las cuotas apuntan a la compra", {compra}, {c["compra_id"] for c in cuotas})
    deuda = deudas.create("Noe", "Cena", "me_deben", 5000, ars, "2026-09-10", origen_tipo="transaccion", origen_id=primera)
    fila_deuda = deudas.get(deuda.entity_id)
    caso("una deuda con origen transacción: UUID propio y origen_id = el UUID de la transacción", (True, primera),
         (es_uuid(deuda.entity_id), fila_deuda["origen_id"]))
    caso("foreign_key_check vacío", [], [tuple(f) for f in manager.fetchall("PRAGMA foreign_key_check;")])
    caso("integrity_check ok", "ok", manager.fetchone("PRAGMA integrity_check;")[0])
    manager.desconectar()

    # ============================================================
    print("\n=== B. Guarda: una base con ids enteros ===\n")
    # ============================================================
    with tempfile.TemporaryDirectory(prefix="verify_uuid_guarda_") as carpeta:
        vieja = sqlite3.connect(Path(carpeta) / "vieja.db")
        vieja.execute("CREATE TABLE transacciones (id INTEGER PRIMARY KEY AUTOINCREMENT, concepto TEXT);")
        try:
            aplicar_migraciones_columna(vieja)
            caso("inicializar() frena con ids enteros", "RuntimeError", "no frenó")
        except RuntimeError as err:
            caso("inicializar() frena con ids enteros (y el mensaje dice qué correr)", True, "migrar_a_uuid_pk.py" in str(err))
        finally:
            vieja.close()

    # ============================================================
    print("\n=== C. migration/migrar_a_uuid_pk.py sobre una base vieja ===\n")
    # ============================================================
    with tempfile.TemporaryDirectory(prefix="verify_uuid_migracion_") as carpeta:
        origen = Path(carpeta) / "vieja.db"
        destino = Path(carpeta) / "nueva.db"
        schema_viejo = FK_TEXTO.sub(r"\1\2\3INTEGER", ID_TEXTO.sub(r"\1INTEGER PRIMARY KEY AUTOINCREMENT,", SCHEMA_PATH.read_text(encoding="utf-8")))
        crudo = sqlite3.connect(origen)
        crudo.row_factory = sqlite3.Row
        crudo.executescript(schema_viejo)
        crudo.executescript(SEED_PATH.read_text(encoding="utf-8"))
        crudo.executescript(DDL_SALDOS_MENSUALES_VIEJA)
        caso("la base de prueba tiene ids enteros", "INTEGER",
             next(f["type"] for f in crudo.execute("PRAGMA table_info(transacciones);") if f["name"] == "id").upper())

        def insertar(sql: str, params: tuple) -> int:
            return crudo.execute(sql, params).lastrowid

        ars = crudo.execute("SELECT id FROM monedas WHERE codigo = 'ARS';").fetchone()[0]
        cat = crudo.execute("SELECT id FROM categorias WHERE tipo = 'egreso' ORDER BY id LIMIT 1;").fetchone()[0]
        caja = crudo.execute("SELECT id FROM cuentas WHERE nombre = 'Caja Efectivo';").fetchone()[0]
        visa = insertar("INSERT INTO cuentas (nombre, tipo, cuenta_pago_id) VALUES ('Visa', 'credito', ?);", (caja,))
        crudo.execute("INSERT INTO cuentas_saldos (cuenta_id, moneda_id) VALUES (?, ?);", (visa, ars))
        t1 = insertar(
            "INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor) "
            "VALUES ('2026-09-01', 'Uno', ?, ?, ?, 'egreso', 1000);", (caja, cat, ars),
        )
        t2 = insertar(
            "INSERT INTO transacciones (fecha, concepto, cuenta_id, categoria_id, moneda_id, tipo_movimiento, monto_minor) "
            "VALUES ('2026-09-01', 'Dos', ?, ?, ?, 'egreso', 2500);", (caja, cat, ars),
        )
        compra = insertar(
            "INSERT INTO compras_cuotas (fecha_compra, concepto, cuenta_id, categoria_id, moneda_id, monto_total_minor, "
            "total_cuotas, monto_por_cuota_minor) VALUES ('2026-09-02', 'Tele', ?, ?, ?, 9000, 3, 3000);", (visa, cat, ars),
        )
        cuotas_viejas = [
            insertar(
                "INSERT INTO cuotas_credito (compra_id, numero_cuota, mes_proyectado, anio_proyectado, monto_cuota_minor) "
                "VALUES (?, ?, ?, 2026, 3000);", (compra, n, 9 + n),
            )
            for n in (1, 2, 3)
        ]
        d_tx = insertar(
            "INSERT INTO deudas (entidad_persona, tab, monto_minor, moneda_id, fecha, origen_tipo, origen_id) "
            "VALUES ('NOE', 'me_deben', 500, ?, '2026-09-01', 'transaccion', ?);", (ars, t1),
        )
        d_pago = insertar(
            "INSERT INTO deudas (entidad_persona, tab, monto_minor, moneda_id, fecha, origen_tipo, origen_id) "
            "VALUES ('NOE', 'me_deben', -200, ?, '2026-09-05', 'pago_migrado', 99);", (ars,),
        )
        hogar = insertar("INSERT INTO hogares (codigo_invitacion, nombre) VALUES ('ABC123', 'Casa');", ())
        crudo.execute("INSERT INTO hogar_miembros (hogar_id, usuario_local) VALUES (?, 'bruno');", (hogar,))
        gasto = insertar(
            "INSERT INTO gastos_compartidos (hogar_id, pagador, origen_tipo, origen_id, categoria_id, monto_base_minor, "
            "coeficiente_deuda, monto_adeudado_minor, fecha) VALUES (?, 'bruno', 'cuota_credito', ?, ?, 3000, 50, 1500, '2026-10-01');",
            (hogar, cuotas_viejas[1], cat),
        )
        crudo.execute(
            "INSERT INTO gasto_compartido_pagos (gasto_compartido_id, transaccion_id, monto_aplicado_minor, tipo_pago, fecha) "
            "VALUES (?, ?, 500, 'transaccion', '2026-10-05');", (gasto, t2),
        )
        crudo.execute(
            "INSERT INTO saldos_mensuales (cuenta_id, moneda_id, mes, anio, saldo_minor) VALUES (?, ?, 8, 2026, 123);", (caja, ars),
        )
        crudo.commit()
        crudo.close()

        preparar_origen(origen)
        crear_destino(destino)
        reporte = migrar(origen, destino)
        caso("la verificación del script no encontró problemas", [], reporte.problemas)
        caso("se informa el origen pago_migrado que queda en NULL", 1,
             sum(1 for linea in reporte.referencias_nulas if "pago_migrado" in linea))

        viejo, nuevo = sqlite3.connect(origen), sqlite3.connect(destino)
        viejo.row_factory = nuevo.row_factory = sqlite3.Row
        try:
            for tabla in ("cuentas", "transacciones", "compras_cuotas", "cuotas_credito", "deudas", "gastos_compartidos",
                          "gasto_compartido_pagos", "hogar_miembros", "cuentas_saldos", "categorias", "saldos_mensuales"):
                caso(f"{tabla}: mismas filas", viejo.execute(f"SELECT COUNT(*) FROM {tabla};").fetchone()[0],
                     nuevo.execute(f"SELECT COUNT(*) FROM {tabla};").fetchone()[0])

            def por_concepto(concepto: str) -> sqlite3.Row:
                return nuevo.execute("SELECT * FROM transacciones WHERE concepto = ?;", (concepto,)).fetchone()

            uno, dos = por_concepto("Uno"), por_concepto("Dos")
            caja_nueva = nuevo.execute("SELECT id FROM cuentas WHERE nombre = 'Caja Efectivo';").fetchone()["id"]
            visa_nueva = nuevo.execute("SELECT * FROM cuentas WHERE nombre = 'Visa';").fetchone()
            caso("los ids nuevos son UUID v4", True, all(es_uuid(v) for v in (uno["id"], dos["id"], caja_nueva, visa_nueva["id"])))
            caso("transacción → su cuenta (traducida)", caja_nueva, uno["cuenta_id"])
            caso("cuenta → cuenta de pago (FK de una tabla a sí misma)", caja_nueva, visa_nueva["cuenta_pago_id"])
            caso("monto intacto", 2500, dos["monto_minor"])
            compra_nueva = nuevo.execute("SELECT id FROM compras_cuotas WHERE concepto = 'Tele';").fetchone()["id"]
            caso("las 3 cuotas → la compra", 3,
                 nuevo.execute("SELECT COUNT(*) FROM cuotas_credito WHERE compra_id = ?;", (compra_nueva,)).fetchone()[0])
            deuda_tx = nuevo.execute("SELECT * FROM deudas WHERE origen_tipo = 'transaccion';").fetchone()
            caso("deuda con origen transacción → el UUID de esa transacción", uno["id"], deuda_tx["origen_id"])
            caso("deuda pago_migrado (apuntaba a deuda_pagos) → origen_id NULL", None,
                 nuevo.execute("SELECT origen_id FROM deudas WHERE origen_tipo = 'pago_migrado';").fetchone()[0])
            cuota_2 = nuevo.execute(
                "SELECT id FROM cuotas_credito WHERE compra_id = ? AND numero_cuota = 2;", (compra_nueva,),
            ).fetchone()[0]
            gasto_nuevo = nuevo.execute("SELECT * FROM gastos_compartidos;").fetchone()
            caso("gasto compartido con origen cuota → el UUID de la cuota 2", cuota_2, gasto_nuevo["origen_id"])
            hogar_nuevo = nuevo.execute("SELECT id FROM hogares;").fetchone()[0]
            caso("gasto → su hogar y miembro → su hogar", (hogar_nuevo, hogar_nuevo),
                 (gasto_nuevo["hogar_id"], nuevo.execute("SELECT hogar_id FROM hogar_miembros;").fetchone()[0]))
            pago = nuevo.execute("SELECT * FROM gasto_compartido_pagos;").fetchone()
            caso("pago → su gasto y su transacción", (gasto_nuevo["id"], dos["id"]), (pago["gasto_compartido_id"], pago["transaccion_id"]))
            caso("snapshot (conserva su id entero) → la cuenta traducida", caja_nueva,
                 nuevo.execute("SELECT cuenta_id FROM saldos_mensuales;").fetchone()[0])
            caso("orden de alta conservado (rowid): 'Uno' antes que 'Dos'", ["Uno", "Dos"],
                 [f["concepto"] for f in nuevo.execute("SELECT concepto FROM transacciones ORDER BY rowid;")])
            caso("sincronizado_en en NULL en todas las transacciones (la sync arranca de cero)", 0,
                 nuevo.execute("SELECT COUNT(*) FROM transacciones WHERE sincronizado_en IS NOT NULL;").fetchone()[0])
            caso("sync_cambios vacía (la copia no se registró como cambios)", 0,
                 nuevo.execute("SELECT COUNT(*) FROM sync_cambios;").fetchone()[0])
            caso("foreign_key_check vacío", [], [tuple(f) for f in nuevo.execute("PRAGMA foreign_key_check;")])
            caso("integrity_check ok", "ok", nuevo.execute("PRAGMA integrity_check;").fetchone()[0])
        finally:
            viejo.close()
            nuevo.close()

        nueva_app = DatabaseManager(db_path=destino)
        try:
            nueva_app.inicializar()  # la guarda la deja pasar: ya tiene UUID
            caso("la base migrada abre con inicializar() (pasa la guarda)", True, True)
        except RuntimeError as err:
            caso("la base migrada abre con inicializar() (pasa la guarda)", True, f"falló: {err}")
        finally:
            nueva_app.desconectar()

    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
