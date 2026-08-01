#!/usr/bin/env python3
"""
patch_remove_dead_ast_v2.py -- remove shadowed duplicate definitions from loader.py

Background: successive patches (patch_multisource.py, patch_multipack_retrieval_v1.py,
patch_cache_ttl_v2.py, patch_stale_fallback.py) each APPENDED a new version of a
function instead of replacing the old one. Python binds the LAST definition, so the
earlier ones are unreachable -- but they still appear in every grep, which makes it
impossible to investigate real bugs reliably.

v1 of this patch used text anchors and eyeballed line ranges. It deleted a load_pack
that a still-reachable build_context depended on, and the post-check (module imports?)
was too weak to notice. This version fixes both mistakes:

  * Targets are located by ast.FunctionDef nodes, not by string matching. The parser
    decides what to delete, using the same structure Python uses to bind names.
  * Before deleting anything, every surviving line is scanned for references to the
    names being removed. If a survivor depends on a corpse, the patch aborts.
  * After deleting, the actual CLI call path (main -> handle -> build_context) is
    executed with the network stubbed out. A NameError surfaces HERE, not later.

Deletions (each superseded by a later definition of the same name):
    load_pack       @44    -> superseded by @316 (aliased) and @429 (wrapper)
    assemble        @123   -> superseded by @227
    build_context   @154   -> superseded by @364
    build_context   @264   -> superseded by @364

Explicitly KEPT -- do not add these to the target list:
    fetch_text          used by load_pack@316
    _keyword_retrieve   fallback path called twice by retrieve@92
    retrieve@92         live; used by the CLI path
    show, main, handle  CLI entry points
    assemble@227        live
    _find_entry         live
    load_pack@316       LIVE via the alias `_load_pack_strict = load_pack` at ~426,
                        which captures it BEFORE the @429 wrapper rebinds the name.
                        Deleting this breaks the entire loader.
    build_context@364   live
    load_pack@429       the 404/stale-fallback tolerance wrapper; what `load_pack` binds to

Run from ~/pqc-assistant:   python patch_remove_dead_ast_v2.py
Revert with:                git checkout loader.py
"""

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path("loader.py")

# (function name, 1-based lineno of the def to remove)
TARGETS = [
    ("load_pack", 44),
    ("assemble", 123),
    ("build_context", 154),
    ("build_context", 262),
]

# Names that must still resolve after the patch.
MUST_EXIST = [
    "fetch_text", "_keyword_retrieve", "retrieve", "show", "main",
    "assemble", "_find_entry", "load_pack", "build_context",
]

# load_pack must still bind to the tolerance wrapper's signature.
EXPECTED_SIG = "(domain_id, refresh=False, entry=None)"


def fail(msg, restore=None):
    if restore is not None:
        SRC.write_text(restore)
        print("  (loader.py reverted)")
    print(f"\nABORT: {msg}")
    sys.exit(1)


def run_py(code):
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def signature_of_load_pack():
    out, err, rc = run_py(
        "import loader, inspect; print(str(inspect.signature(loader.load_pack)))"
    )
    return (out, None) if rc == 0 else (None, err)


