# Manage a job after the shell launch receipt

Use these examples when a registered job needs a status check, log inspection, readiness check, wait, or stop. Launch the job with `shell run_in_background=true` and the [start-background-jobs skill](../../start-background-jobs/SKILL.md). These management commands operate on that existing job; they do not launch another one.

Replace `/ABS/PROCESS.py` with the helper's absolute path. Copy `/ABS/STATE` and `RETURNED_NAME` from the receipt's `state_dir` and internal `name`. Do not use `display_name`. Run each command in a separate shell call with `run_in_background=false`, allowing enough time for its configured wait or stop.

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

The wait timeout ends this observation without stopping the job or changing its execution deadline. Continue independent work and rely on automatic completion delivery when another wait would not help decide the next step.

Stop a job no longer needed:

```sh
python3 /ABS/PROCESS.py stop --state-dir /ABS/STATE --name RETURNED_NAME --grace 3 --timeout 10
```

Check that the job reached a terminal state and that cleanup is confirmed. If the stop observation times out, inspect `status` before proceeding. After receiving the actual job result, finish and check any dependent deliverables.

A `task` prevents the Agent from finishing until its exit or stop is confirmed. A `service` may remain running after a successful reply and normal CLI exit. Deliver its address and checked behavior, along with its job ID, absolute state directory, internal name, logs, and exact stop command. Its independent supervisor continues monitoring it. A new shell call or Agent turn does not require restarting it. Benchmark runs reject services.
