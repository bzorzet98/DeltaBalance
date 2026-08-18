"""
DeltaBalance — repositories/presupuestos_repository.py

Acceso a datos para la tabla `presupuestos`. Sin lógica de negocio: no
decide qué presupuestos "deberían" existir, no calcula
monto_ejecutado_minor sumando transacciones reales (ver docstring de
actualizar_ejecutado() más abajo) — eso vive en un futuro
PresupuestosService (Fase 2, paso 2, todavía no existe).

Hallazgos de la revisión de db/database.py (Fase 2, bloque PRESUPUESTOS /
INGRESOS PROYECTADOS / EMPLEOS, paso 1) que determinan la forma de este
repositorio:
- Ningún service existente (TransactionService, DebtsService, FeesService)
  usa `presupuestos` hoy — confirmado por grep, no hay ningún caller real
  que replicar más allá de los propios métodos de db/database.py.
- upsert_presupuesto() en db/database.py tiene el bug de
  execute()/rowcount documentado en docs/AUDITORIA_EXECUTE_ROWCOUNT.md.
  upsert() acá lo corrige desde el arranque: ejecuta directo contra
  self._db.conn (o el `conn` externo) y lee cur.rowcount, nunca pasa por
  self._db.execute().
- El UPDATE del ON CONFLICT original (upsert_presupuesto()) SOLO reescribía
  monto_estimado_minor y notas — NO es_recurrente ni moneda_id. Corregido
  acá: upsert() ahora también reescribe es_recurrente en el camino UPDATE,
  sin excepción especial para esa columna — igual de editable que
  monto_estimado_minor/notas. moneda_id sigue sin tocarse en el camino
  UPDATE (no se pidió ese cambio; si hace falta, es una corrección
  separada).
- copiar_presupuesto() usa INSERT OR IGNORE: si ya existe un presupuesto
  para esa categoría en el período destino, se omite sin sobreescribir.
  copiar_periodo() replica esto exactamente.

NOTA PARA DISEÑO FUTURO (no implementar todavía): el criterio general del
proyecto es que los datos deben poder editarse y eliminarse con
flexibilidad, pero manteniendo trazabilidad de que existieron. Cuando en el
futuro se agregue un eliminar()/desactivar() para presupuestos, evaluar si
conviene soft-delete (columna `activa`, como ya existe en `categorias`) en
vez de DELETE físico — no asumir uno u otro sin pensarlo primero.

crear()/upsert() reciben moneda_id ya resuelto y montos ya en minor units
(no moneda_codigo ni floats) — igual que el resto de los repositorios de
esta fase; resolver moneda_codigo→id y convertir montos es responsabilidad
de quien llama.

`presupuestos` NO tiene columna `deleted_at` — por eso
obtener_por_periodo()/listar_por_periodo() usan QueryBuilder con
include_deleted=True hardcodeado, mismo motivo que en los repositorios
anteriores sin esa columna.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class PresupuestosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # UPSERT
    # ----------------------------------------------------------

    def upsert(
        self,
        categoria_id: int,
        mes: int,
        anio: int,
        moneda_id: int,
        monto_estimado_minor: int,
        es_recurrente: bool = False,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        INSERT ... ON CONFLICT(categoria_id, mes, anio) DO UPDATE, usando el
        UNIQUE de la tabla. En el camino de UPDATE se reescriben
        monto_estimado_minor, es_recurrente y notas — es_recurrente es tan
        editable como los demás campos, sin excepción especial (corregido;
        la versión original de upsert_presupuesto() no lo reescribía, ver
        docstring del módulo). moneda_id NO se reescribe en el camino
        UPDATE.

        Devuelve la cantidad de filas afectadas (siempre 1 si la llamada es
        válida). NO devuelve un id fabricado a partir de
        cur.lastrowid — ese es exactamente el bug de
        DatabaseManager.execute() (cur.lastrowid no cae a cur.rowcount para
        el camino de UPDATE) que este método evita ejecutando directo
        contra la conexión y leyendo cur.rowcount.
        """
        sql = """
            INSERT INTO presupuestos
                (categoria_id, moneda_id, mes, anio, monto_estimado_minor, es_recurrente, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(categoria_id, mes, anio)
            DO UPDATE SET monto_estimado_minor = excluded.monto_estimado_minor,
                          es_recurrente = excluded.es_recurrente,
                          notas = excluded.notas;
        """
        params = (
            categoria_id, moneda_id, mes, anio,
            monto_estimado_minor, int(es_recurrente), notas,
        )
        if conn is not None:
            return conn.execute(sql, params).rowcount
        cur = self._db.conn.execute(sql, params)
        self._db.conn.commit()
        return cur.rowcount

    # ----------------------------------------------------------
    # READ
    # ----------------------------------------------------------

    def obtener_por_periodo(self, categoria_id: int, mes: int, anio: int) -> Optional[sqlite3.Row]:
        """Busca por el UNIQUE(categoria_id, mes, anio) de la tabla."""
        return (
            QueryBuilder("presupuestos", include_deleted=True)
            .where("categoria_id", categoria_id)
            .where("mes", mes)
            .where("anio", anio)
            .ejecutar_uno(self._db.conn)
        )

    def listar_por_periodo(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """
        Réplica exacta de DatabaseManager.obtener_presupuesto(): todos los
        presupuestos de un mes/año, enriquecidos con subcategoria/
        categoria_principal (JOIN categorias) y moneda_codigo (JOIN
        monedas). Ordena por categoria_principal, subcategoria.
        """
        return (
            QueryBuilder("presupuestos p", include_deleted=True)
            .select(
                "p.*",
                "cat.subcategoria",
                "cat.categoria_principal",
                "m.codigo AS moneda_codigo",
            )
            .join("categorias cat", "cat.id = p.categoria_id")
            .join("monedas m", "m.id = p.moneda_id")
            .where("p.mes", mes)
            .where("p.anio", anio)
            .order("cat.categoria_principal")
            .order("cat.subcategoria")
            .ejecutar(self._db.conn)
        )

    # ----------------------------------------------------------
    # UPDATE
    # ----------------------------------------------------------

    def actualizar_ejecutado(
        self,
        categoria_id: int,
        mes: int,
        anio: int,
        monto_ejecutado_minor: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Escribe monto_ejecutado_minor YA CALCULADO por quien llama. La suma
        real de transacciones de esa categoría/mes/año (CLAUDE.md sección 1:
        "monto_ejecutado_minor se actualiza automáticamente sumando
        transacciones... nunca se carga a mano en dos lugares") es
        responsabilidad de un futuro PresupuestosService que lea
        TransaccionesRepository y sume — este repositorio no hace esa
        matemática, solo persiste el resultado. Sin caller todavía; se deja
        listo para no bloquear ese trabajo futuro.
        """
        sql = "UPDATE presupuestos SET monto_ejecutado_minor = ? WHERE categoria_id = ? AND mes = ? AND anio = ?;"
        params = (monto_ejecutado_minor, categoria_id, mes, anio)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.conn.execute(sql, params)
        self._db.conn.commit()

    # ----------------------------------------------------------
    # COPIAR PERÍODO
    # ----------------------------------------------------------

    def copiar_periodo(
        self,
        mes_origen: int,
        anio_origen: int,
        mes_destino: int,
        anio_destino: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Copia los presupuestos de un período a otro. Usa INSERT OR IGNORE —
        si ya existe un presupuesto para esa categoría en el período
        destino (UNIQUE(categoria_id, mes, anio)), se omite sin
        sobreescribir. Réplica exacta de copiar_presupuesto().

        Devuelve la cantidad de presupuestos efectivamente copiados (no
        cuenta los omitidos por conflicto) — leído de cur.rowcount de cada
        INSERT OR IGNORE individual, no de un valor fabricado.

        La lectura del período origen se hace contra `conn` cuando se pasa
        (no siempre contra self._db.conn) — mismo motivo que
        ResumenesTarjetaRepository.marcar_cerrado(): si el período origen
        tiene filas insertadas pero no comiteadas todavía en una
        transacción externa genuinamente distinta, este método debe verlas.
        """
        conexion = conn if conn is not None else self._db.conn
        filas_origen = conexion.execute(
            "SELECT categoria_id, moneda_id, monto_estimado_minor, es_recurrente, notas "
            "FROM presupuestos WHERE mes = ? AND anio = ?;",
            (mes_origen, anio_origen),
        ).fetchall()
        sql = """
            INSERT OR IGNORE INTO presupuestos
                (categoria_id, moneda_id, mes, anio, monto_estimado_minor, es_recurrente, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        copiados = 0
        for fila in filas_origen:
            params = (
                fila["categoria_id"], fila["moneda_id"], mes_destino, anio_destino,
                fila["monto_estimado_minor"], fila["es_recurrente"], fila["notas"],
            )
            if conn is not None:
                cur = conn.execute(sql, params)
            else:
                cur = self._db.conn.execute(sql, params)
            copiados += cur.rowcount
        if conn is None:
            self._db.conn.commit()
        return copiados
