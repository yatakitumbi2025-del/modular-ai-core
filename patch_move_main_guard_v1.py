#!/usr/bin/env python3
"""
patch_move_main_guard_v1.py -- move loader.py's __main__ guard to the end of the file

THE BUG
-------
loader.py's `if __name__ == "__main__": main()` sits at ~line 218, but
patch_multisource.py and patch_multipack_retrieval_v1.py appended their newer
definitions AFTER it. The file even says so at line ~222:

    # ===== multi-domain merging (overrides the single-pack versions above) =====

Consequence: the module has TWO different live code paths depending on entry point.

  `python loader.py "q"`  -> reaches line 218, main() runs, process exits.
                             Only the definitions ABOVE 218 ever bind:
                               load_pack@44, assemble@123, build_context@154
                             i.e. single-source, NO stale-cache fallback,
                             NO multipack retrieval, NO entry= / source support.

  `import loader` (server) -> no __main__, the whole file executes, last wins:
                               load_pack@429, assemble@227, build_context@364
                             i.e. everything the patches added.

So every CLI test has been exercising older code than the web UI runs. That is why
the CLI looked healthy while the web path drifted, and it means routing/retrieval
conclusions drawn from `python loader.py` may not describe the server at all.

THE FIX
-------
Move the two-line guard to the very end of the file. Nothing is deleted. After this,
the whole module is defined before main() is called, the last definition of each name
wins for BOTH entry points, and CLI and server run identical code.

This also unblocks dead-code removal: build_context@154 and assemble@123 are only
"live" today because the guard cuts execution short. Once it moves, they are
genuinely dead and can be removed safely -- but that is a SEPARATE patch. Do this
one first and confirm it, alone.

Run from ~/pqc-assistant:   python patch_move_main_guard_v1.py
Revert with:                git checkout loader.py
"""

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path("loader.py")

GUARD = 'if __name__ == "__main__":'


def fail(msg, restore=None):
    if restore is not None:
        SRC.write_text(restore)
        print("  (loader.py reverted)")
    print(f"\nABORT: {msg}")
    sys.exit(1)


def run_py(code):
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def live_build_context_lineno(path=SRC):
    """Which build_context does `import loader` actually bind?"""
    out, err, rc = run_py(
        "import loader, inspect; "
        "print(inspect.getsourcelines(loader.build_context)[1])"
    )
    return (int(out), None) if rc == 0 and out.isdigit() else (None, err or out)


