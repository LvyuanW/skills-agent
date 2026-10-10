---
name: workspace
description: Inspect task-relevant environment capabilities and read files or attachments through selective, located extraction.
---

Use suitable shell commands for simple reads and searches. The helpers below can be called directly through `shell`; resolve their absolute paths relative to this SKILL.md, then invoke them without first reading their implementation. Use an available Python interpreter. Shell calls are independent, so do not rely on variables set in an earlier call.

- `python /absolute/skill/scripts/inspect.py --executable NAME --module NAME`: check only capabilities needed for the next action. Repeat either option as needed. Module names must be top-level names without dots, so discovery does not execute a parent package. Reports discovered paths or origins and missing names; exit 1 means a requested capability is missing. Discovery does not prove that a program or module runs correctly.
- `python /absolute/skill/scripts/workspace.py read PATH --start 1 --end 200`: stream an inclusive UTF-8 line range. `search PATTERN PATH... [--regex] [--glob '*.py']` preserves filenames and one-based line numbers.
- `python /absolute/skill/scripts/extract.py FORMAT PATH ...`: bounded JSON Pointer, CSV, DOCX, XLSX, or PDF extraction with source locations and continuation cursors. Read [references/extraction.md](references/extraction.md) for the relevant format's options and limits.

Inspect the returned content before deciding the next action. A truncated result is a partial observation: follow its cursor or narrow the selection when omitted content matters. For very long plain-text lines, the feedback skill's `text` helper supports a character budget and line/column continuation. PDF extraction uses an already installed reader and reports missing dependencies; no helper installs packages. Extracted document text does not establish layout, visual correctness, or OCR content. Unsupported binary formats require an appropriate installed reader rather than decoding arbitrary bytes as text.

The main agent obtains original conversation evidence by delegating a named `subagent` with `history: true` and a concrete question, rather than searching session logs itself. Ordinary subagents report missing historical input to the parent agent. A dedicated history retrieval subagent follows its assigned retrieval instructions. Keep general workspace file searches outside session directories.

Choose `result_mode: direct` when exact source text, structured values, or continuation cursors must be retained; use `auto` for ordinary output or `report` for long evidence that needs a focused conclusion. Preserve the helper's failure status and source locations when reporting results.
