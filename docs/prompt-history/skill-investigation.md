# investigation 技能提示词版本史

本文按 Git `--all` 可达提交中的实际说明文本变化整理历史；H1、H2…按提交记录的时间升序排列。未改变说明文本的实现调整不单列版本。v1 是 2026-10-11 建档时的当前工作区基线。

“发现的问题”按相邻版本的差异归纳其要处理的缺口；“本版本效果”描述规则变化或预期，不表示已测得实际改善。

当前源文件：`skills_agent/builtin_skills/investigation/SKILL.md`。

## H1

日期：2026-10-10 09:40:31+08:00\
提交：`f91e56640f9edd87db2cbb85842c9e3117bd2515`\
源位置：`skills_agent/builtin_skills/investigation/SKILL.md`

- 发现的问题：此前没有针对不确定解释、相互矛盾信息及调查停止条件的独立指引。
- 本版本目标：建立围绕问题选择调查方法、区分解释并报告局限的流程。
- 本版本效果：要求从待解决的问题出发，区分观察与结论，并在已足够支持决策时停止。

### 完整提示词

````markdown
---
name: investigation
description: Investigate a question, discrepancy, or suspected cause using traceable evidence and checks that distinguish competing explanations.
---

Start with the question to resolve and the observation that would distinguish the plausible explanations. Include the possibility that the suspected problem is absent. Choose the smallest useful check; scale the investigation to the decision it supports.

Use original sources and existing measurement or inspection interfaces where available. For quantities, identify the measured object, unit, denominator, scope and method before comparing results. Keep estimates separate from measurements. Do not infer a property such as usefulness, causation or completion from a convenient proxy alone.

Collect enough surrounding material to interpret the finding. Keep source locations and relevant versions or times with observations, including execution status and errors. A partial read, filtered sample or failed search does not establish absence. Preserve complete evidence in accessible files when needed; return the relevant findings and precise references instead of dumping the full collection. Follow the assigned access boundaries when choosing sources.

Check that an experiment actually reached the condition under investigation. A failed fixture, unavailable input or earlier error is evidence about the experiment, not proof of the suspected behavior. Prefer the real implementation over a separately re-created approximation when investigating software behavior.

Use new results to revise the explanation. Follow up on contradictions and check decisive claims at their sources. Repeat a check only when an input, method or unresolved question gives it a reason to produce new information. Independent, bounded investigations can be delegated with the question, available evidence, constraints and expected report; use their evidence rather than a completion claim alone.

Report observations, supported conclusions and remaining uncertainty distinctly. State limitations that affect the answer. Stop when the evidence is sufficient for the requested decision; do not add speculative fixes or broaden the assignment to fill gaps that do not matter to it.
````

## H2

日期：2026-10-10 11:06:10+08:00\
提交：`494a85dfaad408a83211f37742900bb712a910eb`\
源位置：`skills_agent/builtin_skills/investigation/SKILL.md`

- 发现的问题：上一版对自定义测量的适用性、替代解释和从发现到建议的推断约束仍可细化。
- 本版本目标：先从问题确定方法，明确替代解释与建议之间的推断边界。
- 本版本效果：增加自定义方法的先行检查、竞争解释及重要不确定性的处理规则，并明确普通查找不需复杂调查。

### 完整提示词

````markdown
---
name: investigation
description: Resolve uncertain claims, discrepancies or causes using source evidence and checks that distinguish competing explanations.
---

Identify the question or decision the investigation must support. Derive the required evidence from that question and the user's constraints before choosing a method. Include the possibility that the suspected problem is absent. Ordinary source lookup needs no elaborate investigation.

Prefer original sources and established inspection methods. Choose a method whose assumptions can be checked, whose results can be observed and whose failures have limited consequences. Before relying on a custom measurement or classification, check known-answer inputs or independently inspect a sample. Identify the measured object, unit, denominator, scope and method; keep estimates separate from measurements.

Ask whether the same observation could occur if the proposed explanation were false. If so, seek evidence that distinguishes the alternatives or narrow the conclusion. Confirm that an experiment reached the condition it claims to test; setup failure describes the experiment, not the suspected behavior. A precise measurement of a proxy does not establish the property or cause inferred from it.

Keep source locations, relevant versions and execution status with observations. Read enough surrounding material to interpret them and look for contradictory evidence. A partial read, filtered sample or failed search does not establish absence. Preserve full evidence in accessible files when needed; return relevant findings and references. Use the assigned access route, including delegated retrieval for conversation-history analysis.

Distinguish observations, assumptions and conclusions. Check the evidence for each step from a finding to a recommendation; certainty does not carry automatically between them. Revise the explanation when evidence conflicts. Seek an independent check when a consequential uncertainty remains; agreement alone is not evidence. Continue only when another check can materially inform the decision. Stop when the question is supported sufficiently, or state what remains unknown without expanding the task or claiming more than the evidence shows.
````

## H3

日期：2026-10-10 19:20:49+08:00\
提交：`99c640f0e67f9e77395d4d0ad75831de18acf977`\
源位置：`skills_agent/builtin_skills/investigation/SKILL.md`

- 发现的问题：目录描述仍可能让简单信息查找被归为调查任务。
- 本版本目标：收窄技能的触发条件。
- 本版本效果：仅修改 description，明确直接查找不足时才使用调查技能；正文保持不变。

### 完整提示词

