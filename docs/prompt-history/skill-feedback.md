# feedback 技能提示词版本时间线

当前源位置：`skills_agent/builtin_skills/feedback/SKILL.md`。本提示词于本次变更新增，没有更早的 Git 修订；后续沿本文追加版本，不覆盖原文。

## v1 · 2026-10-11 · 新增

来源：当前工作区（未提交）。与[主工作提示词 v4](agent-system.md) 一同启用，现有 tools 不变。动态资料使用占位符，不保存实际任务或会话数据。

- 发现的问题：模型需要根据实际工作结果继续推进，但宏观工作规范与文件编辑脚本不足以支撑获取文件、文本和视觉反馈。
- 本版本目标：通过现有 shell 提供文件元数据、范围文本、真实命令日志、已有图片和 HTML／SVG 截图的获取方法，并说明证据边界与下一步决策方式。
- 本版本效果：新增可直接运行的独立脚本；文本可按行列续读，命令保留失败状态和完整日志，图片被送入模型而非仅提供路径；允许选择任务相关的一种反馈，避免机械检查所有类型。实际对模型行为的改善待使用观察。

### 完整提示词快照

````markdown
---
name: feedback
description: Inspect saved work through actual file metadata, text and command results, or native image feedback, including rendering local HTML and SVG for visual review.
---

Use this skill when deciding whether saved work meets the task's requirements or what to change next. Choose feedback that can reveal a relevant failure: file acquisition confirms what was saved, text or command output exposes structure and behavior, and native images expose visible composition. Obtain the actual result before judging it. Simple work needs only the relevant check; every stage does not require all three forms of feedback.

Resolve [scripts/feedback.py](scripts/feedback.py) against this SKILL.md's directory and execute it directly through the existing `shell` tool with Python 3.11 or newer. Set its absolute path in each shell call; variables do not persist between calls. Use `--help` when needed. Read or adapt the source for customization, rather than rebuilding routine acquisition operations. Existing task-specific checks and readers can still be used when they fit better.

## Acquisition operations

| Command | Acquired result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256 for regular files; bounded child metadata for directories. This does not read a file's meaning. |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Numbered UTF-8 text with exact returned characters, including line endings. `--column` applies to the first requested line. Resume at returned `next_line` and `next_column` if truncated. |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual command status, bounded separate stdout/stderr tails, and complete binary logs. Arguments execute directly; use `sh -lc '...'` explicitly when shell syntax is needed. |
| `image PATH... [--label TEXT]` | Existing local PNG/JPEG/GIF/WebP images, with format detected from their headers. Their pixels are sent to the model as native image feedback by the agent runtime. |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | A fresh screenshot of local HTML/SVG through an installed Chrome/Chromium, followed by native image feedback. No dependency installation is attempted. |

Success and acquisition failures return one JSON feedback envelope on stdout; errors also go to stderr. `run` and `render` preserve failing command status; timeout returns 124. Inspect the outer shell status and the returned observations, including `timed_out`, actual `exit_code`, log locations and truncation. For long logs, use `text` to read relevant ranges. The image header check detects format, not complete pixel validity; runtime acquisition can still report an unreadable image.

Return the helper's single JSON object as the entire stdout of that shell call; do not mix it with other commands' printed output or pipe it through a formatter. The agent runtime acquires declared images before text filtering, validates and snapshots them, then sends their bytes in the next model request. Use `shell result_mode=direct` for exact text evidence; `auto` is suitable for bounded summaries, and image acquisition also works with `report`. Merely printing an image path, obtaining its hash, or saving a screenshot does not constitute visual inspection. Inspect the native image and any acquisition diagnostic before describing what you saw.

`render` uses an isolated temporary browser profile and a fresh capture file, so a failed capture cannot reuse an earlier screenshot. Its wait value is a virtual-time budget, not a guarantee of an exact animation frame. A static screenshot can reveal clipping, layout and appearance; it does not prove smooth motion, correct interaction or all animation states. Use task-specific browser checks or several suitably captured states when those properties matter, and keep claims within what was observed.

For PDFs, office files or unsupported binaries, metadata alone is insufficient: use an appropriate installed reader to extract relevant text or render pages, then acquire that text or those images. If a required reader, renderer or model image capability is unavailable, state that specific gap and continue with useful checks that are available; do not repeatedly install speculative dependencies or claim unseen contents.

After feedback, compare the result with the task's acceptance conditions and choose the next meaningful correction or extension. Read [references/feedback-loop.md](references/feedback-loop.md) when a substantial artifact needs examples of separate save, acquisition, observation and revision turns. It demonstrates decisions driven by actual results, without imposing a fixed stage count or checkpoint file.
````

## v2 · 2026-10-11 · 检查明确的验收条件

来源：当前工作区（未提交）。保留 v1 的文件、文本、命令日志与原生图片获取能力。

