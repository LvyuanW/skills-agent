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
