# 后台任务与服务

## v1 · 2026-10-17 · 后台任务与服务

来源：工作区未提交；`skills_agent/builtin_skills/background-tasks/SKILL.md`。

- 发现的问题：后台执行模式、服务保留及进程管理的职责需要分开说明。
- 本版本目标：通过现有 shell 启动，提供可观察执行与协作等待子任务的说明。
- 本版本效果：计算任务等待完成，服务核实就绪后可交付；状态、日志及停止由进程管理技能提供。范例已离线验证，模型表现待观察。

### 完整提示词

````markdown
---
name: background-tasks
description: >-
  Required when a shell call must return control while its command keeps running.
  Use shell run_in_background=true for registered computations or retained services,
  independent deadlines and automatic completion results. Not needed for foreground commands or internal
  parallel work whose entry script waits for every child before returning.
---

Use this skill before starting a command that must continue after its shell tool call returns. A background task is defined by that control handoff, not by its duration, use of subprocesses or whether it is a server. A task can have no children. Ordinary foreground execution uses `run_in_background=false` and does not require this skill; a foreground script that starts parallel children and waits for all of them is still foreground execution.

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

Keep the actual work in the foreground of this command. Do not add a trailing `&`, `nohup`, `setsid`, self-daemonization, or a detached launcher. Shell already provides the asynchronous handoff. Do not call the process-management helper's `start` command as an alternative launch interface.

`run_in_background` chooses whether the tool waits or returns early. The optional `background_lifecycle` chooses whether the Agent must wait for the job before its final reply:

| Lifecycle | Use and final-reply condition |
| --- | --- |
| `task` (default) | A computation needed for the task. It must finish or be stopped before the Agent can finish. |
| `service` | A service intentionally kept available after delivery. Check its health and actual behavior, then deliver its address, job ID, logs and stop command; it need not exit before the Agent can finish. |

Use `background_lifecycle` only with `run_in_background=true`; `run_in_background=false` with `service` is rejected. Do not label a required computation as a service to bypass waiting. Benchmark runs reject `service` explicitly so no work leaks between benchmark tasks.

For a service:

```json
{
  "name": "Serve local preview",
  "command": "python3 -u -m http.server 8765 --bind 127.0.0.1",
  "run_in_background": true,
  "background_lifecycle": "service"
}
```

The launch receipt confirms startup only. It contains a stable `job_id`, the human-facing `display_name`, `process_record_path`, `stdout_path`, `stderr_path`, and the internal `state_dir` and safe `name` needed for later management. Preserve these returned values; do not derive the internal name from the display name or invent a PID. The runtime records ownership, saves complete logs, and displays the job's status and elapsed time in the UI.

There are separate deadlines:

- With `run_in_background=false`, `timeout_seconds` limits synchronous execution: default 120 seconds, maximum 600.
- With `run_in_background=true`, it limits actual execution and may be any positive finite value, including values above 600. A `task` defaults to 3600 seconds. A `service` has no execution deadline unless you supply `timeout_seconds`. Both have a separate five-second startup acknowledgement limit.
- A later management `wait --timeout` only bounds that observation; it does not stop or extend the job.

The runtime waits for the entry process. After it exits, the runtime cleans remaining processes in the same process group before publishing the result. Cleanup of leftover processes is recorded explicitly; entry exit code `0` alone must not be read as every child naturally finishing. Job timeout stops the group and reports timeout, effective exit code 124, the actual process return code, and saved logs.

## Own and finish the work

Your entry script must wait for all required internal work and report failures. Do not start real computation in a child and let its parent exit early. These rules apply to synchronous shell commands as well as background jobs. `&` is allowed *inside* a script for parallel children only when that script waits for every child, including after another child fails.

The runtime delivers each completed job's actual status, bounded stdout/stderr and full log paths once into the owning Agent's working context. The UI can update before that delivery; results enter context at a safe boundary before the next model request. A no-tool reply does not end the run while a registered `task` is pending; the runtime waits for a result and then resumes the model. After completion, inspect the result, produce remaining deliverables and verify them. A launch receipt or progress message is not completion evidence. Failure is evidence to investigate, not permission to retry without limit.

A `service` remains under its independent supervisor after a successful Agent reply and normal CLI exit. Startup is not readiness: check a meaningful health endpoint or task-specific behavior before delivery. For the example server, use a foreground shell call such as:

```json
{
  "name": "Check preview response",
  "command": "python3 -c 'import urllib.request; r = urllib.request.urlopen(\"http://127.0.0.1:8765/\", timeout=5); print(r.status); print(r.read(200).decode())'",
  "run_in_background": false,
  "timeout_seconds": 10
}
```

