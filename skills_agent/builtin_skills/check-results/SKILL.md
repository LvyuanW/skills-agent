---
name: check-results
description: >-
  Use when you need to check whether a calculation, generated file, or running
  program does what the task requires. Also use when a separate test behaves
  differently from the real run, or when a change may not have reached the final
  files. Choose a check that answers the question, inspect the actual result,
  and confirm the correction. Skip another check when existing evidence already
  answers the same question and the relevant inputs, code, and files are unchanged.
---

# Check the actual result

Decide what you need to know before running another check. Choose what to inspect, read the actual result, and use it to decide whether the work needs a correction. Pure planning and lookups that do not need a result check can proceed without this skill.

An existing tool result or a short file read may provide all the evidence you need. Use it directly. If you still need to decide what to check or what the result means, the method below can help even when no new execution is needed. Skip another check when the earlier observation still answers the question and the relevant inputs, code and files have not changed.

## Choose a check that answers the question

Run the program to check its behavior, read the saved file to check its contents, and inspect the image to check its appearance. If correctness depends on how a file is used, open or process the saved file with the intended program or command, including the required input preparation. A successful write or capture alone does not show that the content is correct.

Automatic workspace checkpoints preserve saved versions; they are not verification results. When you need to establish what changed since a check, compare the relevant file versions using the [file-editing skill's history instructions](../file-editing/SKILL.md#use-automatic-file-history). Apply earlier evidence only to contents and execution conditions for which it still holds.

Before using a separate test to change the work, confirm which inputs it used and which code it ran. It may differ from the real task. Keep experiments in separate scripts or files when they could change the execution conditions or overwrite useful outputs. A failure in the test does not by itself show that the real run has failed.

Choose a check that can answer the question you identified. For example, zeros or symmetric inputs may hide a defect that a less uniform input reveals. A separate formula, an input with a known answer or a reference supplied with the task may reveal an error that the implementation's own checks miss. Two implementations can agree and still be wrong if both rely on the same unverified definition or method. Choose the check that fits the problem; these examples are not a required sequence.

## Use the result to guide the work

When results conflict, check whether the problem is in the work, the check itself, the way the separate test ran, or an extra condition you introduced. If you cannot yet tell, choose a check that can tell those causes apart before making a correction. Use the [investigation skill](../investigation/SKILL.md) when you need help deciding between explanations, definitions or methods.

Decide what to change from the original requirement and what the result shows. Do not change the work just to make a check pass. Remove only extra conditions you added without support in the task; keep user requirements that have not yet been met. Make the correction that the evidence supports, within the scope the user allowed.

After a correction, update the affected final files and any results that use them. Read those outputs or run the appropriate check to confirm they contain the correction. Fixing a separate test is not enough if the final files still contain the error. Reuse evidence that still applies. Repeat broader checks only when a change, conflicting result or newly found gap gives a reason to do so.

## Collect execution and file results

[scripts/feedback.py](scripts/feedback.py) provides reference helpers. Use them directly, read or adapt their source, or write a check suited to the task. Direct use requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md. Set variables in each shell call that needs them; they do not persist between calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes the argument list directly. Use `sh -lc '...'` explicitly when the command needs shell syntax. `run` and `render` preserve failure status and return 124 on timeout. Inspect `timed_out`, the actual `exit_code`, collection errors and any truncation. A log tail may omit the cause, so read the full saved logs when needed. The helpers do not install dependencies.

Before substantial computation with an unknown cost, try a small representative input that reaches the expensive stages. Check correctness and measure runtime or progress before scaling up. An extrapolation remains an estimate, and the trial does not replace the required full result. Choose a suitable sample size and stopping condition from the task, such as an explicitly available execution deadline, iteration limit, convergence criterion or interval without progress. `run --timeout` stops the command; set it for the planned work.

## Inspect images

Always use the [images skill](../images/SKILL.md) when attaching an image. Declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images in total, each at most 1,000,000 bytes before base64 encoding. `images.py prepare` makes copies within the limit and keeps the originals. `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. For new images, use its in-memory `save_bounded` before writing. The feedback helpers reject oversized images.

Declare an image as `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`. Local `file://` snapshot URLs also work. The optional `source_url` keeps the original source reference. After execution, the runtime reads and snapshots the declared files and supplies their pixels and snapshot URLs before filtering text.

Background jobs register image paths at launch and attach the images only with the completion result. The explicit `images` value, including `[]`, controls attachment independently of logs, JSON metadata and other stdout. Render or preparation metadata does not attach an image; do not call an extra attachment helper. A header check identifies the format only. Inspect the actual image or the reported collection error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` takes a fresh screenshot in an isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. One screenshot covers only the captured viewport and state. PDF and office files need a suitable reader or rendered pages first. Extracting their text does not check their appearance.

## Check explicit saved values

[scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes a missing value from null and booleans from numbers. Exit codes are 0 when the supplied conditions pass, 1 for a mismatch and 2 for a collection or input failure. Derive expected values from the task, not from the answer being checked.

Read [references/acceptance-checks.md](references/acceptance-checks.md) for examples of checking specific conditions. Read [references/feedback-loop.md](references/feedback-loop.md) when you need to connect a returned observation to the next action. Neither is a fixed checklist.
