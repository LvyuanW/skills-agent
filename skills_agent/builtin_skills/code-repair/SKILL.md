---
name: code-repair
description: Repair reported code defects and verify behavior through affected callers, saved state and consumers.
---

Derive observable acceptance conditions and properties that must remain true from the user's reported failure and existing contracts, before choosing a fix. Keep those conditions independent of the proposed implementation. Separate established causes from hypotheses; a possible improvement is not evidence of a defect.

Trace the affected behavior through callers, transformations, persistence and consumers. Check the outcome where it is used, including later reads or repeated operations when relevant. Use the real internal path; mock external boundaries where needed and state what those substitutes cannot establish.

Make the regression distinguish the defect from the intended behavior. Confirm that setup succeeds, the input reaches the failing condition and the assertion observes its consequence. Where the original behavior is available, demonstrate failure for the intended reason before the fix and success afterward. Otherwise state the reproduction gap. A check that already passes on the faulty behavior may protect compatibility, but does not demonstrate that repair.

Choose the smallest change that satisfies the conditions while preserving existing contracts. When adapting a method, preserve the properties it guarantees and check them rather than imitating its syntax or language. Keep failures observable and changes limited in consequence. Use focused checks to resolve uncertainty before broadening the change or the test run.

Before claiming completion, compare the actual result and evidence with the original acceptance conditions. Ask whether those checks could also pass with the defect present. Inspect the relevant transitions: a saved-state claim needs a new reader, and a retained reference needs resolution after transformation. Consider independent review when it can resolve a consequential uncertainty; provide the original task, constraints, artifacts and evidence. Complete authorized follow-through, report unverified scope and avoid repeating checks without a reason. Passing-test counts alone do not establish coverage.

For command-based checks, read [scripts/run_check.py](scripts/run_check.py) or [scripts/run_check.sh](scripts/run_check.sh) as a reference. Both retain complete combined output, display a bounded tail and return the command's exit status to the caller. Their arguments are a log path followed by the command and its arguments. Reuse, adapt or write your own script; no fixed entry point or language is required. If changing how execution or output is handled, check both a successful and a failing command at the caller boundary. Printed status and process status are distinct; inspect relevant log ranges when the tail is insufficient.
