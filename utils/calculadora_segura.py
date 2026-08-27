"""
DeltaBalance — utils/calculadora_segura.py

Evaluador aritmético seguro para expresiones tipeadas por el usuario en
campos de monto (ej. "=15000+3200-500" en el campo Estimado de
ui/screens/presupuestos.py, ver docstring de ese módulo).

NUNCA usa eval()/exec() — CLAUDE.md §0 lo prohíbe explícitamente para este
caso. En su lugar, parsea la expresión con ast.parse(expr, mode='eval') y
camina el árbol resultante a mano, aceptando ÚNICAMENTE el subconjunto
mínimo necesario para aritmética simple:
    - ast.Expression (nodo raíz que produce mode='eval')
    - ast.BinOp con Add/Sub/Mult/Div
    - ast.UnaryOp con USub (para "-5" al principio de una expresión)
    - ast.Constant numérico (int/float, nunca bool/str/None — bool es
      subclase de int en Python, se excluye a mano)
Cualquier otro nodo (Name, Call, Attribute, Subscript, comprensiones,
lambdas, etc.) — incluido un intento de inyección tipo
"__import__('os').system('...')" — se rechaza con CalculadoraError ANTES
de evaluar nada: ast.parse() por sí solo NO ejecuta código (a diferencia
de eval()), así que ni siquiera un nodo peligroso llega a "correr" — se lo
detecta caminando el árbol y se aborta con un error claro.

Módulo aislado a propósito (sin dependencias de ui/ ni services/, ni
siquiera de db/) para que el mecanismo de seguridad sea chico y auditable
en un solo lugar — ver verify/utils/verify_calculadora_segura.py.

La convención de "el texto empieza con '=' significa fórmula" es decisión
del caller (ui/screens/presupuestos.py) — este módulo solo sabe evaluar
una expresión ya identificada como tal, sin el '=' inicial.
"""

import ast

# Nodos ast.operator permitidos para BinOp — cualquier otro (FloorDiv, Mod,
# Pow, BitAnd, BitOr, BitXor, LShift, RShift, MatMult) queda fuera a
# propósito: la consigna pide exactamente Add/Sub/Mult/Div, nada más.
_OPERADORES_BINARIOS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
}


class CalculadoraError(Exception):
    """Raised when an expression is not a valid/safe arithmetic expression."""


def _evaluar_nodo(nodo: ast.AST) -> float:
    if isinstance(nodo, ast.Expression):
        return _evaluar_nodo(nodo.body)

    if isinstance(nodo, ast.Constant):
        # bool es subclase de int en Python (isinstance(True, int) es
        # True) — se excluye a mano para no aceptar "=True+1" como si
        # fuera aritmética válida.
        if isinstance(nodo.value, bool) or not isinstance(nodo.value, (int, float)):
            raise CalculadoraError(
                f"Solo se permiten números en la expresión — encontrado: {nodo.value!r}."
            )
        return nodo.value

    if isinstance(nodo, ast.BinOp):
        operador = _OPERADORES_BINARIOS.get(type(nodo.op))
        if operador is None:
            raise CalculadoraError(
                f"Operador no permitido: '{type(nodo.op).__name__}'. Solo se permiten + - * /."
            )
        izquierda = _evaluar_nodo(nodo.left)
        derecha = _evaluar_nodo(nodo.right)
        if isinstance(nodo.op, ast.Div) and derecha == 0:
            raise CalculadoraError("División por cero.")
        return operador(izquierda, derecha)

    if isinstance(nodo, ast.UnaryOp):
        if not isinstance(nodo.op, ast.USub):
            raise CalculadoraError(
                f"Operador unario no permitido: '{type(nodo.op).__name__}'. Solo se permite '-'."
            )
        return -_evaluar_nodo(nodo.operand)

    # Cualquier otro tipo de nodo — ast.Name (variables), ast.Call
    # (funciones, incluido "__import__(...)"), ast.Attribute, ast.Subscript,
    # comprensiones, lambdas, etc. — se rechaza acá, nunca llega a evaluarse.
    raise CalculadoraError(
        f"Expresión no permitida: '{type(nodo).__name__}' no es aritmética simple "
        "(solo se permiten números, +, -, *, /, paréntesis)."
    )


def evaluar_expresion(expresion: str) -> float:
    """
    Evalúa una expresión aritmética simple de forma segura (sin eval()/
    exec()). Acepta +, -, *, /, paréntesis, signo negativo unario y
    números (enteros o decimales, '.' como separador) — nada más.

    Args:
        expresion: el texto tal cual lo tipeó el usuario, SIN el '='
                   inicial (esa convención es del caller).

    Returns:
        El resultado numérico (float).

    Raises:
        CalculadoraError: si la expresión está vacía, tiene un error de
                          sintaxis, o usa cualquier construcción fuera del
                          subconjunto permitido (nombres, llamadas a
                          función, atributos, etc.).
    """
    texto = (expresion or "").strip()
    if not texto:
        raise CalculadoraError("La expresión está vacía.")
    try:
        arbol = ast.parse(texto, mode="eval")
    except SyntaxError as err:
        raise CalculadoraError(f"Expresión inválida: {err.msg}.") from err
    resultado = _evaluar_nodo(arbol)
    return float(resultado)
