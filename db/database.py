"""
DeltaBalance — db/database.py
Clase central de acceso a la base de datos SQLite.
Gestiona inicialización, CRUD y utilidades de todas las entidades.
"""

import sqlite3
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


# =============================================================
# RUTAS POR DEFECTO
# =============================================================
BASE_DIR    = Path(__file__).resolve().parent.parent
DATA_DIR    = BASE_DIR / "data"
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"
SEED_PATH   = BASE_DIR / "db" / "seed.sql"
DB_PATH     = DATA_DIR / "deltabalance.db"


# =============================================================
# HELPERS: conversión minor units ↔ float
# =============================================================

def to_minor(amount: float, decimals: int = 2) -> int:
    """Convierte un monto float a minor units. Ej: 1234.56 ARS → 123456"""
    return round(amount * (10 ** decimals))


def from_minor(minor: int, decimals: int = 2) -> float:
    """Convierte minor units a float. Ej: 123456 → 1234.56"""
    return minor / (10 ** decimals)


# =============================================================
# DATABASE MANAGER
# =============================================================

class DatabaseManager:
    """
    Gestiona toda la interacción con la base de datos SQLite de DeltaBalance.

    Uso típico:
        db = DatabaseManager()
        db.inicializar()
        cuentas = db.obtener_cuentas()
    """

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path     = Path(db_path)
        self.schema_path = SCHEMA_PATH
        self.seed_path   = SEED_PATH
        self._conn: Optional[sqlite3.Connection] = None

    # ----------------------------------------------------------
    # CONEXIÓN
    # ----------------------------------------------------------

    def conectar(self) -> sqlite3.Connection:
        """Abre (o reutiliza) la conexión a la base de datos."""
        if self._conn is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row      # acceso por nombre de columna
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA journal_mode = WAL;")
        return self._conn

    def desconectar(self):
        """Cierra la conexión si está abierta."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        self.conectar()
        return self

    def __exit__(self, *_):
        self.desconectar()

    @property
    def conn(self) -> sqlite3.Connection:
        return self.conectar()

    # ----------------------------------------------------------
    # INICIALIZACIÓN
    # ----------------------------------------------------------

    def inicializar(self, force: bool = False):
        """
        Crea la base de datos aplicando schema.sql y seed.sql.
        Si force=True, elimina la DB existente primero.
        """
        if force:
            self.eliminar_db()

        ya_existia = self.db_path.exists()
        conn = self.conectar()

        # Aplicar schema
        with open(self.schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        # Aplicar seed solo si la DB era nueva
        if not ya_existia:
            with open(self.seed_path, "r", encoding="utf-8") as f:
                conn.executescript(f.read())
            print(f"[DeltaBalance] Base de datos inicializada en: {self.db_path}")
        else:
            print(f"[DeltaBalance] Base de datos ya existente en: {self.db_path}")

    def esta_inicializada(self) -> bool:
        """Verifica si la DB existe y tiene las tablas principales."""
        if not self.db_path.exists():
            return False
        try:
            cur = self.conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='transacciones';"
            )
            return cur.fetchone()[0] > 0
        except sqlite3.DatabaseError:
            return False

    # ----------------------------------------------------------
    # BACKUP Y ELIMINACIÓN
    # ----------------------------------------------------------

    def hacer_backup(self, destino: Optional[Path] = None) -> Path:
        """
        Copia la DB a un archivo de backup con timestamp.
        Devuelve la ruta del backup generado.
        """
        if not self.db_path.exists():
            raise FileNotFoundError("No existe base de datos para respaldar.")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = destino or self.db_path.parent / f"deltabalance_backup_{ts}.db"
        shutil.copy2(self.db_path, destino)
        print(f"[DeltaBalance] Backup creado en: {destino}")
        return destino

    def eliminar_db(self):
        """Elimina el archivo de base de datos. Usar con cuidado."""
        self.desconectar()
        if self.db_path.exists():
            self.db_path.unlink()
            print(f"[DeltaBalance] Base de datos eliminada: {self.db_path}")

    # ----------------------------------------------------------
    # UTILIDAD INTERNA
    # ----------------------------------------------------------

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchall()

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        return self.conn.execute(sql, params).fetchone()

    def _execute(self, sql: str, params: tuple = ()) -> int:
        """Ejecuta INSERT/UPDATE/DELETE. Devuelve el lastrowid o rowcount."""
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.lastrowid or cur.rowcount

    # ===========================================================
    # MONEDAS
    # ===========================================================

    def obtener_monedas(self) -> list[sqlite3.Row]:
        return self._fetchall("SELECT * FROM monedas ORDER BY codigo;")

    def obtener_moneda_por_codigo(self, codigo: str) -> Optional[sqlite3.Row]:
        return self._fetchone("SELECT * FROM monedas WHERE codigo = ?;", (codigo,))

    def obtener_moneda_id(self, codigo: str) -> int:
        row = self.obtener_moneda_por_codigo(codigo)
        if row is None:
            raise ValueError(f"Moneda no encontrada: {codigo}")
        return row["id"]

    # ===========================================================
    # CUENTAS
    # ===========================================================

    def obtener_cuentas(self, solo_activas: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM cuentas"
        sql += " WHERE activa = 1" if solo_activas else ""
        sql += " ORDER BY nombre;"
        return self._fetchall(sql)

    def obtener_cuenta(self, cuenta_id: int) -> Optional[sqlite3.Row]:
        return self._fetchone("SELECT * FROM cuentas WHERE id = ?;", (cuenta_id,))

    def crear_cuenta(
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
        cuenta_id = self._execute(
            """
            INSERT INTO cuentas (nombre, tipo, cuenta_pago_id, notas)
            VALUES (?, ?, ?, ?);
            """,
            (nombre, tipo, cuenta_pago_id, notas),
        )
        moneda_id = self.obtener_moneda_id(moneda_codigo)
        decimales = self._fetchone(
            "SELECT decimales FROM monedas WHERE id = ?;", (moneda_id,)
        )["decimales"]
        self._execute(
            """
            INSERT OR IGNORE INTO cuentas_saldos (cuenta_id, moneda_id, saldo_inicial_minor)
            VALUES (?, ?, ?);
            """,
            (cuenta_id, moneda_id, to_minor(saldo_inicial, decimales)),
        )
        return cuenta_id

    def modificar_cuenta(
        self,
        cuenta_id: int,
        nombre: Optional[str] = None,
        tipo: Optional[str] = None,
        cuenta_pago_id: Optional[int] = None,
        notas: Optional[str] = None,
        activa: Optional[int] = None,
    ) -> bool:
        campos, valores = [], []
        if nombre        is not None: campos.append("nombre = ?");        valores.append(nombre)
        if tipo          is not None: campos.append("tipo = ?");          valores.append(tipo)
        if cuenta_pago_id is not None: campos.append("cuenta_pago_id = ?"); valores.append(cuenta_pago_id)
        if notas         is not None: campos.append("notas = ?");         valores.append(notas)
        if activa        is not None: campos.append("activa = ?");        valores.append(activa)
        if not campos:
            return False
        valores.append(cuenta_id)
        self._execute(f"UPDATE cuentas SET {', '.join(campos)} WHERE id = ?;", tuple(valores))
        return True

    def archivar_cuenta(self, cuenta_id: int):
        """Desactiva una cuenta (soft delete)."""
        self._execute("UPDATE cuentas SET activa = 0 WHERE id = ?;", (cuenta_id,))

    def obtener_saldo_cuenta(self, cuenta_id: int, moneda_codigo: str = "ARS") -> float:
        """Calcula el saldo actual: saldo_inicial + suma de transacciones."""
        row = self._fetchone(
            """
            SELECT saldo_minor
            FROM vw_balance_cuentas
            WHERE cuenta_id = ? AND moneda = ?;
            """,
            (cuenta_id, moneda_codigo),
        )
        if row is None:
            return 0.0
        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        return from_minor(row["saldo_minor"], moneda["decimales"])

    # ===========================================================
    # CATEGORIAS
    # ===========================================================

    def obtener_categorias(self, tipo: Optional[str] = None) -> list[sqlite3.Row]:
        if tipo:
            return self._fetchall(
                "SELECT * FROM categorias WHERE tipo = ? ORDER BY categoria_principal, subcategoria;",
                (tipo,),
            )
        return self._fetchall(
            "SELECT * FROM categorias ORDER BY categoria_principal, subcategoria;"
        )

    def obtener_categoria(self, categoria_id: int) -> Optional[sqlite3.Row]:
        return self._fetchone("SELECT * FROM categorias WHERE id = ?;", (categoria_id,))

    def crear_categoria(
        self, categoria_principal: str, subcategoria: str, tipo: str
    ) -> int:
        return self._execute(
            "INSERT INTO categorias (categoria_principal, subcategoria, tipo) VALUES (?, ?, ?);",
            (categoria_principal, subcategoria, tipo),
        )

    # ===========================================================
    # TRANSACCIONES
    # ===========================================================

    def obtener_transacciones(
        self,
        cuenta_id:    Optional[int] = None,
        categoria_id: Optional[int] = None,
        moneda:       Optional[str] = None,
        desde:        Optional[str] = None,   # 'YYYY-MM-DD'
        hasta:        Optional[str] = None,
        limit:        int = 200,
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT t.*, c.nombre AS cuenta, cat.subcategoria, m.codigo AS moneda_codigo
            FROM transacciones t
            JOIN cuentas    c   ON c.id   = t.cuenta_id
            JOIN categorias cat ON cat.id = t.categoria_id
            JOIN monedas    m   ON m.id   = t.moneda_id
            WHERE 1=1
        """
        params = []
        if cuenta_id:    sql += " AND t.cuenta_id = ?";    params.append(cuenta_id)
        if categoria_id: sql += " AND t.categoria_id = ?"; params.append(categoria_id)
        if moneda:       sql += " AND m.codigo = ?";       params.append(moneda)
        if desde:        sql += " AND t.fecha >= ?";       params.append(desde)
        if hasta:        sql += " AND t.fecha <= ?";       params.append(hasta)
        sql += " ORDER BY t.fecha DESC, t.id DESC LIMIT ?;"
        params.append(limit)
        return self._fetchall(sql, tuple(params))

    def obtener_transaccion(self, transaccion_id: int) -> Optional[sqlite3.Row]:
        return self._fetchone(
            "SELECT * FROM transacciones WHERE id = ?;", (transaccion_id,)
        )

    def crear_transaccion(
        self,
        fecha:          str,
        concepto:       str,
        cuenta_id:      int,
        categoria_id:   int,
        moneda_codigo:  str,
        monto:          float,
        tipo_movimiento: str,
        tag:            Optional[str] = None,
        notas:          Optional[str] = None,
    ) -> int:
        """
        Crea una transacción. monto siempre positivo; tipo_movimiento define la dirección.
        """
        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        if moneda is None:
            raise ValueError(f"Moneda desconocida: {moneda_codigo}")
        monto_minor = to_minor(abs(monto), moneda["decimales"])
        return self._execute(
            """
            INSERT INTO transacciones
                (fecha, concepto, cuenta_id, categoria_id, moneda_id,
                 tipo_movimiento, monto_minor, tag, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                fecha, concepto, cuenta_id, categoria_id, moneda["id"],
                tipo_movimiento, monto_minor, tag, notas,
            ),
        )

    def modificar_transaccion(
        self,
        transaccion_id: int,
        fecha:          Optional[str]   = None,
        concepto:       Optional[str]   = None,
        monto:          Optional[float] = None,
        moneda_codigo:  Optional[str]   = None,
        tag:            Optional[str]   = None,
        notas:          Optional[str]   = None,
    ) -> bool:
        campos, valores = [], []
        if fecha    is not None: campos.append("fecha = ?");   valores.append(fecha)
        if concepto is not None: campos.append("concepto = ?"); valores.append(concepto)
        if tag      is not None: campos.append("tag = ?");     valores.append(tag)
        if notas    is not None: campos.append("notas = ?");   valores.append(notas)
        if monto is not None and moneda_codigo is not None:
            moneda = self.obtener_moneda_por_codigo(moneda_codigo)
            campos.append("monto_minor = ?")
            valores.append(to_minor(abs(monto), moneda["decimales"]))
        if not campos:
            return False
        valores.append(transaccion_id)
        self._execute(
            f"UPDATE transacciones SET {', '.join(campos)} WHERE id = ?;",
            tuple(valores),
        )
        return True

    def eliminar_transaccion(self, transaccion_id: int) -> bool:
        """Elimina una transacción. Verificar que no esté vinculada antes de llamar."""
        rowcount = self._execute(
            "DELETE FROM transacciones WHERE id = ?;", (transaccion_id,)
        )
        return rowcount > 0

    # ===========================================================
    # AUTOTRANSFERENCIAS
    # ===========================================================

    def crear_autotransferencia(
        self,
        fecha:          str,
        cuenta_origen_id: int,
        cuenta_destino_id: int,
        moneda_codigo:  str,
        monto:          float,
        categoria_id:   int,
        notas:          Optional[str] = None,
    ) -> tuple[int, int]:
        """
        Crea dos transacciones vinculadas (egreso en origen, ingreso en destino).
        Devuelve (id_salida, id_entrada).
        """
        id_salida = self.crear_transaccion(
            fecha, "Autotransferencia (salida)", cuenta_origen_id,
            categoria_id, moneda_codigo, monto, "egreso", tag="autotransferencia", notas=notas,
        )
        id_entrada = self.crear_transaccion(
            fecha, "Autotransferencia (entrada)", cuenta_destino_id,
            categoria_id, moneda_codigo, monto, "ingreso", tag="autotransferencia", notas=notas,
        )
        self._execute(
            """
            INSERT INTO autotransferencias (transaccion_salida_id, transaccion_entrada_id, notas)
            VALUES (?, ?, ?);
            """,
            (id_salida, id_entrada, notas),
        )
        return id_salida, id_entrada

    # ===========================================================
    # COMPRAS EN CUOTAS
    # ===========================================================

    def crear_compra_cuotas(
        self,
        fecha_compra:     str,
        concepto:         str,
        cuenta_id:        int,
        categoria_id:     int,
        moneda_codigo:    str,
        monto_total:      float,
        total_cuotas:     int,
        monto_por_cuota:  Optional[float] = None,
        notas:            Optional[str]   = None,
    ) -> int:
        """
        Crea la compra y genera automáticamente las N cuotas proyectadas.
        Si monto_por_cuota es None se calcula dividiendo monto_total / total_cuotas.
        Las cuotas se proyectan mes a mes desde la fecha de compra.
        Devuelve el id de compra_cuotas.
        """
        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        if moneda is None:
            raise ValueError(f"Moneda desconocida: {moneda_codigo}")
        dec = moneda["decimales"]

        if monto_por_cuota is None:
            monto_por_cuota = monto_total / total_cuotas

        compra_id = self._execute(
            """
            INSERT INTO compras_cuotas
                (fecha_compra, concepto, cuenta_id, categoria_id, moneda_id,
                 monto_total_minor, total_cuotas, monto_por_cuota_minor, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                fecha_compra, concepto, cuenta_id, categoria_id, moneda["id"],
                to_minor(monto_total, dec), total_cuotas,
                to_minor(monto_por_cuota, dec), notas,
            ),
        )

        # Generar cuotas automáticamente
        fecha_dt = datetime.strptime(fecha_compra, "%Y-%m-%d")
        mes, anio = fecha_dt.month, fecha_dt.year

        for n in range(1, total_cuotas + 1):
            self._execute(
                """
                INSERT INTO cuotas_credito
                    (compra_id, numero_cuota, mes_proyectado, anio_proyectado, monto_cuota_minor)
                VALUES (?, ?, ?, ?, ?);
                """,
                (compra_id, n, mes, anio, to_minor(monto_por_cuota, dec)),
            )
            mes += 1
            if mes > 12:
                mes = 1
                anio += 1

        return compra_id

    def obtener_compras_cuotas(
        self, estado: Optional[str] = None
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT pc.*, c.nombre AS banco, m.codigo AS moneda_codigo
            FROM compras_cuotas pc
            JOIN cuentas c ON c.id = pc.cuenta_id
            JOIN monedas m ON m.id = pc.moneda_id
        """
        if estado:
            sql += " WHERE pc.estado = ?"
            return self._fetchall(sql + " ORDER BY pc.fecha_compra DESC;", (estado,))
        return self._fetchall(sql + " ORDER BY pc.fecha_compra DESC;")

    def obtener_cuotas_por_mes(
        self, mes: int, anio: int, estado: str = "pendiente"
    ) -> list[sqlite3.Row]:
        """Equivalente a tu vista mensual de Google Sheets: cuotas agrupadas por banco."""
        return self._fetchall(
            """
            SELECT
                c.nombre                AS banco,
                m.codigo                AS moneda,
                COUNT(*)                AS cantidad,
                SUM(qc.monto_cuota_minor) AS total_minor
            FROM cuotas_credito qc
            JOIN compras_cuotas pc ON pc.id = qc.compra_id
            JOIN cuentas c         ON c.id  = pc.cuenta_id
            JOIN monedas m         ON m.id  = pc.moneda_id
            WHERE qc.mes_proyectado  = ?
              AND qc.anio_proyectado = ?
              AND qc.estado          = ?
            GROUP BY c.nombre, m.codigo
            ORDER BY c.nombre;
            """,
            (mes, anio, estado),
        )

    def marcar_cuota_en_resumen(
        self,
        cuota_id:       int,
        resumen_id:     int,
        mes_real:       int,
        anio_real:      int,
    ):
        """Marca una cuota como 'en_resumen' cuando aparece en el resumen de tarjeta."""
        self._execute(
            """
            UPDATE cuotas_credito
            SET estado = 'en_resumen', resumen_id = ?,
                mes_real_pago = ?, anio_real_pago = ?
            WHERE id = ?;
            """,
            (resumen_id, mes_real, anio_real, cuota_id),
        )

    # ===========================================================
    # RESUMENES TARJETA
    # ===========================================================

    def crear_resumen_tarjeta(
        self,
        cuenta_id:     int,
        mes:           int,
        anio:          int,
        monto_consumos: float,
        monto_impuestos: float = 0.0,
        porcentaje_bp: int = 0,
        moneda_codigo: str = "ARS",
    ) -> int:
        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        dec = moneda["decimales"]
        total = monto_consumos + monto_impuestos
        return self._execute(
            """
            INSERT INTO resumenes_tarjeta
                (cuenta_id, mes, anio, monto_consumos_minor, monto_impuestos_minor,
                 porcentaje_impuesto_bp, monto_total_pagado_minor)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                cuenta_id, mes, anio,
                to_minor(monto_consumos, dec),
                to_minor(monto_impuestos, dec),
                porcentaje_bp,
                to_minor(total, dec),
            ),
        )

    def pagar_resumen_tarjeta(
        self,
        resumen_id:   int,
        cuenta_pago_id: int,
        fecha_pago:   str,
        categoria_id: int,
        moneda_codigo: str = "ARS",
    ) -> int:
        """
        Registra el pago del resumen: crea la transacción de egreso real
        y actualiza el estado del resumen a 'pagado'.
        Devuelve el id de la transacción generada.
        """
        resumen = self._fetchone(
            "SELECT * FROM resumenes_tarjeta WHERE id = ?;", (resumen_id,)
        )
        if resumen is None:
            raise ValueError(f"Resumen no encontrado: {resumen_id}")

        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        monto = from_minor(resumen["monto_total_pagado_minor"], moneda["decimales"])

        t_id = self.crear_transaccion(
            fecha=fecha_pago,
            concepto=f"Pago resumen tarjeta {resumen['mes']:02d}/{resumen['anio']}",
            cuenta_id=cuenta_pago_id,
            categoria_id=categoria_id,
            moneda_codigo=moneda_codigo,
            monto=monto,
            tipo_movimiento="egreso",
            tag="pago_tarjeta",
        )

        self._execute(
            """
            UPDATE resumenes_tarjeta
            SET estado = 'pagado', fecha_pago = ?
            WHERE id = ?;
            """,
            (fecha_pago, resumen_id),
        )
        # Marcar cuotas del resumen como pagadas
        self._execute(
            """
            UPDATE cuotas_credito SET estado = 'pagado'
            WHERE resumen_id = ? AND estado = 'en_resumen';
            """,
            (resumen_id,),
        )
        return t_id

    # ===========================================================
    # DEUDAS
    # ===========================================================

    def obtener_deudas(
        self, estado: Optional[str] = None, tipo: Optional[str] = None
    ) -> list[sqlite3.Row]:
        sql = """
            SELECT d.*, m.codigo AS moneda_codigo
            FROM deudas d JOIN monedas m ON m.id = d.moneda_id
            WHERE 1=1
        """
        params = []
        if estado: sql += " AND d.estado = ?"; params.append(estado)
        if tipo:   sql += " AND d.tipo = ?";   params.append(tipo)
        return self._fetchall(sql + " ORDER BY d.fecha_inicio DESC;", tuple(params))

    def obtener_deuda(self, deuda_id: int) -> Optional[sqlite3.Row]:
        return self._fetchone("SELECT * FROM deudas WHERE id = ?;", (deuda_id,))

    def crear_deuda(
        self,
        entidad_persona: str,
        tipo:            str,       # 'a_favor' | 'en_contra'
        monto:           float,
        moneda_codigo:   str,
        fecha_inicio:    str,
        fecha_vencimiento: Optional[str] = None,
        origen_tipo:     str = "manual",
        notas:           Optional[str] = None,
    ) -> int:
        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        monto_minor = to_minor(monto, moneda["decimales"])
        return self._execute(
            """
            INSERT INTO deudas
                (entidad_persona, tipo, monto_original_minor, monto_pendiente_minor,
                 moneda_id, fecha_inicio, fecha_vencimiento, origen_tipo, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                entidad_persona, tipo, monto_minor, monto_minor,
                moneda["id"], fecha_inicio, fecha_vencimiento, origen_tipo, notas,
            ),
        )

    def registrar_pago_deuda(
        self,
        deuda_id:       int,
        monto:          float,
        moneda_codigo:  str,
        fecha:          str,
        transaccion_id: Optional[int] = None,
        tipo_pago:      str = "transaccion",
        notas:          Optional[str] = None,
    ):
        """
        Aplica un pago (parcial o total) a una deuda.
        Actualiza monto_pendiente y estado automáticamente.
        """
        deuda = self.obtener_deuda(deuda_id)
        if deuda is None:
            raise ValueError(f"Deuda no encontrada: {deuda_id}")

        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        dec = moneda["decimales"]
        monto_minor = to_minor(monto, dec)

        self._execute(
            """
            INSERT INTO deuda_pagos
                (deuda_id, transaccion_id, monto_applied_minor, tipo_pago, notas, fecha)
            VALUES (?, ?, ?, ?, ?, ?);
            """,
            (deuda_id, transaccion_id, monto_minor, tipo_pago, notas, fecha),
        )

        nuevo_pendiente = max(0, deuda["monto_pendiente_minor"] - monto_minor)
        nuevo_estado = "saldada" if nuevo_pendiente == 0 else "activa"
        self._execute(
            """
            UPDATE deudas SET monto_pendiente_minor = ?, estado = ?
            WHERE id = ?;
            """,
            (nuevo_pendiente, nuevo_estado, deuda_id),
        )

    # ===========================================================
    # PRESUPUESTO
    # ===========================================================

    def obtener_presupuesto(
        self, mes: int, anio: int
    ) -> list[sqlite3.Row]:
        return self._fetchall(
            """
            SELECT p.*, cat.subcategoria, cat.categoria_principal, m.codigo AS moneda_codigo
            FROM presupuestos p
            JOIN categorias cat ON cat.id = p.categoria_id
            JOIN monedas     m  ON m.id   = p.moneda_id
            WHERE p.mes = ? AND p.anio = ?
            ORDER BY cat.categoria_principal, cat.subcategoria;
            """,
            (mes, anio),
        )

    def upsert_presupuesto(
        self,
        mes:          int,
        anio:         int,
        categoria_id: int,
        monto:        float,
        moneda_codigo: str = "ARS",
        es_recurrente: bool = False,
        notas:        Optional[str] = None,
    ) -> int:
        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        return self._execute(
            """
            INSERT INTO presupuestos
                (categoria_id, moneda_id, mes, anio, monto_estimado_minor, es_recurrente, notas)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(categoria_id, mes, anio)
            DO UPDATE SET monto_estimado_minor = excluded.monto_estimado_minor,
                          notas = excluded.notas;
            """,
            (
                categoria_id, moneda["id"], mes, anio,
                to_minor(monto, moneda["decimales"]),
                int(es_recurrente), notas,
            ),
        )

    def copiar_presupuesto(self, mes_origen: int, anio_origen: int, mes_dest: int, anio_dest: int):
        """Copia el presupuesto de un mes a otro (útil para meses recurrentes)."""
        rows = self.obtener_presupuesto(mes_origen, anio_origen)
        for row in rows:
            self._execute(
                """
                INSERT OR IGNORE INTO presupuestos
                    (categoria_id, moneda_id, mes, anio, monto_estimado_minor, es_recurrente, notas)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    row["categoria_id"], row["moneda_id"], mes_dest, anio_dest,
                    row["monto_estimado_minor"], row["es_recurrente"], row["notas"],
                ),
            )

    # ===========================================================
    # RECIBOS DE SUELDO
    # ===========================================================

    def crear_recibo_sueldo(
        self,
        empleo_id:          int,
        mes:                int,
        anio:               int,
        sueldo_bruto:       float,
        desc_jubilacion:    float,
        desc_obra_social:   float,
        desc_copagos:       float = 0.0,
        desc_otros:         float = 0.0,
        cuenta_id:          Optional[int] = None,
        moneda_codigo:      str = "ARS",
        notas:              Optional[str] = None,
    ) -> int:
        """
        Crea el recibo y opcionalmente la transacción de ingreso del neto.
        """
        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        dec = moneda["decimales"]
        neto = sueldo_bruto - desc_jubilacion - desc_obra_social - desc_copagos - desc_otros

        t_id = None
        if cuenta_id is not None:
            cat = self._fetchone(
                "SELECT id FROM categorias WHERE subcategoria = 'Sueldo';",
            )
            t_id = self.crear_transaccion(
                fecha=f"{anio}-{mes:02d}-01",
                concepto=f"Sueldo {mes:02d}/{anio}",
                cuenta_id=cuenta_id,
                categoria_id=cat["id"],
                moneda_codigo=moneda_codigo,
                monto=neto,
                tipo_movimiento="ingreso",
                tag="sueldo",
                notas=notas,
            )

        return self._execute(
            """
            INSERT INTO recibos_sueldo
                (empleo_id, mes, anio, sueldo_bruto_minor,
                 desc_jubilacion_minor, desc_obra_social_minor,
                 desc_copagos_os_minor, desc_otros_minor,
                 monto_neto_final_minor, transaccion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                empleo_id, mes, anio,
                to_minor(sueldo_bruto, dec),
                to_minor(desc_jubilacion, dec),
                to_minor(desc_obra_social, dec),
                to_minor(desc_copagos, dec),
                to_minor(desc_otros, dec),
                to_minor(neto, dec),
                t_id,
            ),
        )

    # ===========================================================
    # DESCUENTOS PROGRAMADOS
    # ===========================================================

    def crear_descuento_programado(
        self,
        concepto:       str,
        monto:          float,
        mes_aplicacion: int,
        anio_aplicacion: int,
        moneda_codigo:  str = "ARS",
        notas:          Optional[str] = None,
    ) -> int:
        moneda = self.obtener_moneda_por_codigo(moneda_codigo)
        return self._execute(
            """
            INSERT INTO descuentos_programados
                (concepto, monto_minor, mes_aplicacion, anio_aplicacion, notas)
            VALUES (?, ?, ?, ?, ?);
            """,
            (concepto, to_minor(monto, moneda["decimales"]), mes_aplicacion, anio_aplicacion, notas),
        )

    def obtener_descuentos_pendientes(self, mes: int, anio: int) -> list[sqlite3.Row]:
        return self._fetchall(
            """
            SELECT * FROM descuentos_programados
            WHERE mes_aplicacion = ? AND anio_aplicacion = ? AND estado = 'pendiente'
            ORDER BY concepto;
            """,
            (mes, anio),
        )

    def aplicar_descuento_programado(self, descuento_id: int, recibo_id: int):
        self._execute(
            """
            UPDATE descuentos_programados
            SET estado = 'aplicado', recibo_id = ?
            WHERE id = ?;
            """,
            (recibo_id, descuento_id),
        )

    # ===========================================================
    # TIPOS DE CAMBIO
    # ===========================================================

    def registrar_tipo_cambio(
        self,
        fecha:          str,
        moneda_origen:  str,
        moneda_destino: str,
        tasa:           float,
        fuente:         str = "manual",
    ) -> int:
        origen_id  = self.obtener_moneda_id(moneda_origen)
        destino_id = self.obtener_moneda_id(moneda_destino)
        # tasa se guarda con 4 decimales como minor (tasa * 10000)
        return self._execute(
            """
            INSERT INTO tipos_cambio
                (fecha, moneda_origen_id, moneda_destino_id, tasa_minor, fuente)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(fecha, moneda_origen_id, moneda_destino_id)
            DO UPDATE SET tasa_minor = excluded.tasa_minor, fuente = excluded.fuente;
            """,
            (fecha, origen_id, destino_id, round(tasa * 10000), fuente),
        )

    def obtener_tipo_cambio(
        self, fecha: str, moneda_origen: str, moneda_destino: str = "ARS"
    ) -> Optional[float]:
        """Devuelve la tasa de cambio para una fecha dada, o None si no existe."""
        origen_id  = self.obtener_moneda_id(moneda_origen)
        destino_id = self.obtener_moneda_id(moneda_destino)
        row = self._fetchone(
            """
            SELECT tasa_minor FROM tipos_cambio
            WHERE fecha <= ? AND moneda_origen_id = ? AND moneda_destino_id = ?
            ORDER BY fecha DESC LIMIT 1;
            """,
            (fecha, origen_id, destino_id),
        )
        return row["tasa_minor"] / 10000 if row else None

    # ===========================================================
    # VISTAS / PROYECCIONES
    # ===========================================================

    def resumen_mensual(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """Ingresos y egresos reales del mes, agrupados por moneda."""
        return self._fetchall(
            """
            SELECT
                m.codigo                                                        AS moneda,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN t.monto_minor ELSE 0 END)                        AS ingresos_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'egreso'
                         THEN t.monto_minor ELSE 0 END)                        AS egresos_minor,
                SUM(CASE WHEN t.tipo_movimiento = 'ingreso'
                         THEN t.monto_minor ELSE -t.monto_minor END)           AS balance_minor
            FROM transacciones t
            JOIN monedas m ON m.id = t.moneda_id
            WHERE strftime('%m', t.fecha) = printf('%02d', ?)
              AND strftime('%Y', t.fecha) = CAST(? AS TEXT)
            GROUP BY m.codigo;
            """,
            (mes, anio),
        )

    def balance_todas_las_cuentas(self) -> list[sqlite3.Row]:
        """Devuelve el saldo actual de todas las cuentas activas."""
        return self._fetchall("SELECT * FROM vw_balance_cuentas ORDER BY nombre;")

    def proyeccion_tarjeta_proximos_meses(
        self, meses: int = 6, moneda_codigo: str = "ARS"
    ) -> list[dict]:
        """
        Calcula cuánto se deberá pagar en tarjeta los próximos N meses.
        Útil para la pantalla de proyecciones.
        """
        hoy = datetime.today()
        resultado = []
        mes, anio = hoy.month, hoy.year
        for _ in range(meses):
            rows = self.obtener_cuotas_por_mes(mes, anio, estado="pendiente")
            total = sum(r["total_minor"] for r in rows if r["moneda"] == moneda_codigo)
            resultado.append({"mes": mes, "anio": anio, "total_minor": total})
            mes += 1
            if mes > 12:
                mes = 1
                anio += 1
        return resultado

    # ===========================================================
    # INFORMACIÓN Y DIAGNÓSTICO
    # ===========================================================

    def info_db(self) -> dict:
        """Devuelve estadísticas básicas de la base de datos."""
        tablas = [
            "cuentas", "transacciones", "compras_cuotas",
            "cuotas_credito", "deudas", "presupuestos", "recibos_sueldo",
        ]
        info = {"path": str(self.db_path), "tablas": {}}
        for tabla in tablas:
            row = self._fetchone(f"SELECT COUNT(*) AS n FROM {tabla};")
            info["tablas"][tabla] = row["n"] if row else 0
        return info
