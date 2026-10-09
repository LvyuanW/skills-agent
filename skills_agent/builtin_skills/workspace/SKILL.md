---
name: workspace
description: Read and search text attachments or workspace files, and retrieve public session history.
---

Read the source of [scripts/workspace.py](scripts/workspace.py) through `shell` to learn its file-reading and search methods. Resolve its path relative to this SKILL.md. Adapt the relevant code or write your own script for the current task, then execute it through `shell` and revise it based on the result. Use inline code or a file in the workspace; no tool registration or fixed script interface is needed.

For UTF-8 text, stream lines and select the relevant ranges instead of loading an entire large file. Preserve filenames and one-based line numbers when reporting matches. The reference `read` function selects inclusive line ranges; `search` demonstrates literal or regular-expression matching and optional filename filtering. Choose ranges, filters, and output format to fit the task.

For PDF, office documents, archives, or other binary attachments, use a suitable installed reader or write a task-specific parser. Extract relevant content before reasoning about it; do not interpret binary data as text.

Retrieve earlier public interactions using only `history` with the main session's `events.jsonl` path supplied in the system context:

```sh
python3 "<skill-directory>/scripts/workspace.py" history "<events.jsonl>" --start 1 --end 200
```

Ranges use original event line numbers and default to 200 lines. This history helper preserves public messages and subagent goal summaries while omitting hidden subagent calls, results, and raw provider payloads. Request further ranges as needed; process or search its output with your own code. Do not use `read`, `search`, or other raw-file commands on session logs or child sessions. Keep file searches outside session directories.
