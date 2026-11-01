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


## v3 · 2026-10-17 · 后台图片随完成结果交付

来源：工作区未提交；`skills_agent/builtin_skills/background-tasks/SKILL.md`。

- 发现的问题：后台启动时未来图片尚未存在，启动回执不能等同于附图成功。
- 本版本目标：记录未来图片声明，在任务结束时附图；长驻服务运行期间另行同步读取图像。
- 本版本效果：技能与工具契约保持一致，脚本可打印普通日志和多个结果；技能格式与 JSON 示例校验通过，实际模型遵循待观察。

### 完整原文

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

The runtime registers these paths at launch and reads/snapshots the images only after the job finishes; the startup receipt contains no image payload. The script must save each final image within 1 MB before exiting. Declare at most five images, using absolute host paths or local `file://` URLs and optional `source_url`. Stdout may contain any progress logs or JSON metadata: explicit `images`, including `[]`, controls attachments independently of that output. Image acquisition failures belong to the completion result and must be checked. For a service that stays running, inspect a current screenshot with a separate synchronous shell call and `images`; do not wait for the service to exit merely to view its output.

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


## v4 · 2026-10-17 · 图片上限统一为10张

来源：工作区未提交；`skills_agent/builtin_skills/background-tasks/SKILL.md`。

- 发现的问题：原上限为每个请求5张图片，用户要求扩展为10张。
- 本版本目标：统一运行时、工具说明与技能的10图上限；每图1 MB及历史图片引用规则不变。
- 本版本效果：允许同一请求携带至多10张图片；超出时使用合图，实际模型表现待观察。

### 完整原文

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

The runtime registers these paths at launch and reads/snapshots the images only after the job finishes; the startup receipt contains no image payload. The script must save each final image within 1 MB before exiting. Declare at most ten images, using absolute host paths or local `file://` URLs and optional `source_url`. Stdout may contain any progress logs or JSON metadata: explicit `images`, including `[]`, controls attachments independently of that output. Image acquisition failures belong to the completion result and must be checked. For a service that stays running, inspect a current screenshot with a separate synchronous shell call and `images`; do not wait for the service to exit merely to view its output.

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

## v5 · 2026-10-17 · 精确停止已登记作业

来源：工作区未提交；`skills_agent/builtin_skills/background-tasks/SKILL.md`。

- 发现的问题：使用宽泛 pkill -f 停止旧任务时可能同时命中新任务的启动链。
- 本版本目标：按回执中的 state_dir 和内部 name 停止旧任务，确认清理后再替换。
- 本版本效果：明确作业停止和替换的精确管理入口；实际模型使用表现待观察。

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

The runtime registers these paths at launch and reads/snapshots the images only after the job finishes; the startup receipt contains no image payload. The script must save each final image within 1 MB before exiting. Declare at most ten images, using absolute host paths or local `file://` URLs and optional `source_url`. Stdout may contain any progress logs or JSON metadata: explicit `images`, including `[]`, controls attachments independently of that output. Image acquisition failures belong to the completion result and must be checked. For a service that stays running, inspect a current screenshot with a separate synchronous shell call and `images`; do not wait for the service to exit merely to view its output.

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

To stop or replace registered work, use that helper's `stop` with the exact receipt's `state_dir` and internal `name`, and confirm cleanup before starting a replacement. Avoid broad command-line matching such as `pkill -f worker.py`: the same text can also occur in the shell and launch helper starting the new job, so it can kill the new launch as well as the old worker.

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

## v6 · 2026-10-17 · 监管状态未确认时继续诊断并保留完成门槛

来源：工作区未提交；`skills_agent/builtin_skills/background-tasks/SKILL.md`。

- 发现的问题：监管异常被当作致命错误，模型没有机会读取登记和日志继续恢复。
- 本版本目标：明确 unconfirmed 的含义、恢复依据和必需 task 的运行时完成门槛，保留 service 生命周期。
- 本版本效果：明确错误可恢复且不等于完成，实际模型恢复表现待观察。

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

The runtime registers these paths at launch and reads/snapshots the images only after the job finishes; the startup receipt contains no image payload. The script must save each final image within 1 MB before exiting. Declare at most ten images, using absolute host paths or local `file://` URLs and optional `source_url`. Stdout may contain any progress logs or JSON metadata: explicit `images`, including `[]`, controls attachments independently of that output. Image acquisition failures belong to the completion result and must be checked. For a service that stays running, inspect a current screenshot with a separate synchronous shell call and `images`; do not wait for the service to exit merely to view its output.

