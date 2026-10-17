# process-management 技能提示词版本时间线

当前源文件：`skills_agent/builtin_skills/process-management/SKILL.md`。本提示词于本次变更新增，没有更早的 Git 修订；后续沿本文追加版本，不覆盖原文。

## v1 · 2026-10-11 · 新增

来源：当前工作区（未提交）。现有 tools 不变。

- 发现的问题：单次 shell 或有界命令检查不能支撑持续进程在不同回合之间的观察与管理；启动成功也不足以判断服务就绪或工作完成。
- 本版本目标：提供通用的 start/status/wait/logs/stop 接口，以保存记录识别自有进程、保留日志与退出状态，说明就绪观测和所有权边界。
- 本版本效果：新增宿主 POSIX 进程管理脚本，通过已有 shell 直接调用；后台运行不阻塞当前 shell，失去 supervisor 归属时明确报告而不猜测信号目标。实际模型跨回合运行管理效果待观察。

### 完整提示词快照

````markdown
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
````


## v2 · 2026-10-13 · 聚焦跨调用状态观察并保留进程归属

来源：当前工作区（未提交）。与[主工作提示词 v7](agent-system.md) 的按需技能选择规则同步；本轮仅调整技能入口，不修改脚本、工具接口或运行时协议。

- 发现的问题：入口中的通用说明较多，跨调用状态、日志和就绪能力与必要的登记和归属协议需要更清楚地区分。
- 本版本目标：精简为使用已保存进程引用启动、观察和停止自有任务的方法，保留自动清理所需登记协议以及 CLI、benchmark 和 persistent 的实际边界。
- 本版本效果：入口允许阅读和调整参考脚本，同时明确不能破坏归属与自动清理协议；保留就绪不等于行为正确、不可确认归属时不猜测 PID 的要求。本轮没有修改脚本，真实模型的跨回合管理表现待观察。

### 完整提示词快照

````markdown
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
````


## v3 · 2026-10-13 · 以可观察进展决定等待与停止

来源：当前工作区（未提交）。主工作提示词本轮未修改，不新增其版本。

- 发现的问题：后台任务的一次 wait 超时只结束等待，不会停止计算；反复相同轮询可能没有新信息，也不能说明任务在有效推进。
- 本版本目标：通过日志、完成单元或中间结果观察进展，结合已选停止条件决定后续等待，并在条件达到而任务未退出时调用现有 stop。
- 本版本效果：明确区分等待时限与执行时限，要求确认停止结果并避免无限重复无新信息的轮询；保留现有进程登记和清理协议，不修改脚本实现。真实后台任务的进度判断与停止效果待观察。

### 完整提示词快照

````markdown
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

For a background computation, make progress observable through flushed logs, completed units or saved intermediate results, and inspect those observations against its chosen stopping condition. A `wait --timeout` expiry only returns control; it does not stop the job or enforce its execution deadline. When the stopping condition is reached, use `stop` if the job has not exited and verify the returned state. Choose waits around expected progress and the remaining budget; do not repeat identical polls indefinitely without new information. If progress cannot be established, inspect or report that gap rather than treating elapsed waiting as evidence of useful work.

Within the agent, registered jobs belong to the turn that started them, including subagent jobs. Failed/cancelled turns stop their new jobs. Successful CLI turns keep them available across turns; leaving the CLI stops them. Only a user-requested ongoing service should use `--persistent`, and it survives exit only after its creating turn succeeds. Benchmark runs forbid persistence and clean up all registered jobs on success, failure or interruption. Outside the agent, use `stop` explicitly. Do not detach to evade this ownership.

The supervisor retains its direct child's PID until group cleanup ends and never signals a PID supplied by the model or a record. If ownership cannot be confirmed, status is `orphaned` and stop refuses to guess. Report that gap rather than launching duplicates. Preserve the absolute state directory, name and log paths for an intentionally continuing service. Read [references/across-turns.md](references/across-turns.md) for a start/observe/stop example.
````


## v4 · 2026-10-17 · 计算终态投递与独立执行期限

来源：当前工作区（未提交）。

- 发现的问题：后台启动成功被误作计算完成；原helper没有执行期限，个性化脱离脚本无法可靠追踪。
- 本版本目标：复用唯一登记helper，区分启动、等待、作业执行期限，计算默认等待终态，服务显式声明。
- 本版本效果：新增独立job-timeout、service和完成结果协议，保留原始日志；当前sandbox不支持这套后台协议。

### 完整提示词快照

````markdown
---
name: process-management
description: >-
  Use when a server or long-running job must remain active across shell calls,
  with registered execution, independent job deadlines, completion results,
  readiness checks and owned-process cleanup.
  Skip short foreground commands or checks needed immediately; use feedback for
  a bounded run that does not need background management.
