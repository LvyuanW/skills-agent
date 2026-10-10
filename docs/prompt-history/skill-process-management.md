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
