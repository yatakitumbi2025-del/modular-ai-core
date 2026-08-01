#!/usr/bin/env python3
"""
patch_vectors_nocache_v1.py -- stop caching a pack whose vectors fetch failed

THE BUG
-------
In load_pack (loader.py ~244-251):

    if files.get("vectors"):
        try:
            vectors = router.fetch_json(f"{pack_base}/{files['vectors']}")
            data["chunks"] = vectors.get("chunks", [])
        except urllib.error.HTTPError:
            pass  # pack has no knowledge base yet - fine

    cache_file.write_text(json.dumps(data, indent=2))
    return data

That comment is only true the first time a brand-new pack is loaded. On a pack that
DOES declare a vectors file, a transient 5xx or CDN hiccup leaves data["chunks"] as
[], and the very next line writes that knowledge-less pack to disk. It is now the
cached pack for the whole CACHE_TTL.

Consequences:
  * load_pack "succeeded", so the stale-cache fallback in the 427 wrapper never runs
    and "degraded" is never set.
  * The assistant answers from prompt.md alone, with zero retrieved knowledge, while
    presenting a completely normal answer.
  * It stays that way until the TTL expires -- unlike the original fallback bug, which
    recovered on the next request.

It also catches HTTPError only, so DNS/connection failures escape load_pack entirely.

THE FIX
-------
1. Catch URLError as well as HTTPError.
2. Record the failure and DO NOT write the cache file when it happens. A transient
   error should cost a re-fetch on the next query, not an hour of empty answers.
3. Set data["degraded"] = "vectors-fetch-failed" so it rides the reporting channel
   added by patch_surface_degraded_v1.py and shows up as |stale:<pack> in the answer.
4. Warn via router._warn_degraded, the same function that prints the embedding-failed
   banner, rather than inventing a new reporting mechanism.

A genuinely new pack with no vectors file is unaffected: files.get("vectors") is
falsy, the whole block is skipped, and the pack caches normally as before.

Run from ~/pqc-assistant:   python patch_vectors_nocache_v1.py
Revert with:                git checkout loader.py
"""

import ast
import subprocess
import sys
from pathlib import Path

SRC = Path("loader.py")

OLD = '''    if files.get("vectors"):
        try:
            vectors = router.fetch_json(f"{pack_base}/{files['vectors']}")
            data["chunks"] = vectors.get("chunks", [])
        except urllib.error.HTTPError:
            pass  # pack has no knowledge base yet - fine

    cache_file.write_text(json.dumps(data, indent=2))
'''

NEW = '''    _vec_failed = False
    if files.get("vectors"):
        try:
            vectors = router.fetch_json(f"{pack_base}/{files['vectors']}")
            data["chunks"] = vectors.get("chunks", [])
        except (urllib.error.HTTPError, urllib.error.URLError) as _ve:
            # The pack DECLARED a vectors file and we failed to get it. That is a
            # transient fetch problem, not "no knowledge base yet". Caching now
            # would pin an empty pack for the whole TTL, so do not cache.
            _vec_failed = True
            data["degraded"] = "vectors-fetch-failed"
            router._warn_degraded(
                f"vectors fetch failed for {domain_id} ({_ve}) - "
                f"not caching, will retry next request"
            )

    if not _vec_failed:
        cache_file.write_text(json.dumps(data, indent=2))
'''


def fail(msg, restore=None):
    if restore is not None:
        SRC.write_text(restore)
        print("  (loader.py reverted)")
    print(f"\nABORT: {msg}")
    sys.exit(1)


def run_py(code):
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return r.stdout.strip(), r.stderr.strip(), r.returncode


