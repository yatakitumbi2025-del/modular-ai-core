"""
safe_calc — deterministic arithmetic with significant-figure tracking.

Why this exists
---------------
Llama 3.3 70B predicts tokens; it does not compute. It gets significant
figures wrong because sig-figs are a counting rule, not a language pattern.
This module computes the answer with decimal.Decimal and applies the
sig-fig rules mechanically, so the number the model reports is ground truth
it cannot hallucinate.

Safety
------
No eval(), no exec(). The expression is parsed with ast and every node is
checked against an allowlist. Anything else is rejected.

Conventions (stated explicitly so they are auditable)
----------------------------------------------------
* A number written with a decimal point or in scientific notation is a
  MEASURED value; its significant figures are counted.
* A bare integer (7, 100, 3) is treated as EXACT — a counting number or a
  definition. Exact values never limit the precision of a result.
  To mark 100 as a 3-sig-fig measurement, write 100. or 1.00e2.
* Multiplication / division / powers: result keeps the FEWEST sig figs of
  the measured operands.
* Addition / subtraction: result keeps the FEWEST decimal places of the
  measured operands.
* Rounding is half-up (5 rounds away from zero), matching how sig-figs are
  taught in most textbooks.
* Trig functions are evaluated in double precision and take the sig figs of
  their argument. Angles are in RADIANS; use deg(x) to convert degrees.
"""

from __future__ import annotations

import ast
import math
from decimal import Decimal, getcontext, ROUND_HALF_UP, InvalidOperation

getcontext().prec = 60

MAX_EXPR_LEN = 300
MAX_EXPONENT = 200


class CalcError(Exception):
    """Raised when an expression is rejected or cannot be evaluated."""


# --------------------------------------------------------------------------
# Quantity: a value plus what we know about its precision
# --------------------------------------------------------------------------

class Q:
    """A number carrying its precision metadata.

    sf   significant figures, or None if the value is exact
    dp   decimal places, or None if the value is exact
    """

    __slots__ = ("val", "sf", "dp", "rule")

    def __init__(self, val: Decimal, sf=None, dp=None, rule=None):
        self.val = val
        self.sf = sf
        self.dp = dp
        self.rule = rule

    @property
    def exact(self) -> bool:
        return self.sf is None and self.dp is None


def _sf_of(val: Decimal, dp: int) -> int:
    """Sig figs of a value once it is known to be precise to `dp` places."""
    if val == 0:
        return 1
    return max(1, val.adjusted() + dp + 1)


def _analyze_literal(text: str):
    """Return (Decimal, sf, dp) for a numeric literal as the user wrote it."""
    s = text.strip().replace("_", "").lower()
    try:
        val = Decimal(s)
    except InvalidOperation:
        raise CalcError(f"not a number: {text}")

    if "." not in s and "e" not in s:
        return val, None, None  # bare integer -> exact

    mant, _, exp_part = s.partition("e")
    exp = int(exp_part) if exp_part else 0
    mant = mant.lstrip("+-")
    if "." in mant:
        int_part, _, frac = mant.partition(".")
    else:
        int_part, frac = mant, ""

    digits = (int_part + frac).lstrip("0")
    sf = len(digits) if digits else 1
    dp = len(frac) - exp
    return val, sf, dp


# --------------------------------------------------------------------------
# Rounding and formatting
# --------------------------------------------------------------------------

def _round_sf(val: Decimal, sf: int) -> Decimal:
    if val == 0:
        return Decimal(0)
    q = Decimal(1).scaleb(val.adjusted() - sf + 1)
    return val.quantize(q, rounding=ROUND_HALF_UP)


def _round_dp(val: Decimal, dp: int) -> Decimal:
    q = Decimal(1).scaleb(-dp)
    return val.quantize(q, rounding=ROUND_HALF_UP)


def _sci(val: Decimal, sf: int) -> str:
    if val == 0:
        return "0"
    e = val.adjusted()
    mant = val.scaleb(-e).quantize(
        Decimal(1).scaleb(-(sf - 1)), rounding=ROUND_HALF_UP
    )
    if abs(mant) >= 10:
        mant = mant.scaleb(-1)
        e += 1
    return f"{mant}e{e}"


def _fmt(q: Q) -> str:
    """Render a result, using scientific notation when plain form would lie."""
    if q.exact:
        v = q.val.normalize()
        if abs(v.adjusted()) > 15:
            return f"{v:.15g}"
        return f"{v:f}"

    if q.dp is not None:
        r = _round_dp(q.val, q.dp)
        if abs(r.adjusted()) > 15:
            return _sci(r, q.sf or 1)
        return f"{r:f}"

    r = _round_sf(q.val, q.sf)
    _, digits, exp = r.as_tuple()
    if exp > 0:
        # e.g. 5E+2 — plain "500" would imply 3 sig figs it does not have
        return _sci(r, q.sf)
    return f"{r:f}"


