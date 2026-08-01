#!/usr/bin/env python3
"""
patch_multipack_retrieval_v1.py

The router already returns a ranked list of packs in r["domains"], but
retrieval only ever used the first one:

    _pid = r.get("domain") or (r.get("domains") or [None])[0]
    _hits = _rt.retrieve(question, _pid, k=3)

For a question spanning several packs, every chunk outside the top-ranked pack
was unreachable. This patch retrieves from every routed pack, merges the
results, and keeps the globally highest-scoring chunks.

Scores are cosine similarities from the same Jina model across all packs, so
they are directly comparable and a global sort is meaningful.

Tunables at the top of the patched block:
    PER_PACK  chunks pulled from each pack before merging (default 4)
    TOTAL     chunks kept after merging (default 6)

Token cost: TOTAL=6 roughly doubles injected context versus k=3. On the free
Groq tier that may push a long question past the tokens-per-minute ceiling and
return 429. Lower TOTAL to 4 if that happens.

The retrieval status becomes "ok:<kept>/<packs>" so the response shows how many
chunks came back and how many packs were consulted. "empty", "nopack",
"unrouted", "unset" and "error" are unchanged.

Safety:
  - both anchors must match EXACTLY once, or abort touching nothing
  - refuses to run if _pids is already present
  - writes server.py.bak4 before any change
  - re-parses with ast and restores the backup if it does not compile

Usage:
    python patch_multipack_retrieval_v1.py
    python patch_multipack_retrieval_v1.py /path/to/server.py
"""

import ast
import os
import re
import shutil
import sys

DEFAULT_PATH = os.path.expanduser("~/pqc-assistant/server.py")

A_PAT = (
    r'(?m)^([ \t]*)_pid = r\.get\("domain"\) or \(r\.get\("domains"\) or \[None\]\)\[0\]\n'
    r'[ \t]*_hits = _rt\.retrieve\(question, _pid, k=3\) if _pid else \[\]'
)
B_PAT = r'(?m)^([ \t]*)_retrieval = "empty" if _pid else "nopack"'


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

    if "_pids" in src:
        die("`_pids` already present -- looks already patched.")

    a = match_once(A_PAT, src, "_pid / _hits single-pack retrieval")
    match_once(B_PAT, src, '_retrieval = "empty" if _pid else "nopack"')

    ind = a.group(1)

    def build_a():
        i = ind
        return "\n".join([
            i + "_PER_PACK = 4",
            i + "_TOTAL = 6",
            i + '_pids = r.get("domains") or ([r["domain"]] if r.get("domain") else [])',
            i + "_pool = []",
            i + "for _p in _pids:",
            i + "    try:",
            i + "        for _s, _t in _rt.retrieve(question, _p, k=_PER_PACK):",
            i + "            _pool.append((_s, _t))",
            i + "    except Exception as _pe:",
            i + '        print("retrieval failed for", _p, ":", _pe)',
            i + "_seen = set()",
            i + "_hits = []",
            i + "for _s, _t in sorted(_pool, key=lambda x: -x[0]):",
            i + "    if _t in _seen:",
            i + "        continue",
            i + "    _seen.add(_t)",
            i + "    _hits.append((_s, _t))",
            i + "    if len(_hits) >= _TOTAL:",
            i + "        break",
        ])

    out = src[: a.start()] + build_a() + src[a.end():]

    b = match_once(B_PAT, out, '_retrieval = "empty" if _pid else "nopack"')
    out = (out[: b.start()]
           + b.group(1) + '_retrieval = "empty" if _pids else "nopack"'
           + out[b.end():])

    # status now reports how many chunks and how many packs
    old_ok = '_retrieval = "ok"'
    if out.count(old_ok) != 1:
        die('expected exactly one `_retrieval = "ok"`, found %d. '
            "Nothing was written." % out.count(old_ok))
    out = out.replace(
        old_ok, '_retrieval = "ok:%d/%d" % (len(_hits), len(_pids))'
    )

    bak = path + ".bak4"
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

    lines = out.splitlines()
    hits = [n for n, ln in enumerate(lines)
            if "_pids" in ln or "_retrieval" in ln or "_pool" in ln]
    if hits:
        lo, hi = max(0, hits[0] - 3), min(len(lines), hits[-1] + 3)
        print("---- patched region ----")
        for n in range(lo, hi):
            print("%4d  %s" % (n + 1, lines[n]))


if __name__ == "__main__":
    main()
