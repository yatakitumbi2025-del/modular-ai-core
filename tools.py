#!/usr/bin/env python3
"""
tools.py — the code_runner tool.

Extracts Python from the model's answer and runs it, so the system can
VERIFY code instead of trusting the model's claim that it works.

SAFETY: this executes code the model generated. Three guards are built in:
  1. It runs in a separate process (a crash can't take down your session).
  2. A hard timeout stops infinite loops / hangs.
  3. It runs inside a throwaway temp directory (cwd), so files it writes
     land there and vanish, not in your home folder.
This is NOT a true security sandbox. Review code before running anything
you don't understand — core.py asks you to confirm before each run.

Standard library only.
"""

import os
import re
import subprocess
import tempfile


def extract_python_blocks(text):
    """Pull ```python ... ``` (or plain ``` ... ```) fenced code from text."""
    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.DOTALL)
    return [b.strip() for b in blocks if b.strip()]


def run_python(source, timeout=10):
    """Run Python source in a subprocess. Returns stdout, stderr, exit_code."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "snippet.py")
        with open(path, "w") as f:
            f.write(source)
        try:
            proc = subprocess.run(
                ["python", path],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tmp,
            )
            return {
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "exit_code": proc.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": f"Timed out after {timeout}s", "exit_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": f"Could not run: {e}", "exit_code": -1}


def format_result(res):
    """Pretty one-block summary of an execution result."""
    verdict = "OK ✓" if res["exit_code"] == 0 else "FAILED ✗"
    lines = [f"[code_runner] {verdict}  (exit {res['exit_code']})"]
    if res["stdout"].strip():
        lines.append("stdout:")
        lines += ["  " + l for l in res["stdout"].rstrip().splitlines()]
    if res["stderr"].strip():
        lines.append("stderr:")
        lines += ["  " + l for l in res["stderr"].rstrip().splitlines()]
    return "\n".join(lines)


# ======================================================================
# calculator tool — safe arithmetic evaluation (for the math pack)
# ======================================================================
import ast
import operator

_BINOPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow,
    ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def safe_calc(expr):
    """Evaluate a pure arithmetic expression WITHOUT eval().

    Only numbers, + - * / ** % //, parentheses, and unary +/- are allowed.
    Anything else (names, function calls, attribute access) is rejected,
    so a malicious string can't do harm here.
    """
    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
            return _UNARY[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    return _eval(ast.parse(expr, mode="eval"))


def extract_expressions(text):
    """Pull arithmetic expressions the model wrote (in `backticks` or after 'Expression:')."""
    candidates = re.findall(r"`([^`]+)`", text)
    candidates += re.findall(r"[Ee]xpression:?\s*([0-9().+\-*/%\s]+)", text)

    exprs, seen = [], set()
    for c in candidates:
        c = c.strip().rstrip(".")
        # must be purely arithmetic, contain a digit AND an operator
        if (re.fullmatch(r"[0-9().+\-*/%\s]+", c)
                and re.search(r"\d", c) and re.search(r"[+\-*/%]", c)
                and c not in seen):
            seen.add(c)
            exprs.append(c)
    return exprs


def format_calc(results):
    lines = ["[calculator]"]
    for expr, val in results:
        lines.append(f"  {expr} = {val}")
    return "\n".join(lines)
