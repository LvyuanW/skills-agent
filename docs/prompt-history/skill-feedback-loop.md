# feedback 跨回合反馈示例提示词版本时间线

当前源位置：`skills_agent/builtin_skills/feedback/references/feedback-loop.md`。本提示词于本次变更新增，没有更早的 Git 修订；后续沿本文追加版本，不覆盖原文。

## v1 · 2026-10-11 · 新增

来源：当前工作区（未提交）。与[主工作提示词 v4](agent-system.md) 一同启用，现有 tools 不变。动态资料使用占位符，不保存实际任务或会话数据。

- 发现的问题：即使存在获取脚本，模型仍可能把写入、检查和后续扩展合为一次无反馈的大步骤，或把保存成功误认为产物正确。
- 本版本目标：用分别执行并返回结果的示例说明保存、获取、实际观察和修改如何相互衔接，补充检查失败和长日志续读的下一步选择。
- 本版本效果：新增按需读取的 SVG 渐进示例与失败检查示例；每一处后续决策都依赖已返回的实际结果，静态截图与动态效果的边界得到说明；不规定固定回合数。实际执行粒度改善待使用观察。

### 完整提示词快照

````markdown
# Feedback across separate actions

These are distinct agent actions with returned results between them. They are not one large shell script. Replace the absolute helper path placeholders with paths resolved from the loaded skill catalog. Every shell action establishes its own variables.

## Save, acquire, inspect, and extend a visual artifact

The task is to make an SVG animation of a bicycle. The first useful result is a saved scene with two visible wheels; visible composition can then guide the next change.

First action: save that scene through the file-editing helper.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create scene.svg <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 260">
  <rect width="600" height="260" fill="#eef6fa"/>
  <g stroke="#18344b" stroke-width="8" fill="none">
    <circle cx="150" cy="170" r="70"/>
    <circle cx="450" cy="170" r="70"/>
  </g>
</svg>
SVG
```

Returned result: successful creation and a numbered excerpt establish that the initial scene was saved. They do not establish visible appearance.

Next action: acquire the saved text rather than judge a remembered draft.

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text scene.svg --start 1 --end 20
```

Returned result: inspect the actual viewBox, wheel positions and preserved SVG content. If a structural check is useful, invoke it as a separate check and inspect its actual status:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 -c 'import xml.etree.ElementTree as ET; ET.parse("scene.svg"); print("SVG XML parsed")'
```

Returned result: a zero exit status establishes valid XML. This does not establish the visible composition or animation.

Next action: obtain visual feedback using `shell result_mode=direct`.

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" render scene.svg --output .verify/scene-static.png --width 1000 --height 600 --wait-ms 0
```

Returned result: the feedback envelope states the source, viewport, browser status and image declaration. The runtime then supplies the actual image as native feedback. Inspect that image: are the wheels visible, proportionate and positioned as intended? If native acquisition fails, use its diagnostic; do not invent a visual judgment from the path. If composition needs correction, make that focused edit before adding motion.

Suppose the observed scene has appropriate wheel positions. The next useful change can add the frame:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" insert scene.svg --anchor '  </g>' --position before <<'SVG'
    <path d="M150 170 L265 80 L310 170 Z M265 80 L400 80 L450 170 M310 170 L400 80"/>
SVG
```

Returned result: inspect the exact match and edit status. Then acquire a fresh visual result:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" render scene.svg --output .verify/scene-frame.png --width 1000 --height 600 --wait-ms 0
```

The next decision depends on the actual new image. Continue with the rider and motion in useful increments. For an animated version, one still image shows one captured appearance; it cannot establish movement quality. A wait budget is not an exact animation timestamp. Choose additional state captures or a task-specific browser check only when needed to assess motion, and report the remaining scope accurately.

## A failed check changes the next action

For a task that changes a parser, invoke the relevant existing test through the feedback helper:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 -m unittest tests.test_parser
```

Returned result: if the command fails, the outer exit status and `observations.exit_code` retain that failure. Inspect stdout and stderr separately. A missing module is an execution setup problem; a reached assertion with a mismatching result is behavioral feedback. Locate the actual cause before choosing a correction. When a tail omits relevant context, read the returned absolute log path:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text /absolute/path/returned/in/observations/stderr_log --start 1 --end 80
```

