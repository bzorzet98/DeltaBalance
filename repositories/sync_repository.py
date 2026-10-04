"""
DeltaBalance — repositories/sync_repository.py

Acceso a datos GENÉRICO para la sincronización con Supabase
(sync/sync_engine.py, docs/DATA_MODEL_DECISIONS.md sección 24): lee y
escribe filas de cualquier tabla de TABLAS_SINCRONIZADAS por su clave, y
maneja las tablas de control sync_cambios / sync_estado que preparan los
triggers de db/schema_migrations.py (preparar_sync()).

Por qué un repositorio genérico y no un método en cada repositorio de
entidad: la sync copia filas tal cual (columna → valor) sin conocer su
significado — un espejo, no una operación de negocio —, y hacerlo por
entidad obligaría a tocar quince repositorios con el mismo código. Sin
lógica de negocio: no decide qué fila gana un conflicto ni qué se sube; eso
es de SyncEngine.

Nombres de tabla y columna: solo los de TABLAS_SINCRONIZADAS y los que
devuelve PRAGMA table_info (nunca texto que venga de afuera sin validar),
porque se interpolan en el SQL.

Escrituras de la sync: siempre dentro de escritura_sync(), que pone la
MARCA_ESCRITURA_SYNC en sync_estado dentro de la misma transacción (y la
saca antes del COMMIT): así los triggers no vuelven a marcar como
pendiente lo que la propia sync escribe. sync_estado guarda además otras
claves de la sync que SÍ se comitean (estado() / guardar_estado(), ej.
REPARACION_REFERENCIAS de sync/sync_engine.py): los triggers solo miran
MARCA_ESCRITURA_SYNC.

Estructura para sync/referencias.py (docs/DATA_MODEL_DECISIONS.md sección
27): claves_foraneas(), columnas_requeridas(), buscar(),
id_por_clave_natural() y crear_fila() — leen el schema y las filas, no
deciden qué referencia se traduce ni cómo.
"""

import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from db.database import DatabaseManager
from db.schema_migrations import MARCA_ESCRITURA_SYNC, SEPARADOR_CLAVE, TABLAS_SINCRONIZADAS, claves_primarias
from repositories._ids import nuevo_id

# Columnas que no viajan (sincronizado_en es local de cada base).
COLUMNAS_LOCALES = ("sincronizado_en",)

# Claves naturales (los UNIQUE de db/schema.sql): una base nueva trae del
# seed categorías y la cuenta "Caja Efectivo" con UUIDs propios, distintos
# de los de la base que se está restaurando. Al bajar una de esas filas, si
# acá ya hay una con la misma clave natural y otro id, se le cambia el id
# (y el de sus hijos) al remoto en vez de chocar con el UNIQUE.
CLAVES_NATURALES: dict[str, tuple[str, ...]] = {
    "categorias": ("categoria_principal", "subcategoria"),
    "cuentas": ("nombre",),
    "hogares": ("codigo_invitacion",),
}


