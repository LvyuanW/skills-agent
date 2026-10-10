# file-editing 渐进编辑示例版本时间线

当前源文件：`skills_agent/builtin_skills/file-editing/references/incremental-editing.md`。本提示词于本次变更新增，没有更早的 Git 修订；后续沿本文追加版本，不覆盖原文。

## v1 · 2026-10-11 · 新增

来源：当前工作区（未提交）。与[主工作提示词 v3](agent-system.md) 一同启用，现有 tools 不变。

- 发现的问题：只有宏观的渐进执行要求，没有展示模型如何在工具返回后选择下一项编辑，容易把多个阶段写进同一份长脚本。
- 本版本目标：给出跨模型回合的完整行动示例：保存可见产物、根据结果扩展、检查真实文件；另展示歧义匹配失败后读取当前内容并缩小修改范围。
- 本版本效果：新增独立 shell 调用、返回结果解释及后续决定的示例；每次调用独立解析脚本路径，不依赖上一回合的 shell 变量。示例明确 XML 解析与视觉验证的边界，不要求固定步骤数或额外检查点文件。实际模型采纳情况待使用观察。

### 完整提示词快照

````markdown
# Editing across model turns

Each round below is a separate shell call followed by its returned result. Choose the next change after examining that result. These examples demonstrate operation boundaries, not a fixed plan or a requirement to stop after every small edit. A tool result can return control to the model without ending the user's task.

The examples assume the shell starts in the task's working directory and `python3` is available. In every editing call, replace `/absolute/path/from/skill-catalog/file-editing/scripts/edit.py` with the helper path resolved from the loaded SKILL.md. Each call sets its own `edit` variable; no shell variable persists from a previous call. To try the examples without touching task files, use a temporary directory as the shell call's working directory.

Output descriptions are illustrative: inspect the actual JSON, stderr, and exit status returned by the tool. They are not records of a model run.

## Example 1: build a visible scene, then extend it

The requested artifact is an HTML page with an SVG animation of a pelican on a bicycle. The first useful artifact can already show the character and composition. It need not wait for every moving part to be designed.

### Round 1: save a meaningful static scene

