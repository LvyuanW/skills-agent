---
name: file-editing
description: >-
  Make precise changes to existing text or code files while preserving unrelated
  content, line endings and file mode. Use current contents and unique anchors,
  then inspect the saved change. Skip simple standalone file creation, read-only
  inspection and edits to binary contents.
---

## When to use this skill

Use this skill for targeted changes to existing text or code, especially when the surrounding content must stay intact. Simple standalone file creation and read-only inspection need no editing helper. Use a format-specific method to edit binary contents.

## Edit the current file

Read the current saved contents and identify the exact region to change. Preserve unrelated content. If a match is missing or ambiguous, inspect the file again and refine the match; do not reconstruct the file from memory.

Group related edits when their inputs are already known. When one edit depends on another's result, inspect that result before choosing the next edit.

[scripts/edit.py](scripts/edit.py) provides the operations below. You can use it directly, read and adapt its source, or write task-specific editing code that preserves the same relevant properties. The helper requires Python 3.11 or newer. Resolve its absolute path from this SKILL.md in each independent shell call.

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

Old strings and anchors must be nonempty literal text that occurs exactly once, including overlapping matches. The helper edits regular UTF-8 files without NUL bytes; it does not edit symlinks. The `delete-file` operation also accepts binary files. Parent directories must already exist.

Writes use a temporary file and preserve untouched contents, line endings and file mode. Separate operations are not a single transaction. A heredoc adds a final newline; use inline text or a payload file when that difference matters.

## Check the saved result

Inspect the exit status and the returned saved content. If the excerpt is truncated, use its `location` line and column for a targeted follow-up read. Choose `result_mode: direct` when you need exact file text.

Read [references/incremental-editing.md](references/incremental-editing.md) when you need examples of continuing from saved edits or resolving ambiguous matches. The examples illustrate these operations; they do not prescribe a sequence for every task.
