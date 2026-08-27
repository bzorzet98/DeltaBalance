"""
DeltaBalance — services/dashboard_service.py

Purpose:
    Servicio de solo lectura / agregación para el dashboard: patrimonio
    total, gasto del mes por categoría, y comparación estimado vs. real
    contra los presupuestos cargados. No escribe nada, no tiene reglas de
    negocio propias — compone AccountsService/PresupuestosService (que ya
    tienen sus propias reglas) y agrega una query de agregación (GROUP BY)
    propia para el gasto por categoría.

    Revisión previa a escribir esto (paso 1 de la tarea):
    - AccountsService.get_total_balance() ya existe y hace exactamente lo
      que necesita get_patrimonio_total() — se delega directo, no se
      reinventa.
    - TransactionService.monthly_summary(mes, anio) existe pero agrupa por
      MONEDA, no por categoría — no sirve para get_gasto_por_categoria().
      No hay ningún método de TransaccionesRepository ni de
      TransactionService que agrupe por categoría tampoco.
    - Por eso get_gasto_por_categoria() escribe su propia query de
      agregación acá, igual que TransactionService.monthly_summary() y
      FeesService.fees_by_month()/DebtsService.summary_by_person() en
      bloques anteriores: un reporte agregado (GROUP BY) no es CRUD de una
      tabla, no le corresponde vivir en un repositorio.
    - PresupuestosService.list_budgets(mes, anio) ya expone
      PresupuestosRepository.listar_por_periodo() enriquecido con
      subcategoria/categoria_principal/moneda_codigo — se usa tal cual para
      get_comparacion_presupuesto(), no se instancia PresupuestosRepository
      directo.

    Ninguna de las tres columnas nunca mezcla monto de monedas distintas en
    un mismo total: get_patrimonio_total() ya viene separado por moneda_codigo
    (AccountsService), get_gasto_por_categoria() agrupa por
    (categoria_id, moneda_id) — no solo por categoria_id — y
    get_comparacion_presupuesto() solo suma como "real" el gasto que está en
    la MISMA moneda que el presupuesto de esa categoría (presupuestos tiene
    un único moneda_id por fila, UNIQUE(categoria_id, mes, anio) del schema).
"""

import sqlite3
from datetime import date
from typing import Optional

from db.database import DatabaseManager
from services.accounts_service import AccountsService
from services.presupuestos_service import PresupuestosService


