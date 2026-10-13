---
name: process-management
description: Keep a server or long-running job observable across shell calls using saved process records, readiness checks and logs, then stop its owned processes when work ends.
---

Use a saved process reference when a program must run while other work proceeds. Start the task's own program through [scripts/process.py](scripts/process.py), then use its returned state and logs rather than assuming launch means readiness. A bounded command needed immediately can instead use the feedback skill's `run` operation.

Read or adapt the script to understand the method; its process registration and ownership protocol are necessary for the agent's automatic cleanup. Direct use requires Python 3.11 or newer on POSIX with `waitid/WNOWAIT`; it is validated on macOS and does not support Windows or container-to-host path translation. Resolve its absolute path from this SKILL.md. Keep the same absolute `--state-dir` and `--name` across calls; shell variables do not persist.

| Operation | Result |
| --- | --- |
| `start --state-dir DIR --name NAME [--cwd DIR] [--persistent] -- COMMAND...` | Detached supervisor, command PID, saved record and complete stdout/stderr logs |
| `status --state-dir DIR --name NAME` | Verified supervisor identity and recorded lifecycle/exit status |
| `wait --state-dir DIR --name NAME [--timeout 30]` | Actual command status; timeout returns 124 and leaves it running |
| `wait ... --match TEXT [--stream stdout\|stderr\|both]` | Wait for a literal log marker while the process runs; flush readiness logs |
| `wait ... --tcp HOST PORT` | Observe reachability, not service identity or correct behavior |
| `logs --state-dir DIR --name NAME [--max-chars 6000]` | Bounded tails and complete log paths |
| `stop --state-dir DIR --name NAME [--grace 3 --timeout 10]` | TERM then KILL for the owned process group and confirmation of cleanup |

Arguments execute directly; use `sh -lc '...'` for shell syntax. Commands must remain foreground members of the supervisor's process group. Daemonizing or starting a new session escapes its ownership. Commands needing interactive stdin require another approach. An active or unconfirmed name cannot be reused; an ended run may be replaced with fresh logs. Inspect readiness and actual task behavior separately, and stop only task-owned work.

Within the agent, registered jobs belong to the turn that started them, including subagent jobs. Failed/cancelled turns stop their new jobs. Successful CLI turns keep them available across turns; leaving the CLI stops them. Only a user-requested ongoing service should use `--persistent`, and it survives exit only after its creating turn succeeds. Benchmark runs forbid persistence and clean up all registered jobs on success, failure or interruption. Outside the agent, use `stop` explicitly. Do not detach to evade this ownership.

The supervisor retains its direct child's PID until group cleanup ends and never signals a PID supplied by the model or a record. If ownership cannot be confirmed, status is `orphaned` and stop refuses to guess. Report that gap rather than launching duplicates. Preserve the absolute state directory, name and log paths for an intentionally continuing service. Read [references/across-turns.md](references/across-turns.md) for a start/observe/stop example.
