"""
verify/deudas/verify_deudas_repository.py

Verifica DeudasRepository (repositories/deudas_repository.py): crear,
obtener_por_id/obtener_enriquecida, listar/listar_enriquecida respetando
cada filtro por separado (cruzado: incluye y excluye), actualizar con el
sentinel NO_CAMBIAR vs None explícito, registrar_pago (standalone, con conn
externo, y con rollback simulado a mitad de camino), y write_off.

También verifica el fix del bug de Fase 2 DEUDAS paso 2 (concepto/notas se
pisaban en actualizar() porque compartían la columna `notas`): que
crear()/actualizar() tratan concepto y notas como columnas independientes —
actualizar ambos a la vez sin que uno pise al otro, y actualizar solo uno
dejando el otro en NO_CAMBIAR sin que se pierda su valor previo.

Correlo con:
    python verify/deudas/verify_deudas_repository.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager
from repositories.deudas_repository import DeudasRepository
from repositories._sentinels import NO_CAMBIAR


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
    repo = DeudasRepository(manager)

    moneda_ars = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'ARS';")["id"]
    moneda_usd = manager.fetchone("SELECT id FROM monedas WHERE codigo = 'USD';")["id"]

    print("--- crear() ---")
    deuda_a = repo.crear(
        entidad_persona="Noe",
        tipo="a_favor",
        monto_minor=500000,
        moneda_id=moneda_ars,
        fecha_inicio="2026-01-10",
        origen_tipo="manual",
    )
    deuda_b = repo.crear(
        entidad_persona="Papi",
        tipo="en_contra",
        monto_minor=200000,
        moneda_id=moneda_ars,
        fecha_inicio="2026-02-05",
        fecha_vencimiento="2026-06-01",
        origen_tipo="transaccion",
        origen_id=999,
        notas="nota inicial",
    )
    deuda_c = repo.crear(
        entidad_persona="Noe",
        tipo="a_favor",
        monto_minor=10000,
        moneda_id=moneda_usd,
        fecha_inicio="2026-03-01",
    )
    caso(
        "crear() devuelve ids numéricos distintos para A, B y C",
        True,
        len({deuda_a, deuda_b, deuda_c}) == 3 and all(isinstance(x, int) for x in (deuda_a, deuda_b, deuda_c)),
    )

    print("\n--- crear() — monto_original == monto_pendiente al nacer ---")
    fila_a_cruda = manager.fetchone("SELECT * FROM deudas WHERE id = ?;", (deuda_a,))
    caso(
        "monto_pendiente_minor arranca igual a monto_original_minor",
        fila_a_cruda["monto_original_minor"],
        fila_a_cruda["monto_pendiente_minor"],
    )
    caso("estado inicial es 'activa'", "activa", fila_a_cruda["estado"])

    print("\n--- obtener_por_id() ---")
    fila_a = repo.obtener_por_id(deuda_a)
    caso("obtener_por_id() encuentra la deuda A", "Noe", fila_a["entidad_persona"] if fila_a else None)
    caso("obtener_por_id() de un id inexistente devuelve None", None, repo.obtener_por_id(999999))

    print("\n--- obtener_enriquecida() ---")
    fila_a_enriquecida = repo.obtener_enriquecida(deuda_a)
    caso(
        "obtener_enriquecida() trae currency_code vía JOIN",
        "ARS",
        fila_a_enriquecida["currency_code"] if fila_a_enriquecida else None,
    )
    caso(
        "obtener_enriquecida() trae currency_symbol vía JOIN",
        "$",
        fila_a_enriquecida["currency_symbol"] if fila_a_enriquecida else None,
    )
    caso(
        "obtener_enriquecida() trae decimales vía JOIN",
        2,
        fila_a_enriquecida["decimales"] if fila_a_enriquecida else None,
    )
    caso(
        "obtener_enriquecida() sigue trayendo columnas propias (entidad_persona)",
        "Noe",
        fila_a_enriquecida["entidad_persona"] if fila_a_enriquecida else None,
    )

    print("\n--- listar() — filtro por entidad_persona ---")
    solo_noe = repo.listar(entidad_persona="Noe")
    ids_noe = [r["id"] for r in solo_noe]
    caso("filtro de entidad_persona incluye A y C (Noe)", True, deuda_a in ids_noe and deuda_c in ids_noe)
    caso("filtro de entidad_persona excluye B (Papi)", False, deuda_b in ids_noe)

    print("\n--- listar() — filtro por tipo ---")
    solo_en_contra = repo.listar(tipo="en_contra")
    ids_en_contra = [r["id"] for r in solo_en_contra]
    caso("filtro de tipo incluye B (en_contra)", True, deuda_b in ids_en_contra)
    caso("filtro de tipo excluye A y C (a_favor)", False, deuda_a in ids_en_contra or deuda_c in ids_en_contra)

    print("\n--- listar() — filtro por moneda_id ---")
    solo_usd = repo.listar(moneda_id=moneda_usd)
    ids_usd = [r["id"] for r in solo_usd]
    caso("filtro de moneda_id incluye C (USD)", True, deuda_c in ids_usd)
    caso("filtro de moneda_id excluye A y B (ARS)", False, deuda_a in ids_usd or deuda_b in ids_usd)

    print("\n--- listar() — filtro por origen_tipo ---")
    solo_transaccion = repo.listar(origen_tipo="transaccion")
    ids_transaccion = [r["id"] for r in solo_transaccion]
    caso("filtro de origen_tipo incluye B (transaccion)", True, deuda_b in ids_transaccion)
    caso("filtro de origen_tipo excluye A (manual)", False, deuda_a in ids_transaccion)

    print("\n--- listar() — filtro por estado ---")
    solo_activas = repo.listar(estado="activa")
    ids_activas = [r["id"] for r in solo_activas]
    caso("filtro de estado incluye A, B y C (todas activas todavía)", True, all(x in ids_activas for x in (deuda_a, deuda_b, deuda_c)))
    solo_saldadas = repo.listar(estado="saldada")
    caso("filtro de estado='saldada' no incluye nada todavía", False, deuda_a in [r["id"] for r in solo_saldadas])

    print("\n--- listar_enriquecida() ---")
    listado_enriquecido = repo.listar_enriquecida(entidad_persona="Noe")
    ids_enriquecido = [r["id"] for r in listado_enriquecido]
    caso("listar_enriquecida(entidad_persona='Noe') incluye A y C", True, deuda_a in ids_enriquecido and deuda_c in ids_enriquecido)
    caso("listar_enriquecida(entidad_persona='Noe') excluye B", False, deuda_b in ids_enriquecido)
    fila_lista_enriquecida = next((r for r in listado_enriquecido if r["id"] == deuda_a), None)
    caso(
        "listar_enriquecida() trae currency_code enriquecido",
        True,
        "currency_code" in fila_lista_enriquecida.keys() if fila_lista_enriquecida else False,
    )

    print("\n--- actualizar() — sentinel NO_CAMBIAR vs None explícito ---")
    repo.actualizar(deuda_b, entidad_persona="Papi Actualizado")
    fila_b_1 = repo.obtener_por_id(deuda_b)
    caso("actualizar() aplica el campo pasado (entidad_persona)", "Papi Actualizado", fila_b_1["entidad_persona"])
    caso("actualizar() sin pasar notas (default NO_CAMBIAR) no las toca", "nota inicial", fila_b_1["notas"])
    caso("actualizar() sin pasar fecha_vencimiento no la toca", "2026-06-01", fila_b_1["fecha_vencimiento"])

    repo.actualizar(deuda_b, notas="motivo del ajuste", fecha_vencimiento=NO_CAMBIAR)
    fila_b_2 = repo.obtener_por_id(deuda_b)
    caso("actualizar() aplica notas cuando se pasan", "motivo del ajuste", fila_b_2["notas"])
    caso("actualizar() con fecha_vencimiento=NO_CAMBIAR explícito no la toca", "2026-06-01", fila_b_2["fecha_vencimiento"])

    repo.actualizar(deuda_b, notas=None)
    fila_b_3 = repo.obtener_por_id(deuda_b)
    caso("actualizar(notas=None) escribe NULL explícito", None, fila_b_3["notas"])
    caso("actualizar(notas=None) no toca entidad_persona", "Papi Actualizado", fila_b_3["entidad_persona"])
    caso("actualizar(notas=None) no toca fecha_vencimiento", "2026-06-01", fila_b_3["fecha_vencimiento"])

    sin_cambios = repo.actualizar(deuda_b)
    caso("actualizar() sin ningún campo devuelve False", False, sin_cambios)

    print("\n--- concepto y notas: columnas independientes (fix del bug) ---")
    deuda_e = repo.crear(
        entidad_persona="Test Concepto Notas",
        tipo="a_favor",
        monto_minor=1000,
        moneda_id=moneda_ars,
        fecha_inicio="2026-05-01",
        concepto="Concepto inicial",
        notas="Notas iniciales",
    )
    fila_e_0 = repo.obtener_por_id(deuda_e)
    caso("crear() persiste concepto en su propia columna", "Concepto inicial", fila_e_0["concepto"])
    caso("crear() persiste notas en su propia columna", "Notas iniciales", fila_e_0["notas"])

    repo.actualizar(deuda_e, concepto="Concepto nuevo", notas="Notas nuevas")
    fila_e_1 = repo.obtener_por_id(deuda_e)
    caso("actualizar(concepto=X, notas=Y) juntos: concepto queda X, no pisado por Y", "Concepto nuevo", fila_e_1["concepto"])
    caso("actualizar(concepto=X, notas=Y) juntos: notas queda Y, no pisado por X", "Notas nuevas", fila_e_1["notas"])

    repo.actualizar(deuda_e, concepto="Solo concepto cambia")
    fila_e_2 = repo.obtener_por_id(deuda_e)
    caso("actualizar(concepto=...) solo: concepto se actualiza", "Solo concepto cambia", fila_e_2["concepto"])
    caso(
        "actualizar(concepto=...) solo: notas mantiene su valor previo real (NO_CAMBIAR, no NULL)",
        "Notas nuevas",
        fila_e_2["notas"],
    )

    repo.actualizar(deuda_e, notas="Solo notas cambia")
    fila_e_3 = repo.obtener_por_id(deuda_e)
    caso("actualizar(notas=...) solo: notas se actualiza", "Solo notas cambia", fila_e_3["notas"])
    caso(
        "actualizar(notas=...) solo: concepto mantiene su valor previo real (NO_CAMBIAR, no NULL)",
        "Solo concepto cambia",
        fila_e_3["concepto"],
    )

    print("\n--- registrar_pago() — standalone, pago parcial ---")
    pago_1_id = repo.registrar_pago(
        deuda_id=deuda_a,
        monto_applied_minor=200000,
        tipo_pago="transaccion",
        fecha="2026-01-20",
        nuevo_monto_pendiente_minor=300000,
        nuevo_estado="activa",
        concepto="Primer pago parcial",
    )
    caso("registrar_pago() devuelve un id numérico", True, isinstance(pago_1_id, int))
    fila_a_tras_pago1 = repo.obtener_por_id(deuda_a)
    caso("registrar_pago() actualiza monto_pendiente_minor", 300000, fila_a_tras_pago1["monto_pendiente_minor"])
    caso("registrar_pago() deja estado 'activa' si no salda del todo", "activa", fila_a_tras_pago1["estado"])
    pago_1_fila = manager.fetchone("SELECT * FROM deuda_pagos WHERE id = ?;", (pago_1_id,))
    caso("el pago quedó insertado en deuda_pagos con el monto correcto", 200000, pago_1_fila["monto_applied_minor"] if pago_1_fila else None)

    print("\n--- registrar_pago() — standalone, pago que salda la deuda ---")
    repo.registrar_pago(
        deuda_id=deuda_a,
        monto_applied_minor=300000,
        tipo_pago="transaccion",
        fecha="2026-01-25",
        nuevo_monto_pendiente_minor=0,
        nuevo_estado="saldada",
        concepto="Pago final",
    )
    fila_a_saldada = repo.obtener_por_id(deuda_a)
    caso("registrar_pago() deja monto_pendiente_minor en 0", 0, fila_a_saldada["monto_pendiente_minor"])
    caso("registrar_pago() transiciona estado a 'saldada'", "saldada", fila_a_saldada["estado"])

    print("\n--- registrar_pago(conn=...) — transacción externa exitosa ---")
    conn_externo = manager.conn
    with manager.transaction():
        pago_2_id = repo.registrar_pago(
            deuda_id=deuda_b,
            monto_applied_minor=100000,
            tipo_pago="ajuste",
            fecha="2026-02-10",
            nuevo_monto_pendiente_minor=100000,
            nuevo_estado="activa",
            conn=conn_externo,
        )
    fila_b_tras_pago = repo.obtener_por_id(deuda_b)
    caso("registrar_pago(conn=...) en transacción exitosa: actualiza el saldo", 100000, fila_b_tras_pago["monto_pendiente_minor"])
    pago_2_fila = manager.fetchone("SELECT id FROM deuda_pagos WHERE id = ?;", (pago_2_id,))
    caso("registrar_pago(conn=...) en transacción exitosa: el pago queda insertado", True, pago_2_fila is not None)

    print("\n--- registrar_pago(conn=...) — rollback simulado a mitad de camino ---")
    monto_pendiente_antes_rollback = repo.obtener_por_id(deuda_b)["monto_pendiente_minor"]
    pagos_antes_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM deuda_pagos WHERE deuda_id = ?;", (deuda_b,))["n"]
    try:
        with manager.transaction():
            repo.registrar_pago(
                deuda_id=deuda_b,
                monto_applied_minor=50000,
                tipo_pago="ajuste",
                fecha="2026-02-15",
                nuevo_monto_pendiente_minor=50000,
                nuevo_estado="activa",
                conn=conn_externo,
            )
            raise RuntimeError("Fallo simulado a mitad de la transacción externa")
    except RuntimeError:
        pass
    fila_b_tras_rollback = repo.obtener_por_id(deuda_b)
    pagos_tras_rollback = manager.fetchone("SELECT COUNT(*) AS n FROM deuda_pagos WHERE deuda_id = ?;", (deuda_b,))["n"]
    caso(
        "rollback revierte el UPDATE de monto_pendiente_minor",
        monto_pendiente_antes_rollback,
        fila_b_tras_rollback["monto_pendiente_minor"],
    )
    caso(
        "rollback revierte también el INSERT en deuda_pagos (no queda huérfano)",
        pagos_antes_rollback,
        pagos_tras_rollback,
    )

    print("\n--- write_off() ---")
    repo.write_off(deuda_c, notas="Se decidió perdonar la deuda")
    fila_c_write_off = repo.obtener_por_id(deuda_c)
    caso("write_off() deja estado 'incobrable'", "incobrable", fila_c_write_off["estado"])
    caso("write_off() escribe las notas pasadas", "Se decidió perdonar la deuda", fila_c_write_off["notas"])

    print("\n--- write_off(conn=...) — transacción externa ---")
    deuda_d = repo.crear(
        entidad_persona="Test WriteOff Conn",
        tipo="en_contra",
        monto_minor=1000,
        moneda_id=moneda_ars,
        fecha_inicio="2026-04-01",
    )
    with manager.transaction():
        repo.write_off(deuda_d, notas="Write-off dentro de transacción externa", conn=conn_externo)
    fila_d = repo.obtener_por_id(deuda_d)
    caso("write_off(conn=...) en transacción exitosa persiste el cambio", "incobrable", fila_d["estado"])

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
