#!/usr/bin/env python3
"""
patch_score_hybrid.py -- let score_routing.py assert multi-pack expectations,
and add the firmware-signing query as a regression case.

Why: the current check is `top[1] == expected`, a single winner. The firmware
question ("sign firmware updates, show me the code AND how to roll it out")
legitimately spans pqc_implementation and pqc_application. Either leading is
fine; what must not happen is implementation falling out of the top two, which
is what produced the ungrounded answer.

Changes:
  1. Adds _matches(). If `expected` is a set, every id in it must appear in the
     top two. If it is a string, behaviour is unchanged.
  2. Adds the firmware query with expected={IMP, APP}.

Run from ~/pqc-assistant:   python patch_score_hybrid.py
"""

import shutil
import sys
from pathlib import Path

TARGET = Path(__file__).parent / "score_routing.py"

OLD_HELPER_ANCHOR = "for (q, expected), qv in zip(QUERIES, qvecs):"
NEW_HELPER_ANCHOR = '''def _matches(top, second, expected):
    """A set expectation means every id in it must be in the top two.
    A plain string keeps the original single-winner behaviour."""
    if isinstance(expected, (set, frozenset)):
        return expected <= {top[1], second[1]}
    return top[1] == expected


for (q, expected), qv in zip(QUERIES, qvecs):'''

OLD_CHECK = "top[1] == expected"
NEW_CHECK = "_matches(top, second, expected)"

OLD_QUERIES = '''QUERIES = [
    ("Show me Kyber key exchange example",'''
NEW_QUERIES = '''QUERIES = [
    ("I need to sign firmware updates. Show me the code "
     "and tell me how to roll it out.", {IMP, APP}),
    ("Show me Kyber key exchange example",'''

EDITS = [
    ("_matches helper", OLD_HELPER_ANCHOR, NEW_HELPER_ANCHOR),
    ("match check", OLD_CHECK, NEW_CHECK),
    ("firmware query", OLD_QUERIES, NEW_QUERIES),
]


def main():
    if not TARGET.exists():
        sys.exit(f"ERROR: {TARGET} not found. Run this from ~/pqc-assistant.")

    src = TARGET.read_text()

    if "_matches" in src:
        sys.exit("Already patched (_matches present). Nothing to do.")

    # The new query references IMP and APP; confirm they are defined.
    for name in ("IMP", "APP"):
        if name not in src:
            sys.exit(f"ERROR: constant {name} not found in {TARGET.name}. Aborting.")

    for label, old, _ in EDITS:
        n = src.count(old)
        if n != 1:
            sys.exit(
                f"ERROR: anchor for '{label}' matched {n} times, expected 1. "
                "Aborting without changes."
            )

    backup = TARGET.with_suffix(".py.bak")
    shutil.copy2(TARGET, backup)

    for label, old, new in EDITS:
        src = src.replace(old, new)
        print(f"  applied: {label}")

    TARGET.write_text(src)
    print(f"\nPatched {TARGET.name}. Backup at {backup.name}.")
    print("Run: python score_routing.py")
    print("The firmware query passes only if BOTH packs are in the top two.")


if __name__ == "__main__":
    main()
