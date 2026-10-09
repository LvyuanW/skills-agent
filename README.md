# skills-agent

Python CLI Agent。模型阅读 `SKILL.md` 和参考脚本源码，针对任务自行编写脚本，通过 shell 执行并根据结果修正。核心负责会话、上下文、模型调用和工具执行。

## 运行

需要 Python 3.11+，运行依赖为 PyYAML、Rich 和 prompt-toolkit。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .

export OPENAI_API_KEY='你的密钥'
export OPENAI_MODEL='支持工具调用的模型名称'
.venv/bin/skills-agent run --session demo

# 进入后输入自然语言，每行一轮；/exit 或 Ctrl-D 退出
> 检查当前工作目录
> 继续分析刚才的结果
> /exit

# 回放主会话，无需 API 配置
.venv/bin/skills-agent history --session demo

# 展开原始公开消息、命令源码和工具输出
.venv/bin/skills-agent history --session demo --full
```

也可以使用 `.venv/bin/python -m skills_agent`。`run --session demo` 创建或恢复名为 `demo` 的会话，先按时间顺序回放主会话，再等待输入，回答后继续留在会话中。启动命令不接受消息参数。Ctrl-C 中断当前任务并停止正在运行的脚本，随后可继续输入。

两种 API 均使用流式接收。正文收到即显示；正文和工具调用按到达顺序记录、渲染，历史回放保持相同顺序。工具调用只显示摘要及完成状态、失败退出码和输出行数，不展开命令源码、工具 JSON 或思考字段。终端输入时显示 `>`，发送后清除临时输入区，显示一次与历史回放相同的 `user:` 消息块；附件只列名称。用户输入为黑底白字，Agent 回复为白底黑字，工具摘要为白底蓝字。颜色只用于终端，重定向输出及原始日志不含新增的颜色控制码。`history` 使用相同的摘要显示；`history --full` 展开完整公开记录，不调用模型。原始日志始终完整保存，摘要只影响显示，不额外调用模型。

终端中的 Agent 正文渲染 Markdown，包括标题、加粗、斜体、列表、引用、链接、代码和表格。流式接收时更新当前正文的预览；正文超过一屏时显示末尾，结束或出现下一条工具摘要时，完整正文进入终端回滚历史。历史回放直接渲染完整正文。重定向输出和日志保留 Markdown 源文。

stdin 每行作为一轮输入，EOF 退出。需要将多行任务作为一次输入并执行到结束时，使用下方的 benchmark 入口。

终端输入使用 prompt-toolkit，支持方向键移动光标、编辑文字和上下键切换本次运行中的输入历史。

Anthropic：

```sh
export ANTHROPIC_API_KEY='你的密钥'
export ANTHROPIC_MODEL='支持工具调用的模型名称'
.venv/bin/skills-agent run --provider anthropic --session claude
```

`--model` 优先于对应的 `OPENAI_MODEL` / `ANTHROPIC_MODEL`。`--base-url` 优先于对应的 `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`，值为包含 API 前缀的根地址，例如 `http://localhost:8000/v1`。默认端点分别为 OpenAI `/v1/chat/completions` 和 Anthropic `/v1/messages`。本地兼容服务可以不设置密钥。

## 图片和文件

```sh
.venv/bin/skills-agent run --session files \
  --file ./image.png --file ./report.pdf --file ./data.csv

> 结合图片、报告和数据分析差异
> /file "./new data.csv"
> 对比新数据
```

`--file` 和会话内的 `/file PATH` 将文件加入下一条消息的附件，可添加多个；有附件时直接输入空行也可发送。每个附件先完整复制到会话目录。PNG、JPEG、GIF、WebP 和 PDF 使用模型原生输入，需要所选模型及兼容服务支持该格式。UTF-8 文本作为文本附件输入，过长时模型只收到摘录及完整副本路径。其他格式保留副本路径，由模型编写脚本或使用适合的现有代码读取。

`--workspace` 指定 shell 工作目录，默认当前目录。附件的相对路径按工作目录解析。`--sessions-dir` 指定会话存储位置，默认 `<workspace>/.agent/sessions`。

## 单次任务与 ALE

```sh
.venv/bin/python -m skills_agent.benchmark \
  --prompt-file /path/to/prompt.txt \
  --workspace /path/to/task \
  --log-dir /path/to/run-logs
```

完整任务从 UTF-8 文件读取，执行到模型返回最终回答后退出。模型、技能和附件参数与 `run` 相同。每次运行使用独立的 `<log-dir>/session`，已存在时直接报错。退出码为成功 `0`、失败 `1`、中断 `130`。接到 SIGTERM 时停止当前脚本并退出。

[ALE 接入说明](adapters/ale/README.md) 包含可复制到 ALE 仓库的 Deployer、Agent 配置及单任务 smoke 配置。适配器在 ALE 的任务 sandbox 内安装并运行本项目，产物由 Agent 按题目指定路径写入，日志写入 ALE 的 `executor.work_dir`。

## 模块

```text
skills_agent/
  cli.py             命令行输入、摘要显示与完整记录回放
  markdown.py        终端 Markdown 渲染与流式预览
  benchmark.py       从任务文件运行一次并退出
  runtime.py         两个入口共用的参数与 Agent 构造
  agent.py           同步模型/工具循环、独立子会话
  session/           追加式事件日志、输入附件副本
  context/           历史选择、提取摘要、预算裁剪
  providers/         OpenAI 与 Anthropic 协议转换
  tools/             shell、subagent、sandbox
  skills/            技能发现与系统提示词中的技能目录
  sandbox/           Docker 命令执行
  builtin_skills/    内置 workspace skill 及其脚本
tests/
adapters/ale/        ALE Deployer 与运行配置
```

