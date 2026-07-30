import sys
import statistics
import time
import os

import oqs

ALG = sys.argv[1] if len(sys.argv) > 1 else "ML-DSA-65"
N = 1000
MSG_SIZE = 256  # Example message size, adjust as needed

sign_times = []

with oqs.Signature(ALG) as signer:
    signer.generate_keypair()
    for i in range(N + 10):
        msg = os.urandom(MSG_SIZE)
        t0 = time.perf_counter()
        sig = signer.sign(msg)
        t1 = time.perf_counter()

        if i >= 10:                      # discard warm-up
            sign_times.append((t1 - t0) * 1e3)

print(f"{ALG}, {N} iterations, 10 discarded as warm-up")
print(f"message size {MSG_SIZE} bytes")
print(f"signature size {len(sig)} bytes")
print()
print(f"{'op':<12}{'median ms':>12}{'p95 ms':>10}{'p99 ms':>10}")
xs_sorted = sorted(sign_times)
p95 = xs_sorted[int(0.95 * len(xs_sorted))]
p99 = xs_sorted[int(0.99 * len(xs_sorted))]
print(f"{'signing':<12}{statistics.median(sign_times):>12.3f}{p95:>10.3f}{p99:>10.3f}")
