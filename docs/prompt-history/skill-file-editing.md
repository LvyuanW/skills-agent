# file-editing 技能提示词版本时间线

当前源文件：`skills_agent/builtin_skills/file-editing/SKILL.md`。本提示词于本次变更新增，没有更早的 Git 修订；后续沿本文追加版本，不覆盖原文。

## v1 · 2026-10-11 · 新增

来源：当前工作区（未提交）。与[主工作提示词 v3](agent-system.md) 一同启用，现有 tools 不变。

- 发现的问题：现有技能偏重读取、调查和修复规范，缺少可直接使用的通用文件编辑能力；模型可能把整份产物的生成视为一个步骤。
- 本版本目标：通过现有 shell 提供创建、精确替换、锚点插入、删除内容和删除文件的操作，说明唯一匹配、输入输出与错误行为，并引导尽早保存有用产物。
- 本版本效果：新增按需发现的内置技能及独立 Python 脚本，已有操作可直接调用，失败时保留文件内容，成功时提供有限反馈；关联修改仍可合并执行。实际对 DeepSeek 执行粒度和长思考的改善待使用观察。

### 完整提示词快照

````markdown
---
name: file-editing
description: Create, modify, and extend text or code files, including HTML and SVG artifacts, through reusable shell scripts and incremental edits.
---

Use this skill when a task needs file creation or editing, including building a new artifact. It provides concrete operations through the existing `shell` tool; it does not add an agent tool. Choose a meaningful next change, save it, and use the observed result to choose subsequent work. For a substantial artifact, save a minimal useful version early rather than drafting its entire implementation before the first write. Simple changes can remain simple, and tightly related edits can share a shell call.

Resolve [scripts/edit.py](scripts/edit.py) against this SKILL.md's directory. Set `edit` to that resolved path in each shell call that needs it; shell variables do not persist between calls. Execute the helper directly with Python 3.11 or newer. Start with `python3 "$edit" --help` or a command's `--help` when needed. Read the source only to understand an unexpected result or customize an operation; routine use does not require rebuilding the helper.

## Operations

| Command | Purpose | Content arguments |
| --- | --- | --- |
| `create PATH` | Create a new UTF-8 file; refuse to overwrite | `--text TEXT`, `--text-file FILE`, or stdin |
| `replace PATH` | Replace one exact, nonempty old string | `--old TEXT` or `--old-file FILE`; replacement from `--new TEXT`, `--new-file FILE`, or stdin |
| `insert PATH` | Insert text beside one exact, nonempty anchor | `--anchor TEXT` or `--anchor-file FILE`, `--position before\|after`; text from `--text TEXT`, `--text-file FILE`, or stdin |
| `delete-content PATH` | Remove one exact, nonempty string | `--old TEXT` or `--old-file FILE` |
| `delete-file PATH` | Remove a regular file | No content argument |

Old strings and anchors must occur exactly once, including overlapping occurrences. A missing or ambiguous match fails without changing the file. Inspect the current text and refine the match; do not recover by replacing the whole file from a remembered copy. Anchors are literal text, not regular expressions. Include enough neighboring content to identify the intended location.

Targets must be regular files, not symlinks; text operations require UTF-8 without NUL bytes. `delete-file` also accepts binary regular files. Parent directories must already exist. Text operations preserve existing content, line endings, and file mode outside the requested change; supplied text keeps its own exact characters. A heredoc adds a final newline, so use `--text`, `--new`, or a payload file if that distinction matters.

Success prints a JSON result with the operation, path, whether it changed the file, and a bounded numbered excerpt when relevant. Errors go to stderr with a nonzero exit status. Inspect the exit status and feedback before proceeding. The excerpt may be truncated, including within a long line; use the returned `location` line and column to target a read when the actual change is outside the excerpt. Choose `shell result_mode=auto` for routine operation summaries and `direct` for skill instructions or exact file text.

## Practical calls

Each example uses `/absolute/path/from/skill-catalog/file-editing/scripts/edit.py` as a placeholder. Replace it with the helper path resolved from the loaded SKILL.md. Each shell call sets its own `edit` variable; there is no setup variable inherited from an earlier call.

Create a small useful file through a quoted heredoc, which keeps `$`, backticks, and backslashes literal:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create notes.md <<'TEXT'
# Build notes

The static scene is ready for review.
TEXT
```

Replace a known string; keep multiline replacement text out of shell escaping:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace notes.md --old 'The static scene is ready for review.' <<'TEXT'
The static scene and wheel animation are ready for review.
TEXT
```

Insert after a unique heading:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" insert notes.md --anchor '# Build notes' --position after <<'TEXT'

Scope: a pelican riding a bicycle.
TEXT
```

Delete a specific piece of content or an authorized file:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" delete-content notes.md --old 'Scope: a pelican riding a bicycle.' &&
python3 "$edit" delete-file obsolete-draft.txt
```

