# process-management 跨回合参考提示词版本时间线

当前源文件：`skills_agent/builtin_skills/process-management/references/across-turns.md`。本提示词于本次变更新增，没有更早的 Git 修订；后续沿本文追加版本，不覆盖原文。

## v1 · 2026-10-11 · 新增

来源：当前工作区（未提交）。现有 tools 不变。

- 发现的问题：持续运行程序时，模型可能等待整个任务结束、丢失日志位置，或把启动与就绪当作相同状态。
- 本版本目标：用通用后台工作程序示例说明分别启动、继续其它工作、观察日志与状态、处理失败并停止自有进程。
- 本版本效果：提供分回合操作与实际返回结果的决策示例，不依赖具体产物类型，也不要求所有任务启动后台进程。实际使用效果待观察。

### 完整提示词快照

````markdown
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
````


## v2 · 2026-10-17 · 独立计算期限与服务示例

来源：当前工作区（未提交）。

- 发现的问题：原示例没有区分计算与持续服务，等待超时不等于作业执行上限。
- 本版本目标：提供个性化前台计算脚本、7200秒独立期限、有限观察和显式服务启动就绪停止示例。
- 本版本效果：示例复用现有helper，不引入第二个后台启动器；脚本在临时工作区验证。

### 完整提示词快照

````markdown
# Process work across separate shell calls

Replace `/ABS/PROCESS.py` with the resolved helper path and `/ABS/TASK` with the task workspace. Each block is a separate shell call; the process record, rather than a remembered shell variable, connects them.

Write or reuse a foreground computation, then start it with its own execution deadline:

```sh
python3 /ABS/PROCESS.py start --state-dir /ABS/TASK/.processes --name worker --cwd /ABS/TASK --job-timeout 7200 -- python3 -u worker.py
```

`worker.py` may be a task-specific script; it must do its work in the foreground, flush useful progress and preserve failures. For example:

```python
from pathlib import Path
import json

results = []
for source in sorted(Path("input").glob("*.json")):
    value = json.loads(source.read_text())
    results.append({"source": source.name, "items": len(value)})
    print(f"processed {source.name}", flush=True)
Path("output").mkdir(exist_ok=True)
Path("output/results.json").write_text(json.dumps(results))
print("computation complete", flush=True)
```

Read the launch observation. If startup failed, inspect the diagnostic before changing the command. If it launched, continue independent work. The short shell call may finish successfully while this computation is still running. Its later completion notification contains the actual exit status and output; use it to continue the task and verify its artifacts.

When a progress marker is needed before continuing a dependent step:

```sh
python3 /ABS/PROCESS.py wait --state-dir /ABS/TASK/.processes --name worker --match "processed " --stream stdout --timeout 20
```

A marker is useful only when that program defines it meaningfully. This `wait` deadline only bounds the observation; the 7200-second computation deadline continues independently.

```sh
python3 /ABS/PROCESS.py logs --state-dir /ABS/TASK/.processes --name worker --max-chars 4000
```

For a batch job, omit a readiness condition to wait for its real exit:

```sh
python3 /ABS/PROCESS.py wait --state-dir /ABS/TASK/.processes --name worker --timeout 30
```

For an ongoing service, declare that lifecycle explicitly instead of treating it as a computation that should exit:

```sh
python3 /ABS/PROCESS.py start --state-dir /ABS/TASK/.processes --name preview --cwd /ABS/TASK --service -- python3 -u -m http.server 8765 --bind 127.0.0.1
python3 /ABS/PROCESS.py wait --state-dir /ABS/TASK/.processes --name preview --tcp 127.0.0.1 8765 --timeout 20
```

TCP reachability is only readiness evidence; make a task-specific request to check behavior. A service does not block the Agent's final reply. Add `--persistent` only when the user requested survival across CLI exit; benchmark rejects persistence. Once this task no longer needs the service:

```sh
python3 /ABS/PROCESS.py stop --state-dir /ABS/TASK/.processes --name preview --grace 3 --timeout 10
```

Verify the returned terminal state. If this call times out, the stop request still exists; use `status` to see whether cleanup subsequently finished. Preserve an ongoing service when requested, and record its absolute state directory and name so a later turn can resume inspection without starting a duplicate.
````

## v3 · 2026-10-17 · 统一 shell 启动后跨调用管理

来源：工作区未提交；`skills_agent/builtin_skills/process-management/references/across-turns.md`。

- 发现的问题：后台执行模式、服务保留及进程管理的职责需要分开说明。
- 本版本目标：通过现有 shell 启动，提供可观察执行与协作等待子任务的说明。
- 本版本效果：计算任务等待完成，服务核实就绪后可交付；状态、日志及停止由进程管理技能提供。范例已离线验证，模型表现待观察。

### 完整提示词

````markdown
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
````