````markdown
---
name: investigation
description: Investigate uncertain claims, conflicting evidence, or unexplained behavior when a direct lookup is insufficient.
---

Identify the question or decision the investigation must support. Derive the required evidence from that question and the user's constraints before choosing a method. Include the possibility that the suspected problem is absent. Ordinary source lookup needs no elaborate investigation.

Prefer original sources and established inspection methods. Choose a method whose assumptions can be checked, whose results can be observed and whose failures have limited consequences. Before relying on a custom measurement or classification, check known-answer inputs or independently inspect a sample. Identify the measured object, unit, denominator, scope and method; keep estimates separate from measurements.

Ask whether the same observation could occur if the proposed explanation were false. If so, seek evidence that distinguishes the alternatives or narrow the conclusion. Confirm that an experiment reached the condition it claims to test; setup failure describes the experiment, not the suspected behavior. A precise measurement of a proxy does not establish the property or cause inferred from it.

Keep source locations, relevant versions and execution status with observations. Read enough surrounding material to interpret them and look for contradictory evidence. A partial read, filtered sample or failed search does not establish absence. Preserve full evidence in accessible files when needed; return relevant findings and references. Use the assigned access route, including delegated retrieval for conversation-history analysis.

Distinguish observations, assumptions and conclusions. Check the evidence for each step from a finding to a recommendation; certainty does not carry automatically between them. Revise the explanation when evidence conflicts. Seek an independent check when a consequential uncertainty remains; agreement alone is not evidence. Continue only when another check can materially inform the decision. Stop when the question is supported sufficiently, or state what remains unknown without expanding the task or claiming more than the evidence shows.
````

## v1 — 当前工作区基线

日期：2026-10-11\
提交：`工作区基线；与 H3 同文（源文件无未提交改动）`\
源位置：`skills_agent/builtin_skills/investigation/SKILL.md`

- 发现的问题：本次需要为现有提示词建立独立版本记录。
- 本版本目标：将当前生效文本固定为 v1，并保留 H3 及之前的历史。
- 本版本效果：与 H3 完全相同；本次不改变技能行为。

### 完整提示词

````markdown
---
name: investigation
description: Investigate uncertain claims, conflicting evidence, or unexplained behavior when a direct lookup is insufficient.
---

Identify the question or decision the investigation must support. Derive the required evidence from that question and the user's constraints before choosing a method. Include the possibility that the suspected problem is absent. Ordinary source lookup needs no elaborate investigation.

Prefer original sources and established inspection methods. Choose a method whose assumptions can be checked, whose results can be observed and whose failures have limited consequences. Before relying on a custom measurement or classification, check known-answer inputs or independently inspect a sample. Identify the measured object, unit, denominator, scope and method; keep estimates separate from measurements.

Ask whether the same observation could occur if the proposed explanation were false. If so, seek evidence that distinguishes the alternatives or narrow the conclusion. Confirm that an experiment reached the condition it claims to test; setup failure describes the experiment, not the suspected behavior. A precise measurement of a proxy does not establish the property or cause inferred from it.

Keep source locations, relevant versions and execution status with observations. Read enough surrounding material to interpret them and look for contradictory evidence. A partial read, filtered sample or failed search does not establish absence. Preserve full evidence in accessible files when needed; return relevant findings and references. Use the assigned access route, including delegated retrieval for conversation-history analysis.

Distinguish observations, assumptions and conclusions. Check the evidence for each step from a finding to a recommendation; certainty does not carry automatically between them. Revise the explanation when evidence conflicts. Seek an independent check when a consequential uncertainty remains; agreement alone is not evidence. Continue only when another check can materially inform the decision. Stop when the question is supported sufficiently, or state what remains unknown without expanding the task or claiming more than the evidence shows.
````

## v2 · 2026-10-11 · 获取已知来源并保留原始资料

来源：当前工作区（未提交）。保留既有调查推理规则，补充通用 HTTP(S) 来源获取能力。现有 tools 不变。

- 发现的问题：调查规范要求原始来源和精确出处，但缺少直接获取已知 URL、保存完整资料与来源元数据的脚本，模型需要反复自行实现。
- 本版本目标：用现有 shell 获取已知来源，分别保存响应体、元数据和提取文本；保留 HTTP 状态、重定向、时刻、完整性与哈希，按预览范围继续读取。
- 本版本效果：新增独立 fetch.py，通过响应上限和共用网络 deadline 限制未知来源的占用，失败与不完整内容明确保留；静态提取不冒充浏览器、全文含义、搜索或登录。实际调查效率和引用质量待使用观察。

### 完整提示词快照

````markdown
---
name: investigation
description: Acquire and preserve known HTTP(S) sources, and investigate uncertain claims, conflicting evidence, or unexplained behavior.
---

Identify the question or decision the investigation must support. Derive the required evidence from that question and the user's constraints before choosing a method. Include the possibility that the suspected problem is absent. Ordinary source lookup needs no elaborate investigation.

Prefer original sources and established inspection methods. Choose a method whose assumptions can be checked, whose results can be observed and whose failures have limited consequences. Before relying on a custom measurement or classification, check known-answer inputs or independently inspect a sample. Identify the measured object, unit, denominator, scope and method; keep estimates separate from measurements.

