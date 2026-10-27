---
name: feedback
description: >-
  Inspect actual output or behavior when the next decision needs execution logs,
  saved text or JSON values, or a rendered HTML/SVG image. Skip planning and lookups
  that need no execution or output check, or when existing observations already
  answer the question. Use the images skill as well whenever attaching images.
---

# Check the actual result

Use this skill when you need an observation from execution or a saved artifact to decide what to do next. It provides ways to collect and inspect results. Pure planning and lookups that do not need such a check can proceed without it. When the cause of conflicting results needs investigation, use the [investigation skill](../investigation/SKILL.md) as needed.

If the current tool result or a simple targeted read already provides the evidence you need, use it directly. You do not need to load this skill for an additional check.

## Choose evidence for the question

Run the behavior to check behavior, read saved content to check values, and inspect pixels to check appearance. If correctness depends on how a file is used, open or process the saved file through its intended consumer, entry point and relevant preprocessing steps. A successful write or capture alone does not prove that the content is correct.

Before applying a diagnostic result, confirm which inputs and execution path the check used. A separate probe does not automatically reproduce the task's actual execution. Preserve valid outputs and try changes to execution conditions separately.

Choose a check that addresses the unresolved question. For example, zeros or symmetric inputs may hide a defect that a less uniform input reveals. An independent formula, an input with a known answer or a task-provided reference may test conditions shared by the implementation's own checks. A second implementation that makes the same assumptions offers little independent evidence. Use the appropriate method; these examples are not a required sequence.

## Use the result to guide the work

When results conflict, determine whether the problem is in the work, the check, the diagnostic path or an expectation that has no basis in the task. If the cause remains unclear, choose a check that can distinguish the possible causes before making changes.

Decide from the original requirement and the observed cause. Do not choose a change just because it makes a check pass. Remove extra conditions you introduced without support in the task, but keep unresolved user requirements visible. Apply only corrections supported by evidence and allowed by the task.

After a correction, update and check the affected final output and any results that depend on it. Reuse evidence that still applies. Repeat broader checks only when a change, contradiction or newly found gap gives a reason to do so.

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
