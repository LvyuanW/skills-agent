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


## v4 · 2026-10-13 · 明确实际取证能力与原生图片协议

来源：当前工作区（未提交）。与[主工作提示词 v7](agent-system.md) 的按需技能选择规则同步；本轮仅调整技能入口，不修改脚本、工具接口或运行时协议。

- 发现的问题：泛化描述不利于模型发现命令时限、完整日志、原生图片和精确 JSON 检查等具体能力，正文存在重复说明。
- 本版本目标：按当前决策选择实际执行、文本值或像素证据，精简操作入口并保留图片反馈信封、失败状态、截断和验收边界。
- 本版本效果：技能描述突出真实取证能力，入口允许复用、调整或自编检查；原生图片仍要求保持整段 stdout 的反馈协议。本轮没有修改脚本和参考文件，真实模型的取证与判断效果待观察。

### 完整提示词快照

````markdown
---
name: feedback
description: Obtain execution or artifact evidence with command timeouts and full logs, exact text/JSON checks, and native image feedback from local images or rendered HTML/SVG.
---

Choose the observation needed for the next decision: actual execution for behavior, saved text or values for content, and pixels for appearance. A successful write, file path or screenshot command alone does not establish the contents or visual result.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `image PATH... [--label TEXT]` | Existing PNG/JPEG/GIF/WebP pixels sent as native model input |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot of local HTML/SVG through installed Chrome/Chromium, returned as native image feedback |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

**Native image protocol:** return the helper's single JSON feedback envelope as the entire shell stdout, without other printed output or a formatter. Custom scripts returning images must preserve this protocol; inspect the reference implementation before changing it. The runtime validates and snapshots declared images before filtering text, then supplies their bytes to the model. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````


## v5 · 2026-10-13 · 未知成本计算先试跑并选择停止条件

来源：当前工作区（未提交）。主工作提示词本轮未修改，不新增其版本。

- 发现的问题：在执行前不估计未知计算成本、也不设有意义的停止条件，可能让一次脚本调用长时间占用当前任务；小规模成功也不能证明完整任务已完成。
- 本版本目标：先用覆盖昂贵步骤的代表性小输入检查正确性并估计成本，再按任务选择执行时间、迭代、收敛或无进展条件。
- 本版本效果：技能要求区分试跑估计与完整结果，并明确 run 的执行超时会停止命令；不设统一样本量或固定时限。没有新增运行时超时机制，真实计算任务的使用效果待观察。

### 完整提示词快照

````markdown
---
name: feedback
description: Obtain execution or artifact evidence with command timeouts and full logs, exact text/JSON checks, and native image feedback from local images or rendered HTML/SVG.
---

Choose the observation needed for the next decision: actual execution for behavior, saved text or values for content, and pixels for appearance. A successful write, file path or screenshot command alone does not establish the contents or visual result.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `image PATH... [--label TEXT]` | Existing PNG/JPEG/GIF/WebP pixels sent as native model input |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot of local HTML/SVG through installed Chrome/Chromium, returned as native image feedback |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Native image protocol:** return the helper's single JSON feedback envelope as the entire shell stdout, without other printed output or a formatter. Custom scripts returning images must preserve this protocol; inspect the reference implementation before changing it. The runtime validates and snapshots declared images before filtering text, then supplies their bytes to the model. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````


## v6 · 2026-10-16 · 附图统一转入 images 技能

来源：当前工作区（未提交）。

- 发现的问题：feedback 的直接附图入口没有统一图片张数、单图大小和历史回查地址要求。
- 本版本目标：将所有 image/render 附图路由到 images 技能，维持现有取证通道，明确每请求5张与每图1,000,000字节，并记录发送快照地址。
- 本版本效果：新增技能路由与限额说明；helper 对超限明确失败，并指导使用 images.py prepare/compose/save_bounded 压缩、合图或生成；原图留存，普通文件、文本和执行反馈行为保持。实际模型使用效果待观察。

### 完整提示词快照

````markdown
---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification; this does not replace final review.
---

