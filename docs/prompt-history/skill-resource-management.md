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

<!-- 历史分支：05-measured-resources；下列版本号属于该分支，保留原记录。 -->

## v2 · 2026-10-26 · 依据实际执行机制判断资源配置

来源：分支 `experiment/ale-measured-resources` 的独立实验；共同基线 `1dd2267`。本条与对应提示词改写同一提交。

- 发现的问题：资源配置变量并不保证被运行中的库采用；若仅按配置意图或核数安排工作，可能忽略嵌套线程池、竞争和实际峰值。重复试跑也可能浪费已有的有效观测。
- 本版本目标：复用适用的运行证据，在同一代表性输入及必需计算上比较配置；仅在设置与观测不符时调查实际后端与线程控制。保留有效预算、正确性及轻量任务排除，不新增调用要求或运行工具。
- 本版本效果：提示词局部替换已完成；格式、加载与差异检查见本实验交付记录。实际模型行为、成绩和成本效果待用户配对测评，不据文案检查宣称提升。

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
2. Reuse an applicable completed run, or begin with one worker and a bounded trial
   that reaches the expensive stages. Observe correctness, peak memory and elapsed
   runtime, including parsing, conversion, temporary copies and retained results.
   When comparing execution settings, keep the representative input and required
   computation the same. File size and idle memory are not peak-memory estimates; a small
   trial may miss costs that appear only at the intended scale.
3. Choose batch size and concurrency conservatively. The combined estimated peak
   of planned work, existing work and reserved headroom must fit the effective
   allowance. CPU availability is another upper bound, not evidence that memory
   supports the same number of workers. Account for subprocesses and library threads
   inside each worker. If a configured limit disagrees with observed use or runtime,
   inspect the active backend and its worker/thread controls; setting a variable
   alone does not show that the running library honors it. Assume sharing only when
   it has been established.
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

A batch script sets a thread limit and launches one worker per CPU core, but the
loaded library uses a different threading runtime. Each worker starts its own
thread pool, increasing contention and memory demand; compressed inputs also
expand during parsing. An unchanged or slower run is reason to inspect the actual
workers and library, not assume that the limit took effect. Compare suitable
settings on the same workload and choose the measured plan that fits the budget
while preserving results. Neither one thread nor all available cores is always
the best setting.
````

## v3 · 2026-10-27 · 将资源观测落实为瓶颈修复与执行调整

来源：分支 `experiment/ale-measured-resources`，工作区未提交修改；沿用本分支的 skill 常驻方式。

- 发现的问题：资源观测可能停留在内存、worker 数与吞吐统计；已定位的计算热点没有转为实际优化，增加并发也不必然改善整体吞吐。
- 本版本目标：在实测吞吐出现未解决问题时定位耗时阶段，选择缓存、批处理或等价计算等针对性改动；比较同工作负载的正确性、整体耗时与峰值内存，将验证有效的改动用于正式执行并复用仍有效的成果。保留轻量任务排除，不引入隐藏剩余时间、评分条件或减少必需输入的要求。
- 本版本效果：已补充有条件的瓶颈诊断与行动规则，并使发现描述和使用边界与之对应。实际模型遵循情况、性能和测评分数变化待后续实验观察；文案与加载检查不证明行为改善。

### 完整提示词快照

````markdown
---
name: resource-management
description: >-
  Plan memory use, concurrency and computation cost before resource-intensive work, scaling a
  workload, or adding parallel execution whose aggregate demand has not been
  verified to fit the available budget. Use after resource exhaustion or a measured
  throughput problem to revise the execution plan. Skip routine bounded operations
  and unchanged workloads with adequate measured resource use and throughput.
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
- Measured throughput falls below the estimate or fails to improve when concurrency
  increases, and the cause remains unresolved.

It applies to foreground and background execution alike. Duration alone does not
determine whether the skill is needed.

## When not to use

- Simple, bounded reads, searches, edits or status checks with a small, understood
  working set.
- Repeating a workload whose measured peak fits the current budget, with no
  unresolved throughput problem or material change to its scale, concurrency,
  implementation or available resources.
