"""
DeltaBalance — sync/referencias.py

Referencias de una fila COMPARTIDA a filas PRIVADAS de quien la escribió
(docs/DATA_MODEL_DECISIONS.md sección 27). Módulo de aplicación, como el
resto de sync/: lo usa SyncEngine al subir y al bajar; el acceso a la base
pasa por SyncRepository.

--- El problema ---

Una fila de TABLAS_COMPARTIDAS viaja a la base de cada miembro del hogar,
pero algunas de sus FK apuntan a tablas que NO son compartidas: cada
miembro tiene las suyas, con sus propios UUID. Hoy son dos:
- gastos_compartidos.categoria_id → categorias (NOT NULL).
- gasto_compartido_pagos.transaccion_id → transacciones (acepta NULL).
El UUID de la categoría de NOELIA no existe en la base de BRUNO: guardar la
fila tal cual falla con "FOREIGN KEY constraint failed".

Qué FK son "privadas" no está escrito a mano: sale del schema (PRAGMA
foreign_key_list, SyncRepository.claves_foraneas()) — toda FK de una tabla
compartida a una tabla sincronizada que no es compartida. Una FK nueva de
ese tipo queda cubierta sin tocar este módulo. No se tocan las FK a otra
compartida (hogar_id, gasto_compartido_id: el padre viaja a todos los
miembros, en orden de dependencias) ni las FK a tablas que no se
sincronizan (monedas: mismos ids en toda base, por el seed).

--- Al subir: la referencia en forma portable (para_subir()) ---

Dentro de `datos`, junto a la fila, viaja MARCA_REFERENCIAS: por cada FK
privada con valor, la fila apuntada descripta de una forma que cualquier
base entiende — su clave natural (CLAVES_NATURALES de
repositories/sync_repository.py) más las columnas sin las que no se puede
insertar (SyncRepository.columnas_requeridas()). Ej.:
    "_referencias": {"categoria_id": {"categoria_principal": "EGRESOS VARIABLES",
                                      "subcategoria": "Supermercado", "tipo": "egreso"}}
El UUID original sigue viajando en su columna, como siempre. Una tabla sin
clave natural (transacciones) no tiene forma portable: no viaja nada extra.
Como las otras marcas (_borrado, _modificado_en), no es una columna:
guardar_fila() la ignora y Supabase no cambia.

--- Al bajar: traducir a ids de esta base (a_local()) ---

Por cada FK privada cuyo valor NO existe acá (es de la base de otro):
1. Hay una fila local con la misma clave natural → su id local.
2. Si no, y la referencia trae todo lo necesario → se CREA la fila acá, con
   un UUID nuevo — nunca el del otro: chocaría con su fila privada en
   Supabase — e INACTIVA si la tabla tiene soft-delete (`activa`): existe
   para que la FK se cumpla y el nombre se vea, no aparece para cargar
   (pantalla Categorías: se ve como inactiva y se puede reactivar). Queda
   pendiente de subir como fila propia, como cualquier alta.
3. Si no se puede crear y la columna acepta NULL → NULL (ej. la transacción
   con la que pagó el otro miembro: acá no existe).
4. Si no → la fila no se guarda y se cuenta como error, con un mensaje que
   dice qué columna no se pudo traducir.
Una referencia que sí existe acá (la fila es de esta base, o ya se tradujo)
no se toca. Cada base traduce por su cuenta, así que da igual cuántos
miembros tenga el hogar.

--- Lo que acá no se ve, no se pisa (para_subir()) ---

Si esta base guardó NULL en una FK privada por el paso 3, y la fila se
vuelve a subir desde acá (ej. otra edición), se conserva el valor que tiene
Supabase (y su referencia): si no, se borraría el vínculo en la base de su
autor. Se conserva solo si apunta a algo que acá no existe — en la base del
autor sí existe, y ahí un NULL nuevo es un cambio de verdad y viaja.
"""

import sqlite3
from typing import Any, Iterable, Optional

from db.schema_migrations import TABLAS_SINCRONIZADAS
from repositories.sync_repository import CLAVES_NATURALES, SyncRepository

MARCA_REFERENCIAS = "_referencias"
# Soft-delete (docs/DATA_MODEL_DECISIONS.md sección 11): una fila creada solo
# para respaldar la referencia de otro miembro nace inactiva (paso 2).
COLUMNA_ACTIVA = "activa"


def _referencias_de(datos: dict) -> dict:
    """MARCA_REFERENCIAS de `datos` como dict ({} si no está o vino rota)."""
    referencias = datos.get(MARCA_REFERENCIAS)
    return referencias if isinstance(referencias, dict) else {}


