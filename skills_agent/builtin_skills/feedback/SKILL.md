---
name: feedback
description: Acquire file, text, execution, and image evidence to assess actual work results and choose the next action.
---

Use this skill when a decision about work depends on observing its actual result. Identify the condition or uncertainty to resolve, then select evidence that can answer it: metadata for file existence and identity, text for actual content, execution for behavior, or images for visible properties. Feedback need not follow a fixed sequence or use every source. Obtain the relevant result, compare it with the task's requirements, and let that observation determine whether to correct, extend, finish, or investigate further.

Resolve [scripts/feedback.py](scripts/feedback.py) against this SKILL.md's directory and execute it directly through the existing `shell` tool with Python 3.11 or newer. Set its absolute path in each shell call; variables do not persist between calls. Use `--help` when needed. Read or adapt the source for customization, rather than rebuilding routine acquisition operations. Existing task-specific checks and readers can still be used when they fit better.

## Acquisition operations

| Command | Acquired result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256 for regular files; bounded child metadata for directories. This does not read a file's meaning. |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Numbered UTF-8 text with exact returned characters, including line endings. `--column` applies to the first requested line. Resume at returned `next_line` and `next_column` if truncated. |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual command status, bounded separate stdout/stderr tails, and complete binary logs. Arguments execute directly; use `sh -lc '...'` explicitly when shell syntax is needed. |
| `image PATH... [--label TEXT]` | Existing local PNG/JPEG/GIF/WebP images, with format detected from their headers. Their pixels are sent to the model as native image feedback by the agent runtime. |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | A fresh screenshot of local HTML/SVG through an installed Chrome/Chromium, followed by native image feedback. No dependency installation is attempted. |

Success and acquisition failures return one JSON feedback envelope on stdout; errors also go to stderr. `run` and `render` preserve failing command status; timeout returns 124. Inspect the outer shell status and the returned observations, including `timed_out`, actual `exit_code`, log locations and truncation. For long logs, use `text` to read relevant ranges. The image header check detects format, not complete pixel validity; runtime acquisition can still report an unreadable image.

Return the helper's single JSON object as the entire stdout of that shell call; do not mix it with other commands' printed output or pipe it through a formatter. The agent runtime acquires declared images before text filtering, validates and snapshots them, then sends their bytes in the next model request. Use `shell result_mode=direct` for exact text evidence; `auto` is suitable for bounded summaries, and image acquisition also works with `report`. Merely printing an image path, obtaining its hash, or saving a screenshot does not constitute visual inspection. Inspect the native image and any acquisition diagnostic before describing what you saw.

`render` uses an isolated temporary browser profile and a fresh capture file, so a failed capture cannot reuse an earlier screenshot. Its wait value is a virtual-time budget, not a guarantee of an exact animation frame. A screenshot establishes the visible state captured at its viewport; temporal or interactive properties need observations of those properties. Choose suitable state captures or executable interaction checks when required, and keep claims within what was observed.

For PDFs, office files or unsupported binaries, metadata alone is insufficient: use a suitable reader to extract relevant text or render pages, then acquire that text or those images. If a required reader, renderer or model image capability is unavailable, distinguish that acquisition gap from a failed work result. Choose a relevant alternative or resolve the missing capability within the task's scope; report what remains unobserved.

For explicit acceptance conditions on saved files, use [scripts/check.py](scripts/check.py): `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]` (flags can repeat), or `json PATH [--pointer /path] --equals-json VALUE`. It checks the actual saved data, returns each condition's result and bounded evidence, and exits 0 for passing supplied conditions, 1 for failed conditions or 2 for input/acquisition errors. A missing JSON value differs from null, and booleans differ from numbers. Choose expected conditions from the task or established contract before evaluating the output. Do not change them merely to make a failing result pass. For behavior, run the relevant executable check through `run`; text presence or a matching value alone cannot establish the behavior of a program. Read [references/acceptance-checks.md](references/acceptance-checks.md) when deciding how to turn requirements into observable checks and respond to a failure.

Read [references/feedback-loop.md](references/feedback-loop.md) when deciding how to connect an observation with the next action. Its examples show alternative feedback routes and decisions based on returned results, without prescribing an artifact, stage count, or checkpoint file.
