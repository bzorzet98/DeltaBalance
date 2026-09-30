"""
DeltaBalance — ui/components/tipo_valor.py

"Tipo de valor" de un monto que se puede cargar de tres formas: pills
[$] [0.XX] [%] + el campo del valor.

- $     monto fijo (CampoMonto, con fórmulas — CLAUDE.md §9).
- 0.XX  coeficiente de otro monto (0 < x ≤ 1): 0.25 de $100.000 = $25.000.
- %     porcentaje de otro monto (0 < x ≤ 100): 25% de $100.000 = $25.000.

La base ("otro monto") la tipea el usuario en el campo "¿DE CUÁNTO?"
(con_base=True: Deudas y "Registrar como deuda" del Registro) o la pone el
caller (con_base=False: Gastos compartidos, donde la base es el monto del
movimiento compartido). El campo "¿DE CUÁNTO?" solo aparece con 0.XX o %.

Lo usan la fila de alta de Deudas (ui/screens/deudas.py), la de Gastos
compartidos (ui/screens/gastos_compartidos.py) y el diálogo "Registrar
como deuda" del Registro (ui/components/registro_transacciones.py): el
cálculo vive acá una sola vez. El componente valida la forma (número
válido, coeficiente/porcentaje en rango, base mayor a 0); las reglas de
dominio las pone el caller (mismo reparto que CampoMonto).

Dos disposiciones:
- "fila": pills + valor + base en un Row, sin bordes propios (estilo_campo()
  de tabla_planilla.py), para una celda de fila de alta.
- "dialogo": etiqueta + pills arriba, valor y base como TextField con label,
  para un AlertDialog.

`page` puede ser la página real o un reemplazo con update(*controles)
(TablaPlanilla.pagina_alta): el componente parchea solo su propio control.
"""

from typing import Callable, Optional

import flet as ft

from ui.components.campo_monto import CampoMonto
from ui.components.tabla_planilla import estilo_campo, sin_auto_update, sin_borde
from ui.theme.tabla_tokens import BORDER_DEFAULT, BTN_COMPARTIR, PESO_HEADER, TEXT_SECONDARY, TEXT_SOBRE_BOTON
from ui.theme.tokens import TypographyTokens
from utils.calculadora_segura import CalculadoraError, evaluar_expresion

# --- Configuración de layout ---
ALTURA_PILL = 22
RADIO_PILL = 11
ESPACIO_PILLS = 2
ESPACIO_CAMPOS = 4
ESPACIO_DIALOGO = 8
TAMANIO_PILL = TypographyTokens.REGISTRO_FONT_HEADER
ANCHO_BORDE_PILL = 1
# Reparto del ancho en la fila entre el valor y "¿DE CUÁNTO?" (el valor de
# 0.XX / % es corto; la base es un monto). Con $ la base no se ve y el valor
# ocupa todo.
FLEX_VALOR = 2
FLEX_BASE = 3

TIPO_MONTO = "monto"
TIPO_COEFICIENTE = "coeficiente"
TIPO_PORCENTAJE = "porcentaje"

ETIQUETAS_TIPO = {TIPO_MONTO: "$", TIPO_COEFICIENTE: "0.XX", TIPO_PORCENTAJE: "%"}
# Ancho fijo de cada pill: el bloque de pills mide siempre ANCHO_PILLS, así
# una celda de fila de alta sabe cuánto le queda para los campos.
ANCHOS_PILL = {TIPO_MONTO: 20, TIPO_COEFICIENTE: 32, TIPO_PORCENTAJE: 20}
ANCHO_PILLS = sum(ANCHOS_PILL.values()) + ESPACIO_PILLS * (len(ANCHOS_PILL) - 1)
TOOLTIPS_TIPO = {
    TIPO_MONTO: "MONTO FIJO",
    TIPO_COEFICIENTE: "COEFICIENTE DE OTRO MONTO (EJ: 0.25)",
    TIPO_PORCENTAJE: "PORCENTAJE DE OTRO MONTO (EJ: 25)",
}
HINTS_TIPO = {TIPO_MONTO: "MONTO", TIPO_COEFICIENTE: "0.25", TIPO_PORCENTAJE: "25"}
LABELS_TIPO = {
    TIPO_MONTO: "MONTO",
    TIPO_COEFICIENTE: "COEFICIENTE (0 A 1)",
    TIPO_PORCENTAJE: "PORCENTAJE (0 A 100)",
}
TEXTO_BASE = "¿DE CUÁNTO?"
# En la fila el campo es angosto: placeholder corto, la pregunta completa en el tooltip.
HINT_BASE_FILA = "¿CUÁNTO?"
TOOLTIP_BASE = "¿DE CUÁNTO? — EL MONTO SOBRE EL QUE SE CALCULA"
MAXIMO_TIPO = {TIPO_COEFICIENTE: 1.0, TIPO_PORCENTAJE: 100.0}


