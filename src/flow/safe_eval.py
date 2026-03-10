"""Safe expression evaluator for GazerFlow.

Replaces dangerous eval() with a restricted AST-based evaluator.
Only allows safe operations: attribute access, comparison, boolean logic,
basic functions (len, any, all), and dict/list indexing.
"""

import ast
import logging
import operator
from typing import Any, Dict, Optional

logger = logging.getLogger("SafeEval")

# Safe binary operators
_SAFE_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.And: None,  # Handled specially for short-circuit
    ast.Or: None,   # Handled specially for short-circuit
}

# Safe unary operators
_SAFE_UNARYOPS = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Safe built-in functions
_SAFE_FUNCTIONS = {
    "len": len,
    "any": any,
    "all": all,
    "bool": bool,
    "int": int,
    "float": float,
    "str": str,
    "list": list,
    "dict": dict,
    "min": min,
    "max": max,
    "abs": abs,
    "sum": sum,
    "sorted": sorted,
    "reversed": lambda x: list(reversed(x)),
    "enumerate": lambda x: list(enumerate(x)),
    "zip": lambda *args: list(zip(*args)),
    "range": lambda *args: list(range(*args)),
    "isinstance": isinstance,
}

# Safe method names that can be called on objects
# This whitelist prevents calling dangerous methods like __class__, __import__, etc.
_SAFE_METHOD_NAMES = frozenset({
    # dict methods
    "get", "keys", "values", "items", "copy",
    # list methods
    "index", "count", "copy",
    # str methods
    "lower", "upper", "strip", "lstrip", "rstrip", "split", "join",
    "replace", "startswith", "endswith", "find", "rfind",
    "encode", "decode", "isdigit", "isalpha", "isalnum",
    # set methods
    "union", "intersection", "difference",
})

# Types on which attribute access is permitted
_SAFE_ATTR_TYPES = (dict, str, list, set, frozenset, tuple, int, float, bool)

# Maximum recursion depth for nested expressions
_MAX_DEPTH = 50

# Maximum number of AST nodes allowed
_MAX_NODES = 200


class SafeEvalError(Exception):
    """Raised when safe evaluation fails."""
    pass


class UnsafeExpressionError(SafeEvalError):
    """Raised when an unsafe expression is detected."""
    pass


