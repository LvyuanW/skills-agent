---
name: code-repair
description: Diagnose and repair code defects using observed behavior, relevant contracts and focused executable checks.
---

Derive observable acceptance conditions and properties that must remain true from the user's reported failure and existing contracts, before choosing a fix. Keep those conditions independent of the proposed implementation. Separate established causes from hypotheses; a possible improvement is not evidence of a defect.

Locate the part of the execution path that can explain the observed failure. Follow inputs and outputs across relevant boundaries until the cause is supported; expand the investigation when the result indicates another dependency. Check the outcome where the requirement is observable. Use real affected code, substituting external dependencies only when needed and stating what those substitutes cannot establish.

Select an existing relevant check, a focused executable probe, or a new regression when one is needed to resolve the uncertainty or protect the repaired behavior. Confirm that setup succeeds, the input reaches the relevant condition and the observation distinguishes the defect from the intended behavior. Compare the original and changed behavior when practical; otherwise state the reproduction gap. Scale the checks to the change and its consequences rather than requiring a new test suite for every edit.

Choose the smallest change that satisfies the conditions while preserving existing contracts. When adapting a method, preserve the properties it guarantees and check them rather than imitating its syntax or language. Keep failures observable and changes limited in consequence. Use focused checks to resolve uncertainty before broadening the change or the test run.

After a useful change, obtain feedback from the affected behavior before choosing further changes. Exercise lifecycle transitions only when they are part of the requirement or suspected failure; choose their boundaries from the actual code and contract. Before claiming completion, compare the observed result with the original acceptance conditions and ask whether the checks could also pass with the defect present. Consider independent review when it can resolve a consequential uncertainty; provide the task, constraints, artifacts and evidence. Complete authorized follow-through and report unverified scope. Passing-test counts alone do not establish coverage.

Use the feedback skill's `run` operation when you need bounded command output with separate full logs and timeout handling. For simple combined logging, resolve [scripts/run_check.py](scripts/run_check.py) or [scripts/run_check.sh](scripts/run_check.sh) against this skill's directory and execute it directly through `shell`. Both take an absolute log path followed by the command and its arguments, retain complete combined output, display a bounded tail and return the command's exit status. Read or adapt their source when customization is needed. Printed status and process status are distinct; inspect relevant log ranges when the tail is insufficient. When repairing command execution or error propagation, include failure behavior in the relevant checks.