# --------------------------------------------------------------------------
# Functions and constants
# --------------------------------------------------------------------------

def _dec_from_float(x: float) -> Decimal:
    return Decimal(repr(x))


FUNCS = {
    "sqrt": lambda d: d.sqrt(),
    "abs": abs,
    "ln": lambda d: d.ln(),
    "log": lambda d: d.ln(),
    "log10": lambda d: d.log10(),
    "exp": lambda d: d.exp(),
    "sin": lambda d: _dec_from_float(math.sin(float(d))),
    "cos": lambda d: _dec_from_float(math.cos(float(d))),
    "tan": lambda d: _dec_from_float(math.tan(float(d))),
    "asin": lambda d: _dec_from_float(math.asin(float(d))),
    "acos": lambda d: _dec_from_float(math.acos(float(d))),
    "atan": lambda d: _dec_from_float(math.atan(float(d))),
    "deg": lambda d: d * Decimal(repr(math.pi)) / 180,
    "rad": lambda d: d * 180 / Decimal(repr(math.pi)),
}

CONSTS = {
    "pi": Decimal("3.14159265358979323846264338327950288419716939937510"),
    "e": Decimal("2.71828182845904523536028747135266249775724709369996"),
}

ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Call,
    ast.Name, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.Pow, ast.Mod, ast.USub, ast.UAdd, ast.FloorDiv,
)


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

def _combine_mul(a: Q, b: Q, val: Decimal) -> Q:
    sfs = [q.sf for q in (a, b) if q.sf is not None]
    if not sfs:
        return Q(val, rule="exact")
    return Q(val, sf=min(sfs), rule="mul")


def _combine_add(a: Q, b: Q, val: Decimal) -> Q:
    dps = [q.dp for q in (a, b) if q.dp is not None]
    if not dps:
        return Q(val, rule="exact")
    dp = min(dps)
    return Q(val, sf=_sf_of(_round_dp(val, dp), dp), dp=dp, rule="add")


def _eval(node, src: str) -> Q:
    if not isinstance(node, ALLOWED_NODES):
        raise CalcError(f"not allowed in an expression: {type(node).__name__}")

    if isinstance(node, ast.Expression):
        return _eval(node.body, src)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("only numbers are allowed")
        text = ast.get_source_segment(src, node) or str(node.value)
        val, sf, dp = _analyze_literal(text)
        return Q(val, sf, dp)

    if isinstance(node, ast.Name):
        if node.id not in CONSTS:
            raise CalcError(f"unknown name: {node.id}")
        return Q(CONSTS[node.id])  # constants are exact

    if isinstance(node, ast.UnaryOp):
        q = _eval(node.operand, src)
        val = -q.val if isinstance(node.op, ast.USub) else q.val
        return Q(val, q.sf, q.dp)

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
            raise CalcError("unknown function")
        if node.keywords or len(node.args) != 1:
            raise CalcError("functions take exactly one argument")
        arg = _eval(node.args[0], src)
        try:
            val = FUNCS[node.func.id](arg.val)
        except (InvalidOperation, ValueError, OverflowError) as exc:
            raise CalcError(f"{node.func.id}() is undefined for that input")
        return Q(Decimal(val), arg.sf, rule="func" if arg.sf else "exact")

    # BinOp
    a, b = _eval(node.left, src), _eval(node.right, src)
    op = node.op

    if isinstance(op, ast.Add):
        return _combine_add(a, b, a.val + b.val)
    if isinstance(op, ast.Sub):
        return _combine_add(a, b, a.val - b.val)
    if isinstance(op, ast.Mult):
        return _combine_mul(a, b, a.val * b.val)
    if isinstance(op, (ast.Div, ast.FloorDiv, ast.Mod)):
        if b.val == 0:
            raise CalcError("division by zero")
        if isinstance(op, ast.Div):
            val = a.val / b.val
        elif isinstance(op, ast.FloorDiv):
            val = (a.val // b.val)
        else:
            val = a.val % b.val
        return _combine_mul(a, b, val)
    if isinstance(op, ast.Pow):
        if abs(b.val) > MAX_EXPONENT:
            raise CalcError("exponent too large")
        if b.val == b.val.to_integral_value():
            val = a.val ** int(b.val)
        else:
            if a.val <= 0:
                raise CalcError("fractional power of a non-positive number")
            val = (b.val * a.val.ln()).exp()
        return Q(val, a.sf, None, rule="mul" if a.sf else "exact")

    raise CalcError("unsupported operator")


RULE = {
    "mul": "multiplication/division -> fewest significant figures",
    "add": "addition/subtraction -> fewest decimal places",
    "exact": "all inputs exact -> result is exact",
}


def safe_calc(expression: str) -> str:
    """Evaluate one expression and return a verified, formatted report."""
    expr = expression.strip()
    if not expr:
        raise CalcError("empty expression")
    if len(expr) > MAX_EXPR_LEN:
        raise CalcError("expression too long")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"syntax error: {exc.msg}")

    q = _eval(tree, expr)

    lines = [f"expression : {expr}"]
    if abs(q.val.adjusted()) > 15:
        exact_str = f"{q.val:.15g}"
    else:
        exact_str = f"{q.val.normalize():f}"
        if len(exact_str.replace("-", "").replace(".", "")) > 20:
            exact_str = f"{q.val:.15g}"
    lines.append(f"exact      : {exact_str}")
    lines.append(f"result     : {_fmt(q)}")
    if q.rule == "add":
        lines.append(f"rule       : {RULE['add']} ({q.dp} dp, {q.sf} sf)")
    elif q.rule in ("mul", "func"):
        lines.append(f"rule       : {RULE['mul']} ({q.sf} sf)")
    elif q.exact:
        lines.append(f"rule       : {RULE['exact']}")
    else:
        lines.append(f"rule       : single value as written ({q.sf} sf)")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Structured entry point — this is what tools.safe_calc delegates to
