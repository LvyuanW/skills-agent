# code-repair 技能提示词版本史

本文按 Git `--all` 可达提交中的实际说明文本变化整理历史；H1、H2…按提交记录的时间升序排列。未改变说明文本的实现调整不单列版本。v1 是 2026-10-11 建档时的当前工作区基线。

“发现的问题”按相邻版本的差异归纳其要处理的缺口；“本版本效果”描述规则变化或预期，不表示已测得实际改善。

当前源文件：`skills_agent/builtin_skills/code-repair/SKILL.md`。

## H1

日期：2026-10-10 09:40:31+08:00\
提交：`f91e56640f9edd87db2cbb85842c9e3117bd2515`\
源位置：`skills_agent/builtin_skills/code-repair/SKILL.md`

- 发现的问题：此前缺少针对缺陷修复全过程的独立工作规范。
- 本版本目标：从用户报告的失败出发，串联定位、修复和完成判断。
- 本版本效果：要求跟踪受影响调用与状态流转、聚焦回归问题，并保持命令退出状态可见。

### 完整提示词

````markdown
---
name: code-repair
description: Investigate and repair code defects with reproductions and verification of affected data flows.
---

Start from the user's observed failure and define an observable acceptance condition. Separate established causes from hypotheses. Trace affected inputs, identifiers, errors and outputs through callers, transformations, persistence and consumers. Include later reads or repeated operations when they can change the outcome; changing a value at its origin does not establish that its eventual consumer receives or uses it correctly.

Build a focused regression that reaches the actual failure. Check its prerequisites: the input must exercise the claimed branch, required setup must succeed, and the assertion must inspect the consumer-visible result. Test the relevant counterexample as well as the successful path. Exercise the claimed transition: for example, a persistence check should use a new reader opening the saved data rather than reuse an in-memory object. Reuse the same regression on the old and changed behavior where practical.

Keep the affected internal path real; mock external boundaries such as remote APIs. A canned success response does not validate the internal operation it replaces or the actual behavior of an external service. For a retained reference, check resolution after the relevant transformation, not just its presence before it. A passing test proves its exercised path and assertions; the number of passing tests does not establish coverage.

After the fix, review the changed path and run the checks needed for its acceptance condition and affected neighbors. Consider an independent review when a separate investigation of cross-component behavior or counterexamples would add useful evidence. Supply the reviewer with the task, constraints and relevant materials; do not require a subagent for every edit. Complete the tests and usage instructions needed for the authorized repair without adding unrelated mechanisms. Report the result, supporting checks and any remaining uncertainty in proportion to the evidence.

Read [scripts/run_check.py](scripts/run_check.py) as a reference for preserving a command's exit status while limiting displayed output. Learn from it, adapt it, reuse it or write your own script; no fixed script entry point is required. It saves complete combined stdout and stderr to the specified log and displays only the tail, exit code and log path:

```sh
python "<skill-directory>/scripts/run_check.py" /tmp/regression.log python -m unittest tests.test_example
```

Use the actual exit code and assertions to judge execution. A success-looking final line or a successful formatting step cannot override an earlier command failure. Read targeted log ranges when the displayed tail is insufficient.
````

## H2

日期：2026-10-10 11:06:10+08:00\
提交：`494a85dfaad408a83211f37742900bb712a910eb`\
源位置：`skills_agent/builtin_skills/code-repair/SKILL.md`

- 发现的问题：上一版对验收条件独立性、最小修复和改写脚本时的行为保留说明不够集中。
- 本版本目标：将验收条件与实现分离，补足修复范围和脚本适配规则。
- 本版本效果：明确优先最小改动、保留既有契约，并同时提供 Python 与 shell 参考脚本的使用说明。

### 完整提示词

````markdown
---
name: code-repair
description: Repair reported code defects and verify behavior through affected callers, saved state and consumers.
---

Derive observable acceptance conditions and properties that must remain true from the user's reported failure and existing contracts, before choosing a fix. Keep those conditions independent of the proposed implementation. Separate established causes from hypotheses; a possible improvement is not evidence of a defect.

Trace the affected behavior through callers, transformations, persistence and consumers. Check the outcome where it is used, including later reads or repeated operations when relevant. Use the real internal path; mock external boundaries where needed and state what those substitutes cannot establish.

Make the regression distinguish the defect from the intended behavior. Confirm that setup succeeds, the input reaches the failing condition and the assertion observes its consequence. Where the original behavior is available, demonstrate failure for the intended reason before the fix and success afterward. Otherwise state the reproduction gap. A check that already passes on the faulty behavior may protect compatibility, but does not demonstrate that repair.

