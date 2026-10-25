# Focused experiments through the real entry point

Use an experiment when a concrete decision needs observed behavior: distinguishing explanations, exercising an important input case, checking a correction, or measuring representative work before a costly run. Existing relevant tests and ordinary `feedback.py run` remain appropriate. These helpers are optional capabilities, not stages every task must complete.

`scripts/experiment.py` offers `run` and `compare`. Resolve its absolute path from the loaded skill directory and invoke it with Python 3.11+. It uses only the standard library; the command under test can use a different, explicitly selected interpreter. The helpers do not install dependencies, discover a correct answer, or create a new agent tool.

## Run a real entry with a small fixture

Supply the actual application command and its required working directory. A thin probe may call that entry and expose its outputs, but a reimplementation of the expected algorithm does not establish that the delivered entry works.

```json
{
  "schema_version": 1,
  "purpose": "Check that the existing CLI totals two known records.",
  "command": [
    "/project/.venv/bin/python", "/project/total.py",
    "--input", "{fixture}/input.json", "--output", "{output}/result.json"
  ],
  "cwd": "/project",
  "fixtures": [
    {"path": "input.json", "json": {"items": [2, 3]}}
  ],
  "outputs": [{"name": "result", "path": "result.json"}],
  "timeout": 30,
  "manifest": {"subjects": ["/project/total.py"]}
}
```

```sh
python3 /absolute/skill/feedback/scripts/experiment.py run \
  --spec /project/total-experiment.json --root /project/.verify
```

For a small one-off experiment, pass the JSON directly on stdin with `--spec -`; no separate planning document or hand-written spec file is needed. The helper saves the exact input bytes with its evidence. File specs resolve relative paths against the spec's directory; stdin specs use the shell's calling directory.

Each invocation creates fresh `fixture`, `output` and log directories plus a versioned record. A `setup.json` intent is saved before fixture materialization; cancellation may leave it `started`, which is not completed execution. `{fixture}` and `{output}` are substituted in argv elements without invoking a shell. Use an explicit shell command only when shell syntax is genuinely needed. Relative `cwd`, `copy_from` and comparison file paths are resolved against the spec file's directory; manifest paths are resolved against the declared execution `cwd`.

A fixture entry has `path` and exactly one of `text`, `json`, or `copy_from`. Targets must remain within the new fixture directory. Copy individual declared files; this does not snapshot the workspace. `outputs` names the files to collect relative to the new output directory. Either list may be empty when the observation does not need it. A file's existence is an observation, not evidence that its contents are correct.

The existing `run_process` performs execution and timeout handling. Native shell/background management still owns cancellation and process-group cleanup; the helper does not daemonize or create a second job manager. For longer work, use the existing managed background capability and allow its deadline to cover the command plus preparation and recording. A timeout is a bound, not evidence that the implementation is wrong.

The returned `execution` preserves the child exit code, startup errors, timeout flag, full separate logs and bounded tails. `run` returns that command status, or 124 on timeout; setup, startup and output-acquisition failures return 2. A child exit 2 is distinguishable from setup failure through `phase`, `execution.exit_code` and `startup_error`. `verdict: not_compared` means no correctness judgment has been made.

## Compare saved evidence without running again

Derive the expected value or relationship independently from the task, an established contract, a directly calculable case, or a justified invariant. A previous implementation is only a reference; agreement does not prove that both implementations are correct. If the purpose is exploratory, state the difference observed without turning it into a completion requirement.

```json
{
  "schema_version": 1,
  "basis": "The input records are 2 and 3; the documented CLI returns their sum.",
  "checks": [{
    "id": "total",
    "kind": "exact",
    "left": {
      "path": "/project/.verify/experiment-EXAMPLE/output/result.json",
      "run_record": "/project/.verify/run-EXAMPLE/record.json",
      "pointer": "/total"
    },
    "right": {"value": 5}
  }]
}
```

```sh
python3 /absolute/skill/feedback/scripts/experiment.py compare \
  --spec /project/compare-total.json --root /project/.verify
```

Use the actual paths returned by `run`. `compare` reads existing artifacts and writes only its own evidence record. It does not rerun commands, change expectations, or repair outputs. `checks` must be nonempty and each check needs a unique `id`, a `kind`, `left`, and `right`.

A selector is either `{"value": ...}` or `{"path": "...", "format": "json", "pointer": "/..."}`. The format defaults to JSON; `format: text` compares exact UTF-8 text and has no JSON pointer. JSON pointers follow RFC 6901. Missing paths, invalid encodings, duplicate JSON keys and nonstandard NaN/Infinity cannot pass.

