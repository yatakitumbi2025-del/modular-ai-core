#!/usr/bin/env python3
"""
patch_cache_ttl.py -- add TTL expiry to loader.py's pack cache.

Problem: load_pack() returns a cached pack forever once written. Editing a
pack's prompt.md on the remote has no effect until pack_cache is deleted by
hand, so the model silently answers from a stale prompt.

Fix: expire cache entries by mtime. Set CACHE_TTL to 0 to bypass the cache
entirely while iterating on packs.

Run from ~/pqc-assistant:   python patch_cache_ttl.py
"""

import shutil
import sys
from pathlib import Path

TARGET = Path(__file__).parent / "loader.py"

OLD_IMPORT = "import json\nimport sys\n"
NEW_IMPORT = "import json\nimport sys\nimport time\n"

OLD_CACHE_DIR = 'CACHE_DIR = Path(__file__).parent / "pack_cache"'
NEW_CACHE_DIR = (
    'CACHE_DIR = Path(__file__).parent / "pack_cache"\n'
    "\n"
    "# Seconds a cached pack stays valid. Set to 0 to always re-fetch\n"
    "# (do this while editing packs). 300 is a reasonable steady-state value.\n"
    "CACHE_TTL = 300"
)

OLD_GUARD = (
    "    if cache_file.exists() and not refresh:\n"
    "        return json.loads(cache_file.read_text())"
)
NEW_GUARD = (
    "    if cache_file.exists() and not refresh:\n"
    "        age = time.time() - cache_file.stat().st_mtime\n"
    "        if CACHE_TTL and age < CACHE_TTL:\n"
    "            return json.loads(cache_file.read_text())"
)

EDITS = [
    ("time import", OLD_IMPORT, NEW_IMPORT),
    ("CACHE_TTL constant", OLD_CACHE_DIR, NEW_CACHE_DIR),
    ("TTL guard in load_pack", OLD_GUARD, NEW_GUARD),
]


def main():
    if not TARGET.exists():
        sys.exit(f"ERROR: {TARGET} not found. Run this from ~/pqc-assistant.")

    src = TARGET.read_text()

    if "CACHE_TTL" in src:
        sys.exit("Already patched (CACHE_TTL present). Nothing to do.")

    # Verify every anchor before writing anything.
    for label, old, _ in EDITS:
        if src.count(old) != 1:
            sys.exit(
                f"ERROR: anchor for '{label}' matched {src.count(old)} times, "
                "expected exactly 1. Aborting without changes."
            )

    backup = TARGET.with_suffix(".py.bak")
    shutil.copy2(TARGET, backup)

    for label, old, new in EDITS:
        src = src.replace(old, new)
        print(f"  applied: {label}")

    TARGET.write_text(src)
    print(f"\nPatched {TARGET.name}. Backup at {backup.name}.")
    print("Cache entries now expire after CACHE_TTL seconds (default 300).")
    print("Set CACHE_TTL = 0 in loader.py to disable caching while editing packs.")


if __name__ == "__main__":
    main()
