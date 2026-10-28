# feedback 验收检查示例提示词版本时间线

当前源文件：`skills_agent/builtin_skills/feedback/references/acceptance-checks.md`。本参考指令于本次变更新增，没有更早的 Git 修订。

## v1 · 2026-10-11 · 通用验收边界与失败决策

来源：当前工作区（未提交）。现有 tools 不变。

- 发现的问题：文件存在、文本匹配或命令退出成功只能支撑有限结论，模型可能将这些代理检查当作完整行为验证。
- 本版本目标：用通用数据与行为示例说明条件如何来自需求、实际数据如何检查，以及检查失败后如何区分获取、设置与行为问题。
- 本版本效果：提供分别返回结果的文件、JSON、文本和命令检查示例，明确条件独立于实现、保存后从新读取者验证、不强制建立验收文件。实际模型使用效果待观察。

### 完整提示词快照

````markdown
# Check the requirement at its observable boundary

Select the condition that would distinguish an acceptable result from the reported failure. Obtain the actual saved artifact or execution result, then check it. A check covers only its stated condition; it does not certify the whole task. Simple work needs a relevant check, not a new test suite or a mandatory acceptance file.

Resolve each helper path from the loaded skill directory. These are separate shell actions with returned results between them; each action establishes its own variables.

## Saved data

If the contract requires a nonempty output file, observe that condition directly:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" file result.json --min-bytes 1
```

Successful acquisition establishes that a regular nonempty file exists. It does not establish valid JSON or correct values. If the agreed contract requires a specific nested result, inspect that value:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" json result.json --pointer /summary/processed --equals-json '12'
```

The expected 12 must come from the actual input or the contract, not from copying the program's answer. A failure reports the observed value or missing path. Compare the difference and inspect its cause before modifying the implementation. RFC 6901 pointers use `~1` for `/`, `~0` for `~`, and zero-based array indexes. JSON strings in `--equals-json` require JSON quotes, such as `'"ready"'`.

Literal content checks can inspect a required text fragment and an obsolete one:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" text result.txt --contains 'required literal text' --absent 'obsolete literal text'
```

Choose the actual requirement's literals. Returned source positions support later reading; a match does not prove semantic correctness or working behavior.

## Behavior and execution failures

When the requirement concerns behavior, exercise the behavior at its consumer boundary. Use an existing relevant test or a focused executable probe rather than checking whether implementation text contains a desired function name:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 -m unittest tests.test_affected_behavior
```

Inspect the returned status and output. First confirm that the check reached the behavior: a missing module, unavailable service or invalid test setup is an execution gap. An assertion on the actual outcome is behavioral evidence. Read the full saved logs when the bounded tail leaves this distinction uncertain.

After a failure, choose the smallest useful next action based on the diagnostic: acquire missing evidence, repair setup, or change the observed faulty behavior. Run the relevant check again after that change. Keep requirements independent of the implementation; weaken a condition only when the user or an established contract changes it.

For a stateful result, verify through a new reader or actual consumer after the write. For visual or interactive requirements, inspect the native visual feedback or execute the relevant interaction; data checks alone do not establish those properties. Before claiming completion, compare covered conditions with the original task and identify any remaining unobserved scope.
````

## v2 · 2026-10-11 · 从任务契约选择检查边界

来源：当前工作区（未提交）。现有 tools 和操作脚本接口不变。

- 发现的问题：参考以已报告故障作为默认起点，绑定特定测试模块，并对状态检查给出过于固定的读取路线。
- 本版本目标：支持一般任务结果的条件检查，由实际契约确定行为或状态边界，并允许依据原始要求纠正错误的检查。
- 本版本效果：执行示例不再绑定框架或模块；保留条件与产物独立、真实失败状态和观察范围，避免以当前输出倒推期望值。实际模型验收判断待使用观察。

### 完整提示词快照

````markdown
# Check the requirement at its observable boundary

Select a condition derived from the task or established contract that distinguishes an acceptable result from an unacceptable one. Obtain the actual saved artifact or execution result, then check it. A check covers only its stated condition; it does not certify the whole task. Simple work needs a relevant check, not a new test suite or a mandatory acceptance file.

Resolve each helper path from the loaded skill directory. These are separate shell actions with returned results between them; each action establishes its own variables.

## Saved data

