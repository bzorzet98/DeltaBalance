"""
DeltaBalance — repositories/empleos_repository.py

Acceso a datos para las tablas `empleos`, `recibos_sueldo` y
`descuentos_programados`. Las tres van juntas en un solo repositorio porque
recibos_sueldo y descuentos_programados están fuertemente acoplados a
empleos (un recibo no existe sin un empleo; un descuento programado apunta
opcionalmente a un recibo ya aplicado) — mismo criterio que
compras_cuotas_repository.py/cuotas_credito_repository.py yendo en archivos
separados pero relacionados, salvo que acá entran los tres en el mismo
archivo porque el volumen de métodos es menor.

Sin lógica de negocio: no calcula el neto de un recibo (sueldo_bruto -
descuentos), no decide qué porcentajes de jubilación/obra social aplican, no
valida que un descuento programado "corresponda" a un recibo — todo eso
vive en un futuro EmpleosService (Fase 2, paso 2, todavía no existe).

Hallazgos de la revisión de db/database.py (Fase 2, bloque PRESUPUESTOS /
INGRESOS PROYECTADOS / EMPLEOS, paso 1) que determinan la forma de este
repositorio — importantes para el paso 2:

1. Ningún service existente (TransactionService, DebtsService, FeesService)
   usa `empleos`/`recibos_sueldo`/`descuentos_programados` hoy (confirmado
   por grep).

2. crear_recibo_sueldo() en db/database.py HOY hace más de lo que parece:
   si se le pasa `cuenta_id`, además de insertar el recibo, crea una
   TRANSACCIÓN de ingreso llamando a self.crear_transaccion() — una
   escritura cruzada a la tabla `transacciones`, que NO es de este
   repositorio (la administra TransaccionesRepository). Un repositorio
   nunca debe insertar en una tabla ajena directamente (CLAUDE.md sección
   3). Por eso crear_recibo() acá SOLO inserta en recibos_sueldo y recibe
   `transaccion_id` ya resuelto (Optional, default None) en vez de crear la
   transacción — la orquestación real (abrir una transacción externa,
   llamar a TransaccionesRepository.crear(conn=...) para el ingreso, tomar
   su id, y recién ahí llamar a crear_recibo(transaccion_id=t_id,
   conn=...)) es trabajo del futuro EmpleosService, no de este repositorio.
   Por el mismo motivo, `notas` NO es parámetro de crear_recibo(): en el
   método original ese `notas` nunca se guardaba en recibos_sueldo (no
   existe esa columna en el schema) — solo se pasaba al crear la
   transacción. Acá no hay transacción que crear, así que no hay lugar
   donde ese `notas` tendría sentido.

3. HALLAZGO IMPORTANTE (contradice la suposición original de la consigna):
   aplicar_descuento_programado() NO es una operación atómica de dos
   tablas. Lee el código real:
       UPDATE descuentos_programados SET estado = 'aplicado', recibo_id = ?
       WHERE id = ?;
   Eso es TODO lo que hace — una sola tabla, un solo UPDATE.
   `recibos_sueldo` no tiene ninguna columna que acumule "total de
   descuentos aplicados" ni nada que aplicar_descuento_programado() escriba
   ahí. El flujo real es secuencial, no atómico-conjunto: primero existe un
   recibo (creado por separado, con crear_recibo()), y DESPUÉS, uno por
   uno, cada descuento pendiente se marca 'aplicado' apuntando a ese
   recibo_id ya existente vía marcar_descuento_aplicado(). Por eso
   marcar_descuento_aplicado() acá acepta `conn` (tal como pide la
   consigna, por si un futuro service quiere agruparlo con alguna otra
   escritura), pero NO participa de ninguna escritura atómica real con
   crear_recibo() — no existe tal atomicidad hoy en el código real.

crear_recibo()/crear_descuento_programado() reciben montos ya en minor
units (no floats) — igual que el resto de los repositorios de esta fase;
convertir a minor y calcular el neto es responsabilidad de quien llama.

`empleos`/`recibos_sueldo`/`descuentos_programados` NO tienen columna
`deleted_at` — por eso los métodos de lectura usan QueryBuilder con
include_deleted=True hardcodeado, mismo motivo que en los repositorios
anteriores sin esa columna. `empleos` sí tiene `activa` (como `cuentas`/
`categorias`), usada por listar_empleos(solo_activos=True).

Agregado en Fase 2, bloque EMPLEOS paso 2c (para EmpleosService, sin
reimplementar SQL en el service — CLAUDE.md sección 3): obtener_recibo_por_id()
y obtener_descuento_por_id(), ambos lookups simples por PK que faltaban
para que apply_discount() pueda validar existencia sin escribir la query
en la capa de servicio.
"""

import sqlite3
from typing import Optional

from db.database import DatabaseManager
from db.query_builder import QueryBuilder


class EmpleosRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db

    # ----------------------------------------------------------
    # EMPLEOS
    # ----------------------------------------------------------

    def crear_empleo(
        self,
        nombre_empresa: str,
        moneda_id: int,
        puesto: Optional[str] = None,
        porcentaje_jubilacion: int = 1100,
        porcentaje_obra_social: int = 300,
        porcentaje_gremio: int = 0,
        tope_copago_os_minor: int = 0,
    ) -> int:
        """
        Inserta un empleo. Los porcentajes (en basis points) tienen los
        mismos defaults que el schema (1100 = 11% jubilación, 300 = 3% obra
        social, 0% gremio) — se pasan explícitos en el INSERT en vez de
        confiar en el default de columna, para que quede claro en el propio
        INSERT qué valores tiene cada fila.
        """
        return self._db.execute(
            """
            INSERT INTO empleos
                (nombre_empresa, puesto, moneda_id, porcentaje_jubilacion,
                 porcentaje_obra_social, porcentaje_gremio, tope_copago_os_minor)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """,
            (
                nombre_empresa, puesto, moneda_id, porcentaje_jubilacion,
                porcentaje_obra_social, porcentaje_gremio, tope_copago_os_minor,
            ),
        )

    def obtener_empleo_por_id(self, empleo_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("empleos", include_deleted=True)
            .where("id", empleo_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar_empleos(self, solo_activos: bool = True) -> list[sqlite3.Row]:
        builder = QueryBuilder("empleos", include_deleted=True)
        if solo_activos:
            builder = builder.where("activa", 1)
        return builder.order("nombre_empresa").ejecutar(self._db.conn)

    # ----------------------------------------------------------
    # RECIBOS DE SUELDO
    # ----------------------------------------------------------

    def crear_recibo(
        self,
        empleo_id: int,
        mes: int,
        anio: int,
        sueldo_bruto_minor: int,
        desc_jubilacion_minor: int,
        desc_obra_social_minor: int,
        monto_neto_final_minor: int,
        desc_copagos_os_minor: int = 0,
        desc_otros_minor: int = 0,
        transaccion_id: Optional[int] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """
        Inserta un recibo de sueldo. SOLO toca recibos_sueldo — ver
        docstring del módulo (hallazgo 2) sobre por qué no crea ninguna
        transacción acá: `transaccion_id` se recibe ya resuelto.
        monto_neto_final_minor también se recibe ya calculado — la resta
        (bruto - descuentos) es aritmética de negocio, no de este
        repositorio.

        Acepta `conn` para que un futuro service pueda insertar la
        transacción de ingreso (vía TransaccionesRepository.crear(conn=...))
        y este recibo en la misma transacción externa.
        """
        sql = """
            INSERT INTO recibos_sueldo
                (empleo_id, mes, anio, sueldo_bruto_minor,
                 desc_jubilacion_minor, desc_obra_social_minor,
                 desc_copagos_os_minor, desc_otros_minor,
                 monto_neto_final_minor, transaccion_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        params = (
            empleo_id, mes, anio, sueldo_bruto_minor,
            desc_jubilacion_minor, desc_obra_social_minor,
            desc_copagos_os_minor, desc_otros_minor,
            monto_neto_final_minor, transaccion_id,
        )
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    def obtener_recibo_por_periodo(self, empleo_id: int, mes: int, anio: int) -> Optional[sqlite3.Row]:
        """Busca por el UNIQUE(empleo_id, mes, anio) de la tabla."""
        return (
            QueryBuilder("recibos_sueldo", include_deleted=True)
            .where("empleo_id", empleo_id)
            .where("mes", mes)
            .where("anio", anio)
            .ejecutar_uno(self._db.conn)
        )

    def obtener_recibo_por_id(self, recibo_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("recibos_sueldo", include_deleted=True)
            .where("id", recibo_id)
            .ejecutar_uno(self._db.conn)
        )

    # ----------------------------------------------------------
    # DESCUENTOS PROGRAMADOS
    # ----------------------------------------------------------

    def crear_descuento_programado(
        self,
        concepto: str,
        monto_minor: int,
        mes_aplicacion: int,
        anio_aplicacion: int,
        notas: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Inserta un descuento programado. estado arranca en 'pendiente' (default de columna)."""
        sql = """
            INSERT INTO descuentos_programados
                (concepto, monto_minor, mes_aplicacion, anio_aplicacion, notas)
            VALUES (?, ?, ?, ?, ?);
        """
        params = (concepto, monto_minor, mes_aplicacion, anio_aplicacion, notas)
        if conn is not None:
            return conn.execute(sql, params).lastrowid
        return self._db.execute(sql, params)

    def obtener_descuento_por_id(self, descuento_id: int) -> Optional[sqlite3.Row]:
        return (
            QueryBuilder("descuentos_programados", include_deleted=True)
            .where("id", descuento_id)
            .ejecutar_uno(self._db.conn)
        )

    def listar_descuentos_pendientes(self, mes: int, anio: int) -> list[sqlite3.Row]:
        """Réplica exacta de obtener_descuentos_pendientes(): estado='pendiente', ordenado por concepto."""
        return (
            QueryBuilder("descuentos_programados", include_deleted=True)
            .where("mes_aplicacion", mes)
            .where("anio_aplicacion", anio)
            .where("estado", "pendiente")
            .order("concepto")
            .ejecutar(self._db.conn)
        )

    def marcar_descuento_aplicado(
        self,
        descuento_id: int,
        recibo_id: int,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """
        Marca un descuento programado como 'aplicado', apuntando a
        recibo_id. Réplica exacta de aplicar_descuento_programado() — ver
        docstring del módulo (hallazgo 3): es una escritura de UNA sola
        tabla, no un par atómico con recibos_sueldo. Acepta `conn` porque lo
        pide la consigna de este bloque, no porque exista hoy una
        contraparte real con la que deba ser atómico.
        """
        sql = "UPDATE descuentos_programados SET estado = 'aplicado', recibo_id = ? WHERE id = ?;"
        params = (recibo_id, descuento_id)
        if conn is not None:
            conn.execute(sql, params)
            return
        self._db.conn.execute(sql, params)
        self._db.conn.commit()