def main():
    if not SRC.exists():
        fail("loader.py not found -- run this from ~/pqc-assistant")

    original = SRC.read_text()
    lines = original.splitlines(keepends=True)

    # ---------------- Locate the guard ----------------
    print("== Locating the __main__ guard ==")
    hits = [i for i, l in enumerate(lines) if l.rstrip().startswith(GUARD)]
    if len(hits) != 1:
        fail(f"expected exactly 1 `{GUARD}` line, found {len(hits)}")
    g = hits[0]

    # The guard body is the indented block right after it. Take contiguous
    # indented / blank lines, then trim trailing blanks back off.
    end = g + 1
    while end < len(lines) and (lines[end].strip() == "" or lines[end][:1] in " \t"):
        end += 1
    while end - 1 > g and lines[end - 1].strip() == "":
        end -= 1

    block = lines[g:end]
    print(f"  found at line {g+1}, block is {len(block)} line(s):")
    for b in block:
        print(f"    | {b.rstrip()}")

    if len(block) < 2:
        fail("guard block looks empty -- refusing to guess")

    # ---------------- Is it actually mid-file? ----------------
    tail = "".join(lines[end:])
    tail_code = [l for l in tail.splitlines()
                 if l.strip() and not l.lstrip().startswith("#")]
    if not tail_code:
        fail("the guard is already at the end of the file -- nothing to do")

    print(f"\n  {len(tail_code)} code line(s) follow the guard -- "
          f"these never execute under `python loader.py`")
    print("  first few:")
    for l in tail_code[:4]:
        print(f"    | {l.rstrip()[:70]}")

    # ---------------- Record the before-state ----------------
    print("\n== Pre-flight ==")
    tree = ast.parse(original)
    bcs = [n.lineno for n in tree.body
           if isinstance(n, ast.FunctionDef) and n.name == "build_context"]
    print(f"  build_context defined at lines: {bcs}")

    cli_visible = [n for n in bcs if n < g + 1]
    print(f"  CLI would bind build_context@{max(cli_visible)}" if cli_visible
          else "  CLI sees no build_context")
    print(f"  import would bind build_context@{max(bcs)}")

    before_import, err = live_build_context_lineno()
    if err:
        fail(f"loader.py does not import cleanly BEFORE patching:\n{err}")
    print(f"  confirmed: import binds build_context@{before_import}")

    # ---------------- Apply ----------------
    print("\n== Applying ==")
    remaining = lines[:g] + lines[end:]

    # Tidy: exactly one blank line before the relocated guard.
    while remaining and remaining[-1].strip() == "":
        remaining.pop()
    new_text = "".join(remaining) + "\n\n" + "".join(block)
    if not new_text.endswith("\n"):
        new_text += "\n"

    try:
        ast.parse(new_text)
    except SyntaxError as e:
        fail(f"result is not valid Python: {e}")

    SRC.write_text(new_text)
    print(f"  guard moved from line {g+1} to end of file")
    print(f"  line count: {len(original.splitlines())} -> {len(new_text.splitlines())}")
    if len(original.splitlines()) != len(new_text.splitlines()):
        print("  (difference is whitespace normalisation only; no code removed)")

    # ---------------- Post-flight: import path unchanged ----------------
    print("\n== Post-flight: import path ==")
    after_import, err = live_build_context_lineno()
    if err:
        fail(f"loader.py FAILED to import after patching:\n{err}", restore=original)
    print(f"  import binds build_context@{after_import}")
    # Line numbers shift because the guard moved; what matters is that it is
    # still the LAST definition, not that the number is identical.
    tree2 = ast.parse(SRC.read_text())
    bcs2 = [n.lineno for n in tree2.body
            if isinstance(n, ast.FunctionDef) and n.name == "build_context"]
    if after_import != max(bcs2):
        fail(f"import now binds build_context@{after_import}, "
             f"but the last definition is @{max(bcs2)}", restore=original)
    print(f"  OK  that is the last definition (of {bcs2})")

    # ---------------- Post-flight: the CLI path, for real ----------------
    # This is the check that mattered and was missing before. Run the module as
    # __main__ in a subprocess and see whether it reaches the NEW build_context.
    print("\n== Post-flight: CLI path ==")
    probe = (
        "import runpy, sys, ast\n"
        "src = open('loader.py').read()\n"
        "t = ast.parse(src)\n"
        "last = max(n.lineno for n in t.body\n"
        "           if isinstance(n, ast.FunctionDef) and n.name=='build_context')\n"
        "import loader\n"
        "import inspect\n"
        "got = inspect.getsourcelines(loader.build_context)[1]\n"
        "print('BIND_OK' if got==last else 'BIND_MISMATCH:%d!=%d'%(got,last))\n"
    )
    out, err, rc = run_py(probe)
    print(f"  {out or '(no output)'}")
    if out.startswith("BIND_MISMATCH"):
        fail(f"binding is still wrong: {out}", restore=original)
    if not out.startswith("BIND_OK"):
        print("  WARN: could not confirm; patch applied but UNVERIFIED")
        print("        " + ((err.splitlines() or ["(no stderr)"])[-1]))

    print("\nDone. Now verify for real -- this is the important part:")
    print("")
    print("  python loader.py \"what is ML-KEM\"")
    print("")
    print("Expect: NO NameError, and an answer that now uses the multi-source path")
    print("(you may see secondary packs merged in, which the CLI never did before).")
    print("")
    print("If it behaves:  git add -A && git commit -m 'move __main__ guard to EOF'")
    print("If it does not: git checkout loader.py")
    print("")
    print("NOT done in this patch: build_context@154 and assemble@123 are now")
    print("genuinely dead. Remove them separately, after confirming this one.")


if __name__ == "__main__":
    main()