Ask whether the same observation could occur if the proposed explanation were false. If so, seek evidence that distinguishes the alternatives or narrow the conclusion. Confirm that an experiment reached the condition it claims to test; setup failure describes the experiment, not the suspected behavior. A precise measurement of a proxy does not establish the property or cause inferred from it.

Keep source locations, relevant versions and execution status with observations. Read enough surrounding material to interpret them and look for contradictory evidence. A partial read, filtered sample or failed search does not establish absence. Preserve full evidence in accessible files when needed; return relevant findings and references. Use the assigned access route, including delegated retrieval for conversation-history analysis.

For a known HTTP(S) URL, resolve [scripts/fetch.py](scripts/fetch.py) against this skill's directory and run it through the existing `shell` tool with Python 3.11 or newer:

```sh
python3 /absolute/path/to/investigation/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

Each call creates a fresh evidence directory containing the complete received body as `body.bin`, `metadata.json` with requested/final URLs, redirect hops, actual HTTP status, response headers, UTC retrieval time and SHA-256, and extracted text when supported. HTTP errors retain their bodies and return failure; interrupted or failed transfers are marked incomplete. Inspect status, completeness and extraction diagnostics before using the preview. Retrieval time is not publication time, and a saved path alone does not establish its contents. Use `shell result_mode=direct` for exact excerpts, or read the preserved files in subsequent calls when the bounded preview is insufficient.

`--extract auto` handles declared HTML, JSON and text; `none` saves the body only. Explicit `text`, `html` or `json` selects extraction when a source's type is incorrect; `--encoding` overrides a known charset. `--max-extract-bytes` limits extraction memory; `--max-chars` limits only the returned preview. `--max-body-bytes` defaults to 64 MiB: exceeding it preserves only the acquired prefix and reports an incomplete failure. Raise it explicitly for a source that needs more. HTML extraction normalizes whitespace and omits scripts, styles and templates: it can include hidden or navigation text and cannot establish the rendered page, JavaScript content or complete article meaning. JSON extraction validates without reformatting the decoded source. The raw response remains authoritative.

The helper performs GET for known URLs; it does not search the internet, log in, execute page scripts or install readers. It sends no authorization/cookies/custom headers and does not inherit environment proxy credentials. Credential-bearing URLs and redirects are rejected. `--timeout` applies one deadline across redirects, response headers and body transfer; operating-system DNS lookup can exceed socket timeouts. If access or extraction fails, state the specific limitation and use an available appropriate access route; do not invent search results or unseen source contents. Use `--help` for the full interface.

Distinguish observations, assumptions and conclusions. Check the evidence for each step from a finding to a recommendation; certainty does not carry automatically between them. Revise the explanation when evidence conflicts. Seek an independent check when a consequential uncertainty remains; agreement alone is not evidence. Continue only when another check can materially inform the decision. Stop when the question is supported sufficiently, or state what remains unknown without expanding the task or claiming more than the evidence shows.
````


## v3 · 2026-10-13 · 聚焦可区分解释的证据和来源完整性

来源：当前工作区（未提交）。与[主工作提示词 v7](agent-system.md) 的按需技能选择规则同步；本轮仅调整技能入口，不修改脚本、工具接口或运行时协议。

- 发现的问题：通用调查说教与获取操作混在入口，重复规则使已知来源获取能力及证据局限不够突出。
- 本版本目标：以能够区分解释的观察组织调查，简化入口并保留已知 URL 获取、来源记录、失败和截断契约，允许选用或编写适合来源的读取方法。
- 本版本效果：入口更集中于证据与方法，保留请求来源、完整性、获取时间和渲染局限等必要区别；本轮没有修改脚本。真实模型的调查范围与结论质量待观察。

### 完整提示词快照

````markdown
---
name: investigation
description: Resolve conflicting evidence or uncertain causes using discriminating observations; acquire known HTTP(S) sources with original response bodies, status, provenance and completeness records.
---

Choose an observation that can distinguish the plausible explanations, including that the suspected problem is absent. Check that an experiment reaches its intended condition. For a custom measurement, identify the object, unit, denominator and method; check a known-answer input or inspect a sample before relying on it. A proxy measurement alone does not establish the inferred cause.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct, and stop investigating when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

For a known URL, [scripts/fetch.py](scripts/fetch.py) is a reference for acquisition with provenance. Read or adapt it, reuse it directly, or write a reader appropriate to the source. Direct use requires Python 3.11 or newer and the path resolved relative to this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request/final URLs, redirect hops, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. Failed or interrupted transfers remain marked incomplete; HTTP error bodies are retained with failure status. Inspect status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` handles declared HTML, JSON and text; use `none` to save bytes only or an explicit type/encoding for known incorrect metadata. `--max-chars` limits the preview, `--max-extract-bytes` bounds extraction memory, and `--max-body-bytes` defaults to 64 MiB. Exceeding the body limit preserves a prefix and reports failure; raise it only when the task needs more. HTML extraction omits scripts/styles/templates and normalizes whitespace; it does not establish rendered or JavaScript content. JSON extraction validates without reformatting. The raw response remains authoritative.

This helper fetches known URLs; it does not provide search, login or browser execution. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects credential-bearing URLs and redirects. Its timeout spans redirects, headers and body transfer, but OS DNS lookup may outlast socket timeouts. For unsupported access, use an appropriate authorized route or report the limitation. Choose `result_mode: direct` for exact excerpts; inspect saved content when a preview is insufficient.
````

