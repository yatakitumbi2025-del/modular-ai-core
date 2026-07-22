#!/usr/bin/env python3
"""
llm.py — the ONLY piece that talks to the model.

This version calls the Groq API (groq.com) — an inference service that hosts
open-source models (Llama, Qwen, etc.) with a generous free tier. It is
OpenAI-compatible, so nothing else in the system changes.

Note: Groq (groq.com, fast inference of open models) is NOT Grok (xAI). Same
sound, different company. This file targets Groq.

SETUP (do this once in Termux):
  1. Get a free API key at https://console.groq.com  (key looks like "gsk_...")
  2. Set it as an environment variable:
        export GROQ_API_KEY="gsk_your-key-here"
     To make it permanent so you don't retype it:
        echo 'export GROQ_API_KEY="gsk_your-key-here"' >> ~/.bashrc
        source ~/.bashrc
  3. (Optional) See which model IDs are available:
        curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"
     Then set MODEL below to one of those IDs.

No pip install needed — standard library only.
"""

import os
import json
import urllib.request
import urllib.error

API_URL = "https://api.groq.com/openai/v1/chat/completions"

# A strong, free-tier model good for coding. If you get a 404, list models
# (see step 3) and pick another, e.g. a Qwen or GPT-OSS variant.
MODEL = "openai/gpt-oss-120b"


def generate(system, user, model=MODEL):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return (
            "[llm error] No API key set. In Termux run:\n"
            '  export GROQ_API_KEY="gsk_your-key-here"'
        )

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
            "Authorization": f"Bearer {key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:300]
        if e.code == 401:
            return "[llm error] 401 Unauthorized — check your GROQ_API_KEY is correct."
        if e.code == 404:
            return (
                f"[llm error] 404 — model '{model}' not available.\n"
                "List valid ids:  curl https://api.groq.com/openai/v1/models "
                '-H "Authorization: Bearer $GROQ_API_KEY"'
            )
        if e.code == 429:
            return "[llm error] 429 — hit the free rate limit. Wait a moment and retry."
        return f"[llm error] HTTP {e.code}: {detail}"
    except urllib.error.URLError as e:
        return f"[llm error] Could not reach the Groq API (internet?): {e}"