If the contract requires a nonempty output file, observe that condition directly:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" file result.json --min-bytes 1
```

Successful acquisition establishes that a regular nonempty file exists. It does not establish valid JSON or correct values. If the agreed contract requires a specific nested result, inspect that value:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" json result.json --pointer /summary/processed --equals-json '12'
```

The expected 12 must come from the actual input or the contract, not from copying the program's answer. A failure reports the observed value or missing path. Compare the difference and inspect its cause before modifying the implementation. RFC 6901 pointers use `~1` for `/`, `~0` for `~`, and zero-based array indexes. JSON strings in `--equals-json` require JSON quotes, such as `'"ready"'`.

Literal content checks can inspect a required text fragment and an obsolete one:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" text result.txt --contains 'required literal text' --absent 'obsolete literal text'
```

Choose the actual requirement's literals. Returned source positions support later reading; a match does not prove semantic correctness or working behavior.

## Behavior and execution failures

When the requirement concerns behavior, exercise the behavior at its consumer boundary. Use an existing relevant test or a focused executable probe rather than checking whether implementation text contains a desired function name:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the command and arguments with the project's relevant executable check or a focused probe of the required behavior; this does not prescribe a language, test framework, or new script.

Inspect the returned status and output. First confirm that the check reached the behavior: a missing module, unavailable service or invalid test setup is an execution gap. An assertion on the actual outcome is behavioral evidence. Read the full saved logs when the bounded tail leaves this distinction uncertain.

After a failure, choose the smallest useful next action based on the diagnostic: acquire missing evidence, repair setup, or change the observed faulty behavior. Run the relevant check again after that change. Keep the requirement independent of the implementation. If evidence shows that the check or its interpretation is wrong, correct it against the original requirement and explain the mismatch; do not change an expected value merely to accept the current output.

When the requirement involves a later read or state transition, observe that actual transition at its relevant boundary. For visual or interactive requirements, inspect the native visual feedback or execute the relevant interaction; data checks alone do not establish those properties. Before claiming completion, compare covered conditions with the original task and identify any remaining unobserved scope.
````

## v3 · 2026-10-27 · 直接英文表达与明确使用边界

来源：本轮工作区未提交；skills_agent/builtin_skills/feedback/references/acceptance-checks.md。

- 发现的问题：部分指令较抽象或过长，使用与跳过条件需要更容易区分。
- 本版本目标：使用清楚、自然、直接的英文说明动作与边界；保留实质约束、原脚本接口和代码示例。六个实验 worktree 使用同一正文。
- 本版本效果：技能格式、本地引用和原有代码示例核对通过；已完成技能路由场景审阅。未运行新一轮真实模型测评，行为与分数效果待验证。

### 完整原文

````text
# Check the result where the requirement applies

Use this reference when a known task requirement can be checked against a saved file or an execution result. Choose the expected condition from the task or established contract, then inspect the actual result. A check supports only the condition it covers. It does not prove that the whole task is complete. Simple work does not need a new test suite or a separate requirements file.

Resolve helper paths from the loaded skill directory. The examples below are separate shell calls. Read a returned result before deciding the next action, and set variables in each call that uses them.

## Saved data

If the task requires a nonempty output file, check that condition directly:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" file result.json --min-bytes 1
```

