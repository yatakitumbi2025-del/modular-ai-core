#!/usr/bin/env python3
"""
llm.py — the ONLY piece that talks to the model.

This version calls the Groq API (groq.com) — an inference service that hosts
open-source models (Llama, Qwen, GPT-OSS, etc.) with a generous free tier. It
is OpenAI-compatible, so nothing else in the system changes.

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

WHY THE tool_use_failed HANDLING EXISTS
---------------------------------------
We never send a "tools" array, so Groq treats tool_choice as "none". Some
models (notably openai/gpt-oss-120b) still emit a tool call when the pack
prompt mentions tools like `calculator`. Groq then returns:

    HTTP 400  {"error":{"code":"tool_use_failed", ...}}

That used to surface to the user as a wall of JSON. Now we retry once with an
explicit "do not call tools" instruction appended to the system prompt, which
gets a normal answer instead.
"""

import os
import json
import urllib.request
import urllib.error

API_URL = "https://api.groq.com/openai/v1/chat/completions"

# A strong, free-tier model good for coding. If you get a 404, list models
# (see step 3) and pick another, e.g. a Qwen or GPT-OSS variant.
MODEL = "llama-3.3-70b-versatile"

# Appended to the system prompt on retry after a tool_use_failed 400.
NO_TOOLS_RULE = (
    "IMPORTANT: You have NO tools, functions, or plugins available in this "
    "request. Never emit a tool call or function call of any kind. If a "
    "calculation is needed, do the arithmetic yourself and show the steps in "
    "plain text. Put all code inside ```python fenced blocks."
)


def _build_body(system, user, model):
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }


def _post(body, key):
    """Send one request. Raises urllib.error.HTTPError on non-2xx."""
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "modular-ai/1.0",
            "Authorization": f"Bearer {key}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _is_tool_use_failed(detail):
    """True if a 400 body is Groq's 'model called a tool but tools are off'."""
    try:
        err = json.loads(detail).get("error", {})
    except (ValueError, AttributeError):
        return "tool_use_failed" in (detail or "")
    return err.get("code") == "tool_use_failed"


def generate(system, user, model=MODEL):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return (
            "[llm error] No API key set. In Termux run:\n"
            '  export GROQ_API_KEY="gsk_your-key-here"'
        )

    try:
        result = _post(_build_body(system, user, model), key)
        return result["choices"][0]["message"]["content"]

    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")

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

        # The model tried to call a tool we never declared. Retry once, telling
        # it plainly that no tools exist.
        if e.code == 400 and _is_tool_use_failed(detail):
            hardened = system.rstrip() + "\n\n" + NO_TOOLS_RULE
            try:
                result = _post(_build_body(hardened, user, model), key)
                return result["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e2:
                d2 = e2.read().decode("utf-8", "ignore")[:300]
                return (
                    f"[llm error] HTTP {e2.code} after no-tools retry: {d2}\n"
                    "The pack prompt likely advertises a tool this model keeps "
                    "trying to call. Check prompt.md for that domain."
                )
            except urllib.error.URLError as e2:
                return f"[llm error] Could not reach the Groq API on retry: {e2}"

        return f"[llm error] HTTP {e.code}: {detail[:300]}"

    except urllib.error.URLError as e:
        return f"[llm error] Could not reach the Groq API (internet?): {e}"
    except (KeyError, IndexError, ValueError) as e:
        return f"[llm error] Unexpected response shape from Groq: {e}"