<!-- 历史分支：01-data-semantics；下列版本号属于该分支，保留原记录。 -->

## v4 · 2026-10-26 · 澄清冲突数据的含义

来源：分支 `experiment/ale-data-semantics` 的本次提交；共同基线 `1dd2267`。这是独立实验版本，未与其他方法改写合并。

- 发现的问题：已有规则列出测量对象与单位，但未说明定义不一致时应如何解释数值冲突；两个实现也可能共享同一个未决定义。
- 本版本目标：以通用的时期总量与期末值例子区分定义冲突和实现错误，并在数据转换中保留必要标识与坐标。
- 本版本效果：仅替换入口正文的方法说明；名称、发现描述、来源获取脚本和调用接口均保持不变。没有新增强制阶段或调用。真实模型行为与测评效果待观察。

### 完整提示词快照

````markdown
---
name: investigation
description: >-
  Use to resolve conflicting evidence or uncertain causes with discriminating
  observations, or to capture known HTTP(S) sources with provenance and completeness
  records. Skip straightforward lookups needing no source capture or comparison,
  routine edits, and conversation-history retrieval assigned to the history skill.
---

Choose an observation that can distinguish the plausible explanations, including that the suspected problem is absent. Check that an experiment reaches its intended condition. A proxy measurement alone does not establish the inferred cause.

When a measurement or comparison disagrees, check whether both sides describe the same object, unit, denominator, time or coordinate, and aggregation. A period total and a value at the period's end can both be correct; recalculating them does not settle which the task asks for. Resolve the relevant definition from the task, source or established contract before treating the difference as an implementation error. Carry the needed identifiers and coordinates through transformations. For a custom measurement, check a known-answer input or inspect a sample. Agreement between implementations sharing an uncertain definition does not resolve that uncertainty.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct, and stop investigating when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

For a known URL, [scripts/fetch.py](scripts/fetch.py) is a reference for acquisition with provenance. Read or adapt it, reuse it directly, or write a reader appropriate to the source. Direct use requires Python 3.11 or newer and the path resolved relative to this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request/final URLs, redirect hops, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. Failed or interrupted transfers remain marked incomplete; HTTP error bodies are retained with failure status. Inspect status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` handles declared HTML, JSON and text; use `none` to save bytes only or an explicit type/encoding for known incorrect metadata. `--max-chars` limits the preview, `--max-extract-bytes` bounds extraction memory, and `--max-body-bytes` defaults to 64 MiB. Exceeding the body limit preserves a prefix and reports failure; raise it only when the task needs more. HTML extraction omits scripts/styles/templates and normalizes whitespace; it does not establish rendered or JavaScript content. JSON extraction validates without reformatting. The raw response remains authoritative.

This helper fetches known URLs; it does not provide search, login or browser execution. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects credential-bearing URLs and redirects. Its timeout spans redirects, headers and body transfer, but OS DNS lookup may outlast socket timeouts. For unsupported access, use an appropriate authorized route or report the limitation. Choose `result_mode: direct` for exact excerpts; inspect saved content when a preview is insufficient.
````


## v5 · 2026-10-27 · 将口径判断落实到依赖产物

来源：本分支当前工作区（未提交）。保留独立实验方向，仅修改 investigation 方法正文；不修改系统共用原则、注入机制或工具接口。

- 发现的问题：定义核对可能只停留在解释中，未更新采用该定义的转换、计算和交付物；证据不足时也可能用容易吻合的口径代替未决问题。
- 本版本目标：保留数据语义检查，要求已确定的定义落实到受影响结果；依据不足时明确保留歧义，不以数值吻合作为选取口径的理由。
- 本版本效果：已增加对应的行动与结果核对要求；名称、发现描述和来源获取契约保持不变。真实模型遵循情况及测评效果待观察。

### 完整提示词快照

````markdown
---
name: investigation
description: >-
  Use to resolve conflicting evidence or uncertain causes with discriminating
  observations, or to capture known HTTP(S) sources with provenance and completeness
  records. Skip straightforward lookups needing no source capture or comparison,
  routine edits, and conversation-history retrieval assigned to the history skill.
---

Choose an observation that can distinguish the plausible explanations, including that the suspected problem is absent. Check that an experiment reaches its intended condition. A proxy measurement alone does not establish the inferred cause.

When a measurement or comparison disagrees, check whether both sides describe the same object, unit, denominator, time or coordinate, and aggregation. A period total and a value at the period's end can both be correct; recalculating them does not settle which the task asks for. Resolve the relevant definition from the task, source or established contract before treating the difference as an implementation error. Carry the needed identifiers and coordinates through transformations. For a custom measurement, check a known-answer input or inspect a sample. Agreement between implementations sharing an uncertain definition does not resolve that uncertainty.

Once a definition is resolved, apply it to the affected transformations, calculations and comparisons, update dependent deliverables, and inspect the changed results. If the available evidence does not settle the definition, keep the ambiguity explicit rather than selecting the interpretation that makes values agree.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct, and stop investigating when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

For a known URL, [scripts/fetch.py](scripts/fetch.py) is a reference for acquisition with provenance. Read or adapt it, reuse it directly, or write a reader appropriate to the source. Direct use requires Python 3.11 or newer and the path resolved relative to this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request/final URLs, redirect hops, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. Failed or interrupted transfers remain marked incomplete; HTTP error bodies are retained with failure status. Inspect status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` handles declared HTML, JSON and text; use `none` to save bytes only or an explicit type/encoding for known incorrect metadata. `--max-chars` limits the preview, `--max-extract-bytes` bounds extraction memory, and `--max-body-bytes` defaults to 64 MiB. Exceeding the body limit preserves a prefix and reports failure; raise it only when the task needs more. HTML extraction omits scripts/styles/templates and normalizes whitespace; it does not establish rendered or JavaScript content. JSON extraction validates without reformatting. The raw response remains authoritative.

