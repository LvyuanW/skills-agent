---
name: background-tasks
description: >-
  Start registered shell jobs when the tool call must return before the command
  finishes. Required for shell run_in_background=true, including retained services.
  Skip this skill for foreground commands and scripts that wait for all their
  children before returning. Use process-management to inspect or stop an existing job.
---

Use this skill before a shell command needs to keep running after its tool call returns. This applies to a computation or service, even if it has no child processes.

Use `run_in_background=false` when the shell call should wait for the command. A foreground script may run children in parallel if it waits for all of them before returning; that alone does not require this skill. To inspect or stop a job already registered by shell, use the [process-management skill](../process-management/SKILL.md).

## Start through shell

Use the existing `shell` tool with the required Boolean `run_in_background`:

```json
{
  "name": "Build analysis outputs",
  "command": "python3 -u /ABS/TASK/analyze.py",
  "run_in_background": true,
  "background_lifecycle": "task",
  "timeout_seconds": 7200
}
```

Keep the actual work in the foreground of the command you pass to shell. Shell handles the early return. Do not add a trailing `&`, use `nohup` or `setsid`, daemonize, or launch detached work. Do not use the process-management helper's legacy `start` command to launch jobs.

`run_in_background` controls whether the tool waits for the command or returns early. For background jobs, `background_lifecycle` controls when the Agent may give its final reply:

| Lifecycle | Use and final-reply condition |
| --- | --- |
| `task` (default) | A computation needed for the task. It must finish or be stopped before the Agent can finish. |
| `service` | A service intentionally kept available after delivery. Check its health and actual behavior, then deliver its address, job ID, logs and stop command; it need not exit before the Agent can finish. |

Use `background_lifecycle` only with `run_in_background=true`. The runtime rejects `service` with `run_in_background=false`. Use `service` only when the user needs it to remain available after delivery. Do not classify a required computation as a service to avoid waiting. Benchmark runs reject `service`.

For a service:

```json
{
  "name": "Serve local preview",
  "command": "python3 -u -m http.server 8765 --bind 127.0.0.1",
  "run_in_background": true,
  "background_lifecycle": "service"
}
```

The launch receipt confirms that the job started; it does not confirm completion. Save its `job_id`, `display_name`, `process_record_path`, `stdout_path`, `stderr_path`, `state_dir`, and internal `name`. Later management commands need the exact returned `state_dir` and `name`. The display name is only a label; do not use it to guess the internal name or a PID. The runtime records job ownership, keeps complete logs, and shows status and elapsed time in the UI.

For image outputs, read the [images skill](../images/SKILL.md) and declare future files in the same shell call:

```json
{
  "name": "Compute and inspect final plot",
  "command": "python3 -u /ABS/TASK/analyze.py",
  "run_in_background": true,
  "background_lifecycle": "task",
  "timeout_seconds": 7200,
  "images": [{"path": "/ABS/TASK/plot.png", "label": "Final analysis plot"}]
}
```

The runtime registers the image paths at launch, then reads and snapshots the files after the job finishes. The startup receipt has no image payload. Before exiting, the script must save each final image at no more than 1,000,000 bytes (1 MB). Declare at most ten images, using absolute host paths or local `file://` URLs. Add `source_url` when needed to retain the original source.

The `images` field controls attachments independently of stdout, including when it is `[]`. The command may still print progress logs or JSON metadata. Check the completion result for image acquisition errors. To inspect a service that remains running, capture and attach its current screenshot in a separate foreground shell call; the service does not need to exit.

There are separate deadlines:

- With `run_in_background=false`, `timeout_seconds` limits synchronous execution: default 120 seconds, maximum 600.
- With `run_in_background=true`, it limits actual execution and may be any positive finite value, including values above 600. A `task` defaults to 3600 seconds. A `service` has no execution deadline unless you supply `timeout_seconds`. Both have a separate ten-second startup acknowledgement limit.
- A later management `wait --timeout` only bounds that observation; it does not stop or extend the job.

The runtime waits for the entry process to exit, then cleans up any remaining processes in the same group before publishing the result. The result records this cleanup. An entry exit code of `0` does not prove that every child finished on its own. If the job exceeds its execution timeout, the runtime stops the group and reports the timeout, effective exit code 124, actual process return code, and saved logs.

## Own and finish the work

The entry script must wait for all required work and report failures. It must not start a computation in a child and then exit while that child is still working. This rule also applies to foreground shell commands. A script may use `&` internally to run children in parallel, but it must wait for every child, even after one fails.

The runtime delivers each completed job's status, bounded stdout/stderr, and full log paths once to the Agent that owns it. The UI may update first; the result enters the Agent's context before a subsequent model request.

A reply with no tool call does not end the run while a registered `task` is pending. The runtime waits for a result and resumes the model. Read the completed result, finish any dependent deliverables, and check them. A launch receipt or progress message does not prove completion. Investigate failures before deciding whether to retry.

If a launch receipt is missing or damaged, the runtime checks the saved registration before returning an error. It does not rerun the command. A `state: "unconfirmed"` or `status: "unconfirmed"` result means the outcome or supervision is unknown. It does not confirm that the job exited.

Read the returned error, job identifiers, record, and log paths. Use the [process-management skill](../process-management/SKILL.md) to check status, read logs, or stop the job. Do not launch a duplicate blindly, invent a PID, or edit registration fields to claim cleanup.

