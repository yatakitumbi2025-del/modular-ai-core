#!/usr/bin/env python3
"""
patch_surface_degraded_v1.py -- make stale-cache degradation visible to the caller

Problem: loader.py's load_pack wrapper sets data["degraded"] = "stale-cache" when a
fetch fails and it falls back to an expired cache entry. Nothing ever reads that key.
build_context() drops it, so server.ask() cannot report it, so the web UI presents a
stale-pack answer identically to a fresh-pack answer.

This does NOT change any behaviour of the fallback itself -- that already works.
It only makes the existing signal reach the response payload.

Changes:
  1. loader.py   -- build_context() collects the ids of any degraded packs
                    (primary and/or secondary) and includes them in its return dict.
  2. server.py   -- ask() appends "|stale:<ids>" to the existing _retrieval string.

Why a list and not a bool: secondary packs load independently of the primary, so one
can be stale while the other is fresh. Knowing WHICH pack is stale is the useful part.

Why append rather than overwrite _retrieval: the existing states (ok:N/M, empty,
nopack, error, unrouted) all stay intact and readable. "ok:4/6" becomes
"ok:4/6|stale:pqc_core".

Run from ~/pqc-assistant:   python patch_surface_degraded_v1.py
Revert with:                git checkout loader.py server.py
"""

import subprocess
import sys
from pathlib import Path

LOADER = Path("loader.py")
SERVER = Path("server.py")

# --- loader.py -------------------------------------------------------------
# Anchor: the return dict of the LIVE build_context (the third definition).
# "domain": top_domain, appears only in that return.
L_ANCHOR = '''    return {
        "domain": top_domain,
'''

L_NEW = '''    _degraded = [p.get("id", "?") for p in [primary] + secondary
                 if isinstance(p, dict) and p.get("degraded")]
    return {
        "degraded": _degraded,
        "domain": top_domain,
'''

# --- server.py -------------------------------------------------------------
# Anchor: the line that pulls domains out of r, immediately after the
# retrieval try/except block and before the llm call.
S_ANCHOR = '''        domains = r.get("domains", [r["domain"]]); tool_names = r["tools"]
'''

S_NEW = '''        if r.get("degraded"):
            _retrieval += "|stale:" + ",".join(r["degraded"])
        domains = r.get("domains", [r["domain"]]); tool_names = r["tools"]
'''

EDITS = [
    (LOADER, L_ANCHOR, L_NEW, "loader.build_context returns degraded ids"),
    (SERVER, S_ANCHOR, S_NEW, "server.ask appends stale marker to _retrieval"),
]


def fail(msg, restore=None):
    if restore:
        for path, original in restore.items():
            path.write_text(original)
        print("  (reverted all changes)")
    print(f"ABORT: {msg}")
    sys.exit(1)


def check_imports(label):
    """Both modules must still parse and import."""
    code = ("import importlib, loader, server; "
            "importlib.reload(loader); importlib.reload(server); "
            "print('IMPORT_OK')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    if "IMPORT_OK" not in r.stdout:
        return False, r.stderr.strip()
    return True, None


def main():
    for p in (LOADER, SERVER):
        if not p.exists():
            fail(f"{p} not found -- run this from ~/pqc-assistant")

    print("== Pre-flight ==")
    ok, err = check_imports("before")
    if not ok:
        fail(f"modules do not import cleanly BEFORE patching:\n{err}")
    print("  loader.py and server.py import cleanly")

    originals = {p: p.read_text() for p in (LOADER, SERVER)}

    # --- verify every anchor is unique before writing anything -------------
    print("\n== Anchor verification ==")
    for path, anchor, _, label in EDITS:
        n = originals[path].count(anchor)
        if n == 0:
            fail(f"[{label}] anchor NOT FOUND in {path.name} -- "
                 f"the file may have changed since it was inspected")
        if n > 1:
            fail(f"[{label}] anchor appears {n}x in {path.name} -- not unique")
        print(f"  OK  {label}")

    # --- check we are not double-applying ---------------------------------
    if '"degraded": _degraded' in originals[LOADER]:
        fail("loader.py already contains the degraded propagation -- already patched?")
    if '"|stale:"' in originals[SERVER]:
        fail("server.py already contains the stale marker -- already patched?")

    # --- apply -------------------------------------------------------------
    print("\n== Applying ==")
    for path, anchor, new, label in EDITS:
        text = path.read_text().replace(anchor, new, 1)
        path.write_text(text)
        print(f"  patched {path.name}: {label}")

    # --- post-flight -------------------------------------------------------
    print("\n== Post-flight ==")
    ok, err = check_imports("after")
    if not ok:
        fail(f"modules FAILED to import after patching:\n{err}", restore=originals)
    print("  both modules still import cleanly")

    # Confirm ask() still returns the expected keys, using a monkeypatched
    # build_context so we do not hit the network or the LLM.
    probe = '''
import server, loader
_calls = {}
def fake_build_context(q, table, refresh=False):
    return {"degraded": ["pqc_core"], "domain": "d", "domains": ["d"],
            "tools": [], "context": {"system": "s", "user": "u"}}
def fake_generate(system, user):
    return "stub answer"
def fake_get_table(refresh=False):
    return []
loader.build_context = fake_build_context
server.get_table = fake_get_table
server.llm.generate = fake_generate
out = server.ask("probe question")
missing = [k for k in ("answer","domains","tools","blocks","retrieval")
           if k not in out]
if missing:
    print("MISSING_KEYS:" + ",".join(missing))
elif "stale:pqc_core" not in out["retrieval"]:
    print("FLAG_NOT_SURFACED:" + str(out["retrieval"]))
else:
    print("PROBE_OK:" + out["retrieval"])
'''
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    out = r.stdout.strip()
    if out.startswith("PROBE_OK"):
        print(f"  {out}")
    elif out.startswith(("MISSING_KEYS", "FLAG_NOT_SURFACED")):
        fail(f"behavioural probe failed: {out}", restore=originals)
    else:
        # The probe itself may fail for unrelated reasons (import side effects,
        # llm module shape). That is not proof the patch is wrong, but it is
        # not proof it is right either -- so say so plainly instead of guessing.
        print("  WARN: probe could not run; patch applied but UNVERIFIED")
        print("        " + (r.stderr.strip().splitlines() or ["(no stderr)"])[-1])

    print("\nDone.")
    print("Verify manually:")
    print("  git diff --stat")
    print("  git diff loader.py server.py")
    print("Revert with:")
    print("  git checkout loader.py server.py")
    print("\nNOTE: end-to-end confirmation needs a working JINA_API_KEY.")
    print("With embeddings at 403, retrieval returns 'empty' regardless.")


if __name__ == "__main__":
    main()
