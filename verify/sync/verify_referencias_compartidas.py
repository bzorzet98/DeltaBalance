"""
verify/sync/verify_referencias_compartidas.py

Verifica que los gastos compartidos viajen entre las bases de los miembros
del hogar aunque apunten a filas PRIVADAS de quien los cargó
(sync/referencias.py, docs/DATA_MODEL_DECISIONS.md sección 27). Tres bases
temporales — BRUNO, NOELIA y una tercera, CARLA — y un Supabase falso en
memoria (verify/sync/_fake_supabase.py, sin RLS ni red). Cada base nace con
su propio seed: "SUPERMERCADO" existe en las tres, con un UUID distinto en
cada una. Es el caso real que rompía con "FOREIGN KEY constraint failed".

Cubre:
  - Un gasto de NOELIA llega a BRUNO con la categoría DE BRUNO (misma
    clave natural, otro UUID).
  - Una categoría que BRUNO no tiene se crea en su base, INACTIVA y con un
    UUID propio, y después sube como categoría privada de BRUNO.
  - La pantalla de Compartidos de BRUNO (SharedExpensesService) los lista
    con su categoría.
  - Una sync sin cambios no vuelve a aplicar las filas traducidas.
  - Ida y vuelta: BRUNO paga el gasto de NOELIA; NOELIA recibe el pago y su
    gasto sigue con SU categoría.
  - FK que acepta NULL (gasto_compartido_pagos.transaccion_id): la
    transacción de NOELIA no existe en la base de BRUNO → NULL; si BRUNO
    vuelve a subir ese pago, NOELIA no pierde el vínculo.
  - Tercera integrante: CARLA baja todo desde cero, sin errores, cada
    gasto con categorías de SU base.
  - Las privadas viajan como siempre (sin _referencias).
  - Concepto del origen: un gasto de NOELIA sin descripción propia llega a
    BRUNO — que no tiene la transacción de origen — con el concepto de esa
    transacción como descripción (antes: "ORIGEN #… NO ENCONTRADO"); en la
    base de NOELIA la descripción sigue vacía, también después de que
    BRUNO vuelva a subir el gasto. Una fila subida sin el concepto la
    completa su autora en la reparación.
  - Filas viejas (subidas sin _referencias): error claro antes de la
    reparación; la reparación de su autor las completa y llegan. Y al
    revés: si la marca de bajada ya había pasado de largo, la primera
    corrida con esta versión rebaja las compartidas desde cero.

Correlo con:
    python verify/sync/verify_referencias_compartidas.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from verify.sync._fake_supabase import FakeSupabase, MarcasEnMemoria
from db.database import DatabaseManager
from services.categorias_service import CategoriasService
from services.shared_expenses_service import SharedExpensesService
from services.transaction_service import TransactionService
from sync.referencias import MARCA_CONCEPTO, MARCA_REFERENCIAS
from sync.sync_engine import REPARACION_CONCEPTOS, REPARACION_REFERENCIAS, SyncEngine

BRUNO = "f5ca8a42-a039-4a46-998e-2b01dbb28e9c"
NOELIA = "3cb96dd5-e56b-47b6-803f-63410d9c3bdd"
CARLA = "c4a2b9e0-0000-4000-8000-000000000003"  # inventado: solo para probar un tercer miembro
MARCA_EN_EL_FUTURO = "9999-12-31T00:00:00+00:00"


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

    fake = FakeSupabase()

    def base(nombre: str) -> DatabaseManager:
        path = crear_dummy_db()
        print(f"Dummy DB ({nombre}): {path}")
        manager = DatabaseManager(db_path=path)
        manager.inicializar()
        return manager

    def categoria_id(m: DatabaseManager, subcategoria: str) -> str:
        return m.fetchone("SELECT id FROM categorias WHERE subcategoria = ?;", (subcategoria,))["id"]

    def gasto(m: DatabaseManager, gasto_id: str) -> dict:
        fila = m.fetchone("SELECT * FROM gastos_compartidos WHERE id = ?;", (gasto_id,))
        return dict(fila) if fila is not None else {}

    def pago_local(m: DatabaseManager, pago_id: str) -> dict:
        fila = m.fetchone("SELECT * FROM gasto_compartido_pagos WHERE id = ?;", (pago_id,))
        return dict(fila) if fila is not None else {}

    def transaccion(m: DatabaseManager, categoria: str, monto_minor: int, fecha: str, concepto: str) -> str:
        caja = m.fetchone("SELECT id FROM cuentas WHERE nombre = 'Caja Efectivo';")["id"]
        return TransactionService(m).create(
            date_str=fecha, concept=concepto, account_id=caja, category_id=categoria,
            currency_code="ARS", amount=monto_minor / 100, movement_type="egreso",
        ).transaction_id

    def compartir(m: DatabaseManager, pagador: str, categoria: str, monto_minor: int, fecha: str, concepto: str) -> str:
        """Como el Registro: una transacción en la Caja Efectivo de esa base y un gasto compartido 50% sobre ella."""
        tx = transaccion(m, categoria, monto_minor, fecha, concepto)
        return SharedExpensesService(m).add_shared_expense(
            hogar_id, pagador, "transaccion", tx, categoria, monto_minor, 50.0, fecha, descripcion=concepto,
        ).entity_id

    # ============================================================
    print("--- Dos bases, un hogar ---")
    # ============================================================
    mb = base("BRUNO")
    marcas_b = MarcasEnMemoria()
    motor_b = SyncEngine(mb, None, cliente=fake, usuario_id=BRUNO, marcas=marcas_b)
    hogar = SharedExpensesService(mb).create_hogar("BRUNO", "CASA")
    hogar_id, codigo = hogar.entity_id, hogar.data["codigo_invitacion"]
    caso("BRUNO: primera sync OK", True, motor_b.sync_completo().success)

    mn = base("NOELIA")
    motor_n = SyncEngine(mn, None, cliente=fake, usuario_id=NOELIA, marcas=MarcasEnMemoria())
    caso("NOELIA: primera sync OK", True, motor_n.sync_completo().success)
    caso("NOELIA bajó el hogar de BRUNO", True,
         mn.fetchone("SELECT id FROM hogares WHERE id = ?;", (hogar_id,)) is not None)
    SharedExpensesService(mn).join_hogar(codigo, "NOELIA")
    super_b, super_n = categoria_id(mb, "SUPERMERCADO"), categoria_id(mn, "SUPERMERCADO")
    caso("'SUPERMERCADO' existe en las dos bases con UUID distinto (cada seed genera el suyo)", True, super_b != super_n)

    # ============================================================
    print("\n--- NOELIA comparte dos gastos: uno con una categoría que BRUNO también tiene, otro con una que no ---")
    # ============================================================
    g_super = compartir(mn, "NOELIA", super_n, 100000, "2026-09-10", "SUPER DE NOE")
    vet_n = CategoriasService(mn).create_category("EGRESOS VARIABLES", "Veterinaria", "egreso").categoria_id
    g_vet = compartir(mn, "NOELIA", vet_n, 50000, "2026-09-11", "VACUNA")
    caso("NOELIA: sync OK", True, motor_n.sync_completo().success)
    caso(f"en Supabase el gasto lleva {MARCA_REFERENCIAS}: la categoría descripta por nombre",
         {"categoria_principal": "EGRESOS", "subcategoria": "SUPERMERCADO", "tipo": "egreso"},
         fake.datos(g_super).get(MARCA_REFERENCIAS, {}).get("categoria_id"))

    r = motor_b.sync_completo()
    caso("BRUNO: sync sin errores (antes: FOREIGN KEY constraint failed)", 0, r.errores)
    caso("el gasto de NOELIA llegó a la base de BRUNO", True, bool(gasto(mb, g_super)))
    caso("… con la categoría 'SUPERMERCADO' DE BRUNO", super_b, gasto(mb, g_super).get("categoria_id"))
    fila_vet = mb.fetchone("SELECT * FROM categorias WHERE subcategoria = 'Veterinaria';")
    vet_b = dict(fila_vet) if fila_vet is not None else {}
    caso("la categoría que BRUNO no tenía se creó en su base", True, bool(vet_b))
    caso("… inactiva (no aparece para cargar)", 0, vet_b.get("activa"))
    caso("… con un UUID propio, no el de NOELIA", True, vet_b.get("id") not in (None, vet_n))
    caso("… y el gasto apunta a ella", vet_b.get("id"), gasto(mb, g_vet).get("categoria_id"))
    caso("… queda pendiente de subir (sincronizado_en NULL)", None, vet_b.get("sincronizado_en"))
    nombres = {g["id"]: g["category_name"] for g in SharedExpensesService(mb).list_shared_expenses(hogar_id)}
    caso("la pantalla de Compartidos de BRUNO los lista con su categoría",
         {g_super: "SUPERMERCADO", g_vet: "Veterinaria"}, nombres)

    motor_b.sync_completo()
    caso("la categoría creada subió como fila privada de BRUNO", BRUNO, fake.fila(vet_b.get("id", "")).get("usuario_id"))
    r = motor_b.sync_completo()
    caso("otra sync sin cambios: (subidas, bajadas, errores) = 0 — lo traducido no se reaplica",
         (0, 0, 0), (r.subidas, r.bajadas, r.errores))

    # ============================================================
    print("\n--- Ida y vuelta: BRUNO paga el gasto de NOELIA ---")
    # ============================================================
    SharedExpensesService(mb).aplicar_pago(g_super, hogar_id, 20000, "2026-09-15", tipo_pago="compensacion")
    motor_b.sync_completo()
    caso("BRUNO lo sube con SU categoría + la referencia por nombre", (super_b, "SUPERMERCADO"),
         (fake.datos(g_super).get("categoria_id"),
          fake.datos(g_super).get(MARCA_REFERENCIAS, {}).get("categoria_id", {}).get("subcategoria")))
    r = motor_n.sync_completo()
    caso("NOELIA: sync sin errores", 0, r.errores)
    caso("NOELIA recibe el pago de BRUNO (pendiente 50.000 − 20.000)", 30000, gasto(mn, g_super).get("monto_pendiente_minor"))
    caso("… y su gasto sigue con SU categoría", super_n, gasto(mn, g_super).get("categoria_id"))

    # ============================================================
    print("\n--- FK que acepta NULL: NOELIA paga un gasto de BRUNO con una transacción suya ---")
    # ============================================================
    g_bruno = compartir(mb, "BRUNO", super_b, 80000, "2026-09-12", "SUPER DE BRUNO")
    motor_b.sync_completo()
    motor_n.sync_completo()
    caso("el gasto de BRUNO llegó a NOELIA con la categoría de ella", super_n, gasto(mn, g_bruno).get("categoria_id"))
    tx_pago = transaccion(mn, super_n, 10000, "2026-09-16", "PAGO A BRUNO")
    pago = SharedExpensesService(mn).aplicar_pago(
        g_bruno, hogar_id, 10000, "2026-09-16", tipo_pago="transaccion", transaccion_id=tx_pago,
    ).data["pago_id"]
    motor_n.sync_completo()
    r = motor_b.sync_completo()
    caso("BRUNO: sync sin errores", 0, r.errores)
    caso("el pago de NOELIA llegó a BRUNO", True, bool(pago_local(mb, pago)))
    caso("… sin la transacción de NOELIA, que en la base de BRUNO no existe: NULL", None,
         pago_local(mb, pago).get("transaccion_id", "NO LLEGÓ"))

    # Una edición del pago en la base de BRUNO (no hay pantalla que lo haga
    # hoy; se simula con SQL para que el pago se vuelva a subir desde acá).
    mb.execute("UPDATE gasto_compartido_pagos SET notas = 'VISTO POR BRUNO' WHERE id = ?;", (pago,))
    motor_b.sync_completo()
    caso("BRUNO vuelve a subir el pago: en Supabase sigue la transacción de NOELIA", tx_pago,
         fake.datos(pago).get("transaccion_id"))
    motor_n.sync_completo()
    caso("NOELIA recibe la nota de BRUNO", "VISTO POR BRUNO", pago_local(mn, pago).get("notas"))
    caso("… y conserva el vínculo con su transacción", tx_pago, pago_local(mn, pago).get("transaccion_id"))

    # ============================================================
    print("\n--- Tercera integrante: CARLA baja todo desde cero ---")
    # ============================================================
    mc = base("CARLA")
    motor_c = SyncEngine(mc, None, cliente=fake, usuario_id=CARLA, marcas=MarcasEnMemoria())
    r = motor_c.sync_completo()
    caso("CARLA: primera sync sin errores", 0, r.errores)
    gastos_c = [dict(f) for f in mc.fetchall("SELECT id, categoria_id FROM gastos_compartidos;")]
    caso("CARLA tiene los 3 gastos del hogar", {g_super, g_vet, g_bruno}, {g["id"] for g in gastos_c})
    ids_categorias_c = {f["id"] for f in mc.fetchall("SELECT id FROM categorias;")}
    caso("… todos apuntando a categorías de SU base", True, all(g["categoria_id"] in ids_categorias_c for g in gastos_c))
    caso("… 'SUPERMERCADO' traducido a la de CARLA", categoria_id(mc, "SUPERMERCADO"), gasto(mc, g_super).get("categoria_id"))
    caso("… y los 2 pagos", 2, mc.fetchone("SELECT COUNT(*) AS n FROM gasto_compartido_pagos;")["n"])

    caso("las privadas viajan como siempre: la categoría de NOELIA, en deltabalance_filas y sin referencias",
         (NOELIA, False), (fake.fila(vet_n).get("usuario_id"), MARCA_REFERENCIAS in fake.datos(vet_n)))

    # ============================================================
    print("\n--- Concepto del origen: un gasto de NOELIA sin descripción propia ---")
    # ============================================================
    tx_farmacia = transaccion(mn, super_n, 40000, "2026-09-22", "FARMACIA DE NOE")
    g_farmacia = SharedExpensesService(mn).add_shared_expense(
        hogar_id, "NOELIA", "transaccion", tx_farmacia, super_n, 40000, 50.0, "2026-09-22",
    ).entity_id
    motor_n.sync_completo()
    caso(f"en Supabase el gasto lleva {MARCA_CONCEPTO}: el concepto de la transacción de NOELIA, sin descripción",
         ("FARMACIA DE NOE", None), (fake.datos(g_farmacia).get(MARCA_CONCEPTO), fake.datos(g_farmacia).get("descripcion")))
    r = motor_b.sync_completo()
    caso("BRUNO: sync sin errores", 0, r.errores)
    caso("en la base de BRUNO (sin esa transacción) la descripción es el concepto — no 'ORIGEN #… NO ENCONTRADO'",
         "FARMACIA DE NOE", gasto(mb, g_farmacia).get("descripcion"))
    caso("en la base de NOELIA sigue sin descripción (ve el concepto vivo de su transacción)",
         None, gasto(mn, g_farmacia).get("descripcion"))
    r = motor_b.sync_completo()
    caso("otra sync de BRUNO sin cambios: (subidas, bajadas, errores) = 0 — la descripción completada no se reaplica",
         (0, 0, 0), (r.subidas, r.bajadas, r.errores))

    SharedExpensesService(mb).aplicar_pago(g_farmacia, hogar_id, 5000, "2026-09-23", tipo_pago="compensacion")
    motor_b.sync_completo()
    caso("BRUNO le registra un pago y lo vuelve a subir: en Supabase, descripción vacía y el concepto de NOELIA",
         (None, "FARMACIA DE NOE"),
         (fake.datos(g_farmacia).get("descripcion"), fake.datos(g_farmacia).get(MARCA_CONCEPTO)))
    motor_n.sync_completo()
    caso("NOELIA recibe el pago (20.000 − 5.000) y su gasto sigue sin descripción", (15000, None),
         (gasto(mn, g_farmacia).get("monto_pendiente_minor"), gasto(mn, g_farmacia).get("descripcion")))

    # Como lo dejó subido una versión anterior de la app: sin el concepto.
    datos_sin_concepto = fake.datos(g_farmacia)
    datos_sin_concepto.pop(MARCA_CONCEPTO, None)
    fake.reescribir_datos(g_farmacia, datos_sin_concepto)
    mn.execute("DELETE FROM sync_estado WHERE clave = ?;", (REPARACION_CONCEPTOS,))  # NOELIA abre la versión nueva
    r = motor_n.sync_completo()
    caso("una fila subida sin el concepto: NOELIA, su autora, lo completa en la reparación", (0, "FARMACIA DE NOE"),
         (r.errores, fake.datos(g_farmacia).get(MARCA_CONCEPTO)))

    # ============================================================
    print("\n--- Filas viejas: subidas antes de que existiera _referencias ---")
    # ============================================================
    g_viejo = compartir(mn, "NOELIA", super_n, 30000, "2026-09-20", "SUPER VIEJO")
    motor_n.sync_completo()
    # Como lo dejó subido una versión anterior de la app: sin la marca.
    datos_viejos = fake.datos(g_viejo)
    datos_viejos.pop(MARCA_REFERENCIAS, None)
    fake.reescribir_datos(g_viejo, datos_viejos)
    # NOELIA todavía no abrió la versión nueva: su base no hizo la reparación.
    mn.execute("DELETE FROM sync_estado WHERE clave = ?;", (REPARACION_REFERENCIAS,))

    r = motor_b.sync_completo()
    caso("BRUNO: el gasto viejo no se puede traducir → 1 error (no se inventa ninguna categoría)", 1, r.errores)
    print(f"   mensaje: {r.mensaje}")
    caso("… y no está en su base", {}, gasto(mb, g_viejo))

    r = motor_n.sync_completo()
    caso("NOELIA (primera corrida de la versión nueva): sync OK", True, r.success)
    caso(f"… completó {MARCA_REFERENCIAS} del gasto viejo en Supabase", "SUPERMERCADO",
         fake.datos(g_viejo).get(MARCA_REFERENCIAS, {}).get("categoria_id", {}).get("subcategoria"))
    caso("… y anotó la reparación en sync_estado", True,
         mn.fetchone("SELECT valor FROM sync_estado WHERE clave = ?;", (REPARACION_REFERENCIAS,)) is not None)
    r = motor_b.sync_completo()
    caso("BRUNO lo baja en su próxima sync (la reparación cambió su updated_at), con su categoría",
         super_b, gasto(mb, g_viejo).get("categoria_id"))
    caso("… sin errores", 0, r.errores)

    # ============================================================
    print("\n--- Al revés: la marca de bajada de BRUNO ya había pasado de largo una fila ---")
    # ============================================================
    g_perdido = compartir(mn, "NOELIA", super_n, 25000, "2026-09-21", "SUPER PERDIDO")
    motor_n.sync_completo()
    # Lo que pasaba antes de esta versión: la fila falló por FK y la marca
    # de bajada siguió de largo. Se simula moviendo la marca al futuro.
    for clave in marcas_b.marcas:
        marcas_b.marcas[clave] = MARCA_EN_EL_FUTURO
    motor_b.sync_completo()
    caso("con la marca más adelante que la fila, BRUNO no la ve", {}, gasto(mb, g_perdido))
    mb.execute("DELETE FROM sync_estado WHERE clave = ?;", (REPARACION_REFERENCIAS,))  # BRUNO abre la versión nueva
    r = motor_b.sync_completo()
    caso("la primera corrida de la versión nueva rebaja las compartidas desde cero y la recupera",
         super_b, gasto(mb, g_perdido).get("categoria_id"))
    caso("… sin errores", 0, r.errores)

    mb.desconectar()
    mn.desconectar()
    mc.desconectar()
    print(f"\n{casos_ok}/{casos_total} casos OK")


if __name__ == "__main__":
    main()
