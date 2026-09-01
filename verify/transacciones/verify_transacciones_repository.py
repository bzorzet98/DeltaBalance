"""
verify/transacciones/verify_transacciones_repository.py

Verifica TransaccionesRepository (repositories/transacciones_repository.py):
crear, obtener_por_id, listar respetando cada filtro por separado (fecha,
cuenta_id, categoria_id, tipo_movimiento), actualizar, y que eliminar()/
restaurar() son un soft-delete real vía deleted_at (nunca un DELETE físico).

También verifica el bloque agregado en Fase 2 (TRANSACCIONES paso 3):
obtener_enriquecida()/listar_enriquecida() (shape con JOINs, réplica de lo
que antes vivía a mano en TransactionService), crear(conn=...) participando
de una transacción externa (incluyendo que un rollback a mitad de camino
revierte ambas altas), y el sentinel NO_CAMBIAR de actualizar() distinguiendo
"no tocar" de "escribir NULL a propósito".

Y la resolución perezosa de moneda agregada en la Tarea 6f
(docs/PROXIMOS_PASOS.md): crear() en una cuenta+moneda sin fila en
cuentas_saldos la genera sola en 0 antes de aplicar la transacción (tanto
sin conn como con conn externo), y una segunda transacción en la misma
combinación no duplica esa fila (reusa la existente).

Correlo con:
    python verify/transacciones/verify_transacciones_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.cuentas_repository import CuentasRepository
from repositories.transacciones_repository import TransaccionesRepository, NO_CAMBIAR


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
    repo = TransaccionesRepository(manager)

    # --- Datos de apoyo: dos cuentas y dos categorías de tipo distinto ---
    cuenta_a = cuentas_repo.crear(nombre="Cuenta A Repo TX", tipo="efectivo", moneda_codigo="ARS")
    cuenta_b = cuentas_repo.crear(nombre="Cuenta B Repo TX", tipo="efectivo", moneda_codigo="ARS")
    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    cat_ingreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'ingreso' LIMIT 1;")["id"]
    cat_egreso = manager.fetchone("SELECT id FROM categorias WHERE tipo = 'egreso' LIMIT 1;")["id"]

    print("--- crear() ---")
    tx_a = repo.crear(
        fecha="2026-01-10",
        concepto="Test A",
        cuenta_id=cuenta_a,
        categoria_id=cat_ingreso,
        moneda_id=moneda_ars,
        tipo_movimiento="ingreso",
        monto_minor=100000,
    )
    tx_b = repo.crear(
        fecha="2026-02-15",
        concepto="Test B",
        cuenta_id=cuenta_b,
        categoria_id=cat_egreso,
        moneda_id=moneda_ars,
        tipo_movimiento="egreso",
        monto_minor=50000,
    )
    caso("crear() devuelve ids numéricos distintos para A y B", True, isinstance(tx_a, int) and isinstance(tx_b, int) and tx_a != tx_b)

    print("\n--- obtener_por_id() ---")
    fila_a = repo.obtener_por_id(tx_a)
    caso("obtener_por_id() encuentra la transacción A", "Test A", fila_a["concepto"] if fila_a else None)

    print("\n--- listar() — filtro por fecha ---")
    solo_enero = repo.listar(fecha_desde="2026-01-01", fecha_hasta="2026-01-31")
    ids_enero = [r["id"] for r in solo_enero]
    caso("filtro de fecha incluye A (enero)", True, tx_a in ids_enero)
    caso("filtro de fecha excluye B (febrero)", False, tx_b in ids_enero)

    print("\n--- listar() — filtro por cuenta_id ---")
    solo_cuenta_a = repo.listar(cuenta_id=cuenta_a)
    ids_cuenta_a = [r["id"] for r in solo_cuenta_a]
    caso("filtro de cuenta_id incluye A", True, tx_a in ids_cuenta_a)
    caso("filtro de cuenta_id excluye B", False, tx_b in ids_cuenta_a)

    print("\n--- listar() — filtro por categoria_id ---")
    solo_cat_ingreso = repo.listar(categoria_id=cat_ingreso)
    ids_cat_ingreso = [r["id"] for r in solo_cat_ingreso]
    caso("filtro de categoria_id incluye A (ingreso)", True, tx_a in ids_cat_ingreso)
    caso("filtro de categoria_id excluye B (egreso)", False, tx_b in ids_cat_ingreso)

    print("\n--- listar() — filtro por tipo_movimiento ---")
    solo_egresos = repo.listar(tipo_movimiento="egreso")
    ids_egresos = [r["id"] for r in solo_egresos]
    caso("filtro de tipo_movimiento incluye B (egreso)", True, tx_b in ids_egresos)
    caso("filtro de tipo_movimiento excluye A (ingreso)", False, tx_a in ids_egresos)

    print("\n--- obtener_enriquecida() ---")
    fila_enriquecida = repo.obtener_enriquecida(tx_a)
    caso(
        "obtener_enriquecida() trae account_name vía JOIN",
        "Cuenta A Repo TX",
        fila_enriquecida["account_name"] if fila_enriquecida else None,
    )
    caso(
        "obtener_enriquecida() trae currency_code vía JOIN",
        "ARS",
        fila_enriquecida["currency_code"] if fila_enriquecida else None,
    )
    caso(
        "obtener_enriquecida() sigue trayendo las columnas propias (concepto)",
        "Test A",
        fila_enriquecida["concepto"] if fila_enriquecida else None,
    )

    print("\n--- listar_enriquecida() ---")
    listado_enriquecido = repo.listar_enriquecida(cuenta_id=cuenta_a)
    caso(
        "listar_enriquecida(cuenta_id=...) incluye A",
        True,
        tx_a in [r["id"] for r in listado_enriquecido],
    )
    fila_lista_enriquecida = next((r for r in listado_enriquecido if r["id"] == tx_a), None)
    caso(
        "listar_enriquecida() trae account_name enriquecido",
        True,
        "account_name" in fila_lista_enriquecida.keys() if fila_lista_enriquecida else False,
    )

    print("\n--- crear(conn=...) participando de una transacción externa ---")
    conn_externo = manager.conn
    try:
        with manager.transaction():
            tx_atom_1 = repo.crear(
                fecha="2026-03-01", concepto="Atomic 1", cuenta_id=cuenta_a,
                categoria_id=cat_ingreso, moneda_id=moneda_ars,
                tipo_movimiento="ingreso", monto_minor=1000,
                conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    fila_tras_rollback = manager.fetchone(
        "SELECT id FROM transacciones WHERE concepto = 'Atomic 1';"
    )
    caso(
        "crear(conn=...) dentro de una transacción que falla se revierte (rollback)",
        None,
        fila_tras_rollback,
    )

    with manager.transaction():
        tx_atom_a = repo.crear(
            fecha="2026-03-01", concepto="Atomic A", cuenta_id=cuenta_a,
            categoria_id=cat_ingreso, moneda_id=moneda_ars,
            tipo_movimiento="ingreso", monto_minor=2000,
            conn=conn_externo,
        )
        tx_atom_b = repo.crear(
            fecha="2026-03-01", concepto="Atomic B", cuenta_id=cuenta_b,
            categoria_id=cat_egreso, moneda_id=moneda_ars,
            tipo_movimiento="egreso", monto_minor=2000,
            conn=conn_externo,
        )
    caso(
        "crear(conn=...) x2 dentro de una transacción exitosa: ambas persisten (A)",
        "Atomic A",
        repo.obtener_por_id(tx_atom_a)["concepto"] if repo.obtener_por_id(tx_atom_a) else None,
    )
    caso(
        "crear(conn=...) x2 dentro de una transacción exitosa: ambas persisten (B)",
        "Atomic B",
        repo.obtener_por_id(tx_atom_b)["concepto"] if repo.obtener_por_id(tx_atom_b) else None,
    )

    print("\n--- actualizar() ---")
    actualizado = repo.actualizar(tx_b, notas="nota actualizada")
    caso("actualizar() devuelve True cuando hay campos para actualizar", True, actualizado)
    fila_b_actualizada = repo.obtener_por_id(tx_b)
    caso("actualizar() persiste el cambio de notas", "nota actualizada", fila_b_actualizada["notas"])
    sin_cambios = repo.actualizar(tx_b)
    caso("actualizar() sin campos devuelve False", False, sin_cambios)

    print("\n--- actualizar() — sentinel NO_CAMBIAR vs None explícito ---")
    tx_c = repo.crear(
        fecha="2026-04-01",
        concepto="Test C",
        cuenta_id=cuenta_a,
        categoria_id=cat_ingreso,
        moneda_id=moneda_ars,
        tipo_movimiento="ingreso",
        monto_minor=5000,
        tag="tag_original",
        notas="notas originales",
    )

    repo.actualizar(tx_c, concepto="Concepto nuevo", tag=NO_CAMBIAR)
    fila_c = repo.obtener_por_id(tx_c)
    caso("actualizar() con tag=NO_CAMBIAR explícito no lo toca", "tag_original", fila_c["tag"])
    caso("actualizar() sin pasar notas (default NO_CAMBIAR) no las toca", "notas originales", fila_c["notas"])
    caso("actualizar() sí aplica el campo pasado (concepto)", "Concepto nuevo", fila_c["concepto"])

    repo.actualizar(tx_c, tag=None)
    fila_c2 = repo.obtener_por_id(tx_c)
    caso("actualizar(tag=None) escribe NULL explícito en tag", None, fila_c2["tag"])
    caso("actualizar(tag=None) no toca notas (NO_CAMBIAR sigue siendo default)", "notas originales", fila_c2["notas"])

    print("\n--- eliminar() (soft-delete) ---")
    repo.eliminar(tx_a)
    caso("obtener_por_id() default (incluir_eliminadas=False) ya no encuentra A", None, repo.obtener_por_id(tx_a))
    listado_default = repo.listar()
    caso("listar() default ya no incluye A", False, tx_a in [r["id"] for r in listado_default])

    fila_cruda = manager.fetchone("SELECT id, deleted_at FROM transacciones WHERE id = ?;", (tx_a,))
    caso("la fila de A sigue existiendo físicamente en la tabla", True, fila_cruda is not None)
    caso("deleted_at quedó seteado (no NULL) tras eliminar()", True, fila_cruda["deleted_at"] is not None if fila_cruda else False)

    fila_con_eliminadas = repo.obtener_por_id(tx_a, incluir_eliminadas=True)
    caso("obtener_por_id(incluir_eliminadas=True) sí encuentra A eliminada", "Test A", fila_con_eliminadas["concepto"] if fila_con_eliminadas else None)

    print("\n--- restaurar() ---")
    repo.restaurar(tx_a)
    fila_restaurada = repo.obtener_por_id(tx_a)
    caso("obtener_por_id() default vuelve a encontrar A tras restaurar()", "Test A", fila_restaurada["concepto"] if fila_restaurada else None)
    caso("deleted_at vuelve a NULL tras restaurar()", None, fila_restaurada["deleted_at"] if fila_restaurada else "no encontrada")
    listado_tras_restaurar = repo.listar()
    caso("listar() default vuelve a incluir A tras restaurar()", True, tx_a in [r["id"] for r in listado_tras_restaurar])

    print("\n--- crear() — resolución perezosa de moneda (Tarea 6f) ---")
    # Cuenta creada SIN ninguna moneda declarada (moneda_codigo=None) —
    # mismo mecanismo que usa AccountsService.create_account(monedas=[]).
    cuenta_sin_moneda = cuentas_repo.crear(nombre="Cuenta Sin Moneda TX", tipo="debito", moneda_codigo=None)
    fila_saldo_previa = manager.fetchone(
        "SELECT * FROM cuentas_saldos WHERE cuenta_id = ? AND moneda_id = ?;",
        (cuenta_sin_moneda, moneda_ars),
    )
    caso("la cuenta recién creada sin moneda no tiene fila en cuentas_saldos todavía", None, fila_saldo_previa)

    tx_lazy_1 = repo.crear(
        fecha="2026-05-01", concepto="Primera vez en esta moneda", cuenta_id=cuenta_sin_moneda,
        categoria_id=cat_ingreso, moneda_id=moneda_ars, tipo_movimiento="ingreso", monto_minor=30000,
    )
    fila_saldo_creada = manager.fetchone(
        "SELECT * FROM cuentas_saldos WHERE cuenta_id = ? AND moneda_id = ?;",
        (cuenta_sin_moneda, moneda_ars),
    )
    caso("crear() en una combinación cuenta+moneda sin saldo_inicial previo lo genera solo", True, fila_saldo_creada is not None)
    caso("el saldo_inicial generado solo arranca en 0", 0, fila_saldo_creada["saldo_inicial_minor"] if fila_saldo_creada else None)
    caso("la transacción se insertó igual", True, repo.obtener_por_id(tx_lazy_1) is not None)

    tx_lazy_2 = repo.crear(
        fecha="2026-05-02", concepto="Segunda vez en la misma moneda", cuenta_id=cuenta_sin_moneda,
        categoria_id=cat_ingreso, moneda_id=moneda_ars, tipo_movimiento="ingreso", monto_minor=5000,
    )
    filas_saldo_tras_segunda = manager.fetchall(
        "SELECT * FROM cuentas_saldos WHERE cuenta_id = ? AND moneda_id = ?;",
        (cuenta_sin_moneda, moneda_ars),
    )
    caso("una segunda transacción en la misma combinación NO duplica la fila de saldo (reusa la existente)", 1, len(filas_saldo_tras_segunda))
    caso("la segunda transacción también se insertó", True, repo.obtener_por_id(tx_lazy_2) is not None)

    print("\n--- crear(conn=...) — resolución perezosa de moneda dentro de una transacción externa ---")
    cuenta_sin_moneda_atomica = cuentas_repo.crear(nombre="Cuenta Sin Moneda TX Atomica", tipo="debito", moneda_codigo=None)
    with manager.transaction():
        tx_lazy_atomico = repo.crear(
            fecha="2026-05-03", concepto="Perezosa con conn externo", cuenta_id=cuenta_sin_moneda_atomica,
            categoria_id=cat_ingreso, moneda_id=moneda_ars, tipo_movimiento="ingreso", monto_minor=7000,
            conn=conn_externo,
        )
    fila_saldo_atomica = manager.fetchone(
        "SELECT * FROM cuentas_saldos WHERE cuenta_id = ? AND moneda_id = ?;",
        (cuenta_sin_moneda_atomica, moneda_ars),
    )
    caso("crear(conn=...) también resuelve la moneda de forma perezosa, dentro de la misma transacción externa", True, fila_saldo_atomica is not None)
    caso("crear(conn=...) perezoso: la transacción se insertó", True, repo.obtener_por_id(tx_lazy_atomico) is not None)

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