A successful HTTP response proves reachability and response content; also check what matters for the delivered service. Hand over its address, `job_id`, absolute state/log paths and an exact stop command from the process-management skill. Stop it when no longer needed. A failed or cancelled turn cleans its newly launched services as well as computations; services retained by earlier successful turns stay available. Benchmark does not permit service retention.

Use the [process-management skill](../process-management/SKILL.md) only when you need an intermediate status, log tail, readiness check, bounded wait or explicit stop. It is not required merely to launch a job or receive its automatic completion result. Avoid repeated polls without a question the observation can answer; continue independent work while the runtime monitors the job.

## Runnable script examples

Resolve paths relative to this skill before using these examples. Write task outputs into the task workspace, not the skill directory. The scripts are examples to adapt; they do not replace the runtime supervisor.

| Script | Pattern and use |
| --- | --- |
| [scripts/no_children.py](scripts/no_children.py) | A foreground entry with no children; flushed progress and a saved result. `python3 /ABS/SKILL/scripts/no_children.py --output /ABS/TASK/result.json` |
| [scripts/sequential.py](scripts/sequential.py) | Run one child synchronously, wait for it and preserve its status. `python3 /ABS/SKILL/scripts/sequential.py -- python3 /ABS/TASK/worker.py` |
| [scripts/parallel.sh](scripts/parallel.sh) | Start two foreground children in the same process group, wait for each even if the first fails, aggregate a nonzero status, and handle cancellation with TERM, a grace period, KILL if needed, and `wait`. `bash /ABS/SKILL/scripts/parallel.sh /ABS/TASK/results` |

For the parallel example, an optional second argument supplies the first child's exit code: `bash /ABS/SKILL/scripts/parallel.sh /ABS/TASK/results 7` still waits for and saves the second child's result before returning 7. Its optional third and fourth arguments set each child's step count and delay, useful for testing cancellation. Replace the two sample commands with actual foreground commands while retaining both waits and cancellation cleanup.

## Execution boundary

This is a native POSIX process-group contract, not a hard sandbox. Descendants that call `setsid`, change process groups, daemonize or delegate execution to an external service can escape it; do not use those techniques. Cleanup cannot promise to discover such processes. The runtime knows the entry process's exit status, not every descendant's status; the script must wait and summarize its children. This protocol needs the host shell environment with POSIX `ps` and `waitid/WNOWAIT`. The short-lived Docker `sandbox` tool does not support these registered background jobs, although shell works when the whole Agent runs in a benchmark container.
````

## v2 · 2026-10-17 · 独立10秒启动确认

来源：工作区未提交；`skills_agent/builtin_skills/background-tasks/SKILL.md`。

- 发现的问题：后台启动确认原为5秒，繁忙环境需要更宽的启动窗口。
- 本版本目标：将技能中的启动确认说明同步到10秒。
- 本版本效果：启动确认统一为10秒，任务执行期限不变；启动结果仍不等于任务完成。

### 完整提示词

````markdown
---
name: background-tasks
description: >-
  Required when a shell call must return control while its command keeps running.
  Use shell run_in_background=true for registered computations or retained services,
  independent deadlines and automatic completion results. Not needed for foreground commands or internal
  parallel work whose entry script waits for every child before returning.
---

Use this skill before starting a command that must continue after its shell tool call returns. A background task is defined by that control handoff, not by its duration, use of subprocesses or whether it is a server. A task can have no children. Ordinary foreground execution uses `run_in_background=false` and does not require this skill; a foreground script that starts parallel children and waits for all of them is still foreground execution.

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

Keep the actual work in the foreground of this command. Do not add a trailing `&`, `nohup`, `setsid`, self-daemonization, or a detached launcher. Shell already provides the asynchronous handoff. Do not call the process-management helper's `start` command as an alternative launch interface.

`run_in_background` chooses whether the tool waits or returns early. The optional `background_lifecycle` chooses whether the Agent must wait for the job before its final reply:

| Lifecycle | Use and final-reply condition |
| --- | --- |
| `task` (default) | A computation needed for the task. It must finish or be stopped before the Agent can finish. |
| `service` | A service intentionally kept available after delivery. Check its health and actual behavior, then deliver its address, job ID, logs and stop command; it need not exit before the Agent can finish. |

Use `background_lifecycle` only with `run_in_background=true`; `run_in_background=false` with `service` is rejected. Do not label a required computation as a service to bypass waiting. Benchmark runs reject `service` explicitly so no work leaks between benchmark tasks.

For a service:

```json
{
  "name": "Serve local preview",
  "command": "python3 -u -m http.server 8765 --bind 127.0.0.1",
  "run_in_background": true,
  "background_lifecycle": "service"
}
```

