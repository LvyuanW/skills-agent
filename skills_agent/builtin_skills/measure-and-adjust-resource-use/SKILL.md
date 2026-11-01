---
name: measure-and-adjust-resource-use
description: >-
  Use before a large local calculation, simulation, recording, or file conversion
  when its memory needs or running speed are not yet known. Also use when work
  becomes larger, keeps more data in memory, overlaps other jobs, runs unexpectedly
  slowly, or fails because resources may be exhausted. Measure the work and use
  what you learn to choose batch sizes and how many workers or threads to run.
  Skip small operations with known low resource use, ordinary reading and reasoning,
  and unchanged work that already has suitable measurements.
---

# Fit the work to the available resources

Consider the work the command will start. A short command can launch a large
simulation or keep a whole video in memory. Before making the work larger or
running jobs together, check whether earlier measurements still apply. Use a
small but useful trial when you need new measurements, then choose how to run the
full work while preserving its required inputs and outputs.

## When to use

Use this skill when the next run needs a new estimate or a different execution
plan. This includes:

- Starting work whose peak memory use or running speed is not known.
- Expanding a short trial to the full run, or reaching a costly stage that the
  trial did not include.
- Using larger inputs or batches, keeping more cached data or intermediate
  results, or running more workers or threads.
- Running jobs together before checking how much they will need in total and how
  much is already used by other work.
- Retrying after a failure that may have exhausted resources.
- Seeing a slower run than expected, or finding that adding workers does not
  improve speed.

These conditions apply to both foreground and background jobs. A long duration
alone does not mean that a new resource check is needed.

## When not to use

- Ordinary reads, searches, edits or status checks that use a small, known amount of
  data and memory.
- Repeating work whose measured memory use fits the current allowance and whose
  speed is adequate, when its size, code, workers, threads and available resources
  have not changed.
- Reasoning, planning or remote requests that do not start substantial local work.

Reuse recent measurements while the conditions they describe remain unchanged.
Do not measure every shell call.

## Budget, measure, then scale

1. Check the memory and CPU limits that apply to this task, including container
   limits. The host's total capacity may not be available. Check what other work
   already uses and leave room for the agent, libraries and temporary allocations.
   Do not count unknown usage as free capacity.
2. Reuse measurements from a suitable completed run. If none exist, start with one
   worker and a short trial that reaches the expensive stages. Check correctness,
   peak memory and elapsed time, including reading and converting inputs,
   temporary copies and results kept in memory. Compare execution settings on the
   same input and required calculation. File size and memory use before the run do
   not show the peak. A short trial may miss growth over time or a stage reached
   only in the full run; account for those before expanding it.
3. Choose batch size and worker count so that the new work, existing jobs and
   reserved room fit within the allowance. CPU count alone does not show how many
   workers memory can support. Count subprocesses and library threads inside each
   worker. If the observed use or speed does not match a configured limit, check
   which library or backend is running and how it controls workers and threads.
   Setting a variable does not prove that the library obeys it. Count data as
   shared only after confirming that workers actually share it.
4. If one worker still needs too much memory, keep less data in memory at once:
   stream or batch the work, or use disk storage before adding workers. Release
   data that is no longer needed, while preserving useful results. Keep the
   required outputs, the meaning of the calculation and its numerical correctness.
5. If the work runs too slowly, measure its stages before changing more settings.
   Find whether time goes to calculation, repeated setup, moving data or workers
   competing for resources. Change the part the measurement identifies. For
   example, reuse valid cached results, combine repeated setup, or use an
   equivalent calculation that costs less. Compare correctness, total runtime and
   peak memory on the same workload. A faster single stage may not improve the
   whole run. If the result does not support the suspected cause, revise the
   explanation instead of repeating similar runs or adding workers. Apply a
   verified improvement to the command or code that runs the actual task. Reuse
   completed work that remains valid and regenerate only affected results. Do not
   omit required inputs or change the calculation's meaning to gain speed.
6. Watch resource use as the work grows and update estimates when observations
   change. After suspected memory exhaustion, inspect available logs and resource
   measurements before retrying. If they confirm that demand exceeded the limit,
   reduce it before the next run. Do not repeat a plan that has already exhausted
   resources. An exit signal or code alone does not prove that memory was the cause.

Use the [inspect-files-and-run-checks skill](../inspect-files-and-run-checks/SKILL.md) when you need help checking
actual results or confirming correctness. If work must continue after the shell tool returns,
also use the [start-background-jobs skill](../start-background-jobs/SKILL.md). Running in the
background does not provide extra resources.

## Common counterexample

A script sets a thread limit and launches one worker per CPU core. The loaded
library does not use that setting, so each worker starts its own thread pool.
Memory use rises, workers compete for CPUs, and compressed inputs expand when
read. If the run is no faster or becomes slower, inspect the actual workers and
library controls. Compare settings on the same workload and use the measured
choice that fits the available resources while preserving results. Neither one
thread nor all available cores is always the best choice.
