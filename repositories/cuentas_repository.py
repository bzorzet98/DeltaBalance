"""
DeltaBalance — repositories/cuentas_repository.py

Acceso a datos para la tabla `cuentas` (+ su fila correspondiente en
`cuentas_saldos`). Sin lógica de negocio: no valida reglas de dominio, no
decide si algo "se puede" editar — eso queda para un service, si hace falta.

Reemplaza a los métodos de cuentas de db/database.py::DatabaseManager
(obtener_cuentas, obtener_cuenta, crear_cuenta, modificar_cuenta,
archivar_cuenta, obtener_saldo_cuenta), que quedan deprecados ahí hasta que
services/ migre a usar este repositorio.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager, to_minor, from_minor


class CuentasRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    def crear(
        self,
        nombre: str,
        tipo: str,
        moneda_codigo: str = "ARS",
        saldo_inicial: float = 0.0,
        cuenta_pago_id: Optional[int] = None,
        notas: Optional[str] = None,
        color_hex: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Crea una cuenta y su fila en cuentas_saldos para la moneda indicada.
        Devuelve el id de la cuenta creada.

        color_hex es opcional: si no se pasa (None), la columna directamente
        no entra en el INSERT y toma su default de schema ('#5F5E5A', ver
        db/schema_migrations.py) — no se hardcodea ese valor acá también.

        Si se pasa `conn`, los dos INSERT se ejecutan ahí directamente sin
        comitear, para participar de una transacción externa (ej.
        AccountsService.create_account() con más de una moneda, que además
        llama a crear_saldo_inicial() para las monedas restantes dentro del
        mismo `with self._db.transaction():` — mismo patrón que
        ComprasCuotasRepository.crear()/CuotasCreditoRepository.crear_lote()).
        """
        columnas_cuenta = ["nombre", "tipo", "cuenta_pago_id", "notas"]
        valores_cuenta = [nombre, tipo, cuenta_pago_id, notas]
        if color_hex is not None:
            columnas_cuenta.append("color_hex")
            valores_cuenta.append(color_hex)

        sql_cuenta = (
            f"INSERT INTO cuentas ({', '.join(columnas_cuenta)}) "
            f"VALUES ({', '.join('?' for _ in columnas_cuenta)});"
        )
        params_cuenta = tuple(valores_cuenta)
        if conn is not None:
            cuenta_id = conn.execute(sql_cuenta, params_cuenta).lastrowid
        else:
            cuenta_id = self._db.execute(sql_cuenta, params_cuenta)

        moneda = self._db.fetchone("SELECT * FROM monedas WHERE codigo = ?;", (moneda_codigo,))
        if moneda is None:
            raise ValueError(f"Moneda desconocida: {moneda_codigo}")

        sql_saldo = """
            INSERT OR IGNORE INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor)
            VALUES (?, ?, ?);
        """
        params_saldo = (cuenta_id, moneda["id"], to_minor(saldo_inicial, moneda["decimales"]))
        if conn is not None:
            conn.execute(sql_saldo, params_saldo)
        else:
            self._db.execute(sql_saldo, params_saldo)

        return cuenta_id

    def crear_saldo_inicial(
        self,
        cuenta_id: int,
        moneda_id: int,
        monto_minor: int = 0,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Agrega una fila de saldo inicial para una moneda adicional de una
        cuenta ya existente — una cuenta puede operar en más de una moneda
        (ver docs/DATA_MODEL_DECISIONS.md sección 14). INSERT OR IGNORE: si
        la combinación (cuenta_id, moneda_id) ya existe, no hace nada; el
        caller valida antes si necesita distinguir "ya existía" de "se
        agregó" (ver AccountsService.add_currency_to_account()).

        Si se pasa `conn`, participa de una transacción externa — mismo
        motivo que en crear().
        """
        sql = """
            INSERT OR IGNORE INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor)
            VALUES (?, ?, ?);
        """
        params = (cuenta_id, moneda_id, monto_minor)
        if conn is not None:
            conn.execute(sql, params)
        else:
            self._db.execute(sql, params)

    def obtener_por_id(self, cuenta_id: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM cuentas WHERE id = ?;", (cuenta_id,))

    def listar(self, solo_activas: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM cuentas"
        sql += " WHERE activa = 1" if solo_activas else ""
        sql += " ORDER BY nombre;"
        return self._db.fetchall(sql)

    def listar_saldos(self, cuenta_id: int) -> list[sqlite3.Row]:
        """
        Todas las filas de cuentas_saldos de una cuenta, enriquecidas con la
        moneda (codigo/simbolo/decimales) — una cuenta puede operar en más
        de una moneda (ver docs/DATA_MODEL_DECISIONS.md sección 14).
        """
        return self._db.fetchall(
            """
            SELECT cs.cuenta_id, cs.moneda_id, cs.saldo_inicial_minor, cs.visible,
                   m.codigo, m.simbolo, m.decimales
            FROM cuentas_saldos cs
            JOIN monedas m ON m.id = cs.moneda_id
            WHERE cs.cuenta_id = ?
            ORDER BY m.codigo;
            """,
            (cuenta_id,),
        )

    def contar_dependientes(self, cuenta_id: int) -> int:
        """Cuántas cuentas tienen a esta como cuenta_pago_id (ej. tarjetas que pagan desde acá)."""
        fila = self._db.fetchone(
            "SELECT COUNT(*) AS n FROM cuentas WHERE cuenta_pago_id = ?;", (cuenta_id,)
        )
        return fila["n"]

    def actualizar(
        self,
        cuenta_id: int,
        nombre: Optional[str] = None,
        tipo: Optional[str] = None,
        cuenta_pago_id: Optional[int] = None,
        notas: Optional[str] = None,
        activa: Optional[int] = None,
        color_hex: Optional[str] = None,
    ) -> bool:
        campos, valores = [], []
        if nombre         is not None: campos.append("nombre = ?");         valores.append(nombre)
        if tipo           is not None: campos.append("tipo = ?");           valores.append(tipo)
        if cuenta_pago_id is not None: campos.append("cuenta_pago_id = ?"); valores.append(cuenta_pago_id)
        if notas          is not None: campos.append("notas = ?");          valores.append(notas)
        if activa         is not None: campos.append("activa = ?");         valores.append(activa)
        if color_hex      is not None: campos.append("color_hex = ?");      valores.append(color_hex)
        if not campos:
            return False
        valores.append(cuenta_id)
        self._db.execute(f"UPDATE cuentas SET {', '.join(campos)} WHERE id = ?;", tuple(valores))
        return True

    def archivar(self, cuenta_id: int) -> None:
        """Desactiva una cuenta (soft delete vía activa = 0)."""
        self._db.execute("UPDATE cuentas SET activa = 0 WHERE id = ?;", (cuenta_id,))

    def obtener_saldo(self, cuenta_id: int, moneda_codigo: str = "ARS") -> float:
        """Calcula el saldo actual vía vw_balance_cuentas (saldo_inicial + transacciones)."""
        row = self._db.fetchone(
            """
            SELECT saldo_minor
            FROM vw_balance_cuentas
            WHERE cuenta_id = ? AND moneda = ?;
            """,
            (cuenta_id, moneda_codigo),
        )
        if row is None:
            return 0.0
        moneda = self._db.fetchone("SELECT * FROM monedas WHERE codigo = ?;", (moneda_codigo,))
        return from_minor(row["saldo_minor"], moneda["decimales"])

    # ----------------------------------------------------------
    # DELETE (físico — distinto de archivar())
    # ----------------------------------------------------------
    # Solo debe llamarse una vez que el caller (AccountsService.delete_account())
    # ya validó que la cuenta no tiene transacciones, saldo inicial != 0 en
    # ninguna moneda, ni otras cuentas que dependan de ella como
    # cuenta_pago_id — acá no se valida nada, es DELETE físico literal, mismo
    # criterio que AsignacionesRepository.eliminar()/ResumenCargosExtraRepository.eliminar()
    # (repositorios no conocen reglas de negocio, ver CLAUDE.md §1).

    def eliminar(self, cuenta_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
        """DELETE físico de la cuenta."""
        sql = "DELETE FROM cuentas WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, (cuenta_id,))
        else:
            self._db.execute(sql, (cuenta_id,))

    def eliminar_saldos(self, cuenta_id: int, conn: Optional[sqlite3.Connection] = None) -> None:
        """DELETE físico de todas las filas de cuentas_saldos de una cuenta (en este punto, todas en 0)."""
        sql = "DELETE FROM cuentas_saldos WHERE cuenta_id = ?;"
        if conn is not None:
            conn.execute(sql, (cuenta_id,))
        else:
            self._db.execute(sql, (cuenta_id,))