Choose the smallest change that satisfies the conditions while preserving existing contracts. When adapting a method, preserve the properties it guarantees and check them rather than imitating its syntax or language. Keep failures observable and changes limited in consequence. Use focused checks to resolve uncertainty before broadening the change or the test run.

Before claiming completion, compare the actual result and evidence with the original acceptance conditions. Ask whether those checks could also pass with the defect present. Inspect the relevant transitions: a saved-state claim needs a new reader, and a retained reference needs resolution after transformation. Consider independent review when it can resolve a consequential uncertainty; provide the original task, constraints, artifacts and evidence. Complete authorized follow-through, report unverified scope and avoid repeating checks without a reason. Passing-test counts alone do not establish coverage.

For command-based checks, read [scripts/run_check.py](scripts/run_check.py) or [scripts/run_check.sh](scripts/run_check.sh) as a reference. Both retain complete combined output, display a bounded tail and return the command's exit status to the caller. Their arguments are a log path followed by the command and its arguments. Reuse, adapt or write your own script; no fixed entry point or language is required. If changing how execution or output is handled, check both a successful and a failing command at the caller boundary. Printed status and process status are distinct; inspect relevant log ranges when the tail is insufficient.
````

## v1 — 当前工作区基线

日期：2026-10-11\
提交：`工作区基线；与 H2 同文（源文件无未提交改动）`\
源位置：`skills_agent/builtin_skills/code-repair/SKILL.md`

- 发现的问题：本次需要为现有提示词建立独立版本记录。
- 本版本目标：将当前生效文本固定为 v1，并保留 H2 及之前的历史。
- 本版本效果：与 H2 完全相同；本次不改变技能行为。

### 完整提示词

````markdown
---
name: code-repair
description: Repair reported code defects and verify behavior through affected callers, saved state and consumers.
---

Derive observable acceptance conditions and properties that must remain true from the user's reported failure and existing contracts, before choosing a fix. Keep those conditions independent of the proposed implementation. Separate established causes from hypotheses; a possible improvement is not evidence of a defect.

Trace the affected behavior through callers, transformations, persistence and consumers. Check the outcome where it is used, including later reads or repeated operations when relevant. Use the real internal path; mock external boundaries where needed and state what those substitutes cannot establish.

Make the regression distinguish the defect from the intended behavior. Confirm that setup succeeds, the input reaches the failing condition and the assertion observes its consequence. Where the original behavior is available, demonstrate failure for the intended reason before the fix and success afterward. Otherwise state the reproduction gap. A check that already passes on the faulty behavior may protect compatibility, but does not demonstrate that repair.

Choose the smallest change that satisfies the conditions while preserving existing contracts. When adapting a method, preserve the properties it guarantees and check them rather than imitating its syntax or language. Keep failures observable and changes limited in consequence. Use focused checks to resolve uncertainty before broadening the change or the test run.

Before claiming completion, compare the actual result and evidence with the original acceptance conditions. Ask whether those checks could also pass with the defect present. Inspect the relevant transitions: a saved-state claim needs a new reader, and a retained reference needs resolution after transformation. Consider independent review when it can resolve a consequential uncertainty; provide the original task, constraints, artifacts and evidence. Complete authorized follow-through, report unverified scope and avoid repeating checks without a reason. Passing-test counts alone do not establish coverage.

For command-based checks, read [scripts/run_check.py](scripts/run_check.py) or [scripts/run_check.sh](scripts/run_check.sh) as a reference. Both retain complete combined output, display a bounded tail and return the command's exit status to the caller. Their arguments are a log path followed by the command and its arguments. Reuse, adapt or write your own script; no fixed entry point or language is required. If changing how execution or output is handled, check both a successful and a failing command at the caller boundary. Printed status and process status are distinct; inspect relevant log ranges when the tail is insufficient.
````

## v2 · 2026-10-11 · 按故障相关性诊断和验证

来源：当前工作区（未提交）。现有 tools 和操作脚本接口不变。

- 发现的问题：原文将保存状态、引用转换及完整回归路径写得过于普遍，可能使小修复也套用某类故障的全流程。
- 本版本目标：保留通用代码诊断和修复能力，按实际故障、契约和后果选择调查边界及必要检查。
- 本版本效果：改为选择现有检查、必要探针或回归；状态转换按相关性观察，已有 runner 可直接运行；保留失败传播和结果可观测性。实际模型的检查粒度待使用观察。

### 完整提示词快照

