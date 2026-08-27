"""
verify/utils/verify_calculadora_segura.py

Verifica utils/calculadora_segura.py — subcarpeta nueva (`verify/utils/`,
ver verify/README.md "si abre un bloque nuevo que todavía no tiene
subcarpeta, se crea una") porque este módulo no pertenece a ningún bloque
de dominio de services/repositories, vive aislado en utils/.

Cubre: expresión simple (+/-/*/), con paréntesis, negativo unario,
división (incluyendo por cero), expresión vacía, error de sintaxis, y — el
caso central de esta tarea — que CUALQUIER intento de ejecutar código
(nombres, llamadas a función como "__import__('os')", atributos,
comprensiones) se rechace con CalculadoraError SIN ejecutarse, nunca con
eval()/exec().

Garantía estática (_llamadas_a_eval_o_exec() más abajo): un chequeo de
substring plano ("eval(" in codigo_fuente) daba un FALSO POSITIVO real —
el propio docstring del módulo dice, en prosa, "NUNCA usa eval()/exec()"
(la advertencia de no usarlo), y esa frase CONTIENE el substring "eval("
aunque no sea una llamada de verdad. La versión corregida parsea
calculadora_segura.py con ast.parse() — análisis estático del código
fuente, no ejecución, mismo principio que el propio módulo aplica sobre la
expresión del usuario — y camina el árbol buscando específicamente nodos
ast.Call cuyo func sea ast.Name(id='eval') o ast.Name(id='exec'): el
código llama a eval/exec solo si existe ESE patrón exacto en el AST, no si
la palabra aparece en un comentario o docstring.

Correlo con:
    python verify/utils/verify_calculadora_segura.py
"""

import ast
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from utils.calculadora_segura import CalculadoraError, evaluar_expresion


def _llamadas_a_eval_o_exec(codigo_fuente: str) -> list[str]:
    """
    Análisis estático del código fuente de calculadora_segura.py — parsea
    con ast.parse() (no ejecuta ni una línea de ese módulo) y devuelve una
    entrada por cada ast.Call real cuyo func sea eval o exec. Lista vacía
    = ninguna llamada real, sin importar cuántas veces la palabra "eval("/
    "exec(" aparezca en comentarios o docstrings.
    """
    arbol = ast.parse(codigo_fuente)
    encontradas = []
    for nodo in ast.walk(arbol):
        if (
            isinstance(nodo, ast.Call)
            and isinstance(nodo.func, ast.Name)
            and nodo.func.id in ("eval", "exec")
        ):
            encontradas.append(f"{nodo.func.id}() en línea {nodo.lineno}")
    return encontradas


