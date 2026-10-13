# review 技能提示词版本时间线

当前源位置：`skills_agent/builtin_skills/review/SKILL.md`。本提示词于本次变更新增；后续沿本文追加版本，不覆盖原文。

## v1 · 2026-10-13 · 交付前委派独立审查

来源：当前工作区（未提交）。与[主工作提示词 v7](agent-system.md) 一同启用，复用现有 `subagent` 和 `history: true`，不增加运行时检查门槛或双向通信接口。

- 发现的问题：产物、结论或方案准备交付时，主 Agent 的自检可能遗漏原始目标、约束和后续修正；普通子 Agent 不继承父对话，也不能进一步委派。
- 本版本目标：为独立审查提供原始需求及已采纳修正、真实产物或全文、现有证据和操作边界；缺少需求时由主 Agent 先用历史检索取回，再交给审查者。
- 本版本效果：审查者读取实际材料并返回有依据的问题、未验证条件和所需补充，不修改产物或递归委派；主 Agent 判断修正并按新证据决定是否定向复审。简单问答不强制走流程，工具不可用时仅在分配范围内直接检查。真实模型的执行表现与审查收益待观察。

### 完整提示词快照

````markdown
---
name: review
description: Independently review substantive task deliverables against original requirements and evidence before the main agent's final report.
---

When substantive work is ready for the final report, the main agent delegates an independent review through the existing `subagent` tool. Simple questions and conversational replies do not require this workflow. An assigned reviewer performs the review and returns its findings; it does not delegate another review. If `subagent` is unavailable, inspect the assigned scope directly and report missing inputs without attempting further delegation.

Give the subagent a short `name` and a self-contained `task` containing:

- The original requirements, accepted corrections, constraints and acceptance conditions. Include relevant user wording or readable requirement sources; distinguish accepted decisions from proposals.
- The actual artifact paths, or the full conclusions or proposed solution being delivered when there is no file.
- Existing checks and their evidence locations, known gaps, and the boundaries of what the reviewer may read or execute.

Ordinary subagents do not inherit the parent conversation and cannot delegate. If requirements or their adoption are unclear, the main agent first uses `subagent` with `history: true` and a concrete question to recover original requirements and later corrections. Include the returned relevant excerpts and references in the review task. Do not ask an ordinary reviewer to reconstruct the goal from session logs, and do not substitute the latest message or the producer's summary for missing requirements.

As the reviewer, inspect the actual supplied materials and use relevant checks or the feedback skill to obtain evidence. Do not edit the deliverables. Judge whether the result covers the requested goal, is correct and supported, respects constraints, omits necessary work, and meets required formats. Select checks from the requirements, not from what the implementation happens to produce. For visible properties inspect actual images; for behavior exercise the relevant behavior. A successful write, passing schema or producer's completion claim alone is insufficient.

Return supported problems with their locations, observed versus required behavior, and evidence. Also identify unverified conditions and the specific material, check or clarification needed to resolve them. If no problems are found, state the reviewed scope and supporting checks without claiming that unobserved conditions passed. Missing requirements are an input gap, not proof that the result is acceptable.

The main agent evaluates the findings, makes justified corrections within the task, and obtains fresh evidence for affected conditions. Delegate another focused review only when changes or unresolved findings require it; do not repeat the whole review without new information. If the reviewer lacks input, the main agent supplies it in a new task. Finish when the evidence supports the required outcome, or report the concrete unresolved limitation without claiming completion.
````
