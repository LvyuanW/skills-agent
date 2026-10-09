---
name: workspace
description: Read text attachments and other files by line range, search their contents, and retrieve public session history.
---

Use `shell` to execute `scripts/workspace.py` beside this SKILL.md. Resolve the script's absolute path from the catalog's SKILL.md path. Paths supplied to the script may be absolute or relative to the workspace.

Read a file with one-based, inclusive line numbers:

```sh
python3 "<skill-directory>/scripts/workspace.py" read "<file>" --start 1 --end 200
```

The default range is 200 lines. Request further ranges when needed. Search literal text across files or directories; add `--regex` for regular expressions or `--glob '*.py'` to restrict filenames:

```sh
python3 "<skill-directory>/scripts/workspace.py" search "<pattern>" "<path>"
```

Retrieve earlier public interactions using only `history` with the main session's `events.jsonl` path supplied in the system context:

```sh
python3 "<skill-directory>/scripts/workspace.py" history "<events.jsonl>" --start 1 --end 200
```

Ranges use original event line numbers and default to 200 lines. The command preserves public messages and subagent goal summaries while omitting hidden subagent calls, results, and raw provider payloads. Request further ranges as needed; to search public history, pipe `history` output to a text search command. Do not use `read`, `search`, or other raw-file commands on session logs or child sessions. Keep file searches outside session directories.

Use these commands for UTF-8 text. For PDF, office documents, archives, or other binary attachments, use a suitable installed reader or a task-specific script. Extract the relevant content before reasoning about it; do not interpret binary data as text.
