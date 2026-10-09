# skills-agent

Python CLI Agent。能力由 `SKILL.md` 和脚本提供，核心只负责会话、上下文、模型调用和工具执行。

## 运行

需要 Python 3.11+，唯一运行依赖是 PyYAML。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .

export OPENAI_API_KEY='你的密钥'
export OPENAI_MODEL='支持工具调用的模型名称'
.venv/bin/skills-agent run --session demo '检查当前工作目录'

# 使用同一会话继续，或从 stdin 接收任务
.venv/bin/skills-agent run --session demo '继续分析刚才的结果'
printf '列出结论\n' | .venv/bin/skills-agent run --session demo

# 显示完整主会话历史，无需 API 配置
.venv/bin/skills-agent history --session demo
```

也可以使用 `.venv/bin/python -m skills_agent`。每次 `run` 输出本次新增记录；`history` 按顺序回放整个主会话，不压缩、不裁剪正文或工具输出。

Anthropic：

```sh
export ANTHROPIC_API_KEY='你的密钥'
export ANTHROPIC_MODEL='支持工具调用的模型名称'
.venv/bin/skills-agent run --provider anthropic --session claude '检查当前工作目录'
```

`--model` 优先于对应的 `OPENAI_MODEL` / `ANTHROPIC_MODEL`。`--base-url` 优先于对应的 `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`，值为包含 API 前缀的根地址，例如 `http://localhost:8000/v1`。默认端点分别为 OpenAI `/v1/chat/completions` 和 Anthropic `/v1/messages`。本地兼容服务可以不设置密钥。

## 图片和文件

```sh
.venv/bin/skills-agent run --session files \
  --file ./image.png --file ./report.pdf --file ./data.csv \
  '结合图片、报告和数据分析差异'
```

每个附件先完整复制到会话目录。PNG、JPEG、GIF、WebP 和 PDF 使用模型原生输入，需要所选模型及兼容服务支持该格式。UTF-8 文本作为文本附件输入，过长时模型只收到摘录及完整副本路径。其他格式保留副本路径，交给 skill 中的脚本读取。

`--workspace` 指定 shell 工作目录，默认当前目录。命令行文件路径相对启动 CLI 时的目录解析。`--sessions-dir` 指定会话存储位置，默认 `<workspace>/.agent/sessions`。

## 模块

```text
skills_agent/
  cli.py             命令行输入与完整主会话回放
  agent.py           同步模型/工具循环、独立子会话
  session/           追加式事件日志、输入附件副本
  context/           历史选择、提取摘要、预算裁剪
  providers/         OpenAI 与 Anthropic 协议转换
  tools/             read_skill、shell、subagent、sandbox
  skills/            技能目录发现与按需读取
  sandbox/           Docker 命令执行
  builtin_skills/    内置 workspace skill 及其脚本
tests/
```

执行顺序：记录用户输入 → 构建模型上下文 → 调用模型 → 记录回答和工具调用 → 执行工具并记录结果 → 再次调用模型，直到模型返回最终回答。

### 会话与上下文

- `events.jsonl` 只追加，保存完整原始文本、工具参数、工具输出及模型原始响应 `_raw`。附件在 `attachments/`，子会话在 `subagents/`。
- 终端显示主 Agent 的对话和工具交互。sub-agent 的调用细节、内部对话及直接返回值不展示，只显示目标摘要，例如 `sub-agent 处理了：统计文件`。主 Agent 使用返回结果完成最终回答。
- sub-agent 与主 Agent 共享工作目录、技能和模型配置；拥有独立历史和上下文，只接收 `task` 中显式给出的信息，只返回最终结果。执行是同步的，子 Agent 不再创建子 Agent。
- 模型上下文从日志构建，去掉 `_raw`。超出预算时先压缩长工具输出和文本附件，再按近期与关键词相关性选择历史，对遗漏内容生成提取式摘要。摘要不额外调用模型。
- 工具调用与对应结果作为整体选择，当前用户输入与最新完整工具调用组保留。不完整的工具组不进入模型上下文，原始日志不变。
- `--context-chars` 默认 48000，统计规范化消息、系统指令和工具定义的 JSON 字符数。它不是精确 token 上限；原生图片、PDF 只计元数据，实际媒体 token 由模型服务计算。不可裁剪的当前输入超预算时直接报错。
- `--max-output-tokens` 默认 4096。模型输出截断时保留已收到的正文供显示和回放，记录错误并停止，不执行不完整的工具调用。

### Skills 和 scripts

默认发现内置技能和 `<workspace>/skills`，可重复传入 `--skill-dir` 增加目录。启动时只读取 YAML 元数据，将名称、描述和路径提供给模型；`read_skill` 按需读取完整正文。相同名称的不同技能会报错。

```text
skills/my-skill/
  SKILL.md
  scripts/task.py
```

`SKILL.md`：

```markdown
---
name: my-skill
description: 描述这个技能能处理的具体任务及适用条件。
---
使用 shell 执行本技能目录下的 scripts/task.py。说明脚本参数及结果格式。
```

业务能力写在技能和脚本中，不增加专用工具。内置 `workspace` skill 的脚本支持按行读取、文本检索和读取会话历史；历史使用专门的 `history` 子命令，排除隐藏的子会话细节。

### Sandbox

```sh
.venv/bin/skills-agent run --sandbox-image python:3.12-slim \
  '在 sandbox 中运行 Python 脚本分析当前目录数据'
```

提供 `--sandbox-image` 才注册 `sandbox` 工具，需要可运行的 Docker。每次调用启动一个临时容器：禁用网络，工作目录读写挂载，外部技能和会话附件目录只读挂载。脚本依赖需预装在指定镜像中。`shell` 始终在宿主机执行；模型通过 `sandbox` 工具明确选择容器执行，容器失败会返回错误。

## 验证

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖真实 shell 脚本执行、两种协议的本地 HTTP 端到端交互、附件编码、会话续聊、历史不变性、上下文预算与工具配对、子会话显示隔离、Docker 参数。未调用远程付费模型；本机 Docker daemon 未运行，未验证实际容器启动。

协议依据：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[OpenAI 文件输入](https://developers.openai.com/api/docs/guides/file-inputs)、[Anthropic 工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Anthropic 图片](https://platform.claude.com/docs/en/build-with-claude/vision)、[Anthropic PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support)。