---

For work that must continue across shell calls, launch the task's foreground program through [scripts/process.py](scripts/process.py) using the shell tool. Your program may be an existing command or a task-specific script. Keep the helper's registration protocol; do not replace it with `&`, `nohup`, `setsid`, or a custom detached launcher. A successful `start` only confirms launch, not completion or service readiness. A bounded command needed immediately can instead use the feedback skill's `run` operation.

The helper requires Python 3.11 or newer on POSIX with `ps` and `waitid/WNOWAIT`. Resolve its absolute path from this SKILL.md. Keep the same absolute `--state-dir` and `--name` across calls; shell variables do not persist. This protocol supports host shell execution, including when the whole Agent runs inside a benchmark container. The sandbox tool's short-lived Docker containers do not support registered background jobs; do not launch this helper there or assume a detached process will survive that tool call.

| Operation | Result |
| --- | --- |
| `start --state-dir DIR --name NAME [--cwd DIR] [--job-timeout SECONDS] -- COMMAND...` | Registered computation, stable run ID, independent execution deadline and complete stdout/stderr logs |
| `start ... --service [--persistent] -- COMMAND...` | Explicit ongoing service; its exit is not required before the Agent replies |
| `status --state-dir DIR --name NAME` | Verified supervisor identity and recorded lifecycle/exit status |
| `wait --state-dir DIR --name NAME [--timeout 30]` | Actual command status; timeout returns 124 and leaves it running |
| `wait ... --match TEXT [--stream stdout\|stderr\|both]` | Wait for a literal log marker while the process runs; flush readiness logs |
| `wait ... --tcp HOST PORT` | Observe reachability, not service identity or correct behavior |
| `logs --state-dir DIR --name NAME [--max-chars 6000]` | Bounded tails and complete log paths |
| `stop --state-dir DIR --name NAME [--grace 3 --timeout 10]` | TERM then KILL for the owned process group and confirmation of cleanup |

Arguments execute directly; use `sh -lc '...'` for shell syntax, keeping the actual work in the foreground of that managed command. Do not daemonize or create a new session inside it. Commands needing interactive stdin require another approach. An active or unconfirmed name cannot be reused; an ended run may be replaced with fresh logs. Stable run IDs distinguish these executions even if they share a name.

There are three separate time limits:

- The shell tool's `timeout_seconds` bounds the short launch/status/wait call (default 120 seconds, maximum 600).
- `start --job-timeout SECONDS` bounds the actual computation from child startup (default 3600 seconds); choose it for the workload and task budget. It is independent of the shell limit. Expiry stops the owned process group and records `timed_out`, effective exit code 124, the real process return code and saved logs.
- `wait --timeout SECONDS` only bounds that observation. Expiry returns control while the job continues; it does not extend or replace the job deadline.

Registered computations must reach a confirmed terminal state before the Agent accepts a final reply. Their completion results enter the owning Agent's working context at the next safe boundary, with exit status, timeout reason, bounded output and full log paths. Results arriving during a model response are delivered before the next request; launching successfully is never substituted for the actual result. Read those results and complete remaining work, including producing and checking deliverables. Failure or timeout is evidence to inspect, not an instruction to restart automatically.

For a background computation, make progress observable through flushed logs, completed units or saved intermediate results, and inspect those observations against its chosen stopping condition. A `wait --timeout` expiry only returns control; it does not stop the job or enforce its execution deadline. When the stopping condition is reached, use `stop` if the job has not exited and verify the returned state. Choose waits around expected progress and the remaining budget; do not repeat identical polls indefinitely without new information. If progress cannot be established, inspect or report that gap rather than treating elapsed waiting as evidence of useful work.

Use `--service` only for a process intentionally needed while other work continues, such as a development server; check its readiness and actual behavior separately. Do not mark a required computation as a service to bypass completion waiting. Services have no execution deadline by default; an explicit `--job-timeout` can bound their lifetime. Stop them when no longer needed. `--persistent` requires `--service` and an explicit user need to keep it across CLI exit; it is a retention permission, not proof of readiness or completion.

Registered jobs belong to the turn that started them, including subagent jobs. Failed/cancelled turns stop their new work. Successful CLI turns may keep services available for later turns; leaving the CLI stops nonpersistent services. Benchmark runs forbid persistence and clean up all registered processes on exit. Outside the Agent, use `wait`, `logs` and `stop` explicitly: there is no automatic working-context delivery.

The supervisor retains its direct child's PID until group cleanup ends and never signals a PID supplied by the model or a record. If ownership cannot be confirmed, status is `orphaned` and stop refuses to guess. Report that gap rather than launching duplicates. Preserve the absolute state directory, name and log paths for an intentionally continuing service. Read [references/across-turns.md](references/across-turns.md) for a start/observe/stop example.
````

