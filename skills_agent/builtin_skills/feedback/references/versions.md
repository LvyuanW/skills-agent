# Associate evidence with content versions

Use a record when later edits, expensive runs, or handoffs make it useful to know which content a check covered. Existing calls work without recording. Record directories belong inside the task's permitted writable locations; there is no required document or directory name.

## Existing checks and commands

```sh
python3 /absolute/feedback/scripts/check.py json result.json \
  --pointer /summary/count --equals-json 12 --record-dir .verify \
  --scope 'The delivered count equals the independently counted input records'

python3 /absolute/feedback/scripts/feedback.py run --log-dir .verify/logs \
  --record-dir .verify --manifest run-inputs.json --cwd /absolute/project \
  --scope 'Exercise the real entry point on the selected input' -- \
  /absolute/task-environment/python check_result.py

python3 /absolute/feedback/scripts/feedback.py status --record /absolute/.verify/run-ID/record.json
```

`--manifest` and `--scope` require `--record-dir`. `run --cwd` is also available without recording. The manifest file is located relative to the helper's working directory; paths inside it are relative to the command's `--cwd` (or current directory). A check always adds its actual subjects to any supplied manifest.

```json
{
  "subjects": ["result.json"],
  "dependencies": [{"path": "check_result.py", "label": "checker"}, "reference.json"],
  "outputs": ["check-details.json"],
  "environment": ["OMP_NUM_THREADS"],
  "limits": {"max_bytes": 268435456, "max_seconds": 10}
}
```

Declare only relevant files and explicitly selected nonsecret environment variables. The default hash budget is 256 MiB and 10 seconds **per file**. Directory traversal is not implicit: list the relevant files. Missing, unreadable, changing, unsupported or over-budget resources remain unknown rather than receiving an approximate identity. Time checks apply between filesystem reads; they cannot interrupt a blocked OS read. Environment/package completeness and dependency discovery are not supplied by this helper.

## What a record establishes

Every invocation creates a separate `run-ID/record.json`; parallel invocations do not share an append file. A started record is written before acquisition/execution, then finalized atomically. Interrupted helpers may leave a started record whose result is unknown. Full command logs remain at the paths returned by `run`; preserve them together with records when moving evidence.

The record includes actual argv/cwd, helper source hashes and Python runtime, declared subjects/dependencies/outputs, selected environment, observations and existing session/tool-call identifiers when available. The resolved command launcher is recorded, but does not identify every interpreter, imported library or external service used by that command.

- `check.py text/json` hashes the same raw byte buffer it parses. `capture.consumed` identifies that content even when line endings or Unicode matter. A byte buffer is the content actually checked, not a claim of an atomic filesystem snapshot.
- `check.py file` checks metadata, so it does not claim the contents were parsed. Metadata conditions and surrounding version observations are recorded separately.
- `run` records declared file fingerprints before and after the entry process. Equality at these boundaries does not exclude changes followed by restoration during the run, nor prove which files the program read. Multiple captures are not one atomic workspace snapshot.
- `completion_boundary=entry_process_exit` and `descendant_quiescence=unobserved` explicitly limit the observation. A command that launches asynchronous work and returns is not evidence that the work finished. Use the existing background-task route for long work and wait for its completion.
- Outputs may be newly generated or changed; these changes do not count as changed input dependencies. Their final fingerprints identify the observed outputs, not a guarantee of their generation history.

For arbitrary commands, exit zero remains execution success with `verdict=not_reported`. Only an explicit check reports conditions passing or failing. Checks preserve exits 0/1/2; runs preserve the command's exit status and timeout 124. Recording errors are explicit and cannot turn into successful verification.

## Reading current status

`status` reads current versions and never runs the recorded command. It keeps the historical execution/check result alongside `current_relation` and per-file `same`, `different`, `missing` or `unknown` values. A started record remains unknown. Compare these with `boundary_relation` and `capture_mode`; matching current files does not strengthen a historically uncertain run.

For directly consumed subjects or dependencies, the reference is the consumed bytes. Otherwise command inputs use the pre-run version, with the boundary relation retained; outputs use the observed post-run version. This also distinguishes a copied fixture or parsed experiment specification from an original file that changed before execution began. Symlink declarations retain their link targets rather than silently watching only a former resolved file.

Whether to recheck depends on the change and the recorded scope. For example, an unchanged result file can still satisfy a content check after its generator changes, while the claim that it was generated by the current generator needs different evidence. Declare generation dependencies on generation records and checking dependencies on checking records accordingly. A new expected condition is a different check; `status` does not invent or evaluate it.
