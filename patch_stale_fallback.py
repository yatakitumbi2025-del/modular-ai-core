#!/usr/bin/env python3
"""
patch_stale_fallback.py -- make load_pack's fetch-failure path degrade to the
stale cache instead of to an empty pack.

Context: the tolerance wrapper at the bottom of loader.py catches HTTPError and
returns a structurally valid but EMPTY pack (prompt "", chunks []). Downstream
code cannot tell that apart from a real pack, so a transient network failure
produces a confident, ungrounded, route-stamped answer.

Changes:
  1. On failure, return the cached pack if one exists on disk, regardless of
     age. A stale pack beats no pack.
  2. Catch urllib.error.URLError as well as HTTPError, so DNS/connection
     failures degrade the same way instead of raising.
  3. Tag whatever is returned with "degraded" so the answer path can surface it.

Run from ~/pqc-assistant:   python patch_stale_fallback.py
"""

import shutil
import sys
from pathlib import Path

TARGET = Path(__file__).parent / "loader.py"

OLD = '''def load_pack(domain_id, refresh=False, entry=None):
    try:
        return _load_pack_strict(domain_id, refresh=refresh, entry=entry)
    except urllib.error.HTTPError as e:
        print(f"  pack '{domain_id}' unavailable (HTTP {e.code}) — "
              f"answering without it")
        return {
            "id": domain_id,
            "source": (entry or {}).get("source", "core"),
            "name": (entry or {}).get("name", domain_id),
            "prompt": "",
            "examples": "",
            "tools": [],
            "chunks": [],
        }'''

NEW = '''def load_pack(domain_id, refresh=False, entry=None):
    try:
        return _load_pack_strict(domain_id, refresh=refresh, entry=entry)
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        reason = f"HTTP {e.code}" if isinstance(e, urllib.error.HTTPError) else e.reason

        # Prefer a stale cache entry over an empty pack. The cache is only
        # stale because CACHE_TTL expired, not because it is wrong.
        cache_file = CACHE_DIR / f"{domain_id}.json"
        if cache_file.exists():
            print(f"  pack '{domain_id}' fetch failed ({reason}) — "
                  f"using cached copy")
            try:
                data = json.loads(cache_file.read_text())
                data["degraded"] = "stale-cache"
                return data
            except (json.JSONDecodeError, OSError) as ce:
                print(f"  cached copy unreadable ({ce}) — answering without it")

        print(f"  pack '{domain_id}' unavailable ({reason}) — "
              f"answering without it")
        return {
            "id": domain_id,
            "source": (entry or {}).get("source", "core"),
            "name": (entry or {}).get("name", domain_id),
            "prompt": "",
            "examples": "",
            "tools": [],
            "chunks": [],
            "degraded": "no-pack",
        }'''


def main():
    if not TARGET.exists():
        sys.exit(f"ERROR: {TARGET} not found. Run this from ~/pqc-assistant.")

    src = TARGET.read_text()

    if '"degraded"' in src:
        sys.exit("Already patched (degraded key present). Nothing to do.")

    n = src.count(OLD)
    if n != 1:
        sys.exit(
            f"ERROR: anchor matched {n} times, expected 1. "
            "Aborting without changes."
        )

    backup = TARGET.with_suffix(".py.bak2")
    shutil.copy2(TARGET, backup)
    TARGET.write_text(src.replace(OLD, NEW))

    print(f"Patched {TARGET.name}. Backup at {backup.name}.")
    print("Fetch failures now fall back to the stale cache when one exists.")
    print("\nNOTE: packs returned this way carry a 'degraded' key.")
    print("Nothing reads it yet — surfacing it in the UI is the next step.")


if __name__ == "__main__":
    main()