A later call resumes a truncated long line using both returned coordinates:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text /absolute/path/returned/in/observations/stderr_log --start 12 --column 12001 --end 20
```

After making a supported correction, rerun the relevant check and inspect its new result. Keep full logs as evidence; a summary, file existence, or a previously passing command does not prove the current work satisfies the task.
````

## v2 · 2026-10-11 · 依据观察选择下一动作

来源：当前工作区（未提交）。现有 tools 和操作脚本接口不变。

- 发现的问题：旧参考将自行车 SVG 的车轮、车架、人物和动画开发顺序，以及某种 parser 测试流程固化进基础反馈技能。
- 本版本目标：改为已有任务结果的保存内容、执行行为和视觉属性三种可选反馈路线，每次选择由待判断问题与真实观察决定。
- 本版本效果：删除具体产物开发配方；保留可调用的获取示例、失败后下一步和停止条件，不要求每项任务执行全部路线。实际模型表现待使用观察。

### 完整提示词快照

````markdown
# Let the observed result determine the next action

Feedback serves a decision: whether a requirement is met, whether a suspected failure occurred, or which change should come next. Select the smallest useful observation that can resolve that question. These examples are alternative routes, not stages every task must follow. When a later choice depends on a returned result, acquire that result before choosing the later action; independent observations may be grouped.

The examples assume a result already produced by the current task. Replace paths, ranges, expected conditions, and executable commands with the ones relevant to that task. Resolve helper paths from the loaded skill directory. Each shell action establishes its own variables.

## Acquire the relevant saved data

If the question is whether an output was saved at the expected location, acquire its metadata:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" file result.txt
```

Returned observation: file type, size, modification time and hash describe the actual file. A missing path is evidence about acquisition or saving; an existing file is not evidence that its contents are correct. When the decision concerns content, read the relevant range instead:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text result.txt --start 1 --end 80
```

Decision after observation: compare the returned content with the task's requirement. If the range does not cover the relevant content, obtain that missing range. For truncation within a long line, resume using the returned `next_line` as `--start` and `next_column` as `--column`. If a required condition is visibly wrong, make a supported correction. If it is met, proceed with the remaining task or finish when the required scope is complete.

For literal text, file properties or JSON values that admit an explicit expected condition, [acceptance-checks.md](acceptance-checks.md) shows how to check that condition and retain the observed difference. Choose the expected result from the task or established contract, independently of the produced answer.

## Observe execution when the requirement concerns behavior

Use an existing relevant check or a focused probe that exercises the required behavior. For example, if the project already provides an applicable check script:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the entire command and arguments with the actual executable check. The helper is not limited to Python or test runners, and this example does not require creating a new check script.

Returned observation: the actual exit code, timeout status, separate stdout/stderr tails and full log paths. Determine what the execution reached. A missing executable or dependency is an acquisition gap; an observed mismatch after the intended behavior ran is feedback about that behavior. When the tail is insufficient, read the relevant saved log range:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text /absolute/path/returned/in/observations/stderr_log --start 1 --end 80
```

Decision after observation: obtain missing evidence, resolve the execution gap, or change the demonstrated faulty behavior. A successful command supports only the conditions it exercised. After a relevant change, acquire a fresh result for those affected conditions instead of relying on the previous result.

## Acquire pixels when the question is visual

If a task already produced an image, request that image directly:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" image output.png --label 'Current work result'
```

For a local HTML or SVG result, the installed browser can produce a screenshot:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" render preview.html --output .verify/preview.png --width 1200 --height 800 --wait-ms 0
```

Choose a viewport and wait budget relevant to the property under review. A zero wait requests capture without a virtual-time budget; a nonzero value does not promise an exact animation timestamp. These calls return one JSON envelope as the entire stdout. The runtime then supplies acquired images as native visual evidence.

Decision after observation: inspect the actual native image for the task's visible requirements. If acquisition fails, address or report that gap instead of inferring appearance from a filename, hash, or successful render command. If the visible result differs from the requirement, make a relevant correction and acquire the updated image when needed to judge that correction. One screenshot covers its captured state and viewport; motion, interaction and other states require their own relevant observations.

## Decide when there is enough feedback

Finish when the observed result supports the task's required conditions and authorized follow-through is complete. Acquire more feedback when a meaningful uncertainty remains, rather than repeating checks solely because a stage ended. Keep the actual artifacts and available result references usable for later work; create additional notes or checkpoints only when they serve the task. Report the result and any condition that could not be observed.
````


## v3 · 2026-10-17 · 组合脚本与视觉结果分离

来源：工作区未提交；`skills_agent/builtin_skills/feedback/references/feedback-loop.md`。

- 发现的问题：跨回合示例要求 helper 输出独占 stdout，容易与实际日志组合冲突。
- 本版本目标：使用结构化参数选择图片，渲染、日志与反馈读取分别明确。
- 本版本效果：技能与工具契约保持一致，脚本可打印普通日志和多个结果；技能格式与 JSON 示例校验通过，实际模型遵循待观察。

