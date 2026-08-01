#!/usr/bin/env python3
"""
loader.py — the pack loader for the modular AI.

The router decides WHICH domain a question needs. The loader ACTIVATES that
domain: it fetches the pack's prompt, examples, tools, and knowledge, picks
the most relevant knowledge chunks, and assembles the exact context you would
hand to the local model.

Depends on router.py (must sit in the same folder). Standard library only,
so it runs in Termux with nothing to pip install.

NOTE on retrieval: real retrieval will embed the question and compare against
each chunk's vector. That needs the embedding model, which comes later. For
now `retrieve()` uses simple keyword overlap over the chunk text — a working
placeholder that we swap for vector search without changing anything else.
"""

import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import router  # reuse fetch_json, CDN_BASE, tokenize, route, build_routing_table
import embed   # real semantic embeddings (Jina) + cosine similarity

CACHE_DIR = Path(__file__).parent / "pack_cache"

# Seconds a cached pack stays valid. Set to 0 to always re-fetch
# (do this while editing packs). 300 is a reasonable steady-state value.
CACHE_TTL = 300


# ---- Fetch helper for text files (prompt.md, examples.md) --------------
def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "modular-ai-loader"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


# ---- Load (activate) a pack --------------------------------------------


# ---- Retrieve the most relevant knowledge chunks -----------------------
def _keyword_retrieve(question, chunks, k):
    """Fallback: keyword overlap, used only if a pack still has placeholder vectors."""
    q_words = router.tokenize(question)
    scored = []
    for c in chunks:
        overlap = len(q_words & router.tokenize(c.get("text", "")))
        if overlap:
            scored.append((overlap, c["text"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in scored[:k]]


def retrieve(question, chunks, k=2):
    """Return the k most semantically similar chunks to the question.

    Embeds the question with the same model the pack vectors were built with,
    then ranks chunks by cosine similarity. Falls back to keyword overlap if the
    pack still carries placeholder vectors (so an un-rebuilt pack won't break).
    """
    if not chunks:
        return []

    # Real vectors are 512-dim; placeholders were length 8. Guard against mixing.
    real_vectors = all(
        isinstance(c.get("vector"), list) and len(c["vector"]) == embed.DIM
        for c in chunks
    )
    if not real_vectors:
        router._warn_degraded("pack has placeholder vectors — run build_chunks.py --write")
        return _keyword_retrieve(question, chunks, k)

    try:
        q_vec = embed.embed(question)
    except Exception as e:
        router._warn_degraded(e)
        return _keyword_retrieve(question, chunks, k)

    scored = [(embed.cosine(q_vec, c["vector"]), c["text"]) for c in chunks]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in scored[:k]]


# ---- Assemble the model-ready context ----------------------------------


# ---- Tie router + loader together --------------------------------------


# ---- CLI ----------------------------------------------------------------
def show(result):
    if result is None:
        print("  -> no domain matched (would fall back to a general path)")
        return
    print(f"  domain: {result['domain']}")
    if result["also_matched"]:
        print(f"  also matched: {', '.join(result['also_matched'])}")
    if result["tools"]:
        print(f"  tools available: {', '.join(result['tools'])}")
    print(f"  knowledge chunks pulled in: {result['retrieved_count']}")
    print("\n  ----- CONTEXT THAT WOULD GO TO THE MODEL -----")
    print("  [SYSTEM]")
    for line in result["context"]["system"].splitlines():
        print(f"  {line}")
    print("\n  [USER]")
    print(f"  {result['context']['user']}")
    print("  ----------------------------------------------")


def main():
    refresh = "--refresh" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--refresh"]

    table = router.build_routing_table(refresh=refresh)
    print(f"Loaded {len(table)} domain(s): {', '.join(e['id'] for e in table)}\n")

    def handle(q):
        print(f"Q: {q}")
        show(build_context(q, table, refresh=refresh))

    if args:
        handle(" ".join(args))
        return

    print("Type a coding question (or 'quit'):")
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q.lower() in ("quit", "exit", ""):
            break
        handle(q)




# ===== multi-domain merging (overrides the single-pack versions above) =====
SECONDARY_RATIO = 0.75
MAX_SECONDARY = 2


def assemble(pack_data, retrieved, question, secondary=None):
    parts = [pack_data["prompt"].strip()]
    if secondary:
        names = ", ".join(p["name"] for p in secondary)
        parts.append(
            f"This single request has already been routed to multiple modules: "
            f"{pack_data['name']} plus {names}. You are answering ALL of it "
            "yourself in this one response -- do not say a part should be handled "
            "by another module or routed elsewhere, that routing already happened. "
            "Apply the combined standards of every matched module, and mark which "
            "module a specific claim draws from when it is not obvious."
        )
        for _sp in secondary:
            _text = (_sp.get("prompt") or "").strip()
            if _text:
                parts.append(
                    f"--- Binding standards from module: {_sp['name']} ---\n"
                    f"{_text}"
                )
    if retrieved:
        lines = [f"- [{d}] {c}" for d, c in retrieved]
        parts.append("Relevant reference material:\n" + "\n".join(lines))
    if pack_data.get("examples"):
        parts.append(
            "The text between <EXAMPLES> tags below shows the desired STYLE only. "
            "It is NOT the user's question. Do not answer it, repeat it, or invent "
            "new question/answer pairs from it.\n"
            "<EXAMPLES>\n" + pack_data["examples"].strip() + "\n</EXAMPLES>"
        )
    parts.append(
        "Now answer ONLY the user's actual question below, in your own words, "
        "matching the style above. Do not write 'Q:' or 'A:' labels, and do not "
        "add extra questions of your own."
    )
    return {"system": "\n\n".join(parts), "user": question}




# ===== multi-source (added by patch_multisource.py) =====
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
        age = time.time() - cache_file.stat().st_mtime
        if CACHE_TTL and age < CACHE_TTL:
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


# --- load_pack 404 tolerance ---
# A bad manifest path in a remote registry should degrade that one pack, not
# kill the whole answer. The routing table already tolerates this; load_pack
# now does too.

_load_pack_strict = load_pack


def load_pack(domain_id, refresh=False, entry=None):
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
        }


if __name__ == "__main__":
    main()
