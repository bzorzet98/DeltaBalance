"""
verify/sync/_fake_supabase.py

Supabase FALSO en memoria para los scripts de verify/sync/. Implementa solo
lo que usa sync/sync_engine.py — table().select/eq/in_/gt/order/range/
upsert().execute() — sobre las dos tablas remotas que usa hoy el motor:
deltabalance_filas (privadas) y deltabalance_compartidos (compartidas),
con `uuid` como clave del upsert y `updated_at` como un reloj del
"servidor" que siempre avanza (igual que el trigger de Supabase, se
actualiza en cada upsert).

No implementa RLS: toda consulta ve todas las filas. Para las privadas da
igual (el motor ya filtra por usuario_id); para las compartidas equivale a
que todos los usuarios del script son miembros del mismo hogar. Tampoco
hay red: un error de conexión o un APIError no se simulan acá.

Uso:
    from verify.sync._fake_supabase import FakeSupabase, MarcasEnMemoria
    fake = FakeSupabase()
    motor = SyncEngine(manager, None, cliente=fake, usuario_id=UUID, marcas=MarcasEnMemoria())
"""

import copy
import json

TABLAS_REMOTAS = ("deltabalance_filas", "deltabalance_compartidos")


class _Respuesta:
    def __init__(self, data: list):
        self.data = data


class FakeSupabase:
    def __init__(self) -> None:
        self.tablas: dict[str, dict[str, dict]] = {nombre: {} for nombre in TABLAS_REMOTAS}
        self._reloj = 0

    def ahora(self) -> str:
        self._reloj += 1
        return f"2026-10-01T00:00:00.{self._reloj:06d}+00:00"

    def table(self, nombre: str) -> "_Consulta":
        return _Consulta(self, nombre)

    # --- Ayudas para los scripts (el motor no las usa) ---

    def fila(self, uuid: str) -> dict:
        """La fila remota con ese uuid (en cualquiera de las dos tablas, sin copiar), o {} si no está."""
        for almacen in self.tablas.values():
            if uuid in almacen:
                return almacen[uuid]
        return {}

    def datos(self, uuid: str) -> dict:
        """`datos` de esa fila como dict ({} si no está)."""
        datos = self.fila(uuid).get("datos") or {}
        return json.loads(datos) if isinstance(datos, str) else datos

    def reescribir_datos(self, uuid: str, datos: dict) -> None:
        """Cambia `datos` SIN pasar por un upsert (updated_at no cambia): simula lo que dejó subido una versión anterior."""
        self.fila(uuid)["datos"] = json.dumps(datos)


class _Consulta:
    def __init__(self, fake: FakeSupabase, tabla: str):
        self._fake = fake
        self._almacen = fake.tablas[tabla]
        self._filtros: list = []
        self._orden: list[str] = []
        self._rango = None
        self._upsert = None

    def select(self, *columnas):
        return self

    def eq(self, columna, valor):
        self._filtros.append(lambda f: f.get(columna) == valor)
        return self

    def in_(self, columna, valores):
        conjunto = set(valores)
        self._filtros.append(lambda f: f.get(columna) in conjunto)
        return self

    def gt(self, columna, valor):
        self._filtros.append(lambda f: (f.get(columna) or "") > valor)
        return self

    def order(self, columna, desc=False):
        self._orden.append(columna)
        return self

    def range(self, inicio, fin):
        self._rango = (inicio, fin)
        return self

    def upsert(self, filas, on_conflict=""):
        self._upsert = (filas, on_conflict)
        return self

    def execute(self) -> _Respuesta:
        if self._upsert is not None:
            filas, clave = self._upsert
            devueltas = []
            for fila in filas:
                previa = self._almacen.get(fila[clave], {})
                momento = self._fake.ahora()
                # deepcopy: como un ida y vuelta por JSON.
                nueva = {"created_at": momento, **previa, **copy.deepcopy(fila), "updated_at": momento}
                self._almacen[fila[clave]] = nueva
                devueltas.append(copy.deepcopy(nueva))
            return _Respuesta(devueltas)
        filas = [copy.deepcopy(f) for f in self._almacen.values() if all(filtro(f) for filtro in self._filtros)]
        for columna in reversed(self._orden):
            filas.sort(key=lambda f: str(f.get(columna) or ""))
        if self._rango is not None:
            filas = filas[self._rango[0]:self._rango[1] + 1]
        return _Respuesta(filas)


class MarcasEnMemoria:
    """Reemplazo de MarcasEnPrefs: el verify no toca .deltabalance_prefs.json."""

    def __init__(self) -> None:
        self.marcas: dict[str, str] = {}

    def leer(self, clave):
        return self.marcas.get(clave)

    def escribir(self, clave, valor):
        self.marcas[clave] = valor
