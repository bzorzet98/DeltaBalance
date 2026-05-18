"""
DeltaBalance — db/query_builder.py

Propósito:
    Provee una interfaz fluida (fluent interface) para construir queries SQLite
    de forma programática y segura, sin concatenar strings SQL a mano en los servicios.

    En lugar de escribir:
        sql = "SELECT * FROM transacciones WHERE cuenta_id = ? AND fecha >= ? ORDER BY fecha DESC LIMIT 50"
        params = (cuenta_id, desde)

    Los servicios escriben:
        rows = (QueryBuilder("transacciones")
                    .where("cuenta_id", cuenta_id)
                    .where("fecha", desde, ">=")
                    .order("fecha", "DESC")
                    .limit(50)
                    .ejecutar(conn))

    Ventajas:
    - Todos los parámetros van como placeholders (?), nunca interpolados → sin SQL injection.
    - Los filtros opcionales se encadenan solo si tienen valor, sin bloques if dispersos.
    - Cambiar orden, límite o joins no requiere reescribir el string SQL completo.
    - Facilita el testing: se puede inspeccionar .build() antes de ejecutar.

    Limitaciones intencionales:
    - Solo soporta SELECT con JOINs simples. Para queries complejas con subconsultas
      o CTEs, usar SQL literal en database.py directamente. ##### NO IMPLEMENTADO SE VE
    - No soporta INSERT/UPDATE/DELETE — esas operaciones van en DatabaseManager.


    Que no hacer:
    NO lo convertiría en ORM.

    - NO agregaría: mapeo automático,relaciones mágicas,
    lazy loading,models activos,decorators raros.
    Porque perderías:claridad,control SQL,performance,debuggability.
"""

import sqlite3
from typing import Any, Optional
import copy