## v5 · 2026-10-17 · 强制覆盖全部后台任务

来源：当前工作区（未提交）。

- 发现的问题：描述中的长任务条件可能使模型遗漏短后台任务或个性化脚本。
- 本版本目标：描述与正文明确任何后台任务或持续服务均须使用本技能，普通前台命令除外。
- 本版本效果：技能目录阶段即可看到强制触发条件，正文保留登记 helper 和禁止自行脱离进程的约束；模型实际遵循情况待观察。

### 完整提示词快照

````markdown
---
name: process-management
description: >-
  Required for every background task or ongoing service, regardless of duration,
  including task-specific scripts that continue after a shell call returns.
  Provides registered execution, independent job deadlines, completion results,
  readiness checks and owned-process cleanup. Skip ordinary foreground commands.
---

Before starting any background task or ongoing service, read and use this skill, even for a short task or a task-specific script. Launch the task's foreground program through [scripts/process.py](scripts/process.py) using the shell tool. Your program may be an existing command or a task-specific script. Keep the helper's registration protocol; do not replace it with `&`, `nohup`, `setsid`, or a custom detached launcher. A successful `start` only confirms launch, not completion or service readiness. Ordinary foreground commands do not require this skill; a bounded command needed immediately can instead use the feedback skill's `run` operation.

The helper requires Python 3.11 or newer on POSIX with `ps` and `waitid/WNOWAIT`. Resolve its absolute path from this SKILL.md. Keep the same absolute `--state-dir` and `--name` across calls; shell variables do not persist. This protocol supports host shell execution, including when the whole Agent runs inside a benchmark container. The sandbox tool's short-lived Docker containers do not support registered background jobs; do not launch this helper there or assume a detached process will survive that tool call.

| Operation | Result |
| --- | --- |
| `start --state-dir DIR --name NAME [--cwd DIR] [--job-timeout SECONDS] -- COMMAND...` | Registered computation, stable run ID, independent execution deadline and complete stdout/stderr logs |
| `start ... --service [--persistent] -- COMMAND...` | Explicit ongoing service; its exit is not required before the Agent replies |
| `status --state-dir DIR --name NAME` | Verified supervisor identity and recorded lifecycle/exit status |
| `wait --state-dir DIR --name NAME [--timeout 30]` | Actual command status; timeout returns 124 and leaves it running |
| `wait ... --match TEXT [--stream stdout\|stderr\|both]` | Wait for a literal log marker while the process runs; flush readiness logs |
| `wait ... --tcp HOST PORT` | Observe reachability, not service identity or correct behavior |
| `logs --state-dir DIR --name NAME [--max-chars 6000]` | Bounded tails and complete log paths |
| `stop --state-dir DIR --name NAME [--grace 3 --timeout 10]` | TERM then KILL for the owned process group and confirmation of cleanup |

Arguments execute directly; use `sh -lc '...'` for shell syntax, keeping the actual work in the foreground of that managed command. Do not daemonize or create a new session inside it. Commands needing interactive stdin require another approach. An active or unconfirmed name cannot be reused; an ended run may be replaced with fresh logs. Stable run IDs distinguish these executions even if they share a name.

There are three separate time limits:

- The shell tool's `timeout_seconds` bounds the short launch/status/wait call (default 120 seconds, maximum 600).
- `start --job-timeout SECONDS` bounds the actual computation from child startup (default 3600 seconds); choose it for the workload and task budget. It is independent of the shell limit. Expiry stops the owned process group and records `timed_out`, effective exit code 124, the real process return code and saved logs.
- `wait --timeout SECONDS` only bounds that observation. Expiry returns control while the job continues; it does not extend or replace the job deadline.

Registered computations must reach a confirmed terminal state before the Agent accepts a final reply. Their completion results enter the owning Agent's working context at the next safe boundary, with exit status, timeout reason, bounded output and full log paths. Results arriving during a model response are delivered before the next request; launching successfully is never substituted for the actual result. Read those results and complete remaining work, including producing and checking deliverables. Failure or timeout is evidence to inspect, not an instruction to restart automatically.

For a background computation, make progress observable through flushed logs, completed units or saved intermediate results, and inspect those observations against its chosen stopping condition. A `wait --timeout` expiry only returns control; it does not stop the job or enforce its execution deadline. When the stopping condition is reached, use `stop` if the job has not exited and verify the returned state. Choose waits around expected progress and the remaining budget; do not repeat identical polls indefinitely without new information. If progress cannot be established, inspect or report that gap rather than treating elapsed waiting as evidence of useful work.

