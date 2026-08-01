#!/usr/bin/env python3
"""
patch_remove_dead_v1.py -- remove shadowed dead definitions from loader.py

Context: successive patches (patch_multisource.py, patch_multipack_retrieval_v1.py)
APPENDED new versions of functions instead of replacing the old ones. Python binds
the LAST definition, so the earlier ones are dead -- but they still show up in every
grep, which makes investigating real bugs unreliable.

Removes three regions:
  1. old load_pack(domain_id, refresh=False)        -- superseded by the entry= version
  2. old assemble(pack_data, retrieved, question)   -- superseded by the secondary= version
  3. middle build_context(...)                      -- superseded by the third definition

KEEPS (do not touch -- these live above/between the dead regions):
  fetch_text, _keyword_retrieve, retrieve, show, main, _find_entry,
  load_pack(entry=None) at ~316, build_context at ~364,
  _load_pack_strict alias at ~426, load_pack wrapper at ~429

CRITICAL: the alias `_load_pack_strict = load_pack` captures the ~316 definition
before the ~429 wrapper redefines the name. Deleting ~316 would break the loader.
This patch does NOT touch it.

Run from ~/pqc-assistant:   python patch_remove_dead_v1.py
Revert with:                git checkout loader.py
"""

import re
import subprocess
import sys
from pathlib import Path

SRC = Path("loader.py")

# Each region: (label, start_anchor, end_anchor)
# The region removed is [start_anchor, end_anchor) -- end_anchor line is KEPT.
REGIONS = [
    (
        "old single-source load_pack",
        "# ---- Load (activate) a pack ",
        "# ---- Retrieve the most relevant knowledge chunks ",
    ),
    (
        "old assemble (no secondary)",
        "# ---- Assemble the model-ready context ",
        "# ---- Tie router + loader together ",
    ),
    (
        "middle build_context",
        "def build_context(question, table, refresh=False):",  # 2nd occurrence
        "def _find_entry(domain_id, table):",
    ),
]

# Definitions that MUST still be bound after the patch, with expected signatures.
EXPECTED = {
    "load_pack": "(domain_id, refresh=False, entry=None)",
}
MUST_EXIST = ["fetch_text", "retrieve", "assemble", "build_context",
              "_find_entry", "load_pack", "show", "main"]


def fail(msg):
    print(f"ABORT: {msg}")
    sys.exit(1)


def current_signature():
    """Ask a fresh interpreter what loader.load_pack actually binds to."""
    code = (
        "import loader, inspect; "
        "print(str(inspect.signature(loader.load_pack)))"
    )
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr.strip()
    return r.stdout.strip(), None


def main():
    if not SRC.exists():
        fail("loader.py not found -- run this from ~/pqc-assistant")

    print("== Pre-flight ==")
    before_sig, err = current_signature()
    if err:
        fail(f"loader.py does not import cleanly BEFORE patching:\n{err}")
    print(f"  load_pack binds to: {before_sig}")
    if before_sig != EXPECTED["load_pack"]:
        fail(f"unexpected starting signature; expected {EXPECTED['load_pack']}")

    lines = SRC.read_text().splitlines(keepends=True)
    original_count = len(lines)

    # --- Verify anchors before touching anything -------------------------
    print("\n== Anchor verification ==")
    cuts = []
    for label, start_a, end_a in REGIONS:
        starts = [i for i, l in enumerate(lines) if l.strip().startswith(start_a.strip())]
        ends = [i for i, l in enumerate(lines) if l.strip().startswith(end_a.strip())]

        if label == "middle build_context":
            # Ambiguous by design: there are 3 build_context defs. We want the
            # SECOND one, which is the one immediately preceding _find_entry.
            if len(ends) != 1:
                fail(f"[{label}] end anchor not unique ({len(ends)} matches)")
            end_i = ends[0]
            before_end = [i for i in starts if i < end_i]
            if len(before_end) != 2:
                fail(f"[{label}] expected 2 build_context defs before _find_entry, "
                     f"found {len(before_end)}")
            start_i = before_end[-1]  # the second one
        else:
            if len(starts) != 1:
                fail(f"[{label}] start anchor not unique ({len(starts)} matches)")
            if len(ends) != 1:
                fail(f"[{label}] end anchor not unique ({len(ends)} matches)")
            start_i, end_i = starts[0], ends[0]

        if start_i >= end_i:
            fail(f"[{label}] start (line {start_i+1}) is not before end (line {end_i+1})")

        cuts.append((label, start_i, end_i))
        print(f"  OK  {label}: lines {start_i+1}-{end_i} "
              f"({end_i - start_i} lines)")

    # Regions must not overlap
    ordered = sorted(cuts, key=lambda c: c[1])
    for (l1, s1, e1), (l2, s2, e2) in zip(ordered, ordered[1:]):
        if e1 > s2:
            fail(f"regions overlap: [{l1}] and [{l2}]")

    # --- Apply (highest line first so indices stay valid) ----------------
    print("\n== Applying ==")
    new_lines = list(lines)
    for label, start_i, end_i in sorted(cuts, key=lambda c: c[1], reverse=True):
        del new_lines[start_i:end_i]
        print(f"  removed {label}")

    removed = original_count - len(new_lines)
    print(f"  {original_count} -> {len(new_lines)} lines ({removed} removed)")

    backup = SRC.with_suffix(".py.predead")
    backup.write_text("".join(lines))
    SRC.write_text("".join(new_lines))
    print(f"  backup written to {backup.name}")

    # --- Post-flight -----------------------------------------------------
    print("\n== Post-flight ==")
    after_sig, err = current_signature()
    if err:
        SRC.write_text("".join(lines))
        fail(f"loader.py FAILED to import after patching -- reverted.\n{err}")
    print(f"  load_pack binds to: {after_sig}")

    if after_sig != before_sig:
        SRC.write_text("".join(lines))
        fail(f"binding CHANGED ({before_sig} -> {after_sig}) -- reverted.")

    code = ("import loader; "
            "missing=[n for n in %r if not hasattr(loader,n)]; "
            "print('MISSING:'+','.join(missing) if missing else 'ALL_PRESENT')"
            % MUST_EXIST)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    out = r.stdout.strip()
    print(f"  {out}")
    if out != "ALL_PRESENT":
        SRC.write_text("".join(lines))
        fail("a required definition disappeared -- reverted.")

    # Confirm the duplicates are actually gone
    text = SRC.read_text()
    for name, expected_n in [("def load_pack(", 2), ("def build_context(", 1),
                             ("def assemble(", 1)]:
        n = len(re.findall(re.escape(name), text))
        status = "OK " if n == expected_n else "WARN"
        print(f"  {status} {name!r} appears {n}x (expected {expected_n})")

    print("\nDone. loader.py imports cleanly and load_pack binding is unchanged.")
    print("Next:")
    print("  1. Run a known-good query and compare the answer.")
    print("  2. If wrong:  git checkout loader.py")
    print("  3. If right:  git add -A && git commit -m 'remove dead shadowed defs'")


if __name__ == "__main__":
    main()
