"""
DeltaBalance — ui/components/reparto_compartido.py

Cuánto le corresponde al otro miembro, en los diálogos de compartir
(compartir_gasto.py, compartir_compra.py, compartir_varios.py): el campo
CampoTipoValor (ui/components/tipo_valor.py, disposición "dialogo", sin
"¿DE CUÁNTO?") con las mismas pills que la fila de alta de Gastos
compartidos y que Deudas — pedido explícito: al compartir se elige si va un
porcentaje, un coeficiente o un monto fijo:

- %     porcentaje del monto base (50 = la mitad).
- 0.XX  coeficiente del monto base (0.5 = la mitad).
- $     monto fijo; se pasa a % del monto base (CampoTipoValor.porcentaje()),
        así el service sigue recibiendo coeficiente_deuda en %. No puede
        superar al monto base. Va sin redondear, para que el adeudado dé el
        monto exacto (mismo criterio que Gastos compartidos).

El monto base lo pone el caller por fila (porcentaje(base_minor)): con
varias filas, % y 0.XX se aplican igual a cada una y $ es el mismo monto
fijo PARA CADA fila.

Sugerencia: el porcentaje default del otro miembro del hogar
(SharedExpensesService.get_suggested_coefficient(), en %), precargado en el
tipo elegido (50 con %, 0.5 con 0.XX; con $ queda vacío), con una línea de
ayuda abajo. Cambiar de hogar (cambiar_hogar()) rehace el campo con la
sugerencia de ese hogar, en el tipo que esté elegido.

Un ingreso pide el reparto igual que un gasto (pedido explícito: no se
presupone que un ingreso se comparte al 100%); el signo lo pone el caller
en el monto base.

Con `base_minor` (diálogos de una sola fila) muestra además la vista previa
del adeudado, como la fila de alta de Gastos compartidos.

Reglas de arquitectura: solo SharedExpensesService, de lectura (miembros y
sugerencia) — nunca repositories/ ni db/ directo (CLAUDE.md §2/§3).
"""

from typing import Callable, Optional

import flet as ft

from services.shared_expenses_service import SharedExpensesService
from ui.components.tipo_valor import TIPO_COEFICIENTE, TIPO_MONTO, TIPO_PORCENTAJE, CampoTipoValor
from ui.theme.tabla_tokens import TEXT_SECONDARY
from ui.theme.tokens import TypographyTokens
from utils.money import amount_display

# --- Configuración de layout ---
ESPACIADO = 6
TAMANIO_AYUDA = TypographyTokens.METADATA_SIZE

# Mismo orden que la fila de alta de Gastos compartidos.
ORDEN_TIPOS = (TIPO_PORCENTAJE, TIPO_COEFICIENTE, TIPO_MONTO)
# 0.3 * 100 = 30.000000000000004: se redondea para no guardar ruido de float.
DECIMALES_COEFICIENTE = 4
ETIQUETA_PREVIA_DEFAULT = "ADEUDADO"


