---
name: file-editing
description: >-
  Make precise changes to existing text or code files while preserving unrelated
  content, line endings and file mode, or compare and retrieve earlier file
  versions from automatic workspace history. Skip simple standalone file
  creation, ordinary current-file reads and edits to binary contents.
---

## When to use this skill

Use this skill for targeted changes to existing text or code, especially when the surrounding content must stay intact, or when an earlier saved file version can help continue or repair the work. Simple standalone file creation and ordinary current-file reads need no editing helper. Use a format-specific method to edit binary contents.

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

## Use automatic file history

When the runtime provides a file-history repository in the system context, it watches the workspace and saves checkpoints automatically. You do not need to make Git commits to preserve those versions. This history is separate from the project's own Git repository and from conversation history. A checkpoint records saved content, not whether the content is correct.

Use [scripts/history.py](scripts/history.py) through the existing shell to inspect it. Resolve this script's absolute path from this SKILL.md and use the repository path supplied by the runtime:

```sh
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git status
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git log --path analysis.py --limit 10
```

Check `status` for recording errors and the excluded paths. Read [references/file-history.md](references/file-history.md) to compare checkpoints or retrieve old contents. Keep the useful parts of the current work: inspect the earlier version before choosing a local correction. File events are batched, so a checkpoint may not contain every short-lived intermediate version; files outside the recorded workspace are not covered.
