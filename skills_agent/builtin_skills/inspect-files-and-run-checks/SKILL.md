---
name: inspect-files-and-run-checks
description: >-
  Read file metadata or bounded text, run commands with saved logs, render local
  HTML or SVG, and compare saved files, text or JSON values with explicit
  expectations. Use when these command-line helpers are needed; a normal tool
  result or direct read may already provide the requested information.
---

# Inspect files and run checks

The helpers collect observations and test conditions supplied by the caller. They do not choose the conditions or establish overall task correctness. They require Python 3.11 or newer and do not install dependencies. Resolve each script's absolute path from this SKILL.md. Shell variables do not persist between calls.

## Read files or collect command output

[scripts/feedback.py](scripts/feedback.py) provides these subcommands:

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256 for regular files; bounded directory metadata or a symlink target. |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Numbered UTF-8 text and continuation coordinates when truncated. |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and paths to separate complete binary logs. |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | A PNG screenshot of local HTML/SVG through installed Chrome/Chromium and capture metadata. |

For example:

```sh
python3 /absolute/skill/scripts/feedback.py text /absolute/workspace/result.txt --start 1 --end 80
```

`text` uses one-based line and column numbers. Resume a truncated result with its `next_line` as `--start` and `next_column` as `--column`. Without `--end`, it reads at most 200 lines starting at `--start`. It requires UTF-8 text and rejects NUL bytes; binary or other document formats need an appropriate reader. Use `result_mode: direct` for exact text.

`run` executes the supplied argument list with no interactive stdin and creates a fresh `run-*` directory under `--log-dir` for `stdout.log` and `stderr.log`. Use `sh -lc '...'` explicitly when the command needs shell syntax. The returned `observations` include `exit_code`, `timed_out`, `stdout_log`, `stderr_log`, their bounded tails and any startup error. A tail may omit earlier output; the complete logs remain at the returned paths.

`run` and `render` preserve command failure status and return 124 on timeout. The timeout stops the command; it is not a progress-reporting interval. File and text collection errors return a nonzero status with a diagnostic.

## Compare saved values

[scripts/check.py](scripts/check.py) accepts explicit conditions:

- `file PATH... [--min-bytes N]`: regular-file existence and optional minimum size.
- `text PATH --contains TEXT [--absent TEXT]`: exact literal presence or absence in UTF-8 text. Both flags can be repeated.
- `json PATH [--pointer /path] --equals-json VALUE`: equality with a supplied JSON value, optionally at an RFC 6901 JSON Pointer.

It distinguishes a missing value from `null` and booleans from numbers. Exit codes are 0 when the supplied conditions pass, 1 for a mismatch and 2 for a collection or input failure. The JSON result includes each checked condition and its observed value or missing path. A successful file-size check does not validate the file's format or contents.

Read [references/acceptance-checks.md](references/acceptance-checks.md) for command examples, JSON Pointer escaping, value quoting and result fields.

## Render and attach images

`render` accepts an existing local `.html`, `.htm` or `.svg` file, not a URL. `--output` must be a distinct `.png` path. It uses an installed Chrome/Chromium executable, optionally selected with `--browser`, in a fresh isolated profile. It does not install a browser. The screenshot covers the selected viewport and state; `--wait-ms` is a virtual-time budget, not an exact animation timestamp. PDF and office files need another reader or renderer.

Use the [attach-images skill](../attach-images/SKILL.md) when attaching an image. A model request permits at most 10 images in total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while preserving originals; `compose` makes a numbered grid with source mappings. For new images, `save_bounded` can enforce the size limit before writing. These helpers reject oversized image attachments.

Save screenshots and prepared image copies in the system context's **Image output directory** unless the user specified a delivery location. Shell and sandbox expose it as `SKILLS_AGENT_IMAGE_DIR`. The `images.path` parameter requires the actual absolute path and does not expand environment variables. Keep source files and task scripts in their existing locations.

Declare the selected image paths in the shell call, for example:

```json
{
  "name": "Render local preview",
  "command": "python3 /absolute/skill/scripts/feedback.py render /absolute/workspace/preview.html --output /absolute/image-output/preview.png --width 500 --height 400 --wait-ms 0",
  "run_in_background": false,
  "images": [{"path": "/absolute/image-output/preview.png", "label": "Preview"}]
}
```

Replace `/absolute/image-output` with the supplied directory. Local `file://` snapshot URLs are also accepted as image paths; optional `source_url` retains the original source reference. The runtime reads and snapshots declared images after execution and supplies their pixels before filtering text. Capture metadata, a filename or an image header is not the pixel content.

The explicit `images` value controls attachment independently of logs or JSON metadata. Use `images: []` when only preparing files. A background call registers paths at launch and attaches images with its completion result. Do not call an extra attachment helper. Native images work with `result_mode: direct`, `auto` or `report`; acquisition errors are returned when capture or attachment fails.
