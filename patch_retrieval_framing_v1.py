#!/usr/bin/env python3
"""
patch_retrieval_framing_v1.py

Fixes two issues in pqc-assistant/server.py:

  1. Broken string concatenation in the retrieval-injection system prompt.
     The joined text currently reads:
        "...Treat it as your own knowledge and knowledge. If it does not cover..."
     A fragment was lost in an earlier edit. Replaced with a coherent sentence.

  2. Silent retrieval degradation. `except Exception` only printed, so a failed
     retrieval produced an un-augmented answer that was indistinguishable from a
     legitimate no-hits answer. Adds a `_retrieval` status ("none" / "ok" /
     "error") threaded into the returned dict as "retrieval".

Safety:
  - every anchor must match EXACTLY once, or the script aborts touching nothing
  - refuses to run twice (detects `_retrieval` already present)
  - writes server.py.bak before any change
  - re-parses the result with ast and restores the .bak if it does not compile
  - prints the patched region at the end for eyeballing

Usage:
    python patch_retrieval_framing_v1.py
    python patch_retrieval_framing_v1.py /path/to/server.py
"""

import ast
import os
import re
import shutil
import sys

DEFAULT_PATH = os.path.expanduser("~/pqc-assistant/server.py")


def die(msg):
    print("ABORT:", msg)
    sys.exit(1)


def match_once(pattern, text, label, flags=0):
    """Return the single match for `pattern`, or abort."""
    matches = list(re.finditer(pattern, text, flags))
    if len(matches) != 1:
        die(
            "anchor %r matched %d times (expected exactly 1). "
            "Nothing was written." % (label, len(matches))
        )
    return matches[0]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    if not os.path.isfile(path):
        die("no such file: %s" % path)

    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if "_retrieval" in src:
        die("`_retrieval` already present -- server.py looks already patched.")

    # ------------------------------------------------------------------
    # Anchor A: the whole `system = ( "Background knowledge ... + system)`
    # expression. DOTALL so it spans the implicit string concatenation.
    # ------------------------------------------------------------------
    A_PAT = r'(?m)^([ \t]*)system = \("Background knowledge you have\..*?\+ system\)'
    a = match_once(A_PAT, src, "system = (\"Background knowledge...", re.DOTALL)
    ind_a = a.group(1)

    def build_a():
        i = ind_a
        return (
            i + "system = (\n"
            + i + '    "Background knowledge you have. Treat it as your own knowledge and "\n'
            + i + '    "answer directly from it. If it does not cover the question, say so "\n'
            + i + '    "plainly rather than inventing API names, flags, or version numbers. "\n'
            + i + '    "Never mention packs, modules, sources, or where this information "\n'
            + i + '    "came from. Do not append notes labelled by pack name.\\n\\n"\n'
            + i + '    + _ref + "\\n\\n---\\n\\n" + system\n'
            + i + ")\n"
            + i + '_retrieval = "ok"'
        )

    # ------------------------------------------------------------------
    # Anchor B: `try:` immediately followed by `import retrieve as _rt`.
    # Initialise the status before the try so it is always bound.
    # ------------------------------------------------------------------
    B_PAT = r'(?m)^([ \t]*)try:\n([ \t]*)import retrieve as _rt'
    b = match_once(B_PAT, src, "try: / import retrieve as _rt")

    def build_b():
        outer, inner = b.group(1), b.group(2)
        return (
            outer + '_retrieval = "none"\n'
            + outer + "try:\n"
            + inner + "import retrieve as _rt"
        )

    # ------------------------------------------------------------------
    # Anchor C: the except-branch print.
    # ------------------------------------------------------------------
    C_PAT = r'(?m)^([ \t]*)print\("retrieval skipped:", _e\)'
    c = match_once(C_PAT, src, 'print("retrieval skipped:", _e)')

    def build_c():
        i = c.group(1)
        return (
            i + 'print("retrieval skipped:", _e)\n'
            + i + '_retrieval = "error"'
        )

    # ------------------------------------------------------------------
    # Anchor D: the return dict.
    # ------------------------------------------------------------------
    D_PAT = r'"blocks": blocks\}'
    match_once(D_PAT, src, '"blocks": blocks}')

    # ---- all anchors validated; apply -------------------------------
    out = src
    out = out[: a.start()] + build_a() + out[a.end() :]
    # re-locate B, C, D against the mutated text (offsets have shifted)
    b = match_once(B_PAT, out, "try: / import retrieve as _rt")
    out = out[: b.start()] + build_b() + out[b.end() :]
    c = match_once(C_PAT, out, 'print("retrieval skipped:", _e)')
    out = out[: c.start()] + build_c() + out[c.end() :]
    out = out.replace('"blocks": blocks}', '"blocks": blocks, "retrieval": _retrieval}')

    bak = path + ".bak"
    shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)

    try:
        ast.parse(out)
    except SyntaxError as e:
        shutil.copy2(bak, path)
        die("patched file failed to parse (%s). Restored from %s" % (e, bak))

    print("OK  backup: %s" % bak)
    print("OK  server.py parses cleanly\n")
    print("---- patched region ----")
    lines = out.splitlines()
    hit = next(
        (n for n, ln in enumerate(lines) if "_retrieval" in ln), None
    )
    if hit is not None:
        lo, hi = max(0, hit - 4), min(len(lines), hit + 26)
        for n in range(lo, hi):
            print("%4d  %s" % (n + 1, lines[n]))


if __name__ == "__main__":
    main()
