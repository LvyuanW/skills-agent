# Let the observed result determine the next action

Use this reference when you need to decide what to do after a check. The question may be whether a requirement is met, whether a suspected failure happened or which change to make next. Collect the smallest useful observation that can answer it. The examples are alternative routes, not stages for every task. Wait for a result before making a decision that depends on it. Independent observations may be collected together.

The examples assume that the current task has already produced a result. Replace paths, ranges, expected conditions and commands with ones that apply to the task. Resolve helper paths from the loaded skill directory. Set variables in every shell call that uses them.

## Read the relevant saved data

To check whether an output was saved in the expected location, read its metadata:

```sh
feedback='/absolute/path/from/skill-catalog/check-results/scripts/feedback.py'
python3 "$feedback" file result.txt
```

The helper returns the actual file's type, size, modification time and hash. A missing path indicates a problem finding or saving the file. An existing file does not show that its contents are correct. To check content, read the relevant range:

```sh
feedback='/absolute/path/from/skill-catalog/check-results/scripts/feedback.py'
python3 "$feedback" text result.txt --start 1 --end 80
```

Compare the returned content with the task's requirement. If the relevant content is outside the range, read the missing range. If a long line was truncated, resume with the returned `next_line` as `--start` and `next_column` as `--column`. Correct a demonstrated defect within the task's scope. If the condition is met, continue the remaining work or finish when the task is complete.

[acceptance-checks.md](acceptance-checks.md) shows how to check exact text, file properties or JSON values against an explicit expectation and keep the observed difference. Derive the expectation from the task or established contract, not from the answer being checked.

## Observe execution when the requirement concerns behavior

Use an existing relevant check or a focused probe that runs the required behavior. For example, the project may already provide a suitable check script:

```sh
feedback='/absolute/path/from/skill-catalog/check-results/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the command and arguments with the actual check. The helper can run more than Python or test runners, and this example does not require a new script.

The helper returns the actual exit code, timeout status, separate stdout and stderr tails, and paths to the full logs. Check how far execution got. A missing executable or dependency means the intended behavior was not tested. A mismatch after that behavior ran is evidence about its result. If the tail is not enough, read the relevant part of the saved log:

```sh
feedback='/absolute/path/from/skill-catalog/check-results/scripts/feedback.py'
python3 "$feedback" text /absolute/path/returned/in/observations/stderr_log --start 1 --end 80
```

Use the result to collect missing evidence, fix the execution setup or correct the demonstrated defect. A successful command supports only the conditions it exercised. After a relevant change, check the affected conditions again; the old result does not verify the new version.

## Inspect pixels when the question is visual

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
  "command": "python3 /absolute/path/from/skill-catalog/check-results/scripts/feedback.py render /absolute/workspace/preview.html --output /absolute/workspace/.verify/preview.png --width 500 --height 400 --wait-ms 0",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/.verify/preview.png", "label": "Preview"}]
}
```

Choose a viewport and wait budget suited to the property being checked. A zero wait requests capture without a virtual-time budget. A nonzero value does not guarantee an exact animation timestamp. The command may print capture metadata and ordinary logs, but `images` selects which files are supplied to the model as images after the command finishes. Use absolute host paths or local snapshot URLs, at most ten images per request and at most 1 MB per image. If you are only preparing files for later inspection, pass `images: []`. A background call registers the future output paths and supplies images with its completion result, not its launch receipt.

Inspect the actual image against the task's visual requirements. If capture or attachment fails, fix or report that problem. A filename, hash or successful render command cannot show the appearance. If the visible result is wrong, make a targeted correction and inspect an updated image when needed to check the change. One screenshot covers only its captured state and viewport. Check motion, interaction and other required states through observations that can show them.

## Decide when there is enough feedback

Finish when the observed results support the task's requirements and the authorized follow-up work is complete. Collect more feedback when an important question remains. Do not repeat checks just because a stage has ended. Keep the actual files and result references available for later work. Add notes or checkpoints only when they help the task. Report the result and any required condition you could not observe.
