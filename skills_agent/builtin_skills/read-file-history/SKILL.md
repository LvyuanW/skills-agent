---
name: read-file-history
description: >-
  Use to find or compare earlier file versions recorded by edit_file, or inspect
  saved contents before restoring a specific change. Skip ordinary current-file
  reads, new edits, and conversation-history retrieval.
---

The runtime records the files changed through `edit_file` in a separate Git history. It preserves the actual contents before an edit and the resulting state after a successful edit. It does not watch the workspace: program outputs and human edits are not automatically recorded. A checkpoint establishes saved contents, not correctness or the state of every workspace file.

Use the history repository path supplied in the system context, not the project's own `.git`. Resolve [scripts/history.py](scripts/history.py) from this skill's absolute path. Run these read-only queries through shell with an available Python interpreter:

```sh
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git status
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git log --path analysis.py --limit 10
```

`status` reports the available history and recording errors. `log` returns checkpoint IDs; omit `--path` to inspect all recorded edits. File paths are relative to the recorded workspace. After selecting actual IDs from the results:

```sh
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git diff BEFORE AFTER --path analysis.py
python3 /absolute/skill/scripts/history.py --repository /absolute/history.git show BEFORE analysis.py
```

Replace `BEFORE` and `AFTER` with the selected IDs. `diff` compares saved versions; `show` writes the exact saved contents to stdout. Inspect the query's exit status and compare the saved content with the current file before deciding what to recover. A file may have changed outside the tool since its last checkpoint.

Restore useful text with `edit_file`, preserving unrelated current work. Do not redirect `show` into a workspace file or use Git checkout/reset to bypass the editing tool. Retrieving a prior version does not restore a running process, external state, or the conversation. Use delegated history retrieval for what was said or executed.
