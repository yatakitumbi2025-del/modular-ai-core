#!/usr/bin/env python3
"""
patch_secondary_prompts.py -- include secondary packs' prompts in the system
message instead of only their names.

The defect: assemble() builds `parts` from the PRIMARY prompt only, then tells
the model "Apply the combined standards of every matched module" while listing
the secondary modules by name. Their prompt.md content is never included. The
model is instructed to follow rules it was never given, so it invents them.

Observed consequence: a firmware-signing question tied between pqc_application
(0.311) and pqc_implementation (0.303). Implementation loaded as secondary, its
banned-strings list never reached the model, and the answer recommended qTesla
(a dead scheme), presented SPHINCS and SLH-DSA as different algorithms, and
closed with the generic "consult documentation and experts" filler that
implementation's prompt.md explicitly forbids.

Fix: append each secondary pack's prompt under a clear header, inside the
existing `if secondary:` block.

Deliberately NOT changed: secondary `examples`. Examples define output STYLE,
and blending styles from two packs makes output worse, not better. The primary
pack should continue to set style alone.

Run from ~/pqc-assistant:   python patch_secondary_prompts.py
"""

import shutil
import sys
from pathlib import Path

TARGET = Path(__file__).parent / "loader.py"

# This two-line form appears only in the LIVE assemble() (line ~227).
# The dead assemble() at ~123 uses a different body, so this cannot match it.
OLD = '''    if retrieved:
        lines = [f"- [{d}] {c}" for d, c in retrieved]'''

NEW = '''        for _sp in secondary:
            _text = (_sp.get("prompt") or "").strip()
            if _text:
                parts.append(
                    f"--- Binding standards from module: {_sp['name']} ---\\n"
                    f"{_text}"
                )
    if retrieved:
        lines = [f"- [{d}] {c}" for d, c in retrieved]'''


def main():
    if not TARGET.exists():
        sys.exit(f"ERROR: {TARGET} not found. Run this from ~/pqc-assistant.")

    src = TARGET.read_text()

    if "Binding standards from module" in src:
        sys.exit("Already patched. Nothing to do.")

    n = src.count(OLD)
    if n != 1:
        sys.exit(
            f"ERROR: anchor matched {n} times, expected 1. "
            "Aborting without changes."
        )

    backup = TARGET.with_suffix(".py.bak3")
    shutil.copy2(TARGET, backup)
    TARGET.write_text(src.replace(OLD, NEW))

    print(f"Patched {TARGET.name}. Backup at {backup.name}.")
    print("Secondary packs now contribute their prompts, not just their names.")
    print("\nExpect longer system prompts on multi-pack questions.")
    print("If two packs give conflicting instructions, that conflict is now")
    print("visible to the model rather than silently resolved by dropping one.")


if __name__ == "__main__":
    main()