The launch receipt confirms startup only. It contains a stable `job_id`, the human-facing `display_name`, `process_record_path`, `stdout_path`, `stderr_path`, and the internal `state_dir` and safe `name` needed for later management. Preserve these returned values; do not derive the internal name from the display name or invent a PID. The runtime records ownership, saves complete logs, and displays the job's status and elapsed time in the UI.

There are separate deadlines:

- With `run_in_background=false`, `timeout_seconds` limits synchronous execution: default 120 seconds, maximum 600.
- With `run_in_background=true`, it limits actual execution and may be any positive finite value, including values above 600. A `task` defaults to 3600 seconds. A `service` has no execution deadline unless you supply `timeout_seconds`. Both have a separate ten-second startup acknowledgement limit.
- A later management `wait --timeout` only bounds that observation; it does not stop or extend the job.

The runtime waits for the entry process. After it exits, the runtime cleans remaining processes in the same process group before publishing the result. Cleanup of leftover processes is recorded explicitly; entry exit code `0` alone must not be read as every child naturally finishing. Job timeout stops the group and reports timeout, effective exit code 124, the actual process return code, and saved logs.

## Own and finish the work

Your entry script must wait for all required internal work and report failures. Do not start real computation in a child and let its parent exit early. These rules apply to synchronous shell commands as well as background jobs. `&` is allowed *inside* a script for parallel children only when that script waits for every child, including after another child fails.

The runtime delivers each completed job's actual status, bounded stdout/stderr and full log paths once into the owning Agent's working context. The UI can update before that delivery; results enter context at a safe boundary before the next model request. A no-tool reply does not end the run while a registered `task` is pending; the runtime waits for a result and then resumes the model. After completion, inspect the result, produce remaining deliverables and verify them. A launch receipt or progress message is not completion evidence. Failure is evidence to investigate, not permission to retry without limit.

A `service` remains under its independent supervisor after a successful Agent reply and normal CLI exit. Startup is not readiness: check a meaningful health endpoint or task-specific behavior before delivery. For the example server, use a foreground shell call such as:

```json
{
  "name": "Check preview response",
  "command": "python3 -c 'import urllib.request; r = urllib.request.urlopen(\"http://127.0.0.1:8765/\", timeout=5); print(r.status); print(r.read(200).decode())'",
  "run_in_background": false,
  "timeout_seconds": 10
}
```

A successful HTTP response proves reachability and response content; also check what matters for the delivered service. Hand over its address, `job_id`, absolute state/log paths and an exact stop command from the process-management skill. Stop it when no longer needed. A failed or cancelled turn cleans its newly launched services as well as computations; services retained by earlier successful turns stay available. Benchmark does not permit service retention.

Use the [process-management skill](../process-management/SKILL.md) only when you need an intermediate status, log tail, readiness check, bounded wait or explicit stop. It is not required merely to launch a job or receive its automatic completion result. Avoid repeated polls without a question the observation can answer; continue independent work while the runtime monitors the job.

## Runnable script examples

Resolve paths relative to this skill before using these examples. Write task outputs into the task workspace, not the skill directory. The scripts are examples to adapt; they do not replace the runtime supervisor.

| Script | Pattern and use |
| --- | --- |
| [scripts/no_children.py](scripts/no_children.py) | A foreground entry with no children; flushed progress and a saved result. `python3 /ABS/SKILL/scripts/no_children.py --output /ABS/TASK/result.json` |
| [scripts/sequential.py](scripts/sequential.py) | Run one child synchronously, wait for it and preserve its status. `python3 /ABS/SKILL/scripts/sequential.py -- python3 /ABS/TASK/worker.py` |
| [scripts/parallel.sh](scripts/parallel.sh) | Start two foreground children in the same process group, wait for each even if the first fails, aggregate a nonzero status, and handle cancellation with TERM, a grace period, KILL if needed, and `wait`. `bash /ABS/SKILL/scripts/parallel.sh /ABS/TASK/results` |

For the parallel example, an optional second argument supplies the first child's exit code: `bash /ABS/SKILL/scripts/parallel.sh /ABS/TASK/results 7` still waits for and saves the second child's result before returning 7. Its optional third and fourth arguments set each child's step count and delay, useful for testing cancellation. Replace the two sample commands with actual foreground commands while retaining both waits and cancellation cleanup.

## Execution boundary

This is a native POSIX process-group contract, not a hard sandbox. Descendants that call `setsid`, change process groups, daemonize or delegate execution to an external service can escape it; do not use those techniques. Cleanup cannot promise to discover such processes. The runtime knows the entry process's exit status, not every descendant's status; the script must wait and summarize its children. This protocol needs the host shell environment with POSIX `ps` and `waitid/WNOWAIT`. The short-lived Docker `sandbox` tool does not support these registered background jobs, although shell works when the whole Agent runs in a benchmark container.
````