class RepartoCompartido:
    """
    Uso típico (diálogo de una fila):
        reparto = RepartoCompartido(page, service, usuario_local, hogar_id, decimales=2,
                                    base_minor=monto_base, simbolo="$")
        controles.append(reparto.control)
        ...
        coeficiente = reparto.porcentaje(monto_base)   # lanza ValueError (MAYÚSCULAS)
    """

    def __init__(
        self,
        page: ft.Page,
        shared_expenses_service: SharedExpensesService,
        usuario_local: str,
        hogar_id: str,
        *,
        decimales: int,
        tipo_inicial: str = TIPO_PORCENTAJE,
        orden: tuple[str, ...] = ORDEN_TIPOS,
        base_minor: Optional[int] = None,
        simbolo: str = "",
        etiqueta_previa: str = ETIQUETA_PREVIA_DEFAULT,
        autofocus: bool = False,
        on_enter: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            decimales:       De la moneda (el CampoMonto de $).
            tipo_inicial:    Pill elegida al abrir.
            orden:           Pills disponibles (sin $ si las filas son de
                             monedas distintas).
            base_minor:      Monto base CON SIGNO de la única fila: activa la
                             vista previa del adeudado. None = sin vista previa.
            simbolo / etiqueta_previa: formato de la vista previa.
            autofocus:       Foco en el valor al abrir el diálogo.
            on_enter:        Enter en el valor (típicamente: foco al botón).
        """
        self._page = page
        self._service = shared_expenses_service
        self._usuario_local = usuario_local
        self._decimales = decimales
        self._orden = orden
        self._base_minor = base_minor
        self._simbolo = simbolo
        self._etiqueta_previa = etiqueta_previa
        self._on_enter = on_enter

        self._contenedor = ft.Container()
        self._ayuda = ft.Text("", size=TAMANIO_AYUDA, color=TEXT_SECONDARY)
        self._vista_previa = ft.Text("", size=TAMANIO_AYUDA, color=TEXT_SECONDARY, visible=base_minor is not None)
        self.control = ft.Column([self._contenedor, self._ayuda, self._vista_previa], spacing=ESPACIADO, tight=True)

        self._campo: CampoTipoValor = self._armar(hogar_id, tipo_inicial if tipo_inicial in orden else orden[0])
        self._campo.campo_foco.autofocus = autofocus

    # ------------------------------------------------------------
    # API PÚBLICA
    # ------------------------------------------------------------

    @property
    def tipo(self) -> str:
        return self._campo.tipo

    @property
    def campo_foco(self) -> ft.Control:
        return self._campo.campo_foco

    def cambiar_hogar(self, hogar_id: str) -> None:
        """Otro hogar elegido: el campo vuelve a la sugerencia de ese hogar (diálogo ya abierto)."""
        self._campo = self._armar(hogar_id, self._campo.tipo)
        self.control.update()

    def porcentaje(self, base_minor: int) -> float:
        """
        coeficiente_deuda (en %, 0 < x ≤ 100) para una fila de monto base
        `base_minor` (con signo; se usa su valor absoluto). Lanza ValueError
        (MAYÚSCULAS) si el valor no es válido o, con $, supera a la base.
        """
        porcentaje = self._campo.porcentaje(base_minor)
        if self._campo.tipo == TIPO_MONTO:
            return porcentaje  # sin redondear: el adeudado da el monto fijo exacto
        return round(porcentaje, DECIMALES_COEFICIENTE)

    # ------------------------------------------------------------
    # INTERNOS
    # ------------------------------------------------------------

    def _armar(self, hogar_id: str, tipo: str) -> CampoTipoValor:
        sugerido, ayuda = self._sugerencia(hogar_id)
        campo = CampoTipoValor(
            self._page, orden=self._orden, decimales=self._decimales, con_base=False, disposicion="dialogo",
            tipo_inicial=tipo, valor_inicial=self._texto_sugerido(sugerido, tipo),
            on_cambio=self._al_cambiar, on_enter=self._on_enter,
        )
        self._contenedor.content = campo.control
        self._ayuda.value = ayuda
        self._campo = campo
        self._actualizar_vista_previa()
        return campo

    def _sugerencia(self, hogar_id: str) -> tuple[Optional[float], str]:
        """(porcentaje sugerido o None, texto de ayuda)."""
        otros = [
            m for m in self._service.list_miembros(hogar_id) if m["usuario_local"] != self._usuario_local
        ]
        if not otros:
            return None, "SIN OTRO MIEMBRO EN ESTE HOGAR TODAVÍA."
        otro = otros[0]["usuario_local"]
        sugerido = self._service.get_suggested_coefficient(hogar_id, otro)
        if sugerido is None:
            return None, f"'{otro.upper()}' NO TIENE UN PORCENTAJE DEFAULT CONFIGURADO."
        return sugerido, f"SUGERIDO: {sugerido:g}% (DEFAULT DE '{otro.upper()}')."

    @staticmethod
    def _texto_sugerido(sugerido: Optional[float], tipo: str) -> str:
        if sugerido is None or tipo == TIPO_MONTO:
            return ""
        return f"{sugerido / 100:g}" if tipo == TIPO_COEFICIENTE else f"{sugerido:g}"

    def _actualizar_vista_previa(self) -> None:
        if self._base_minor is None:
            return
        previa = self._campo.vista_previa_minor(abs(self._base_minor))
        if previa is None:
            self._vista_previa.value = f"{self._etiqueta_previa}: —"
            return
        # El adeudado hereda el signo del monto base (negativo en un ingreso).
        signo = "-" if self._base_minor < 0 else ""
        self._vista_previa.value = (
            f"{self._etiqueta_previa}: {signo}{amount_display(previa, self._decimales, self._simbolo)}"
        )

    def _al_cambiar(self) -> None:
        # Se tipeó o se cambió de pill (diálogo abierto): CampoTipoValor
        # parchea su propio control; la vista previa es de acá.
        if self._base_minor is None:
            return
        self._actualizar_vista_previa()
        self._vista_previa.update()