This helper fetches known URLs; it does not provide search, login or browser execution. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects credential-bearing URLs and redirects. Its timeout spans redirects, headers and body transfer, but OS DNS lookup may outlast socket timeouts. For unsupported access, use an appropriate authorized route or report the limitation. Choose `result_mode: direct` for exact excerpts; inspect saved content when a preview is insufficient.
````

<!-- 历史分支：02-discriminating-experiments；下列版本号属于该分支，保留原记录。 -->

## v4 · 2026-10-26 · 按差异形态选择判别观察

来源：分支 `experiment/ale-discriminating-experiments` 的本次提交；共同基线 `1dd2267`。这是独立实验版本，未与其他方法改写合并。

- 发现的问题：已有规则要求区分解释，但在多种尝试产生相近误差或搜索停滞时，未给出足够具体的观察选择方法。
- 本版本目标：把误差的共同部分、分层结构与边界变化用于选择能够改变判断的观察；既适用于执行试验，也适用于已有静态证据。
- 本版本效果：仅替换入口正文的方法说明；名称、发现描述、来源获取脚本和调用接口均保持不变。没有新增强制阶段或调用。真实模型行为与测评效果待观察。

### 完整提示词快照

````markdown
---
name: investigation
description: >-
  Use to resolve conflicting evidence or uncertain causes with discriminating
  observations, or to capture known HTTP(S) sources with provenance and completeness
  records. Skip straightforward lookups needing no source capture or comparison,
  routine edits, and conversation-history retrieval assigned to the history skill.
---

Choose an observation whose possible outcomes would favor different plausible explanations, including that the suspected problem is absent. If several changes leave a similar mismatch, inspect an assumption they share instead of trying another equivalent variant. A systematic mismatch can be split by processing stage or input condition to find where results first diverge. When a smooth search stalls, a boundary probe may expose an untested convention or discrete behavior. These are alternatives when the observed pattern warrants them; existing logs or source data may already distinguish the explanations without another run.

Check that an experiment reaches its intended condition, and let its result determine which explanation to retain or revise. For a custom measurement, identify the object, unit, denominator and method; check a known-answer input or inspect a sample before relying on it. A proxy measurement alone does not establish the inferred cause.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct, and stop investigating when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

For a known URL, [scripts/fetch.py](scripts/fetch.py) is a reference for acquisition with provenance. Read or adapt it, reuse it directly, or write a reader appropriate to the source. Direct use requires Python 3.11 or newer and the path resolved relative to this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request/final URLs, redirect hops, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. Failed or interrupted transfers remain marked incomplete; HTTP error bodies are retained with failure status. Inspect status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` handles declared HTML, JSON and text; use `none` to save bytes only or an explicit type/encoding for known incorrect metadata. `--max-chars` limits the preview, `--max-extract-bytes` bounds extraction memory, and `--max-body-bytes` defaults to 64 MiB. Exceeding the body limit preserves a prefix and reports failure; raise it only when the task needs more. HTML extraction omits scripts/styles/templates and normalizes whitespace; it does not establish rendered or JavaScript content. JSON extraction validates without reformatting. The raw response remains authoritative.

This helper fetches known URLs; it does not provide search, login or browser execution. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects credential-bearing URLs and redirects. Its timeout spans redirects, headers and body transfer, but OS DNS lookup may outlast socket timeouts. For unsupported access, use an appropriate authorized route or report the limitation. Choose `result_mode: direct` for exact excerpts; inspect saved content when a preview is insufficient.
````


## v5 · 2026-10-27 · 将判别观察落实到对应行动

来源：本分支当前工作区（未提交）。保留独立实验方向，仅修改 investigation 方法正文；不修改系统共用原则、注入机制或工具接口。

- 发现的问题：原文要求按实验结果保留或修正解释，但未明确定位缺陷、出现反证和仍无法区分原因时各自应采取的行动。
- 本版本目标：将结果分别落实为局部修复及核对、撤回或限缩诊断并调整行动、在决策仍未解决时改用其他观察，保留未受影响的工作。
- 本版本效果：已增加对应的行动与结果核对要求；名称、发现描述和来源获取契约保持不变。真实模型遵循情况及测评效果待观察。

### 完整提示词快照

````markdown
---
name: investigation
description: >-
  Use to resolve conflicting evidence or uncertain causes with discriminating
  observations, or to capture known HTTP(S) sources with provenance and completeness
  records. Skip straightforward lookups needing no source capture or comparison,
  routine edits, and conversation-history retrieval assigned to the history skill.
---

Choose an observation whose possible outcomes would favor different plausible explanations, including that the suspected problem is absent. If several changes leave a similar mismatch, inspect an assumption they share instead of trying another equivalent variant. A systematic mismatch can be split by processing stage or input condition to find where results first diverge. When a smooth search stalls, a boundary probe may expose an untested convention or discrete behavior. These are alternatives when the observed pattern warrants them; existing logs or source data may already distinguish the explanations without another run.

Check that an experiment reaches its intended condition before interpreting its result. When the evidence identifies an in-scope defect, repair that part and verify the affected output while preserving unaffected work. When it contradicts or limits a suspected cause, withdraw or narrow the diagnosis and revise the next action. If the result leaves the causes indistinguishable and the decision still matters, choose a different observation rather than another equivalent run.

For a custom measurement, identify the object, unit, denominator and method; check a known-answer input or inspect a sample before relying on it. A proxy measurement alone does not establish the inferred cause.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct, and stop investigating when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

For a known URL, [scripts/fetch.py](scripts/fetch.py) is a reference for acquisition with provenance. Read or adapt it, reuse it directly, or write a reader appropriate to the source. Direct use requires Python 3.11 or newer and the path resolved relative to this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request/final URLs, redirect hops, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. Failed or interrupted transfers remain marked incomplete; HTTP error bodies are retained with failure status. Inspect status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` handles declared HTML, JSON and text; use `none` to save bytes only or an explicit type/encoding for known incorrect metadata. `--max-chars` limits the preview, `--max-extract-bytes` bounds extraction memory, and `--max-body-bytes` defaults to 64 MiB. Exceeding the body limit preserves a prefix and reports failure; raise it only when the task needs more. HTML extraction omits scripts/styles/templates and normalizes whitespace; it does not establish rendered or JavaScript content. JSON extraction validates without reformatting. The raw response remains authoritative.

