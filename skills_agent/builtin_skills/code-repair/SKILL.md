---
name: code-repair
description: Investigate and repair code defects with reproductions and verification of affected data flows.
---

Start from the user's observed failure and define an observable acceptance condition. Separate established causes from hypotheses. Trace affected inputs, identifiers, errors and outputs through callers, transformations, persistence and consumers. Include later reads or repeated operations when they can change the outcome; changing a value at its origin does not establish that its eventual consumer receives or uses it correctly.

Build a focused regression that reaches the actual failure. Check its prerequisites: the input must exercise the claimed branch, required setup must succeed, and the assertion must inspect the consumer-visible result. Test the relevant counterexample as well as the successful path. Exercise the claimed transition: for example, a persistence check should use a new reader opening the saved data rather than reuse an in-memory object. Reuse the same regression on the old and changed behavior where practical.

Keep the affected internal path real; mock external boundaries such as remote APIs. A canned success response does not validate the internal operation it replaces or the actual behavior of an external service. For a retained reference, check resolution after the relevant transformation, not just its presence before it. A passing test proves its exercised path and assertions; the number of passing tests does not establish coverage.

After the fix, review the changed path and run the checks needed for its acceptance condition and affected neighbors. Consider an independent review when a separate investigation of cross-component behavior or counterexamples would add useful evidence. Supply the reviewer with the task, constraints and relevant materials; do not require a subagent for every edit. Complete the tests and usage instructions needed for the authorized repair without adding unrelated mechanisms. Report the result, supporting checks and any remaining uncertainty in proportion to the evidence.

Read [scripts/run_check.py](scripts/run_check.py) as a reference for preserving a command's exit status while limiting displayed output. Learn from it, adapt it, reuse it or write your own script; no fixed script entry point is required. It saves complete combined stdout and stderr to the specified log and displays only the tail, exit code and log path:

```sh
python "<skill-directory>/scripts/run_check.py" /tmp/regression.log python -m unittest tests.test_example
```

Use the actual exit code and assertions to judge execution. A success-looking final line or a successful formatting step cannot override an earlier command failure. Read targeted log ranges when the displayed tail is insufficient.