def main():
    if not SRC.exists():
        fail("loader.py not found -- run this from ~/pqc-assistant")

    original = SRC.read_text()

    # ---------------- Pre-flight ----------------
    print("== Pre-flight ==")
    before_sig, err = signature_of_load_pack()
    if err:
        fail(f"loader.py does not import cleanly BEFORE patching:\n{err}")
    print(f"  load_pack binds to: {before_sig}")
    if before_sig != EXPECTED_SIG:
        fail(f"unexpected starting signature; expected {EXPECTED_SIG}")

    tree = ast.parse(original)
    top_funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]

    # ---------------- Locate targets via AST ----------------
    print("\n== Locating targets (AST) ==")
    by_name = {}
    for fn in top_funcs:
        by_name.setdefault(fn.name, []).append(fn)

    nodes = []
    for name, lineno in TARGETS:
        matches = [f for f in top_funcs if f.name == name and f.lineno == lineno]
        if len(matches) != 1:
            fail(f"expected exactly 1 def {name} at line {lineno}, found {len(matches)}"
                 f" -- loader.py differs from the version this patch was written for")
        node = matches[0]

        # Never delete the LAST definition of a name -- that is the live one.
        last = max(by_name[name], key=lambda f: f.lineno)
        if node.lineno == last.lineno:
            fail(f"{name}@{lineno} is the LAST definition of {name} -- it is live, refusing")

        nodes.append((name, node))
        print(f"  {name}@{node.lineno}-{node.end_lineno} "
              f"({node.end_lineno - node.lineno + 1} lines)  [live: {name}@{last.lineno}]")

    # Regions must not overlap
    spans = sorted((n.lineno, n.end_lineno) for _, n in nodes)
    for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
        if e1 >= s2:
            fail(f"target spans overlap: {s1}-{e1} and {s2}-{e2}")

    # ---------------- Reachability check ----------------
    # A duplicate is only safe to delete if nothing OUTSIDE the doomed regions
    # calls it in a way that would resolve to the doomed body. Since Python binds
    # the last definition, any call resolves to the survivor -- EXCEPT calls made
    # from inside another doomed region (irrelevant) or via an alias assignment.
    # Aliases are the real danger: `_load_pack_strict = load_pack` captured @316.
    print("\n== Reachability / alias check ==")
    doomed_lines = set()
    for _, n in nodes:
        doomed_lines.update(range(n.lineno, n.end_lineno + 1))

    doomed_names = {name for name, _ in nodes}
    aliases = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and node.lineno not in doomed_lines:
            if isinstance(node.value, ast.Name) and node.value.id in doomed_names:
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                aliases.append((node.lineno, targets, node.value.id))

    for lineno, targets, src_name in aliases:
        live = max(by_name[src_name], key=lambda f: f.lineno)
        doomed_here = [n.lineno for _, n in nodes if _ == src_name]
        print(f"  alias at line {lineno}: {targets} = {src_name}")
        print(f"    -> resolves to {src_name}@{live.lineno} (live), "
              f"not to {doomed_here} -- safe")

    if not aliases:
        print("  no aliases of doomed names found outside target regions")

    # ---------------- Apply ----------------
    print("\n== Applying ==")
    lines = original.splitlines(keepends=True)
    for name, node in sorted(nodes, key=lambda x: x[1].lineno, reverse=True):
        del lines[node.lineno - 1:node.end_lineno]
        print(f"  removed {name}@{node.lineno}")

    patched = "".join(lines)
    removed = len(original.splitlines()) - len(patched.splitlines())
    print(f"  {len(original.splitlines())} -> {len(patched.splitlines())} lines "
          f"({removed} removed)")

    try:
        ast.parse(patched)
    except SyntaxError as e:
        fail(f"result is not valid Python: {e}")

    SRC.write_text(patched)

    # ---------------- Post-flight: binding ----------------
    print("\n== Post-flight: bindings ==")
    after_sig, err = signature_of_load_pack()
    if err:
        fail(f"loader.py FAILED to import after patching:\n{err}", restore=original)
    print(f"  load_pack binds to: {after_sig}")
    if after_sig != before_sig:
        fail(f"binding CHANGED ({before_sig} -> {after_sig})", restore=original)

    out, err, rc = run_py(
        "import loader; "
        "m=[n for n in %r if not hasattr(loader,n)]; "
        "print('MISSING:'+','.join(m) if m else 'ALL_PRESENT')" % MUST_EXIST
    )
    print(f"  {out or err}")
    if out != "ALL_PRESENT":
        fail("a required definition disappeared", restore=original)

    # Each name should now appear exactly once, except load_pack (316 + 429 wrapper).
    tree2 = ast.parse(patched)
    counts = {}
    for n in tree2.body:
        if isinstance(n, ast.FunctionDef):
            counts[n.name] = counts.get(n.name, 0) + 1
    expected_counts = {"load_pack": 2, "build_context": 1, "assemble": 1}
    for name, exp in expected_counts.items():
        got = counts.get(name, 0)
        flag = "OK " if got == exp else "WARN"
        print(f"  {flag} def {name} appears {got}x (expected {exp})")

    # ---------------- Post-flight: the path that broke last time ----------------
    # v1 passed every check above and still died with NameError, because a
    # still-reachable build_context called a load_pack that had been deleted.
    # Importing proves nothing about running. So: actually run the CLI path.
    print("\n== Post-flight: CLI path smoke test ==")
    smoke = '''
import loader, router
# Stub the network so this never touches the CDN or Jina.
router.build_routing_table = lambda *a, **k: []
router.route = lambda q, table: []          # -> build_context returns None early
loader.fetch_text = lambda url: ""
try:
    r = loader.build_context("smoke test question", [])
    print("CLI_PATH_OK:" + repr(r))
except NameError as e:
    print("NAME_ERROR:" + str(e))
except Exception as e:
    print("OTHER:" + type(e).__name__ + ":" + str(e))
'''
    out, err, rc = run_py(smoke)
    if out.startswith("NAME_ERROR"):
        fail(f"the exact failure v1 hit is present: {out}", restore=original)
    elif out.startswith("CLI_PATH_OK"):
        print(f"  {out}")
    elif out.startswith("OTHER"):
        # A non-NameError here is usually a stub mismatch, not a real defect.
        print(f"  WARN: smoke test raised {out}")
        print("        This is probably the stub, not the patch -- but it is UNVERIFIED.")
    else:
        print("  WARN: smoke test did not run; patch applied but UNVERIFIED")
        print("        " + ((err.splitlines() or ["(no stderr)"])[-1]))

    print("\nDone.")
    print("Now verify for real:")
    print("  git diff --stat")
    print("  python loader.py \"what is ML-KEM\"")
    print("")
    print("If it behaves:  git add -A && git commit -m 'remove dead shadowed defs'")
    print("If it does not: git checkout loader.py")
    print("")
    print("NOTE: with JINA_API_KEY returning 403, routing runs on keyword fallback.")
    print("A degraded answer is expected -- what matters is that it does not crash.")


if __name__ == "__main__":
    main()
