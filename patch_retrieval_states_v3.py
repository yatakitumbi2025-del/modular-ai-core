#!/usr/bin/env python3
"""
patch_retrieval_states_v3.py

Follow-up to patch_retrieval_framing_v1.py and patch_retrieval_scope_v2.py.

After v2, `_retrieval == "none"` collapsed three unrelated states:
  - the router matched nothing at all (r is None)
  - a pack matched but carried no domain id, so retrieve() was never called
  - retrieve() ran and returned zero chunks

Only the third points at chunk/vector quality, so they need distinct labels.
Final state set:

  "unrouted"  router returned nothing; general system prompt used
  "nopack"    routed, but no _pid -- retrieve() never called
  "empty"     retrieve() ran, zero chunks came back   <-- vector staleness signal
  "ok"        chunks retrieved and injected
  "error"     retrieval raised; see the "retrieval skipped:" log line

Safety:
  - both anchors must match EXACTLY once, or abort touching nothing
  - refuses to run if "unrouted" is already present
  - verifies the `if _hits:` body is indented under the `if` before editing
  - writes server.py.bak3 before any change
  - re-parses with ast and restores the backup if it does not compile

Usage:
    python patch_retrieval_states_v3.py
    python patch_retrieval_states_v3.py /path/to/server.py
"""

import ast
import os
import re
import shutil
import sys

DEFAULT_PATH = os.path.expanduser("~/pqc-assistant/server.py")

# general branch: the "none" that directly follows the ["general"] assignment
A_PAT = r'(?m)^([ \t]*)domains, tool_names = \["general"\], \[\]\n([ \t]*)_retrieval = "none"'
# the if _hits: header, and the "ok" assignment that closes its body
B_PAT = r'(?m)^([ \t]*)if _hits:'
C_PAT = r'(?m)^([ \t]*)_retrieval = "ok"'


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

    if '"unrouted"' in src:
        die('"unrouted" already present -- looks already patched.')
    if '_retrieval = "ok"' not in src:
        die("no `_retrieval` scaffolding found -- run v1 and v2 first.")

    a = match_once(A_PAT, src, 'general branch _retrieval = "none"')
    b = match_once(B_PAT, src, "if _hits:")
    c = match_once(C_PAT, src, '_retrieval = "ok"')

    if_ind, body_ind = b.group(1), c.group(1)
    if not (body_ind.startswith(if_ind) and len(body_ind) > len(if_ind)):
        die("`_retrieval = \"ok\"` (indent %d) is not nested under `if _hits:` "
            "(indent %d). Nothing was written."
            % (len(body_ind), len(if_ind)))

    # ---- edit A: general branch -> "unrouted" ----
    new_a = (
        a.group(1) + 'domains, tool_names = ["general"], []\n'
        + a.group(2) + '_retrieval = "unrouted"'
    )
    out = src[: a.start()] + new_a + src[a.end() :]

    # ---- edit B: add the else arm to `if _hits:` ----
    c = match_once(C_PAT, out, '_retrieval = "ok"')
    new_c = (
        body_ind + '_retrieval = "ok"\n'
        + if_ind + 'else:\n'
        + body_ind + '_retrieval = "empty" if _pid else "nopack"'
    )
    out = out[: c.start()] + new_c + out[c.end() :]

    bak = path + ".bak3"
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
    hits = [n for n, ln in enumerate(lines) if "_retrieval" in ln]
    print("---- all _retrieval sites ----")
    for n in hits:
        print("%4d  %s" % (n + 1, lines[n]))
    leftover = [n + 1 for n in hits if '"none"' in lines[n]]
    if leftover:
        print("\nWARNING: `\"none\"` still assigned on lines %s -- unreachable "
              "state, worth a look." % leftover)


if __name__ == "__main__":
    main()
