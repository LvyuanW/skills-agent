# Check the requirement at its observable boundary

Select a condition derived from the task or established contract that distinguishes an acceptable result from an unacceptable one. Obtain the actual saved artifact or execution result, then check it. A check covers only its stated condition; it does not certify the whole task. Simple work needs a relevant check, not a new test suite or a mandatory acceptance file.

Resolve each helper path from the loaded skill directory. These are separate shell actions with returned results between them; each action establishes its own variables.

## Saved data

If the contract requires a nonempty output file, observe that condition directly:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" file result.json --min-bytes 1
```

Successful acquisition establishes that a regular nonempty file exists. It does not establish valid JSON or correct values. If the agreed contract requires a specific nested result, inspect that value:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" json result.json --pointer /summary/processed --equals-json '12'
```

The expected 12 must come from the actual input or the contract, not from copying the program's answer. A failure reports the observed value or missing path. Compare the difference and inspect its cause before modifying the implementation. RFC 6901 pointers use `~1` for `/`, `~0` for `~`, and zero-based array indexes. JSON strings in `--equals-json` require JSON quotes, such as `'"ready"'`.

Literal content checks can inspect a required text fragment and an obsolete one:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" text result.txt --contains 'required literal text' --absent 'obsolete literal text'
```

Choose the actual requirement's literals. Returned source positions support later reading; a match does not prove semantic correctness or working behavior.

## Behavior and execution failures

When the requirement concerns behavior, exercise the behavior at its consumer boundary. Use an existing relevant test or a focused executable probe rather than checking whether implementation text contains a desired function name:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the command and arguments with the project's relevant executable check or a focused probe of the required behavior; this does not prescribe a language, test framework, or new script.

Inspect the returned status and output. First confirm that the check reached the behavior: a missing module, unavailable service or invalid test setup is an execution gap. An assertion on the actual outcome is behavioral evidence. Read the full saved logs when the bounded tail leaves this distinction uncertain.

After a failure, choose the smallest useful next action based on the diagnostic: acquire missing evidence, repair setup, or change the observed faulty behavior. Run the relevant check again after that change. Keep the requirement independent of the implementation. If evidence shows that the check or its interpretation is wrong, correct it against the original requirement and explain the mismatch; do not change an expected value merely to accept the current output.

When the requirement involves a later read or state transition, observe that actual transition at its relevant boundary. For visual or interactive requirements, inspect the native visual feedback or execute the relevant interaction; data checks alone do not establish those properties. Before claiming completion, compare covered conditions with the original task and identify any remaining unobserved scope.