def main():
    if not SRC.exists():
        fail("loader.py not found -- run this from ~/pqc-assistant")

    original = SRC.read_text()

    print("== Pre-flight ==")
    out, err, rc = run_py("import loader; print('OK')")
    if rc != 0:
        fail(f"loader.py does not import cleanly BEFORE patching:\n{err}")
    print("  loader.py imports cleanly")

    if "_vec_failed" in original:
        fail("loader.py already contains _vec_failed -- already patched?")

    print("\n== Anchor verification ==")
    n = original.count(OLD)
    if n == 0:
        # Most likely cause: the comment text or whitespace differs slightly.
        print("  anchor not found. Nearby lines for comparison:")
        for i, l in enumerate(original.splitlines(), 1):
            if 'files.get("vectors")' in l or "cache_file.write_text" in l:
                print(f"    {i}: {l!r}")
        fail("anchor NOT FOUND -- the block differs from what this patch expects. "
             "Compare the printed lines above against the OLD string in this script.")
    if n > 1:
        fail(f"anchor appears {n}x -- not unique")
    print("  OK  vectors-fetch block found exactly once")

    # router._warn_degraded must actually exist, since NEW calls it.
    out, err, rc = run_py(
        "import router; print('HAS' if hasattr(router,'_warn_degraded') else 'MISSING')"
    )
    if out != "HAS":
        fail("router._warn_degraded not found -- this patch calls it")
    print("  OK  router._warn_degraded exists")

    print("\n== Applying ==")
    patched = original.replace(OLD, NEW, 1)
    try:
        ast.parse(patched)
    except SyntaxError as e:
        fail(f"result is not valid Python: {e}")
    SRC.write_text(patched)
    print(f"  loader.py: {len(original.splitlines())} -> "
          f"{len(patched.splitlines())} lines")

    print("\n== Post-flight ==")
    out, err, rc = run_py("import loader; print('OK')")
    if rc != 0:
        fail(f"loader.py FAILED to import after patching:\n{err}", restore=original)
    print("  loader.py still imports cleanly")

    # Behavioural probe: force the vectors fetch to raise, then assert that
    # (a) no cache file was written and (b) degraded was set.
    probe = '''
import json, urllib.error, tempfile, pathlib, sys
import loader, router

tmp = pathlib.Path(tempfile.mkdtemp())
loader.CACHE_DIR = tmp

_real = router.fetch_json
def boom(url):
    if "vector" in url.lower():
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", {}, None)
    return {"id": "probe", "name": "probe",
            "files": {"prompt": "prompt.md", "vectors": "vectors.json"},
            "tools": []}
router.fetch_json = boom
loader.fetch_text = lambda url: "PROMPT BODY"
router._warn_degraded = lambda *a, **k: None
router.CDN_BASE = "https://example.invalid"

try:
    data = loader._load_pack_strict("probe_pack", refresh=True,
                                    entry={"base": "https://example.invalid",
                                           "source": "probe"})
except Exception as e:
    print("PROBE_RAISED:" + type(e).__name__ + ":" + str(e)); sys.exit()

wrote = list(tmp.glob("*.json"))
if wrote:
    print("STILL_CACHED:" + ",".join(p.name for p in wrote))
elif data.get("degraded") != "vectors-fetch-failed":
    print("NO_FLAG:" + repr(data.get("degraded")))
else:
    print("PROBE_OK: not cached, degraded=" + data["degraded"])
'''
    out, err, rc = run_py(probe)
    if out.startswith("PROBE_OK"):
        print(f"  {out}")
    elif out.startswith(("STILL_CACHED", "NO_FLAG")):
        fail(f"behavioural probe failed: {out}", restore=original)
    else:
        # The probe stubs a lot; a mismatch here is more likely the stub than
        # the patch. Say so plainly rather than claiming success.
        print("  WARN: probe could not run; patch applied but UNVERIFIED")
        print("        " + (out or (err.splitlines() or ["(no stderr)"])[-1]))

    print("\nDone. Verify:")
    print("  git diff loader.py")
    print("  python loader.py \"what is ML-KEM\"")
    print("")
    print("Expect unchanged output -- this only alters the failure path.")
    print("If it behaves:  git add -A && git commit -m "
          "'do not cache packs whose vectors fetch failed'")
    print("If it does not: git checkout loader.py")


if __name__ == "__main__":
    main()
