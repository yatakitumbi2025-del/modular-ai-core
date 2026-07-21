#!/usr/bin/env python3
"""
router.py — the domain router for the modular AI.

What it does:
  1. Fetches your live pack registry from the jsDelivr CDN.
  2. For each pack, pulls its keyword list from the pack manifest.
  3. Given a question, decides which domain(s) it belongs to.

It uses ONLY the Python standard library, so it runs in Termux with
nothing to `pip install`. The routing table is cached to a local file
so repeat runs are instant (and work offline). Use --refresh to rebuild.
"""

import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ---- Config -------------------------------------------------------------
GITHUB_USER = "yatakitumbi2025-del"
REPO = "modular-ai-packs"
CDN_BASE = f"https://cdn.jsdelivr.net/gh/{GITHUB_USER}/{REPO}"
REGISTRY_URL = f"{CDN_BASE}/registry/index.json"
CACHE_FILE = Path(__file__).parent / "routing_cache.json"


# ---- Fetch helper -------------------------------------------------------
def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "modular-ai-router"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---- Build the routing table -------------------------------------------
def build_routing_table(refresh=False):
    """Fetch the registry + each manifest, and cache the result locally."""
    if CACHE_FILE.exists() and not refresh:
        return json.loads(CACHE_FILE.read_text())

    print("Fetching registry from the CDN ...")
    registry = fetch_json(REGISTRY_URL)

    table = []
    for pack in registry.get("packs", []):
        entry = {
            "id": pack["id"],
            "name": pack["name"],
            "tags": pack.get("tags", []),
            "keywords": [],
        }
        # Try to enrich with the full keyword list from the pack manifest.
        manifest_url = f"{CDN_BASE}/{pack['manifest']}"
        try:
            manifest = fetch_json(manifest_url)
            entry["keywords"] = manifest.get("routing", {}).get("keywords", [])
            print(f"  loaded {len(entry['keywords'])} keywords for '{pack['id']}'")
        except urllib.error.HTTPError as e:
            print(f"  note: no manifest for '{pack['id']}' yet ({e.code}) — using tags only")

        # Fall back to the registry tags if the manifest had no keywords.
        if not entry["keywords"]:
            entry["keywords"] = [t.lower() for t in entry["tags"]]

        table.append(entry)

    CACHE_FILE.write_text(json.dumps(table, indent=2))
    print(f"Cached routing table to {CACHE_FILE.name}\n")
    return table


# ---- The router ---------------------------------------------------------
def tokenize(text):
    # Keep letters, digits, and + # so "c++" and "c#" survive as tokens.
    return set(re.findall(r"[a-z0-9+#]+", text.lower()))


def route(question, table):
    """Return a ranked list of (domain_id, score, matched_keywords)."""
    words = tokenize(question)
    scores = []
    for entry in table:
        matched = [kw for kw in entry["keywords"] if kw.lower() in words]
        if matched:
            scores.append((entry["id"], len(matched), matched))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


# ---- CLI ----------------------------------------------------------------
def handle(question, table):
    print(f"Q: {question}")
    results = route(question, table)
    if not results:
        print("  -> no domain matched (would fall back to a general path)")
        return
    for domain, score, matched in results:
        print(f"  -> {domain}  (score {score}: {', '.join(matched)})")


def main():
    refresh = "--refresh" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--refresh"]

    try:
        table = build_routing_table(refresh=refresh)
    except Exception as e:
        print(f"Could not build routing table: {e}")
        print("Check your internet connection and the GITHUB_USER/REPO at the top.")
        sys.exit(1)

    print(f"Loaded {len(table)} domain(s): {', '.join(e['id'] for e in table)}\n")

    if args:
        handle(" ".join(args), table)
        return

    print("Type a question (or 'quit'):")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in ("quit", "exit", ""):
            break
        handle(q, table)


if __name__ == "__main__":
    main()
