#!/usr/bin/env python3
"""
router.py — the domain router.

Now SEMANTIC: it embeds the user's question and each domain's description, then
picks the domain(s) whose meaning is closest — no keyword matching needed. Falls
back to keyword overlap only if the embedding API is unavailable, so it never
hard-fails.

Standard library only (embeddings go through embed.py / the Jina API).
"""

import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

import embed  # semantic embeddings + cosine

# ---- Config -------------------------------------------------------------
GITHUB_USER = "yatakitumbi2025-del"
REPO = "modular-ai-packs"
CDN_BASE = f"https://cdn.jsdelivr.net/gh/{GITHUB_USER}/{REPO}"
REGISTRY_URL = f"{CDN_BASE}/registry/index.json"
CACHE_FILE = Path(__file__).parent / "routing_cache.json"

# How similar a question must be to a domain to route there. Lower = more eager
# to pick a specialist; higher = more likely to use the general fallback.
# Run `python router.py` to see live scores and tune this if needed.
ROUTE_THRESHOLD = 0.25


# ---- Fetch helper -------------------------------------------------------
def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "modular-ai-router"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tokenize(text):
    return set(re.findall(r"[a-z0-9+#]+", text.lower()))


# ---- Build the routing table (with domain embeddings) -------------------
def build_routing_table(refresh=False):
    if CACHE_FILE.exists() and not refresh:
        table = json.loads(CACHE_FILE.read_text())
        if table and all(e.get("vector") for e in table):
            return table  # cache already has embeddings — use it
        # else: old keyword-only cache — fall through and rebuild

    print("Fetching registry from the CDN ...")
    registry = fetch_json(REGISTRY_URL)

    table = []
    for pack in registry.get("packs", []):
        entry = {
            "id": pack["id"], "name": pack["name"],
            "tags": pack.get("tags", []), "keywords": [],
            "profile": "", "vector": None,
        }
        desc = pack.get("description", "")
        try:
            manifest = fetch_json(f"{CDN_BASE}/{pack['manifest']}")
            routing = manifest.get("routing", {})
            entry["keywords"] = routing.get("keywords", [])
            desc = routing.get("description_for_router", desc)
        except urllib.error.HTTPError as e:
            print(f"  note: no manifest for '{pack['id']}' ({e.code})")

        if not entry["keywords"]:
            entry["keywords"] = [t.lower() for t in entry["tags"]]
        entry["profile"] = f"{entry['name']}. {desc} Keywords: {', '.join(entry['keywords'])}"
        table.append(entry)

    # Embed every domain's profile once (batched) so routing is just a cosine later.
    try:
        vectors = embed.embed([e["profile"] for e in table])
        for e, v in zip(table, vectors):
            e["vector"] = v
        print("Embedded domain profiles for semantic routing.")
    except Exception as ex:
        print(f"  (could not embed domains: {ex} — will use keyword routing)")

    CACHE_FILE.write_text(json.dumps(table, indent=2))
    return table


# ---- Routing ------------------------------------------------------------
def _keyword_route(question, table):
    words = tokenize(question)
    scores = []
    for e in table:
        matched = [kw for kw in e["keywords"] if kw.lower() in words]
        if matched:
            scores.append((e["id"], len(matched), "keywords: " + ", ".join(matched)))
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


_warned = False


def _warn_degraded(err):
    """Say it out loud, once. A silent fallback makes a broken key look like a
    tuning problem — which is exactly how it wasted an afternoon."""
    global _warned
    if not _warned:
        _warned = True
        print("\n" + "!" * 60)
        print("!! EMBEDDING FAILED — falling back to dumb keyword matching.")
        print(f"!! Reason: {err}")
        print("!! Routing and retrieval are DEGRADED until this is fixed.")
        print("!! Check:  echo ${JINA_API_KEY:0:7}   and your quota at jina.ai")
        print("!" * 60 + "\n")


def route(question, table):
    """Return ranked [(domain_id, score, detail), ...]; empty => general fallback."""
    if all(e.get("vector") for e in table):
        try:
            q_vec = embed.embed(question)
            scored = [(e["id"], embed.cosine(q_vec, e["vector"])) for e in table]
            scored.sort(key=lambda x: x[1], reverse=True)
            hits = [(d, round(s, 3), "semantic") for d, s in scored if s >= ROUTE_THRESHOLD]
            return hits
        except Exception as e:
            _warn_degraded(e)
    else:
        _warn_degraded("domain profiles have no vectors (run with --refresh)")
    return _keyword_route(question, table)


# ---- CLI ----------------------------------------------------------------
def handle(question, table):
    print(f"Q: {question}")
    results = route(question, table)
    if not results:
        print("  -> no domain matched (general fallback)")
        return
    for domain, score, detail in results:
        print(f"  -> {domain}  (score {score}; {detail})")


def main():
    refresh = "--refresh" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--refresh"]

    try:
        table = build_routing_table(refresh=refresh)
    except Exception as e:
        print(f"Could not build routing table: {e}")
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