There are separate deadlines:

- With `run_in_background=false`, `timeout_seconds` limits synchronous execution: default 120 seconds, maximum 600.
- With `run_in_background=true`, it limits actual execution and may be any positive finite value, including values above 600. A `task` defaults to 3600 seconds. A `service` has no execution deadline unless you supply `timeout_seconds`. Both have a separate ten-second startup acknowledgement limit.
- A later management `wait --timeout` only bounds that observation; it does not stop or extend the job.

The runtime waits for the entry process. After it exits, the runtime cleans remaining processes in the same process group before publishing the result. Cleanup of leftover processes is recorded explicitly; entry exit code `0` alone must not be read as every child naturally finishing. Job timeout stops the group and reports timeout, effective exit code 124, the actual process return code, and saved logs.

## Own and finish the work

Your entry script must wait for all required internal work and report failures. Do not start real computation in a child and let its parent exit early. These rules apply to synchronous shell commands as well as background jobs. `&` is allowed *inside* a script for parallel children only when that script waits for every child, including after another child fails.

The runtime delivers each completed job's actual status, bounded stdout/stderr and full log paths once into the owning Agent's working context. The UI can update before that delivery; results enter context at a safe boundary before the next model request. A no-tool reply does not end the run while a registered `task` is pending; the runtime waits for a result and then resumes the model. After completion, inspect the result, produce remaining deliverables and verify them. A launch receipt or progress message is not completion evidence. Failure is evidence to investigate, not permission to retry without limit.

If a launch receipt is missing or damaged, the runtime checks the durable registration before returning an error; it does not rerun the command. A `state: "unconfirmed"` or `status: "unconfirmed"` result means the outcome or supervision is unknown, not that the job exited or completed. The error and available job identifiers, record and log paths are returned to you so you can diagnose and recover while the conversation continues. Read that evidence and use the [process-management skill](../process-management/SKILL.md) for verified status, logs or stop. Do not blindly launch a duplicate, invent a PID or edit the registration to claim cleanup. The runtime preserves a required task's final-reply gate until supervision is restored or actual exit and cleanup are confirmed; reporting the uncertainty alone does not release this gate. A verified service retains its nonblocking lifecycle contract, but uncertainty is not evidence that it is healthy or ready for delivery.

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

To stop or replace registered work, use that helper's `stop` with the exact receipt's `state_dir` and internal `name`, and confirm cleanup before starting a replacement. Avoid broad command-line matching such as `pkill -f worker.py`: the same text can also occur in the shell and launch helper starting the new job, so it can kill the new launch as well as the old worker.

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

## v7 · 2026-10-17 · 可见进度、精确停止与细化任务反例

来源：提交 `46adca584389cc787b5b6ba358ee999a1664c394`；`skills_agent/builtin_skills/background-tasks/SKILL.md`。

- 发现的问题：后台计算的缓冲和 tail 管道隐藏进度，模型依据空日志决定重跑，并用宽泛 pkill 误匹配启动链，失去启动回执。
- 本版本目标：说明可观测进度与停止判断，提供基于登记标识的精确停止调用，加入此次细化任务的简短反例；保持未确认状态可恢复和完成门槛。
- 本版本效果：技能已明确上述操作与判断依据，实际模型表现待观察。

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

The runtime registers these paths at launch and reads/snapshots the images only after the job finishes; the startup receipt contains no image payload. The script must save each final image within 1 MB before exiting. Declare at most ten images, using absolute host paths or local `file://` URLs and optional `source_url`. Stdout may contain any progress logs or JSON metadata: explicit `images`, including `[]`, controls attachments independently of that output. Image acquisition failures belong to the completion result and must be checked. For a service that stays running, inspect a current screenshot with a separate synchronous shell call and `images`; do not wait for the service to exit merely to view its output.

There are separate deadlines:

- With `run_in_background=false`, `timeout_seconds` limits synchronous execution: default 120 seconds, maximum 600.
- With `run_in_background=true`, it limits actual execution and may be any positive finite value, including values above 600. A `task` defaults to 3600 seconds. A `service` has no execution deadline unless you supply `timeout_seconds`. Both have a separate ten-second startup acknowledgement limit.
- A later management `wait --timeout` only bounds that observation; it does not stop or extend the job.

