# workspace 技能提示词版本史

本文按 Git `--all` 可达提交中的实际说明文本变化整理历史；H1、H2…按提交记录的时间升序排列。未改变说明文本的实现调整不单列版本。v1 是 2026-10-11 建档时的当前工作区基线。

“发现的问题”按相邻版本的差异归纳其要处理的缺口；“本版本效果”描述规则变化或预期，不表示已测得实际改善。

当前源文件：`skills_agent/builtin_skills/workspace/SKILL.md`。

## H1

日期：2026-10-09 19:23:06+08:00\
提交：`23e5359e3fb3d05e33e40651d617a06e6fecd548`\
源位置：`skills_agent/builtin_skills/workspace/SKILL.md`

- 发现的问题：此前没有统一的文件读取、搜索及公开会话历史访问指引。
- 本版本目标：为工作区文件和附件提供按范围读取、搜索及历史访问方法。
- 本版本效果：规定通过参考脚本读取指定行段、保留来源位置，并区分文本与二进制附件。

### 完整提示词

````markdown
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
````

## H2

日期：2026-10-09 19:49:51+08:00\
提交：`5fffe1403f3521e846911091e7e00ea9b0e09f6c`\
源位置：`skills_agent/builtin_skills/workspace/SKILL.md`

- 发现的问题：初版以固定脚本命令为中心，缺少按任务改写读取方法的说明。
- 本版本目标：将脚本定位为可学习和改写的参考实现。
- 本版本效果：允许模型读取源码后自行编写脚本，并根据任务选择范围、过滤条件与输出格式。

### 完整提示词

````markdown
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
````

## H3

日期：2026-10-10 04:34:41+08:00\
提交：`08586891a747eea02ff1b647afe944b736534f65`\
源位置：`skills_agent/builtin_skills/workspace/SKILL.md`

- 发现的问题：工作区技能兼管历史读取，缺少独立检索路径和长输出处理说明。
- 本版本目标：将会话历史交给专用技能及子代理，并明确输出模式。
- 本版本效果：工作区搜索避开会话目录；历史访问转交专用检索；新增 direct、auto、report 的选择规则。

### 完整提示词

````markdown
---
name: workspace
description: Read and search text attachments or workspace files with scripts.
---

Read the source of [scripts/workspace.py](scripts/workspace.py) through `shell` to learn its file-reading and search methods. Resolve its path relative to this SKILL.md. Adapt the relevant code or write your own script for the current task, then execute it through `shell` and revise it based on the result. Use inline code or a file in the workspace; no tool registration or fixed script interface is needed.

For UTF-8 text, stream lines and select the relevant ranges instead of loading an entire large file. Preserve filenames and one-based line numbers when reporting matches. The reference `read` function selects inclusive line ranges; `search` demonstrates literal or regular-expression matching and optional filename filtering. Choose ranges, filters, and output format to fit the task.

For PDF, office documents, archives, or other binary attachments, use a suitable installed reader or write a task-specific parser. Extract relevant content before reasoning about it; do not interpret binary data as text.

For original session evidence, use the `history` skill and a named history subagent. Its source-linked helper indexes complete main-session messages, including the delegated tasks and final reports the main agent received. Do not read independent subagent, compaction, or report sessions. Keep general workspace file searches outside session directories.

Your script's output should answer its stated purpose: include exact source paths and line ranges, relevant observations, and execution status. Return code or precise data unchanged when that is what the task needs. Choose `result_mode: direct` for source ranges that must retain exact text; use `auto` for ordinary output or `report` for long evidence that needs a focused conclusion.
````

## H4

日期：2026-10-10 06:20:46+08:00\
提交：`b82571dd623e04c65583d5ce894d950f02071732`\
源位置：`skills_agent/builtin_skills/workspace/SKILL.md`

- 发现的问题：主代理与普通子代理如何请求历史仍不够明确。
- 本版本目标：规定由主代理以 history: true 委派具名历史任务。
- 本版本效果：主代理不直接搜索会话日志；普通子代理把历史输入缺口返回父代理处理。

### 完整提示词

````markdown
---
name: workspace
description: Read and search text attachments or workspace files with scripts.
---

Read the source of [scripts/workspace.py](scripts/workspace.py) through `shell` to learn its file-reading and search methods. Resolve its path relative to this SKILL.md. Adapt the relevant code or write your own script for the current task, then execute it through `shell` and revise it based on the result. Use inline code or a file in the workspace; no tool registration or fixed script interface is needed.

For UTF-8 text, stream lines and select the relevant ranges instead of loading an entire large file. Preserve filenames and one-based line numbers when reporting matches. The reference `read` function selects inclusive line ranges; `search` demonstrates literal or regular-expression matching and optional filename filtering. Choose ranges, filters, and output format to fit the task.

For PDF, office documents, archives, or other binary attachments, use a suitable installed reader or write a task-specific parser. Extract relevant content before reasoning about it; do not interpret binary data as text.

The main agent obtains original conversation evidence by delegating a named `subagent` with `history: true` and a concrete question, rather than searching session logs itself. Ordinary subagents report missing historical input to the parent agent. A dedicated history retrieval subagent follows its assigned retrieval instructions. Keep general workspace file searches outside session directories.