- 发现的问题：获取实际反馈后，仍需要具体方法检查约定的条件，并区分条件失败、读取失败与任务整体完成。
- 本版本目标：通过通用脚本检查文件存在与大小、实际文本片段及 JSON 值；从用户要求或既有契约选定条件，行为要求通过实际可执行检查获取反馈。
- 本版本效果：新增可直接调用的 check.py 和按需验收示例；缺失 JSON 值与 null、布尔值与数字被明确区分，退出状态保留失败类别；检查结果只覆盖所提供条件，不自动宣布整个任务完成。实际模型的验收判断质量待使用观察。

### 完整提示词快照

````markdown
---
name: feedback
description: Inspect saved work through actual file metadata, text and command results, or native image feedback, including rendering local HTML and SVG for visual review.
---

Use this skill when deciding whether saved work meets the task's requirements or what to change next. Choose feedback that can reveal a relevant failure: file acquisition confirms what was saved, text or command output exposes structure and behavior, and native images expose visible composition. Obtain the actual result before judging it. Simple work needs only the relevant check; every stage does not require all three forms of feedback.

Resolve [scripts/feedback.py](scripts/feedback.py) against this SKILL.md's directory and execute it directly through the existing `shell` tool with Python 3.11 or newer. Set its absolute path in each shell call; variables do not persist between calls. Use `--help` when needed. Read or adapt the source for customization, rather than rebuilding routine acquisition operations. Existing task-specific checks and readers can still be used when they fit better.

## Acquisition operations

| Command | Acquired result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256 for regular files; bounded child metadata for directories. This does not read a file's meaning. |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Numbered UTF-8 text with exact returned characters, including line endings. `--column` applies to the first requested line. Resume at returned `next_line` and `next_column` if truncated. |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual command status, bounded separate stdout/stderr tails, and complete binary logs. Arguments execute directly; use `sh -lc '...'` explicitly when shell syntax is needed. |
| `image PATH... [--label TEXT]` | Existing local PNG/JPEG/GIF/WebP images, with format detected from their headers. Their pixels are sent to the model as native image feedback by the agent runtime. |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | A fresh screenshot of local HTML/SVG through an installed Chrome/Chromium, followed by native image feedback. No dependency installation is attempted. |

Success and acquisition failures return one JSON feedback envelope on stdout; errors also go to stderr. `run` and `render` preserve failing command status; timeout returns 124. Inspect the outer shell status and the returned observations, including `timed_out`, actual `exit_code`, log locations and truncation. For long logs, use `text` to read relevant ranges. The image header check detects format, not complete pixel validity; runtime acquisition can still report an unreadable image.

Return the helper's single JSON object as the entire stdout of that shell call; do not mix it with other commands' printed output or pipe it through a formatter. The agent runtime acquires declared images before text filtering, validates and snapshots them, then sends their bytes in the next model request. Use `shell result_mode=direct` for exact text evidence; `auto` is suitable for bounded summaries, and image acquisition also works with `report`. Merely printing an image path, obtaining its hash, or saving a screenshot does not constitute visual inspection. Inspect the native image and any acquisition diagnostic before describing what you saw.

`render` uses an isolated temporary browser profile and a fresh capture file, so a failed capture cannot reuse an earlier screenshot. Its wait value is a virtual-time budget, not a guarantee of an exact animation frame. A static screenshot can reveal clipping, layout and appearance; it does not prove smooth motion, correct interaction or all animation states. Use task-specific browser checks or several suitably captured states when those properties matter, and keep claims within what was observed.

For PDFs, office files or unsupported binaries, metadata alone is insufficient: use an appropriate installed reader to extract relevant text or render pages, then acquire that text or those images. If a required reader, renderer or model image capability is unavailable, state that specific gap and continue with useful checks that are available; do not repeatedly install speculative dependencies or claim unseen contents.

For explicit acceptance conditions on saved files, use [scripts/check.py](scripts/check.py): `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]` (flags can repeat), or `json PATH [--pointer /path] --equals-json VALUE`. It checks the actual saved data, returns each condition's result and bounded evidence, and exits 0 for passing supplied conditions, 1 for failed conditions or 2 for input/acquisition errors. A missing JSON value differs from null, and booleans differ from numbers. Choose expected conditions from the task or established contract before evaluating the output. Do not change them merely to make a failing result pass. For behavior, run the relevant executable check through `run`; text presence or a matching value alone cannot establish the behavior of a program. Read [references/acceptance-checks.md](references/acceptance-checks.md) when deciding how to turn requirements into observable checks and respond to a failure.

After feedback, compare the result with the task's acceptance conditions and choose the next meaningful correction or extension. Read [references/feedback-loop.md](references/feedback-loop.md) when a substantial artifact needs examples of separate save, acquisition, observation and revision turns. It demonstrates decisions driven by actual results, without imposing a fixed stage count or checkpoint file.
````

