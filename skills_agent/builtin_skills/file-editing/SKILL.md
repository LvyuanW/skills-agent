---
name: file-editing
description: Make precise changes to text or code files using unique anchors, preserving surrounding contents, line endings and file mode; create or delete task files and inspect the saved change.
---

Base an edit on the current saved contents. Identify the exact region to change and preserve unrelated content. If a match is missing or ambiguous, inspect the file and refine the match instead of reconstructing it from memory. Group related edits when their inputs are known; inspect a result before choosing a dependent edit.

[scripts/edit.py](scripts/edit.py) implements these operations. Read its source to learn or adapt the method, call it directly when useful, or write task-specific editing code that preserves the same relevant properties. Simple file creation does not require a helper. The reference helper requires Python 3.11 or newer; resolve its absolute path from this SKILL.md in each independent shell call.

| Operation | Content arguments |
| --- | --- |
| `create PATH` | `--text TEXT`, `--text-file FILE`, or stdin; refuses to overwrite |
| `replace PATH` | `--old TEXT` or `--old-file FILE`; replacement via `--new TEXT`, `--new-file FILE`, or stdin |
| `insert PATH` | `--anchor TEXT` or `--anchor-file FILE`, `--position before\|after`; content via `--text`, `--text-file`, or stdin |
| `delete-content PATH` | `--old TEXT` or `--old-file FILE` |
| `delete-file PATH` | Removes one regular file; never recurses |

For example, after observing a unique current string:

```sh
python3 /absolute/skill/scripts/edit.py replace notes.txt --old 'Original paragraph.' --new 'Revised paragraph.'
```

The helper requires nonempty literal old strings and anchors that occur exactly once, counting overlaps. It edits regular, nonsymlink UTF-8 files without NUL bytes; `delete-file` also accepts binary files. Parent directories must exist. Writes use a temporary file and preserve untouched contents, line endings and mode. Separate operations are not one transaction. A heredoc adds a final newline; use inline text or a payload file when that distinction matters.

Inspect the exit status and saved-content feedback. The returned excerpt may be truncated; its `location` line and column locate a targeted follow-up read. Choose `result_mode: direct` for exact file text. Read [references/incremental-editing.md](references/incremental-editing.md) for examples of continuing from saved edits and resolving ambiguous matches; their sequence is not a required task plan.