def numero(texto: str) -> Optional[float]:
    """Texto tipeado → número, o None si no es válido. Acepta coma decimal, "%" al final y fórmulas con "="."""
    limpio = (texto or "").strip().replace(",", ".")
    if limpio.endswith("%"):
        limpio = limpio[:-1].strip()
    if not limpio:
        return None
    if limpio.startswith("="):
        try:
            return float(evaluar_expresion(limpio[1:]))
        except CalculadoraError:
            return None
    try:
        return float(limpio)
    except ValueError:
        return None


class CampoTipoValor:
    """
    Uso típico (fila de alta):
        campo = CampoTipoValor(tabla.pagina_alta, orden=(TIPO_MONTO, TIPO_COEFICIENTE, TIPO_PORCENTAJE),
                               decimales=2, con_base=True, on_enter=_confirmar_alta)
        celdas["monto"] = campo.control
        ...
        monto_minor = campo.monto_minor()          # lanza ValueError si falta algo
    """

    def __init__(
        self,
        page,
        *,
        orden: tuple[str, ...],
        decimales: int,
        con_base: bool,
        disposicion: str = "fila",
        tipo_inicial: Optional[str] = None,
        valor_inicial: str = "",
        base_inicial: str = "",
        on_cambio: Optional[Callable[[], None]] = None,
        on_enter: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            orden:         Tipos disponibles, en el orden de las pills.
            decimales:     De la moneda del monto (CampoMonto).
            con_base:      True = muestra "¿DE CUÁNTO?" con 0.XX / %.
            disposicion:   "fila" o "dialogo" (ver docstring del módulo).
            tipo_inicial:  Default: el primero de `orden`.
            valor_inicial / base_inicial: texto precargado (borrador).
            on_cambio:     Cada vez que cambia el tipo o se tipea (borrador,
                           vista previa del caller).
            on_enter:      Enter en el último campo visible (la base con
                           0.XX / %, el valor con $). Enter en el valor con
                           la base visible pasa a la base: nunca se salta
                           "¿DE CUÁNTO?".
        """
        self._page = page
        self._orden = orden
        self._decimales = decimales
        self._con_base = con_base
        self._dialogo = disposicion == "dialogo"
        self._tipo = tipo_inicial if tipo_inicial in orden else orden[0]
        self._on_cambio = on_cambio
        self._on_enter = on_enter
        self._motivo_fijo: Optional[str] = None  # fijar(): tipo y valor bloqueados

        self._pills = ft.Row(spacing=ESPACIO_PILLS, tight=True)
        # expand solo en la fila (Row): en el diálogo irían dentro de una
        # Column con scroll, donde un hijo flexible no tiene alto acotado.
        self._contenedor_valor = ft.Container(expand=None if self._dialogo else FLEX_VALOR)
        self._contenedor_base = ft.Container(expand=None if self._dialogo else FLEX_BASE)
        self._campo_valor: Optional[ft.TextField] = None
        self._monto_valor: Optional[CampoMonto] = None
        self._base = self._nuevo_monto(base_inicial, HINT_BASE_FILA, TEXTO_BASE, TOOLTIP_BASE)
        self._contenedor_base.content = self._base.control
        self._armar_valor(valor_inicial)
        self._dibujar_pills()
        self._actualizar_base()

        if self._dialogo:
            self.control: ft.Control = ft.Column(
                [
                    ft.Row(
                        [ft.Text("TIPO DE VALOR", size=TypographyTokens.LABEL_SIZE, color=TEXT_SECONDARY), self._pills],
                        spacing=ESPACIO_DIALOGO,
                    ),
                    self._contenedor_valor,
                    self._contenedor_base,
                ],
                spacing=ESPACIO_DIALOGO,
                tight=True,
            )
        else:
            self.control = ft.Row(
                [self._pills, self._contenedor_valor, self._contenedor_base],
                spacing=ESPACIO_CAMPOS,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )

    # ------------------------------------------------------------
    # API PÚBLICA
    # ------------------------------------------------------------

    @property
    def tipo(self) -> str:
        return self._tipo

    @property
    def texto_valor(self) -> str:
        campo = self._monto_valor.control if self._monto_valor else self._campo_valor
        return campo.value or ""

    @property
    def texto_base(self) -> str:
        return self._base.texto

    @property
    def campo_foco(self) -> ft.Control:
        """El campo del valor (para enfocarlo)."""
        return self._monto_valor.control if self._monto_valor else self._campo_valor

    def fijar(self, tipo: Optional[str], texto_valor: str = "", motivo: Optional[str] = None) -> None:
        """
        motivo != None: fija tipo y valor y bloquea pills y campo (el motivo
        va de tooltip). motivo None: desbloquea (tipo None = deja el actual).
        No parchea: lo hace el caller junto con el resto de su fila.
        """
        if motivo is None and self._motivo_fijo is None and tipo in (None, self._tipo):
            return  # nada bloqueado ni que cambiar: el campo queda como está
        self._motivo_fijo = motivo
        if tipo in self._orden:
            self._tipo = tipo
        self._armar_valor(texto_valor if motivo is not None else self.texto_valor)
        self._dibujar_pills()
        self._actualizar_base()

    def cambiar_decimales(self, decimales: int) -> None:
        """La moneda cambió: los CampoMonto se rehacen con los decimales nuevos (se conserva lo tipeado)."""
        if decimales == self._decimales:
            return
        self._decimales = decimales
        texto_base = self._base.texto
        self._base = self._nuevo_monto(texto_base, HINT_BASE_FILA, TEXTO_BASE, TOOLTIP_BASE)
        self._contenedor_base.content = self._base.control
        self._armar_valor(self.texto_valor)

    def monto_minor(self) -> int:
        """
        Monto final en minor units (> 0). Base: la de "¿DE CUÁNTO?".
        Lanza ValueError (MAYÚSCULAS) si falta algo o está fuera de rango.
        """
        if self._tipo == TIPO_MONTO:
            return self._monto_fijo_minor()
        valor = self._valor_relativo()
        base = self._monto_de(self._base, "COMPLETÁ ¿DE CUÁNTO? CON UN MONTO MAYOR A 0.")
        return round(base * self._fraccion(valor))

    def porcentaje(self, base_minor: int) -> float:
        """
        Porcentaje (0 < x ≤ 100) de `base_minor` que representa el valor —
        para coeficiente_deuda de un gasto compartido. Con $, el monto fijo
        no puede superar |base|.
        """
        if not base_minor:
            raise ValueError("NO HAY MONTO BASE SOBRE EL CUAL CALCULAR.")
        if self._tipo == TIPO_MONTO:
            monto = self._monto_fijo_minor()
            if monto > abs(base_minor):
                raise ValueError("EL MONTO FIJO NO PUEDE SUPERAR AL MONTO BASE.")
            return monto * 100 / abs(base_minor)
        return self._fraccion(self._valor_relativo()) * 100

    def vista_previa_minor(self, base_minor: Optional[int] = None) -> Optional[int]:
        """Monto calculado con lo tipeado hasta ahora, sin validar ni confirmar nada (None si todavía no se puede)."""
        valor = numero(self.texto_valor)
        if valor is None or valor <= 0:
            return None
        if self._tipo == TIPO_MONTO:
            return round(valor * 10 ** self._decimales)
        if valor > MAXIMO_TIPO[self._tipo]:
            return None
        base = base_minor
        if base is None:
            numero_base = numero(self._base.texto)
            base = round(numero_base * 10 ** self._decimales) if numero_base and numero_base > 0 else None
        return round(base * self._fraccion(valor)) if base else None

    # ------------------------------------------------------------
    # INTERNOS
    # ------------------------------------------------------------

    def _fraccion(self, valor: float) -> float:
        return valor if self._tipo == TIPO_COEFICIENTE else valor / 100

    def _monto_fijo_minor(self) -> int:
        return self._monto_de(self._monto_valor, "EL MONTO DEBE SER UN NÚMERO MAYOR A 0.")

    def _monto_de(self, campo: CampoMonto, error: str) -> int:
        # confirmar() resuelve una fórmula pendiente (y marca el borde rojo si no es válida).
        if not campo.confirmar():
            raise ValueError(error)
        valor = numero(campo.texto)
        if valor is None or valor <= 0:
            raise ValueError(error)
        return round(valor * 10 ** self._decimales)

    def _valor_relativo(self) -> float:
        maximo = MAXIMO_TIPO[self._tipo]
        valor = numero(self.texto_valor)
        if valor is None or not 0 < valor <= maximo:
            ejemplo = "0.25" if self._tipo == TIPO_COEFICIENTE else "25"
            raise ValueError(f"EL VALOR DEBE SER MAYOR A 0 Y COMO MÁXIMO {maximo:g} (EJ: {ejemplo}).")
        return valor

    def _nuevo_monto(
        self, texto: str, hint: str, label: str, tooltip: Optional[str] = None,
        on_avanzar: Optional[Callable[[], None]] = None,
    ) -> CampoMonto:
        """CampoMonto con `hint` en la fila o `label` en el diálogo."""
        campo = CampoMonto(
            self._page,
            on_confirmar=lambda monto_minor: self._avisar_cambio(),
            decimales=self._decimales,
            hint_text=None if self._dialogo else hint,
            label=label if self._dialogo else None,
            text_size=TypographyTokens.REGISTRO_FONT_CELDA,
            on_avanzar=on_avanzar or self._enter,
        )
        if texto:
            campo.control.value = texto
        campo.control.on_change = self._on_tipeo
        campo.control.tooltip = tooltip
        if not self._dialogo:
            sin_borde(campo.control)
            campo.control.text_align = ft.TextAlign.RIGHT
            campo.control.expand = True
        return campo

    def _armar_valor(self, texto: str) -> None:
        """Campo del valor según el tipo: CampoMonto con $, TextField con 0.XX / %."""
        bloqueado = self._motivo_fijo is not None
        if self._tipo == TIPO_MONTO:
            self._monto_valor = self._nuevo_monto(
                texto, HINTS_TIPO[TIPO_MONTO], LABELS_TIPO[TIPO_MONTO], on_avanzar=self._enter_valor,
            )
            self._campo_valor = None
            campo = self._monto_valor.control
        else:
            self._monto_valor = None
            estilo = {} if self._dialogo else estilo_campo()
            campo = ft.TextField(
                value=texto,
                hint_text=None if self._dialogo else HINTS_TIPO[self._tipo],
                label=LABELS_TIPO[self._tipo] if self._dialogo else None,
                text_align=ft.TextAlign.CENTER,
                expand=not self._dialogo,
                on_change=self._on_tipeo,
                on_submit=lambda e: self._enter_valor(),
                **estilo,
            )
            self._campo_valor = campo
        if self._dialogo:
            campo.label = LABELS_TIPO[self._tipo]
        campo.disabled = bloqueado
        campo.tooltip = self._motivo_fijo
        self._contenedor_valor.content = campo

    def _actualizar_base(self) -> None:
        self._contenedor_base.visible = self._con_base and self._tipo != TIPO_MONTO

    def _dibujar_pills(self) -> None:
        bloqueado = self._motivo_fijo is not None
        pills = []
        for tipo in self._orden:
            activo = tipo == self._tipo
            pills.append(
                ft.Container(
                    width=ANCHOS_PILL[tipo],
                    height=ALTURA_PILL,
                    border_radius=RADIO_PILL,
                    bgcolor=BTN_COMPARTIR if activo else None,
                    border=None if activo else ft.Border.all(ANCHO_BORDE_PILL, BORDER_DEFAULT),
                    alignment=ft.Alignment.CENTER,
                    tooltip=self._motivo_fijo or TOOLTIPS_TIPO[tipo],
                    on_click=None if bloqueado else (lambda e, t=tipo: self._elegir(t)),
                    content=ft.Text(
                        ETIQUETAS_TIPO[tipo], size=TAMANIO_PILL, weight=PESO_HEADER,
                        color=TEXT_SOBRE_BOTON if activo else TEXT_SECONDARY,
                    ),
                )
            )
        self._pills.controls = pills

    def _elegir(self, tipo: str) -> None:
        if tipo == self._tipo:
            sin_auto_update()
            return
        self._tipo = tipo
        self._armar_valor(self.texto_valor)
        self._dibujar_pills()
        self._actualizar_base()
        self._avisar_cambio()
        self._page.update(self.control)
        self._page.run_task(self.campo_foco.focus)

    def _avisar_cambio(self) -> None:
        if self._on_cambio is not None:
            self._on_cambio()

    def _on_tipeo(self, e: ft.ControlEvent) -> None:
        # El texto ya está en pantalla: el caller parchea lo suyo (vista previa).
        self._avisar_cambio()
        sin_auto_update()

    def _enter_valor(self) -> None:
        """Enter en el valor: a "¿DE CUÁNTO?" si está visible; si no, es el último campo."""
        if self._contenedor_base.visible:
            self._page.run_task(self._base.control.focus)
            return
        self._enter()

    def _enter(self) -> None:
        if self._on_enter is not None:
            self._on_enter()
