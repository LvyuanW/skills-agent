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

两种 API 均使用流式接收。正文收到即显示；正文和工具调用按到达顺序记录、渲染，历史回放保持相同顺序。工具调用显示名称及完成状态、失败退出码和输出行数，不展开命令源码或工具 JSON；可读思考标记为“思考”，按到达顺序显示，签名和加密内容不显示。终端输入时显示 `>`，发送后清除临时输入区，显示一次与历史回放相同的 `user:` 消息块；附件只列名称。用户消息的黑底铺满整行，Agent 回复为白底黑字，段落空行同样填充白底；工具摘要为蓝字、终端默认背景。颜色只用于终端，重定向输出及原始日志不含新增的颜色控制码。`history` 使用相同的摘要显示；`history --full` 展开完整公开记录，不调用模型。原始日志始终完整保存，显示摘要不调用模型；工作上下文的压缩与长工具结果报告另有独立模型调用。

交互会话使用可滚动的终端消息区，底部输入行上下各留一行间距，最下方显示“按 F2 选中文本”。输入光标使用终端原生的闪烁竖线，实际效果取决于终端支持。思考正文和标题为灰色字、终端默认背景。思考在流式接收时展开，当前模型响应结束后收起为 `▶ 思考`；点击标题展开或再次收起，恢复会话时默认收起。折叠只影响显示，原始思考与消息顺序完整保留。