### 完整原文

````markdown
# Let the observed result determine the next action

Feedback serves a decision: whether a requirement is met, whether a suspected failure occurred, or which change should come next. Select the smallest useful observation that can resolve that question. These examples are alternative routes, not stages every task must follow. When a later choice depends on a returned result, acquire that result before choosing the later action; independent observations may be grouped.

The examples assume a result already produced by the current task. Replace paths, ranges, expected conditions, and executable commands with the ones relevant to that task. Resolve helper paths from the loaded skill directory. Each shell action establishes its own variables.

## Acquire the relevant saved data

If the question is whether an output was saved at the expected location, acquire its metadata:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" file result.txt
```

Returned observation: file type, size, modification time and hash describe the actual file. A missing path is evidence about acquisition or saving; an existing file is not evidence that its contents are correct. When the decision concerns content, read the relevant range instead:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text result.txt --start 1 --end 80
```

Decision after observation: compare the returned content with the task's requirement. If the range does not cover the relevant content, obtain that missing range. For truncation within a long line, resume using the returned `next_line` as `--start` and `next_column` as `--column`. If a required condition is visibly wrong, make a supported correction. If it is met, proceed with the remaining task or finish when the required scope is complete.

For literal text, file properties or JSON values that admit an explicit expected condition, [acceptance-checks.md](acceptance-checks.md) shows how to check that condition and retain the observed difference. Choose the expected result from the task or established contract, independently of the produced answer.

## Observe execution when the requirement concerns behavior

Use an existing relevant check or a focused probe that exercises the required behavior. For example, if the project already provides an applicable check script:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the entire command and arguments with the actual executable check. The helper is not limited to Python or test runners, and this example does not require creating a new check script.

Returned observation: the actual exit code, timeout status, separate stdout/stderr tails and full log paths. Determine what the execution reached. A missing executable or dependency is an acquisition gap; an observed mismatch after the intended behavior ran is feedback about that behavior. When the tail is insufficient, read the relevant saved log range:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text /absolute/path/returned/in/observations/stderr_log --start 1 --end 80
```

Decision after observation: obtain missing evidence, resolve the execution gap, or change the demonstrated faulty behavior. A successful command supports only the conditions it exercised. After a relevant change, acquire a fresh result for those affected conditions instead of relying on the previous result.

## Acquire pixels when the question is visual

Use the [images skill](../../images/SKILL.md) and declare the selected image in the shell tool call. If the task already produced a bounded image, no preparation command is needed:

```json
{
  "name": "Inspect current work result",
  "command": "true",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/output.png", "label": "Current work result"}]
}
```

For a local HTML or SVG result, the installed browser can produce a screenshot:

```json
{
  "name": "Render and inspect preview",
  "command": "python3 /absolute/path/from/skill-catalog/feedback/scripts/feedback.py render /absolute/workspace/preview.html --output /absolute/workspace/.verify/preview.png --width 500 --height 400 --wait-ms 0",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/.verify/preview.png", "label": "Preview"}]
}
```

Choose a viewport and wait budget relevant to the property under review. A zero wait requests capture without a virtual-time budget; a nonzero value does not promise an exact animation timestamp. The command may print acquisition metadata and ordinary logs; `images` selects which files become native visual evidence after the command finishes. Use absolute host paths or local snapshot URLs, at most five images per request and at most 1 MB per image. If only preparing files for later inspection, pass `images: []`. A background call registers future output paths and supplies the images with its completion result, not its launch receipt.

Decision after observation: inspect the actual native image for the task's visible requirements. If acquisition fails, address or report that gap instead of inferring appearance from a filename, hash, or successful render command. If the visible result differs from the requirement, make a relevant correction and acquire the updated image when needed to judge that correction. One screenshot covers its captured state and viewport; motion, interaction and other states require their own relevant observations.

## Decide when there is enough feedback

Finish when the observed result supports the task's required conditions and authorized follow-through is complete. Acquire more feedback when a meaningful uncertainty remains, rather than repeating checks solely because a stage ended. Keep the actual artifacts and available result references usable for later work; create additional notes or checkpoints only when they serve the task. Report the result and any condition that could not be observed.
````


## v4 · 2026-10-17 · 图片上限统一为10张

来源：工作区未提交；`skills_agent/builtin_skills/feedback/references/feedback-loop.md`。

- 发现的问题：原上限为每个请求5张图片，用户要求扩展为10张。
- 本版本目标：统一运行时、工具说明与技能的10图上限；每图1 MB及历史图片引用规则不变。
- 本版本效果：允许同一请求携带至多10张图片；超出时使用合图，实际模型表现待观察。

### 完整原文

````markdown
# Let the observed result determine the next action

Feedback serves a decision: whether a requirement is met, whether a suspected failure occurred, or which change should come next. Select the smallest useful observation that can resolve that question. These examples are alternative routes, not stages every task must follow. When a later choice depends on a returned result, acquire that result before choosing the later action; independent observations may be grouped.

The examples assume a result already produced by the current task. Replace paths, ranges, expected conditions, and executable commands with the ones relevant to that task. Resolve helper paths from the loaded skill directory. Each shell action establishes its own variables.

## Acquire the relevant saved data

If the question is whether an output was saved at the expected location, acquire its metadata:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" file result.txt
```