A successful check proves that a regular, nonempty file exists. It does not prove that the JSON or its values are correct. If the task requires a particular nested value, check it:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" json result.json --pointer /summary/processed --equals-json '12'
```

The expected value, 12 in this example, must come from the input or the contract. Do not copy it from the program's answer. A failure reports the observed value or missing path. Inspect the difference and its cause before changing the implementation. RFC 6901 pointers use `~1` for `/`, `~0` for `~`, and zero-based array indexes. JSON strings in `--equals-json` need JSON quotes, such as `'"ready"'`.

For an exact-text requirement, check that the required text is present and obsolete text is absent:

```sh
check='/absolute/path/from/skill-catalog/feedback/scripts/check.py'
python3 "$check" text result.txt --contains 'required literal text' --absent 'obsolete literal text'
```

Replace these strings with the task's required text. Returned positions help you read the source later. A text match does not prove that the meaning or behavior is correct.

## Behavior and execution failures

When the requirement concerns behavior, run it through the interface that actually uses it. Use an existing relevant test or a focused executable check. Finding a function name in the source does not show that the behavior works:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the command and arguments with a check suited to the required behavior. This example does not require Python, a particular test framework or a new script.

Inspect the returned status and output. First confirm that the check reached the intended behavior. A missing module, unavailable service or invalid test setup means it did not. An assertion about the actual outcome provides evidence about the behavior. Read the full saved logs if the tail does not show which happened.

After a failure, use the result to choose a focused next action: collect missing evidence, fix the setup or correct the demonstrated defect. Run the relevant check again after the change. Keep the original requirement as the basis for the expected result. If the check or its interpretation is wrong, correct it against that requirement and explain the mismatch. Do not change an expected value just to accept the current output.

If the requirement depends on a later read or a change of state, observe that part of the actual workflow. For appearance, inspect the actual image; for interaction, perform the relevant action. Data checks alone do not establish either. Before claiming completion, compare what was checked with the original task and identify any required conditions that remain unobserved.
````

## v4 · 2026-10-28 · 同步 check-results 检查示例路径

来源：六个实验 worktree 的共享修改；提交标识见 [本轮提交记录](README.md#2026-10-28-召回条件与名称更新)。

当前源文件：`skills_agent/builtin_skills/check-results/references/acceptance-checks.md`。

- 发现的问题：结果检查技能目录改名后，文件、JSON、文本和执行检查示例的 helper 路径仍会指向旧 feedback 目录。
- 本版本目标：将示例路径更新为 check-results/scripts，保留从可见任务或既有约定确定期望、检查实际行为和限制检查结论范围的说明。
- 本版本效果：完整参考快照已同步到现行目录，旧历史文件名与既有版本原文保留；真实模型的使用和行为效果待测评。

### 完整原文

````text
# Check the result where the requirement applies

Use this reference when a known task requirement can be checked against a saved file or an execution result. Choose the expected condition from the task or established contract, then inspect the actual result. A check supports only the condition it covers. It does not prove that the whole task is complete. Simple work does not need a new test suite or a separate requirements file.

Resolve helper paths from the loaded skill directory. The examples below are separate shell calls. Read a returned result before deciding the next action, and set variables in each call that uses them.

## Saved data

If the task requires a nonempty output file, check that condition directly:

```sh
check='/absolute/path/from/skill-catalog/check-results/scripts/check.py'
python3 "$check" file result.json --min-bytes 1
```

A successful check proves that a regular, nonempty file exists. It does not prove that the JSON or its values are correct. If the task requires a particular nested value, check it:

```sh
check='/absolute/path/from/skill-catalog/check-results/scripts/check.py'
python3 "$check" json result.json --pointer /summary/processed --equals-json '12'
```

The expected value, 12 in this example, must come from the input or the contract. Do not copy it from the program's answer. A failure reports the observed value or missing path. Inspect the difference and its cause before changing the implementation. RFC 6901 pointers use `~1` for `/`, `~0` for `~`, and zero-based array indexes. JSON strings in `--equals-json` need JSON quotes, such as `'"ready"'`.

For an exact-text requirement, check that the required text is present and obsolete text is absent:

```sh
check='/absolute/path/from/skill-catalog/check-results/scripts/check.py'
python3 "$check" text result.txt --contains 'required literal text' --absent 'obsolete literal text'
```

Replace these strings with the task's required text. Returned positions help you read the source later. A text match does not prove that the meaning or behavior is correct.

## Behavior and execution failures

When the requirement concerns behavior, run it through the interface that actually uses it. Use an existing relevant test or a focused executable check. Finding a function name in the source does not show that the behavior works:

```sh
feedback='/absolute/path/from/skill-catalog/check-results/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the command and arguments with a check suited to the required behavior. This example does not require Python, a particular test framework or a new script.

Inspect the returned status and output. First confirm that the check reached the intended behavior. A missing module, unavailable service or invalid test setup means it did not. An assertion about the actual outcome provides evidence about the behavior. Read the full saved logs if the tail does not show which happened.

After a failure, use the result to choose a focused next action: collect missing evidence, fix the setup or correct the demonstrated defect. Run the relevant check again after the change. Keep the original requirement as the basis for the expected result. If the check or its interpretation is wrong, correct it against that requirement and explain the mismatch. Do not change an expected value just to accept the current output.

If the requirement depends on a later read or a change of state, observe that part of the actual workflow. For appearance, inspect the actual image; for interaction, perform the relevant action. Data checks alone do not establish either. Before claiming completion, compare what was checked with the original task and identify any required conditions that remain unobserved.
````
