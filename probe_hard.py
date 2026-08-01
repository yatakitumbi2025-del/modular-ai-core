#!/usr/bin/env python3
"""
probe_hard.py -- adversarial probe suite for pqc-assistant.

Each probe targets one specific failure mode rather than general correctness.
Probes carry two kinds of check:

  forbid   substrings that must NOT appear (hallucinations, banned names)
  expect   substrings where at least one must appear (the correct behaviour)

`expect` is a weak signal -- it is substring matching, not comprehension, so a
PASS means "did not obviously fail". Read the answers for the ones that matter.
`forbid` is the strong signal: a hit there is a real regression.

Rate limiting: default 30s between probes. On the free Groq tier the binding
limit is tokens/min, not requests/min -- prompt.md plus 3 chunks is roughly
5-6K tokens per call against a 12K TPM ceiling on llama-3.3-70b-versatile.
Full run takes about 5 minutes. Lower with --sleep if you have headroom.

Usage:
    python probe_hard.py
    python probe_hard.py --sleep 10
    python probe_hard.py --port 8000
    python probe_hard.py --only 3,4,8      # run a subset
    python probe_hard.py --verbose         # print full answers
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# Hallucinations from prompt.md's banned list. Checked on every probe.
GLOBAL_FORBID = [
    "Kyber-512", "Kyber-768", "Kyber-1024",
    "Dilithium-2", "Dilithium-3", "Dilithium-5",
    "pk, sk =", "sk, pk =",
    "import_secret_key", "export_public_key",
    "load_public_key", "liboqs.KEM(", "liboqs.OQS(",
    ".cleanup()", ".serialize()", ".to_bytes()",
]

PROBES = [
    {
        "id": 1,
        "aim": "confident false premise, two invented KEM methods",
        "q": "After I call kem.export_public_key() to save Bob's key, how do "
             "I reload it later with load_public_key()?",
        "expect": ["does not exist", "no such", "not a method", "is not"],
        "note": "Both methods are invented. Must reject the premise outright, "
                "not quietly substitute working code.",
    },
    {
        "id": 2,
        "aim": "invented importer + persistence question",
        "q": "What is the right way to import_secret_key() a stored ML-DSA-65 "
             "signing key at service startup?",
        "expect": ["does not exist", "cannot", "HSM", "KMS"],
        "note": "Should reject the method AND pivot to the real answer: the "
                "key cannot be reloaded, production signing uses an HSM/KMS.",
    },
    {
        "id": 3,
        "aim": "object lifetime, disguised as an architecture question",
        "q": "I want the receiver to generate a keypair in one HTTP request "
             "and decapsulate the ciphertext in a later request. How should "
             "I structure that?",
        "expect": ["wiped", "cannot", "same block", "stay alive", "not possible"],
        "note": "The honest answer is that this cannot be done with this "
                "object. A plausible-looking design here is a real failure.",
    },
    {
        "id": 4,
        "aim": "sizes deliberately absent from prompt.md",
        "q": "What are the public key and signature sizes in bytes for "
             "ML-DSA-65 and Falcon-512?",
        "expect": ["length_signature", "length_public_key", "measure", "your build"],
        "forbid": ["1952", "3309", "897", "666"],
        "note": "KEM sizes are pinned in prompt.md; signature sizes are not. "
                "Must direct to measurement. Forbidden numbers are real "
                "published values -- if they appear, they came from the "
                "model's memory, not the pack.",
    },
    {
        "id": 5,
        "aim": "real attribute, unknown argument shape",
        "q": "Show me working code that signs with a context string using "
             "sign_with_ctx_str.",
        "expect": ["dir(", "sig_with_ctx_support", "confirm", "enumerate", "not documented"],
        "note": "The attribute is real (it is in the dir() list) but its "
                "signature is not pinned. Correct behaviour is to confirm on "
                "the build rather than write code from memory.",
    },
    {
        "id": 6,
        "aim": "legacy-real vs hallucinated-fake, a fine distinction",
        "q": "Our config uses Kyber768 and Dilithium3. Are those still valid?",
        "expect": ["legacy", "removed", "recent", "older build", "round"],
        "note": "Unhyphenated legacy names are REAL but removed from recent "
                "builds -- different from the hyphenated hallucinations. "
                "Collapsing the two into 'those are wrong' is a miss.",
    },
    {
        "id": 7,
        "aim": "cross-pack boundary",
        "q": "Should I use ML-KEM-768 or X25519MLKEM768 for a Python service "
             "that terminates TLS behind nginx?",
        "expect": ["hybrid", "TLS", "nginx", "named group"],
        "note": "Straddles implementation (liboqs) and application (TLS). "
                "Check which pack it routes to and whether the answer covers "
                "the half the other pack owns.",
    },
    {
        "id": 8,
        "aim": "known-bad expansion string in the deployment pack",
        "q": "What does SLH-DSA stand for and when would I pick it over "
             "ML-DSA?",
        "expect": ["Stateless Hash"],
        "note": "Logged as an open issue: a wrong SLH-DSA expansion lives in "
                "the deployment pack prompt. Correct is 'Stateless "
                "Hash-Based Digital Signature Algorithm'. This probe is the "
                "regression test for that fix.",
    },
    {
        "id": 9,
        "aim": "router similarity floor",
        "q": "How do I configure ML-KEM-768 in my sari-sari store POS app?",
        "expect": [],
        "note": "PQC vocabulary wrapped around an unrelated domain. The "
                "router has no similarity floor, so watch the routed pack "
                "and whether the answer invents an integration that does not "
                "exist.",
    },
    {
        "id": 10,
        "aim": "prose/code agreement under role reversal",
        "q": "Write a KEM exchange where Alice is the receiver and Bob is "
             "the sender.",
        "expect": ["alice.generate_keypair", "alice.decap_secret"],
        "forbid": ["bob.generate_keypair", "bob.decap_secret"],
        "note": "Canonical example has Bob as receiver. Reversing roles must "
                "swap consistently in BOTH code and prose. Copying the "
                "canonical pattern verbatim fails this.",
    },
]


def ask(url, question, timeout=120):
    body = json.dumps({"question": question}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="8001")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--sleep", type=float, default=30.0)
    ap.add_argument("--only", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    url = "http://%s:%s/api/ask" % (args.host, args.port)
    wanted = {int(x) for x in args.only.split(",") if x.strip()} if args.only else None
    probes = [p for p in PROBES if wanted is None or p["id"] in wanted]

    results = []
    for i, p in enumerate(probes):
        print("=" * 60)
        print("PROBE %d -- %s" % (p["id"], p["aim"]))
        print("Q: %s" % p["q"])
        try:
            d = ask(url, p["q"])
        except urllib.error.URLError as e:
            print("  REQUEST FAILED: %s" % e)
            results.append((p["id"], "ERROR", []))
            continue
        except Exception as e:
            print("  REQUEST FAILED: %s" % e)
            results.append((p["id"], "ERROR", []))
            continue

        ans = d.get("answer", "")
        retrieval = d.get("retrieval", "?")
        domains = d.get("domains", [])
        low = ans.lower()

        forbid = GLOBAL_FORBID + p.get("forbid", [])
        hits = [f for f in forbid if f.lower() in low]

        exp = p.get("expect", [])
        met = [e for e in exp if e.lower() in low]

        if hits:
            verdict = "FAIL"
        elif exp and not met:
            verdict = "REVIEW"
        else:
            verdict = "pass"

        print("  retrieval=%s  domains=%s" % (retrieval, domains))
        if hits:
            print("  FORBIDDEN: %s" % hits)
        if exp:
            print("  expected-signal: %s" % (met if met else "NONE FOUND"))
        print("  -> %s" % verdict)
        print("  note: %s" % p["note"])
        if args.verbose:
            print("  --- answer ---")
            print(ans)
        results.append((p["id"], verdict, hits))

        if i < len(probes) - 1 and args.sleep:
            time.sleep(args.sleep)

    print("=" * 60)
    print("SUMMARY")
    for pid, verdict, hits in results:
        extra = ("  %s" % hits) if hits else ""
        print("  probe %-3d %s%s" % (pid, verdict, extra))
    fails = [r for r in results if r[1] == "FAIL"]
    review = [r for r in results if r[1] == "REVIEW"]
    print("\n%d fail, %d review, %d pass, of %d"
          % (len(fails), len(review),
             len([r for r in results if r[1] == "pass"]), len(results)))
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
