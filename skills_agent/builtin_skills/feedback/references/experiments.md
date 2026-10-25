# Focused experiments through the real entry point

Use an experiment when a concrete decision needs observed behavior: distinguishing explanations, exercising an important input case, checking a correction, or measuring representative work before a costly run. Existing relevant tests and ordinary `feedback.py run` remain appropriate. These helpers are optional capabilities, not stages every task must complete.

`scripts/experiment.py` offers `run` and `compare`. Resolve its absolute path from the loaded skill directory and invoke it with Python 3.11+. JSON, text and CSV need only the standard library; NPY/NPZ reading uses NumPy if the selected interpreter already has it. The command under test can use a different, explicitly selected interpreter. The helpers do not install dependencies, discover a correct answer, or create a new agent tool.

## Compare files you already have

For one comparison, pass two existing files and its basis directly. No spec file, new execution, or evidence directory is required. This also works on an existing checker's JSON report: select the reported value and independently justified expected value, rather than reimplementing or rerunning the checker.

```sh
python3 /absolute/skill/feedback/scripts/experiment.py compare actual.json expected.json \
  --left-pointer /metrics/count --right-pointer /count \
  --basis 'The documented input contract contains exactly 12 records.'
```

This defaults to strict JSON equality. Use `--format text` for exact UTF-8 text, or `--kind set` / `--kind multiset` for explicitly unordered arrays. The output reports only the supplied comparison, not whether the original checker ran successfully or whether its conclusion is valid. Include `--left-record` or `--right-record` with an existing producer record from native `shell.evidence`, `feedback.py run` or `experiment.py run` when the conclusion depends on successful execution; declare the compared artifacts as `outputs`. A failed producer, timeout, changed output, or unstable declared inputs makes the comparison inconclusive even when the files match. A previous implementation is a reference, not an automatic oracle.

For CSV numbers, select the value column and row identifier. IDs remain strings and must be unique with identical key sets on both sides; row order may differ. Numeric intent, tolerances and units are explicit:

```sh
python3 /absolute/skill/feedback/scripts/experiment.py compare actual.csv reference.csv \
  --format csv --column pressure --key sensor_id --kind numeric \
  --atol 0.01 --rtol 0 --unit Pa \
  --basis 'The supplied reference records specify pressure for these sensor IDs.'
```

Without `--key`, a numeric CSV column needs `--alignment position` and a justified row order. Non-numeric CSV comparisons retain exact text: `01` and `1` differ, and an empty string is not zero. Duplicate/empty headers, malformed rows, duplicate/empty keys, missing keys, nonnumeric values and NaN/Infinity in numeric columns cannot pass. Only the selected value and key columns are compared.

For NPY/NPZ, use an existing task interpreter with NumPy. Select an NPZ array by its exact saved name; NPY does not take `--array`. A basic `--slice` is optional and accepts comma-separated integer indices or `start:stop[:step]`, without expressions or advanced indexing:

```sh
/project/.venv/bin/python /absolute/skill/feedback/scripts/experiment.py compare actual.npz reference.npz \
  --format npz --array pressure --slice '0:100,:' --kind numeric \
  --alignment position --atol 0.01 --rtol 0 --unit Pa \
  --basis 'Corresponding rows and columns follow the supplied pressure reference.'
```

Arrays retain their selected shape, including dimensions after empty axes; they are never flattened implicitly. Numeric comparison requires nonempty, finite, correspondingly shaped values. Set/multiset comparison of NumPy data requires a one-dimensional selection. Boolean, integer and real dtypes up to 64 bits are supported; object/pickle, string and complex data are rejected. NPY header versions 1 and 2 are supported. The reader bounds each source/NPZ member and declared array storage to 256 MiB, dimensionality to 16, and the selected result to 250,000 elements before conversion to Python values. Slicing reduces the comparison, not initial array loading; export a smaller artifact if loading exceeds the budget. Missing NumPy returns an actionable inconclusive result; it is never installed automatically.

`compare` is read-only by default and reports the fingerprints of consumed files. Add `--root /project/.verify` only when retaining a versioned comparison is useful; this saves the generated spec and evidence. The same optional recording applies to `compare --spec FILE` and `compare --spec -`. Use a spec for multiple checks, literals, different selectors on each side, explicit negative-test exit codes, or paired relations. Existing `--spec ... --root ...` calls remain supported. Do not mix a spec with the direct-selection flags.

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

Each run creates fresh `fixture`, `output` and log directories plus a versioned record. A `setup.json` intent is saved before fixture materialization; cancellation may leave it `started`, which is not completed execution. `{fixture}` and `{output}` are substituted in argv elements and `cwd` without invoking a shell. Set `"cwd": "{fixture}"` when the real entry expects input paths relative to its working directory. Use an explicit shell command only when shell syntax is genuinely needed. Other relative `cwd`, `copy_from` and comparison file paths are resolved against the spec file's directory; manifest paths are resolved against the actual execution `cwd`.

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

Use the actual paths returned by `run`. `compare` reads existing artifacts; the example's `--root` requests its own evidence record. It does not rerun commands, change expectations, or repair outputs. `checks` must be nonempty and each check needs a unique `id`, a `kind`, `left`, and `right`.

A selector is either `{"value": ...}` or a file selector. JSON is the default: `{"path": "...", "format": "json", "pointer": "/..."}`. JSON pointers follow RFC 6901. Other formats do not accept a nonempty pointer:

- `{"path":"report.txt","format":"text"}` selects exact UTF-8 text.
- `{"path":"results.csv","format":"csv","column":"pressure","key":"sensor_id","value_type":"number"}` selects keyed numeric values; `key` is optional, and `value_type` defaults to `text`. With `key`, rows become `{"key":...,"value":...}` for numeric alignment `{"kind":"key","key":"/key","value":"/value"}`.
- `{"path":"values.npy","format":"npy","slice":"0:100,:"}` selects an array or its optional slice.
- `{"path":"values.npz","format":"npz","array":"pressure","slice":"0:100,:"}` selects a named array; `slice` is optional.

Missing paths, invalid encodings, duplicate JSON keys and nonstandard NaN/Infinity cannot pass. Format-specific restrictions and bounds are the same as for direct comparisons.

For an execution-produced artifact, include `run_record`. This verifies finalized execution, the expected exit code, and the artifact's recorded output fingerprint against the bytes actually compared. The default expected exit code is 0; a negative test may explicitly set `expected_exit_codes: [2]`. Startup failures, unfinished or timed-out execution, a missing fingerprint, or an altered artifact remain inconclusive. A native record reporting residual work that required cleanup, forced cleanup or explicitly unconfirmed cleanup is also inconclusive: the entry's exit code alone does not establish completion of its child work. Older records without cleanup fields remain readable. Declared input/helper versions that changed or could not be established as stable also prevent record-backed comparison; this is not a complete dependency trace. For intentionally mutable state, preserve the immutable input and identify the changed state as output instead of silently treating an input version as fixed.

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
- `{"kind":"position"}` for rectangular arrays of the same shape in a contractually corresponding order.
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
