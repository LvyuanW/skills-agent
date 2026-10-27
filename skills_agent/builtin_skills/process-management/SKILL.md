---
name: process-management
description: >-
  Inspect status and logs, wait for readiness or completion, or stop a job already
  registered by shell run_in_background=true. Use when you need an intermediate
  observation or an explicit stop. Use background-tasks to launch jobs; skip this
  skill for ordinary foreground commands or automatic completion delivery.
---

Use this skill when an existing registered job needs a status check, log inspection, readiness check, bounded wait, or explicit stop.

Launch background work with `shell run_in_background=true` and the [background-tasks skill](../background-tasks/SKILL.md). Do not use this helper's legacy `start` operation. You do not need this skill just to launch a job or receive its automatic completion result. Ordinary foreground commands also do not require it.

Resolve [scripts/process.py](scripts/process.py) to an absolute path from this SKILL.md. Copy the exact `state_dir` and internal `name` from the shell launch receipt. `display_name` is only a label. Retain the returned values between calls; shell variables do not persist. The helper requires Python 3.11 or newer and a POSIX host with `ps` and `waitid/WNOWAIT`.

| Operation | Observation |
| --- | --- |
| `status --state-dir DIR --name NAME` | Verified supervisor identity and recorded lifecycle/exit status |
| `logs --state-dir DIR --name NAME [--max-chars 6000]` | Bounded stdout/stderr tails and complete log paths |
| `wait --state-dir DIR --name NAME [--timeout 30]` | Wait for actual exit; observation timeout returns 124 and leaves the job running |
| `wait ... --match TEXT [--stream stdout\|stderr\|both]` | Wait for a literal log marker while the job runs; the program must flush readiness logs |
| `wait ... --tcp HOST PORT` | Observe reachability, not service identity or correct behavior |
| `stop --state-dir DIR --name NAME [--grace 3 --timeout 10]` | Request TERM, escalate to KILL for the owned process group, and confirm cleanup |

Run these management commands with `shell run_in_background=false`. Allow enough time for the helper's wait or stop, within the shell call's 600-second maximum. A management timeout limits how long that call observes the job; it does not change the job's execution deadline.

Stop a registered job using its returned identifiers. Avoid broad matching such as `pkill -f worker.py`: that text may also appear in a new launch's shell or helper, so it can kill the replacement too. In this example, substitute the helper's absolute path and the exact `state_dir` and internal `name` from the receipt:

```json
{
  "name": "Stop the registered worker before replacing it",
  "command": "python3 /ABS/SKILL/scripts/process.py stop --state-dir /ABS/RETURNED/jobs --name RETURNED-INTERNAL-NAME --grace 3 --timeout 10",
  "run_in_background": false,
  "timeout_seconds": 15
}
```

Check the returned state and cleanup confirmation before starting a replacement.

Wait or poll only when the result will answer a specific question about dependencies, readiness, or progress. Use meaningful flushed markers or task-specific checks; elapsed time alone is weak evidence. TCP reachability does not establish service identity or correct behavior. A launch receipt or a log marker does not prove that the computation succeeded.

When you do not need an intermediate observation, continue independent work and let the runtime deliver the completion result.

Stop a job when it is no longer needed, then check the returned state. If `stop --timeout` expires, the observation has ended but cleanup may still be running. Use `status` to confirm what happened. Do not start a duplicate merely because an observation timed out.

A `task` must reach a confirmed terminal state before the Agent can finish. A `service` may stay running after a successful reply and normal CLI exit. Check its readiness and required behavior before delivery, and provide its job ID, absolute state and log paths, and exact stop command. A failed or cancelled turn cleans up services launched in that turn; previously retained services remain. Benchmark runs reject services.

The supervisor signals a process group only after verifying that it owns the group. If it cannot confirm ownership, it reports `orphaned`. Do not guess a PID to signal or treat this state as completion.

When supervision or cleanup is uncertain, the runtime returns an `unconfirmed` diagnostic and keeps the Agent running. Read its error, exact job identifiers, saved record, and logs. Use verified `status` or `stop` results to recover. Do not rerun the command blindly or edit registration fields to claim cleanup. A required task still prevents the final reply until supervision is restored or actual exit and cleanup are confirmed. A verified service may remain running under its `service` lifecycle, but an uncertain state does not prove readiness.

This protocol uses the same cooperative process-group boundary as shell execution. It is not a hard sandbox. Do not create new sessions, daemonize, or delegate unmanaged work. The entry process's exit status does not report every descendant's result; scripts must wait for their children and combine their results.

Read [references/across-turns.md](references/across-turns.md) when you need command examples for status, logs, readiness, completion, or stopping a job using its shell launch receipt.
