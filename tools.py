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
import sigcalc


def extract_python_blocks(text):
    """Pull fenced code from text, keeping only real Python.

    1. A block is kept only if it parses. That drops pasted program OUTPUT
       (timing tables, tracebacks) that used to run and raise
       "invalid decimal literal".
    2. Surviving blocks are joined into ONE script, so an import in an early
       block is still in scope later. Previously each ran alone -> NameError.
    """
    raw = re.findall(r"```([\w+-]*)[ \t]*\n(.*?)```", text, re.DOTALL)
    good = []
    for lang, body in raw:
        if lang.lower() not in ("python", "py", "python3", ""):
            continue
        body = body.strip()
        if not body:
            continue
        try:
            ast.parse(body)
        except SyntaxError:
            continue
        good.append(body)

    if not good:
        return []

    script = "\n\n".join(good)
    try:
        ast.parse(script)
    except SyntaxError:
        return [good[-1]]
    return [script]


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
    """Evaluate one arithmetic expression with significant-figure tracking.

    Delegates to sigcalc, which parses with ast against an allowlist (no
    eval) and computes in Decimal. Returns a dict:
        {"expr", "result", "exact", "rule", "error"}
    `result` is the sig-fig-correct value as a string, or None on error.
    This function never raises -- a bad expression must not kill the answer.
    """
    return sigcalc.calc(expr)


def extract_expressions(text):
    """Pull arithmetic expressions the model wrote.

    Three sources, in order of reliability:
      1. inside backticks
      2. after an 'Expression:' label
      3. immediately to the left of an '=' sign, anywhere in prose

    Pattern 3 is the one that matters in practice. A model stating a
    computed claim writes "4.52 * 3.1 = 14.012" in plain text; it does not
    reliably use backticks, and asking it to in the prompt is a request it
    can ignore. Reading what it actually wrote is not.

    `e`/`E` are permitted so scientific notation (1.00e2) survives -- that
    is how a measured value declares its significant figures. Newlines are
    excluded from the charset so a match cannot run across lines.
    """
    charset = r"[0-9eE()._+\-*/%\t ]"

    candidates = re.findall(r"`([^`]+)`", text)
    candidates += re.findall(r"[Ee]xpression:?[\t ]*(" + charset + r"+)", text)
    candidates += re.findall(r"([0-9(]" + charset + r"*?)[\t ]*=", text)

    exprs, seen = [], set()
    for c in candidates:
        c = c.strip().rstrip(".")
        if len(c) > 120:
            continue
        if (re.fullmatch(charset + r"+", c)
                and re.search(r"\d", c) and re.search(r"[+\-*/%]", c)
                and c not in seen):
            seen.add(c)
            exprs.append(c)
    return exprs


def format_calc(results):
    """Render calculator output.

    Accepts the dicts returned by safe_calc, and still tolerates a bare
    number in case something older calls this.
    """
    lines = ["[calculator]"]
    for expr, val in results:
        if isinstance(val, dict):
            if val.get("error"):
                lines.append(f"  {expr} -> could not evaluate: {val['error']}")
                continue
            lines.append(f"  {expr} = {val['result']}")
            if val.get("exact") not in (None, val.get("result")):
                lines.append(f"      unrounded {val['exact']}  ({val['rule']})")
        else:
            lines.append(f"  {expr} = {val}")
    return "\n".join(lines)