class QueryBuilder:
    """
    Constructor de queries SELECT para SQLite con interfaz fluida.

    Uso básico:
        rows = (QueryBuilder("transacciones")
                    .select("id", "fecha", "concepto", "monto_minor")
                    .join("cuentas c", "c.id = transacciones.cuenta_id")
                    .where("transacciones.cuenta_id", 3)
                    .where("transacciones.fecha", "2026-01-01", ">=")
                    .where_raw("monto_minor < 0")
                    .order("fecha", "DESC")
                    .limit(100)
                    .ejecutar(conn))
    """

    VALID_OPERATORS = {
        "=",
        "!=",
        ">",
        ">=",
        "<",
        "<=",
        "LIKE",
        "IS",
        "IS NOT"
    }

    VALID_JOIN_TYPES = {
        "INNER",
        "LEFT",
        "RIGHT",
        "FULL"
    }

    def __init__(self, tabla: str, include_deleted: bool = False):
        """
        Inicializa el builder con la tabla principal del FROM.

        Args:
            tabla: Nombre de la tabla base, con alias opcional. Ej: "transacciones t"
        """
        self._tabla       = tabla
        self._columnas:   list[str]          = []
        self._joins:      list[str]          = []
        self._condiciones: list[str]         = []
        self._params:     list[Any]          = []
        self._orden:      list[str]          = []
        self._limit_val:  Optional[int]      = None
        self._offset_val: Optional[int]      = None
        self._group_by:   list[str]          = []
        self._include_deleted = include_deleted

    # ----------------------------------------------------------
    # SELECCIÓN DE COLUMNAS
    # ----------------------------------------------------------

    def select(self, *columnas: str) -> "QueryBuilder":
        """
        Define qué columnas incluir en el SELECT.
        Si no se llama, el builder usa SELECT * por defecto.

        Args:
            *columnas: Nombres de columnas o expresiones SQL.
                       Ej: "id", "fecha", "SUM(monto_minor) AS total"

        Returns:
            self — para encadenamiento fluido.
        """
        self._columnas.extend(columnas)
        return self

    # ----------------------------------------------------------
    # JOINS
    # ----------------------------------------------------------

    def join(self, tabla: str, condicion: str, tipo: str = "INNER") -> "QueryBuilder":
        """
        Agrega un JOIN a la query.

        Args:
            tabla:     Tabla y alias. Ej: "cuentas c"
            condicion: Condición de unión. Ej: "c.id = transacciones.cuenta_id"
            tipo:      Tipo de JOIN: "INNER", "LEFT", "RIGHT". Default: "INNER"

        Returns:
            self — para encadenamiento fluido.

        Ejemplo:
            .join("monedas m", "m.id = t.moneda_id", "LEFT")
        """
        tipo = tipo.upper()

        if tipo not in self.VALID_JOIN_TYPES:
            raise ValueError(
                f"Tipo de JOIN inválido: {tipo}. "
                f"Use uno de: {', '.join(self.VALID_JOIN_TYPES)}"
            )
        
        self._joins.append(f"{tipo} JOIN {tabla} ON {condicion}")
        return self

    def left_join(self, tabla: str, condicion: str) -> "QueryBuilder":
        """
        Atajo para LEFT JOIN. Equivalente a .join(tabla, condicion, 'LEFT').

        Args:
            tabla:     Tabla y alias.
            condicion: Condición de unión.

        Returns:
            self — para encadenamiento fluido.
        """
        return self.join(tabla, condicion, "LEFT")

    # ----------------------------------------------------------
    # FILTROS (WHERE)
    # ----------------------------------------------------------

    def where(
        self,
        columna:  str,
        valor:    Any,
        operador: str = "=",
    ) -> "QueryBuilder":
        """
        Agrega una condición WHERE segura con placeholder (?).
        Ignora el filtro automáticamente si valor es None,
        lo que permite filtros opcionales sin bloques if externos.

        Args:
            columna:  Nombre de columna o expresión. Ej: "t.fecha", "monto_minor"
            valor:    Valor a comparar. Si es None, el filtro se omite.
            operador: Operador SQL. Default "=". Otros: "!=", ">", ">=", "<", "<=", "LIKE"

        Returns:
            self — para encadenamiento fluido.

        Ejemplo:
            .where("cuenta_id", cuenta_id)          # solo filtra si cuenta_id no es None
            .where("fecha", "2026-01-01", ">=")
            .where("concepto", "%nafta%", "LIKE")
        """
        
        if valor is None:
            return self
        
        operador = operador.upper()

        if operador not in self.VALID_OPERATORS:
            raise ValueError(
                f"Operador inválido: {operador}. "
                f"Use uno de: {', '.join(self.VALID_OPERATORS)}"
            )
        
        self._condiciones.append(f"{columna} {operador} ?")
        self._params.append(valor)
        return self

    def where_in(self, columna: str, valores: list[Any]) -> "QueryBuilder":
        """
        Agrega una condición WHERE columna IN (v1, v2, ...).
        Ignora el filtro si la lista está vacía.

        Args:
            columna: Nombre de columna.
            valores: Lista de valores. Si está vacía, el filtro se omite.

        Returns:
            self — para encadenamiento fluido.

        Ejemplo:
            .where_in("estado", ["pendiente", "en_resumen"])
        """
        if not valores:
            return self
        placeholders = ", ".join("?" * len(valores))
        self._condiciones.append(f"{columna} IN ({placeholders})")
        self._params.extend(valores)
        return self

    def where_between(
        self, columna: str, desde: Any, hasta: Any
    ) -> "QueryBuilder":
        """
        Agrega una condición WHERE columna BETWEEN desde AND hasta.
        Solo aplica el filtro si ambos valores están presentes.

        Args:
            columna: Nombre de columna.
            desde:   Límite inferior del rango.
            hasta:   Límite superior del rango.

        Returns:
            self — para encadenamiento fluido.

        Ejemplo:
            .where_between("fecha", "2026-01-01", "2026-01-31")
        """
        if desde is not None and hasta is not None:
            self._condiciones.append(f"{columna} BETWEEN ? AND ?")
            self._params.extend([desde, hasta])
        elif desde is not None:
            self.where(columna, desde, ">=")
        elif hasta is not None:
            self.where(columna, hasta, "<=")
        return self

    def where_raw(self, expresion: str, *params: Any) -> "QueryBuilder":
        """
        Agrega una condición WHERE como SQL literal.
        Usar solo cuando los métodos tipados no alcanzan.
        Los valores deben pasarse como parámetros adicionales, nunca interpolados.

        Args:
            expresion: Expresión SQL con placeholders (?). Ej: "monto_minor < 0"
            *params:   Valores para los placeholders de la expresión.

        Returns:
            self — para encadenamiento fluido.

        Ejemplo:
            .where_raw("strftime('%Y', fecha) = ?", "2026")
            .where_raw("monto_minor < 0")
        """
        self._condiciones.append(expresion)
        self._params.extend(params)
        return self

    # ----------------------------------------------------------
    # AGRUPACIÓN
    # ----------------------------------------------------------

    def group_by(self, *columnas: str) -> "QueryBuilder":
        """
        Agrega columnas al GROUP BY.

        Args:
            *columnas: Columnas o expresiones por las cuales agrupar.

        Returns:
            self — para encadenamiento fluido.

        Ejemplo:
            .select("moneda_id", "SUM(monto_minor) AS total")
            .group_by("moneda_id")
        """
        self._group_by.extend(columnas)
        return self

    # ----------------------------------------------------------
    # ORDENAMIENTO
    # ----------------------------------------------------------

    def order(self, columna: str, direccion: str = "ASC") -> "QueryBuilder":
        """
        Agrega una columna al ORDER BY.
        Se pueden encadenar múltiples llamadas para orden compuesto.

        Args:
            columna:    Nombre de columna o expresión.
            direccion:  "ASC" o "DESC". Default: "ASC"

        Returns:
            self — para encadenamiento fluido.

        Ejemplo:
            .order("fecha", "DESC")
            .order("id", "DESC")   # desempate por id
        """
        direccion = direccion.upper()
        if direccion not in ("ASC", "DESC"):
            raise ValueError(f"Dirección de orden inválida: {direccion}. Use 'ASC' o 'DESC'.")
        self._orden.append(f"{columna} {direccion}")
        return self

    # ----------------------------------------------------------
    # PAGINACIÓN
    # ----------------------------------------------------------

    def limit(self, n: int) -> "QueryBuilder":
        """
        Limita la cantidad de filas devueltas.

        Args:
            n: Número máximo de filas. Debe ser positivo.

        Returns:
            self — para encadenamiento fluido.
        """
        if n <= 0:
            raise ValueError(f"El límite debe ser positivo. Recibido: {n}")
        self._limit_val = n
        return self

    def offset(self, n: int) -> "QueryBuilder":
        """
        Omite las primeras N filas del resultado. Úsalo junto con .limit() para paginar.

        Args:
            n: Cantidad de filas a saltar. Debe ser no negativo.

        Returns:
            self — para encadenamiento fluido.

        Ejemplo — página 2 con 20 ítems por página:
            .limit(20).offset(20)
        """
        if n < 0:
            raise ValueError(f"El offset no puede ser negativo. Recibido: {n}")
        self._offset_val = n
        return self

    def paginar(self, pagina: int, por_pagina: int = 50) -> "QueryBuilder":
        """
        Atajo para paginación basada en número de página (1-indexed).
        Equivalente a .limit(por_pagina).offset((pagina - 1) * por_pagina)

        Args:
            pagina:    Número de página, comenzando en 1.
            por_pagina: Registros por página. Default: 50.

        Returns:
            self — para encadenamiento fluido.

        Ejemplo:
            .paginar(2)          # página 2, 50 ítems
            .paginar(3, 20)      # página 3, 20 ítems
        """
        if pagina < 1:
            raise ValueError(f"El número de página debe ser >= 1. Recibido: {pagina}")
        return self.limit(por_pagina).offset((pagina - 1) * por_pagina)

    # ----------------------------------------------------------
    # CONSTRUCCIÓN Y EJECUCIÓN
    # ----------------------------------------------------------
    def build(self) -> tuple[str, list[Any]]:
        """
        Constructs the SQL string and parameter list without executing.
        Useful for debugging, logging, or testing.

        The soft delete filter (deleted_at IS NULL) is injected automatically
        as the first condition unless include_deleted=True was set on init.

        Returns:
            Tuple (sql_string, list_of_parameters)

        Example:
            sql, params = builder.build()
            print(sql)      # SELECT * FROM transacciones WHERE deleted_at IS NULL AND cuenta_id = ?
            print(params)   # [3]
        """
        columnas = ", ".join(self._columnas) if self._columnas else "*"
        sql = f"SELECT {columnas} FROM {self._tabla}"

        if self._joins:
            sql += " " + " ".join(self._joins)

        # Inject soft delete filter as the first condition before building WHERE
        condiciones = list(self._condiciones)
        if not self._include_deleted:
            condiciones.insert(0, "deleted_at IS NULL")

        if condiciones:
            sql += " WHERE " + " AND ".join(condiciones)

        if self._group_by:
            sql += " GROUP BY " + ", ".join(self._group_by)

        if self._orden:
            sql += " ORDER BY " + ", ".join(self._orden)

        if self._limit_val is not None and not self._orden:
            print(
                "[QueryBuilder WARNING] LIMIT used without ORDER BY. "
                "Result order may be non-deterministic."
            )

        if self._limit_val is not None:
            sql += f" LIMIT {self._limit_val}"

        if self._offset_val is not None:
            sql += f" OFFSET {self._offset_val}"

        return sql, list(self._params)

    def ejecutar(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        """
        Construye y ejecuta la query sobre la conexión dada.
        Devuelve todas las filas como lista de sqlite3.Row (acceso por nombre de columna).

        Args:
            conn: Conexión SQLite activa (con row_factory = sqlite3.Row).

        Returns:
            Lista de sqlite3.Row. Vacía si no hay resultados.

        Ejemplo:
            filas = builder.ejecutar(db.conn)
            for f in filas:
                print(f["fecha"], f["monto_minor"])
        """
        sql, params = self.build()
        return conn.execute(sql, params).fetchall()

    def ejecutar_uno(self, conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
        """
        Construye y ejecuta la query, devolviendo solo la primera fila.
        Equivalente a ejecutar()[0] pero sin traer todas las filas al mismo tiempo.

        Args:
            conn: Conexión SQLite activa.

        Returns:
            sqlite3.Row si existe al menos una fila, None si no hay resultados.

        Ejemplo:
            cuenta = (QueryBuilder("cuentas")
                         .where("nombre", "Galicia")
                         .ejecutar_uno(db.conn))
            if cuenta:
                print(cuenta["id"])
        """
        sql, params = self.build()
        return conn.execute(sql, params).fetchone()

    def contar(self, conn: sqlite3.Connection) -> int:
        """
        Ejecuta una versión COUNT(*) de la query actual para obtener
        el total de filas sin traerlas todas.

        Si la query tiene GROUP BY, se envuelve en una subquery
        para contar correctamente los grupos resultantes.
        """

        # ------------------------------------------------------
        # CASO SIMPLE (sin GROUP BY)
        # ------------------------------------------------------

        if not self._group_by:

            sql = f"SELECT COUNT(*) FROM {self._tabla}"

            if self._joins:
                sql += " " + " ".join(self._joins)

            if self._condiciones:
                sql += " WHERE " + " AND ".join(self._condiciones)

            row = conn.execute(sql, self._params).fetchone()

            return row[0] if row else 0

        # ------------------------------------------------------
        # CASO CON GROUP BY
        # ------------------------------------------------------

        sql, params = self.build()

        subquery = f"""
            SELECT COUNT(*)
            FROM (
                {sql}
            ) AS grouped_query
        """

        row = conn.execute(subquery, params).fetchone()

        return row[0] if row else 0
    # ----------------------------------------------------------
    # REPRESENTACIÓN PARA DEBUGGING
    # ----------------------------------------------------------
    def clone(self) -> "QueryBuilder":
        """
        Devuelve una copia profunda del builder actual.

        Útil para reutilizar una query base sin mutar
        el objeto original.

        Ejemplo:
            base = QueryBuilder("transacciones").where("cuenta_id", 1)

            total = base.clone().contar(conn)

            filas = (
                base.clone()
                    .order("fecha", "DESC")
                    .paginar(1, 50)
                    .ejecutar(conn)
            )
        """
        return copy.deepcopy(self)
    
    def __repr__(self) -> str:
        """
        Muestra la query SQL construida al imprimir o inspeccionar el objeto.
        No ejecuta nada — solo sirve para debugging rápido en terminal o logs.

        Ejemplo:
            print(builder)
            # SELECT * FROM transacciones WHERE cuenta_id = ? ORDER BY fecha DESC LIMIT 50
            # params: [3]
        """
        sql, params = self.build()
        return f"QueryBuilder:\n  SQL: {sql}\n  params: {params}"