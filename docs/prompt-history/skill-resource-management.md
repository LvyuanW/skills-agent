# resource-management 技能提示词版本时间线

源位置：`skills_agent/builtin_skills/resource-management/SKILL.md`。

## v1 · 2026-10-18 · 按资源预算规划处理规模与并发

来源：提交 `334761abe8454f6cea8d224b63c1e147476e89f7`。

- 发现的问题：按核数或输入文件大小直接安排并行工作，可能忽略处理中间状态与并发副本的内存需求。
- 本版本目标：以通用的资源需求、规模变化及执行决策界定何时使用和何时不使用；采用普通文件批处理反例，不绑定具体测评或数据领域。
- 本版本效果：新增可自动发现的技能，指导预算、代表性试跑、峰值观测和有余量的并发决策；不新增运行时硬限额。实际模型效果待观察。

### 完整提示词快照

````markdown
---
name: resource-management
description: >-
  Plan memory use and concurrency before resource-intensive work, scaling a
  workload, or adding parallel execution whose aggregate demand has not been
  verified to fit the available budget. Use after resource exhaustion to revise
  the execution plan. Skip routine bounded operations and unchanged workloads
  already measured to fit the current budget.
---

Keep execution within the resources actually available to this task. Choose the
working set, batch size and concurrency from evidence rather than CPU count alone.
This skill guides execution decisions; it does not enforce a memory limit or
guarantee that arbitrary commands cannot run out of memory.

## When to use

Read this skill before executing work when any of these conditions applies:

- Peak resource demand at the intended scale is unknown and could exhaust the
  available budget.
- Increasing input size, batch size, retained intermediate results or concurrency
  could invalidate a previous resource estimate.
- Multiple operations will overlap and their combined demand has not been shown
  to fit alongside existing work.
- A resource-related failure requires a different execution plan before retrying.

It applies to foreground and background execution alike. Duration alone does not
determine whether the skill is needed.

## When not to use

- Simple, bounded reads, searches, edits or status checks with a small, understood
  working set.
- Repeating a workload whose measured peak fits the current budget, with no
  material change to its scale, concurrency, implementation or available resources.
- Reasoning, planning or remote requests that do not start substantial local work.

Reuse recent resource observations while their assumptions remain valid; do not
turn every shell call into another profiling exercise.

## Budget, measure, then scale

1. Establish the task's effective memory and CPU allowance and current usage using
   the available environment tools. Include work already running and leave explicit
   headroom for the Agent, libraries and uncertain temporary allocations. Use the
   task or container allowance rather than the host's total capacity. Unknown usage
   is not free capacity.
2. Begin with one worker and a bounded, representative trial that reaches the
   expensive stages. Check correctness and observe peak memory as well as runtime.
   Include parsing, conversion, temporary copies, intermediate results and output
   retention. File size and idle memory are not peak-memory estimates; a small trial
   may miss costs that appear only at the intended scale.
3. Choose batch size and concurrency conservatively. The combined estimated peak
   of planned work, existing work and reserved headroom must fit the effective
   allowance. CPU availability is another upper bound, not evidence that memory
   supports the same number of workers. Account for subprocesses and library threads
   created inside each worker. Assume sharing only when it has been established.
4. If even one worker does not fit, reduce the working set, stream or batch the
   work, or use an appropriate storage-backed method before increasing concurrency.
   Release results no longer needed instead of accumulating an entire batch in
   memory. Preserve the task's required outputs and numerical or semantic correctness.
5. Check resource observations while scaling and revise the estimate when evidence
   changes. After suspected memory exhaustion, inspect available diagnostics and
   reduce demand before another attempt; do not repeat the same resource plan.
   An exit signal or code alone does not establish that OOM caused the failure.

Use the [feedback skill](../feedback/SKILL.md) for representative execution and
correctness evidence. If work must continue after the shell tool returns, also use
the [background-tasks skill](../background-tasks/SKILL.md); starting asynchronously
does not provide an additional resource budget.

## Common counterexample

A batch-processing script starts one worker per CPU core. Each input file is small
on disk, but decompression and parsing expand it substantially in memory. On a
16-core machine with an 8 GiB task allowance, sixteen workers each peaking near
2 GiB can demand roughly 32 GiB before other costs. Counting cores and compressed
file sizes therefore produces an execution plan that does not fit. Measure one
representative worker, account for the current budget and headroom, then process
bounded batches with a suitable number of workers and release completed results.
````
