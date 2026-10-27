# 要求与证据记录技能

位置：skills_agent/builtin_skills/acceptance-criteria/SKILL.md。

## v1 · 2026-10-27 · 本轮英文改写前的现有正文

来源：本轮工作区未提交；skills_agent/builtin_skills/acceptance-criteria/SKILL.md。

- 发现的问题：现有技能尚未单独归档。
- 本版本目标：保留修改前的完整正文。
- 本版本效果：历史快照；当前版本见后续记录。

### 完整原文

````text
---
name: acceptance-criteria
description: >-
  Use when a persistent requirements-and-evidence record would help
  coordination or later continuation, or when the user requests one.
  Reuse existing task records. Skip when the requirements remain clear
  without a separate document.
---

Preserve original requirements, accepted corrections and source references in an existing task record, or create a concise document when useful. Keep necessary implications, assumptions, open questions and optional improvements distinguishable. Completion conditions should make the requirements observable, not expand the requested outcome.

Record relevant checks and evidence without turning examples into a universal checklist. Update the record when requirements change or an interpretation is corrected, retaining the reason and source. Do not weaken a valid requirement to fit the output, or retain an unsupported criterion merely because it was written down.

Delegated work reuses the parent's record. Recover missing original requirements through the existing history-retrieval route rather than presenting a summary as an original quote. Respect file-creation restrictions; a separate document is not a prerequisite to authorized work.
````

## v2 · 2026-10-27 · 直接英文表达与明确使用边界

来源：本轮工作区未提交；skills_agent/builtin_skills/acceptance-criteria/SKILL.md。

- 发现的问题：部分指令较抽象或过长，使用与跳过条件需要更容易区分。
- 本版本目标：使用清楚、自然、直接的英文说明动作与边界；保留实质约束、原脚本接口和代码示例。六个实验 worktree 使用同一正文。
- 本版本效果：技能格式、本地引用和原有代码示例核对通过；已完成技能路由场景审阅。未运行新一轮真实模型测评，行为与分数效果待验证。

### 完整原文

````text
---
name: acceptance-criteria
description: >-
  Keep requirements and supporting evidence in a task record when the user asks
  for one, or when it would help delegation or later continuation. Reuse existing
  records. Skip when the requirements are already clear and a separate record
  would not help the work.
---

# Keep requirements and evidence available

Use this skill when a shared or lasting record will help people or agents continue the same task. It records the requirements already available; it does not discover hidden evaluation rules or add new goals.

Save the original requirements, accepted corrections and source references in an existing task record. Create a concise document only when needed and allowed. Keep required work, necessary implications, assumptions, open questions and optional improvements distinct. Describe how a requirement can be observed without expanding what the user asked for.

Add relevant checks and their evidence. Examples are not a checklist for every task. When a requirement changes or an interpretation is corrected, update the record and keep the reason and source. Do not weaken a valid requirement to fit the current output. Remove a condition that has no basis in the task, even if it was recorded earlier.

Delegated work should reuse the parent's record. Recover missing original requirements through the assigned history-retrieval route; do not present a summary as an original quote. Respect restrictions on creating files. A separate requirements document is not a prerequisite to work that is already authorized.
````