## v3 · 2026-10-11 · 按待判断条件选择反馈能力

来源：当前工作区（未提交）。现有 tools 和操作脚本接口不变。

- 发现的问题：反馈入口强调旧场景的视觉制作方式，并把依赖不足后的处理写成普遍禁令，容易让模型套用固定路径。
- 本版本目标：以当前待判断条件选择文件、文本、执行或图片证据，区分工作结果失败和获取能力不足。
- 本版本效果：保留真实获取接口、原生图片与格式边界，按实际结果决定修正、扩展、完成或继续调查；不要求固定反馈顺序。实际模型的反馈选择质量待使用观察。

### 完整提示词快照

````markdown
---
name: feedback
description: Acquire file, text, execution, and image evidence to assess actual work results and choose the next action.
---

Use this skill when a decision about work depends on observing its actual result. Identify the condition or uncertainty to resolve, then select evidence that can answer it: metadata for file existence and identity, text for actual content, execution for behavior, or images for visible properties. Feedback need not follow a fixed sequence or use every source. Obtain the relevant result, compare it with the task's requirements, and let that observation determine whether to correct, extend, finish, or investigate further.

Resolve [scripts/feedback.py](scripts/feedback.py) against this SKILL.md's directory and execute it directly through the existing `shell` tool with Python 3.11 or newer. Set its absolute path in each shell call; variables do not persist between calls. Use `--help` when needed. Read or adapt the source for customization, rather than rebuilding routine acquisition operations. Existing task-specific checks and readers can still be used when they fit better.

## Acquisition operations

| Command | Acquired result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256 for regular files; bounded child metadata for directories. This does not read a file's meaning. |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Numbered UTF-8 text with exact returned characters, including line endings. `--column` applies to the first requested line. Resume at returned `next_line` and `next_column` if truncated. |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual command status, bounded separate stdout/stderr tails, and complete binary logs. Arguments execute directly; use `sh -lc '...'` explicitly when shell syntax is needed. |
| `image PATH... [--label TEXT]` | Existing local PNG/JPEG/GIF/WebP images, with format detected from their headers. Their pixels are sent to the model as native image feedback by the agent runtime. |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | A fresh screenshot of local HTML/SVG through an installed Chrome/Chromium, followed by native image feedback. No dependency installation is attempted. |

Success and acquisition failures return one JSON feedback envelope on stdout; errors also go to stderr. `run` and `render` preserve failing command status; timeout returns 124. Inspect the outer shell status and the returned observations, including `timed_out`, actual `exit_code`, log locations and truncation. For long logs, use `text` to read relevant ranges. The image header check detects format, not complete pixel validity; runtime acquisition can still report an unreadable image.

Return the helper's single JSON object as the entire stdout of that shell call; do not mix it with other commands' printed output or pipe it through a formatter. The agent runtime acquires declared images before text filtering, validates and snapshots them, then sends their bytes in the next model request. Use `shell result_mode=direct` for exact text evidence; `auto` is suitable for bounded summaries, and image acquisition also works with `report`. Merely printing an image path, obtaining its hash, or saving a screenshot does not constitute visual inspection. Inspect the native image and any acquisition diagnostic before describing what you saw.

`render` uses an isolated temporary browser profile and a fresh capture file, so a failed capture cannot reuse an earlier screenshot. Its wait value is a virtual-time budget, not a guarantee of an exact animation frame. A screenshot establishes the visible state captured at its viewport; temporal or interactive properties need observations of those properties. Choose suitable state captures or executable interaction checks when required, and keep claims within what was observed.

For PDFs, office files or unsupported binaries, metadata alone is insufficient: use a suitable reader to extract relevant text or render pages, then acquire that text or those images. If a required reader, renderer or model image capability is unavailable, distinguish that acquisition gap from a failed work result. Choose a relevant alternative or resolve the missing capability within the task's scope; report what remains unobserved.

For explicit acceptance conditions on saved files, use [scripts/check.py](scripts/check.py): `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]` (flags can repeat), or `json PATH [--pointer /path] --equals-json VALUE`. It checks the actual saved data, returns each condition's result and bounded evidence, and exits 0 for passing supplied conditions, 1 for failed conditions or 2 for input/acquisition errors. A missing JSON value differs from null, and booleans differ from numbers. Choose expected conditions from the task or established contract before evaluating the output. Do not change them merely to make a failing result pass. For behavior, run the relevant executable check through `run`; text presence or a matching value alone cannot establish the behavior of a program. Read [references/acceptance-checks.md](references/acceptance-checks.md) when deciding how to turn requirements into observable checks and respond to a failure.

Read [references/feedback-loop.md](references/feedback-loop.md) when deciding how to connect an observation with the next action. Its examples show alternative feedback routes and decisions based on returned results, without prescribing an artifact, stage count, or checkpoint file.
````
