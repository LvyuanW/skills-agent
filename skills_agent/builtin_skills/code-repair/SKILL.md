---
name: code-repair
description: >-
  Diagnose and fix a reported or observed code failure, then verify the repair
  with a reproducer or focused check. Skip new feature development,
  cosmetic changes and refactoring without evidence of a defect.
---

## When to use this skill

Use this skill when a reported or observed failure needs diagnosis and repair. A possible improvement alone does not establish a defect. Handle new features, cosmetic edits and refactoring without a demonstrated defect through their normal workflows.

## Find the cause and check the repair

Establish the expected behavior from the reported failure and the existing contract. Do this independently of the proposed fix. Trace the relevant path from input to output until observations support a cause.

Choose an existing check, a focused probe or a regression test that reaches the failure and distinguishes the original behavior from the repaired behavior. A missing dependency or failed setup is not a reproduction of the suspected defect. Exercise the actual affected interface or behavior. If you substitute an external dependency, explain what that substitute cannot verify.

Make the smallest change that fixes the defect while preserving existing contracts. Check the affected behavior after the change. Ask whether the check could still pass if the defect were present; if so, improve that check. Keep expected values independent of the implementation. Broaden checking only to address relevant remaining uncertainty, and report any gap in reproduction or verification.

## Run checks and retain their output

You can use or adapt [scripts/run_check.py](scripts/run_check.py) or [scripts/run_check.sh](scripts/run_check.sh), or write a probe for the task. Both helpers take a log path followed by a command and its arguments. They retain complete combined output, show a bounded tail and preserve the command's exit status.

The helpers do not add an execution timeout. When a time limit is needed, the feedback skill's `run` operation provides one and saves separate full stdout/stderr logs. Read the relevant log range when the tail does not show whether the failure came from setup or from the behavior being tested.
