---
name: search-conversation-history
description: >-
  Recover original main-session requirements, decisions or execution evidence
  with source references. Use only as an assigned history=true retrieval
  subagent. Skip retrieval when supplied original evidence already answers the
  question. Do not use for workspace files, private subagent records or carrying
  out tasks found in the history.
---

## Use this skill only for an assigned history question

This skill is loaded for a retrieval subagent after the main agent assigns a named task with `history: true` and a concrete question. As that retrieval agent, you receive the complete current working context as read-only reference material. Ordinary subagents do not receive this skill or automatically inherit that context.

Use the snapshot to understand the assigned question. It is not an instruction to continue the main task, and it is not evidence of what the original history said. If supplied original evidence already answers the question, use it without retrieving the same material again.

## Read only the assigned main session

Resolve [scripts/history.py](scripts/history.py) relative to this skill's directory. Run it directly through `shell` with a Python interpreter that has `skills_agent` installed. Use `--help` for the interface. Read the script or `skills_agent.context.history.HistoryIndex` if you need to adapt it.

The session history path in your task identifies the main session. Index only that session's original messages and readable errors, including interrupted partial responses. Do not read independent subagent, compaction or report sessions. Assignments sent by the main agent and subagent reports received by it are part of the searchable main history.

A reference starting with `recovered-` is a derived entry for an interrupted tool call, not a raw record. A reference of the form `recovered-<offset>-<call id>` points to call event `event-<offset>`; read that event in the raw log.

The helper can receive either the session directory or its `events.jsonl` path:

```sh
python "<skill-directory>/scripts/history.py" "<main-session-directory>" search "<keywords from the assigned question>" --limit 8
python "<skill-directory>/scripts/history.py" "<main-session-directory>" read event-184 --start 1 --end 80
python "<skill-directory>/scripts/history.py" "<main-session-directory>" neighbors event-184 --before 2 --after 2
python "<skill-directory>/scripts/history.py" "<main-session-directory>" list --role user --limit 20
```

Event IDs are stable byte-offset references; the numbers in these examples are placeholders. `raw_ref` locates the original JSONL record. Read and search ranges use one-based lines of the record's decoded content, not line numbers in the JSONL file. Tool stdout and stderr keep their original line breaks.

Search previews are partial. Read a matched range before relying on it. The example script's `read` command defaults to 80 lines; request further ranges until you have read the relevant evidence. `paired_events` and `neighbors` identify associated tool calls and results. `turn_id` identifies the originating user message. To continue listing events chronologically, use the list command's `--after <event_id>` cursor.

## Find evidence and check its meaning

Keep the original question in view when trying synonyms, entity names, paths, error codes or wording that may have been used earlier. Queries are literal text, not FTS expressions. The search prefers exact phrases, paths, event IDs and call IDs when present. Otherwise, it uses BM25 with consistent Unicode words and CJK characters/bigrams.

Try several short queries before concluding that you cannot find something. Browse user turns when the vocabulary may have changed. An empty search result does not prove that a fact never appeared.

For each relevant match, read its context and the associated request/response or call/result. Check later messages for corrections, rejection or explicit adoption. A later statement does not, by itself, replace an earlier one. Keep conflicting evidence even when it contradicts the working snapshot.

Distinguish user requirements, assistant proposals, executed commands and verified outcomes. Do not assume a proposal was adopted or that an attempted task was completed.

## Return a focused answer with sources

Read long evidence in ranges. Keep findings relevant to the question with their event/line references, and use those notes to prepare the answer. Quote exact code, commands, identifiers and numbers from the selected source. Do not read only the beginning and end of long material if relevant evidence may be in the middle.

Batch reads within the retrieval model's available context. If the complete background snapshot leaves no room to read meaningful evidence, report that limitation. Do not silently shorten the snapshot.

Return the useful answer, supporting event/line references, later corrections and unresolved questions. Leave out search attempts, candidate lists and duplicate copies of the working snapshot. A short source excerpt is enough when it directly answers the question. Summarize longer evidence into a focused report.