class ReferenciasCompartidas:
    """
    Usage (SyncEngine):
        referencias = ReferenciasCompartidas(TABLAS_COMPARTIDAS)
        referencias.para_subir(repo, tabla, datos, datos_remotos)              # antes de json.dumps
        datos, sin_traducir = referencias.a_local(repo, tabla, datos, conn)    # antes de guardar_fila()
    """

    def __init__(self, tablas_compartidas: Iterable[str]):
        self._compartidas = frozenset(tablas_compartidas)

    def privadas(self, repo: SyncRepository, tabla: str) -> list[dict]:
        """Las FK de `tabla` (si es compartida) a tablas sincronizadas que no son compartidas (ver docstring del módulo)."""
        if tabla not in self._compartidas:
            return []
        return [
            fk for fk in repo.claves_foraneas(tabla)
            if fk["tabla"] in TABLAS_SINCRONIZADAS and fk["tabla"] not in self._compartidas
        ]

    def portables(self, repo: SyncRepository, tabla: str, datos: dict) -> dict[str, dict]:
        """{columna: referencia portable} de las FK privadas de `datos` que apuntan a una fila de esta base con clave natural."""
        referencias: dict[str, dict] = {}
        for fk in self.privadas(repo, tabla):
            valor = datos.get(fk["columna"])
            natural = CLAVES_NATURALES.get(fk["tabla"])
            if valor is None or not natural:
                continue
            fila = repo.buscar(fk["tabla"], fk["columna_ref"], valor)
            if fila is None:
                continue
            columnas = dict.fromkeys([*natural, *repo.columnas_requeridas(fk["tabla"])])
            referencias[fk["columna"]] = {columna: fila[columna] for columna in columnas}
        return referencias

    # ----------------------------------------------------------
    # SUBIR
    # ----------------------------------------------------------

    def para_subir(
        self, repo: SyncRepository, tabla: str, datos: dict, datos_remotos: Optional[dict] = None,
    ) -> None:
        """
        Prepara `datos` (in place) para subir una fila: conserva las FK
        privadas que acá no se ven y agrega MARCA_REFERENCIAS (ver docstring
        del módulo). `datos_remotos`: `datos` de la versión que hay en
        Supabase (None si no hay o no se consultó).
        """
        remotos = datos_remotos or {}
        previas = _referencias_de(remotos)
        conservadas: list[str] = []
        for fk in self.privadas(repo, tabla):
            columna = fk["columna"]
            ajeno = remotos.get(columna)
            if (
                columna in datos and datos[columna] is None and ajeno is not None
                and repo.buscar(fk["tabla"], fk["columna_ref"], ajeno) is None
            ):
                datos[columna] = ajeno
                conservadas.append(columna)
        referencias = self.portables(repo, tabla, datos)
        referencias.update({columna: previas[columna] for columna in conservadas if columna in previas})
        if referencias:
            datos[MARCA_REFERENCIAS] = referencias

    def completar(self, repo: SyncRepository, tabla: str, datos: dict) -> bool:
        """
        Agrega a `datos` (in place) las referencias portables que le falten
        — filas subidas antes de que existiera MARCA_REFERENCIAS. Solo las
        que esta base puede describir (las que apuntan a filas suyas). True
        si agregó alguna.
        """
        previas = _referencias_de(datos)
        faltantes = {
            columna: referencia for columna, referencia in self.portables(repo, tabla, datos).items()
            if columna not in previas
        }
        if not faltantes:
            return False
        datos[MARCA_REFERENCIAS] = {**previas, **faltantes}
        return True

    # ----------------------------------------------------------
    # BAJAR
    # ----------------------------------------------------------

    def a_local(
        self, repo: SyncRepository, tabla: str, datos: dict, conn: Optional[sqlite3.Connection] = None,
    ) -> tuple[dict, list[str]]:
        """
        `datos` de una fila bajada con sus FK privadas traducidas a ids de
        esta base (pasos 1 a 4 del docstring del módulo), y las columnas que
        no se pudieron traducir (paso 4). Sin `conn` solo lee: no crea filas
        (el paso 2 queda sin traducir) — sirve para comparar con la fila
        local. Con `conn` (la transacción de escritura_sync()) puede crear
        la fila referenciada. Sin FK privadas devuelve `datos` tal cual.
        """
        fks = self.privadas(repo, tabla)
        if not fks:
            return datos, []
        traducidos = dict(datos)
        referencias = _referencias_de(datos)
        sin_traducir: list[str] = []
        for fk in fks:
            columna = fk["columna"]
            valor = traducidos.get(columna)
            if valor is None or repo.buscar(fk["tabla"], fk["columna_ref"], valor) is not None:
                continue
            local = self._resolver(repo, fk, referencias.get(columna), conn)
            if local is not None:
                traducidos[columna] = local
            elif fk["nullable"]:
                traducidos[columna] = None
            else:
                sin_traducir.append(columna)
        return traducidos, sin_traducir

    @staticmethod
    def _resolver(
        repo: SyncRepository, fk: dict, referencia: Any, conn: Optional[sqlite3.Connection],
    ) -> Optional[str]:
        """Pasos 1 y 2: el id local equivalente a `referencia`, creando la fila si hace falta y se puede (solo con `conn`)."""
        tabla = fk["tabla"]
        natural = CLAVES_NATURALES.get(tabla)
        if not natural or not isinstance(referencia, dict) or fk["columna_ref"] != "id":
            return None
        existente = repo.id_por_clave_natural(tabla, referencia)
        if existente is not None or conn is None:
            return existente
        columnas = list(dict.fromkeys([*natural, *repo.columnas_requeridas(tabla)]))
        if any(referencia.get(columna) is None for columna in columnas):
            return None
        valores = {columna: referencia[columna] for columna in columnas}
        if COLUMNA_ACTIVA in repo.columnas(tabla):
            valores[COLUMNA_ACTIVA] = 0
        return repo.crear_fila(conn, tabla, valores)