- Reasoning, planning or remote requests that do not start substantial local work.

Reuse recent resource observations while their assumptions remain valid; do not
turn every shell call into another profiling exercise.

## Budget, measure, then scale

1. Establish the task's effective memory and CPU allowance and current usage using
   the available environment tools. Include work already running and leave explicit
   headroom for the Agent, libraries and uncertain temporary allocations. Use the
   task or container allowance rather than the host's total capacity. Unknown usage
   is not free capacity.
2. Reuse an applicable completed run, or begin with one worker and a bounded trial
   that reaches the expensive stages. Observe correctness, peak memory and elapsed
   runtime, including parsing, conversion, temporary copies and retained results.
   When comparing execution settings, keep the representative input and required
   computation the same. File size and idle memory are not peak-memory estimates;
   a small trial may miss costs that appear only at the intended scale.
3. Choose batch size and concurrency conservatively. The combined estimated peak
   of planned work, existing work and reserved headroom must fit the effective
   allowance. CPU availability is another upper bound, not evidence that memory
   supports the same number of workers. Account for subprocesses and library threads
   inside each worker. If a configured limit disagrees with observed use or runtime,
   inspect the active backend and its worker/thread controls; setting a variable
   alone does not show that the running library honors it. Assume sharing only when
   it has been established.
4. If even one worker does not fit, reduce the working set, stream or batch the
   work, or use an appropriate storage-backed method before increasing concurrency.
   Release results no longer needed instead of accumulating an entire batch in
   memory. Preserve the task's required outputs and numerical or semantic correctness.
5. When throughput indicates an unresolved bottleneck, measure the relevant stages
   on representative input before changing more execution settings. Locate the cost
   in computation, repeated setup, data movement or contention. Choose a targeted
   change, such as reusing valid cached work, batching repeated setup or replacing
   a costly calculation with an equivalent implementation. Compare correctness,
   end-to-end runtime and peak memory on the same workload; a faster isolated stage
   does not establish a better overall plan. If the result does not support the
   diagnosis, revise it rather than repeating equivalent runs or adding workers.
   Apply a verified improvement to the actual execution path, reuse completed work
   that remains valid, and regenerate only results affected by the change. Do not
   reduce required input coverage or change the calculation's meaning to gain speed.
6. Check resource observations while scaling and revise the estimate when evidence
   changes. After suspected memory exhaustion, inspect available diagnostics and
   reduce demand before another attempt; do not repeat the same resource plan.
   An exit signal or code alone does not establish that OOM caused the failure.

Use the [feedback skill](../feedback/SKILL.md) for representative execution and
correctness evidence. If work must continue after the shell tool returns, also use
the [background-tasks skill](../background-tasks/SKILL.md); starting asynchronously
does not provide an additional resource budget.

## Common counterexample

A batch script sets a thread limit and launches one worker per CPU core, but the
loaded library uses a different threading runtime. Each worker starts its own
thread pool, increasing contention and memory demand; compressed inputs also
expand during parsing. An unchanged or slower run is reason to inspect the actual
workers and library, not assume that the limit took effect. Compare suitable
settings on the same workload and choose the measured plan that fits the budget
while preserving results. Neither one thread nor all available cores is always
the best setting.
````

## v4 · 2026-10-27 · 直接英文表达与明确使用边界

来源：本轮工作区未提交；skills_agent/builtin_skills/resource-management/SKILL.md。

- 发现的问题：部分指令较抽象或过长，使用与跳过条件需要更容易区分。
- 本版本目标：使用清楚、自然、直接的英文说明动作与边界；保留实质约束、原脚本接口和代码示例。六个实验 worktree 使用同一正文。
- 本版本效果：技能格式、本地引用和原有代码示例核对通过；已完成技能路由场景审阅。未运行新一轮真实模型测评，行为与分数效果待验证。

### 完整原文

````text
---
name: resource-management
description: >-
  Choose memory use, batch size and concurrency for substantial computation whose
  resource needs are unknown, when scaling work, or after resource exhaustion or
  measured slowdowns. Skip small bounded operations and unchanged workloads whose
  measured resource use and throughput are already adequate.
