---
name: workspace
description: >-
  Use for selective, located reads of large or structured files and attachments
  (JSON, CSV, DOCX, XLSX, PDF), or to check required local executables and modules.
  Skip simple targeted text reads and searches; use feedback for visual or runtime
  verification, and delegated history retrieval for conversation records.
---

Select the part of an input needed for the current decision. Preserve its path and meaningful location; follow a continuation cursor when omitted content matters. Simple text reads and searches can use ordinary shell commands. Learn from, reuse or adapt the reference scripts below, or write a parser suited to the task.

Locate the relevant workspace, repository or dependency directory before recursively searching file contents. Start from those explicit directories rather than `/`. Prune unrelated directories before reading their files; downstream `grep -v` filters and `head` only filter output and do not bound traversal time. Preserve useful error diagnostics. Foreground shell calls have a default 120-second execution limit, adjustable with `timeout_seconds` up to 600 seconds; use managed background processes for longer work. On timeout, inspect the partial results and narrow or revise the search instead of automatically repeating it.

Resolve scripts relative to this SKILL.md and run them through `shell` with an available Python interpreter. Each shell call has its own variables.

| Reference | Capability |
| --- | --- |
| [scripts/workspace.py](scripts/workspace.py) | `read PATH --start 1 --end 200` selects inclusive UTF-8 lines; `search PATTERN PATH... [--regex] [--glob '*.py']` returns filenames and one-based lines. |
| [scripts/extract.py](scripts/extract.py) | Located, bounded JSON Pointer, CSV, DOCX, XLSX and PDF extraction. Read [references/extraction.md](references/extraction.md) for the selected format's options, limits and cursor rules. |
| [scripts/probe_environment.py](scripts/probe_environment.py) | `--executable NAME` / `--module NAME` checks required capabilities without running them; flags may repeat. Module names are top-level names, without dots. Exit 1 means a requested capability is missing. |

Keep the source and selection options unchanged while following cursors; re-read changed files. Extraction describes stored content, not document layout, OCR, formula evaluation or application behavior. PDF extraction uses an installed reader and does not install dependencies. Use a suitable reader for other binary formats. For very long text lines, the feedback skill's `text` operation provides line/column continuation.

Choose `result_mode: direct` for exact source, structured values and cursors. Inspect returned content and failure status before using it as evidence; finding an executable or module does not establish that it runs correctly.

The main agent retrieves original conversation evidence through a named `subagent` with `history: true` and a concrete question. Ordinary subagents report missing historical input to the parent; dedicated history retrieval subagents follow their assigned instructions. Keep workspace searches outside session directories.

Earlier file contents are available separately when the runtime provides automatic workspace history. Use the [file-editing skill's history instructions](../file-editing/SKILL.md#use-automatic-file-history) to inspect or compare those versions. Conversation retrieval answers what was said or executed; file history supplies the saved file contents at a checkpoint.
