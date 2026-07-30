#!/usr/bin/env python3
"""
patch_multisource.py — add multi-source pack loading to the core.

Follows the pattern already in loader.py: append an override block that
redefines functions, leaving the working single-source code above intact as a
fallback. Nothing is edited in place.

What it does:
  1. Backs up router.py and loader.py (.bak-multisource)
  2. Creates sources.json if absent, with the existing packs repo as source
     "core" and the three PQC repos added
  3. Appends a multi-source block to router.py:
       - load_sources / source_base / allow_tools_map
       - build_routing_table override: loops enabled sources, isolates
         failures per source, namespaces ids as source__pack, and still
         embeds every profile in ONE batched Jina call
  4. Appends a multi-source block to loader.py:
       - load_pack override taking the routing-table entry, so it fetches
         from that source's base URL and caches to pack_cache/source__pack.json
       - build_context override that filters tools through the core's
         allowlist, so a remote manifest asking for code_runner is ignored
         unless that source is trusted

Idempotent: re-running detects the marker and stops.

Usage:
  cd ~/modular-ai-core
  python patch_multisource.py
"""

import json
import os
import shutil
import sys

MARKER = "# ===== multi-source (added by patch_multisource.py) ====="

HERE = os.path.dirname(os.path.abspath(__file__))
ROUTER = os.path.join(HERE, "router.py")
LOADER = os.path.join(HERE, "loader.py")
SOURCES = os.path.join(HERE, "sources.json")

OWNER = "yatakitumbi2025-del"

DEFAULT_SOURCES = {
    "sources": [
        {"id": "core", "repo": OWNER + "/modular-ai-packs@main",
         "enabled": True, "allow_tools": True},
        {"id": "pqc-core", "repo": OWNER + "/pqc-core@main",
         "enabled": True, "allow_tools": False},
        {"id": "pqc-experiment", "repo": OWNER + "/pqc-experiment@main",
         "enabled": True, "allow_tools": True},
        {"id": "pqc-application", "repo": OWNER + "/pqc-application@main",
         "enabled": True, "allow_tools": False},
    ]
}


ROUTER_BLOCK = '''

''' + MARKER + '''
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
'''