Choose the observation needed for the next decision: actual execution for behavior, saved text or values for content, and pixels for appearance. A successful write, file path or screenshot command alone does not establish the contents or visual result.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `image PATH... [--label TEXT] [--source-url URL]` | Image attachment channel used by the images skill; repeat source URLs in path order |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot of local HTML/SVG through installed Chrome/Chromium, returned as native image feedback |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) before attaching existing or generated images, including `image` and `render` outputs. A model request permits at most 5 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than five needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images. Each attachment carries a local URL, and the runtime supplies the immutable snapshot URL alongside the pixels for later inspection.

**Native image protocol:** return the helper's single JSON feedback envelope as the entire shell stdout, without other printed output or a formatter. The runtime validates and snapshots declared images before filtering text, then supplies their bytes to the model. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````


## v7 · 2026-10-17 · 视觉反馈使用声明式附图

来源：工作区未提交；`skills_agent/builtin_skills/feedback/SKILL.md`。

- 发现的问题：反馈技能仍将单 stdout JSON 作为附图入口，组合调用容易破坏。
- 本版本目标：视觉反馈转向 images 技能和 shell.images；保留实际像素验证要求。
- 本版本效果：技能与工具契约保持一致，脚本可打印普通日志和多个结果；技能格式与 JSON 示例校验通过，实际模型遵循待观察。

### 完整原文

````markdown
---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification; this does not replace final review.
---

Choose the observation needed for the next decision: actual execution for behavior, saved text or values for content, and pixels for appearance. A successful write, file path or screenshot command alone does not establish the contents or visual result.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 5 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than five needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````

### 同步更新的 helper 帮助原文

来源：`skills_agent/builtin_skills/feedback/scripts/feedback.py`；仅改 image 子命令帮助，旧输出保持兼容。

```text
image: legacy image feedback metadata; prefer shell.images
```


## v8 · 2026-10-17 · 图片上限统一为10张

来源：工作区未提交；`skills_agent/builtin_skills/feedback/SKILL.md`。

- 发现的问题：原上限为每个请求5张图片，用户要求扩展为10张。
- 本版本目标：统一运行时、工具说明与技能的10图上限；每图1 MB及历史图片引用规则不变。
- 本版本效果：允许同一请求携带至多10张图片；超出时使用合图，实际模型表现待观察。

### 完整原文

````markdown
---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification; this does not replace final review.
---

Choose the observation needed for the next decision: actual execution for behavior, saved text or values for content, and pixels for appearance. A successful write, file path or screenshot command alone does not establish the contents or visual result.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````

<!-- 历史分支：03-feedback-conflicts；下列版本号属于该分支，保留原记录。 -->

## v9 · 2026-10-26 · 验证冲突的相反处置示例

来源：实验分支 `experiment/ale-feedback-conflicts`，共同基线 `1dd2267`；本节随该分支的提示词改写一同提交。

- 发现的问题：原有原则已区分坏检查与坏产出，但抽象说明不足以展示两种情形需要相反处置；检查通过数量也可能掩盖仍未满足的要求。
- 本版本目标：按原始要求和观察到的原因选择修交付物或修检查器，保留有效要求，撤回无依据的额外条件。
- 本版本效果：仅替换技能开头的方法说明，发现描述、脚本接口及其他指导不变；格式和兼容验证见提交记录，模型是否自主采用以及实际测评效果待观察。

### 完整原文

````markdown
---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification.
---

Choose execution for behavior, saved content for values, and pixels for appearance; a successful write or capture alone proves neither content nor correctness.

When a check fails, distinguish two cases. An output can have the right row count yet duplicate one required ID and omit another: fix the output; passing the count check does not waive coverage. Conversely, a check can reject a valid dated snapshot because it reads only period ranges: establish the source's time meaning and fix the check, preserving valid data. Decide from the original requirement and observed cause, not from which change yields a pass. Withdraw an unsupported extra condition when justified; keep unresolved valid conditions visible.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````

## v10 · 2026-10-27 · 定位反馈来源并限制修复范围

来源：`03-feedback-conflicts` 工作区；`skills_agent/builtin_skills/feedback/SKILL.md`。