A required task still prevents the final reply until supervision is restored or actual exit and cleanup are confirmed. Reporting uncertainty alone does not remove that restriction. A verified service may remain running under its `service` lifecycle, but an uncertain state does not prove that it is healthy or ready to deliver.

A `service` stays under its independent supervisor after a successful Agent reply and normal CLI exit. Before delivery, check a meaningful health endpoint or the behavior the user needs. For the example server, use a foreground shell call such as:

```json
{
  "name": "Check preview response",
  "command": "python3 -c 'import urllib.request; r = urllib.request.urlopen(\"http://127.0.0.1:8765/\", timeout=5); print(r.status); print(r.read(200).decode())'",
  "run_in_background": false,
  "timeout_seconds": 10
}
```

A successful HTTP response confirms reachability and the returned content. Check any other behavior required for delivery. Give the user the service address, `job_id`, absolute state and log paths, and an exact stop command from the process-management skill. Stop the service when it is no longer needed.

A failed or cancelled turn cleans up its newly launched services and computations. Services retained by earlier successful turns stay available. Benchmark runs do not permit retained services.

Use the [process-management skill](../process-management/SKILL.md) when you need status, a log tail, a readiness check, a bounded wait, or an explicit stop before automatic completion. You do not need it just to launch a job or receive its completion result. Poll only to answer a specific question. Otherwise, continue independent work while the runtime monitors the job.

## Observe progress and replace work

If progress information will help you decide what to do next, run Python with `-u` or flush meaningful milestones such as completed batches, the current phase, and elapsed time. Let the supervisor capture stdout/stderr, then use the process-management helper to read bounded tails. Avoid `python worker.py 2>&1 | tail -35` for live progress: `tail -35` normally waits for the input to end, and it cannot show progress that the worker has not printed.

Empty logs or missing output files do not prove that a job has stalled. CPU at 100% shows activity, but does not establish useful progress or a hang. Use task-specific progress, expected work, known resource limits, and the configured execution deadline to decide whether to wait, stop, or improve and restart the job. Do not assume that the task's remaining time is known. You may stop before the execution timeout when evidence supports replacement.

To stop or replace registered work, use the process-management helper's `stop` with the exact receipt's `state_dir` and internal `name`, and confirm cleanup before starting a replacement:

```json
{
  "name": "Stop registered work before replacing it",
  "command": "python3 /ABS/PROCESS-MANAGEMENT/scripts/process.py stop --state-dir /ABS/RETURNED/jobs --name RETURNED-INTERNAL-NAME --grace 3 --timeout 10",
  "run_in_background": false,
  "timeout_seconds": 15
}
```

Resolve the helper path from the process-management skill. Copy `state_dir` and the internal `name` from the receipt. Avoid broad matching such as `pkill -f worker.py`: the same text may appear in a replacement launch's shell or helper, so the command can kill the new launch too. If stop returns an unconfirmed state, diagnose it before starting a replacement.

Example failure: `python run_refine.py 2>&1 | tail -35` showed no logs or output files while the worker used 100% CPU. About 126 seconds into a 600-second deadline, the Agent included `pkill -f run_refine.py; ...` in a replacement command. The replacement lost its launch receipt, which was consistent with the broad match also killing its helper or shell. The empty log did not establish a hang. To replace this work safely, expose flushed progress, stop the exact registered job, and confirm cleanup before launching the replacement.

## Runnable script examples

Resolve paths relative to this skill before using these examples. Write task outputs into the task workspace, not the skill directory. The scripts are examples to adapt; they do not replace the runtime supervisor.

| Script | Pattern and use |
| --- | --- |
| [scripts/no_children.py](scripts/no_children.py) | A foreground entry with no children; flushed progress and a saved result. `python3 /ABS/SKILL/scripts/no_children.py --output /ABS/TASK/result.json` |
| [scripts/sequential.py](scripts/sequential.py) | Run one child synchronously, wait for it and preserve its status. `python3 /ABS/SKILL/scripts/sequential.py -- python3 /ABS/TASK/worker.py` |
| [scripts/parallel.sh](scripts/parallel.sh) | Start two foreground children in the same process group, wait for each even if the first fails, aggregate a nonzero status, and handle cancellation with TERM, a grace period, KILL if needed, and `wait`. `bash /ABS/SKILL/scripts/parallel.sh /ABS/TASK/results` |

For the parallel example, an optional second argument supplies the first child's exit code: `bash /ABS/SKILL/scripts/parallel.sh /ABS/TASK/results 7` still waits for and saves the second child's result before returning 7. Its optional third and fourth arguments set each child's step count and delay, useful for testing cancellation. Replace the two sample commands with actual foreground commands while retaining both waits and cancellation cleanup.

## Execution boundary

This protocol uses POSIX process groups and relies on scripts cooperating. It is not a hard sandbox. Do not call `setsid`, change process groups, daemonize, or send work to an unmanaged external service; descendants can escape cleanup that way. The runtime cannot guarantee discovery of those processes.

The runtime knows the entry process's exit status. The script must wait for its children and report their results. This protocol requires the host shell with POSIX `ps` and `waitid/WNOWAIT`. The short-lived Docker `sandbox` tool cannot run these registered background jobs. The shell tool can still use them when the entire Agent runs in a benchmark container.
