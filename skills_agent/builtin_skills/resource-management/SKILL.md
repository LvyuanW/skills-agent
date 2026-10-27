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
