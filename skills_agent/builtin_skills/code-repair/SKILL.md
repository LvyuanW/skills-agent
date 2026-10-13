---
name: code-repair
description: Diagnose a reported code failure and verify a minimal repair with a reproducer or focused check that distinguishes the defect from the intended behavior.
---

Derive the expected behavior from the reported failure and existing contract, independently of the proposed fix. Follow the relevant input-to-output path until observations support a cause. A possible improvement is not evidence of a defect.

Choose an existing check, a focused probe or a regression that reaches the failure and would distinguish the original and repaired behavior. A missing dependency or failed setup does not reproduce the suspected defect. Exercise the real affected boundary; when substituting an external dependency, state what the substitute cannot establish.

Make the smallest change that satisfies the condition while preserving existing contracts. After changing it, inspect the affected behavior and ask whether the check could still pass with the defect present. Broaden checks only for relevant remaining uncertainty. Keep expected values independent of the implementation; report any reproduction or verification gap.

For executable checks, adapt or reuse [scripts/run_check.py](scripts/run_check.py) or [scripts/run_check.sh](scripts/run_check.sh), or write the task's own probe. Both references take a log path followed by a command and its arguments, keep complete combined output, show a bounded tail and preserve the command's exit status. They do not add an execution timeout. When that is needed, the feedback skill's `run` operation provides a time limit and separate full stdout/stderr logs. Inspect the relevant log range when a tail cannot distinguish setup failure from the behavior under test.
