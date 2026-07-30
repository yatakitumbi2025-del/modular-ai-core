#!/usr/bin/env python3
"""Score routing across every pack in every enabled source.
Reads a local checkout when present (~/<repo>), else fetches from GitHub.
Rebuilds the router's profile string exactly: name, desc, keywords."""
import json, os, urllib.request, embed

BASE = "https://raw.githubusercontent.com/{}/main"
SRC = os.path.expanduser("~/pqc-assistant/sources.json")
HOME = os.path.expanduser("~")

def fetch(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)

table = []
for src in json.load(open(SRC))["sources"]:
    if not src.get("enabled", True):
        continue
    slug = src["repo"].replace("@main", "")
    local = os.path.join(HOME, slug.split("/")[-1])
    use_local = os.path.isdir(local)
    origin = "local " if use_local else "remote"

    def load(rel):
        if use_local:
            return json.load(open(os.path.join(local, rel)))
        return fetch(f"{BASE.format(slug)}/{rel}")

    try:
        registry = load("registry/index.json")
    except Exception as e:
        print(f"  skip {src['id']}: {e}")
        continue
    print(f"  {origin}  {src['id']}: {len(registry.get('packs', []))} pack(s)")
    for pack in registry.get("packs", []):
        kw, desc = [], pack.get("description", "")
        try:
            routing = load(pack["manifest"]).get("routing", {})
            kw = routing.get("keywords", [])
            desc = routing.get("description_for_router", desc)
        except Exception as e:
            print(f"    note: no manifest for {pack['id']} ({e})")
        if not kw:
            kw = [t.lower() for t in pack.get("tags", [])]
        table.append({
            "id": f"{src['id']}__{pack['id']}",
            "profile": f"{pack['name']}. {desc} Keywords: {', '.join(kw)}",
        })

APP = "pqc-application__pqc_application"
IMP = "pqc-application__pqc_implementation"
QUERIES = [
    ("Show me Kyber key exchange example",           IMP),
    ("Why does decap_secret fail in my script",      IMP),
    ("How do I enable hybrid PQC in nginx",          APP),
    ("Should we deploy hybrid or pure ML-KEM",       APP),
    ("Which should we choose for production",        APP),
    ("Is hybrid worth the cost for us",              APP),
    ("What do we pick for our TLS termination",      APP),
    ("Do we need pure ML-KEM or is hybrid enough",   APP),
]

print(f"\nScoring {len(table)} packs against {len(QUERIES)} queries\n")
vecs = embed.embed([e["profile"] for e in table] + [q for q, _ in QUERIES])
pvecs, qvecs = vecs[:len(table)], vecs[len(table):]

for (q, expected), qv in zip(QUERIES, qvecs):
    scores = sorted(((embed.cosine(qv, pv), e["id"])
                     for e, pv in zip(table, pvecs)), reverse=True)
    top, second = scores[0], scores[1]
    flag = "OK      " if top[1] == expected else "MISROUTE"
    print(f"{flag} margin={top[0]-second[0]:+.3f}  {q}")
    for s, pid in scores:
        print(f"      {s:.3f}  {pid}")
    print()
