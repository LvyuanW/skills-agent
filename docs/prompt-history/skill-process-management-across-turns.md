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
