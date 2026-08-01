#!/usr/bin/env python3
"""
patch_retrieval_scope_v2.py

Follow-up to patch_retrieval_framing_v1.py.

v1 inserted `_retrieval = "none"` inside the `else:` branch only. The `if`
branch (the general / unrouted path, which sets `domains, tool_names =
["general"], []`) never binds it, so the shared `return` raises NameError on
that path.

This binds `_retrieval = "none"` in the general branch too.

Safety:
  - anchor must match EXACTLY once, or abort touching nothing
  - refuses to run if the general branch already binds _retrieval
  - writes server.py.bak2 before any change
  - re-parses with ast and restores the backup if it does not compile
  - prints both branches at the end

Usage:
    python patch_retrieval_scope_v2.py
    python patch_retrieval_scope_v2.py /path/to/server.py
"""

import ast
import os
import re
import shutil
import sys

DEFAULT_PATH = os.path.expanduser("~/pqc-assistant/server.py")
ANCHOR = r'(?m)^([ \t]*)domains, tool_names = \["general"\], \[\]'


def die(msg):
    print("ABORT:", msg)
    sys.exit(1)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    if not os.path.isfile(path):
        die("no such file: %s" % path)

    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if "_retrieval" not in src:
        die("no `_retrieval` found -- run patch_retrieval_framing_v1.py first.")

    matches = list(re.finditer(ANCHOR, src))
    if len(matches) != 1:
        die(
            'anchor `domains, tool_names = ["general"], []` matched %d times '
            "(expected exactly 1). Nothing was written." % len(matches)
        )
    m = matches[0]
    ind = m.group(1)

    # already bound on this branch?
    tail = src[m.end() : m.end() + 200]
    if re.match(r'\n[ \t]*_retrieval = ', tail):
        die("general branch already binds _retrieval -- looks already patched.")

    new = (
        ind + 'domains, tool_names = ["general"], []\n'
        + ind + '_retrieval = "none"'
    )
    out = src[: m.start()] + new + src[m.end() :]

    bak = path + ".bak2"
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
    print("---- both branches ----")
    lines = out.splitlines()
    hits = [n for n, ln in enumerate(lines) if "_retrieval" in ln]
    if hits:
        lo, hi = max(0, hits[0] - 4), min(len(lines), hits[0] + 10)
        for n in range(lo, hi):
            print("%4d  %s" % (n + 1, lines[n]))
    print("\n_retrieval bound/read on lines: %s" % [n + 1 for n in hits])


if __name__ == "__main__":
    main()
