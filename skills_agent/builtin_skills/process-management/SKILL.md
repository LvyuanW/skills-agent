---
name: process-management
description: >-
  Inspect, wait for readiness or completion, read logs, and stop jobs already
  registered by shell run_in_background=true. Use when an intermediate observation
  or explicit stop is needed. Starting background work belongs to background-tasks;
  ordinary foreground execution does not require this skill.
---

Use this skill to manage an existing registered job. Start all background work with the existing `shell` tool's `run_in_background=true`, following the [background-tasks skill](../background-tasks/SKILL.md). Do not use this helper's legacy `start` operation as a second launch interface. You do not need to read this skill just to start a job or receive the runtime's automatic completion result.

Resolve [scripts/process.py](scripts/process.py) to an absolute path from this SKILL.md. Copy the exact internal `state_dir` and `name` from the shell launch receipt; `display_name` is only the human-facing label. Keep those values across calls; shell variables do not persist. The helper needs Python 3.11 or newer and a POSIX host with `ps` and `waitid/WNOWAIT`.

| Operation | Observation |
| --- | --- |
| `status --state-dir DIR --name NAME` | Verified supervisor identity and recorded lifecycle/exit status |
| `logs --state-dir DIR --name NAME [--max-chars 6000]` | Bounded stdout/stderr tails and complete log paths |
| `wait --state-dir DIR --name NAME [--timeout 30]` | Wait for actual exit; observation timeout returns 124 and leaves the job running |
| `wait ... --match TEXT [--stream stdout\|stderr\|both]` | Wait for a literal log marker while the job runs; the program must flush readiness logs |
| `wait ... --tcp HOST PORT` | Observe reachability, not service identity or correct behavior |
| `stop --state-dir DIR --name NAME [--grace 3 --timeout 10]` | Request TERM, escalate to KILL for the owned process group, and confirm cleanup |

Invoke these short management commands with `shell run_in_background=false`. Give the shell call enough time for the helper's bounded wait or stop, within the synchronous 600-second maximum. An observation deadline neither extends nor replaces the job's independent execution deadline.

Stop registered work by its returned identifiers instead of broad command-line matching such as `pkill -f worker.py`. That text may also appear in a new launch helper or shell, causing it to kill the replacement while trying to stop the old worker. For example, substitute the helper's absolute path and the exact `state_dir` and internal `name` from the receipt:

```json
{
  "name": "Stop the registered worker before replacing it",
  "command": "python3 /ABS/SKILL/scripts/process.py stop --state-dir /ABS/RETURNED/jobs --name RETURNED-INTERNAL-NAME --grace 3 --timeout 10",
  "run_in_background": false,
  "timeout_seconds": 15
}
```

Check the returned state and cleanup confirmation before starting a replacement.

Only wait or poll to answer a concrete dependency, readiness or progress question. Prefer meaningful flushed markers or task-specific checks over elapsed time. TCP reachability alone does not prove your service is ready or behaving correctly. When no intermediate observation is needed, continue independent work and let the runtime deliver completion. A successful launch or a marker is not proof of full computation success.

Explicitly stop a job when it is no longer needed. Read the returned state: a `stop --timeout` expiry only ends that observation, so use `status` to confirm subsequent cleanup. Do not relaunch a duplicate merely because an observation timed out. A `task` must reach a confirmed terminal state before the Agent can finish. A `service` may remain running after a successful reply and normal CLI exit, but needs an actual readiness and behavior check before delivery. Preserve its job ID, absolute state/log paths and exact stop command for later management; stop it when no longer needed. A failed or cancelled turn cleans its newly launched services, while previously retained services remain. Benchmark rejects services.

The supervisor signals only its verified owned process group. If ownership cannot be confirmed, it reports `orphaned` and refuses to guess; do not signal an arbitrary PID or treat that state as completion. The runtime returns an `unconfirmed` diagnostic instead of terminating the Agent when supervision or cleanup is uncertain. Inspect its error, exact job identifiers, durable record and logs, then use verified `status` or `stop` observations to recover. Do not rerun the command blindly or change registration fields to manufacture cleanup confirmation. An unresolved required task remains a runtime-enforced final-reply gate until supervision is restored or actual exit and cleanup are confirmed. A verified service keeps its nonblocking lifecycle contract; an uncertain state does not prove readiness. This is the same cooperative process-group boundary as shell execution, not a hard sandbox: do not create new sessions, daemonize or delegate unmanaged work. Entry exit status does not contain every descendant's status; scripts must wait and aggregate their internal results.

Read [references/across-turns.md](references/across-turns.md) for status, logs, readiness and stop examples using a shell launch receipt.