This helper fetches known URLs; it does not provide search, login or browser execution. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects credential-bearing URLs and redirects. Its timeout spans redirects, headers and body transfer, but OS DNS lookup may outlast socket timeouts. For unsupported access, use an appropriate authorized route or report the limitation. Choose `result_mode: direct` for exact excerpts; inspect saved content when a preview is insufficient.
````

## v6 · 2026-10-27 · 统一语义核对、判别实验与后续行动

来源：六个实验 worktree 的共享内容整理，工作区未提交。历史分支记录保留；同名旧版本按来源分支区分。

- 发现的问题：此前01与02分别保存语义和实验指导，导致除注入选择外还有正文差异。
- 本版本目标：合并两版有效方法，去除重复；口径修正和实验结果均落实到局部行动与依赖产物。
- 本版本效果：已形成共享正文和实现；各 worktree 只保留显式注入选择差异。实际模型行为和测评分数待运行验证，不以格式或单元测试推断效果。

### 完整原文

````markdown
---
name: investigation
description: >-
  Use to resolve conflicting evidence or uncertain causes with discriminating
  observations, or to capture known HTTP(S) sources with provenance and completeness
  records. Skip straightforward lookups needing no source capture or comparison,
  routine edits, and conversation-history retrieval assigned to the history skill.
---

Choose an observation whose possible outcomes would favor different plausible explanations, including that the suspected problem is absent. If several changes leave a similar mismatch, inspect an assumption they share instead of trying another equivalent variant. A systematic mismatch can be split by processing stage or input condition to find where results first diverge. When a smooth search stalls, a boundary probe may expose an untested convention or discrete behavior. These are alternatives when the observed pattern warrants them; existing logs or source data may already distinguish the explanations without another run.

When a measurement or comparison disagrees, check whether both sides describe the same object, unit, denominator, time or coordinate, and aggregation. A period total and a value at the period's end can both be correct; recalculating them does not settle which the task asks for. Resolve the relevant definition from the task, source or established contract before treating the difference as an implementation error. Carry the needed identifiers and coordinates through transformations. If available evidence does not settle the definition, keep the ambiguity explicit rather than selecting the interpretation that makes values agree. For a custom measurement, establish these definitions and the measurement method, then check a known-answer input or inspect a sample before relying on it. Agreement between implementations sharing an uncertain definition does not resolve that uncertainty. A proxy measurement alone does not establish the inferred cause.

Check that an experiment reaches its intended condition before interpreting its result. When evidence identifies an in-scope defect, repair that part while preserving unaffected work. Apply resolved definitions consistently to affected transformations, calculations and comparisons. After either change, update and inspect the affected deliverables. When evidence contradicts or limits a suspected cause, withdraw or narrow the diagnosis and revise the next action. If the result leaves the causes indistinguishable and the decision still matters, choose a different observation rather than another equivalent run.

Retain source locations and relevant versions with observations. Read enough context to assess contradictory evidence; a filtered sample or failed search does not prove absence. Keep observations, assumptions and conclusions distinct, and stop investigating when further checks would not change the decision. Ordinary source lookup needs no elaborate investigation. Conversation history follows its assigned delegated retrieval route.

