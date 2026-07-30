import json, os, sys
sys.path.insert(0, os.path.expanduser("~/pqc-assistant"))
import embed

HOME = os.path.expanduser("~")

def _vectors_path(pack_id):
    # pack_id arrives namespaced: source__pack
    src, _, pack = pack_id.partition("__")
    return os.path.join(HOME, src, "packs", pack, "vectors.json")

def retrieve(question, pack_id, k=3, q_vec=None):
    p = _vectors_path(pack_id)
    if not os.path.exists(p):
        return []
    chunks = json.load(open(p))["chunks"]
    if q_vec is None:
        q_vec = embed.embed(question)
    scored = [(embed.cosine(q_vec, c["vector"]), c) for c in chunks if c.get("vector")]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(round(s, 3), c["text"]) for s, c in scored[:k]]

if __name__ == "__main__":
    for s, t in retrieve(sys.argv[1], sys.argv[2]):
        print(s, "|", t[:90].replace("\n", " "))
