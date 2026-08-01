#!/usr/bin/env python3
"""
patch_calc_web_v2.py -- wire the deterministic calculator into the web path

v1 aborted cleanly: its core.py anchor contained "# not real arithmetic - skip it"
with an ASCII hyphen, but the file has an em-dash (cat -A showed M-bM-^@M-^T, i.e.
UTF-8 E2 80 94). Rather than chase exact comment bytes, v2 locates calc_and_show
with ast and replaces it by line span. Comment characters become irrelevant.

server.py's return line is likewise targeted by a short distinctive fragment rather
than the full line, since it was transcribed from a wrapped terminal screenshot.

THE DRIFT
---------
core.calc_and_show(answer_text) post-processes the model's answer: it extracts any
arithmetic the model wrote, recomputes it with sigcalc (ast parse against an
allowlist) and shows the exact results. The CLI calls it. server.py never has, so
the same question asked in the browser gets the model's unchecked arithmetic --
which is precisely what sigcalc exists to prevent.

calc_and_show cannot just be called from server.py because it PRINTS; a print in
the server goes to the Termux terminal, not the browser.

THE FIX
-------
Extract the logic into tools.compute_checks(answer_text), returning the same
formatted string format_calc() already produces, or "" when there is no arithmetic.
  * core.calc_and_show   -> calls it and prints          (CLI behaviour unchanged)
  * server.ask           -> calls it and returns "calc"  (new)
  * ui.html              -> renders r.calc via codePanel (existing renderer)

One implementation, two presentations -- deliberately not a second copy of the loop.

Run from ~/pqc-assistant:   python patch_calc_web_v2.py
Revert with:                git checkout tools.py core.py server.py ui.html
"""

import ast
import subprocess
import sys
from pathlib import Path

TOOLS = Path("tools.py")
CORE = Path("core.py")
SERVER = Path("server.py")
UI = Path("ui.html")

# ---- tools.py: insert compute_checks above format_calc -------------------
T_ANCHOR = "def format_calc(results):"
T_NEW = '''def compute_checks(answer_text):
    """Recompute any arithmetic the model wrote; return formatted text or "".

    Shared by the CLI (core.calc_and_show prints this) and the web path
    (server.ask returns it as the "calc" field). Never raises.
    """
    results = []
    for expr in extract_expressions(answer_text or ""):
        try:
            results.append((expr, safe_calc(expr)))
        except Exception:
            pass  # not real arithmetic, skip it
    return format_calc(results) if results else ""


def format_calc(results):'''

# ---- core.py: whole calc_and_show body, located by AST -------------------
C_REPLACEMENT = '''def calc_and_show(answer_text):
    """Evaluate any arithmetic the model wrote and show the exact results."""
    out = tools.compute_checks(answer_text)
    if out:
        print("\\n" + out)
'''

# ---- server.py: add calc to the returned payload ------------------------
S_FRAGMENT = '"retrieval": _retrieval}'
S_REPLACEMENT = '"retrieval": _retrieval, "calc": tools.compute_checks(answer)}'

# ---- ui.html: render r.calc --------------------------------------------
U_OLD = '''    if(!shown.has(norm(src))) box.appendChild(codePanel(src, true));
  });
'''
U_NEW = '''    if(!shown.has(norm(src))) box.appendChild(codePanel(src, true));
  });
  /* deterministic arithmetic check, same fixed format the CLI prints */
  if(r.calc) box.appendChild(codePanel(r.calc, false));
'''


def fail(msg, restore=None):
    if restore:
        for p, txt in restore.items():
            p.write_text(txt)
        print("  (all files reverted)")
    print(f"\nABORT: {msg}")
    sys.exit(1)