- 发现的问题：反馈冲突的两个示例没有说明原因尚未确定时如何行动；独立诊断路径的现象可能被错误外推到正式流程，进而引发缺少依据的全局修改。撤回额外条件的对象也需要限定为 agent 自己添加的条件。
- 本版本目标：区分产物、检查、诊断路径和自设预期，先定位未确定原因；隔离改变执行条件的实验，按证据与任务边界限定修复，并更新核对实际交付物。明确只使用已知可见的执行截止条件。
- 本版本效果：指令现在将未定位原因导向区分性检查，并要求将已确认修复落实到受影响交付物；发现描述、工具接口及共用 system 原则不变。此版本未重跑测评，实际模型行为和分数改善待观察。

### 完整原文

````markdown
---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification.
---

Choose execution for behavior, saved content for values, and pixels for appearance; a successful write or capture alone proves neither content nor correctness. Before applying a diagnostic finding, establish which inputs, entry point and preprocessing it exercised. A separate probe does not automatically reproduce the task's actual path.

When feedback conflicts, locate the defect in the work, the check, the diagnostic path or an unsupported expectation. If the cause is unresolved, choose a check that distinguishes the plausible causes before changing the work. Preserve valid outputs and test changes to execution conditions separately; apply corrections only to the scope supported by evidence and allowed by the task. Update the affected deliverable and verify the corrected result. Decide from the original requirement and observed cause, not from which change yields a pass. Withdraw only extra conditions you introduced without support in the task; keep unresolved user requirements visible.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an explicitly available execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````

<!-- 历史分支：04-discriminating-verification；下列版本号属于该分支，保留原记录。 -->

## v9 · 2026-10-26 · 按交付边界和待解问题选择验证

来源：实验分支 `experiment/ale-discriminating-verification`，共同基线 `1dd2267`；本节随该分支的提示词改写一同提交。

- 发现的问题：检查可能只覆盖内存中的中间结果、另一条预处理路径或与实现共享假设的重复计算，未触达实际交付物的待证性质。
- 本版本目标：按具体缺口选择真实消费者入口、非平凡输入或独立数学依据，复用足够证据，避免默认要求第二实现或更多检查。
- 本版本效果：仅替换技能开头的方法说明，发现描述、脚本接口及其他指导不变；格式和兼容验证见提交记录，模型是否自主采用以及实际测评效果待观察。

### 完整原文

````markdown
---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification.
---

Choose evidence at the boundary the task will use: load the saved artifact through its intended consumer, exercise the actual entry point and preprocessing, or inspect pixels for appearance. An in-memory result, successful write or screenshot command alone does not establish that boundary's result.

For an unresolved correctness question, choose a probe that distinguishes the suspected error: a nontrivial input can expose behavior hidden by zeros or symmetry; an independent formula, known-answer case or task-provided reference can test assumptions shared by the implementation's own checks. A second implementation using the same assumptions adds little independence. Choose the method suited to the gap, rather than requiring all of them; reuse existing evidence when it already resolves the question.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````

## v10 · 2026-10-27 · 按具体缺口核对受影响的实际交付

来源：`04-discriminating-verification` 工作区；`skills_agent/builtin_skills/feedback/SKILL.md`。

- 发现的问题：真实入口和独立依据的方向需要进一步限定到当前待解问题；局部逻辑修复后可能未更新最终产物，或未按实际影响范围决定后续检查。
- 本版本目标：保留实际 consumer 与区分性验证，按问题条件选择入口检查；修复后更新核对最终输出及依赖结果，复用仍适用的证据，只因新变化、反证或覆盖缺口扩大检查。明确只使用已知可见的执行截止条件。
- 本版本效果：指令现在连接当前问题、所选验证方式和受影响的实际交付物，保留非平凡输入及独立依据的选择空间；发现描述、工具接口及共用 system 原则不变。此版本未重跑测评，实际模型行为和分数改善待观察。

### 完整原文

````markdown
---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification.
---

Choose evidence for the unresolved question. When correctness depends on how an artifact is consumed, inspect the saved artifact through its intended consumer and relevant actual entry point or preprocessing path. Inspect pixels for questions about appearance. An in-memory result, successful write or screenshot command alone does not establish the result at that boundary.

Choose a probe that distinguishes the suspected error: a nontrivial input can expose behavior hidden by zeros or symmetry; an independent formula, known-answer case or task-provided reference can test assumptions shared by the implementation's own checks. A second implementation using the same assumptions adds little independence. Choose the method suited to the gap, rather than requiring all of them. After a correction, update and check the affected final output and dependent results. Reuse evidence that remains applicable; repeat broader checks only when a new change, contradiction or coverage gap warrants them.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an explicitly available execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````