Your script's output should answer its stated purpose: include exact source paths and line ranges, relevant observations, and execution status. Return code or precise data unchanged when that is what the task needs. Choose `result_mode: direct` for source ranges that must retain exact text; use `auto` for ordinary output or `report` for long evidence that needs a focused conclusion.
````

## H5

日期：2026-10-10 19:20:49+08:00\
提交：`99c640f0e67f9e77395d4d0ad75831de18acf977`\
源位置：`skills_agent/builtin_skills/workspace/SKILL.md`

- 发现的问题：简单定点读取也被引导先读取参考脚本，技能适用范围偏宽。
- 本版本目标：允许直接完成简单读取，将参考实现留给需要它的任务。
- 本版本效果：技能描述聚焦大文件和附件；简单搜索可直接使用合适的 shell 命令。

### 完整提示词

````markdown
---
name: workspace
description: Inspect large files and attachments using selective reads, extraction, and precise source locations.
---

Simple targeted reads and searches can use suitable shell commands directly. When a reference implementation would help, read [scripts/workspace.py](scripts/workspace.py) through `shell` to learn its file-reading and search methods. Resolve its path relative to this SKILL.md. Reuse or adapt the relevant code, or write your own script for the current task. Execute inline code or a file through `shell` and revise it based on the result; no tool registration or fixed script interface is needed.

For UTF-8 text, stream lines and select the relevant ranges instead of loading an entire large file. Preserve filenames and one-based line numbers when reporting matches. The reference `read` function selects inclusive line ranges; `search` demonstrates literal or regular-expression matching and optional filename filtering. Choose ranges, filters, and output format to fit the task.

For PDF, office documents, archives, or other binary attachments, use a suitable installed reader or write a task-specific parser. Extract relevant content before reasoning about it; do not interpret binary data as text.

The main agent obtains original conversation evidence by delegating a named `subagent` with `history: true` and a concrete question, rather than searching session logs itself. Ordinary subagents report missing historical input to the parent agent. A dedicated history retrieval subagent follows its assigned retrieval instructions. Keep general workspace file searches outside session directories.

Your script's output should answer its stated purpose: include exact source paths and line ranges, relevant observations, and execution status. Return code or precise data unchanged when that is what the task needs. Choose `result_mode: direct` for source ranges that must retain exact text; use `auto` for ordinary output or `report` for long evidence that needs a focused conclusion.
````

## v1 — 当前工作区基线

日期：2026-10-11\
提交：`工作区基线；与 H5 同文（源文件无未提交改动）`\
源位置：`skills_agent/builtin_skills/workspace/SKILL.md`

- 发现的问题：本次需要为现有提示词建立独立版本记录。
- 本版本目标：将当前生效文本固定为 v1，并保留 H5 及之前的历史。
- 本版本效果：与 H5 完全相同；本次不改变技能行为。

### 完整提示词

````markdown
---
name: workspace
description: Inspect large files and attachments using selective reads, extraction, and precise source locations.
---

Simple targeted reads and searches can use suitable shell commands directly. When a reference implementation would help, read [scripts/workspace.py](scripts/workspace.py) through `shell` to learn its file-reading and search methods. Resolve its path relative to this SKILL.md. Reuse or adapt the relevant code, or write your own script for the current task. Execute inline code or a file through `shell` and revise it based on the result; no tool registration or fixed script interface is needed.

For UTF-8 text, stream lines and select the relevant ranges instead of loading an entire large file. Preserve filenames and one-based line numbers when reporting matches. The reference `read` function selects inclusive line ranges; `search` demonstrates literal or regular-expression matching and optional filename filtering. Choose ranges, filters, and output format to fit the task.

For PDF, office documents, archives, or other binary attachments, use a suitable installed reader or write a task-specific parser. Extract relevant content before reasoning about it; do not interpret binary data as text.

The main agent obtains original conversation evidence by delegating a named `subagent` with `history: true` and a concrete question, rather than searching session logs itself. Ordinary subagents report missing historical input to the parent agent. A dedicated history retrieval subagent follows its assigned retrieval instructions. Keep general workspace file searches outside session directories.

Your script's output should answer its stated purpose: include exact source paths and line ranges, relevant observations, and execution status. Return code or precise data unchanged when that is what the task needs. Choose `result_mode: direct` for source ranges that must retain exact text; use `auto` for ordinary output or `report` for long evidence that needs a focused conclusion.
````

## v2 · 2026-10-11 · 按需环境探测与定位提取

来源：当前工作区（未提交）。现有 tools 不变。

- 发现的问题：既有技能主要提供文本读取与检索参考；模型仍需反复编写环境探测和文档解析代码，缺少可直接使用的定位提取接口。
- 本版本目标：按下一步所需能力探测可执行文件与顶层 Python 模块，直接调用 JSON、CSV、DOCX、XLSX、PDF 提取器，保留格式内位置和可续读游标。
- 本版本效果：新增独立 inspect.py、extract.py 和按需格式参考，保留旧 read/search 接口；文档提取不冒充版式、OCR 或公式重算，PDF 缺读取器时明确失败且不自动安装。实际模型使用效率待观察。

