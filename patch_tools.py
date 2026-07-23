#!/usr/bin/env python3
"""
patch_tools.py — swap the calculator engine in tools.py.

Run once, from ~/modular-ai-core:

    python patch_tools.py

What it changes (nothing else is touched):
  * adds `import sigcalc` if missing
  * safe_calc()          -> delegates to sigcalc, returns a dict, never raises
  * extract_expressions() -> allows `e` so 1.00e2 can reach the calculator
  * format_calc()        -> renders the dict, shows the rule when it matters

A backup is written to tools.py.bak before anything is modified.
Functions are located by their `def name(` line and replaced up to the next
top-level `def`, so this does not depend on exact whitespace inside them.
"""

import os
import re
import shutil
import sys

TARGET = "tools.py"
BACKUP = "tools.py.bak"

NEW_SAFE_CALC = '''def safe_calc(expr):
    """Evaluate one arithmetic expression with significant-figure tracking.

    Delegates to sigcalc, which parses with ast against an allowlist (no
    eval) and computes in Decimal. Returns a dict:
        {"expr", "result", "exact", "rule", "error"}
    `result` is the sig-fig-correct value as a string, or None on error.
    This function never raises -- a bad expression must not kill the answer.
    """
    return sigcalc.calc(expr)
'''

NEW_EXTRACT = '''def extract_expressions(text):
    """Pull arithmetic expressions the model wrote (in backticks or after
    'Expression:').

    `e` and `E` are permitted so scientific notation (1.00e2) survives the
    filter -- that is how a measured value declares its significant figures.
    A candidate still has to be purely arithmetic and contain both a digit
    and an operator, so prose cannot leak through.
    """
    charset = r"[0-9eE()._+\\-*/%\\s]"
    candidates = re.findall(r"`([^`]+)`", text)
    candidates += re.findall(r"[Ee]xpression:?\\s*(" + charset + r"+)", text)

    exprs, seen = [], set()
    for c in candidates:
        c = c.strip().rstrip(".")
        if (re.fullmatch(charset + r"+", c)
                and re.search(r"\\d", c) and re.search(r"[+\\-*/%]", c)
                and c not in seen):
            seen.add(c)
            exprs.append(c)
    return exprs
'''

NEW_FORMAT = '''def format_calc(results):
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
    return "\\n".join(lines)
'''

REPLACEMENTS = [
    ("safe_calc", NEW_SAFE_CALC),
    ("extract_expressions", NEW_EXTRACT),
    ("format_calc", NEW_FORMAT),
]


def replace_function(src, name, new_body):
    """Replace `def name(...)` through to the next top-level def/class/EOF."""
    pattern = re.compile(
        r"^def " + re.escape(name) + r"\(.*?(?=^def |^class |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    if not pattern.search(src):
        return None
    return pattern.sub(lambda m: new_body.rstrip() + "\n\n\n", src, count=1)


def main():
    if not os.path.exists(TARGET):
        sys.exit(f"error: {TARGET} not found. Run this from ~/modular-ai-core")
    if not os.path.exists("sigcalc.py"):
        sys.exit("error: sigcalc.py not found. Copy it in first.")

    src = open(TARGET, encoding="utf-8").read()
    shutil.copyfile(TARGET, BACKUP)
    print(f"backup written to {BACKUP}")

    if not re.search(r"^import sigcalc\b", src, re.MULTILINE):
        lines = src.split("\n")
        last_import = 0
        for i, line in enumerate(lines[:40]):
            if line.startswith(("import ", "from ")):
                last_import = i
        lines.insert(last_import + 1, "import sigcalc")
        src = "\n".join(lines)
        print("added: import sigcalc")
    else:
        print("skipped: import sigcalc already present")

    for name, body in REPLACEMENTS:
        out = replace_function(src, name, body)
        if out is None:
            print(f"NOT FOUND: {name}  -- left unchanged, check manually")
        else:
            src = out
            print(f"replaced: {name}")

    open(TARGET, "w", encoding="utf-8").write(src)

    import ast as _ast
    try:
        _ast.parse(src)
        print("\nsyntax check: OK")
    except SyntaxError as exc:
        print(f"\nSYNTAX ERROR at line {exc.lineno}: {exc.msg}")
        print(f"restore with:  cp {BACKUP} {TARGET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