````markdown
---
name: code-repair
description: Diagnose and repair code defects using observed behavior, relevant contracts and focused executable checks.
---

Derive observable acceptance conditions and properties that must remain true from the user's reported failure and existing contracts, before choosing a fix. Keep those conditions independent of the proposed implementation. Separate established causes from hypotheses; a possible improvement is not evidence of a defect.

Locate the part of the execution path that can explain the observed failure. Follow inputs and outputs across relevant boundaries until the cause is supported; expand the investigation when the result indicates another dependency. Check the outcome where the requirement is observable. Use real affected code, substituting external dependencies only when needed and stating what those substitutes cannot establish.

Select an existing relevant check, a focused executable probe, or a new regression when one is needed to resolve the uncertainty or protect the repaired behavior. Confirm that setup succeeds, the input reaches the relevant condition and the observation distinguishes the defect from the intended behavior. Compare the original and changed behavior when practical; otherwise state the reproduction gap. Scale the checks to the change and its consequences rather than requiring a new test suite for every edit.

Choose the smallest change that satisfies the conditions while preserving existing contracts. When adapting a method, preserve the properties it guarantees and check them rather than imitating its syntax or language. Keep failures observable and changes limited in consequence. Use focused checks to resolve uncertainty before broadening the change or the test run.

After a useful change, obtain feedback from the affected behavior before choosing further changes. Exercise lifecycle transitions only when they are part of the requirement or suspected failure; choose their boundaries from the actual code and contract. Before claiming completion, compare the observed result with the original acceptance conditions and ask whether the checks could also pass with the defect present. Consider independent review when it can resolve a consequential uncertainty; provide the task, constraints, artifacts and evidence. Complete authorized follow-through and report unverified scope. Passing-test counts alone do not establish coverage.

Use the feedback skill's `run` operation when you need bounded command output with separate full logs and timeout handling. For simple combined logging, resolve [scripts/run_check.py](scripts/run_check.py) or [scripts/run_check.sh](scripts/run_check.sh) against this skill's directory and execute it directly through `shell`. Both take an absolute log path followed by the command and its arguments, retain complete combined output, display a bounded tail and return the command's exit status. Read or adapt their source when customization is needed. Printed status and process status are distinct; inspect relevant log ranges when the tail is insufficient. When repairing command execution or error propagation, include failure behavior in the relevant checks.
````


## v3 · 2026-10-13 · 聚焦可复现缺陷与有区分力的验证

来源：当前工作区（未提交）。与[主工作提示词 v7](agent-system.md) 的按需技能选择规则同步；本轮仅调整技能入口，不修改脚本、工具接口或运行时协议。

- 发现的问题：入口重复通用工作规范，脚本直接调用说明弱化了根据缺陷自行编写探针的选择；run_check 的时限边界需要明确。
- 本版本目标：从原始需求确定预期，以能区分原始与修复行为的探针定位并验证最小改动，清楚区分日志参考脚本与带时限的反馈执行。
- 本版本效果：保留真实边界、独立预期值和复现缺口要求，明确 run_check 不自动设置超时；可复用、调整或自行编写检查。本轮没有修改脚本，真实模型的修复效果待观察。

### 完整提示词快照

````markdown
---
name: code-repair
description: Diagnose a reported code failure and verify a minimal repair with a reproducer or focused check that distinguishes the defect from the intended behavior.
---

Derive the expected behavior from the reported failure and existing contract, independently of the proposed fix. Follow the relevant input-to-output path until observations support a cause. A possible improvement is not evidence of a defect.

Choose an existing check, a focused probe or a regression that reaches the failure and would distinguish the original and repaired behavior. A missing dependency or failed setup does not reproduce the suspected defect. Exercise the real affected boundary; when substituting an external dependency, state what the substitute cannot establish.

Make the smallest change that satisfies the condition while preserving existing contracts. After changing it, inspect the affected behavior and ask whether the check could still pass with the defect present. Broaden checks only for relevant remaining uncertainty. Keep expected values independent of the implementation; report any reproduction or verification gap.

For executable checks, adapt or reuse [scripts/run_check.py](scripts/run_check.py) or [scripts/run_check.sh](scripts/run_check.sh), or write the task's own probe. Both references take a log path followed by a command and its arguments, keep complete combined output, show a bounded tail and preserve the command's exit status. They do not add an execution timeout. When that is needed, the feedback skill's `run` operation provides a time limit and separate full stdout/stderr logs. Inspect the relevant log range when a tail cannot distinguish setup failure from the behavior under test.
````
