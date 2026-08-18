"""
DeltaBalance — repositories/

Una clase por tabla/agregado — SOLO acceso a datos, sin lógica de negocio.
Ver docs/ARCHITECTURE.md para el diseño completo de esta carpeta.
"""

from repositories.cuentas_repository import CuentasRepository
from repositories.categorias_repository import CategoriasRepository
from repositories.transacciones_repository import TransaccionesRepository
from repositories.deudas_repository import DeudasRepository
from repositories.compras_cuotas_repository import ComprasCuotasRepository
from repositories.cuotas_credito_repository import CuotasCreditoRepository
from repositories.resumenes_tarjeta_repository import ResumenesTarjetaRepository
from repositories.resumen_cargos_extra_repository import ResumenCargosExtraRepository
from repositories.presupuestos_repository import PresupuestosRepository
from repositories.ingresos_proyectados_repository import IngresosProyectadosRepository
from repositories.empleos_repository import EmpleosRepository
from repositories.activos_financieros_repository import ActivosFinancierosRepository
from repositories.objetivos_ahorro_repository import ObjetivosAhorroRepository
from repositories.movimientos_activo_repository import MovimientosActivoRepository
from repositories.asignaciones_repository import AsignacionesRepository
from repositories.hogares_repository import HogaresRepository
from repositories.hogar_miembros_repository import HogarMiembrosRepository
from repositories.gastos_compartidos_repository import GastosCompartidosRepository
from repositories.prestamos_repository import PrestamosRepository
from repositories.cuotas_prestamo_repository import CuotasPrestamoRepository

__all__ = [
    "CuentasRepository",
    "CategoriasRepository",
    "TransaccionesRepository",
    "DeudasRepository",
    "ComprasCuotasRepository",
    "CuotasCreditoRepository",
    "ResumenesTarjetaRepository",
    "ResumenCargosExtraRepository",
    "PresupuestosRepository",
    "IngresosProyectadosRepository",
    "EmpleosRepository",
    "ActivosFinancierosRepository",
    "ObjetivosAhorroRepository",
    "MovimientosActivoRepository",
    "AsignacionesRepository",
    "HogaresRepository",
    "HogarMiembrosRepository",
    "GastosCompartidosRepository",
    "PrestamosRepository",
    "CuotasPrestamoRepository",
]
