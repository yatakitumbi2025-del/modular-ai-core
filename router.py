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
CDN_BASE = f"https://raw.githubusercontent.com/{GITHUB_USER}/{REPO}/main"
REGISTRY_URL = f"{CDN_BASE}/registry/index.json"
CACHE_FILE = Path(__file__).parent / "routing_cache.json"

# How similar a question must be to a domain to route there. Lower = more eager
# to pick a specialist; higher = more likely to use the general fallback.
# Run `python router.py` to see live scores and tune this if needed.
ROUTE_THRESHOLD = 0.25
MARGIN_FLOOR = 0.10
MARGIN_RATIO = 2.0


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


CHITCHAT = {
    "thanks", "thank you", "thanks that helped", "ty", "thx",
    "hello", "hi", "hey", "yo", "ok", "okay", "cool", "nice",
    "got it", "great", "perfect", "sure", "yes", "no", "yep", "nope",
    "good morning", "good night", "bye", "goodbye", "sorry",
    "that helped", "it worked", "works now", "awesome",
}

def _is_chitchat(question):
    q = question.lower().strip().strip("!.?,")
    if q in CHITCHAT:
        return True
    words = q.split()
    if len(words) <= 4 and not any(len(w) > 8 for w in words):
        if any(w in {"thanks","thank","hello","hi","hey","ok","okay",
                     "cool","nice","great","bye","sorry","yes","no"}
               for w in words):
            return True
    return False


def route(question, table):
    """Return ranked [(domain_id, score, detail), ...]; empty => general fallback."""
    if _is_chitchat(question):
        return []
    if all(e.get("vector") for e in table):
        try:
            q_vec = embed.embed(question)
            scored = [(e["id"], embed.cosine(q_vec, e["vector"])) for e in table]
            scored.sort(key=lambda x: x[1], reverse=True)
            hits = [(d, round(s, 3), "semantic") for d, s in scored if s >= ROUTE_THRESHOLD]
            if not hits and scored:
                lead_id, lead = scored[0]
                second = scored[1][1] if len(scored) > 1 else 0.0
                if lead >= MARGIN_FLOOR and lead >= MARGIN_RATIO * max(second, 1e-6):
                    hits = [(lead_id, round(lead, 3),
                             f"margin: {round(lead,3)} vs {round(second,3)}")]
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


# ===== multi-source (added by patch_multisource.py) =====
# Loads packs from several repos instead of one. The single-source functions
# above still work and are used as the fallback if sources.json is missing.
#
# Entry ids are namespaced source__pack so two repos can both ship a "math"
# pack without colliding in the route table or the pack cache.

SOURCES_FILE = Path(__file__).parent / "sources.json"

DEFAULT_SOURCES = [
    {"id": "core", "repo": f"{GITHUB_USER}/{REPO}@main",
     "enabled": True, "allow_tools": True},
]


def load_sources():
    """Read sources.json. Falls back to the original single source."""
    if SOURCES_FILE.exists():
        try:
            data = json.loads(SOURCES_FILE.read_text())
            srcs = data.get("sources") if isinstance(data, dict) else data
            if srcs:
                return srcs
        except Exception as e:
            print(f"  sources.json unreadable ({e}) - using default source")
    return DEFAULT_SOURCES


def source_base(repo):
    """'owner/repo@branch' -> raw.githubusercontent base URL."""
    spec = repo.strip()
    branch = "main"
    if "@" in spec:
        spec, branch = spec.rsplit("@", 1)
    return f"https://raw.githubusercontent.com/{spec}/{branch}"


def allow_tools_map():
    """source id -> whether the core trusts that source with tools."""
    return {s["id"]: bool(s.get("allow_tools")) for s in load_sources()}


def _entries_for_source(src):
    """Build routing-table entries for one source. Raises if the repo is dead."""
    sid = src["id"]
    base = source_base(src["repo"])
    registry = fetch_json(f"{base}/registry/index.json")

    entries = []
    for pack in registry.get("packs", []):
        pid = pack["id"]
        manifest_path = pack.get("manifest", f"packs/{pid}/pack.json")
        entry = {
            "id": f"{sid}__{pid}",
            "pack_id": pid,
            "source": sid,
            "base": base,
            "manifest": manifest_path,
            "name": pack.get("name", pid),
            "tags": pack.get("tags", []),
            "keywords": [],
            "profile": "",
            "vector": None,
        }
        desc = pack.get("description", "")
        try:
            manifest = fetch_json(f"{base}/{manifest_path}")
            routing = manifest.get("routing", {})
            entry["keywords"] = routing.get("keywords", [])
            desc = routing.get("description_for_router", desc)
        except urllib.error.HTTPError as e:
            print(f"  note: no manifest for '{entry['id']}' ({e.code})")

        if not entry["keywords"]:
            entry["keywords"] = [t.lower() for t in entry["tags"]]

        # Same profile shape as the single-source builder: the router embeds
        # name + description + keywords, not the description alone.
        entry["profile"] = (
            f"{entry['name']}. {desc} Keywords: {', '.join(entry['keywords'])}"
        )
        entries.append(entry)
    return entries


def build_routing_table(refresh=False):
    """Multi-source table build. One dead repo must not kill startup."""
    if CACHE_FILE.exists() and not refresh:
        try:
            table = json.loads(CACHE_FILE.read_text())
        except Exception:
            table = None
        # Require "source" too, so an old single-source cache rebuilds.
        if table and all(e.get("vector") and e.get("source") for e in table):
            return table

    print("Fetching registries from all enabled sources ...")
    table = []
    failed = []
    for src in load_sources():
        if not src.get("enabled", True):
            print(f"  source '{src.get('id')}': disabled, skipping")
            continue
        try:
            got = _entries_for_source(src)
            table.extend(got)
            print(f"  source '{src['id']}': {len(got)} pack(s)")
        except Exception as e:
            failed.append(src.get("id"))
            print(f"  source '{src.get('id')}' FAILED: {e}")

    if not table:
        raise RuntimeError("no packs loaded from any source")
    if failed:
        print(f"  continuing without: {', '.join(str(f) for f in failed)}")

    # One batched embed for every profile across every source.
    try:
        vectors = embed.embed([e["profile"] for e in table])
        for e, v in zip(table, vectors):
            e["vector"] = v
        print(f"Embedded {len(table)} domain profiles for semantic routing.")
    except Exception as ex:
        print(f"  (could not embed domains: {ex} - will use keyword routing)")

    CACHE_FILE.write_text(json.dumps(table, indent=2))
    return table