class _SafeEvaluator(ast.NodeVisitor):
    """AST-based safe expression evaluator."""

    def __init__(self, names: Dict[str, Any], max_depth: int = _MAX_DEPTH) -> None:
        self._names = names
        self._max_depth = max_depth
        self._depth = 0
        self._node_count = 0

    def _check_limits(self) -> None:
        """Check recursion and node count limits."""
        self._node_count += 1
        if self._depth > self._max_depth:
            raise SafeEvalError(f"Expression too deeply nested (max {self._max_depth})")
        if self._node_count > _MAX_NODES:
            raise SafeEvalError(f"Expression too complex (max {_MAX_NODES} nodes)")

    def visit(self, node: ast.AST) -> Any:
        self._check_limits()
        self._depth += 1
        try:
            return super().visit(node)
        finally:
            self._depth -= 1

    def generic_visit(self, node: ast.AST) -> Any:
        raise UnsafeExpressionError(f"Unsupported expression type: {type(node).__name__}")

    def visit_Expression(self, node: ast.Expression) -> Any:
        return self.visit(node.body)

    def visit_Constant(self, node: ast.Constant) -> Any:
        return node.value

    def visit_Name(self, node: ast.Name) -> Any:
        name = node.id
        if name in self._names:
            return self._names[name]
        if name in _SAFE_FUNCTIONS:
            return _SAFE_FUNCTIONS[name]
        raise SafeEvalError(f"Unknown name: {name}")

    def visit_Attribute(self, node: ast.Attribute) -> Any:
        value = self.visit(node.value)
        attr = node.attr
        # Prevent access to dunder attributes
        if attr.startswith("_"):
            raise UnsafeExpressionError(f"Access to private attribute '{attr}' is forbidden")
        # Only permit attribute access on safe built-in types to prevent
        # method/attribute leakage from arbitrary user-provided objects.
        if not isinstance(value, _SAFE_ATTR_TYPES):
            raise UnsafeExpressionError(
                f"Attribute access on type '{type(value).__name__}' is not allowed"
            )
        if isinstance(value, dict):
            # For dicts: whitelisted method names go through getattr,
            # everything else is treated as a key lookup.
            if attr in _SAFE_METHOD_NAMES:
                return getattr(value, attr)
            return value.get(attr)
        if hasattr(value, attr):
            return getattr(value, attr)
        return None

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        value = self.visit(node.value)
        index = self.visit(node.slice)
        try:
            return value[index]
        except (KeyError, IndexError, TypeError):
            return None

    def visit_List(self, node: ast.List) -> Any:
        return [self.visit(el) for el in node.elts]

    def visit_Tuple(self, node: ast.Tuple) -> Any:
        return tuple(self.visit(el) for el in node.elts)

    def visit_Dict(self, node: ast.Dict) -> Any:
        return {
            self.visit(k): self.visit(v)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }

    def visit_Set(self, node: ast.Set) -> Any:
        return {self.visit(el) for el in node.elts}

    def visit_BinOp(self, node: ast.BinOp) -> Any:
        op_type = type(node.op)
        if op_type not in _SAFE_BINOPS:
            raise UnsafeExpressionError(f"Unsupported binary operator: {op_type.__name__}")
        left = self.visit(node.left)
        right = self.visit(node.right)
        return _SAFE_BINOPS[op_type](left, right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> Any:
        op_type = type(node.op)
        if op_type not in _SAFE_UNARYOPS:
            raise UnsafeExpressionError(f"Unsupported unary operator: {op_type.__name__}")
        operand = self.visit(node.operand)
        return _SAFE_UNARYOPS[op_type](operand)

    def visit_Compare(self, node: ast.Compare) -> Any:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            op_type = type(op)
            if op_type not in _SAFE_BINOPS:
                raise UnsafeExpressionError(f"Unsupported comparison: {op_type.__name__}")
            right = self.visit(comparator)
            if not _SAFE_BINOPS[op_type](left, right):
                return False
            left = right
        return True

    def visit_BoolOp(self, node: ast.BoolOp) -> Any:
        if isinstance(node.op, ast.And):
            result = True
            for value in node.values:
                result = self.visit(value)
                if not result:
                    return result  # Short-circuit
            return result
        elif isinstance(node.op, ast.Or):
            result = False
            for value in node.values:
                result = self.visit(value)
                if result:
                    return result  # Short-circuit
            return result
        else:
            raise UnsafeExpressionError(f"Unsupported boolean operator: {type(node.op).__name__}")

    def visit_IfExp(self, node: ast.IfExp) -> Any:
        if self.visit(node.test):
            return self.visit(node.body)
        return self.visit(node.orelse)

    def visit_Call(self, node: ast.Call) -> Any:
        func = self.visit(node.func)
        func_name = getattr(func, "__name__", str(func))
        
        if func in _SAFE_FUNCTIONS.values():
            # Whitelisted builtin function
            pass
        elif hasattr(func, "__self__"):
            # It's a bound method - check if the method name is whitelisted
            if func_name not in _SAFE_METHOD_NAMES:
                raise UnsafeExpressionError(
                    f"Calling method '{func_name}' is not allowed. "
                    f"Allowed methods: {', '.join(sorted(_SAFE_METHOD_NAMES)[:10])}..."
                )
        else:
            raise UnsafeExpressionError(f"Calling '{func_name}' is not allowed")
        
        args = [self.visit(arg) for arg in node.args]
        kwargs = {kw.arg: self.visit(kw.value) for kw in node.keywords if kw.arg is not None}
        try:
            return func(*args, **kwargs)
        except Exception as e:
            raise SafeEvalError(f"Function call failed: {e}")

    def visit_ListComp(self, node: ast.ListComp) -> Any:
        raise UnsafeExpressionError("List comprehensions are not allowed for safety")

    def visit_DictComp(self, node: ast.DictComp) -> Any:
        raise UnsafeExpressionError("Dict comprehensions are not allowed for safety")

    def visit_SetComp(self, node: ast.SetComp) -> Any:
        raise UnsafeExpressionError("Set comprehensions are not allowed for safety")

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> Any:
        raise UnsafeExpressionError("Generator expressions are not allowed for safety")

    def visit_Lambda(self, node: ast.Lambda) -> Any:
        raise UnsafeExpressionError("Lambda expressions are not allowed for safety")


def safe_eval(expr: str, names: Optional[Dict[str, Any]] = None) -> Any:
    """Safely evaluate an expression with restricted operations.

    Args:
        expr: The expression string to evaluate.
        names: A dictionary of names available in the expression.

    Returns:
        The result of the expression.

    Raises:
        SafeEvalError: If the expression is invalid or unsafe.
        UnsafeExpressionError: If the expression contains forbidden operations.
    """
    if names is None:
        names = {}

    # Add constants
    names.setdefault("True", True)
    names.setdefault("False", False)
    names.setdefault("None", None)

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise SafeEvalError(f"Invalid expression syntax: {e}")

    evaluator = _SafeEvaluator(names)
    try:
        return evaluator.visit(tree)
    except SafeEvalError:
        raise
    except Exception as e:
        raise SafeEvalError(f"Expression evaluation failed: {e}")


def safe_eval_bool(expr: str, names: Optional[Dict[str, Any]] = None) -> bool:
    """Safely evaluate an expression and return a boolean result.

    Args:
        expr: The expression string to evaluate.
        names: A dictionary of names available in the expression.

    Returns:
        True if the expression evaluates to a truthy value, False otherwise.
    """
    try:
        result = safe_eval(expr, names)
        return bool(result)
    except SafeEvalError as e:
        logger.warning("Safe eval condition '%s' failed: %s", expr, e)
        return False