Action: create an HTML page displaying the pelican against a ground line.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create index.html <<'HTML'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pelican on a bicycle</title>
<style>
body { margin: 0; min-height: 100vh; display: grid; place-items: center; background: #e8f5fb; }
svg { width: min(800px, 95vw); height: auto; }
</style>
</head>
<body>
<svg viewBox="0 0 600 360" xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="scene-title scene-description">
  <title id="scene-title">Pelican on a bicycle</title>
  <desc id="scene-description">A pelican above a quiet ground line.</desc>
  <path d="M40 310 H560" stroke="#568d74" stroke-width="5"/>
  <g id="bicycle"></g>
  <g id="pelican" fill="#fffdf1" stroke="#334155" stroke-width="3">
    <ellipse cx="295" cy="180" rx="55" ry="37"/>
    <path d="M315 167 Q318 108 342 109 Q366 113 354 137 L339 168"/>
    <path d="M350 121 L407 136 L349 143 Z" fill="#f2b85a"/>
    <circle cx="343" cy="122" r="3" fill="#334155" stroke="none"/>
    <path d="M273 164 Q299 173 308 193" fill="none"/>
  </g>
</svg>
</body>
</html>
HTML
```

Returned result: a successful `create` result has `changed=true` and a numbered excerpt of the saved file. If creation fails because the file already exists, inspect that file and continue from its real contents instead of overwriting it.

Decision after observing success: the artifact is on disk and its SVG structure is available for a focused addition. Add the bicycle beneath the character. There is no need to draft the final animation or a generator for the entire page in advance.

### Round 2: add the bicycle in one coherent change

Action: replace the unique bicycle group with wheels and frame geometry.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace index.html --old '<g id="bicycle"></g>' <<'SVG'
<g id="bicycle" fill="none" stroke="#334155" stroke-width="4">
    <g class="wheel" transform="translate(210 264)">
      <circle r="44"/>
      <path d="M-44 0 H44 M0 -44 V44" stroke-width="2"/>
    </g>
    <g class="wheel" transform="translate(385 264)">
      <circle r="44"/>
      <path d="M-44 0 H44 M0 -44 V44" stroke-width="2"/>
    </g>
    <path d="M210 264 L257 219 L302 264 Z M257 219 L365 219 L302 264 M365 219 L385 264 M362 209 L365 219 M357 209 H380" stroke="#2874a6"/>
    <path d="M257 219 L252 204 M242 204 H267"/>
    <path d="M282 208 L302 244 L315 249" stroke="#d99837"/>
  </g>
SVG
```

Returned result: the `replace` result identifies the changed region. Check that the frame and both wheels appear in the excerpt; use a targeted read if the excerpt is truncated or leaves the result uncertain.

Decision after observing success: the static composition is saved. Add a small motion treatment to the wheel groups already present, and make the accessible description accurate. Those tightly related changes can share the next call.

### Round 3: add and describe motion

Action: insert CSS before the unique closing style tag, then update the existing description. `&&` preserves the failure signal and prevents the second edit if the first fails.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" insert index.html --anchor '</style>' --position before <<'CSS' &&
.wheel { transform-box: fill-box; transform-origin: center; animation: wheel-turn 2s linear infinite; }
@keyframes wheel-turn { to { rotate: 360deg; } }
@media (prefers-reduced-motion: reduce) { .wheel { animation: none; } }
CSS
python3 "$edit" replace index.html \
  --old 'A pelican above a quiet ground line.' \
  --new 'A pelican riding a bicycle with gently rotating wheels.'
```

Returned result: each operation returns its own JSON and the shell reports the final exit status. If an operation fails, retain the successful saved changes and inspect the failed location before choosing a repair. Do not claim the whole call succeeded just because an earlier operation did.

Decision after observing success: inspect and validate the saved artifact. Structural parsing can catch malformed SVG; a visual inspection, when available in the task environment, is still needed to assess composition and motion.

### Round 4: inspect the artifact that actually exists

```sh
nl -ba index.html
python3 - <<'PY'
from pathlib import Path
from xml.etree import ElementTree

html = Path("index.html").read_text(encoding="utf-8")
start = html.index("<svg ")
end = html.index("</svg>", start) + len("</svg>")
root = ElementTree.fromstring(html[start:end])
print("SVG parsed:", root.tag)
print("Saved file:", Path("index.html").resolve())
PY
```

Returned result: inspect the current contents and parser exit status. A successful parse establishes valid SVG XML; it does not establish that the animation looks correct in a browser. Continue with task-relevant improvements or available visual verification, and describe any remaining limitation accurately.

The useful work survives interruption after any successful stage: another turn can read `index.html` and continue from the saved artifact. No extra checkpoint file is required for this example.

## Example 2: refine a failed exact match

Suppose an existing `notes.txt` contains:

```text
Status: draft

Next: draft the acceptance notes.
```

Round 1 attempts to update the status:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace notes.txt --old 'draft' --new 'ready'
```

Returned result: a nonzero exit status and an ambiguous-match error; the file is unchanged. The word appears in both the status and the next action.

Decision: inspect the relevant current lines before narrowing the match. Do not suppress the error, guess a line number, or rewrite the complete file.

Round 2 reads the current file:

```sh
nl -ba notes.txt
```

Decision after observing the two occurrences: the full status line identifies the intended change.

Round 3 performs that focused change:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace notes.txt --old 'Status: draft' --new 'Status: ready'
```

Returned result: the successful edit shows the changed status and surrounding content. Confirm that the next action remains intact. If the text has changed again, refine the operation from the new observation rather than falling back to an overwrite.
````

## v2 · 2026-10-11 · 跨回合保存、续改与失败恢复

来源：当前工作区（未提交）。现有 tools 和操作脚本接口不变。

- 发现的问题：旧示例将鹈鹕自行车 SVG 动画拆成预定开发阶段，可能使模型把一次具体任务的实现顺序当作通用工作方法。
- 本版本目标：用通用文本操作说明如何依据已保存内容继续修改，以及匹配失败后如何取得反馈并缩小修改范围。
- 本版本效果：替换为创建、插入、局部替换和歧义匹配修正示例；后续操作依赖返回结果，不预设产物类型或回合数。实际模型执行方式待使用观察。

### 完整提示词快照

````markdown
# Editing across model turns

These examples show general file operations with an observation between calls. Each editing call returns control to the model, which can inspect the result and decide what change is needed next. A tool result does not end the user's task. Simple work can use one call, and related changes can share a call; no fixed number of turns or uniform splitting rule applies.

The examples assume the shell starts in the task's working directory and `python3` is available. In every editing call, replace `/absolute/path/from/skill-catalog/file-editing/scripts/edit.py` with the helper path resolved from the loaded SKILL.md. Each call sets its own `edit` variable; no shell variable persists from a previous call. To try the examples without touching task files, use a temporary directory as the shell call's working directory.

Output descriptions are illustrative. Inspect the actual JSON, stderr, and exit status returned by the tool. They are not records of a model run or a task-specific implementation recipe.

## Continue from a saved file

The following exchanges illustrate creation, insertion, and replacement using a small text file. The sample paragraphs stand for content the task actually requires; they are not prescribed milestones for building other artifacts.

Action: save the initial requested contents.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create document.txt <<'TEXT'
Section
Original paragraph.
TEXT
```

Observation: a successful result reports `operation=create`, `changed=true`, the saved path, and a numbered excerpt. If the target already exists, creation fails without overwriting it; read the existing file before deciding how to proceed.

Next decision: use the saved contents as the basis for any further requested change. For example, if another paragraph belongs before the existing one, its unique text is a usable anchor.

Action: insert that paragraph without reconstructing the file.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" insert document.txt --anchor 'Original paragraph.' --position before <<'TEXT'
Additional paragraph.
TEXT
```

Observation: the result identifies the insertion location and shows nearby saved text. Inspect a targeted range if the excerpt is truncated or leaves the result uncertain.

Next decision: an insertion has been saved. If the existing paragraph also needs revision, replace its current exact text. If the requested change is already complete, no additional edit is needed.

Action: perform that replacement when it is required.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace document.txt --old 'Original paragraph.' --new 'Updated paragraph.'
```

Observation: the result shows the changed region. A read of the saved file now yields:

```text
Section
Additional paragraph.
Updated paragraph.
```

The file survives interruption after each successful write. A subsequent turn reads it and continues from those contents, rather than regenerating completed changes from memory. The operation feedback establishes what was saved; task-level correctness may require other checks appropriate to the file and the requested outcome.

## Refine an ambiguous match

For a runnable example, first create a file containing a repeated value:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create values.txt <<'TEXT'
First: value
Second: value
TEXT
```

Suppose the requested change affects only the first entry. A match on the repeated word is insufficient:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace values.txt --old 'value' --new 'updated'
```

Observation: a nonzero exit status and an ambiguous-match error; the file is unchanged. The word appears in both entries.

Next decision: inspect the relevant current contents before narrowing the match. Do not suppress the failure or reconstruct the entire file from a remembered copy.

```sh
nl -ba values.txt
```

Observation: the two numbered lines show that the complete first entry identifies the intended location.

Action: use that unique exact match.

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace values.txt --old 'First: value' --new 'First: updated'
```

Observation: the successful result shows the changed first entry and the preserved second entry. If the current contents no longer contain a unique match, the operation fails without modifying the file; inspect the new contents and choose a matching operation from them.
````