class SyncRepository:
    def __init__(self, db: DatabaseManager):
        self._db = db
        self._info_cache: dict[str, list[dict]] = {}
        self._fks_cache: dict[str, list[dict]] = {}

    # ----------------------------------------------------------
    # ESTRUCTURA
    # ----------------------------------------------------------

    @staticmethod
    def _validar_tabla(tabla: str) -> str:
        if tabla not in TABLAS_SINCRONIZADAS:
            raise ValueError(f"'{tabla}' no es una tabla sincronizada.")
        return tabla

    def _info(self, tabla: str) -> list[dict]:
        """PRAGMA table_info de la tabla (name, notnull, dflt_value, pk…), leído una vez."""
        if tabla not in self._info_cache:
            filas = self._db.conn.execute(f"PRAGMA table_info({self._validar_tabla(tabla)});").fetchall()
            self._info_cache[tabla] = [dict(fila) for fila in filas]
        return self._info_cache[tabla]

    def columnas(self, tabla: str) -> list[str]:
        """Columnas reales de la tabla en esta base (PRAGMA table_info)."""
        return [columna["name"] for columna in self._info(tabla)]

    def columnas_requeridas(self, tabla: str) -> list[str]:
        """Las que un INSERT tiene que traer sí o sí: NOT NULL, sin DEFAULT y fuera de la clave primaria."""
        return [
            columna["name"] for columna in self._info(tabla)
            if columna["notnull"] and columna["dflt_value"] is None and not columna["pk"]
        ]

    def claves_foraneas(self, tabla: str) -> list[dict]:
        """
        Las FK declaradas de la tabla (PRAGMA foreign_key_list), de a una
        columna: [{columna, tabla, columna_ref, nullable}] — `tabla` es la
        referenciada y `columna_ref` la columna a la que apunta (su clave
        primaria si el schema no la nombra). Una FK de varias columnas se
        saltea (el schema no tiene ninguna).
        """
        if tabla not in self._fks_cache:
            nullables = {columna["name"]: not columna["notnull"] for columna in self._info(tabla)}
            por_fk: dict[int, list] = {}
            for fk in self._db.conn.execute(f"PRAGMA foreign_key_list({self._validar_tabla(tabla)});").fetchall():
                por_fk.setdefault(fk["id"], []).append(fk)
            self._fks_cache[tabla] = [
                {
                    "columna": fk["from"],
                    "tabla": fk["table"],
                    "columna_ref": fk["to"] or claves_primarias(fk["table"])[0],
                    "nullable": nullables.get(fk["from"], True),
                }
                for partes in por_fk.values() if len(partes) == 1
                for fk in partes
            ]
        return self._fks_cache[tabla]

    @staticmethod
    def clave_de(tabla: str, fila: dict) -> str:
        return SEPARADOR_CLAVE.join(str(fila[columna]) for columna in claves_primarias(tabla))

    @staticmethod
    def valores_de_clave(tabla: str, clave: str) -> tuple[str, ...]:
        """Inversa de clave_de() (como texto: SQLite aplica la afinidad de la columna al comparar)."""
        columnas = claves_primarias(tabla)
        return tuple(clave.split(SEPARADOR_CLAVE, len(columnas) - 1))

    def _where_clave(self, tabla: str) -> str:
        return " AND ".join(f"{columna} = ?" for columna in claves_primarias(tabla))

    # ----------------------------------------------------------
    # LECTURA
    # ----------------------------------------------------------

    def fila(self, tabla: str, clave: str) -> Optional[dict]:
        fila = self._db.conn.execute(
            f"SELECT * FROM {self._validar_tabla(tabla)} WHERE {self._where_clave(tabla)};",
            self.valores_de_clave(tabla, clave),
        ).fetchone()
        return dict(fila) if fila is not None else None

    def todas(self, tabla: str) -> list[dict]:
        return [dict(f) for f in self._db.conn.execute(f"SELECT * FROM {self._validar_tabla(tabla)};").fetchall()]

    def buscar(self, tabla: str, columna: str, valor: Any) -> Optional[dict]:
        """La fila con columna = valor (la primera), o None. `columna` tiene que ser una columna real: se interpola en el SQL."""
        if columna not in self.columnas(tabla):
            raise ValueError(f"'{columna}' no es una columna de '{tabla}'.")
        fila = self._db.conn.execute(f"SELECT * FROM {tabla} WHERE {columna} = ? LIMIT 1;", (valor,)).fetchone()
        return dict(fila) if fila is not None else None

    def id_por_clave_natural(self, tabla: str, valores: dict) -> Optional[str]:
        """
        El id de la fila de esta base con la misma clave natural
        (CLAVES_NATURALES) que `valores`. None si la tabla no tiene clave
        natural, si a `valores` le falta alguna de sus columnas, o si no hay
        ninguna fila así.
        """
        columnas = CLAVES_NATURALES.get(self._validar_tabla(tabla))
        if not columnas or any(columna not in valores for columna in columnas):
            return None
        fila = self._db.conn.execute(
            f"SELECT id FROM {tabla} WHERE " + " AND ".join(f"{c} = ?" for c in columnas) + ";",
            tuple(valores[c] for c in columnas),
        ).fetchone()
        return fila["id"] if fila is not None else None

    def estado(self, clave: str) -> Optional[str]:
        """El valor de `clave` en sync_estado, o None si no está."""
        fila = self._db.conn.execute("SELECT valor FROM sync_estado WHERE clave = ?;", (clave,)).fetchone()
        return fila["valor"] if fila is not None else None

    def cambio(self, tabla: str, clave: str) -> Optional[dict]:
        """La entrada de sync_cambios de esa fila, o None si no cambió desde la última sync."""
        fila = self._db.conn.execute(
            "SELECT * FROM sync_cambios WHERE tabla = ? AND clave = ?;", (tabla, clave),
        ).fetchone()
        return dict(fila) if fila is not None else None

    def pendientes(self, tabla: str) -> list[dict]:
        """
        Lo que hay que subir de una tabla: [{clave, operacion, modificado_en,
        fila (dict o None si se borró)}]. Son las entradas de sync_cambios y,
        además, las filas que nunca se sincronizaron (sincronizado_en NULL)
        sin entrada — las que ya existían antes de los triggers —, con
        modificado_en None.
        """
        self._validar_tabla(tabla)
        pendientes: list[dict] = []
        vistas: set[str] = set()
        for cambio in self._db.conn.execute(
            "SELECT clave, operacion, modificado_en FROM sync_cambios WHERE tabla = ? ORDER BY modificado_en;",
            (tabla,),
        ).fetchall():
            fila = self.fila(tabla, cambio["clave"])
            pendientes.append({
                "clave": cambio["clave"],
                # Una fila 'guardado' que ya no existe se borró después (y viceversa).
                "operacion": "guardado" if fila is not None else "borrado",
                "modificado_en": cambio["modificado_en"],
                "fila": fila,
            })
            vistas.add(cambio["clave"])
        for fila in self._db.conn.execute(f"SELECT * FROM {tabla} WHERE sincronizado_en IS NULL;").fetchall():
            fila = dict(fila)
            clave = self.clave_de(tabla, fila)
            if clave not in vistas:
                pendientes.append({"clave": clave, "operacion": "guardado", "modificado_en": None, "fila": fila})
        return pendientes

    def contar_pendientes(self) -> int:
        """Cuántas filas de todas las tablas sincronizadas faltan subir."""
        total = self._db.conn.execute("SELECT COUNT(*) FROM sync_cambios;").fetchone()[0]
        for tabla in TABLAS_SINCRONIZADAS:
            separador = f" || '{SEPARADOR_CLAVE}' || "
            clave_sql = separador.join(f"CAST(t.{columna} AS TEXT)" for columna in claves_primarias(tabla))
            sql = (
                f"SELECT COUNT(*) FROM {tabla} t WHERE t.sincronizado_en IS NULL AND NOT EXISTS "
                f"(SELECT 1 FROM sync_cambios c WHERE c.tabla = ? AND c.clave = {clave_sql});"
            )
            total += self._db.conn.execute(sql, (tabla,)).fetchone()[0]
        return total

    def hogar_codigo(self, tabla: str, fila: dict) -> Optional[str]:
        """codigo_invitacion del hogar de una fila compartida (None si no es de un hogar o no se encuentra)."""
        hogar_id: Any = None
        if tabla == "hogares":
            return fila.get("codigo_invitacion")
        if tabla in ("hogar_miembros", "gastos_compartidos"):
            hogar_id = fila.get("hogar_id")
        elif tabla == "gasto_compartido_pagos":
            gasto = self._db.conn.execute(
                "SELECT hogar_id FROM gastos_compartidos WHERE id = ?;", (fila.get("gasto_compartido_id"),),
            ).fetchone()
            hogar_id = gasto["hogar_id"] if gasto is not None else None
        if hogar_id is None:
            return None
        hogar = self._db.conn.execute("SELECT codigo_invitacion FROM hogares WHERE id = ?;", (hogar_id,)).fetchone()
        return hogar["codigo_invitacion"] if hogar is not None else None

    # ----------------------------------------------------------
    # ESCRITURA (siempre dentro de escritura_sync())
    # ----------------------------------------------------------

    @contextmanager
    def escritura_sync(self) -> Iterator[sqlite3.Connection]:
        """
        Transacción de la sync, con la marca que apaga los triggers (ver
        docstring del módulo). Commit al salir; rollback si algo falla.
        """
        conn = self._db.conn
        conn.commit()  # cierra una transacción implícita que hubiera quedado abierta
        conn.execute("BEGIN;")
        try:
            conn.execute("INSERT OR REPLACE INTO sync_estado (clave, valor) VALUES (?, '1');", (MARCA_ESCRITURA_SYNC,))
            yield conn
            conn.execute("DELETE FROM sync_estado WHERE clave = ?;", (MARCA_ESCRITURA_SYNC,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def marcar_sincronizada(self, conn: sqlite3.Connection, tabla: str, clave: str, hasta: str, momento: str) -> None:
        """
        La fila quedó igual que en Supabase: sincronizado_en = momento, y su
        entrada de sync_cambios se borra si es de `hasta` o antes (el
        modificado_en que se subió, o cuándo se leyeron los pendientes). Si
        volvió a cambiar mientras se subía, su modificado_en es posterior:
        sigue pendiente para la próxima.
        """
        conn.execute(
            f"UPDATE {self._validar_tabla(tabla)} SET sincronizado_en = ? WHERE {self._where_clave(tabla)};",
            (momento, *self.valores_de_clave(tabla, clave)),
        )
        conn.execute(
            "DELETE FROM sync_cambios WHERE tabla = ? AND clave = ? AND modificado_en <= ?;", (tabla, clave, hasta),
        )

    def quitar_cambio(self, conn: sqlite3.Connection, tabla: str, clave: str) -> None:
        conn.execute("DELETE FROM sync_cambios WHERE tabla = ? AND clave = ?;", (tabla, clave))

    def guardar_estado(self, conn: sqlite3.Connection, clave: str, valor: str) -> None:
        """Escribe `clave` en sync_estado (se comitea con la transacción, a diferencia de MARCA_ESCRITURA_SYNC)."""
        conn.execute("INSERT OR REPLACE INTO sync_estado (clave, valor) VALUES (?, ?);", (clave, valor))

    def crear_fila(self, conn: sqlite3.Connection, tabla: str, valores: dict) -> str:
        """
        INSERT de una fila que arma la propia sync (no viene de Supabase):
        id nuevo (repositories/_ids.py), solo las columnas que existen acá y
        sincronizado_en NULL — queda pendiente de subir como fila propia de
        esta base. Devuelve el id. Solo tablas con clave `id`. Puede lanzar
        sqlite3.IntegrityError (ej. un UNIQUE).
        """
        if claves_primarias(self._validar_tabla(tabla)) != ("id",):
            raise ValueError(f"'{tabla}' no tiene clave `id`.")
        columnas_locales = self.columnas(tabla)
        fila = {
            c: v for c, v in valores.items()
            if c in columnas_locales and c not in COLUMNAS_LOCALES and c != "id"
        }
        fila["id"] = nuevo_id()
        columnas = list(fila)
        conn.execute(
            f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES ({', '.join('?' for _ in columnas)});",
            tuple(fila[c] for c in columnas),
        )
        return fila["id"]

    def guardar_fila(self, conn: sqlite3.Connection, tabla: str, datos: dict, momento: str) -> None:
        """
        INSERT o UPDATE por clave de una fila que vino de Supabase, solo con
        las columnas que existen en esta base, y sincronizado_en = momento.
        Puede lanzar sqlite3.IntegrityError (ej. una FK a una fila que acá
        no está): el caller la cuenta como error y sigue.
        """
        columnas_locales = self.columnas(tabla)
        valores = {c: v for c, v in datos.items() if c in columnas_locales and c not in COLUMNAS_LOCALES}
        valores["sincronizado_en"] = momento
        clave = self.clave_de(tabla, valores)
        if self.fila(tabla, clave) is None:
            self._adoptar_id_por_clave_natural(conn, tabla, valores)
        if self.fila(tabla, clave) is None:
            columnas = list(valores)
            conn.execute(
                f"INSERT INTO {tabla} ({', '.join(columnas)}) VALUES ({', '.join('?' for _ in columnas)});",
                tuple(valores[c] for c in columnas),
            )
            return
        pk = claves_primarias(tabla)
        asignaciones = [c for c in valores if c not in pk]
        conn.execute(
            f"UPDATE {tabla} SET {', '.join(f'{c} = ?' for c in asignaciones)} WHERE {self._where_clave(tabla)};",
            (*(valores[c] for c in asignaciones), *self.valores_de_clave(tabla, clave)),
        )

    def _adoptar_id_por_clave_natural(self, conn: sqlite3.Connection, tabla: str, valores: dict) -> None:
        """
        Ver CLAVES_NATURALES: si hay una fila local con la misma clave
        natural que `valores` pero otro id, pasa a tener el id remoto, y
        todas las filas que la referencian (FKs declaradas, de cualquier
        tabla) también. defer_foreign_keys: las FKs se chequean recién al
        COMMIT, así el orden de los UPDATE no importa (SQLite lo apaga solo
        al terminar la transacción).
        """
        viejo = self.id_por_clave_natural(tabla, valores)  # None si la tabla no tiene clave natural
        if viejo is None or viejo == valores["id"]:
            return
        nuevo = valores["id"]
        conn.execute("PRAGMA defer_foreign_keys = ON;")
        for fila in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table';").fetchall():
            hija = fila["name"]
            for fk in conn.execute(f"PRAGMA foreign_key_list({hija});").fetchall():
                if fk["table"] == tabla:
                    conn.execute(f"UPDATE {hija} SET {fk['from']} = ? WHERE {fk['from']} = ?;", (nuevo, viejo))
        conn.execute(f"UPDATE {tabla} SET id = ? WHERE id = ?;", (nuevo, viejo))
        conn.execute("DELETE FROM sync_cambios WHERE tabla = ? AND clave = ?;", (tabla, str(viejo)))

    def borrar_fila(self, conn: sqlite3.Connection, tabla: str, clave: str) -> None:
        """DELETE por clave (un borrado que vino de Supabase). Puede lanzar sqlite3.IntegrityError (FK)."""
        conn.execute(
            f"DELETE FROM {self._validar_tabla(tabla)} WHERE {self._where_clave(tabla)};",
            self.valores_de_clave(tabla, clave),
        )
