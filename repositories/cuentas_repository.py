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
    ) -> int:
        """
        Crea una cuenta y su fila en cuentas_saldos para la moneda indicada.
        Devuelve el id de la cuenta creada.
        """
        cuenta_id = self._db.execute(
            """
            INSERT INTO cuentas (nombre, tipo, cuenta_pago_id, notas)
            VALUES (?, ?, ?, ?);
            """,
            (nombre, tipo, cuenta_pago_id, notas),
        )
        moneda = self._db.fetchone("SELECT * FROM monedas WHERE codigo = ?;", (moneda_codigo,))
        if moneda is None:
            raise ValueError(f"Moneda desconocida: {moneda_codigo}")
        self._db.execute(
            """
            INSERT OR IGNORE INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor)
            VALUES (?, ?, ?);
            """,
            (cuenta_id, moneda["id"], to_minor(saldo_inicial, moneda["decimales"])),
        )
        return cuenta_id

    def obtener_por_id(self, cuenta_id: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone("SELECT * FROM cuentas WHERE id = ?;", (cuenta_id,))

    def listar(self, solo_activas: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM cuentas"
        sql += " WHERE activa = 1" if solo_activas else ""
        sql += " ORDER BY nombre;"
        return self._db.fetchall(sql)

    def actualizar(
        self,
        cuenta_id: int,
        nombre: Optional[str] = None,
        tipo: Optional[str] = None,
        cuenta_pago_id: Optional[int] = None,
        notas: Optional[str] = None,
        activa: Optional[int] = None,
    ) -> bool:
        campos, valores = [], []
        if nombre         is not None: campos.append("nombre = ?");         valores.append(nombre)
        if tipo           is not None: campos.append("tipo = ?");           valores.append(tipo)
        if cuenta_pago_id is not None: campos.append("cuenta_pago_id = ?"); valores.append(cuenta_pago_id)
        if notas          is not None: campos.append("notas = ?");          valores.append(notas)
        if activa         is not None: campos.append("activa = ?");         valores.append(activa)
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