Use `--service` only for a process intentionally needed while other work continues, such as a development server; check its readiness and actual behavior separately. Do not mark a required computation as a service to bypass completion waiting. Services have no execution deadline by default; an explicit `--job-timeout` can bound their lifetime. Stop them when no longer needed. `--persistent` requires `--service` and an explicit user need to keep it across CLI exit; it is a retention permission, not proof of readiness or completion.

Registered jobs belong to the turn that started them, including subagent jobs. Failed/cancelled turns stop their new work. Successful CLI turns may keep services available for later turns; leaving the CLI stops nonpersistent services. Benchmark runs forbid persistence and clean up all registered processes on exit. Outside the Agent, use `wait`, `logs` and `stop` explicitly: there is no automatic working-context delivery.

The supervisor retains its direct child's PID until group cleanup ends and never signals a PID supplied by the model or a record. If ownership cannot be confirmed, status is `orphaned` and stop refuses to guess. Report that gap rather than launching duplicates. Preserve the absolute state directory, name and log paths for an intentionally continuing service. Read [references/across-turns.md](references/across-turns.md) for a start/observe/stop example.
````

## v6 · 2026-10-17 · 聚焦已有作业的观察与停止

来源：工作区未提交；`skills_agent/builtin_skills/process-management/SKILL.md`。

- 发现的问题：后台执行模式、服务保留及进程管理的职责需要分开说明。
- 本版本目标：通过现有 shell 启动，提供可观察执行与协作等待子任务的说明。
- 本版本效果：计算任务等待完成，服务核实就绪后可交付；状态、日志及停止由进程管理技能提供。范例已离线验证，模型表现待观察。

### 完整提示词

````markdown
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

Only wait or poll to answer a concrete dependency, readiness or progress question. Prefer meaningful flushed markers or task-specific checks over elapsed time. TCP reachability alone does not prove your service is ready or behaving correctly. When no intermediate observation is needed, continue independent work and let the runtime deliver completion. A successful launch or a marker is not proof of full computation success.

Explicitly stop a job when it is no longer needed. Read the returned state: a `stop --timeout` expiry only ends that observation, so use `status` to confirm subsequent cleanup. Do not relaunch a duplicate merely because an observation timed out. A `task` must reach a confirmed terminal state before the Agent can finish. A `service` may remain running after a successful reply and normal CLI exit, but needs an actual readiness and behavior check before delivery. Preserve its job ID, absolute state/log paths and exact stop command for later management; stop it when no longer needed. A failed or cancelled turn cleans its newly launched services, while previously retained services remain. Benchmark rejects services.

The supervisor signals only its verified owned process group. If ownership cannot be confirmed, it reports `orphaned` and refuses to guess; report that gap rather than signaling an arbitrary PID or treating it as completion. This is the same cooperative process-group boundary as shell execution, not a hard sandbox: do not create new sessions, daemonize or delegate unmanaged work. Entry exit status does not contain every descendant's status; scripts must wait and aggregate their internal results.

Read [references/across-turns.md](references/across-turns.md) for status, logs, readiness and stop examples using a shell launch receipt.
````

## v7 · 2026-10-17 · 按登记标识停止与替换任务

来源：工作区未提交；`skills_agent/builtin_skills/process-management/SKILL.md`。

- 发现的问题：命令文本可能同时出现在旧工作进程、新启动 helper 和 shell 中。
- 本版本目标：补充同步 shell 调用 stop 的完整范例，避免按业务命令文本查杀。
- 本版本效果：明确作业停止和替换的精确管理入口；实际模型使用表现待观察。

### 完整提示词

````markdown
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

The supervisor signals only its verified owned process group. If ownership cannot be confirmed, it reports `orphaned` and refuses to guess; report that gap rather than signaling an arbitrary PID or treating it as completion. This is the same cooperative process-group boundary as shell execution, not a hard sandbox: do not create new sessions, daemonize or delegate unmanaged work. Entry exit status does not contain every descendant's status; scripts must wait and aggregate their internal results.

Read [references/across-turns.md](references/across-turns.md) for status, logs, readiness and stop examples using a shell launch receipt.
````

## v8 · 2026-10-17 · 恢复未确认监管状态

来源：工作区未提交；`skills_agent/builtin_skills/process-management/SKILL.md`。

- 发现的问题：原说明仅报告归属丢失，没有说明会话继续后如何处理未确认作业。
- 本版本目标：按准确登记与日志诊断，不重复启动、不猜测 PID、不伪造清理确认，保留必需 task 的完成门槛。
- 本版本效果：明确错误可恢复且不等于完成，实际模型恢复表现待观察。

### 完整提示词

````markdown
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

````