For an execution-produced artifact, include `run_record`. This verifies finalized execution, the expected exit code, and the artifact's recorded output fingerprint against the bytes actually compared. The default expected exit code is 0; a negative test may explicitly set `expected_exit_codes: [2]`. Startup failures, unfinished or timed-out execution, a missing fingerprint, or an altered artifact remain inconclusive. Declared input/helper versions that changed or could not be established as stable also prevent record-backed comparison; this is not a complete dependency trace. For intentionally mutable state, preserve the immutable input and identify the changed state as output instead of silently treating an input version as fixed.

### Available conditions

| Kind | Required additions | Meaning |
| --- | --- | --- |
| `exact` | None | Strict JSON equality, or exact text; boolean and number differ, arrays retain order |
| `numeric` | `alignment`, `atol`, `rtol`, `unit` | `abs(left-right) <= atol + rtol*abs(right)` |
| `set` | Optional `relation` | JSON arrays have equal distinct elements, or the left is a subset/superset of the right; duplicates intentionally do not matter |
| `multiset` | Optional `relation` | The same relations, respecting element multiplicities |
| `paired` | Numeric additions plus `relation` | Check an explicitly justified relation from left to right |

Set/multiset `relation` defaults to `equal` and also accepts `subset` or `superset`. For a multiset, `[1,1]` is not a subset of `[1,2]`. Results include bounded missing/extra element examples and counts relative to the right-hand side; only the differences relevant to the chosen relation determine passing.

Numeric tolerances must be finite, nonnegative JSON numbers, never implicit defaults or strings. `unit` is an explicit label such as `K` or `unitless`; the helper does not infer units or convert values. All selected numeric values must be finite numbers, not booleans. Numeric arrays must be nonempty.

Choose numeric alignment explicitly:

- `{"kind":"scalar"}` for two scalar numbers.
- `{"kind":"position"}` for equal-length arrays in a contractually corresponding order.
- `{"kind":"key","key":"/id","value":"/value"}` for arrays of rows; IDs must be unique non-null scalar values with identical key sets. Reordering is allowed, silently dropping unmatched rows is not.

`paired` supports `equal`, `nondecreasing`, `nonincreasing`, `delta`, and `scale`. With left `a` and right `b`, `delta` requires a numeric `delta` and compares `b-a` to it; `scale` requires a numeric `factor` and compares `b` to `a*factor`. The other relations take no parameter. Tolerance uses the right-hand expected quantity: `a` for equality/monotonicity, the declared delta, or `a*factor`. Thus a positive tolerance permits that much slack in a monotonic condition; choose zero when the condition is exact.

For example, a justified doubling relation on corresponding numeric arrays uses:

```json
{
  "id": "doubling",
  "kind": "paired",
  "left": {"path": "base.json"},
  "right": {"path": "doubled.json"},
  "relation": "scale", "factor": 2,
  "alignment": {"kind": "position"},
  "atol": 0.000001, "rtol": 0, "unit": "unitless"
}
```

Comparisons return 0 when the supplied conditions pass, 1 for an observed mismatch, and 2 for invalid input, unavailable evidence or an inconclusive comparison. A mismatch includes bounded differences and total counts. This never certifies overall task completion.

## Keep the comparison meaningful and bounded

Input alignment is necessary but does not establish every experimental premise. If relevant, add a `premises` object with text fields `fixed`, `varying`, `inputs`, `randomness`, `units`, and `side_effects` describing what is held fixed and what varies, including when comparing two runs. These are recorded declarations, not properties the helper independently certifies.

Record or control seeds, configuration, normalization and sampling conditions when they affect the conclusion. A single stochastic pair does not establish a reliable improvement, and matching seed numbers alone do not guarantee identical random draws across implementations. Use a task-appropriate repeated experiment only when that uncertainty affects the decision. The helper does not prescribe repetitions or search parameter grids.

Fresh output directories prevent accidental reuse of prior outputs. They do not restrict the tested command's access to the host, network, credentials, database or workspace. Use the task's authorized resources and existing sandbox when needed; do not infer permission for new side effects from an experiment spec. The helpers retain their evidence and do not delete workspace files.

Choose a small case that reaches the relevant behavior. Set runtime limits from the planned work, inspect concrete gaps, and reuse evidence while its relevant inputs and implementation remain applicable. A pilot does not replace a required full result. File reads are bounded to 256 MiB per artifact; select an appropriate exported slice for larger data rather than claiming an unobserved full comparison.