class DashboardService:
    """
    Entry point para las agregaciones de solo lectura que alimentan el
    dashboard.

    Usage:
        db  = DatabaseManager()
        svc = DashboardService(db)

        svc.get_patrimonio_total()                  # {"ARS": 1234500, "USD": 20000}
        svc.get_gasto_por_categoria(5, 2026)         # [{"categoria_id": 3, ...}, ...]
        svc.get_movimientos_por_cuenta(5, 2026)      # [{"cuenta_id": 1, "net_minor": ..., ...}, ...]
        svc.get_comparacion_presupuesto(5, 2026)     # [{"categoria_id": 3, "estimado_minor": ..., "real_minor": ...}, ...]
    """

    def __init__(self, db: DatabaseManager):
        self._db = db
        self._accounts_service = AccountsService(db)
        self._presupuestos_service = PresupuestosService(db)

    # ----------------------------------------------------------
    # INTERNAL HELPERS
    # ----------------------------------------------------------

    @staticmethod
    def _rango_mes(mes: int, anio: int) -> tuple[str, str]:
        """
        Calcula [inicio, fin) del mes como strings 'YYYY-MM-DD' — mismo
        patrón exacto que TransactionService.monthly_summary(): el límite
        superior es el día 1 del mes siguiente (exclusivo), para no
        depender de cuántos días tiene cada mes.
        """
        if not (1 <= mes <= 12):
            raise ValueError(f"Month must be between 1 and 12. Received: {mes}.")

        if mes == 12:
            siguiente = date(anio + 1, 1, 1)
        else:
            siguiente = date(anio, mes + 1, 1)

        inicio = f"{anio:04d}-{mes:02d}-01"
        fin = siguiente.isoformat()
        return inicio, fin

    # ----------------------------------------------------------
    # PATRIMONIO TOTAL
    # ----------------------------------------------------------

    def get_patrimonio_total(self) -> dict:
        """
        Delega directo en AccountsService.get_total_balance(): saldo de
        todas las cuentas activas, agrupado por moneda_codigo, sin mezclar
        monedas distintas en una sola suma.

        Returns:
            Dict {moneda_codigo: total_minor}, ej. {"ARS": 1234500, "USD": 20000}.
        """
        return self._accounts_service.get_total_balance()

    # ----------------------------------------------------------
    # GASTO POR CATEGORÍA
    # ----------------------------------------------------------

    def get_gasto_por_categoria(self, mes: int, anio: int) -> list[dict]:
        """
        Suma todas las transacciones tipo='egreso' del mes/año dado,
        agrupadas por (categoria_id, moneda_id) — si una categoría tuvo
        gasto en más de una moneda en el mismo mes, aparece como una
        entrada por cada moneda, nunca mezcladas en un solo total. Excluye
        transacciones eliminadas lógicamente (deleted_at IS NOT NULL),
        mismo criterio que TransactionService.monthly_summary().

        Args:
            mes:  Mes 1–12.
            anio: Año de 4 dígitos.

        Returns:
            Lista de dicts {categoria_id, categoria_nombre, monto_total_minor,
            moneda_id}, ordenada de mayor a menor monto_total_minor.

        Raises:
            ValueError si mes está fuera de rango.
        """
        inicio, fin = self._rango_mes(mes, anio)

        filas = self._db.fetchall(
            """
            SELECT
                t.categoria_id,
                cat.subcategoria AS categoria_nombre,
                t.moneda_id,
                SUM(t.monto_minor) AS monto_total_minor
            FROM transacciones t
            JOIN categorias cat ON cat.id = t.categoria_id
            WHERE t.tipo_movimiento = 'egreso'
              AND t.fecha >= ?
              AND t.fecha < ?
              AND t.deleted_at IS NULL
            GROUP BY t.categoria_id, t.moneda_id
            ORDER BY monto_total_minor DESC;
            """,
            (inicio, fin),
        )

        return [
            {
                "categoria_id": fila["categoria_id"],
                "categoria_nombre": fila["categoria_nombre"],
                "monto_total_minor": fila["monto_total_minor"],
                "moneda_id": fila["moneda_id"],
            }
            for fila in filas
        ]

    # ----------------------------------------------------------
    # MOVIMIENTOS POR CUENTA (desglose por banco — Tarea 4)
    # ----------------------------------------------------------

    def get_movimientos_por_cuenta(self, mes: int, anio: int) -> list[dict]:
        """
        Desglose de movimientos del mes/año dado, agrupado por
        (cuenta_id, moneda_id) — SOLO cuentas con al menos una transacción
        real en ese período: el INNER JOIN contra `cuentas` hace la
        detección dinámica sola (nunca se parte de una lista fija de todas
        las cuentas existentes, ni se filtra por `activa` — una cuenta ya
        archivada que tuvo movimientos ese mes histórico igual debe
        aparecer). Mismo criterio de ingreso/egreso/neto que
        TransactionService.monthly_summary(), pero desagregado por cuenta
        además de por moneda. Nunca mezcla monedas distintas en un mismo
        total (agrupa por cuenta+moneda, no solo por cuenta) — mismo
        principio que get_gasto_por_categoria() de acá arriba.

        Args:
            mes:  Mes 1–12.
            anio: Año de 4 dígitos.

        Returns:
            Lista de dicts {cuenta_id, account_name, moneda_id,
            currency_code, currency_symbol, decimales,
            total_income_minor, total_expense_minor, net_minor}, ordenada
            por account_name.

        Raises:
            ValueError si mes está fuera de rango.
        """
        inicio, fin = self._rango_mes(mes, anio)

        filas = self._db.fetchall(
            """
            SELECT
                c.id            AS cuenta_id,
                c.nombre        AS account_name,
                m.id            AS moneda_id,
                m.codigo        AS currency_code,
                m.simbolo       AS currency_symbol,
                m.decimales,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN t.monto_minor ELSE 0 END) AS total_income_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'egreso'
                         THEN t.monto_minor ELSE 0 END) AS total_expense_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN  t.monto_minor
                         WHEN t.tipo_movimiento = 'egreso'
                         THEN -t.monto_minor
                         ELSE  0 END)                   AS net_minor
            FROM transacciones t
            JOIN cuentas c ON c.id = t.cuenta_id
            JOIN monedas m ON m.id = t.moneda_id
            WHERE t.fecha >= ?
              AND t.fecha < ?
              AND t.deleted_at IS NULL
            GROUP BY c.id, m.id
            ORDER BY c.nombre;
            """,
            (inicio, fin),
        )

        return [dict(fila) for fila in filas]

    # ----------------------------------------------------------
    # COMPARACIÓN ESTIMADO VS. REAL
    # ----------------------------------------------------------

    def get_comparacion_presupuesto(self, mes: int, anio: int) -> list[dict]:
        """
        Cruza los presupuestos cargados para mes/año contra el gasto real
        de get_gasto_por_categoria() del mismo período. Solo incluye
        categorías CON presupuesto cargado (una categoría con gasto real
        pero sin presupuesto no aparece acá — para verla completa está
        get_gasto_por_categoria()).

        El cruce es por (categoria_id, moneda_id): `presupuestos` tiene un
        único moneda_id por fila (UNIQUE(categoria_id, mes, anio) del
        schema), así que solo se suma como real_minor el gasto que está en
        esa misma moneda — nunca se mezcla con gasto de esa categoría en
        otra moneda.

        Args:
            mes:  Mes 1–12.
            anio: Año de 4 dígitos.

        Returns:
            Lista de dicts {categoria_id, categoria_nombre, estimado_minor,
            real_minor}, en el mismo orden que PresupuestosService.list_budgets()
            (categoria_principal, subcategoria). real_minor es 0 si la
            categoría tiene presupuesto pero ningún gasto real todavía en
            esa moneda.
        """
        presupuestos = self._presupuestos_service.list_budgets(mes, anio)
        gastos = self.get_gasto_por_categoria(mes, anio)

        real_por_clave = {
            (gasto["categoria_id"], gasto["moneda_id"]): gasto["monto_total_minor"]
            for gasto in gastos
        }

        return [
            {
                "categoria_id": presupuesto["categoria_id"],
                "categoria_nombre": presupuesto["subcategoria"],
                "estimado_minor": presupuesto["monto_estimado_minor"],
                "real_minor": real_por_clave.get(
                    (presupuesto["categoria_id"], presupuesto["moneda_id"]), 0
                ),
            }
            for presupuesto in presupuestos
        ]
