#!/usr/bin/env python3
"""
embed.py — turns text into vectors, using the Jina embeddings API.

This is shared by BOTH the build script (which embeds pack knowledge) and the
loader (which embeds the user's question). They MUST use the same model and
dimension, or cosine similarity is meaningless — that's why those live here as
constants in one place.

SETUP (once, in Termux):
  1. Get a free key (10M tokens, no card) at https://jina.ai  -> "API" / API key.
     It looks like "jina_...".
  2. export JINA_API_KEY="jina_your-key-here"
     Make it permanent:
       echo 'export JINA_API_KEY="jina_your-key-here"' >> ~/.bashrc
       source ~/.bashrc

Standard library only.
"""

import os
import json
import math
import urllib.request
import urllib.error

API_URL = "https://api.jina.ai/v1/embeddings"
MODEL = "jina-embeddings-v3"
DIM = 512               # smaller = smaller pack files, still accurate. MUST be constant.
TASK = "text-matching"  # symmetric similarity — same for docs and queries


def embed(texts, model=MODEL, dim=DIM, task=TASK):
    """Embed a string or list of strings. Returns a list of vectors."""
    key = os.environ.get("JINA_API_KEY")
    if not key:
        raise RuntimeError(
            "No JINA_API_KEY set. Run:  export JINA_API_KEY=\"jina_your-key-here\""
        )

    single = isinstance(texts, str)
    if single:
        texts = [texts]

    body = {"model": model, "input": texts, "dimensions": dim, "task": task}
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": "Mozilla/5.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"Jina API HTTP {e.code}: {detail}")

    vectors = [item["embedding"] for item in result["data"]]
    return vectors[0] if single else vectors


def cosine(a, b):
    """Cosine similarity between two vectors: 1.0 = identical meaning, 0 = unrelated."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
