#!/usr/bin/env python3
"""Demo worker: the background task for the no-tool-reply hold test."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

PAYLOAD = "no-tool-reply-hold-test:payload:v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--seconds", type=float, default=45.0)
    args = parser.parse_args()

    started = time.time()
    print(f"[worker] start pid={os.getpid()} planned={args.seconds:.0f}s t0={started:.3f}", flush=True)

    steps = 4
    step_seconds = args.seconds / steps
    for step in range(1, steps + 1):
        time.sleep(step_seconds)
        print(f"[worker] progress {step}/{steps} elapsed={time.time() - started:.1f}s", flush=True)

    digest = hashlib.sha256(PAYLOAD.encode("utf-8")).hexdigest()
    total = sum(i * i for i in range(1, 1001))
    finished = time.time()

    result = {
        "payload": PAYLOAD,
        "sha256": digest,
        "sum_squares_1_1000": total,
        "pid": os.getpid(),
        "started_at": round(started, 3),
        "finished_at": round(finished, 3),
        "elapsed_seconds": round(finished - started, 3),
    }

    out_path = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")

    print(f"[worker] done elapsed={result['elapsed_seconds']}s output={out_path}", flush=True)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
