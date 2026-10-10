---
name: process-management
description: Start, inspect, wait for readiness, read logs and stop owned host processes across independent shell calls, including servers and long-running jobs.
---

Use this skill when a program must keep running while other work proceeds or needs observation across turns. Use the feedback skill's `run` operation for a bounded command whose result is needed immediately; this helper instead persists a process and its actual status outside the shell call.

Resolve [scripts/process.py](scripts/process.py) against this SKILL.md's directory and execute it directly through the existing `shell` tool with Python 3.11 or newer. Use an absolute helper path in each call and keep the same absolute `--state-dir` and `--name`; shell variables do not persist. Put the state directory in the task workspace or another appropriate task-owned location. The helper requires a POSIX host with `waitid/WNOWAIT`, is validated on macOS, and does not support Windows or container-to-host path translation.

| Command | Result |
| --- | --- |
| `start --state-dir DIR --name NAME [--cwd DIR] -- COMMAND...` | Detached supervisor, command PID, persistent record and separate complete stdout/stderr logs. Arguments run directly; explicitly use `sh -lc '...'` when shell syntax is necessary. |
| `status --state-dir DIR --name NAME` | Actual supervisor identity and recorded lifecycle, including command exit status. A successful launch does not establish readiness or task success. |
| `wait --state-dir DIR --name NAME [--timeout 30]` | Wait for exit and return the command's actual status. Timeout returns 124 and leaves the process running. |
| `wait ... --match TEXT [--stream stdout\|stderr\|both]` | Wait for a literal substring in this run's logs while the process is running. Flush or use unbuffered output when readiness is logged. |
| `wait ... --tcp HOST PORT` | Wait until the address accepts a connection while the managed process is running. This is reachability feedback; it does not establish application correctness or identify which service owns the port. |
| `logs --state-dir DIR --name NAME [--max-chars 6000]` | Bounded log tails, truncation flags and full log paths. Use the feedback skill's text reader for relevant ranges. |
| `stop --state-dir DIR --name NAME [--grace 3 --timeout 10]` | Ask the verified supervisor to stop its own process group with TERM, then KILL if needed, and confirm cleanup. |

Inspect the returned JSON and shell exit status. Records survive independent shell calls and interrupted agent turns. Reusing an active or unconfirmed name is refused; after an ended run, the same name can start a fresh run with separate logs. Stop only task-owned processes when the task no longer needs them. Keep a process intentionally running when the user requested an ongoing service, and report its record and log locations.

The supervisor owns the command object and never signals a PID supplied in a record or by the model. It retains the direct child's PID until inherited process-group cleanup ends, preventing process-group ID reuse during cleanup. Programs must run in the foreground within that group; daemonization or creating a new session escapes this ownership boundary. If the supervisor was killed or its identity changed, `status` reports `orphaned` and `stop` refuses to guess which process to kill. Inspect and report that gap instead of repeatedly launching replacements. Commands that need interactive stdin require another workflow.

Read [references/across-turns.md](references/across-turns.md) when you need an example of starting a job, doing separate work, inspecting readiness or failure, and cleaning up in later turns.
