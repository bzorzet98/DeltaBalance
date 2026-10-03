"""
DeltaBalance — services/ingresos_proyectados_service.py

Reexport de compatibilidad. El service de ingresos ahora es
services/ingresos_service.py (IngresosService, tarea "Reestructuración de
pantallas Ingresos y Presupuestos"): ui/app.py todavía lo importa con este
nombre y esa tarea no lo toca. Cuando app.py importe IngresosService
directo, este archivo se puede borrar.
"""

from services.ingresos_service import (  # noqa: F401 — reexport
    IngresoError,
    IngresoNotFoundError,
    IngresoResult,
    IngresosService,
)

IngresosProyectadosService = IngresosService
