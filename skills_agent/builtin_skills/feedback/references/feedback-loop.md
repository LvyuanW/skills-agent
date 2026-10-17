# Let the observed result determine the next action

Feedback serves a decision: whether a requirement is met, whether a suspected failure occurred, or which change should come next. Select the smallest useful observation that can resolve that question. These examples are alternative routes, not stages every task must follow. When a later choice depends on a returned result, acquire that result before choosing the later action; independent observations may be grouped.

The examples assume a result already produced by the current task. Replace paths, ranges, expected conditions, and executable commands with the ones relevant to that task. Resolve helper paths from the loaded skill directory. Each shell action establishes its own variables.

## Acquire the relevant saved data

If the question is whether an output was saved at the expected location, acquire its metadata:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" file result.txt
```

Returned observation: file type, size, modification time and hash describe the actual file. A missing path is evidence about acquisition or saving; an existing file is not evidence that its contents are correct. When the decision concerns content, read the relevant range instead:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text result.txt --start 1 --end 80
```

Decision after observation: compare the returned content with the task's requirement. If the range does not cover the relevant content, obtain that missing range. For truncation within a long line, resume using the returned `next_line` as `--start` and `next_column` as `--column`. If a required condition is visibly wrong, make a supported correction. If it is met, proceed with the remaining task or finish when the required scope is complete.

For literal text, file properties or JSON values that admit an explicit expected condition, [acceptance-checks.md](acceptance-checks.md) shows how to check that condition and retain the observed difference. Choose the expected result from the task or established contract, independently of the produced answer.

## Observe execution when the requirement concerns behavior

Use an existing relevant check or a focused probe that exercises the required behavior. For example, if the project already provides an applicable check script:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the entire command and arguments with the actual executable check. The helper is not limited to Python or test runners, and this example does not require creating a new check script.

Returned observation: the actual exit code, timeout status, separate stdout/stderr tails and full log paths. Determine what the execution reached. A missing executable or dependency is an acquisition gap; an observed mismatch after the intended behavior ran is feedback about that behavior. When the tail is insufficient, read the relevant saved log range:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text /absolute/path/returned/in/observations/stderr_log --start 1 --end 80
```

Decision after observation: obtain missing evidence, resolve the execution gap, or change the demonstrated faulty behavior. A successful command supports only the conditions it exercised. After a relevant change, acquire a fresh result for those affected conditions instead of relying on the previous result.

## Acquire pixels when the question is visual

Use the [images skill](../../images/SKILL.md) and declare the selected image in the shell tool call. If the task already produced a bounded image, no preparation command is needed:

```json
{
  "name": "Inspect current work result",
  "command": "true",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/output.png", "label": "Current work result"}]
}
```

For a local HTML or SVG result, the installed browser can produce a screenshot:

```json
{
  "name": "Render and inspect preview",
  "command": "python3 /absolute/path/from/skill-catalog/feedback/scripts/feedback.py render /absolute/workspace/preview.html --output /absolute/workspace/.verify/preview.png --width 500 --height 400 --wait-ms 0",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/.verify/preview.png", "label": "Preview"}]
}
```

Choose a viewport and wait budget relevant to the property under review. A zero wait requests capture without a virtual-time budget; a nonzero value does not promise an exact animation timestamp. The command may print acquisition metadata and ordinary logs; `images` selects which files become native visual evidence after the command finishes. Use absolute host paths or local snapshot URLs, at most ten images per request and at most 1 MB per image. If only preparing files for later inspection, pass `images: []`. A background call registers future output paths and supplies the images with its completion result, not its launch receipt.

Decision after observation: inspect the actual native image for the task's visible requirements. If acquisition fails, address or report that gap instead of inferring appearance from a filename, hash, or successful render command. If the visible result differs from the requirement, make a relevant correction and acquire the updated image when needed to judge that correction. One screenshot covers its captured state and viewport; motion, interaction and other states require their own relevant observations.

## Decide when there is enough feedback

Finish when the observed result supports the task's required conditions and authorized follow-through is complete. Acquire more feedback when a meaningful uncertainty remains, rather than repeating checks solely because a stage ended. Keep the actual artifacts and available result references usable for later work; create additional notes or checkpoints only when they serve the task. Report the result and any condition that could not be observed.
