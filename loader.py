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
def retrieve(question, chunks, k=2):
    """PLACEHOLDER: keyword-overlap ranking. Swap for vector search later."""
    q_words = router.tokenize(question)
    scored = []
    for c in chunks:
        overlap = len(q_words & router.tokenize(c.get("text", "")))
        if overlap:
            scored.append((overlap, c["text"]))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in scored[:k]]


# ---- Assemble the model-ready context ----------------------------------
def assemble(pack_data, retrieved, question):
    system = pack_data["prompt"].strip()

    if retrieved:
        system += "\n\nRelevant reference material:\n"
        for chunk in retrieved:
            system += f"- {chunk}\n"

    if pack_data.get("examples"):
        system += "\n\n" + pack_data["examples"].strip()

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
