---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification.
---

Choose the observation needed for the next decision: actual execution for behavior, saved text or values for content, and pixels for appearance. A successful write, file path or screenshot command alone does not establish the contents or visual result.

Automatic workspace checkpoints preserve saved versions; they are not verification results. When you need to establish what changed since a check, compare the relevant file versions using the [file-editing skill's history instructions](../file-editing/SKILL.md#use-automatic-file-history). Apply earlier evidence only to contents and execution conditions for which it still holds.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
