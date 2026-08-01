#!/usr/bin/env python3
"""
patch_attribution_off_v1.py -- resolve the pack-attribution contradiction, and
                               remove the dead run_code() from server.py

PROBLEM 1: contradictory instructions about attribution
-------------------------------------------------------
loader.assemble() builds the model context. In the multi-pack branch it says:

    "Apply the combined standards of every matched module, and mark which
     module a specific claim draws from when it is not obvious."

and it labels every retrieved chunk with its internal pack id:

    lines = [f"- [{d}] {c}" for d, c in retrieved]
    -> "- [pqc-core__pqc_core] KEM and signature are different jobs"

Meanwhile each pack's own prompt.md says:

    "Never mention packs, modules, sources, or where this information came from.
     Do not append notes labelled by pack name."

So the assembled context tells the model to attribute claims to modules while the
pack prompt forbids exactly that. Today the pack prompt happens to win, but that is
luck, not design -- and the internal ids ("pqc-application__pqc_implementation")
are meaningless to an end user anyway.

Decision taken: attribution OFF. The pack prompt wins explicitly.
  - Retrieved chunks are passed to the model WITHOUT the [pack_id] prefix.
  - The "mark which module a claim draws from" sentence is removed.

The pack ids are NOT lost: build_context still returns "domain", "domains" and
"also_matched", which is what the CLI prints as "domain:" / "also matched:", and
what the web UI shows as the PQC_CORE badge. Debug visibility is unaffected --
only the text handed to the model changes.

PROBLEM 2: dead run_code() in server.py
----------------------------------------
server.py defines run_code twice:
    line ~79   def run_code(src)                  <- dead, shadowed
    line ~123  def run_code(src, system=None)     <- live; adds retry-and-fix logic
The call site at ~107 passes one argument, which the live version accepts via its
default. Same shadowing pattern already cleaned out of loader.py.

Run from ~/pqc-assistant:   python patch_attribution_off_v1.py
Revert with:                git checkout loader.py server.py
"""

import ast
import subprocess
import sys
from pathlib import Path

LOADER = Path("loader.py")
SERVER = Path("server.py")

# --- 1. drop the [pack_id] prefix from retrieved chunks --------------------
OLD_LINES = '''        lines = [f"- [{d}] {c}" for d, c in retrieved]
'''
NEW_LINES = '''        # Attribution is deliberately OFF: pack ids are internal and the pack
        # prompts forbid naming sources. Pack identity is still reported via
        # build_context()'s "domain"/"domains"/"also_matched" keys.
        lines = [f"- {c}" for _d, c in retrieved]
'''

# --- 2. drop the "mark which module" instruction ---------------------------
OLD_MARK = '''            "Apply the combined standards of every matched module, and mark which "
            "module a specific claim draws from when it is not obvious."
'''
NEW_MARK = '''            "Apply the combined standards of every matched module. Do not name "
            "the modules or say where any part of the answer came from."
'''


def fail(msg, restore=None):
    if restore:
        for p, txt in restore.items():
            p.write_text(txt)
        print("  (reverted)")
    print(f"\nABORT: {msg}")
    sys.exit(1)