执行顺序：记录用户输入 → 构建模型上下文 → 流式接收、记录和显示模型事件 → 响应完整后执行工具并记录结果 → 再次调用模型，直到模型返回最终回答。

### 会话与上下文

- `events.jsonl` 只追加，按接收顺序保存正文和工具调用片段，响应结束后保存完整消息及原始 API 分片 `_raw.stream_events`，完整消息用于构建模型上下文，显示时不重复输出。工具参数、工具输出和中断前收到的内容均保留。附件在 `attachments/`，子会话在 `subagents/`。
- 终端显示主 Agent 的对话及中间步骤摘要。sub-agent 的调用细节、内部对话及直接返回值不展示，只显示目标摘要，例如 `sub-agent 处理了：统计文件`，`history --full` 也遵循此规则。主 Agent 使用返回结果完成最终回答。
- sub-agent 与主 Agent 共享工作目录、技能和模型配置；拥有独立历史和上下文，只接收 `task` 中显式给出的信息，只返回最终结果。执行是同步的，子 Agent 不再创建子 Agent。
- 模型上下文从日志构建，去掉 `_raw`。超出预算时压缩已执行的长命令源码、工具输出和文本附件，再按近期与关键词相关性选择历史，对遗漏内容生成提取式摘要。命令摘录标明已经执行，并通过工具调用 ID 指向会话中的完整原文；实际执行和原始日志使用完整命令。摘要不额外调用模型。
- 工具调用与对应结果作为整体选择，当前用户输入与最新完整工具调用组保留。不完整的工具组不进入模型上下文，原始日志不变。
- `--context-chars` 默认 48000，统计规范化消息、系统指令和工具定义的 JSON 字符数。它不是精确 token 上限；原生图片、PDF 只计元数据，实际媒体 token 由模型服务计算。不可裁剪的当前输入超预算时直接报错。
- `--max-output-tokens` 默认 4096。模型输出截断时保留已收到的正文供显示和回放，记录错误并停止，不执行不完整的工具调用。

### Skills 和 scripts

默认发现内置技能和 `<workspace>/skills`，可重复传入 `--skill-dir` 增加目录。启动时只读取 YAML 元数据，将名称、描述和路径放入系统提示词。模型通过 shell 按需读取 `SKILL.md` 和相关脚本源码。相同名称的不同技能会报错。

```text
skills/my-skill/
  SKILL.md
  scripts/example.py
```

`SKILL.md`：

```markdown
---
name: my-skill
description: 描述这个技能能处理的具体任务及适用条件。
---
说明完成这类任务的方法、输入输出约束和注意事项。
阅读 scripts/example.py，理解其中的数据处理方法，再针对当前任务编写代码。
可以复制、修改或复用相关实现。根据执行结果检查并修正自己的脚本。
```

`SKILL.md` 提供知识与方法，`scripts/` 提供参考源码。模型自行决定实现，代码可以内联执行，也可以写成工作目录中的文件再运行。例如，shell 的 `command` 可以直接包含模型编写的代码：

```sh
cat > analyze.py <<'PY'
import csv
from pathlib import Path

with Path("data.csv").open(encoding="utf-8", newline="") as source:
    rows = list(csv.DictReader(source))
print({"rows": len(rows), "columns": list(rows[0]) if rows else []})
PY
python3 analyze.py
```

脚本可使用当前环境中安装的语言与库，无需注册、固定入口或统一返回格式。shell 返回退出码、stdout 和 stderr，模型据此继续修改代码。配置的技能说明作为任务指导，普通输入资料与执行结果作为数据。

shell 和 sandbox 可附带 `summary`，用简短自然语言说明此次操作的目的，供终端显示。它不影响脚本执行；参数尚未收全时显示“接收调用”，完整后显示摘要，未提供摘要时仅显示调用及完成状态。断流或中断时保留已经显示的正文，并记录错误，不执行不完整的工具调用。

内置 `workspace` skill 讲解文本读取和检索方法，附带可阅读、改写的参考实现。会话历史使用其中的 `history` 辅助命令读取，保留公开记录并排除隐藏的子会话细节。

### Sandbox

```sh
.venv/bin/skills-agent run --sandbox-image python:3.12-slim
> 在 sandbox 中运行 Python 脚本分析当前目录数据
```

提供 `--sandbox-image` 才注册 `sandbox` 工具，需要可运行的 Docker。每次调用启动一个临时容器：禁用网络，工作目录读写挂载，外部技能和会话附件目录只读挂载。脚本依赖需预装在指定镜像中。`shell` 始终在宿主机执行；模型通过 `sandbox` 工具明确选择容器执行，容器失败会返回错误。

## 验证

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
```

测试通过本地模拟模型覆盖两种协议：读取技能与参考源码、返回新脚本源码、写入执行、根据错误修改、验证结果并续聊。同时验证多轮交互与恢复历史、长命令实际执行与完整记录、模型上下文裁剪、附件编码、工具配对、子会话显示隔离、进程中断清理和 Docker 参数。ALE 测试需要本地官方源码，运行方法见接入说明。模拟模型验证协议及执行链路，不代表真实模型的代码生成质量；未调用远程付费模型，也未验证实际容器启动。

协议依据：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[OpenAI 流事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[OpenAI 文件输入](https://developers.openai.com/api/docs/guides/file-inputs)、[Anthropic 流事件](https://platform.claude.com/docs/en/build-with-claude/streaming)、[Anthropic 工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Anthropic 图片](https://platform.claude.com/docs/en/build-with-claude/vision)、[Anthropic PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support)。
