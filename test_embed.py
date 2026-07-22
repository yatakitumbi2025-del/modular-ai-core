#!/usr/bin/env python3
"""
test_embed.py — proves the embedding API works AND that it understands meaning.

Run:  python test_embed.py

Related sentences should score HIGH, unrelated ones LOW. If that holds, real
embeddings work on your phone and we can regenerate the pack vectors next.
"""

import embed

texts = [
    "how do I reverse a string in python",     # 0
    "reverse a list using slicing in python",  # 1  (related to 0 — both coding)
    "best recipe for chocolate cake",          # 2  (unrelated)
]

print("Embedding 3 sentences via Jina ...")
vecs = embed.embed(texts)

print(f"vector dimension: {len(vecs[0])}\n")
print(f"coding vs coding : {round(embed.cosine(vecs[0], vecs[1]), 3)}   (should be HIGH, ~0.5+)")
print(f"coding vs cake   : {round(embed.cosine(vecs[0], vecs[2]), 3)}   (should be LOW, ~0.2-)")
print("\nIf HIGH > LOW, embeddings understand meaning. Ready for the next step.")
