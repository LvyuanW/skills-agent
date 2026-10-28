---
name: workspace
description: >-
  Read selected parts of large or structured files and attachments (JSON, CSV,
  DOCX, XLSX, PDF), with source locations, or check whether required local tools
  and Python modules are available. Skip simple text reads and searches. Use
  check-results for visual or runtime checks and delegated history retrieval for
  conversation records.
---

## When to use this skill

Use this skill when you need part of a large or structured input, need a source location for what you read, or need to check whether a local dependency is available. Ordinary shell commands are enough for a simple text read or targeted search.

Read the portion needed for the current decision. Keep its source path and location, and follow a continuation cursor when omitted content matters. You can use or adapt the scripts below, or write a parser suited to the task.

## Find and read the relevant input

Locate the workspace, repository or dependency directory before searching file contents recursively. Start from those directories, not `/`. Exclude unrelated directories before traversal. A later `grep -v` or `head` filters output but does not limit how many files the search visits.

Keep useful error messages. Foreground shell calls default to a 120-second execution limit; `timeout_seconds` can raise it to 600 seconds. Use managed background processes for longer work. After a timeout, inspect the partial results and narrow or revise the search before retrying.

Resolve scripts relative to this SKILL.md and run them through `shell` with an available Python interpreter. Each shell call has its own variables.

| Reference | Capability |
| --- | --- |
| [scripts/workspace.py](scripts/workspace.py) | `read PATH --start 1 --end 200` selects inclusive UTF-8 lines; `search PATTERN PATH... [--regex] [--glob '*.py']` returns filenames and one-based lines. |
| [scripts/extract.py](scripts/extract.py) | Located, bounded JSON Pointer, CSV, DOCX, XLSX and PDF extraction. Read [references/extraction.md](references/extraction.md) for the selected format's options, limits and cursor rules. |
| [scripts/probe_environment.py](scripts/probe_environment.py) | `--executable NAME` / `--module NAME` checks required capabilities without running them; flags may repeat. Module names are top-level names, without dots. Exit 1 means a requested capability is missing. |

Keep the source and selection options unchanged while following cursors. If the file changes, read it again from the appropriate starting point.

These extractors read stored content. They do not check document layout, perform OCR, calculate formulas or test application behavior. Use check-results when those visual or runtime checks are needed. PDF extraction requires an installed reader and does not install one. Use a suitable reader for other binary formats. For very long text lines, the check-results skill's `text` operation supports line/column continuation.

Choose `result_mode: direct` when you need exact source text, structured values or cursors. Inspect the returned content and failure status before relying on it. Finding an executable or module confirms its availability, not that it works correctly.

## Keep conversation retrieval separate

Keep workspace searches outside session directories. The main agent retrieves original conversation evidence by assigning a named `subagent` with `history: true` and a concrete question. Ordinary subagents report missing historical input to their parent. Dedicated history retrieval subagents follow their assigned retrieval instructions.
