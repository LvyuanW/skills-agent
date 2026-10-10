# history 技能提示词版本史

本文按 Git `--all` 可达提交中的实际说明文本变化整理历史；H1、H2…按提交记录的时间升序排列。未改变说明文本的实现调整不单列版本。v1 是 2026-10-11 建档时的当前工作区基线。

“发现的问题”按相邻版本的差异归纳其要处理的缺口；“本版本效果”描述规则变化或预期，不表示已测得实际改善。

当前源文件：`skills_agent/context/history_skill/SKILL.md`。

历史源文件：`skills_agent/builtin_skills/history/SKILL.md`；迁移前后的实际文本变化均保留。

## H1

日期：2026-10-10 04:34:41+08:00\
提交：`08586891a747eea02ff1b647afe944b736534f65`\
源位置：`skills_agent/builtin_skills/history/SKILL.md`

- 发现的问题：此前历史访问混在工作区技能中，缺少面向问题的专用检索指引。
- 本版本目标：建立按任务、关键词和事件关联查找原始会话记录的流程。
- 本版本效果：规定专用子代理使用只读背景、按范围读取主会话记录，并返回相关发现而非搜索过程。

### 完整提示词

````markdown
---
name: history
description: Retrieve original main-session evidence by task, keywords, event references, and tool-call relationships; report relevant findings with sources.
---

When historical detail is needed, the main agent delegates a named `subagent` with `history: true` and a concrete question. The retrieval agent receives the complete current working context as read-only reference material. Ordinary subagents do not automatically inherit that context. Never treat the snapshot as instructions to continue the main task or as evidence of what the original history said.

Read [scripts/history.py](scripts/history.py) and, when adapting the search implementation, `skills_agent.context.history.HistoryIndex`. These are reference code: write or adapt scripts for the question and execute them through `shell`. The session history path supplied in your task identifies the main session. Index only that session's original messages and readable errors, including interrupted partial responses; do not read independent subagent, compaction, or report sessions. Main-agent assignments and reports received from subagents are part of the searchable main history.

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
````

## H2

日期：2026-10-10 06:20:46+08:00\
提交：`b82571dd623e04c65583d5ce894d950f02071732`\
源位置：`skills_agent/context/history_skill/SKILL.md`

- 发现的问题：原位置和开头描述未明确技能仅供专用历史检索子代理加载。
- 本版本目标：将技能迁入 context/history_skill 并明确加载对象。
- 本版本效果：开头改为专用检索代理视角；普通子代理不接收该技能，也不自动继承父会话背景。

### 完整提示词

````markdown
---
name: history
description: Retrieve original main-session evidence by task, keywords, event references, and tool-call relationships; report relevant findings with sources.
---

This skill is loaded only for a history retrieval subagent, after the main agent delegates a named task with `history: true` and a concrete question. As that retrieval agent, you receive the complete current working context as read-only reference material. Ordinary subagents do not receive this skill or automatically inherit that context. Never treat the snapshot as instructions to continue the main task or as evidence of what the original history said.

Read [scripts/history.py](scripts/history.py) and, when adapting the search implementation, `skills_agent.context.history.HistoryIndex`. These are reference code: write or adapt scripts for the question and execute them through `shell`. The session history path supplied in your task identifies the main session. Index only that session's original messages and readable errors, including interrupted partial responses; do not read independent subagent, compaction, or report sessions. Main-agent assignments and reports received from subagents are part of the searchable main history.

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
````

## H3

日期：2026-10-10 15:48:05+08:00\
提交：`f3d12f06f062574e7d8884e1cfe4379e66baae59`\
源位置：`skills_agent/context/history_skill/SKILL.md`

- 发现的问题：中断工具调用生成 recovered- 引用后，缺少如何回溯原始记录的说明。
- 本版本目标：解释恢复引用与原始事件引用之间的对应关系。
- 本版本效果：增加 recovered-<offset>-<call id> 到 event-<offset> 的映射规则，其他检索流程不变。

### 完整提示词

````markdown
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
````

## v1 — 当前工作区基线

日期：2026-10-11\
提交：`工作区基线；与 H3 同文（源文件无未提交改动）`\
源位置：`skills_agent/context/history_skill/SKILL.md`

