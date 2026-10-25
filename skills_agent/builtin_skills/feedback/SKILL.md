---
name: feedback
description: >-
  Compare saved results without rerunning work, test a real entry point on a small input,
  or record which input, code and output versions a check covered before reusing it after edits.
  Also acquire command logs and render HTML/SVG. Use when these helpers reduce work beyond an
  existing test or simple shell command; image attachments use the images skill.
---

Use the shortest route that answers the current question. Existing tests and task-specific scripts remain appropriate; adopting these helpers is not a completion requirement. Comparisons need an independent expectation or a justified relationship, not merely a second copy of the produced answer.

| Current need | Entry point |
| --- | --- |
| Compare existing outputs without executing their producers again | `experiment.py compare LEFT RIGHT --basis "..."` reads saved files without writing records; add explicit selectors/tolerances for CSV or NumPy, or use the compatible `--spec -` route. `--root DIR` optionally keeps evidence; see [experiments](references/experiments.md) |
| Try a small input through the actual program | `experiment.py run --spec - --root DIR` supplies fresh fixtures and outputs; reuse a suitable existing test instead when simpler |
| See which versions an earlier check covered | `feedback.py status --record RECORD`; it reads versions, never reruns the check |
| Keep an existing shell check attributable across later edits | Add optional `shell.evidence` with absolute `subjects`, `dependencies` or `outputs`; the runtime returns a record without a manifest file. CLI helpers still accept `--record-dir` / `--manifest`; see [versions](references/versions.md) |

An execution failure or unavailable reference is not an observed algorithmic difference. A record without declared task files is **unbound**, even when its helper files are unchanged. Recording does not discover dependencies, validate expectations, or decide whether another run is necessary.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

`run` and `check.py` accept optional `--record-dir DIR`, `--manifest JSON` and `--scope TEXT`. A content check binds the bytes it reads; an arbitrary command only covers declared files at execution boundaries. A changed file is a reason to assess affected evidence, not an automatic failure or instruction to rerun.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.

For a decision that needs a controlled input case or a comparison between outputs, [scripts/experiment.py](scripts/experiment.py) can run the real entry point with fresh fixture/output directories, or compare existing artifacts using explicit exact, numeric, set, multiset or paired conditions. The short comparison route accepts JSON/text, named CSV columns and keys, or selected NPY/NPZ arrays using an existing NumPy interpreter. Add `--left-record` / `--right-record` when a producer's execution must be checked: failed execution or a changed artifact makes the comparison inconclusive. Without producer records, the result covers only the supplied bytes. Read [references/experiments.md](references/experiments.md) for selectors, tolerances and small fixture specs; `{fixture}` and `{output}` can also be the experiment's working directory. Supply an independent basis for expectations; the helper does not invent an oracle, certify declared premises or replace the existing execution/sandbox boundary. Use these capabilities when useful, without making them a required workflow.