For a known URL, [scripts/fetch.py](scripts/fetch.py) is a reference for acquisition with provenance. Read or adapt it, reuse it directly, or write a reader appropriate to the source. Direct use requires Python 3.11 or newer and the path resolved relative to this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request/final URLs, redirect hops, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. Failed or interrupted transfers remain marked incomplete; HTTP error bodies are retained with failure status. Inspect status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` handles declared HTML, JSON and text; use `none` to save bytes only or an explicit type/encoding for known incorrect metadata. `--max-chars` limits the preview, `--max-extract-bytes` bounds extraction memory, and `--max-body-bytes` defaults to 64 MiB. Exceeding the body limit preserves a prefix and reports failure; raise it only when the task needs more. HTML extraction omits scripts/styles/templates and normalizes whitespace; it does not establish rendered or JavaScript content. JSON extraction validates without reformatting. The raw response remains authoritative.

This helper fetches known URLs; it does not provide search, login or browser execution. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects credential-bearing URLs and redirects. Its timeout spans redirects, headers and body transfer, but OS DNS lookup may outlast socket timeouts. For unsupported access, use an appropriate authorized route or report the limitation. Choose `result_mode: direct` for exact excerpts; inspect saved content when a preview is insufficient.
````

## v7 · 2026-10-27 · 直接英文表达与明确使用边界

来源：本轮工作区未提交；skills_agent/builtin_skills/investigation/SKILL.md。

- 发现的问题：部分指令较抽象或过长，使用与跳过条件需要更容易区分。
- 本版本目标：使用清楚、自然、直接的英文说明动作与边界；保留实质约束、原脚本接口和代码示例。六个实验 worktree 使用同一正文。
- 本版本效果：技能格式、本地引用和原有代码示例核对通过；已完成技能路由场景审阅。未运行新一轮真实模型测评，行为与分数效果待验证。

### 完整原文

````text
---
name: investigation
description: >-
  Investigate conflicting evidence or uncertain causes by choosing checks that
  distinguish possible explanations. Also use to save a known HTTP(S) source with
  its origin and transfer status. Skip routine edits, straightforward lookups that
  need no saved source or comparison, and conversation-history retrieval.
---

# Investigate a specific uncertainty

Use this skill when the cause or meaning of an observation is unclear and the answer would change the next action. Use the source-capture helper when a known URL needs a saved response and retrieval record. A routine lookup or edit does not need an investigation. Retrieve conversation history through its assigned delegated route.

## Choose a check that can change the diagnosis

Identify a few plausible explanations, including the possibility that the suspected problem is absent. Choose an observation whose possible results would support different explanations. Existing logs or source data may already answer the question.

Match the check to the observed pattern:

- If several changes leave the same mismatch, check a condition they all rely on before trying another similar variation.
- If the mismatch is systematic, compare processing stages or input conditions to find where the results first differ.
- If a search over smoothly varying parameters stalls, checking a boundary may reveal an untested convention or a discrete change in behavior.

These are alternative approaches, not required steps. Confirm that an experiment actually reaches the condition you intended to test before interpreting its result. If the result still cannot distinguish the causes and the decision matters, choose a different observation.

## Check what the values mean

When measurements disagree, check whether both describe the same object, unit, denominator, time, coordinate and aggregation. A period total and a value at the end of that period may both be correct. Recalculating them does not establish which one the task requires.

Resolve the definition from the task, source or established contract before treating the difference as a code defect. Preserve the identifiers and coordinates needed through later transformations. If the evidence does not settle the definition, state the remaining ambiguity; do not choose a meaning just because it makes the numbers agree.

Before relying on a custom measurement, establish these definitions and the measurement method. Then inspect a sample or check an input with a known answer. Two implementations that share an uncertain definition do not resolve that uncertainty. An indirect measurement alone does not prove the suspected cause.

## Apply what the evidence establishes

When evidence identifies a defect within the task's scope, repair that part and preserve unaffected work. When a definition is resolved, apply it consistently to the affected transformations, calculations and comparisons. Update and inspect the affected deliverables after either change.

When evidence contradicts or limits a suspected cause, withdraw or narrow the diagnosis and change the next action accordingly. Keep observations, assumptions and conclusions separate. Save source locations and relevant versions with observations. Read enough surrounding context to assess contradictory evidence: a filtered sample or an unsuccessful search does not prove that something is absent.

Stop investigating when another check would not change the decision, and continue the task.

## Capture a known source

[scripts/fetch.py](scripts/fetch.py) shows how to save a source with its retrieval record. Use it directly, adapt it, or write a reader suited to the source. Direct use requires Python 3.11 or newer. Resolve its absolute path from this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request and final URLs, redirects, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. It marks failed or interrupted transfers as incomplete and keeps HTTP error bodies with their failure status. Check status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` uses the declared HTML, JSON or text type. Use `none` to save bytes only, or supply the type or encoding when the metadata is known to be wrong. `--max-chars` limits the preview, `--max-extract-bytes` limits extraction memory, and `--max-body-bytes` defaults to 64 MiB. If the body exceeds that limit, the helper keeps the received prefix and reports failure. Raise the limit only when the task needs more.

HTML extraction removes scripts, styles and templates and normalizes whitespace. It does not show rendered content or content produced by JavaScript. JSON extraction validates the response without reformatting it. Use the raw response as the source of record.

The helper fetches known URLs; it cannot search, log in or run a browser. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects URLs and redirects that contain credentials. Its timeout covers redirects, headers and body transfer, but operating-system DNS lookup may outlast a socket timeout. If the source needs unsupported access, use an appropriate authorized route or report the limitation.

Use `result_mode: direct` for exact excerpts. Read the saved content when the preview is not enough.
````

## v8 · 2026-10-28 · 明确召回条件与主动比较

