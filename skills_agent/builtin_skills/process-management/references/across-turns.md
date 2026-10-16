# Manage a job after the shell launch receipt

Start the job using `shell` with `run_in_background=true` and the [background-tasks skill](../../background-tasks/SKILL.md). The helper in this reference observes or stops that existing job; it does not start another one.

Replace `/ABS/PROCESS.py` with the resolved helper path. Replace `/ABS/STATE` and `RETURNED_NAME` with the exact `state_dir` and safe internal `name` from the launch receipt. Do not substitute the human-facing `display_name`. Each block below is a separate shell call with `run_in_background=false`.

Read current ownership and execution state:

```sh
python3 /ABS/PROCESS.py status --state-dir /ABS/STATE --name RETURNED_NAME
```

Get a bounded log tail while retaining full log paths:

```sh
python3 /ABS/PROCESS.py logs --state-dir /ABS/STATE --name RETURNED_NAME --max-chars 4000
```

Before a dependent step, wait for a meaningful flushed marker defined by the program:

```sh
python3 /ABS/PROCESS.py wait --state-dir /ABS/STATE --name RETURNED_NAME --match "ready" --stream stdout --timeout 20
```

For a local server, TCP reachability can be one readiness observation:

```sh
python3 /ABS/PROCESS.py wait --state-dir /ABS/STATE --name RETURNED_NAME --tcp 127.0.0.1 8765 --timeout 20
```

Check a task-specific endpoint or result as well; neither the marker nor TCP acceptance proves correct behavior. To wait for actual exit instead:

```sh
python3 /ABS/PROCESS.py wait --state-dir /ABS/STATE --name RETURNED_NAME --timeout 30
```

Its timeout only bounds the observation; the job keeps running under its original execution deadline. You can continue independent work and rely on automatic completion delivery rather than repeatedly waiting.

Stop a job no longer needed:

```sh
python3 /ABS/PROCESS.py stop --state-dir /ABS/STATE --name RETURNED_NAME --grace 3 --timeout 10
```

Confirm a terminal state. If this observation times out, inspect `status` before proceeding. Complete and verify any dependent deliverables after receiving the runtime's actual job result. A `task` blocks final completion until it has exited or been stopped. A `service` may remain after a successful reply and normal CLI exit: deliver its address and verified behavior together with its job ID, absolute state directory, internal name, logs and this exact stop command. The independent supervisor continues monitoring it; do not restart it merely because a new shell call or Agent turn begins. Benchmark rejects services.
