---
name: history
description: Retrieve original main-session evidence by task, keywords, event references, and tool-call relationships; report relevant findings with sources.
---

This skill is loaded only for a history retrieval subagent, after the main agent delegates a named task with `history: true` and a concrete question. As that retrieval agent, you receive the complete current working context as read-only reference material. Ordinary subagents do not receive this skill or automatically inherit that context. Never treat the snapshot as instructions to continue the main task or as evidence of what the original history said.

Read [scripts/history.py](scripts/history.py) and, when adapting the search implementation, `skills_agent.context.history.HistoryIndex`. These are reference code: write or adapt scripts for the question and execute them through `shell`. The session history path supplied in your task identifies the main session. Index only that session's original messages and readable errors, including interrupted partial responses; do not read independent subagent, compaction, or report sessions. Main-agent assignments and reports received from subagents are part of the searchable main history.

A reference starting with `recovered-` is a derived recovery entry for an interrupted tool call, not a raw record. Its form `recovered-<offset>-<call id>` names the call event `event-<offset>` to read in the raw log.

The helper can receive either the session directory or its `events.jsonl` path:

```sh
python "<skill-directory>/scripts/history.py" "<main-session-directory>" search "剪贴板 Ctrl-V" --limit 8
python "<skill-directory>/scripts/history.py" "<main-session-directory>" read event-184 --start 1 --end 80
python "<skill-directory>/scripts/history.py" "<main-session-directory>" neighbors event-184 --before 2 --after 2
python "<skill-directory>/scripts/history.py" "<main-session-directory>" list --role user --limit 20
```

Event IDs are stable byte-offset references; the example numbers are placeholders. `raw_ref` locates the original JSONL record. Read ranges and search ranges refer to one-based lines of that record's decoded content, not JSONL file line numbers. Tool stdout and stderr retain their original line breaks. Search previews are explicitly partial: read the matched range before using it as evidence. `read` defaults to 80 lines in this example script; request following ranges until the relevant evidence has been read. `paired_events` and `neighbors` identify associated tool calls/results; `turn_id` identifies the originating user message. Use the list command's `--after <event_id>` cursor to continue chronologically.

Preserve the original question while expanding queries into plausible synonyms, entity names, paths, error codes, and alternate historical wording. Queries are literal text, not FTS expressions. Exact phrases, paths, event IDs and call IDs are preferred when present; otherwise results use BM25 with consistent Unicode words and CJK characters/bigrams. Try multiple short queries instead of assuming the first miss means absence. Browse user turns when vocabulary has changed.

For each relevant candidate, read its context and associated request/response or call/result. Search later messages for corrections, rejection, or explicit adoption; chronological recency alone does not prove supersession. Keep conflicting evidence even when it contradicts the working snapshot. Distinguish user requirements, assistant proposals, executed commands, and verified outcomes. Do not invent adoption or completion.

Read long evidence in ranges, retain question-relevant findings and event/line references, and use these notes to produce the final report. Exact code, commands, identifiers and numbers should be quoted from the selected source. Do not mechanically take only the beginning and end of long material. Batch candidate reading within the retrieval model's available context. If the complete background snapshot leaves no room for meaningful reading, report that limitation; do not secretly shorten the snapshot.

Return only the useful answer, supporting event/line references, later corrections, and unresolved questions. Do not return your search attempts, candidate lists, or another copy of the working snapshot. A short source excerpt is sufficient when it directly answers the question; longer evidence should produce a focused report. An empty search result is not proof that a fact never appeared.