The runtime waits for the entry process. After it exits, the runtime cleans remaining processes in the same process group before publishing the result. Cleanup of leftover processes is recorded explicitly; entry exit code `0` alone must not be read as every child naturally finishing. Job timeout stops the group and reports timeout, effective exit code 124, the actual process return code, and saved logs.

## Own and finish the work

Your entry script must wait for all required internal work and report failures. Do not start real computation in a child and let its parent exit early. These rules apply to synchronous shell commands as well as background jobs. `&` is allowed *inside* a script for parallel children only when that script waits for every child, including after another child fails.

The runtime delivers each completed job's actual status, bounded stdout/stderr and full log paths once into the owning Agent's working context. The UI can update before that delivery; results enter context at a safe boundary before the next model request. A no-tool reply does not end the run while a registered `task` is pending; the runtime waits for a result and then resumes the model. After completion, inspect the result, produce remaining deliverables and verify them. A launch receipt or progress message is not completion evidence. Failure is evidence to investigate, not permission to retry without limit.

If a launch receipt is missing or damaged, the runtime checks the durable registration before returning an error; it does not rerun the command. A `state: "unconfirmed"` or `status: "unconfirmed"` result means the outcome or supervision is unknown, not that the job exited or completed. The error and available job identifiers, record and log paths are returned to you so you can diagnose and recover while the conversation continues. Read that evidence and use the [process-management skill](../process-management/SKILL.md) for verified status, logs or stop. Do not blindly launch a duplicate, invent a PID or edit the registration to claim cleanup. The runtime preserves a required task's final-reply gate until supervision is restored or actual exit and cleanup are confirmed; reporting the uncertainty alone does not release this gate. A verified service retains its nonblocking lifecycle contract, but uncertainty is not evidence that it is healthy or ready for delivery.

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

## Observe progress and replace work

Make intermediate progress visible when it will inform a decision: run Python with `-u` or flush meaningful milestones, such as completed batches, current phase and elapsed time. Let the supervisor capture stdout/stderr directly, then inspect bounded tails with the process-management helper. Avoid launching `python worker.py 2>&1 | tail -35` when you need live progress: `tail -35` normally waits for end of input before emitting the last lines, and cannot reveal progress the worker has not printed.

Empty logs or no output files do not establish a stall. CPU at 100% shows activity but proves neither useful progress nor a hang. Compare task-specific progress, expected work and the remaining budget before choosing to wait, stop or optimize and restart; you need not wait for the full execution timeout when replacement is justified.

To stop or replace registered work, use the process-management helper's `stop` with the exact receipt's `state_dir` and internal `name`, and confirm cleanup before starting a replacement:

```json
{
  "name": "Stop registered work before replacing it",
  "command": "python3 /ABS/PROCESS-MANAGEMENT/scripts/process.py stop --state-dir /ABS/RETURNED/jobs --name RETURNED-INTERNAL-NAME --grace 3 --timeout 10",
  "run_in_background": false,
  "timeout_seconds": 15
}
```

Resolve the helper path from the process-management skill and copy the two identifiers from the receipt. Avoid broad command-line matching such as `pkill -f worker.py`: the same text can also occur in the shell and launch helper starting the new job, so it can kill the new launch as well as the old worker. If stop reports an unconfirmed state, diagnose it before launching a replacement.

Counterexample from a refinement task: `python run_refine.py 2>&1 | tail -35` showed no logs or output files while the worker used 100% CPU. After about 126 seconds of a 600-second deadline, the Agent tried to optimize and rerun with `pkill -f run_refine.py; ...` inside the replacement command; the new launch lost its receipt, consistent with the broad match also killing its helper or shell. The empty log did not prove a hang, and optimizing was not itself the error: expose flushed progress, then stop the exact registered job and confirm cleanup before replacing it.

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

## v8 · 2026-10-27 · 直接英文表达与明确使用边界

来源：本轮工作区未提交；skills_agent/builtin_skills/background-tasks/SKILL.md。

- 发现的问题：部分指令较抽象或过长，使用与跳过条件需要更容易区分。
- 本版本目标：使用清楚、自然、直接的英文说明动作与边界；保留实质约束、原脚本接口和代码示例。六个实验 worktree 使用同一正文。
- 本版本效果：技能格式、本地引用和原有代码示例核对通过；已完成技能路由场景审阅。未运行新一轮真实模型测评，行为与分数效果待验证。

### 完整原文

````text
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
````
