---
name: file-editing
description: Create, modify, extend, and remove files through reusable shell operations, with exact matching and feedback from the saved contents.
---

Use this skill when a task requires creating or changing files. It supplies file operations through the existing `shell` tool. Select operations from the requested change and the file's current contents; the skill does not prescribe an implementation plan, artifact format, or number of editing steps. Related changes may share a call when their inputs are already known. When a later change depends on an earlier result, examine that result before choosing the next edit.

Resolve [scripts/edit.py](scripts/edit.py) against this SKILL.md's directory. Set `edit` to that resolved path in each shell call that needs it; shell variables do not persist between calls. Execute the helper directly with Python 3.11 or newer. Use `python3 "$edit" --help` or a command's `--help` when needed. Read the source to investigate an unexpected result or customize an operation; routine use does not require rebuilding the helper.

## Operations

| Command | Purpose | Content arguments |
| --- | --- | --- |
| `create PATH` | Create a new UTF-8 file; refuse to overwrite | `--text TEXT`, `--text-file FILE`, or stdin |
| `replace PATH` | Replace one exact, nonempty old string | `--old TEXT` or `--old-file FILE`; replacement from `--new TEXT`, `--new-file FILE`, or stdin |
| `insert PATH` | Insert text beside one exact, nonempty anchor | `--anchor TEXT` or `--anchor-file FILE`, `--position before\|after`; text from `--text TEXT`, `--text-file FILE`, or stdin |
| `delete-content PATH` | Remove one exact, nonempty string | `--old TEXT` or `--old-file FILE` |
| `delete-file PATH` | Remove a regular file | No content argument |

Old strings and anchors must occur exactly once, including overlapping occurrences. A missing or ambiguous match fails without changing the file. Inspect the current text and refine the match instead of replacing the whole file from a remembered copy. Anchors are literal text, not regular expressions. Include enough neighboring content to identify the intended location.

Existing targets must be regular files, not symlinks; text operations require UTF-8 without NUL bytes. `delete-file` also accepts binary regular files and never recurses into directories. Parent directories must already exist. Text operations preserve existing content, line endings, and file mode outside the requested change; supplied text keeps its own exact characters. A heredoc adds a final newline, so use an inline argument or a payload file when that distinction matters.

Writes are prepared in a temporary file in the same directory. Creation publishes the complete file without overwriting an existing target; an edit atomically replaces its target. Several operations in a shell call are separate writes, not one transaction. If a later operation fails, inspect the earlier saved changes before continuing.

Success prints a JSON result with the operation, path, whether it changed the file, and a bounded numbered excerpt when relevant. Errors go to stderr with a nonzero exit status. Inspect the exit status and feedback before proceeding. The excerpt may be truncated, including within a long line; use the returned `location` line and column to target a read when the actual change is outside the excerpt. Choose `shell result_mode=auto` for routine operation summaries and `direct` for skill instructions or exact file text.

## Practical calls

Each example uses `/absolute/path/from/skill-catalog/file-editing/scripts/edit.py` as a placeholder. Replace it with the helper path resolved from the loaded SKILL.md. Each shell call sets its own `edit` variable; there is no setup variable inherited from an earlier call. The following commands illustrate independent operations on ordinary text. They do not define a workflow for a particular kind of task.

Create a file through a quoted heredoc, which keeps `$`, backticks, and backslashes literal:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create notes.txt <<'TEXT'
Heading
Original paragraph.
TEXT
```

Replace one known string. For multiline text, stdin or a payload file avoids complex shell escaping:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace notes.txt --old 'Original paragraph.' --new 'Revised paragraph.'
```

Insert a complete line before a unique anchor. The supplied newline becomes part of the inserted content:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" insert notes.txt --anchor 'Revised paragraph.' --position before <<'TEXT'
Additional paragraph.
TEXT
```

Remove a specified fragment:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" delete-content notes.txt --old 'Additional paragraph.'
```

Remove a file when its removal is part of the requested change:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" delete-file notes.txt
```

If the desired content already exists in files, pass it directly instead of copying it into a shell variable. Here the three payload files and the target `existing.txt` must already exist:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create output.txt --text-file prepared-content.txt &&
python3 "$edit" replace existing.txt --old-file old-fragment.txt --new-file new-fragment.txt
```

For examples of using saved contents across separate calls and refining a failed exact match, read [references/incremental-editing.md](references/incremental-editing.md). Choose editing boundaries according to the requested change, useful saved results, and dependencies between operations. The examples are not a required number or sequence of steps.