def run_py(code):
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def main():
    for p in (LOADER, SERVER):
        if not p.exists():
            fail(f"{p} not found -- run this from ~/pqc-assistant")

    print("== Pre-flight ==")
    out, err, rc = run_py("import loader, server; print('OK')")
    if rc != 0:
        fail(f"modules do not import cleanly BEFORE patching:\n{err}")
    print("  loader.py and server.py import cleanly")

    originals = {LOADER: LOADER.read_text(), SERVER: SERVER.read_text()}
    ltext = originals[LOADER]

    # ---------- loader.py anchors ----------
    print("\n== Anchor verification (loader.py) ==")
    for label, anchor in [("chunk label line", OLD_LINES),
                          ("mark-which-module text", OLD_MARK)]:
        n = ltext.count(anchor)
        if n == 0:
            fail(f"[{label}] anchor NOT FOUND -- loader.py differs from expected")
        if n > 1:
            fail(f"[{label}] anchor appears {n}x -- not unique")
        print(f"  OK  {label}")

    # ---------- server.py: locate the dead run_code via AST ----------
    print("\n== Locating dead run_code (server.py, AST) ==")
    stree = ast.parse(originals[SERVER])
    rcs = [n for n in stree.body
           if isinstance(n, ast.FunctionDef) and n.name == "run_code"]
    if len(rcs) != 2:
        fail(f"expected exactly 2 top-level run_code defs, found {len(rcs)}")
    dead = min(rcs, key=lambda f: f.lineno)
    live = max(rcs, key=lambda f: f.lineno)
    print(f"  dead: run_code@{dead.lineno}-{dead.end_lineno} "
          f"(args: {[a.arg for a in dead.args.args]})")
    print(f"  live: run_code@{live.lineno}-{live.end_lineno} "
          f"(args: {[a.arg for a in live.args.args]})")

    # The live one must still accept a single positional arg, since the
    # /api/run handler calls run_code(code) with one argument.
    live_required = len(live.args.args) - len(live.args.defaults)
    if live_required > 1:
        fail(f"live run_code requires {live_required} args; the /api/run call "
             f"site passes only 1 -- refusing to delete the 1-arg version")
    print(f"  OK  live version needs {live_required} positional arg(s)")

    # ---------- apply ----------
    print("\n== Applying ==")
    lnew = ltext.replace(OLD_LINES, NEW_LINES, 1).replace(OLD_MARK, NEW_MARK, 1)
    try:
        ast.parse(lnew)
    except SyntaxError as e:
        fail(f"loader.py result is not valid Python: {e}")
    LOADER.write_text(lnew)
    print("  loader.py: attribution disabled (labels + mark-module instruction)")

    slines = originals[SERVER].splitlines(keepends=True)
    del slines[dead.lineno - 1:dead.end_lineno]
    snew = "".join(slines)
    try:
        ast.parse(snew)
    except SyntaxError as e:
        fail(f"server.py result is not valid Python: {e}", restore=originals)
    SERVER.write_text(snew)
    print(f"  server.py: removed dead run_code@{dead.lineno} "
          f"({dead.end_lineno - dead.lineno + 1} lines)")

    # ---------- post-flight ----------
    print("\n== Post-flight ==")
    out, err, rc = run_py("import loader, server; print('OK')")
    if rc != 0:
        fail(f"modules FAILED to import after patching:\n{err}", restore=originals)
    print("  both modules still import cleanly")

    out, err, rc = run_py(
        "import ast, inspect, server; "
        "t=ast.parse(open('server.py').read()); "
        "n=[x for x in t.body if isinstance(x,ast.FunctionDef) and x.name=='run_code']; "
        "print('COUNT:%d ARGS:%s' % (len(n), inspect.signature(server.run_code)))"
    )
    print(f"  {out or err}")
    if not out.startswith("COUNT:1"):
        fail("server.run_code did not end up defined exactly once", restore=originals)

    # Behavioural probe: assemble() must no longer emit "[pack_id]" prefixes.
    probe = '''
import loader
pack = {"id": "probe_pack", "name": "Probe", "prompt": "PROMPT BODY", "tools": []}
retrieved = [("pqc-core__pqc_core", "CHUNK ONE"), ("pqc-app__impl", "CHUNK TWO")]
try:
    ctx = loader.assemble(pack, retrieved, "a question")
except Exception as e:
    print("PROBE_RAISED:" + type(e).__name__ + ":" + str(e))
else:
    blob = ctx if isinstance(ctx, str) else repr(ctx)
    if "[pqc-core__pqc_core]" in blob or "[pqc-app__impl]" in blob:
        print("LABEL_STILL_PRESENT")
    elif "CHUNK ONE" not in blob:
        print("CHUNK_LOST")
    else:
        print("PROBE_OK: chunks present, labels gone")
'''
    out, err, rc = run_py(probe)
    if out.startswith("PROBE_OK"):
        print(f"  {out}")
    elif out.startswith(("LABEL_STILL_PRESENT", "CHUNK_LOST")):
        fail(f"behavioural probe failed: {out}", restore=originals)
    else:
        print("  WARN: probe could not run; patch applied but UNVERIFIED")
        print("        " + (out or (err.splitlines() or ["(no stderr)"])[-1]))

    print("\nDone. Verify:")
    print("  git diff loader.py server.py")
    print("  python loader.py \"what is ML-KEM\"")
    print("")
    print("In the CONTEXT dump, reference material lines should now read")
    print("  - KEM and signature are different jobs")
    print("rather than")
    print("  - [pqc-core__pqc_core] KEM and signature are different jobs")
    print("The 'domain:' and 'also matched:' lines are unchanged.")
    print("")
    print("Also worth one web check, since run_code changed:")
    print("  python server.py   -> ask something that triggers code execution")
    print("")
    print("If it behaves:  git add -A && git commit -m "
          "'attribution off; drop dead run_code'")
    print("If it does not: git checkout loader.py server.py")


if __name__ == "__main__":
    main()
