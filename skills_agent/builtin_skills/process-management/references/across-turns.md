# Process work across separate shell calls

Replace `/ABS/PROCESS.py` with the resolved helper path and `/ABS/TASK` with the task workspace. Each block is a separate shell call; the process record, rather than a remembered shell variable, connects them.

Start an existing foreground program that logs a readiness marker:

```sh
python3 /ABS/PROCESS.py start --state-dir /ABS/TASK/.processes --name worker --cwd /ABS/TASK -- python3 -u worker.py
```

Read the launch observation. If startup failed, inspect the actual diagnostic before changing the command. If it launched, continue independent work while it runs. When its readiness is needed:

```sh
python3 /ABS/PROCESS.py wait --state-dir /ABS/TASK/.processes --name worker --match READY --stream stdout --timeout 20
```

A marker is useful only when that program defines it meaningfully. For a local service, use `--tcp 127.0.0.1 PORT` when reachability is the appropriate next observation, then make a task-specific request to examine actual behavior. A wait timeout leaves the process running; inspect its state and output before deciding whether to wait longer, correct the program or stop it.

```sh
python3 /ABS/PROCESS.py logs --state-dir /ABS/TASK/.processes --name worker --max-chars 4000
```

For a batch job, omit a readiness condition to wait for its real exit:

```sh
python3 /ABS/PROCESS.py wait --state-dir /ABS/TASK/.processes --name worker --timeout 30
```

For a persistent service, continue work and inspect its output or artifacts through suitable feedback. Once this task no longer needs the service:

```sh
python3 /ABS/PROCESS.py stop --state-dir /ABS/TASK/.processes --name worker --grace 3 --timeout 10
```

Verify the returned terminal state. If this call times out, the stop request still exists; use `status` to see whether cleanup subsequently finished. Preserve an ongoing service when requested, and record its absolute state directory and name so a later turn can resume inspection without starting a duplicate.
