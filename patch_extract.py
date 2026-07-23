#!/usr/bin/env python3
"""
patch_extract.py — teach extract_expressions to see arithmetic in prose.

Run once, from ~/modular-ai-core:

    python patch_extract.py

The problem it fixes: extract_expressions only looked inside backticks or
after 'Expression:'. Llama writes plain prose -- "4.52 * 3.1 = 14.012" --
so nothing matched and the calculator never ran.

The fix: a third pattern that captures arithmetic sitting immediately to
the left of an '=' sign. That is where a model states a computed claim,
which is exactly the claim worth checking.

This script prints the BEFORE result, patches, then prints the AFTER result
on the same sentence, so you can see it work rather than take my word.
Backup goes to tools.py.bak2.
"""

import importlib
import os
import re
import shutil
import sys

TARGET = "tools.py"
BACKUP = "tools.py.bak2"

SAMPLE = ("4.52 * 3.1 = 14.012. Since 3.1 has 2 significant figures, "
          "the result should be rounded. So, 4.52 * 3.1 = 14.")

NEW_EXTRACT = '''def extract_expressions(text):
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
    charset = r"[0-9eE()._+\\-*/%\\t ]"

    candidates = re.findall(r"`([^`]+)`", text)
    candidates += re.findall(r"[Ee]xpression:?[\\t ]*(" + charset + r"+)", text)
    candidates += re.findall(r"([0-9(]" + charset + r"*?)[\\t ]*=", text)

    exprs, seen = [], set()
    for c in candidates:
        c = c.strip().rstrip(".")
        if len(c) > 120:
            continue
        if (re.fullmatch(charset + r"+", c)
                and re.search(r"\\d", c) and re.search(r"[+\\-*/%]", c)
                and c not in seen):
            seen.add(c)
            exprs.append(c)
    return exprs
'''


def replace_function(src, name, new_body):
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

    sys.path.insert(0, os.getcwd())
    import tools

    print("SAMPLE:")
    print(f"  {SAMPLE}\n")
    print(f"BEFORE: {tools.extract_expressions(SAMPLE)}")

    src = open(TARGET, encoding="utf-8").read()
    shutil.copyfile(TARGET, BACKUP)

    out = replace_function(src, "extract_expressions", NEW_EXTRACT)
    if out is None:
        sys.exit("error: extract_expressions not found -- nothing changed")
    open(TARGET, "w", encoding="utf-8").write(out)

    import ast as _ast
    try:
        _ast.parse(out)
    except SyntaxError as exc:
        print(f"SYNTAX ERROR line {exc.lineno}: {exc.msg}")
        print(f"restore with:  cp {BACKUP} {TARGET}")
        sys.exit(1)

    importlib.reload(tools)
    found = tools.extract_expressions(SAMPLE)
    print(f"AFTER : {found}\n")

    if not found:
        print("still empty -- send me this output, do not run the core")
        sys.exit(1)

    results = [(e, tools.safe_calc(e)) for e in found]
    print(tools.format_calc(results))
    print(f"\nbackup: {BACKUP}")


if __name__ == "__main__":
    main()