鼠标点击需要终端允许鼠标报告；Apple Terminal 默认开启，可在 [View → Allow Mouse Reporting](https://support.apple.com/en-mide/guide/terminal/trmlc69728a5/mac) 中检查。滚轮或 PageUp / PageDown 浏览消息，Ctrl-End 返回最新输出。

按 F2 进入文本选择模式，拖选文字后使用终端的复制快捷键（macOS 为 Cmd-C）；再按 F2 返回。选择期间画面保持不变，模型运行和日志记录继续，返回后显示最新内容并恢复思考标题的点击操作。

右下角“上下文窗口 x%”显示最近一次主模型请求前计量的输入 token 数占有效输入预算的比例，预算取 `--context-tokens` 与模型窗口扣除输出额度后的较小值。它不包含该次新生成的回答，不为显示额外调用计量接口；恢复会话沿用最近一次数值，尚未计量时显示 `—`。

工具通过参数校验并开始执行后，消息区尾部显示带省略号动画的执行状态；上下文压缩和核验期间显示“正在压缩上下文”。结束、失败或中断时清除该状态。动画不写入会话记录，F2 选择期间暂停，返回后恢复。

终端中的 Agent 正文渲染 Markdown，包括标题、加粗、斜体、列表、引用、链接、代码和表格。消息区默认跟随最新输出，向上滚动可以阅读此前内容。`history` 命令直接渲染完整正文和可读思考；重定向输出和日志保留 Markdown 源文。

stdin 每行作为一轮输入，EOF 退出。需要将多行任务作为一次输入并执行到结束时，使用下方的 benchmark 入口。

终端输入使用 prompt-toolkit，支持方向键移动光标、编辑文字和上下键切换本次运行中的输入历史。

Anthropic：

```sh
export ANTHROPIC_API_KEY='你的密钥'
export ANTHROPIC_MODEL='支持工具调用的模型名称'
.venv/bin/skills-agent run --provider anthropic --session claude
```

`--model` 优先于对应的 `OPENAI_MODEL` / `ANTHROPIC_MODEL`。`--base-url` 优先于对应的 `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`，值为包含 API 前缀的根地址，例如 `http://localhost:8000/v1`。OpenAI 官方接入默认使用 `/v1/responses`，并以 `/v1/responses/input_tokens` 计量同一份输入；Anthropic 使用 `/v1/messages` 和 `/v1/messages/count_tokens`。本地兼容服务可以不设置密钥。网关可通过 `--api-format responses` 使用对应接口；Chat 协议需配置与模型匹配的计量方式，不能用 Responses 的计数冒充 Chat 请求计数。

OpenAI 兼容接口返回的 `reasoning_content` 会完整保存，并随保留的助手消息回传，包括未调用工具的回答，以支持 [DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)。可读内容在终端显示为思考；必要的协议状态随保留消息回传。

## 图片和文件

macOS 下在输入行按 **Ctrl-V** 粘贴剪贴板中的图片或已复制的文件，文字则直接插入光标处。附件用简短标记表示：

```text
> 分析 [图片1]，参考 [文件2:报告.pdf]
```

删除标记即可取消该附件，按 Enter 与文字一起发送。图片在粘贴时保存临时副本，发送后与文件一样复制到会话目录；退出时清理临时副本。普通的 Cmd-V 仍由终端处理文字粘贴。

```sh
.venv/bin/skills-agent run --session files \
  --file ./image.png --file ./report.pdf --file ./data.csv

> 结合图片、报告和数据分析差异
> /file "./new data.csv"
> 对比新数据
```

`--file` 和会话内的 `/file PATH` 将文件加入下一条消息的附件，可添加多个；有附件时直接输入空行也可发送。每个附件先完整复制到会话目录。PNG、JPEG、GIF、WebP 和 PDF 使用模型原生输入，需要所选模型及兼容服务支持该格式。UTF-8 文本作为完整文本附件输入；受保护输入超出上下文预算时明确报错，不机械截断。其他格式保留副本路径，由模型编写脚本或使用适合的现有代码读取。

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
  terminal.py        可滚动的交互消息区与思考折叠
  input.py           输入行的剪贴板快捷键与附件标记
  clipboard.py       macOS 剪贴板读取
  markdown.py        终端 Markdown 渲染与流式预览
  benchmark.py       从任务文件运行一次并退出
  runtime.py         两个入口共用的参数与 Agent 构造
  agent.py           同步模型/工具循环、独立子会话
  session/           追加式事件日志、输入附件副本
  context/           持久化工作上下文、模型压缩与核验、原始历史检索及专用 history_skill
  providers/         OpenAI 与 Anthropic 协议转换
  tools/             shell、subagent、sandbox
  skills/            技能发现与系统提示词中的技能目录
  sandbox/           Docker 命令执行
  builtin_skills/    通用 workspace skill 及参考脚本
tests/
adapters/ale/        ALE Deployer 与运行配置
```

执行顺序：记录用户输入 → 构建模型上下文 → 流式接收、记录和显示模型事件 → 响应完整后执行工具并记录结果 → 再次调用模型，直到模型返回最终回答。

### 会话与上下文

实现依据见 [上下文管理设计与调研](docs/context-management-design.md)。

- `events.jsonl` 只追加原始消息、流式事件和完整工具输出；事件 ID 是稳定的字节偏移引用。`attachments/` 保存输入副本，`subagents/` 保存子任务和辅助调用的私有记录。
- `context.json` 保存已挑选的工作记录、摘要、覆盖事件、已处理位置和最近计量。每次只处理新增事件；已压缩内容不会从原始日志重新填回。
- `--context-tokens` 默认 **272000**。每次请求计量系统提示词、工具定义、所有实际发送消息、必要思考状态和媒体。达到 **90%（244800）** 时，以已有摘要和较早的已挑选记录生成新摘要；保留最近 **10 次主模型响应及全部对应工具结果**，另保留最新用户任务原文。辅助模型调用不计入这10次。
- 摘要目标约8192 tokens。生成后独立核验真实性与遗漏，检查来源、原句、用户约束和方案采纳状态；发现问题修订一次并复核。失败不替换旧上下文。连接中断最多尝试3次，不拼接残片，不重跑工具；取消和输出截断不重试。
- `--summary-output-tokens` 默认32768，是辅助生成的输出额度，与摘要目标及报告正文预算分开；需符合实际模型输出能力。`--model-context-window` 指定实际窗口时，输入与输出额度一并检查。最近10组或最新用户任务本身过长时明确报错，不缩短保护范围。
- Anthropic 客户端压缩在协议允许的边界移除失效 thinking 的发送副本，原始历史不变。工具结果尚待回送且需要原签名时延后压缩；硬预算仍不能超出。
- shell/sandbox 必填 `name`，并可选择 `result_mode: auto | direct | report`。`auto` 默认保留不超过8192 tokens的结果，超过则生成2048 tokens以内的报告；`direct` 保留原文，装不下时返回原始事件和行范围；`report` 直接形成有证据的结论。原始结果总是先完整落盘。官方接口对报告正文的独立计量包含少量请求开销，因此是保守预算。
- sub-agent 必填 `name + task`，最终返回 Markdown 报告，说明适用的结论、证据、产物和未解决事项；内部对话不进入主上下文或 UI。`history=true` 时，加载检索专用 history skill，并携带完整工作上下文只读快照搜索父会话；快照装不下就报错，不截断。主 Agent 和普通子任务的技能目录不包含该技能，普通子任务只接收明确交代的输入。
- 普通子任务不继承主对话，`task` 须交代目标、相关约束、必要材料、操作边界和预期交付。主 Agent 根据报告中的证据、执行结果和未解决事项继续工作，定点核验关键或矛盾之处，不盲信完成声明，也不重做整个子任务。
- `history.sqlite` 增量索引原始主会话，支持中文与英文检索、事件/路径精确查找、行范围、邻近记录及工具配对。检索子任务通过查询扩展、回看证据和检查后续修正生成报告，只把最终报告带回主上下文。任何时候均可检索，不必等到压缩后。
- `--max-output-tokens` 默认4096。模型输出截断时保留已收到的公开内容和原始响应，记录错误，不执行不完整的工具调用。

DeepSeek 使用官方请求编码器与匹配的 tokenizer：

```sh
uv sync --extra deepseek
# 从所用模型的官方发布资源取得 tokenizer.json，编码版本必须与模型匹配。
export SKILLS_AGENT_TOKENIZER_PATH='/absolute/path/to/tokenizer.json'
export SKILLS_AGENT_TOKENIZER_ENCODING='v41'  # 使用对应模型版本；另支持 v4
uv run --env-file .env skills-agent run --session demo
```

`api.deepseek.com` 自动选择 Chat 协议和 DeepSeek 计量；其他 DeepSeek 网关还需 `--token-counter deepseek --api-format chat`。配置也可用 `--tokenizer-path` 与 `--tokenizer-encoding`。不使用字符数估算替代；不支持的模型编码或媒体计量会明确报错。官方编码器及模型匹配说明见 [deepseek-recipe](https://github.com/deepseek-ai/deepseek-recipe/blob/main/docs/tokenizer.md)。

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

shell 和 sandbox 必填 `name`，用简短自然语言说明脚本用途，供终端显示。参数尚未收全时显示“接收调用”，完整后显示名称和“调用”，不据此显示“执行中”；收到工具结果后显示完成或失败。断流或中断时保留已经显示的正文，并记录错误，不执行不完整的工具调用。

内置 `workspace` skill 讲解文本读取和检索方法，附带可阅读、改写的参考实现。`context/history_skill` 提供原始会话检索和报告方法，仅在 `subagent(history=true)` 的检索子任务中加载；主 Agent 通过委派获得结论，不直接执行检索流程。主会话索引包含已返回的子任务报告，不索引私有子会话。

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

测试通过本地模拟模型覆盖两种协议：读取技能与参考源码、返回新脚本源码、写入执行、根据错误修改、验证结果并续聊。同时验证多轮交互与恢复历史、长命令实际执行与完整记录、工作上下文增量保存、压缩核验与历史检索、附件编码、工具配对、子会话显示隔离、进程中断清理和 Docker 参数。ALE 测试需要本地官方源码，运行方法见接入说明。模拟模型验证协议及执行链路，不代表真实模型的代码生成质量；未调用远程付费模型，也未验证实际容器启动。

协议依据：[OpenAI token计量](https://developers.openai.com/api/docs/guides/token-counting)、[OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)、[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[OpenAI 流事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[OpenAI 文件输入](https://developers.openai.com/api/docs/guides/file-inputs)、[Anthropic 流事件](https://platform.claude.com/docs/en/build-with-claude/streaming)、[Anthropic 工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Anthropic 图片](https://platform.claude.com/docs/en/build-with-claude/vision)、[Anthropic PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support)。