Returned observation: file type, size, modification time and hash describe the actual file. A missing path is evidence about acquisition or saving; an existing file is not evidence that its contents are correct. When the decision concerns content, read the relevant range instead:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text result.txt --start 1 --end 80
```

Decision after observation: compare the returned content with the task's requirement. If the range does not cover the relevant content, obtain that missing range. For truncation within a long line, resume using the returned `next_line` as `--start` and `next_column` as `--column`. If a required condition is visibly wrong, make a supported correction. If it is met, proceed with the remaining task or finish when the required scope is complete.

For literal text, file properties or JSON values that admit an explicit expected condition, [acceptance-checks.md](acceptance-checks.md) shows how to check that condition and retain the observed difference. Choose the expected result from the task or established contract, independently of the produced answer.

## Observe execution when the requirement concerns behavior

Use an existing relevant check or a focused probe that exercises the required behavior. For example, if the project already provides an applicable check script:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" run --log-dir .verify/logs -- python3 path/to/existing-check.py
```

Replace the entire command and arguments with the actual executable check. The helper is not limited to Python or test runners, and this example does not require creating a new check script.

Returned observation: the actual exit code, timeout status, separate stdout/stderr tails and full log paths. Determine what the execution reached. A missing executable or dependency is an acquisition gap; an observed mismatch after the intended behavior ran is feedback about that behavior. When the tail is insufficient, read the relevant saved log range:

```sh
feedback='/absolute/path/from/skill-catalog/feedback/scripts/feedback.py'
python3 "$feedback" text /absolute/path/returned/in/observations/stderr_log --start 1 --end 80
```

Decision after observation: obtain missing evidence, resolve the execution gap, or change the demonstrated faulty behavior. A successful command supports only the conditions it exercised. After a relevant change, acquire a fresh result for those affected conditions instead of relying on the previous result.

## Acquire pixels when the question is visual

Use the [images skill](../../images/SKILL.md) and declare the selected image in the shell tool call. If the task already produced a bounded image, no preparation command is needed:

```json
{
  "name": "Inspect current work result",
  "command": "true",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/output.png", "label": "Current work result"}]
}
```

For a local HTML or SVG result, the installed browser can produce a screenshot:

```json
{
  "name": "Render and inspect preview",
  "command": "python3 /absolute/path/from/skill-catalog/feedback/scripts/feedback.py render /absolute/workspace/preview.html --output /absolute/workspace/.verify/preview.png --width 500 --height 400 --wait-ms 0",
  "run_in_background": false,
  "images": [{"path": "/absolute/workspace/.verify/preview.png", "label": "Preview"}]
}
```

Choose a viewport and wait budget relevant to the property under review. A zero wait requests capture without a virtual-time budget; a nonzero value does not promise an exact animation timestamp. The command may print acquisition metadata and ordinary logs; `images` selects which files become native visual evidence after the command finishes. Use absolute host paths or local snapshot URLs, at most ten images per request and at most 1 MB per image. If only preparing files for later inspection, pass `images: []`. A background call registers future output paths and supplies the images with its completion result, not its launch receipt.

Decision after observation: inspect the actual native image for the task's visible requirements. If acquisition fails, address or report that gap instead of inferring appearance from a filename, hash, or successful render command. If the visible result differs from the requirement, make a relevant correction and acquire the updated image when needed to judge that correction. One screenshot covers its captured state and viewport; motion, interaction and other states require their own relevant observations.

## Decide when there is enough feedback

Finish when the observed result supports the task's required conditions and authorized follow-through is complete. Acquire more feedback when a meaningful uncertainty remains, rather than repeating checks solely because a stage ended. Keep the actual artifacts and available result references usable for later work; create additional notes or checkpoints only when they serve the task. Report the result and any condition that could not be observed.
````