来源：六个实验 worktree 的共享修改；提交标识见 [本轮提交记录](README.md#2026-10-28-召回条件与名称更新)。

当前源文件：`skills_agent/builtin_skills/investigation/SKILL.md`。

- 发现的问题：20261028 的45次基线运行均可见技能目录，但没有读取 investigation 正文；原描述与开头偏向结果冲突和故障诊断，没有清楚表达主动比较候选方法的使用场景。
- 本版本目标：保留 investigation 名称，逐字采用已确认的自然语言 description；从会影响下一步的问题出发，说明公平比较、试验实际到达条件、数值定义、反证后的行动和受影响产物更新，保留来源抓取协议。
- 本版本效果：已更新目录说明与方法正文；是否改善真实模型的召回、后续行动和任务结果，待真实模型测评。

### 完整原文

````text
---
name: investigation
description: >-
  Use when results disagree, a change has no effect, or you need to clarify what
  a value means before continuing. Also use when you need a fair comparison to
  choose between explanations or methods. Decide what to check and use the result
  to guide the next step. This skill can also save a known web source with its
  retrieval record. Skip ordinary lookups and edits when the answer is clear and
  no saved source is needed. Use the assigned route for conversation history.
---

# Investigate a question that affects the work

Start with the question that would change your next action. It may concern a result you cannot explain, two sources that disagree, the meaning of a value, or which method to use. Choose a check that can help settle that question. You do not need to wait for an error, and you do not need to invent alternatives when the answer is already clear.

## Choose a check or comparison

Use existing logs, source data or measurements when they answer the question. For an unexplained result, consider plausible explanations, including the possibility that the suspected problem is absent. Choose an observation whose possible results would distinguish them.

When choosing between methods, keep the current usable work and compare relevant candidates in a suitably sized trial. Use the same task inputs, constraints and basis of comparison. Check the property that matters to the choice, then use the result to decide which method to continue with.

For a mismatch, choose a check that fits the observed pattern:

- If several changes leave the same mismatch, check a condition they all rely on before trying another similar variation.
- If the mismatch is systematic, compare processing stages or input conditions to find where the results first differ.
- If a search over smoothly varying parameters stalls, checking a boundary may reveal an untested convention or a discrete change in behavior.

These are alternative approaches, not required steps. Before interpreting an experiment, confirm that it actually reaches the condition you intended to test. If it does not, repair the trial before drawing a conclusion. If the result still cannot distinguish the explanations or methods and the decision matters, choose a different observation.

## Check what the values mean

Check definitions that could affect the comparison, such as the object, unit, denominator, time, coordinates or aggregation. A period total and a value at the end of that period may both be correct. Recalculating them does not establish which one the task requires.

Resolve the definition from the task, source or established contract before treating the difference as a code defect. Preserve the identifiers and coordinates needed through later transformations. If the evidence does not settle the definition, state the remaining ambiguity; do not choose a meaning just because it makes the numbers agree.

Before relying on a custom measurement, establish these definitions and the measurement method. Then inspect a sample or check an input with a known answer. Two implementations that share an uncertain definition do not resolve that uncertainty. An indirect measurement alone does not prove the suspected cause.

## Use the result to continue the work

When evidence identifies a defect within the task's scope, repair that part and preserve unaffected work. When a definition is resolved, apply it to the affected transformations, calculations and comparisons. If a comparison supports changing methods, retain the work that remains useful. Update the saved outputs that depend on the change, then read or run them to confirm that the change took effect.

When evidence contradicts or limits an explanation, withdraw or narrow it and change the next action accordingly. Keep observations separate from assumptions and conclusions. Save source locations and relevant versions with observations. Read enough surrounding context to assess contradictory evidence: a filtered sample or an unsuccessful search does not prove that something is absent.

Stop investigating when another check would not change the decision, and continue the task.

## Capture a known source

[scripts/fetch.py](scripts/fetch.py) shows how to save a source with its retrieval record. Use it directly, adapt it, or write a reader suited to the source. Direct use requires Python 3.11 or newer. Resolve its absolute path from this SKILL.md:

```sh
python3 /absolute/skill/scripts/fetch.py 'https://example.org/source' --output-dir /absolute/workspace/evidence --max-chars 8000
```

The helper saves the received body, request and final URLs, redirects, HTTP status, headers, UTC retrieval time and SHA-256 in a fresh directory. It marks failed or interrupted transfers as incomplete and keeps HTTP error bodies with their failure status. Check status, completeness and content before quoting the source. Retrieval time is not publication time.

`--extract auto` uses the declared HTML, JSON or text type. Use `none` to save bytes only, or supply the type or encoding when the metadata is known to be wrong. `--max-chars` limits the preview, `--max-extract-bytes` limits extraction memory, and `--max-body-bytes` defaults to 64 MiB. If the body exceeds that limit, the helper keeps the received prefix and reports failure. Raise the limit only when the task needs more.

HTML extraction removes scripts, styles and templates and normalizes whitespace. It does not show rendered content or content produced by JavaScript. JSON extraction validates the response without reformatting it. Use the raw response as the source of record.

The helper fetches known URLs; it cannot search, log in or run a browser. It sends no authorization, cookies or custom headers, does not inherit environment proxy credentials, and rejects URLs and redirects that contain credentials. Its timeout covers redirects, headers and body transfer, but operating-system DNS lookup may outlast a socket timeout. If the source needs unsupported access, use an appropriate authorized route or report the limitation.

Use `result_mode: direct` for exact excerpts. Read the saved content when the preview is not enough.
````