If the desired text already exists in files, pass it directly instead of reading it into a shell variable or rewriting it:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create output.txt --text-file prepared-content.txt &&
python3 "$edit" replace existing.txt --old-file old-fragment.txt --new-file new-fragment.txt
```

For a substantial new artifact, or when repeated edits tend to turn into one giant generation step, read [references/incremental-editing.md](references/incremental-editing.md). It demonstrates separate actions, returned results, and subsequent decisions. Select boundaries that produce useful, inspectable work; the examples are not a required number or sequence of steps.
````

## v2 · 2026-10-11 · 通用文件操作与依赖驱动的编辑

来源：当前工作区（未提交）。现有 tools 和操作脚本接口不变。

- 发现的问题：入口和操作文案夹带 HTML／SVG 场景、车轮动画和自行车任务，容易让文件基础能力与某次产物的开发路线绑定。
- 本版本目标：围绕创建、局部修改与删除的操作契约组织技能，由当前文件和任务要求决定编辑边界。
- 本版本效果：移除特定产物和阶段，保留唯一匹配、失败不写入、原子发布、精确字符与有限反馈；示例改成普通文本操作。实际模型的任务拆分表现待使用观察。

### 完整提示词快照

````markdown
---
name: file-editing
description: Create, modify, extend, and remove files through reusable shell operations, with exact matching and feedback from the saved contents.
---

Use this skill when a task requires creating or changing files. It supplies file operations through the existing `shell` tool. Select operations from the requested change and the file's current contents; the skill does not prescribe an implementation plan, artifact format, or number of editing steps. Related changes may share a call when their inputs are already known. When a later change depends on an earlier result, examine that result before choosing the next edit.

Resolve [scripts/edit.py](scripts/edit.py) against this SKILL.md's directory. Set `edit` to that resolved path in each shell call that needs it; shell variables do not persist between calls. Execute the helper directly with Python 3.11 or newer. Use `python3 "$edit" --help` or a command's `--help` when needed. Read the source to investigate an unexpected result or customize an operation; routine use does not require rebuilding the helper.

## Operations

| Command | Purpose | Content arguments |
| --- | --- | --- |
| `create PATH` | Create a new UTF-8 file; refuse to overwrite | `--text TEXT`, `--text-file FILE`, or stdin |
| `replace PATH` | Replace one exact, nonempty old string | `--old TEXT` or `--old-file FILE`; replacement from `--new TEXT`, `--new-file FILE`, or stdin |
| `insert PATH` | Insert text beside one exact, nonempty anchor | `--anchor TEXT` or `--anchor-file FILE`, `--position before\|after`; text from `--text TEXT`, `--text-file FILE`, or stdin |
| `delete-content PATH` | Remove one exact, nonempty string | `--old TEXT` or `--old-file FILE` |
| `delete-file PATH` | Remove a regular file | No content argument |

Old strings and anchors must occur exactly once, including overlapping occurrences. A missing or ambiguous match fails without changing the file. Inspect the current text and refine the match instead of replacing the whole file from a remembered copy. Anchors are literal text, not regular expressions. Include enough neighboring content to identify the intended location.

Existing targets must be regular files, not symlinks; text operations require UTF-8 without NUL bytes. `delete-file` also accepts binary regular files and never recurses into directories. Parent directories must already exist. Text operations preserve existing content, line endings, and file mode outside the requested change; supplied text keeps its own exact characters. A heredoc adds a final newline, so use an inline argument or a payload file when that distinction matters.

Writes are prepared in a temporary file in the same directory. Creation publishes the complete file without overwriting an existing target; an edit atomically replaces its target. Several operations in a shell call are separate writes, not one transaction. If a later operation fails, inspect the earlier saved changes before continuing.

Success prints a JSON result with the operation, path, whether it changed the file, and a bounded numbered excerpt when relevant. Errors go to stderr with a nonzero exit status. Inspect the exit status and feedback before proceeding. The excerpt may be truncated, including within a long line; use the returned `location` line and column to target a read when the actual change is outside the excerpt. Choose `shell result_mode=auto` for routine operation summaries and `direct` for skill instructions or exact file text.

## Practical calls

Each example uses `/absolute/path/from/skill-catalog/file-editing/scripts/edit.py` as a placeholder. Replace it with the helper path resolved from the loaded SKILL.md. Each shell call sets its own `edit` variable; there is no setup variable inherited from an earlier call. The following commands illustrate independent operations on ordinary text. They do not define a workflow for a particular kind of task.

Create a file through a quoted heredoc, which keeps `$`, backticks, and backslashes literal:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create notes.txt <<'TEXT'
Heading
Original paragraph.
TEXT
```

Replace one known string. For multiline text, stdin or a payload file avoids complex shell escaping:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" replace notes.txt --old 'Original paragraph.' --new 'Revised paragraph.'
```

Insert a complete line before a unique anchor. The supplied newline becomes part of the inserted content:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" insert notes.txt --anchor 'Revised paragraph.' --position before <<'TEXT'
Additional paragraph.
TEXT
```

Remove a specified fragment:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" delete-content notes.txt --old 'Additional paragraph.'
```

Remove a file when its removal is part of the requested change:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" delete-file notes.txt
```

If the desired content already exists in files, pass it directly instead of copying it into a shell variable. Here the three payload files and the target `existing.txt` must already exist:

```sh
edit='/absolute/path/from/skill-catalog/file-editing/scripts/edit.py'
python3 "$edit" create output.txt --text-file prepared-content.txt &&
python3 "$edit" replace existing.txt --old-file old-fragment.txt --new-file new-fragment.txt
```

For examples of using saved contents across separate calls and refining a failed exact match, read [references/incremental-editing.md](references/incremental-editing.md). Choose editing boundaries according to the requested change, useful saved results, and dependencies between operations. The examples are not a required number or sequence of steps.
````
