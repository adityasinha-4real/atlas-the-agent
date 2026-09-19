"""``calculator`` — safe arithmetic evaluation via a restricted AST walk.

Never uses ``eval``. Only numeric literals and a whitelist of arithmetic
operators/functions are permitted; anything else (names, calls to unknown
functions, attribute access) is rejected as a tool error, which the executor
surfaces as an observation.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import ClassVar

from pydantic import BaseModel, Field

from atlas.tools.base import Tool, ToolError, ToolResult

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCS = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "min": min,
    "max": max,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
}
_CONSTS = {"pi": math.pi, "e": math.e}


class CalculatorArgs(BaseModel):
    expression: str = Field(
        description="A pure arithmetic expression, e.g. '0.15 * 68000000'.",
        min_length=1,
        max_length=500,
    )


class CalculatorTool(Tool):
    name: ClassVar[str] = "calculator"
    description: ClassVar[str] = (
        "Evaluate an arithmetic expression. Supports + - * / // % ** and the "
        "functions abs, round, sqrt, floor, ceil, min, max, log, log10, exp, plus "
        "the constants pi and e. Express percentages as fractions (15% -> 0.15)."
    )
    Args: ClassVar[type[BaseModel]] = CalculatorArgs

    async def run(self, args: BaseModel) -> ToolResult:
        assert isinstance(args, CalculatorArgs)
        try:
            tree = ast.parse(args.expression, mode="eval")
            value = _eval(tree.body)
        except ToolError:
            raise
        except SyntaxError as exc:
            raise ToolError(f"Not a valid expression: {exc.msg}") from exc
        except ZeroDivisionError as exc:
            raise ToolError("Division by zero.") from exc
        except Exception as exc:  # arithmetic overflow, domain errors, etc.
            raise ToolError(f"Could not evaluate expression: {exc}") from exc
        return ToolResult.success(_format(value))


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ToolError("Only numeric literals are allowed.")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ToolError(f"Operator not allowed: {type(node.op).__name__}.")
        return op(_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ToolError(f"Unary operator not allowed: {type(node.op).__name__}.")
        return op(_eval(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ToolError(f"Unknown name: {node.id!r}.")
    if isinstance(node, ast.Call):
        return _eval_call(node)
    raise ToolError(f"Unsupported syntax: {type(node).__name__}.")


def _eval_call(node: ast.Call) -> float:
    if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
        raise ToolError("Only whitelisted math functions may be called.")
    if node.keywords:
        raise ToolError("Keyword arguments are not supported.")
    func = _FUNCS[node.func.id]
    return func(*(_eval(arg) for arg in node.args))


def _format(value: float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return repr(value)