### 完整提示词快照

````markdown
---
name: workspace
description: Inspect task-relevant environment capabilities and read files or attachments through selective, located extraction.
---

Use suitable shell commands for simple reads and searches. The helpers below can be called directly through `shell`; resolve their absolute paths relative to this SKILL.md, then invoke them without first reading their implementation. Use an available Python interpreter. Shell calls are independent, so do not rely on variables set in an earlier call.

- `python /absolute/skill/scripts/inspect.py --executable NAME --module NAME`: check only capabilities needed for the next action. Repeat either option as needed. Module names must be top-level names without dots, so discovery does not execute a parent package. Reports discovered paths or origins and missing names; exit 1 means a requested capability is missing. Discovery does not prove that a program or module runs correctly.
- `python /absolute/skill/scripts/workspace.py read PATH --start 1 --end 200`: stream an inclusive UTF-8 line range. `search PATTERN PATH... [--regex] [--glob '*.py']` preserves filenames and one-based line numbers.
- `python /absolute/skill/scripts/extract.py FORMAT PATH ...`: bounded JSON Pointer, CSV, DOCX, XLSX, or PDF extraction with source locations and continuation cursors. Read [references/extraction.md](references/extraction.md) for the relevant format's options and limits.

Inspect the returned content before deciding the next action. A truncated result is a partial observation: follow its cursor or narrow the selection when omitted content matters. For very long plain-text lines, the feedback skill's `text` helper supports a character budget and line/column continuation. PDF extraction uses an already installed reader and reports missing dependencies; no helper installs packages. Extracted document text does not establish layout, visual correctness, or OCR content. Unsupported binary formats require an appropriate installed reader rather than decoding arbitrary bytes as text.

The main agent obtains original conversation evidence by delegating a named `subagent` with `history: true` and a concrete question, rather than searching session logs itself. Ordinary subagents report missing historical input to the parent agent. A dedicated history retrieval subagent follows its assigned retrieval instructions. Keep general workspace file searches outside session directories.

Choose `result_mode: direct` when exact source text, structured values, or continuation cursors must be retained; use `auto` for ordinary output or `report` for long evidence that needs a focused conclusion. Preserve the helper's failure status and source locations when reporting results.
````


## v3 · 2026-10-13 · 按格式发现定位提取能力并自主选择读取方法

来源：当前工作区（未提交）。与[主工作提示词 v7](agent-system.md) 的按需技能选择规则同步；本轮仅调整技能入口，不修改脚本、工具接口或运行时协议。

- 发现的问题：入口对可处理格式和续读能力的辨识度不足，直接调用 helper 的表述不充分体现学习方法后自主编写脚本。
- 本版本目标：说明大文件和结构化资料的来源定位、续读及环境探测能力，让模型根据当前材料选择普通 shell、复用参考或自行编写解析器。
- 本版本效果：描述改为可发现的具体能力，正文保留游标、原始位置和历史访问边界，删除不必要的执行路径限制。真实模型的技能选择与资料读取质量待观察。

### 完整提示词快照

````markdown
---
name: workspace
description: Read large or structured local files and attachments with source locations and continuation cursors, including JSON, CSV, DOCX, XLSX and PDF; inspect required local executables or modules.
---

Select the part of an input needed for the current decision. Preserve its path and meaningful location; follow a continuation cursor when omitted content matters. Simple text reads and searches can use ordinary shell commands. Learn from, reuse or adapt the reference scripts below, or write a parser suited to the task.

Resolve scripts relative to this SKILL.md and run them through `shell` with an available Python interpreter. Each shell call has its own variables.

| Reference | Capability |
| --- | --- |
| [scripts/workspace.py](scripts/workspace.py) | `read PATH --start 1 --end 200` selects inclusive UTF-8 lines; `search PATTERN PATH... [--regex] [--glob '*.py']` returns filenames and one-based lines. |
| [scripts/extract.py](scripts/extract.py) | Located, bounded JSON Pointer, CSV, DOCX, XLSX and PDF extraction. Read [references/extraction.md](references/extraction.md) for the selected format's options, limits and cursor rules. |
| [scripts/inspect.py](scripts/inspect.py) | `--executable NAME` / `--module NAME` checks required capabilities without running them; flags may repeat. Module names are top-level names, without dots. Exit 1 means a requested capability is missing. |

Keep the source and selection options unchanged while following cursors; re-read changed files. Extraction describes stored content, not document layout, OCR, formula evaluation or application behavior. PDF extraction uses an installed reader and does not install dependencies. Use a suitable reader for other binary formats. For very long text lines, the feedback skill's `text` operation provides line/column continuation.

Choose `result_mode: direct` for exact source, structured values and cursors. Inspect returned content and failure status before using it as evidence; finding an executable or module does not establish that it runs correctly.

The main agent retrieves original conversation evidence through a named `subagent` with `history: true` and a concrete question. Ordinary subagents report missing historical input to the parent; dedicated history retrieval subagents follow their assigned instructions. Keep workspace searches outside session directories.
````