# --------------------------------------------------------------------------

def calc(expression: str) -> dict:
    """Evaluate one expression. Never raises.

    Returns a dict with: expr, result, exact, rule, error.
    `result` is the sig-fig-correct value as a string; `error` is None on
    success and a message on failure (in which case result is None).
    """
    expr = (expression or "").strip()
    try:
        if not expr:
            raise CalcError("empty expression")
        if len(expr) > MAX_EXPR_LEN:
            raise CalcError("expression too long")
        tree = ast.parse(expr, mode="eval")
        q = _eval(tree, expr)
    except CalcError as exc:
        return {"expr": expr, "result": None, "exact": None,
                "rule": None, "error": str(exc)}
    except SyntaxError as exc:
        return {"expr": expr, "result": None, "exact": None,
                "rule": None, "error": f"syntax error: {exc.msg}"}
    except Exception as exc:  # never let the calculator kill the answer
        return {"expr": expr, "result": None, "exact": None,
                "rule": None, "error": f"{type(exc).__name__}: {exc}"}

    if abs(q.val.adjusted()) > 15:
        exact_str = f"{q.val:.15g}"
    else:
        exact_str = f"{q.val.normalize():f}"
        if len(exact_str.replace("-", "").replace(".", "")) > 20:
            exact_str = f"{q.val:.15g}"

    if q.rule == "add":
        rule = f"{RULE['add']} ({q.dp} dp, {q.sf} sf)"
    elif q.rule in ("mul", "func"):
        rule = f"{RULE['mul']} ({q.sf} sf)"
    elif q.exact:
        rule = RULE["exact"]
    else:
        rule = f"single value as written ({q.sf} sf)"

    return {"expr": expr, "result": _fmt(q), "exact": exact_str,
            "rule": rule, "error": None}


# --------------------------------------------------------------------------
# Block extraction — same shape as extract_python_blocks in tools.py
# --------------------------------------------------------------------------

def extract_calc_blocks(text: str):
    """Return the list of expressions inside ```calc fenced blocks."""
    exprs = []
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if not inside and stripped.lower().startswith("```calc"):
            inside = True
            continue
        if inside:
            if stripped.startswith("```"):
                inside = False
                continue
            if stripped and not stripped.startswith("#"):
                exprs.append(stripped)
    return exprs


def run_calc_blocks(text: str) -> str:
    """Evaluate every ```calc block in `text`. Returns '' if there are none."""
    exprs = extract_calc_blocks(text)
    if not exprs:
        return ""
    out = []
    for expr in exprs:
        try:
            out.append(safe_calc(expr))
        except CalcError as exc:
            out.append(f"expression : {expr}\nerror      : {exc}")
    return "\n\n".join(out)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        try:
            print(safe_calc(" ".join(sys.argv[1:])))
        except CalcError as exc:
            print(f"error: {exc}")
    else:
        print("usage: python safe_calc.py '4.52 * 3.1'")
