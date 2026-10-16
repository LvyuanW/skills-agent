#!/usr/bin/env python3
"""One explicitly synthetic native Codex child/image/patch preflight."""

import asyncio
import time
from pathlib import Path
from adapters.harbor.tb4_control.control import trial_worker, verify_boundary


verify_boundary()
assets = Path(__file__).resolve().parent
output = Path("/data/tb4/runs/preflight/codex") / (
    "capability-synthetic-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
output.mkdir(parents=True)
print(f"Native capability preflight only: {output}", flush=True)
asyncio.run(trial_worker(assets / "capability-smoke-task", assets / "smoke-agent.json",
                         output, output / "controller-result.json"))
