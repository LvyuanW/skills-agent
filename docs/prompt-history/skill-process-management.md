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