---

# Fit the work to the available resources

Choose the amount of data kept in memory, batch size and concurrency from observed
resource use. CPU count alone is not enough. This skill guides execution choices;
it does not enforce memory limits or prevent arbitrary commands from running out
of memory.

## When to use

Use this skill before execution when any of these conditions applies:

- Peak demand at the intended scale is unknown and could exceed the available
  resources.
- Increasing input size, batch size, retained intermediate results or concurrency
  could invalidate a previous resource estimate.
- Multiple operations will overlap, and their combined demand has not been checked
  against the resources left by existing work.
- A resource-related failure requires a different execution plan before retrying.
- Measured throughput is below the estimate, or adding workers does not improve it,
  and the cause is still unclear.

It applies to foreground and background execution alike. Duration alone does not
determine whether the skill is needed.

## When not to use

- Simple reads, searches, edits or status checks that use a small, known amount of
  data and memory.
- Repeating a workload whose measured peak fits the current budget, with no
  unresolved throughput problem or material change to its scale, concurrency,
  implementation or available resources.
- Reasoning, planning or remote requests that do not start substantial local work.

Reuse recent measurements while their relevant conditions remain unchanged. Do
not profile every shell call.

## Budget, measure, then scale

1. Check the memory and CPU allowance available to this task and how much is already
   in use. Use the task or container allowance, not the host's total capacity.
   Include other running work and reserve room for the agent, libraries and
   uncertain temporary allocations. Do not treat unknown usage as free capacity.
2. Reuse a suitable completed run. If none exists, start with one worker and a
   bounded trial that reaches the expensive stages. Check correctness, peak memory
   and elapsed time, including parsing, conversion, temporary copies and retained
   results. Compare execution settings on the same representative input and required
   computation. File size and idle memory do not show peak memory. A small trial
   may miss costs that appear only at the intended scale.
3. Choose batch size and worker count so that planned work, existing work and the
   reserved room fit within the allowance. Available CPUs limit concurrency but do
   not show how many workers memory can support. Include subprocesses and library
   threads inside each worker. If observed use or runtime disagrees with a configured
   limit, check the active backend and its worker and thread controls. Setting a
   variable does not prove that the running library obeys it. Count on shared data
   or resources only when that sharing has been confirmed.
4. If one worker still needs too much memory, reduce the data held at once, stream
   or batch the work, or use storage on disk before adding workers. Release results
   that are no longer needed. Preserve required outputs and the meaning and numerical
   correctness of the computation.
5. If throughput suggests a bottleneck, measure the relevant stages on representative
   input before changing more settings. Find whether time goes to computation,
   repeated setup, data movement or contention. Make a targeted change, such as
   reusing valid cached results, batching repeated setup or using an equivalent but
   less costly calculation. Compare correctness, total runtime and peak memory on
   the same workload. A faster isolated stage may not improve the whole run.
   If the result does not support the suspected cause, revise the diagnosis instead
   of repeating similar runs or adding workers. Apply a verified improvement to the
   actual execution path. Reuse completed work that is still valid and regenerate
   only affected results. Do not omit required inputs or change the calculation's
   meaning to gain speed.
6. Watch resource use while scaling and revise estimates when observations change.
   After suspected memory exhaustion, inspect available diagnostics and reduce demand
   before retrying. Do not repeat the same resource plan. An exit signal or code
   alone does not prove that an out-of-memory condition caused the failure.

Use the [feedback skill](../feedback/SKILL.md) when you need help collecting execution
results or checking correctness. If work must continue after the shell tool returns,
also use the [background-tasks skill](../background-tasks/SKILL.md). Running in the
background does not provide extra resources.

## Common counterexample

A script sets a thread limit and launches one worker per CPU core. The loaded
library uses a different threading runtime, so each worker starts its own thread
pool. Contention and memory use increase, and compressed inputs expand during
parsing. If the run is no faster or becomes slower, inspect the actual workers and
library controls. Compare settings on the same workload and choose the measured
plan that fits the available resources while preserving results. Neither one thread
nor all available cores is always the best choice.
````
