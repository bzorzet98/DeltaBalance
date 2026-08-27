"""
DeltaBalance — ui/components/campo_monto.py

Componente propio "campo de Monto con calculadora de fórmulas integrada" —
generaliza a TODA la app el mecanismo que antes vivía solo en
ui/screens/presupuestos.py (campo Estimado, ver el docstring de ese módulo
para el diseño original completo: Partes B y C). CLAUDE.md §8 (agregado en
esta misma tarea) exige que cualquier campo de monto, nuevo o existente, use
este componente en vez de un ft.TextField crudo con validación numérica a
mano — evita que la calculadora quede disponible solo donde alguien se
acordó de pedirla explícitamente.

Mismo patrón de componente ya establecido en
ui/components/campo_filtrable.py (CampoFiltrable): envuelve un ft.TextField
real (así que text_size/focus()/on_submit siguen siendo reales, participa
del encadenado de Enter de una fila de alta igual que CampoFiltrable),
expone `.control` para embeber en un Row/Column del caller, y no importa
nada de services/repositories (CLAUDE.md §2 — es un control de UI genérico,
recibe decimales/valores iniciales ya resueltos por el caller).

--- Qué SÍ resuelve este componente (mecánica universal) ---

Al confirmar (blur o Enter):
- Si el texto está vacío, o es IDÉNTICO al último valor ya confirmado
  (protege contra doble confirmación: ver más abajo, "blur + click en el
  botón de confirmar de una celda inline"), no hace nada — ni error ni
  llamada a on_confirmar.
- Si el texto empieza con "=": se evalúa con
  utils.calculadora_segura.evaluar_expresion() (ast, NUNCA eval()/exec()).
  Si es válida, el valor mostrado se reemplaza por el resultado calculado
  (formateado, no la fórmula) y se llama on_confirmar(monto_minor) con el
  resultado convertido a minor units (round(monto * 10**decimales)). Si es
  inválida (CalculadoraError), el campo queda con borde rojo y NO se llama
  a on_confirmar — el texto tipeado se deja tal cual para poder corregirlo
  en el lugar (no se revierte, mismo criterio que ya tenía Presupuestos).
- Si NO empieza con "=": mismo chequeo de "¿es un número válido?" que ya
  hacía cada pantalla a mano (float(texto.replace(",", "."))) — inválido =
  borde rojo, no confirma (unifica el feedback visual con el caso de
  fórmula inválida, en vez de que cada pantalla revierta el texto a su
  manera). Válido = se llama on_confirmar(monto_minor) igual que en la
  rama de fórmula.

on_error (opcional): si el caller lo pasa, se invoca con un mensaje de
texto en los dos casos de "borde rojo" de arriba, para que cada pantalla
pueda mostrarlo con su propio mecanismo de SnackBar (_mostrar_error) —
mismos mensajes que ya mostraba Presupuestos ("Fórmula inválida: ..."/"El
monto no es un número válido."). Si no se pasa, el componente se queda
solo con el borde rojo (ej. filas de alta de Registro/Compras en cuotas,
que hoy tampoco mostraban error hasta que se confirmaba la fila entera).

--- Qué NO resuelve este componente (queda en el caller, a propósito) ---

Validación de DOMINIO (¿el monto tiene que ser positivo? ¿puede ser cero?
¿el signo decide otra cosa, como tipo_movimiento o routing por categoría?)
NO vive acá — varía por pantalla (Presupuestos exige > 0; el Registro y
Compras en cuotas exigen != 0 pero permiten negativo porque el signo decide
gasto/ingreso o el routing especial; la edición inline del Registro exige >
0 porque ahí el monto siempre se edita en valor absoluto). El caller
implementa esa regla DENTRO de su propio on_confirmar, y puede RECHAZAR el
valor lanzando cualquier excepción — mismo patrón ya usado por
_celda_texto()/_celda_dropdown()/_celda_campo_filtrable() en
ui/components/registro_transacciones.py (on_confirmar puede lanzar
TransactionError/ValueError, el caller de esas celdas lo atrapa, muestra el
error y revierte). Acá el componente atrapa CUALQUIER excepción que lance
on_confirmar, revierte el texto mostrado (y, si persistir_formula=True, la
fórmula recordada) al último estado válido, y devuelve False — el caller
es responsable de mostrar su propio mensaje de error ANTES de lanzar (este
componente no conoce PresupuestoError/TransactionError/FeesError/
SavingsError, ni falta que le hace).

--- persistir_formula ---

- True (Presupuestos, único caso hoy): on_focus precarga el TEXTO de la
  fórmula si la fila la tenía guardada (comportamiento "tipo Excel" ya
  confirmado, solo la PRIMERA vez que el campo gana foco en esta instancia
  — mismo guard `ya_precargo_formula` que tenía Presupuestos, ahora interno
  acá). El texto de la fórmula usada (con el "=") queda disponible en la
  propiedad `.formula` DESPUÉS de un confirm exitoso con fórmula, o None si
  el último confirm fue con un número directo — pensado para que el
  caller la lea DENTRO de su propio on_confirmar y la persista junto con
  el monto (ver ui/screens/presupuestos.py, columna
  presupuestos.formula_estimado).
- False (default — Registro de transacciones, Compras en cuotas): `.formula`
  siempre es None, sin importar qué haya tipeado el usuario — el campo,
  una vez confirmado, muestra siempre el número (nunca la fórmula que lo
  generó), y no hay precarga en on_focus.

--- Formato de visualización ---

Siempre ".2f" tras un confirm exitoso (fórmula o número directo) — mismo
criterio ya usado en TODA la app para mostrar montos (ver
utils.money.amount_display() y el texto_numero original de Presupuestos,
ambos hardcodean ".2f" sin importar los decimales reales de la moneda).
`decimales` SÍ se usa para el cálculo real de monto_minor que recibe
on_confirmar (round(monto * 10**decimales)) — con una moneda de más
decimales que 2 (ej. BTC, 8 decimales, ver db/seed.sql), una fórmula que
necesite esa precisión de más quedaría visualmente truncada a 2 decimales
apenas se confirma (aunque el monto_minor calculado y lo que termine
persistido si el caller lo usa tal cual SÍ sea preciso) — limitación
conocida, no resuelta acá por ser consistente con cómo ya se comporta el
resto de la app, no una regresión nueva de este componente.

--- Protección contra doble confirmación (blur + click en botón) ---

Una celda de edición inline con un ícono de confirmar explícito (ver
_celda_monto() en ui/components/registro_transacciones.py) puede disparar
blur en el campo (por el click) Y el propio on_click del botón, ambos
apuntando a confirmar — sin la guarda de "texto idéntico al último
confirmado" de arriba, on_confirmar se llamaría dos veces. CampoFiltrable
resuelve un problema relacionado (blur vs. click en una sugerencia) con un
delay async — acá no hace falta: como el segundo intento de confirmar ve
el mismo texto ya normalizado por el primero, la guarda de "sin cambios"
alcanza sin necesitar async/sleep.

Reglas de arquitectura: sin dependencias de servicios/repositorios — ver
CLAUDE.md §2/§3, mismo criterio que campo_filtrable.py.
"""

