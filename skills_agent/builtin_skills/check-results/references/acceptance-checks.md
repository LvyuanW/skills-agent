# Check the result where the requirement applies

Use this reference when a known task requirement can be checked against a saved file or an execution result. Choose the expected condition from the task or established contract, then inspect the actual result. A check supports only the condition it covers. It does not prove that the whole task is complete. Simple work does not need a new test suite or a separate requirements file.

Resolve helper paths from the loaded skill directory. The examples below are separate shell calls. Read a returned result before deciding the next action, and set variables in each call that uses them.

## Saved data

If the task requires a nonempty output file, check that condition directly:

```sh
check='/absolute/path/from/skill-catalog/check-results/scripts/check.py'
python3 "$check" file result.json --min-bytes 1
```

A successful check proves that a regular, nonempty file exists. It does not prove that the JSON or its values are correct. If the task requires a particular nested value, check it:

```sh
check='/absolute/path/from/skill-catalog/check-results/scripts/check.py'
python3 "$check" json result.json --pointer /summary/processed --equals-json '12'
```

The expected value, 12 in this example, must come from the input or the contract. Do not copy it from the program's answer. A failure reports the observed value or missing path. Inspect the difference and its cause before changing the implementation. RFC 6901 pointers use `~1` for `/`, `~0` for `~`, and zero-based array indexes. JSON strings in `--equals-json` need JSON quotes, such as `'"ready"'`.

For an exact-text requirement, check that the required text is present and obsolete text is absent:

```sh
check='/absolute/path/from/skill-catalog/check-results/scripts/check.py'
python3 "$check" text result.txt --contains 'required literal text' --absent 'obsolete literal text'
```

Replace these strings with the task's required text. Returned positions help you read the source later. A text match does not prove that the meaning or behavior is correct.

## Behavior and execution failures

When the requirement concerns behavior, run it through the interface that actually uses it. Use an existing relevant test or a focused executable check. Finding a function name in the source does not show that the behavior works:

```sh
feedback='/absolute/path/from/skill-catalog/check-results/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the command and arguments with a check suited to the required behavior. This example does not require Python, a particular test framework or a new script.

Inspect the returned status and output. First confirm that the check reached the intended behavior. A missing module, unavailable service or invalid test setup means it did not. An assertion about the actual outcome provides evidence about the behavior. Read the full saved logs if the tail does not show which happened.

After a failure, use the result to choose a focused next action: collect missing evidence, fix the setup or correct the demonstrated defect. Run the relevant check again after the change. Keep the original requirement as the basis for the expected result. If the check or its interpretation is wrong, correct it against that requirement and explain the mismatch. Do not change an expected value just to accept the current output.

If the requirement depends on a later read or a change of state, observe that part of the actual workflow. For appearance, inspect the actual image; for interaction, perform the relevant action. Data checks alone do not establish either. Before claiming completion, compare what was checked with the original task and identify any required conditions that remain unobserved.
