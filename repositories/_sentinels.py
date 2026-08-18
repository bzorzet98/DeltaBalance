"""
DeltaBalance — repositories/_sentinels.py

Sentinel(s) compartidos entre repositorios. NO_CAMBIAR se centraliza acá (en
vez de definirse una vez por repositorio) porque más de un repositorio
(TransaccionesRepository, DeudasRepository, y los que se agreguen después)
lo necesita en sus métodos actualizar() para distinguir "no tocar este
campo" (default) de "escribir NULL a propósito" (None explícito). Si cada
repositorio definiera su propio `object()`, dos sentinels con el mismo
nombre pero identidad distinta se comparan como no-iguales entre sí — un
error fácil de cometer si alguien importa NO_CAMBIAR del repositorio
equivocado. Con una sola definición compartida, `is NO_CAMBIAR` siempre
compara contra el mismo objeto sin importar de dónde se importe.
"""

NO_CAMBIAR = object()