## v11 · 2026-10-27 · 统一诊断隔离与针对性验证

来源：六个实验 worktree 的共享内容整理，工作区未提交。历史分支记录保留；同名旧版本按来源分支区分。

- 发现的问题：此前03与04分别保存冲突处置和验证指导，导致同名skill正文不同。
- 本版本目标：合并真实路径检查、未定原因的诊断、受证据约束的修复、最终产物核对与证据复用。
- 本版本效果：已形成共享正文和实现；各 worktree 只保留显式注入选择差异。实际模型行为和测评分数待运行验证，不以格式或单元测试推断效果。

### 完整原文

````markdown
---
name: feedback
description: >-
  Use when the next decision needs actual execution or artifact evidence, including
  bounded command runs with full logs, exact text/JSON checks, or rendering
  HTML/SVG. All image attachments also require the images skill. Skip pure planning or lookups that
  require no execution or artifact verification.
---

Choose the observation needed for the next decision: actual execution for behavior, saved content for values, and pixels for appearance. When correctness depends on how an artifact is consumed, inspect the saved artifact through its intended consumer and relevant actual entry point or preprocessing path. Before applying a diagnostic finding, establish which inputs and execution path it exercised; a separate probe does not automatically reproduce the task's actual path. A successful write or capture alone proves neither content nor correctness.

When feedback conflicts, locate the defect in the work, the check, the diagnostic path or an unsupported expectation. If the cause is unresolved, choose a check that distinguishes the plausible causes before changing the work. Preserve valid outputs and test changes to execution conditions separately; apply corrections only to the scope supported by evidence and allowed by the task. Decide from the original requirement and observed cause, not from which change yields a pass. Withdraw only extra conditions you introduced without support in the task; keep unresolved user requirements visible.

Choose the check suited to the gap: a nontrivial input can expose behavior hidden by zeros or symmetry; an independent formula, known-answer case or task-provided reference can test assumptions shared by the implementation's own checks. A second implementation using the same assumptions adds little independence; do not require every method. After a correction, update and check the affected final output and dependent results. Reuse evidence that remains applicable; repeat broader checks only when a new change, contradiction or coverage gap warrants them.

[scripts/feedback.py](scripts/feedback.py) supplies reference acquisition methods. Reuse them, read/adapt the source, or write task-specific checks when appropriate. Direct invocation requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md; variables do not persist across shell calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes argument lists directly; use `sh -lc '...'` explicitly for shell syntax. `run` and `render` preserve failing status and return 124 on timeout. Inspect `timed_out`, actual `exit_code`, acquisition diagnostics and truncation. A tail may omit the cause; read the complete logs as needed. The helpers do not install dependencies.

Before a substantial computation with unknown cost, try a small representative input that reaches the expensive work. Check correctness and measure runtime or progress before scaling up; an extrapolation is an estimate, and a pilot does not replace the required full result. Choose the sample size and a meaningful stopping condition from the task, such as an explicitly available execution deadline, iteration limit, convergence criterion or interval without progress. The `run --timeout` limit stops command execution; set it for the planned work rather than assuming one duration fits every task.

**Images:** always use the [images skill](../images/SKILL.md) and declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images total, each at most 1,000,000 bytes before base64 encoding. Its `images.py prepare` makes bounded copies while keeping originals; `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. New images use its in-memory `save_bounded` before writing. These feedback helpers reject oversized images.

**Image declaration:** use `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`; local `file://` snapshot URLs also work, and optional `source_url` retains the original source. After execution the runtime reads and snapshots the declared files, then supplies pixels and snapshot URLs before filtering text. Background jobs register their images at launch and attach them only with the completion result. Explicit `images`, including `[]`, determines attachment independently of logs, JSON metadata or other stdout. Preparation/render metadata alone is not the attachment interface; do not call an extra attach helper. A header check only identifies format; inspect the actual image or acquisition error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` uses a fresh screenshot and isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. A screenshot covers only the captured viewport and state. PDF/office files need an appropriate reader or page rendering first; text extraction is not visual inspection.

