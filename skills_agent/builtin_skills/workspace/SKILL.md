---
name: workspace
description: Read and search text attachments or workspace files with scripts.
---

Read the source of [scripts/workspace.py](scripts/workspace.py) through `shell` to learn its file-reading and search methods. Resolve its path relative to this SKILL.md. Adapt the relevant code or write your own script for the current task, then execute it through `shell` and revise it based on the result. Use inline code or a file in the workspace; no tool registration or fixed script interface is needed.

For UTF-8 text, stream lines and select the relevant ranges instead of loading an entire large file. Preserve filenames and one-based line numbers when reporting matches. The reference `read` function selects inclusive line ranges; `search` demonstrates literal or regular-expression matching and optional filename filtering. Choose ranges, filters, and output format to fit the task.

For PDF, office documents, archives, or other binary attachments, use a suitable installed reader or write a task-specific parser. Extract relevant content before reasoning about it; do not interpret binary data as text.

The main agent obtains original conversation evidence by delegating a named `subagent` with `history: true` and a concrete question, rather than searching session logs itself. Ordinary subagents report missing historical input to the parent agent. A dedicated history retrieval subagent follows its assigned retrieval instructions. Keep general workspace file searches outside session directories.

Your script's output should answer its stated purpose: include exact source paths and line ranges, relevant observations, and execution status. Return code or precise data unchanged when that is what the task needs. Choose `result_mode: direct` for source ranges that must retain exact text; use `auto` for ordinary output or `report` for long evidence that needs a focused conclusion.