def run_py(code):
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def main():
    for p in (TOOLS, CORE, SERVER, UI):
        if not p.exists():
            fail(f"{p} not found -- run this from ~/pqc-assistant")

    print("== Pre-flight ==")
    out, err, rc = run_py("import tools, core, server; print('OK')")
    if rc != 0:
        fail(f"modules do not import cleanly BEFORE patching:\n{err}")
    print("  tools.py, core.py, server.py import cleanly")

    originals = {p: p.read_text() for p in (TOOLS, CORE, SERVER, UI)}

    if "def compute_checks" in originals[TOOLS]:
        fail("tools.compute_checks already exists -- already patched?")
    if '"calc"' in originals[SERVER]:
        fail("server.py already mentions a calc key -- already patched?")

    print("\n== Target verification ==")

    # tools.py -- text anchor, no comments involved
    n = originals[TOOLS].count(T_ANCHOR)
    if n != 1:
        fail(f"tools.py: 'def format_calc(results):' appears {n}x, expected 1")
    print("  OK  tools.py: format_calc found once")

    # core.py -- AST, immune to comment characters
    ctree = ast.parse(originals[CORE])
    cfuncs = [n for n in ctree.body
              if isinstance(n, ast.FunctionDef) and n.name == "calc_and_show"]
    if len(cfuncs) != 1:
        fail(f"core.py: found {len(cfuncs)} calc_and_show definitions, expected 1")
    cnode = cfuncs[0]
    print(f"  OK  core.py: calc_and_show@{cnode.lineno}-{cnode.end_lineno}")

    # Anything that calls calc_and_show must still work: it takes one arg and
    # returns None both before and after, so only the body changes.
    if len(cnode.args.args) != 1:
        fail(f"core.calc_and_show takes {len(cnode.args.args)} args, expected 1")

    # server.py -- short fragment
    n = originals[SERVER].count(S_FRAGMENT)
    if n != 1:
        fail(f"server.py: {S_FRAGMENT!r} appears {n}x, expected 1")
    print("  OK  server.py: return payload found once")

    # server.py must already import tools (it calls tools.extract_python_blocks)
    if not __import__("re").search(r"^\s*import\s+.*\btools\b", originals[SERVER], __import__("re").M):
        fail("server.py does not import tools -- compute_checks would be unresolved")
    print("  OK  server.py imports tools")

    # ui.html
    n = originals[UI].count(U_OLD)
    if n != 1:
        fail(f"ui.html: block-render anchor appears {n}x, expected 1")
    print("  OK  ui.html: block renderer found once")

    print("\n== Applying ==")

    TOOLS.write_text(originals[TOOLS].replace(T_ANCHOR, T_NEW, 1))
    print("  tools.py: compute_checks added")

    clines = originals[CORE].splitlines(keepends=True)
    clines[cnode.lineno - 1:cnode.end_lineno] = [C_REPLACEMENT]
    CORE.write_text("".join(clines))
    print(f"  core.py: calc_and_show body replaced "
          f"({cnode.end_lineno - cnode.lineno + 1} lines -> 5)")

    SERVER.write_text(originals[SERVER].replace(S_FRAGMENT, S_REPLACEMENT, 1))
    print("  server.py: calc added to payload")

    UI.write_text(originals[UI].replace(U_OLD, U_NEW, 1))
    print("  ui.html: r.calc rendered")

    for path in (TOOLS, CORE, SERVER):
        try:
            ast.parse(path.read_text())
        except SyntaxError as e:
            fail(f"{path.name} is not valid Python: {e}", restore=originals)

    print("\n== Post-flight ==")
    out, err, rc = run_py("import tools, core, server; print('OK')")
    if rc != 0:
        fail(f"modules FAILED to import after patching:\n{err}", restore=originals)
    print("  all modules still import cleanly")

    # 1. compute_checks actually computes.
    probe1 = '''
import tools
out = tools.compute_checks("The total is 1184 + 1088 = 2300 bytes overall.")
print("CALC_OK:" + out.replace(chr(10), " | ")[:120] if out else "NO_RESULT")
'''
    out, err, rc = run_py(probe1)
    print(f"  {out or err}")
    if out.startswith("NO_RESULT"):
        print("  WARN: no expressions extracted from the sample; not fatal,")
        print("        extract_expressions may want a different shape.")

    # 2. benign input must return "" and never raise.
    probe2 = '''
import tools
for t in ["", None, "no numbers here at all", "ML-KEM is FIPS 203"]:
    try:
        r = tools.compute_checks(t)
    except Exception as e:
        print("RAISED on %r: %s" % (t, e)); break
    if r:
        print("FALSE_POSITIVE on %r -> %r" % (t, r[:60])); break
else:
    print("SAFE_OK: empty/prose input returns '' without raising")
'''
    out, err, rc = run_py(probe2)
    print(f"  {out or err}")
    if out.startswith(("RAISED", "FALSE_POSITIVE")):
        fail(f"compute_checks misbehaves on benign input: {out}", restore=originals)

    # 3. CLI path: calc_and_show still prints, still returns None.
    probe3 = '''
import io, contextlib, core
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rv = core.calc_and_show("Adding 12 + 30 = 99 here.")
printed = buf.getvalue()
if rv is not None:
    print("RETURNED_SOMETHING:" + repr(rv)[:60])
elif not printed.strip():
    print("PRINTED_NOTHING")
else:
    print("CLI_OK:" + printed.strip().replace(chr(10), " | ")[:100])
'''
    out, err, rc = run_py(probe3)
    print(f"  {out or err}")
    if out.startswith("RETURNED_SOMETHING"):
        fail("calc_and_show should still return None", restore=originals)

    # 4. server payload carries calc.
    probe4 = '''
import server, loader
loader.build_context = lambda q, t, refresh=False: None
server.get_table = lambda refresh=False: []
server.llm.generate = lambda s, u: "Adding 2 + 2 = 5 here."
out = server.ask("probe")
print("KEY_MISSING" if "calc" not in out else "KEY_OK:" + repr(out["calc"])[:100])
'''
    out, err, rc = run_py(probe4)
    print(f"  {out or err}")
    if out.startswith("KEY_MISSING"):
        fail("server.ask does not return a 'calc' key", restore=originals)

    print("\nDone. Verify:")
    print("  git diff --stat")
    print("  python loader.py \"what is ML-KEM\"     # CLI unchanged")
    print("  python server.py                        # then ask in the browser")
    print("")
    print("Try a question whose answer contains arithmetic, e.g.")
    print('  "how many bytes total for an ML-KEM-768 public key plus ciphertext?"')
    print("A panel with the recomputed expressions should appear under the answer.")
    print("")
    print("If it behaves:  git add -A && git commit -m 'wire calc checks into web path'")
    print("If it does not: git checkout tools.py core.py server.py ui.html")


if __name__ == "__main__":
    main()
