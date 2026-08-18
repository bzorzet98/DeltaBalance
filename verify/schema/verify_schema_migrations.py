"""
verify/schema/verify_schema_migrations.py

Verifica que db/schema_migrations.py resuelve el problema real que motivó su
creación: DatabaseManager.inicializar() reaplica db/schema.sql completo cada
vez que se llama, y antes de este cambio eso incluía ALTER TABLE sueltos
sobre compras_cuotas que rompían con "duplicate column name" en la segunda
corrida. Ahora esas columnas se agregan vía aplicar_migraciones_columna(),
que chequea PRAGMA table_info antes de alterar — inicializar() debería poder
llamarse dos veces seguidas sin explotar, y las columnas deberían quedar una
sola vez cada una.

Correlo con:
    python verify/schema/verify_schema_migrations.py
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from verify._dummy_db import crear_dummy_db
from db.database import DatabaseManager

COLUMNAS_ESPERADAS = ("monto_reintegro_minor", "modo_deuda")


def nombres_columnas(conn, tabla: str) -> list[str]:
    return [fila[1] for fila in conn.execute(f"PRAGMA table_info({tabla});")]


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

    print("--- Primera llamada a inicializar() ---")
    try:
        manager.inicializar()
        primera_llamada_ok = True
    except Exception as e:
        primera_llamada_ok = False
        print(f"Excepción inesperada en la primera llamada: {e!r}")
    caso("inicializar() no lanza excepción en la primera llamada", True, primera_llamada_ok)

    columnas = nombres_columnas(manager.conn, "compras_cuotas")
    for col in COLUMNAS_ESPERADAS:
        caso(f"compras_cuotas.{col} existe tras la primera inicializar()", True, col in columnas)

    print("\n--- Segunda llamada a inicializar() (sobre la misma DB) ---")
    try:
        manager.inicializar()
        segunda_llamada_ok = True
        segunda_llamada_excepcion = None
    except Exception as e:
        segunda_llamada_ok = False
        segunda_llamada_excepcion = e

    caso(
        "inicializar() no lanza excepción en la segunda llamada",
        True,
        segunda_llamada_ok,
    )
    if not segunda_llamada_ok:
        print(f"   Excepción obtenida: {segunda_llamada_excepcion!r}")

    columnas_tras_segunda = nombres_columnas(manager.conn, "compras_cuotas")
    for col in COLUMNAS_ESPERADAS:
        caso(
            f"compras_cuotas.{col} sigue existiendo tras la segunda inicializar()",
            True,
            col in columnas_tras_segunda,
        )
        caso(
            f"compras_cuotas.{col} aparece una sola vez (sin duplicados)",
            1,
            columnas_tras_segunda.count(col),
        )

    manager.desconectar()

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