def main() -> None:
    casos_ok = 0
    casos_total = 0

    def caso(descripcion: str, esperado, obtenido) -> None:
        nonlocal casos_ok, casos_total
        casos_total += 1
        if esperado == obtenido:
            casos_ok += 1
            print(f"✅ {descripcion} — esperado: {esperado!r}, obtenido: {obtenido!r}")
        else:
            print(f"❌ {descripcion} — esperado: {esperado!r}, obtenido: {obtenido!r}")

    def caso_excepcion(descripcion: str, callable_) -> None:
        nonlocal casos_ok, casos_total
        casos_total += 1
        try:
            resultado = callable_()
            print(f"❌ {descripcion} — esperaba CalculadoraError, devolvió: {resultado!r}")
        except CalculadoraError as err:
            casos_ok += 1
            print(f"✅ {descripcion} — lanzó CalculadoraError como se esperaba ({err})")
        except Exception as e:
            print(f"❌ {descripcion} — esperaba CalculadoraError, se lanzó {type(e).__name__}: {e!r}")

    print("--- Expresiones válidas ---")
    caso("suma simple: '15000+3200-500'", 17700.0, evaluar_expresion("15000+3200-500"))
    caso("con espacios: ' 100 + 50 '", 150.0, evaluar_expresion(" 100 + 50 "))
    caso("con paréntesis: '(2+3)*4'", 20.0, evaluar_expresion("(2+3)*4"))
    caso("precedencia sin paréntesis: '2+3*4'", 14.0, evaluar_expresion("2+3*4"))
    caso("negativo unario al inicio: '-5+10'", 5.0, evaluar_expresion("-5+10"))
    caso("decimales: '10.5*2'", 21.0, evaluar_expresion("10.5*2"))
    caso("división: '100/4'", 25.0, evaluar_expresion("100/4"))
    caso("un solo número, sin operador: '42'", 42.0, evaluar_expresion("42"))
    caso("paréntesis anidados: '((1+2)*(3+4))'", 21.0, evaluar_expresion("((1+2)*(3+4))"))

    print("\n--- Errores esperables (aritmética inválida, no inyección) ---")
    caso_excepcion("expresión vacía", lambda: evaluar_expresion(""))
    caso_excepcion("solo espacios", lambda: evaluar_expresion("   "))
    caso_excepcion("error de sintaxis: '5+'", lambda: evaluar_expresion("5+"))
    caso_excepcion("error de sintaxis: '5 5'", lambda: evaluar_expresion("5 5"))
    caso_excepcion("paréntesis sin cerrar: '(5+3'", lambda: evaluar_expresion("(5+3"))
    caso_excepcion("división por cero: '5/0'", lambda: evaluar_expresion("5/0"))

    print("\n--- Intentos de inyección / código arbitrario — deben rechazarse SIEMPRE ---")
    caso_excepcion(
        "llamada a función: \"__import__('os').system('echo pwned')\"",
        lambda: evaluar_expresion("__import__('os').system('echo pwned')"),
    )
    caso_excepcion("llamada a función simple: 'print(1)'", lambda: evaluar_expresion("print(1)"))
    caso_excepcion("nombre/variable: 'x+1'", lambda: evaluar_expresion("x+1"))
    caso_excepcion("atributo: '(1).__class__'", lambda: evaluar_expresion("(1).__class__"))
    caso_excepcion("string literal: \"'a'+'b'\"", lambda: evaluar_expresion("'a'+'b'"))
    caso_excepcion("comprensión de lista: '[x for x in range(10)]'", lambda: evaluar_expresion("[x for x in range(10)]"))
    caso_excepcion("lambda: 'lambda: 1'", lambda: evaluar_expresion("lambda: 1"))
    caso_excepcion("subíndice: '[1,2,3][0]'", lambda: evaluar_expresion("[1,2,3][0]"))
    caso_excepcion("potencia (no está en Add/Sub/Mult/Div): '2**10'", lambda: evaluar_expresion("2**10"))
    caso_excepcion("bool no es aritmética válida: 'True+1'", lambda: evaluar_expresion("True+1"))
    caso_excepcion(
        "múltiples statements con ';': '1+1; __import__(\"os\")'",
        lambda: evaluar_expresion('1+1; __import__("os")'),
    )

    print("\n--- Garantía estática: el módulo nunca LLAMA a eval()/exec() (AST, no substring) ---")
    codigo_fuente = (Path(__file__).resolve().parent.parent.parent / "utils" / "calculadora_segura.py").read_text(encoding="utf-8")
    # Precondición del caso: el docstring del módulo SÍ menciona "eval("/
    # "exec(" en prosa (la advertencia de no usarlos) — si esto alguna vez
    # deja de ser cierto, no invalida el caso de abajo, pero confirma que
    # el caso de verdad está ejercitando el escenario de falso positivo
    # que motivó el cambio a AST (ver docstring del módulo).
    caso(
        "precondición: el código fuente SÍ contiene el substring 'eval(' en algún comentario/docstring",
        True,
        "eval(" in codigo_fuente,
    )
    llamadas_peligrosas = _llamadas_a_eval_o_exec(codigo_fuente)
    caso(
        "el AST de calculadora_segura.py no contiene ninguna llamada REAL a eval()/exec() "
        "(0 nodos ast.Call con func eval/exec, sin importar cuántas veces la palabra aparezca en prosa)",
        [],
        llamadas_peligrosas,
    )

    print(f"\n--- Resumen: {casos_ok}/{casos_total} casos OK ---")


if __name__ == "__main__":
    main()
