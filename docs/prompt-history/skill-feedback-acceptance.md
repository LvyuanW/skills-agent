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