- 发现的问题：本次需要为现有提示词建立独立版本记录。
- 本版本目标：将当前生效文本固定为 v1，并保留 H3 及之前的历史。
- 本版本效果：与 H3 完全相同；本次不改变技能行为。

### 完整提示词

````markdown
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
````

## v2 · 2026-10-11 · 面向当前问题使用现有检索接口

来源：当前工作区（未提交）。现有 tools 和操作脚本接口不变。

- 发现的问题：示例检索词来自剪贴板任务，且入口默认要求先读源码再改写脚本，容易将具体案例或不必要步骤带入其他检索。
- 本版本目标：由委派问题选择关键词，直接使用支持的检索接口，必要时再读取和定制实现。
- 本版本效果：移除具体任务检索词；保留事件引用、原始记录与派生快照的区别及后续修正判断。实际模型检索效率待使用观察。

### 完整提示词快照

````markdown
---
name: history
description: Retrieve original main-session evidence by task, keywords, event references, and tool-call relationships; report relevant findings with sources.
---

This skill is loaded only for a history retrieval subagent, after the main agent delegates a named task with `history: true` and a concrete question. As that retrieval agent, you receive the complete current working context as read-only reference material. Ordinary subagents do not receive this skill or automatically inherit that context. Never treat the snapshot as instructions to continue the main task or as evidence of what the original history said.

Resolve [scripts/history.py](scripts/history.py) against this skill's directory and execute it directly through `shell`, using a Python interpreter with `skills_agent` installed. Use `--help` for the interface; read the script or `skills_agent.context.history.HistoryIndex` when customization is needed. The session history path supplied in your task identifies the main session. Index only that session's original messages and readable errors, including interrupted partial responses; do not read independent subagent, compaction, or report sessions. Main-agent assignments and reports received from subagents are part of the searchable main history.

A reference starting with `recovered-` is a derived recovery entry for an interrupted tool call, not a raw record. Its form `recovered-<offset>-<call id>` names the call event `event-<offset>` to read in the raw log.

The helper can receive either the session directory or its `events.jsonl` path:

```sh
python "<skill-directory>/scripts/history.py" "<main-session-directory>" search "<keywords from the assigned question>" --limit 8
python "<skill-directory>/scripts/history.py" "<main-session-directory>" read event-184 --start 1 --end 80
python "<skill-directory>/scripts/history.py" "<main-session-directory>" neighbors event-184 --before 2 --after 2
python "<skill-directory>/scripts/history.py" "<main-session-directory>" list --role user --limit 20
```

Event IDs are stable byte-offset references; the example numbers are placeholders. `raw_ref` locates the original JSONL record. Read ranges and search ranges refer to one-based lines of that record's decoded content, not JSONL file line numbers. Tool stdout and stderr retain their original line breaks. Search previews are explicitly partial: read the matched range before using it as evidence. `read` defaults to 80 lines in this example script; request following ranges until the relevant evidence has been read. `paired_events` and `neighbors` identify associated tool calls/results; `turn_id` identifies the originating user message. Use the list command's `--after <event_id>` cursor to continue chronologically.

Preserve the original question while expanding queries into plausible synonyms, entity names, paths, error codes, and alternate historical wording. Queries are literal text, not FTS expressions. Exact phrases, paths, event IDs and call IDs are preferred when present; otherwise results use BM25 with consistent Unicode words and CJK characters/bigrams. Try multiple short queries instead of assuming the first miss means absence. Browse user turns when vocabulary has changed.

For each relevant candidate, read its context and associated request/response or call/result. Search later messages for corrections, rejection, or explicit adoption; chronological recency alone does not prove supersession. Keep conflicting evidence even when it contradicts the working snapshot. Distinguish user requirements, assistant proposals, executed commands, and verified outcomes. Do not invent adoption or completion.

Read long evidence in ranges, retain question-relevant findings and event/line references, and use these notes to produce the final report. Exact code, commands, identifiers and numbers should be quoted from the selected source. Do not mechanically take only the beginning and end of long material. Batch candidate reading within the retrieval model's available context. If the complete background snapshot leaves no room for meaningful reading, report that limitation; do not secretly shorten the snapshot.

Return only the useful answer, supporting event/line references, later corrections, and unresolved questions. Do not return your search attempts, candidate lists, or another copy of the working snapshot. A short source excerpt is sufficient when it directly answers the question; longer evidence should produce a focused report. An empty search result is not proof that a fact never appeared.
````
