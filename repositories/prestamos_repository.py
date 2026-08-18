"""
DeltaBalance — repositories/prestamos_repository.py

Acceso a datos para la tabla `prestamos`. Sin lógica de negocio: no
calcula el cronograma de amortización (eso es
CuotasPrestamoRepository.crear_lote(), orquestado por un futuro
LoansService — ver comentario en db/schema.sql arriba de CREATE TABLE
cuotas_prestamo), no valida existencia de cuenta/moneda, no decide si un
préstamo "puede" cambiar de estado.

Confirmado por grep en db/database.py (Fase 2, bloque PRESTAMOS, paso 1):
no existe ningún método relacionado con prestamos/cuotas_prestamo — no hay
comportamiento previo que replicar, el diseño sale directo del schema.

`prestamos` NO tiene columna `deleted_at` ni `activa` — por eso
obtener_por_id()/listar()/obtener_enriquecida()/listar_enriquecida() usan
QueryBuilder con include_deleted=True hardcodeado, mismo motivo que en los
repositorios anteriores sin esa columna (DeudasRepository,
ComprasCuotasRepository). SÍ tiene `updated_en` con su propio trigger
(trg_prestamos_updated) — no hace falta tocarlo desde acá.

crear() acepta `conn` desde el arranque: un préstamo casi siempre se crea
junto con su cronograma completo de cuotas
(CuotasPrestamoRepository.crear_lote(conn=...)), en la misma operación
atómica — mismo patrón que ComprasCuotasRepository.crear() +
CuotasCreditoRepository.crear_lote().

cuenta_debito_id es NULLABLE (un préstamo puede no tener cuenta de débito
vinculada todavía) — obtener_enriquecida()/listar_enriquecida() usan LEFT
JOIN a cuentas para no perder el préstamo si no la tiene.
"""

import sqlite3
from typing import Any, Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder
from repositories._sentinels import NO_CAMBIAR


class PrestamosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # CREATE
    # ----------------------------------------------------------

    def crear(
        self,
        entidad: str,
        tipo: str,
        capital_original_minor: int,
        tasa_anual_bp: int,
        sistema_amortizacion: str,
        moneda_id: int,
        fecha_inicio: str,
        plazo_meses: int,
        cuenta_debito_id: Optional[int] = None,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un préstamo. estado arranca en 'activo' (default de
        columna, no se setea acá explícitamente).

        Si se pasa `conn`, el INSERT se ejecuta ahí directamente sin
        comitear, para participar de la transacción externa que también
        inserta el lote de cuotas_prestamo (ver docstring del módulo).
        """
        sql = """
            INSERT INTO prestamos
                (entidad, tipo, capital_original_minor, tasa_anual_bp,
                 sistema_amortizacion, moneda_id, fecha_inicio, plazo_meses,
                 cuenta_debito_id, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            entidad, tipo, capital_original_minor, tasa_anual_bp,
            sistema_amortizacion, moneda_id, fecha_inicio, plazo_meses,
            cuenta_debito_id, notas,
        )
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_id(self, prestamo_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("prestamos", include_deleted=True)
            .where("id", prestamo_id)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_enriquecida(self, prestamo_id: int) -> Optional[sqlite3.Row]:
        """
        Igual que obtener_por_id(), pero con currency_code (JOIN a
        monedas) y account_name (LEFT JOIN a cuentas — cuenta_debito_id
        puede ser NULL, LEFT JOIN para no perder el préstamo si no tiene
        cuenta vinculada).
        """
        return (
            QueryBuilder("prestamos p", include_deleted=True)
            .select(
                "p.*",
                "m.codigo AS currency_code",
                "c.nombre AS account_name",
            )
            .join("monedas m", "m.id = p.moneda_id")
            .left_join("cuentas c", "c.id = p.cuenta_debito_id")
            .where("p.id", prestamo_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar(self, estado: Optional[str] = None, tipo: Optional[str] = None) -> list[sqlite3.Row]:
        return (
            QueryBuilder("prestamos", include_deleted=True)
            .where("estado", estado)
            .where("tipo", tipo)
            .order("fecha_inicio", "DESC")
            .ejecutar(self._db.conn)
        )

    def listar_enriquecida(self, estado: Optional[str] = None, tipo: Optional[str] = None) -> list[sqlite3.Row]:
        """Misma firma de filtros que listar(), con el shape enriquecido de obtener_enriquecida()."""
        return (
            QueryBuilder("prestamos p", include_deleted=True)
            .select(
                "p.*",
                "m.codigo AS currency_code",
                "c.nombre AS account_name",
            )
            .join("monedas m", "m.id = p.moneda_id")
            .left_join("cuentas c", "c.id = p.cuenta_debito_id")
            .where("p.estado", estado)
            .where("p.tipo", tipo)
            .order("p.fecha_inicio", "DESC")
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar(
        self,
        prestamo_id: int,
        notas: Any = NO_CAMBIAR,
        cuenta_debito_id: Any = NO_CAMBIAR,
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """
        Update parcial. Únicos campos editables: notas y
        cuenta_debito_id — el resto de un préstamo (capital, tasa, plazo,
        sistema de amortización) no debería editarse libremente una vez
        creado; el estado tiene su propia transición dedicada en
        cambiar_estado().
        """
        campos, valores = [], []
        if notas            is not NO_CAMBIAR: campos.append("notas = ?");            valores.append(notas)
        if cuenta_debito_id is not NO_CAMBIAR: campos.append("cuenta_debito_id = ?"); valores.append(cuenta_debito_id)
        if not campos:
            return False
        valores.append(prestamo_id)
        sql = f"UPDATE prestamos SET {', '.join(campos)} WHERE id = ?;"
        if conn is not None:
            conn.execute(sql, tuple(valores))
        else:
            self._db.execute(sql, tuple(valores))
        return True

    # ----------------------------------------------------------
    # CAMBIAR ESTADO
    # ----------------------------------------------------------

    def cambiar_estado(
        self, prestamo_id: int, nuevo_estado: str, conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Transición: activo/cancelado/finalizado. Sin validar el estado previo (trabajo del futuro service)."""
        sql = "UPDATE prestamos SET estado = ? WHERE id = ?;"
        params = (nuevo_estado, prestamo_id)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.execute(sql, params)
