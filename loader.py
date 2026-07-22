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
import urllib.request
import urllib.error
from pathlib import Path

import router  # reuse fetch_json, CDN_BASE, tokenize, route, build_routing_table
import embed   # real semantic embeddings (Jina) + cosine similarity

CACHE_DIR = Path(__file__).parent / "pack_cache"


# ---- Fetch helper for text files (prompt.md, examples.md) --------------
def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "modular-ai-loader"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")


# ---- Load (activate) a pack --------------------------------------------
def load_pack(domain_id, refresh=False):
    """Fetch and cache everything needed to activate one domain pack."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"{domain_id}.json"
    if cache_file.exists() and not refresh:
        return json.loads(cache_file.read_text())

    base = f"{router.CDN_BASE}/packs/{domain_id}"
    manifest = router.fetch_json(f"{base}/pack.json")
    files = manifest.get("files", {})

    data = {
        "id": manifest["id"],
        "name": manifest["name"],
        "prompt": "",
        "examples": "",
        "tools": manifest.get("tools", []),
        "chunks": [],
    }

    if files.get("prompt"):
        data["prompt"] = fetch_text(f"{base}/{files['prompt']}")
    if files.get("examples"):
        data["examples"] = fetch_text(f"{base}/{files['examples']}")
    if files.get("vectors"):
        try:
            vectors = router.fetch_json(f"{base}/{files['vectors']}")
            data["chunks"] = vectors.get("chunks", [])
        except urllib.error.HTTPError:
            pass  # pack has no knowledge base yet — fine

    cache_file.write_text(json.dumps(data, indent=2))
    return data


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
        router._warn_degraded("pack has placeholder vectors — run build_vectors.py")
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
def assemble(pack_data, retrieved, question):
    parts = [pack_data["prompt"].strip()]

    if retrieved:
        parts.append(
            "Relevant reference material:\n"
            + "\n".join(f"- {chunk}" for chunk in retrieved)
        )

    if pack_data.get("examples"):
        # Fence the examples and state plainly that they are NOT the question.
        # Small models otherwise treat the Q/A pairs as a chat to continue,
        # leaking made-up questions into the answer.
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

    system = "\n\n".join(parts)
    return {"system": system, "user": question}


# ---- Tie router + loader together --------------------------------------
def build_context(question, table, refresh=False):
    results = router.route(question, table)
    if not results:
        return None
    top_domain = results[0][0]          # highest-scoring domain
    pack = load_pack(top_domain, refresh=refresh)
    retrieved = retrieve(question, pack["chunks"])
    context = assemble(pack, retrieved, question)
    return {
        "domain": top_domain,
        "also_matched": [d for d, _, _ in results[1:]],
        "tools": [t.get("name") for t in pack.get("tools", [])],
        "retrieved_count": len(retrieved),
        "context": context,
    }


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


if __name__ == "__main__":
    main()


# ===== multi-domain merging (overrides the single-pack versions above) =====
SECONDARY_RATIO = 0.75
MAX_SECONDARY = 2


def assemble(pack_data, retrieved, question, secondary=None):
    parts = [pack_data["prompt"].strip()]
    if secondary:
        names = ", ".join(p["name"] for p in secondary)
        parts.append(
            f"This task also draws on the {names} module(s). Apply their standards "
            "too where they are relevant, and say which domain a given claim comes "
            "from when it is not obvious."
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
    primary = load_pack(top_domain, refresh=refresh)
    secondary = [load_pack(d, refresh=refresh) for d in secondary_ids]
    retrieved = [(top_domain, c) for c in retrieve(question, primary["chunks"], k=2)]
    for pack in secondary:
        retrieved += [(pack["id"], c) for c in retrieve(question, pack["chunks"], k=1)]
    tools = []
    for pack in [primary] + secondary:
        for t in pack.get("tools", []):
            n = t.get("name")
            if n and n not in tools:
                tools.append(n)
    context = assemble(primary, retrieved, question, secondary=secondary)
    return {
        "domain": top_domain,
        "domains": [top_domain] + secondary_ids,
        "also_matched": [d for d, _, _ in results[1:] if d not in secondary_ids],
        "tools": tools,
        "retrieved_count": len(retrieved),
        "context": context,
    }