For explicit saved-data conditions, [scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes missing from null and booleans from numbers; exit codes are 0 for supplied conditions passing, 1 for a mismatch and 2 for acquisition/input failure. Derive expected values from the task rather than the produced answer. Read [references/acceptance-checks.md](references/acceptance-checks.md) for condition-specific examples, or [references/feedback-loop.md](references/feedback-loop.md) for connecting returned observations to the next action. Neither prescribes a fixed checklist.
````

## v12 · 2026-10-27 · 直接英文表达与明确使用边界

来源：本轮工作区未提交；skills_agent/builtin_skills/feedback/SKILL.md。

- 发现的问题：部分指令较抽象或过长，使用与跳过条件需要更容易区分。
- 本版本目标：使用清楚、自然、直接的英文说明动作与边界；保留实质约束、原脚本接口和代码示例。六个实验 worktree 使用同一正文。
- 本版本效果：技能格式、本地引用和原有代码示例核对通过；已完成技能路由场景审阅。未运行新一轮真实模型测评，行为与分数效果待验证。

### 完整原文

````text
---
name: feedback
description: >-
  Inspect actual output or behavior when the next decision needs execution logs,
  saved text or JSON values, or a rendered HTML/SVG image. Skip planning and lookups
  that need no execution or output check, or when existing observations already
  answer the question. Use the images skill as well whenever attaching images.
---

# Check the actual result

Use this skill when you need an observation from execution or a saved artifact to decide what to do next. It provides ways to collect and inspect results. Pure planning and lookups that do not need such a check can proceed without it. When the cause of conflicting results needs investigation, use the [investigation skill](../investigation/SKILL.md) as needed.

If the current tool result or a simple targeted read already provides the evidence you need, use it directly. You do not need to load this skill for an additional check.

## Choose evidence for the question

Run the behavior to check behavior, read saved content to check values, and inspect pixels to check appearance. If correctness depends on how a file is used, open or process the saved file through its intended consumer, entry point and relevant preprocessing steps. A successful write or capture alone does not prove that the content is correct.

Before applying a diagnostic result, confirm which inputs and execution path the check used. A separate probe does not automatically reproduce the task's actual execution. Preserve valid outputs and try changes to execution conditions separately.

Choose a check that addresses the unresolved question. For example, zeros or symmetric inputs may hide a defect that a less uniform input reveals. An independent formula, an input with a known answer or a task-provided reference may test conditions shared by the implementation's own checks. A second implementation that makes the same assumptions offers little independent evidence. Use the appropriate method; these examples are not a required sequence.

## Use the result to guide the work

When results conflict, determine whether the problem is in the work, the check, the diagnostic path or an expectation that has no basis in the task. If the cause remains unclear, choose a check that can distinguish the possible causes before making changes.

Decide from the original requirement and the observed cause. Do not choose a change just because it makes a check pass. Remove extra conditions you introduced without support in the task, but keep unresolved user requirements visible. Apply only corrections supported by evidence and allowed by the task.

After a correction, update and check the affected final output and any results that depend on it. Reuse evidence that still applies. Repeat broader checks only when a change, contradiction or newly found gap gives a reason to do so.

## Collect execution and file results

[scripts/feedback.py](scripts/feedback.py) provides reference helpers. Use them directly, read or adapt their source, or write a check suited to the task. Direct use requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md. Set variables in each shell call that needs them; they do not persist between calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes the argument list directly. Use `sh -lc '...'` explicitly when the command needs shell syntax. `run` and `render` preserve failure status and return 124 on timeout. Inspect `timed_out`, the actual `exit_code`, collection errors and any truncation. A log tail may omit the cause, so read the full saved logs when needed. The helpers do not install dependencies.

Before substantial computation with an unknown cost, try a small representative input that reaches the expensive stages. Check correctness and measure runtime or progress before scaling up. An extrapolation remains an estimate, and the trial does not replace the required full result. Choose a suitable sample size and stopping condition from the task, such as an explicitly available execution deadline, iteration limit, convergence criterion or interval without progress. `run --timeout` stops the command; set it for the planned work.

## Inspect images

Always use the [images skill](../images/SKILL.md) when attaching an image. Declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images in total, each at most 1,000,000 bytes before base64 encoding. `images.py prepare` makes copies within the limit and keeps the originals. `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. For new images, use its in-memory `save_bounded` before writing. The feedback helpers reject oversized images.

Declare an image as `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`. Local `file://` snapshot URLs also work. The optional `source_url` keeps the original source reference. After execution, the runtime reads and snapshots the declared files and supplies their pixels and snapshot URLs before filtering text.

Background jobs register image paths at launch and attach the images only with the completion result. The explicit `images` value, including `[]`, controls attachment independently of logs, JSON metadata and other stdout. Render or preparation metadata does not attach an image; do not call an extra attachment helper. A header check identifies the format only. Inspect the actual image or the reported collection error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` takes a fresh screenshot in an isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. One screenshot covers only the captured viewport and state. PDF and office files need a suitable reader or rendered pages first. Extracting their text does not check their appearance.

## Check explicit saved values

[scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes a missing value from null and booleans from numbers. Exit codes are 0 when the supplied conditions pass, 1 for a mismatch and 2 for a collection or input failure. Derive expected values from the task, not from the answer being checked.

Read [references/acceptance-checks.md](references/acceptance-checks.md) for examples of checking specific conditions. Read [references/feedback-loop.md](references/feedback-loop.md) when you need to connect a returned observation to the next action. Neither is a fixed checklist.
````

## v13 · 2026-10-28 · 改名 check-results 并明确结果检查场景

来源：六个实验 worktree 的共享修改；提交标识见 [本轮提交记录](README.md#2026-10-28-召回条件与名称更新)。

当前源文件：`skills_agent/builtin_skills/check-results/SKILL.md`。

- 发现的问题：20261028 的45次基线运行均可见技能目录，但没有读取 feedback 正文；旧名称可能与一般反馈或输出通道混淆，description 未直接说清检查计算、文件和程序是否满足任务的用途。
- 本版本目标：将现行技能改名为 check-results，并采用已确认的 description；用自然英文说明实际结果、诊断与正式运行的差别、修改到达最终产物以及根据证据检查或纠正，保持 helper 接口与有依据的跳过条件。
- 本版本效果：现行名称、目录和指令已更新，本文沿用旧历史文件名并保留全部旧版本；召回、检查行为和分数效果待真实模型测评。

### 完整原文

````text
---
name: check-results
description: >-
  Use when you need to check whether a calculation, generated file, or running
  program does what the task requires. Also use when a separate test behaves
  differently from the real run, or when a change may not have reached the final
  files. Choose a check that answers the question, inspect the actual result,
  and confirm the correction. Skip another check when existing evidence already
  answers the same question and the relevant inputs, code, and files are unchanged.
---

# Check the actual result

Decide what you need to know before running another check. Choose what to inspect, read the actual result, and use it to decide whether the work needs a correction. Pure planning and lookups that do not need a result check can proceed without this skill.

An existing tool result or a short file read may provide all the evidence you need. Use it directly. If you still need to decide what to check or what the result means, the method below can help even when no new execution is needed. Skip another check when the earlier observation still answers the question and the relevant inputs, code and files have not changed.

## Choose a check that answers the question

Run the program to check its behavior, read the saved file to check its contents, and inspect the image to check its appearance. If correctness depends on how a file is used, open or process the saved file with the intended program or command, including the required input preparation. A successful write or capture alone does not show that the content is correct.

Before using a separate test to change the work, confirm which inputs it used and which code it ran. It may differ from the real task. Keep experiments in separate scripts or files when they could change the execution conditions or overwrite useful outputs. A failure in the test does not by itself show that the real run has failed.

Choose a check that can answer the question you identified. For example, zeros or symmetric inputs may hide a defect that a less uniform input reveals. A separate formula, an input with a known answer or a reference supplied with the task may reveal an error that the implementation's own checks miss. Two implementations can agree and still be wrong if both rely on the same unverified definition or method. Choose the check that fits the problem; these examples are not a required sequence.

## Use the result to guide the work

When results conflict, check whether the problem is in the work, the check itself, the way the separate test ran, or an extra condition you introduced. If you cannot yet tell, choose a check that can tell those causes apart before making a correction. Use the [investigation skill](../investigation/SKILL.md) when you need help deciding between explanations, definitions or methods.

Decide what to change from the original requirement and what the result shows. Do not change the work just to make a check pass. Remove only extra conditions you added without support in the task; keep user requirements that have not yet been met. Make the correction that the evidence supports, within the scope the user allowed.

After a correction, update the affected final files and any results that use them. Read those outputs or run the appropriate check to confirm they contain the correction. Fixing a separate test is not enough if the final files still contain the error. Reuse evidence that still applies. Repeat broader checks only when a change, conflicting result or newly found gap gives a reason to do so.

## Collect execution and file results

[scripts/feedback.py](scripts/feedback.py) provides reference helpers. Use them directly, read or adapt their source, or write a check suited to the task. Direct use requires Python 3.11 or newer and an absolute script path resolved from this SKILL.md. Set variables in each shell call that needs them; they do not persist between calls.

| Operation | Result |
| --- | --- |
| `file PATH [--limit 100]` | File type, size, modification time and SHA-256; bounded directory metadata |
| `text PATH [--start 1 --end 200 --column 1 --max-chars 12000]` | Exact numbered UTF-8 text with `next_line`/`next_column` continuation |
| `run --log-dir DIR [--timeout 120] -- COMMAND...` | Actual exit status, timeout flag, bounded stdout/stderr tails and complete separate binary logs |
| `render PATH --output PNG [--width 1200 --height 800 --wait-ms 1000 --browser PATH --timeout 30]` | Screenshot file of local HTML/SVG through installed Chrome/Chromium, plus acquisition metadata |

`run` executes the argument list directly. Use `sh -lc '...'` explicitly when the command needs shell syntax. `run` and `render` preserve failure status and return 124 on timeout. Inspect `timed_out`, the actual `exit_code`, collection errors and any truncation. A log tail may omit the cause, so read the full saved logs when needed. The helpers do not install dependencies.

Before substantial computation with an unknown cost, try a small representative input that reaches the expensive stages. Check correctness and measure runtime or progress before scaling up. An extrapolation remains an estimate, and the trial does not replace the required full result. Choose a suitable sample size and stopping condition from the task, such as an explicitly available execution deadline, iteration limit, convergence criterion or interval without progress. `run --timeout` stops the command; set it for the planned work.

## Inspect images

Always use the [images skill](../images/SKILL.md) when attaching an image. Declare selected outputs in the shell tool's `images` parameter. A model request permits at most 10 images in total, each at most 1,000,000 bytes before base64 encoding. `images.py prepare` makes copies within the limit and keeps the originals. `compose` combines more than ten needed sources into a numbered grid with source URLs and coordinate mappings. For new images, use its in-memory `save_bounded` before writing. The feedback helpers reject oversized images.

Declare an image as `images: [{"path": "/absolute/host/output.png", "label": "Current result"}]`. Local `file://` snapshot URLs also work. The optional `source_url` keeps the original source reference. After execution, the runtime reads and snapshots the declared files and supplies their pixels and snapshot URLs before filtering text.

Background jobs register image paths at launch and attach the images only with the completion result. The explicit `images` value, including `[]`, controls attachment independently of logs, JSON metadata and other stdout. Render or preparation metadata does not attach an image; do not call an extra attachment helper. A header check identifies the format only. Inspect the actual image or the reported collection error. Use `result_mode: direct` for exact text; native images also work with `auto` or `report`.

`render` takes a fresh screenshot in an isolated browser profile. Its wait value is a virtual-time budget, not an exact animation timestamp. One screenshot covers only the captured viewport and state. PDF and office files need a suitable reader or rendered pages first. Extracting their text does not check their appearance.

## Check explicit saved values

[scripts/check.py](scripts/check.py) supports `file PATH... [--min-bytes N]`, `text PATH --contains TEXT [--absent TEXT]`, and `json PATH [--pointer /path] --equals-json VALUE`. It distinguishes a missing value from null and booleans from numbers. Exit codes are 0 when the supplied conditions pass, 1 for a mismatch and 2 for a collection or input failure. Derive expected values from the task, not from the answer being checked.

Read [references/acceptance-checks.md](references/acceptance-checks.md) for examples of checking specific conditions. Read [references/feedback-loop.md](references/feedback-loop.md) when you need to connect a returned observation to the next action. Neither is a fixed checklist.
````