from typing import Callable, Optional

import flet as ft

from utils.calculadora_segura import CalculadoraError, evaluar_expresion


class CampoMonto:
    """
    Uso típico (persistir_formula=False, caso por default — fila de alta):
        campo = CampoMonto(
            page,
            on_confirmar=lambda monto_minor: None,  # la fila confirma junta, no campo a campo
            width=120, hint_text="± monto",
            on_avanzar=lambda: siguiente_campo.focus(),
        )
        fila = ft.Row([..., campo.control, ...])
        ...
        monto_con_signo = float((campo.texto or "").strip().replace(",", "."))

    Uso típico (persistir_formula=True — Presupuestos, confirma campo a
    campo en cada blur/Enter):
        def _on_confirmar(monto_minor: int) -> None:
            if monto_minor <= 0:
                mostrar_error("...")
                raise ValueError()  # revierte texto/fórmula, no propaga más
            servicio.set_budget(..., monto_estimado_minor=monto_minor,
                                 formula_estimado=campo.formula)

        campo = CampoMonto(
            page, on_confirmar=_on_confirmar, decimales=2,
            persistir_formula=True,
            valor_inicial_minor=fila_actual_o_None,
            formula_inicial=formula_actual_o_None,
            on_error=mostrar_error,
        )
    """

    def __init__(
        self,
        page: ft.Page,
        on_confirmar: Callable[[int], None],
        decimales: int = 2,
        persistir_formula: bool = False,
        valor_inicial_minor: Optional[int] = None,
        formula_inicial: Optional[str] = None,
        on_error: Optional[Callable[[str], None]] = None,
        width: Optional[int] = None,
        hint_text: Optional[str] = None,
        label: Optional[str] = None,
        dense: bool = True,
        text_size: Optional[int] = None,
        autofocus: bool = False,
        on_avanzar: Optional[Callable[[], None]] = None,
    ):
        """
        Args:
            on_confirmar:       Llamado con el monto ya resuelto, en minor
                                 units (round(monto * 10**decimales)), cada
                                 vez que el campo confirma un valor nuevo
                                 (fórmula válida o número directo válido).
                                 Puede lanzar cualquier excepción para
                                 RECHAZAR el valor (validación de dominio
                                 del caller, ej. "debe ser > 0") — el
                                 componente atrapa la excepción, revierte
                                 texto/fórmula al último estado válido, y
                                 el caller es responsable de mostrar su
                                 propio mensaje de error antes de lanzar.
            decimales:           Para convertir el resultado a minor units.
                                 No afecta el formato mostrado (siempre
                                 ".2f", ver docstring del módulo).
            persistir_formula:   Ver docstring del módulo.
            valor_inicial_minor: Precarga el campo (texto formateado). None
                                 = campo vacío.
            formula_inicial:     Solo relevante si persistir_formula=True —
                                 texto de la fórmula (CON "=") a precargar
                                 en el primer on_focus, si la había.
            on_error:            Opcional — mensaje de texto para que el
                                 caller lo muestre con su propio SnackBar.
            label/hint_text:     Passthrough directos al ft.TextField
                                 interno — label para diálogos verticales
                                 (mismo criterio que el resto de los campos
                                 de un AlertDialog en esta app, ej.
                                 ui/screens/cuentas.py), hint_text para
                                 filas de tabla densas (fila de alta,
                                 grilla de Presupuestos).
            on_avanzar:          Opcional — llamado tras un Enter que
                                 confirmó sin error (vacío cuenta como "sin
                                 error", igual que hoy en las filas de alta
                                 que no bloquean Tab/Enter por un campo de
                                 Monto sin completar). Mismo rol que en
                                 CampoFiltrable.
        """
        self._page = page
        self._on_confirmar = on_confirmar
        self._decimales = decimales
        self._persistir_formula = persistir_formula
        self._on_error = on_error
        self._on_avanzar = on_avanzar
        self._formula_actual: Optional[str] = formula_inicial if persistir_formula else None
        self._ya_precargo_formula = False

        texto_inicial = self._formatear_minor(valor_inicial_minor) if valor_inicial_minor is not None else ""
        self._texto_valido_actual = texto_inicial

        self._campo = ft.TextField(
            value=texto_inicial,
            hint_text=hint_text,
            label=label,
            dense=dense,
            width=width,
            text_size=text_size,
            autofocus=autofocus,
            on_focus=self._on_focus,
            on_blur=self._on_blur,
            on_submit=self._on_submit,
        )
        self.control: ft.Control = self._campo

    # ----------------------------------------------------------
    # API pública
    # ----------------------------------------------------------

    @property
    def texto(self) -> str:
        return self._campo.value or ""

    @property
    def formula(self) -> Optional[str]:
        """Fórmula (con '=') del último confirm exitoso, o None — ver docstring del módulo."""
        return self._formula_actual

    def focus(self) -> None:
        self._campo.focus()

    def confirmar(self) -> bool:
        """Dispara la misma lógica de blur/Enter a mano — para un botón de confirmar explícito (ver _celda_monto())."""
        return self._confirmar()

    # ----------------------------------------------------------
    # Formato
    # ----------------------------------------------------------

    def _formatear_minor(self, monto_minor: int) -> str:
        return f"{monto_minor / (10 ** self._decimales):.2f}"

    # ----------------------------------------------------------
    # Confirmación (blur / Enter / botón explícito)
    # ----------------------------------------------------------

    def _confirmar(self) -> bool:
        texto = (self._campo.value or "").strip()
        self._campo.border_color = None

        # Vacío, o sin cambios desde el último confirm exitoso — ver
        # docstring del módulo ("protección contra doble confirmación").
        if not texto or texto == self._texto_valido_actual:
            self._page.update()
            return True

        if texto.startswith("="):
            try:
                monto = evaluar_expresion(texto[1:])
            except CalculadoraError as err:
                self._campo.border_color = ft.Colors.ERROR
                if self._on_error:
                    self._on_error(f"Fórmula inválida: {err}")
                self._page.update()
                return False
            formula_nueva = texto if self._persistir_formula else None
        else:
            try:
                monto = float(texto.replace(",", "."))
            except ValueError:
                self._campo.border_color = ft.Colors.ERROR
                if self._on_error:
                    self._on_error("El monto no es un número válido.")
                self._page.update()
                return False
            formula_nueva = None

        monto_minor = round(monto * (10 ** self._decimales))

        formula_previa = self._formula_actual
        texto_previo = self._texto_valido_actual
        self._formula_actual = formula_nueva
        try:
            self._on_confirmar(monto_minor)
        except Exception:
            # Rechazado por validación de dominio del caller (ya mostró su
            # propio error) — revierte al último estado válido, ver
            # docstring del módulo.
            self._formula_actual = formula_previa
            self._campo.value = texto_previo
            self._page.update()
            return False

        self._texto_valido_actual = self._formatear_minor(monto_minor)
        self._campo.value = self._texto_valido_actual
        self._page.update()
        return True

    # ----------------------------------------------------------
    # Handlers de eventos
    # ----------------------------------------------------------

    def _on_focus(self, e: ft.ControlEvent) -> None:
        if self._persistir_formula and self._formula_actual and not self._ya_precargo_formula:
            self._campo.value = self._formula_actual
            self._ya_precargo_formula = True
            self._page.update()

    def _on_blur(self, e: ft.ControlEvent) -> None:
        self._confirmar()

    def _on_submit(self, e: ft.ControlEvent) -> None:
        exito = self._confirmar()
        if exito and self._on_avanzar:
            self._on_avanzar()
