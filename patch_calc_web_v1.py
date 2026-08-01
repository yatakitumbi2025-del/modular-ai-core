#!/usr/bin/env python3
"""
patch_calc_web_v1.py -- wire the deterministic calculator into the web path

THE DRIFT
---------
core.calc_and_show(answer_text) post-processes the model's answer: it extracts any
arithmetic the model wrote, recomputes it with sigcalc (ast parse against an
allowlist), and shows the exact results. The CLI calls it. server.py never has.

So the same question asked in the browser gets whatever arithmetic the model
produced, unchecked -- which is exactly what sigcalc exists to prevent.

calc_and_show cannot simply be called from server.py, because it PRINTS. A print in
the server goes to your Termux terminal, not to the browser.

THE FIX
-------
Extract the logic into tools.compute_checks(answer_text), which returns the same
formatted string format_calc() already produces, or "" when there is no arithmetic.
Then:
  * core.calc_and_show   -> calls it and prints          (CLI behaviour unchanged)
  * server.ask           -> calls it and returns "calc"  (new)
  * ui.html              -> renders r.calc as a code panel, reusing codePanel()

One implementation, two presentations. Deliberately NOT a second copy of the loop --
duplicated definitions are what made loader.py unreadable in the first place.

The web output is the same fixed-format text the CLI prints. No new markup or CSS.

Run from ~/pqc-assistant:   python patch_calc_web_v1.py
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

# ---- 1. tools.py: add compute_checks just above format_calc ---------------
T_ANCHOR = '''def format_calc(results):'''

T_NEW = '''def compute_checks(answer_text):
    """Recompute any arithmetic the model wrote; return formatted text or "".

    Shared by the CLI (core.calc_and_show, which prints this) and the web path
    (server.ask, which returns it as the "calc" field). Never raises.
    """
    results = []
    for expr in extract_expressions(answer_text or ""):
        try:
            results.append((expr, safe_calc(expr)))
        except Exception:
            pass  # not real arithmetic - skip it
    return format_calc(results) if results else ""


def format_calc(results):'''

# ---- 2. core.py: calc_and_show delegates ---------------------------------
C_OLD = '''    results = []
    for expr in tools.extract_expressions(answer_text):
        try:
            results.append((expr, tools.safe_calc(expr)))
        except Exception:
            pass  # not real arithmetic - skip it
    if results:
        print("\\n" + tools.format_calc(results))
'''

C_NEW = '''    out = tools.compute_checks(answer_text)
    if out:
        print("\\n" + out)
'''

# ---- 3. server.py: include calc in the payload ---------------------------
S_OLD = '''    return {"answer": answer, "domains": domains, "tools": tool_names, "blocks": blocks, "retrieval": _retrieval}'''

S_NEW = '''    return {"answer": answer, "domains": domains, "tools": tool_names, "blocks": blocks, "retrieval": _retrieval, "calc": tools.compute_checks(answer)}'''

# ---- 4. ui.html: render r.calc -------------------------------------------
U_OLD = '''    if(!shown.has(norm(src))) box.appendChild(codePanel(src, true));
  });
'''

U_NEW = '''    if(!shown.has(norm(src))) box.appendChild(codePanel(src, true));
  });
  /* deterministic arithmetic check, same fixed format the CLI prints */
  if(r.calc) box.appendChild(codePanel(r.calc, false));
'''

EDITS = [
    (TOOLS, T_ANCHOR, T_NEW, "tools.compute_checks added"),
    (CORE, C_OLD, C_NEW, "core.calc_and_show delegates"),
    (SERVER, S_OLD, S_NEW, "server.ask returns calc"),
    (UI, U_OLD, U_NEW, "ui.html renders r.calc"),
]


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

    if "def compute_checks" in TOOLS.read_text():
        fail("tools.compute_checks already exists -- already patched?")

    originals = {p: p.read_text() for p in (TOOLS, CORE, SERVER, UI)}

    print("\n== Anchor verification ==")
    for path, anchor, _, label in EDITS:
        n = originals[path].count(anchor)
        if n == 0:
            print(f"  FAILED [{label}] in {path.name}. Expected to find:")
            for l in anchor.splitlines()[:4]:
                print(f"    {l!r}")
            fail(f"[{label}] anchor NOT FOUND -- file differs from expected")
        if n > 1:
            fail(f"[{label}] anchor appears {n}x in {path.name} -- not unique")
        print(f"  OK  {label}")

    print("\n== Applying ==")
    for path, anchor, new, label in EDITS:
        path.write_text(path.read_text().replace(anchor, new, 1))
        print(f"  {path.name}: {label}")

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

    # Probe 1: compute_checks finds arithmetic and returns non-empty text.
    probe1 = '''
import tools
txt = "The total is 1184 + 1088 = 2300 bytes overall."
out = tools.compute_checks(txt)
if not out:
    print("NO_RESULT")
elif "2272" in out or "1184" in out:
    print("CALC_OK:" + out.replace(chr(10), " | ")[:120])
else:
    print("UNEXPECTED:" + out.replace(chr(10), " | ")[:120])
'''
    out, err, rc = run_py(probe1)
    print(f"  {out or err}")
    if out.startswith("NO_RESULT"):
        print("  WARN: no expressions extracted from the sample text.")
        print("        extract_expressions may want a different shape; not fatal.")
    elif not out.startswith(("CALC_OK", "UNEXPECTED")):
        print("  WARN: probe could not run; applied but UNVERIFIED")

    # Probe 2: empty / non-arithmetic input must return "" and never raise.
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

    # Probe 3: server.ask payload carries the calc key.
    probe3 = '''
import server, loader
loader.build_context = lambda q, t, refresh=False: None
server.get_table = lambda refresh=False: []
server.llm.generate = lambda s, u: "Adding 2 + 2 = 5 here."
out = server.ask("probe")
if "calc" not in out:
    print("KEY_MISSING")
else:
    print("KEY_OK:" + repr(out["calc"])[:100])
'''
    out, err, rc = run_py(probe3)
    print(f"  {out or err}")
    if out.startswith("KEY_MISSING"):
        fail("server.ask does not return a 'calc' key", restore=originals)

    print("\nDone. Verify:")
    print("  git diff --stat")
    print("  python server.py")
    print("")
    print("Ask something with arithmetic in the answer, e.g.")
    print('  "how many bytes total for an ML-KEM-768 public key plus ciphertext?"')
    print("A panel with the recomputed expressions should appear under the answer,")
    print("in the same format the CLI prints.")
    print("")
    print("Also confirm the CLI is unchanged:")
    print("  python loader.py \"what is ML-KEM\"")
    print("")
    print("If it behaves:  git add -A && git commit -m 'wire calc checks into web path'")
    print("If it does not: git checkout tools.py core.py server.py ui.html")


if __name__ == "__main__":
    main()
