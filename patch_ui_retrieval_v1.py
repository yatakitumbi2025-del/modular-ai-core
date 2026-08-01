#!/usr/bin/env python3
"""
patch_ui_retrieval_v1.py

The route header in ui.html renders only the first routed pack:

    route.appendChild(el('span', null, (r.domains || ['general'])[0]));

Since retrieval now gathers from every routed pack, showing one name is
misleading — it was the reason a multi-pack answer looked single-pack. This
patch joins all pack names, and appends the retrieval status so the
"ok:<chunks>/<packs>" field is visible in the browser instead of only in the
JSON.

After patching the header reads, for example:

    PQC_CORE + PQC_APPLICATION + PQC_IMPLEMENTATION   ok:6/3

Pack names are shortened by dropping the "source__" prefix, since the source
and pack names are usually the same and the doubled form is hard to read on a
phone.

Safety:
  - both anchors must match EXACTLY once, or abort touching nothing
  - refuses to run if the retrieval span is already present
  - writes ui.html.bak before any change
  - checks the result has balanced parens on the edited lines

Usage:
    python patch_ui_retrieval_v1.py
    python patch_ui_retrieval_v1.py /path/to/ui.html
"""

import os
import re
import shutil
import sys

DEFAULT_PATH = os.path.expanduser("~/pqc-assistant/ui.html")

A_PAT = (
    r"(?m)^([ \t]*)route\.appendChild\(el\('span', null, "
    r"\(r\.domains \|\| \['general'\]\)\[0\]\)\);"
)
B_PAT = r"(?m)^([ \t]*)t2\.appendChild\(route\);"


def die(msg):
    print("ABORT:", msg)
    sys.exit(1)


def match_once(pattern, text, label):
    ms = list(re.finditer(pattern, text))
    if len(ms) != 1:
        die("anchor %r matched %d times (expected exactly 1). "
            "Nothing was written." % (label, len(ms)))
    return ms[0]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    if not os.path.isfile(path):
        die("no such file: %s" % path)

    with open(path, "r", encoding="utf-8") as f:
        src = f.read()

    if "r.retrieval" in src:
        die("`r.retrieval` already present -- looks already patched.")

    a = match_once(A_PAT, src, "route.appendChild single-domain span")
    match_once(B_PAT, src, "t2.appendChild(route);")

    ind = a.group(1)

    new_a = "\n".join([
        ind + "route.appendChild(el('span', null,",
        ind + "  (r.domains || ['general'])",
        ind + "    .map(d => d.split('__').pop())",
        ind + "    .join(' + ')));",
    ])
    out = src[: a.start()] + new_a + src[a.end():]

    b = match_once(B_PAT, out, "t2.appendChild(route);")
    new_b = "\n".join([
        b.group(1) + "if(r.retrieval) route.appendChild("
        "el('span','rule'));",
        b.group(1) + "if(r.retrieval) route.appendChild("
        "el('span','tools', r.retrieval));",
        b.group(1) + "t2.appendChild(route);",
    ])
    out = out[: b.start()] + new_b + out[b.end():]

    bak = path + ".bak"
    shutil.copy2(path, bak)
    with open(path, "w", encoding="utf-8") as f:
        f.write(out)

    print("OK  backup: %s" % bak)

    lines = out.splitlines()
    idx = [n for n, ln in enumerate(lines)
           if "r.domains" in ln or "r.retrieval" in ln]
    if idx:
        lo, hi = max(0, idx[0] - 3), min(len(lines), idx[-1] + 3)
        print("\n---- patched region ----")
        for n in range(lo, hi):
            print("%4d  %s" % (n + 1, lines[n]))
            if lines[n].count("(") != lines[n].count(")") and ";" in lines[n]:
                print("      NOTE: unbalanced parens on a line ending in ';'")


if __name__ == "__main__":
    main()
