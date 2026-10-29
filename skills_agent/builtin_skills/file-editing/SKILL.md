---
name: file-editing
description: >-
  Use for precise changes to existing text or code files while preserving
  surrounding contents, line endings and file mode, or to compare and retrieve
  earlier file versions from automatic workspace history. Skip simple standalone
  file creation, ordinary current-file reads and binary-file editing.
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

## Use automatic file history

When the runtime provides a file-history repository in the system context, it watches the workspace and saves checkpoints automatically. You do not need to make Git commits to preserve those versions. This history is separate from the project's own Git repository and from conversation history. A checkpoint records saved content, not whether the content is correct.

Use [scripts/history.py](scripts/history.py) through the existing shell to inspect it. Resolve this script's absolute path from this SKILL.md and use the repository path supplied by the runtime:

```sh
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git status
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git log --path analysis.py --limit 10
```

Check `status` for recording errors and the excluded paths. Read [references/file-history.md](references/file-history.md) to compare checkpoints or retrieve old contents. Keep the useful parts of the current work: inspect the earlier version before choosing a local correction. File events are batched, so a checkpoint may not contain every short-lived intermediate version; files outside the recorded workspace are not covered.