LOADER_BLOCK = '''

''' + MARKER + '''
# load_pack now takes the routing-table entry so it knows which repo to fetch
# from. build_context gates tools by SOURCE only: a remote pack_json may ASK
# for any tool name, and it is kept if sources.json marks that source
# allow_tools. Tool NAMES are not validated against an allowlist -- there is
# no name-to-callable dispatch yet, so an unknown name is inert text in the
# prompt. If tool dispatch is ever added, add a name allowlist HERE first.
# The old wording claimed a name allowlist that does not exist.
# pack.json may ASK for code_runner, but only a source marked allow_tools in
# sources.json actually gets it.


def _find_entry(domain_id, table):
    for e in (table or []):
        if e.get("id") == domain_id:
            return e
    return None


def load_pack(domain_id, refresh=False, entry=None):
    """Fetch and cache one pack. Cache key is the namespaced id."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{domain_id}.json"
    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text())

    if entry is None:
        # Fallback: original single-source convention.
        pack_base = f"{router.CDN_BASE}/packs/{domain_id}"
        manifest = router.fetch_json(f"{pack_base}/pack.json")
        source_id = "core"
    else:
        base = entry["base"]
        manifest_path = entry.get(
            "manifest", f"packs/{entry.get('pack_id', domain_id)}/pack.json")
        manifest = router.fetch_json(f"{base}/{manifest_path}")
        pack_base = base + "/" + manifest_path.rsplit("/", 1)[0]
        source_id = entry.get("source", "core")

    files = manifest.get("files", {})
    data = {
        "id": domain_id,
        "source": source_id,
        "name": manifest.get("name", domain_id),
        "prompt": "",
        "examples": "",
        "tools": manifest.get("tools", []),
        "chunks": [],
    }

    if files.get("prompt"):
        data["prompt"] = fetch_text(f"{pack_base}/{files['prompt']}")
    if files.get("examples"):
        data["examples"] = fetch_text(f"{pack_base}/{files['examples']}")
    if files.get("vectors"):
        try:
            vectors = router.fetch_json(f"{pack_base}/{files['vectors']}")
            data["chunks"] = vectors.get("chunks", [])
        except urllib.error.HTTPError:
            pass  # pack has no knowledge base yet - fine

    cache_file.write_text(json.dumps(data, indent=2))
    return data


def build_context(question, table, refresh=False):
    results = router.route(question, table)
    if not results:
        return None

    top_domain, top_score = results[0][0], results[0][1]
    secondary_ids = []
    for domain, score, _ in results[1:]:
        if top_score > 0 and score >= top_score * SECONDARY_RATIO:
            secondary_ids.append(domain)
        if len(secondary_ids) >= MAX_SECONDARY:
            break

    primary = load_pack(top_domain, refresh=refresh,
                        entry=_find_entry(top_domain, table))
    secondary = [load_pack(d, refresh=refresh, entry=_find_entry(d, table))
                 for d in secondary_ids]

    retrieved = [(top_domain, c)
                 for c in retrieve(question, primary["chunks"], k=2)]
    for pack in secondary:
        retrieved += [(pack["id"], c)
                      for c in retrieve(question, pack["chunks"], k=1)]

    # Tool allowlist lives HERE, in the core - never in the remote pack.
    try:
        allow = router.allow_tools_map()
    except AttributeError:
        allow = {}
    tools = []
    denied = []
    for pack in [primary] + secondary:
        pack_source = pack.get("source", "core")
        for t in pack.get("tools", []):
            n = t.get("name") if isinstance(t, dict) else t
            if not n:
                continue
            if not allow.get(pack_source, False):
                if n not in denied:
                    denied.append(f"{n} (source {pack_source} not trusted)")
                continue
            if n not in tools:
                tools.append(n)

    context = assemble(primary, retrieved, question, secondary=secondary)
    return {
        "domain": top_domain,
        "domains": [top_domain] + secondary_ids,
        "also_matched": [d for d, _, _ in results[1:]
                         if d not in secondary_ids],
        "tools": tools,
        "tools_denied": denied,
        "retrieved_count": len(retrieved),
        "context": context,
    }
'''


def main():
    for path in (ROUTER, LOADER):
        if not os.path.isfile(path):
            print("Not found: " + path)
            print("Run this from inside ~/modular-ai-core.")
            sys.exit(1)

    for path in (ROUTER, LOADER):
        if MARKER in open(path).read():
            print("Already patched: " + os.path.basename(path))
            print("Nothing to do. Delete the marker block to re-apply.")
            sys.exit(0)

    # --- backups ----------------------------------------------------------
    for path in (ROUTER, LOADER):
        backup = path + ".bak-multisource"
        shutil.copy2(path, backup)
        print("backed up " + os.path.basename(backup))

    # --- sources.json -----------------------------------------------------
    if os.path.exists(SOURCES):
        print("sources.json already exists - leaving it alone")
    else:
        with open(SOURCES, "w") as f:
            json.dump(DEFAULT_SOURCES, f, indent=2)
            f.write("\n")
        print("created sources.json with 4 sources")
        for s in DEFAULT_SOURCES["sources"]:
            print("  " + s["id"].ljust(18)
                  + ("tools" if s["allow_tools"] else "no tools"))

    # --- append blocks ----------------------------------------------------
    with open(ROUTER, "a") as f:
        f.write(ROUTER_BLOCK)
    print("appended multi-source block to router.py")

    with open(LOADER, "a") as f:
        f.write(LOADER_BLOCK)
    print("appended multi-source block to loader.py")

    print()
    print("Next, in order:")
    print("  1. python -c \"import router, loader; print('imports ok')\"")
    print("  2. rm -f routing_cache.json && rm -rf pack_cache")
    print("     (old cache is keyed by bare pack id - it must go)")
    print("  3. python core.py --refresh \"How does ML-KEM encapsulation work?\"")
    print()
    print("Expect: 4 sources listed, 7 packs total, and the route stamp")
    print("showing pqc-core__pqc_core rather than a bare id.")
    print()
    print("To undo:")
    print("  cp router.py.bak-multisource router.py")
    print("  cp loader.py.bak-multisource loader.py")


if __name__ == "__main__":
    main()
