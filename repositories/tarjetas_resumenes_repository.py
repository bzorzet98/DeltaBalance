"""
DeltaBalance — repositories/tarjetas_resumenes_repository.py

Acceso a datos para la tabla `tarjetas_resumenes`: la fecha real de cierre
y de vencimiento de UN resumen de una tarjeta, cuando no es la que dan los
días de tarjetas_config (tabla creada vía db/schema_migrations.py
MIGRACIONES_TABLA, docs/DATA_MODEL_DECISIONS.md sección 29). Una fila por
(cuenta_id, mes, anio); cada fecha es opcional (NULL = la calculada).

Sin lógica de negocio: no valida que la cuenta sea una tarjeta, que las
fechas tengan sentido (vencimiento después del cierre) ni calcula fechas —
eso vive en services/fees_service.py.

guardar() hace un upsert parcial: con NO_CAMBIAR (default) una fecha no se
toca, con None se borra (vuelve a la calculada). updated_en lo escribe acá
(la tabla no tiene trigger de updated_en), igual que TarjetasConfigRepository.

Se sincroniza con Supabase (está en TABLAS_SINCRONIZADAS): los triggers de
sync anotan cada alta y cambio en sync_cambios solos, este repositorio no
tiene que avisar nada.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from repositories._ids import nuevo_id
from repositories._sentinels import NO_CAMBIAR


class TarjetasResumenesRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # UPSERT
    # ----------------------------------------------------------

    def guardar(
        self,
        cuenta_id: str,
        mes: int,
        anio: int,
        fecha_cierre: Any = NO_CAMBIAR,
        fecha_vence: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Crea la fila de (cuenta_id, mes, anio) si no existe y escribe las
        fechas pasadas ('YYYY-MM-DD' o None para borrarla). NO_CAMBIAR deja
        la fecha como está (NULL en una fila nueva).
        """
        ejecutor = conn if conn is not None else self._db.conn
        ejecutor.execute(
            "INSERT OR IGNORE INTO tarjetas_resumenes (id, cuenta_id, mes, anio) VALUES (?, ?, ?, ?);",
            (nuevo_id(), cuenta_id, mes, anio),
        )
        campos, valores = ["updated_en = CURRENT_TIMESTAMP"], []
        if fecha_cierre is not NO_CAMBIAR:
            campos.append("fecha_cierre = ?")
            valores.append(fecha_cierre)
        if fecha_vence is not NO_CAMBIAR:
            campos.append("fecha_vence = ?")
            valores.append(fecha_vence)
        ejecutor.execute(
            f"UPDATE tarjetas_resumenes SET {', '.join(campos)} WHERE cuenta_id = ? AND mes = ? AND anio = ?;",
            (*valores, cuenta_id, mes, anio),
        )
        if conn is None:
            self._db.conn.commit()

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener(self, cuenta_id: str, mes: int, anio: int) -> Optional[sqlite3.Row]:
        return self._db.fetchone(
            "SELECT * FROM tarjetas_resumenes WHERE cuenta_id = ? AND mes = ? AND anio = ?;",
            (cuenta_id, mes, anio),
        )

    def listar_por_cuenta(self, cuenta_id: str) -> list[sqlite3.Row]:
        """Las fechas reales cargadas de una tarjeta, de la más vieja a la más nueva."""
        return self._db.fetchall(
            "SELECT * FROM tarjetas_resumenes WHERE cuenta_id = ? ORDER BY anio, mes;", (cuenta_id,),
        )
