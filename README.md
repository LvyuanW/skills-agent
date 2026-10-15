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

`run` 和 `benchmark` 自动读取所选工作目录下的 `skills-agent.toml`，没有该文件时使用环境变量及内置默认值。项目根目录已提供：

```toml
context_tokens = 524000
model_context_window = 524000
max_output_tokens = 65536
summary_output_tokens = 8192
```

四项依次是工作上下文预算、模型实际总窗口、单次输出额度（含思考）、辅助生成输出额度。修改文件后启动即可：

```sh
uv run --env-file .env skills-agent run --session demo

# Override one value for this run.
uv run --env-file .env skills-agent run --session demo --max-output-tokens 32768
```

优先级为 **显式命令行参数 > 配置文件 > 已支持的环境变量 > 内置默认值**。`--config /path/to/agent.toml` 可指定另一份文件，只读取这一份，不合并多份配置。相对 `--config` 路径按启动目录解析。会话名、工作目录和输入附件仍通过启动参数指定。

配置文件还可填写 `provider`、`model`、`base_url`、`api_format`、`token_counter`、`tokenizer_path`、`tokenizer_encoding`、`skill_dirs` 和 `sandbox_image`。文件中的 `tokenizer_path` 和 `skill_dirs` 相对配置文件所在目录解析；命令行及环境变量中的路径仍相对启动目录。`--skill-dir` 覆盖文件中的额外技能目录列表。API key 只从环境变量读取，可继续放在 `.env` 中，不写入 TOML。

工作上下文默认预算为 **1M（1000000 tokens）**，各项 token 额度均可独立设置为正整数。有效输入预算取 `min(context_tokens, model_context_window - max_output_tokens)`；未设置实际窗口时使用 `context_tokens`。项目配置下，输入预算和压缩触发阈值均为 **458464 tokens**。配置数值仍需符合所用模型的实际能力。

两种 API 均使用流式接收。正文收到即显示；正文和工具调用按到达顺序记录、渲染，历史回放保持相同顺序。工具调用显示名称及完成状态、失败退出码和输出行数，不展开命令源码或工具 JSON；可读思考标记为“思考”，按到达顺序显示，签名和加密内容不显示。终端输入时显示 `>`，发送后清除临时输入区，显示一次与历史回放相同的 `user:` 消息块；附件只列名称。用户消息的黑底铺满整行，Agent 回复为白底黑字，段落空行同样填充白底；工具摘要为蓝字、终端默认背景。颜色只用于终端，重定向输出及原始日志不含新增的颜色控制码。`history` 使用相同的摘要显示；`history --full` 展开完整公开记录，不调用模型。原始日志始终完整保存，显示摘要不调用模型；工作上下文的压缩与长工具结果报告另有独立模型调用。

交互会话使用可滚动的终端消息区，底部输入行上下各留一行间距，最下方显示“按 F2 选中文本”。输入行上方显示本轮耗时，从发送消息起计时，包含工具和子 Agent 的执行时间，精确到 0.1 秒；整轮结束或中断后保留最终耗时，下一轮开始时切换到该轮计时，恢复会话时不回放计时。输入光标使用终端原生的闪烁竖线，实际效果取决于终端支持。思考正文和标题为灰色字、终端默认背景。思考在流式接收时展开，当前模型响应结束后收起为 `▶ 思考`；点击标题展开或再次收起，恢复会话时默认收起。折叠只影响显示，原始思考与消息顺序完整保留。

鼠标点击需要终端允许鼠标报告；Apple Terminal 默认开启，可在 [View → Allow Mouse Reporting](https://support.apple.com/en-mide/guide/terminal/trmlc69728a5/mac) 中检查。滚轮或 PageUp / PageDown 浏览消息，Ctrl-End 返回最新输出。

工具返回图片时，其结果下方显示蓝色带下划线的 `[图片]`；多张图片依次显示 `[图片1]`、`[图片2]`。在交互会话中单击即可用系统默认应用打开工具返回的原路径，恢复会话后也可点击。原文件已移动或删除时显示打开失败，不查找文件或改用会话副本。F2 选择模式下不触发打开；剪贴板附件维持原有显示。独立 `history` 命令和重定向输出显示图片标签与路径，方便复制。

运行期间，耗时右侧显示蓝字“停止”，鼠标移入变红；点击与 Ctrl-C 使用同一中断流程，停止本轮模型接收和正在执行的工具。首次触发后显示“停止中”，清理完成前不重复中断。已收到的正文和思考留在原始会话中；中断正文进入工作上下文，并标记为未完成，不把未完成的工具参数或思考状态作为可续接的响应。已经执行的文件修改不回滚。

模型运行时仍可输入和粘贴附件。提交的消息按顺序排队，在输入行正上方各显示一行预览，超长内容以省略号结尾，右侧蓝字显示“排队中”；鼠标移到状态文字上变为红字“撤回”，点击将该条移出队列，不改动正在输入的草稿。当前轮结束或停止后依次发送，届时才写入会话并展示完整用户消息。排队保存在内存中；该条消息的总耗时包含等待时间。

按 F2 进入文本选择模式，拖选文字后使用终端的复制快捷键（macOS 为 Cmd-C）；再按 F2 返回。选择期间画面保持不变，模型运行和日志记录继续，返回后显示最新内容并恢复思考标题的点击操作。

右下角“上下文窗口 x%”显示最近一次主模型请求前计量的输入 token 数占有效输入预算的比例，预算取 `--context-tokens` 与模型窗口扣除输出额度后的较小值。它不包含该次新生成的回答，不为显示额外调用计量接口；恢复会话沿用最近一次数值，尚未计量时显示 `—`。

工具通过参数校验并开始执行后，消息区尾部显示带省略号动画的执行状态；上下文压缩期间显示“正在压缩上下文”。结束、失败或中断时清除该状态。动画不写入会话记录，F2 选择期间暂停，返回后恢复。

压缩状态行显示当前阶段、批次和流式生成的最新摘要片段，使用蓝字和终端默认背景。预览保持一行，超出宽度时显示末尾片段和省略号；等待模型时保留动画。预览只用于临时状态行，完整过程仍保存在压缩日志中，不增加模型调用。子 Agent 内部压缩只向主界面提供阶段。

子 Agent 状态行显示任务名、实际阶段（执行中、正在执行工具、正在压缩上下文）和运行秒数，精确到 0.1 秒，阶段切换不重置计时。子 Agent 的内部对话、工具名称、参数和输出不展示；返回时显示“已返回报告”，失败或取消时显示“失败”或“已中断”。阶段和计时只用于实时显示，不进入会话记录或模型上下文。

终端中的 Agent 正文渲染 Markdown，包括标题、加粗、斜体、列表、引用、链接、代码和表格。消息区默认跟随最新输出，向上滚动可以阅读此前内容。`history` 命令直接渲染完整正文和可读思考；重定向输出和日志保留 Markdown 源文。

stdin 每行作为一轮输入，EOF 退出。需要将多行任务作为一次输入并执行到结束时，使用下方的 benchmark 入口。

终端输入使用 prompt-toolkit，支持方向键移动光标、编辑文字和上下键切换本次运行中的输入历史。

Anthropic：

```sh
export ANTHROPIC_API_KEY='你的密钥'
export ANTHROPIC_MODEL='支持工具调用的模型名称'
.venv/bin/skills-agent run --provider anthropic --session claude
```

未在命令行或配置文件指定时，模型名称读取对应的 `OPENAI_MODEL` / `ANTHROPIC_MODEL`，API 地址读取 `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`。`base_url` 为包含 API 前缀的根地址，例如 `http://localhost:8000/v1`。OpenAI 官方接入默认使用 `/v1/responses`，并以 `/v1/responses/input_tokens` 计量同一份输入；Anthropic 使用 `/v1/messages` 和 `/v1/messages/count_tokens`。本地兼容服务可以不设置密钥。网关可通过 `api_format = "responses"` 使用对应接口；Chat 协议需配置与模型匹配的计量方式，不能用 Responses 的计数冒充 Chat 请求计数。

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

完整任务从 UTF-8 文件读取，执行到模型返回最终回答后退出。配置文件的读取规则、模型、技能和附件参数与 `run` 相同。每次运行使用独立的 `<log-dir>/session`，已存在时直接报错。退出码为成功 `0`、失败 `1`、中断 `130`。正常结束、失败或接到 SIGTERM 时，均清理当前脚本及通过 process-management 启动的本任务后台进程，再退出；benchmark 不允许创建跨任务保留的服务。

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
  config.py          TOML 配置读取、校验与参数优先级
  runtime.py         两个入口共用的参数与 Agent 构造
  agent.py           同步模型/工具循环、独立子会话
  session/           追加式事件日志、输入附件副本
  context/           持久化工作上下文、模型压缩与程序检查、原始历史检索及专用 history_skill
  providers/         OpenAI 与 Anthropic 协议转换
  tools/             shell、subagent、sandbox
  skills/            技能发现与系统提示词中的技能目录
  sandbox/           Docker 命令执行
  builtin_skills/    通用读取、编辑、反馈、调查、修复、进程管理、验收标准和交付审查技能
tests/
adapters/ale/        ALE Deployer 与运行配置
```

执行顺序：记录用户输入 → 构建模型上下文 → 流式接收、记录和显示模型事件 → 响应完整后执行工具并记录结果 → 再次调用模型，直到模型返回最终回答。

### 会话与上下文

实现依据见 [上下文管理设计与调研](docs/context-management-design.md)。

- `events.jsonl` 只追加原始消息、流式事件和完整工具输出；事件 ID 是稳定的字节偏移引用。`attachments/` 保存输入副本，`subagents/` 保存子任务和辅助调用的私有记录。
- `context.json` 保存已挑选的工作记录、摘要、覆盖事件、已处理位置和最近计量。每次只处理新增事件；已压缩内容不会从原始日志重新填回。
- 会话在工具调用与结果之间被强制结束时，下一次准备请求会给这些调用补记中断结果，使记录组可发送。补记记录是派生状态：ID 形如 `recovered-<原调用事件偏移>-<调用 ID>`，含原响应因而跨响应唯一；记录自身带 `recovered_from` 指向可回查的原调用事件。派生记录由程序生成，摘要不能将它当作用户原话。调用 ID 中的其他字符会被替换，并始终附 8 位摘要（对原始调用 ID 取 sha256 前缀），使不同调用 ID 得到不同引用。补记只进入工作上下文，原始日志不改。只修复被后续记录越过的组；未被越过的尾组仍按原样拒绝发送。同一响应内重复的调用 ID 仍由记录组校验报错。
- `--context-tokens` 默认 **1000000**。每次请求计量系统提示词、工具定义、所有实际发送消息、必要思考状态和媒体。达到配置预算的 **90%（默认 900000）** 时压缩；若有效输入预算更小，则提前在该上限触发。以已有摘要和较早的已挑选记录生成新摘要；保留最近 **10 次主模型响应及全部对应工具结果**，另保留最新用户任务原文。辅助模型调用不计入这10次。
- 压缩摘要关闭思考，默认生成上限为8192 tokens，提示词要求正文和引用合计在本次额度内完成。用旧摘要和本次移出的工作记录生成新摘要；材料过长时按完整调用组分批合并。摘要期望格式为 `{"summary": "正文，可使用【R1】", "references": {"R1": ["event-123"]}}`；引用作为原文定位线索，JSON 格式、引用或引文错误不阻断压缩，旧 Markdown 摘要仍可继续使用。程序直接保存模型返回文本，不调用独立模型核验。达到生成上限而正常结束时，已返回的非空文本仍可采用，结束原因保存在私有日志中。候选请求仍超预算时，在同一摘要流程中继续缩短摘要；装入预算后直接替换，不要求 token 数严格减少。网络断流、取消或程序异常时保留旧上下文；辅助生成和远程 token 计量遇到可重试故障时，每次失败后固定等待5秒，无次数上限；不拼接残片，不重跑工具。Ctrl-C、进程退出和 ALE 等外层运行时限仍可终止等待。
- `--summary-output-tokens` 默认8192，是辅助生成的输出额度，与工具报告正文预算分开；需符合实际模型输出能力。摘要请求的实际额度同时受模型剩余窗口和主请求可容纳摘要的空间约束。最近10组、最新用户任务或固定输入本身过长时明确报告预算限制，不缩短保护范围。
- Anthropic 客户端压缩在协议允许的边界移除失效 thinking 的发送副本，原始历史不变。工具结果尚待回送且需要原签名时延后压缩；硬预算仍不能超出。
- shell/sandbox 必填 `name`，并可选择 `result_mode: auto | direct | report`。`auto` 默认保留不超过8192 tokens的结果，超过则生成2048 tokens以内的报告；`direct` 保留原文，装不下时返回原始事件和行范围；`report` 直接形成有证据的结论。工具报告的生成与计量均关闭思考，截断正文不作为完整报告采用；报告失败时返回明确的 `report_error`、原执行状态及原始结果读取位置，保留图片，当前任务继续。原始结果总是先完整落盘，取消仍立即中断。官方接口对报告正文的独立计量包含少量请求开销，因此是保守预算。
- sub-agent 必填 `name + task`，最终返回 Markdown 报告，说明适用的结论、证据、产物和未解决事项；内部对话不进入主上下文或 UI。`history=true` 时，加载检索专用 history skill，并携带完整工作上下文只读快照搜索父会话；快照装不下就报错，不截断。主 Agent 和普通子任务的技能目录不包含该技能，普通子任务只接收明确交代的输入。
- 普通子任务和历史检索子任务均继承主 Agent 的上下文预算配置。普通子任务不继承主对话，`task` 须交代目标、相关约束、必要材料、操作边界和预期交付。主 Agent 根据报告中的证据、执行结果和未解决事项继续工作，定点核验关键或矛盾之处，不盲信完成声明，也不重做整个子任务。
- `history.sqlite` 增量索引原始主会话，支持中文与英文检索、事件/路径精确查找、行范围、邻近记录及工具配对。检索子任务通过查询扩展、回看证据和检查后续修正生成报告，只把最终报告带回主上下文。任何时候均可检索，不必等到压缩后。
- `max_output_tokens` / `--max-output-tokens` 默认32768，包含思考和回答。主模型输出截断时保留已收到的公开内容和原始响应，记录错误，不执行不完整的工具调用。

DeepSeek 使用官方请求编码器与匹配的 tokenizer：

```sh
uv sync --extra deepseek
# 从所用模型的官方发布资源取得 tokenizer.json，编码版本必须与模型匹配。
export SKILLS_AGENT_TOKENIZER_PATH='/absolute/path/to/tokenizer.json'
export SKILLS_AGENT_TOKENIZER_ENCODING='v41'  # 使用对应模型版本；另支持 v4
uv run --env-file .env skills-agent run --session demo
```

`api.deepseek.com` 自动选择 Chat 协议和 DeepSeek 计量；其他 DeepSeek 网关还需 `--token-counter deepseek --api-format chat`。配置也可用 `--tokenizer-path` 与 `--tokenizer-encoding`。不使用字符数估算替代；不支持的模型编码或媒体计量会明确报错。官方编码器及模型匹配说明见 [deepseek-recipe](https://github.com/deepseek-ai/deepseek-recipe/blob/main/docs/tokenizer.md)。

当前 DeepSeek 官方 Chat 链路支持在 `.env` 中设置小写 `effort`：

```dotenv
effort=low
```

可选 `low`、`high`、`max`，分别控制思考强度；`none` 关闭思考。未设置或留空时不传思考控制参数，使用服务端默认值；其他非空值会在启动时明确报错。此字段目前仅从环境变量读取，不提供 TOML 或命令行选项，仅对 `provider=openai`、主机名为 `api.deepseek.com` 的 Chat 接口生效（包含 `/v1` 地址），其他接口忽略它。

通过 `uv run --env-file .env skills-agent run --session demo` 加载，修改后需重启进程。子 Agent 和工具报告继承该设置；上下文压缩摘要仍按原有规则关闭思考。计量与生成使用相同思考设置。`effort` 控制思考强度，`max_output_tokens` 仍限制单次总输出（含思考），两者用途不同。参数语义见 [DeepSeek 思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)。

### Skills 和 scripts

默认发现内置技能和 `<workspace>/skills`，可重复传入 `--skill-dir` 增加目录。启动时只读取 YAML 元数据，将名称、描述和路径放入系统提示词。选择引导要求模型在任务开始、下一步工作变化和准备交付时，按当前需求选择相关技能，通过 shell 读取 `SKILL.md`，再按需读取参考资料和脚本源码；上下文中已有的说明可直接复用。相同名称的不同技能会报错。

本项目的内置技能按可复用基础能力组织，说明操作契约、选择依据和能力边界。示例演示接口及依据反馈继续工作的方式；实际产物、步骤和验收条件由当前任务决定，不把某次任务的开发顺序或特定故障处理固化为所有任务的流程。

```text
skills/my-skill/
  SKILL.md
  scripts/example.py
```

`SKILL.md`：

```markdown
---
name: my-skill
description: 描述这个技能提供的可复用能力、应该选用的情况和不应该选用的情况。
---
说明如何按当前任务选择操作，以及输入输出约束和能力边界。
scripts/example.py 提供可复用操作；说明它的参数、输入输出和错误行为。
可以阅读源码理解方法，直接调用、改写或自行编写脚本；调用现成脚本时可用 --help 查看参数。
根据执行结果决定下一步。
```

`SKILL.md` 提供知识与方法，`scripts/` 提供可复用或改写的参考源码，`references/` 提供按需读取的细节。模型可以根据任务自行编写脚本，保留适用的输入输出约定；代码可以内联执行，也可以写成工作目录中的文件再运行。例如，shell 的 `command` 可以直接包含模型编写的代码：

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

API 错误的脱敏诊断保存在对应会话 `events.jsonl` 的 `error.diagnostics` 或 `retry.diagnostics` 中：HTTP 状态、错误正文、请求 ID 等少量响应头，以及底层异常类型、原因、`errno`、请求阶段、耗时、请求大小和已收到的流数据量。阶段 `open_request` 包含 DNS、TLS、发送请求和等待响应头；`read_response`／`read_stream` 表示读取响应。错误正文最多读取 64 KiB 加一个截断检测字节，保存上限为 65536 字符，超限标记 `body_truncated`；读取错误正文失败时仍保留原 HTTP 状态和 `body_read_error`。主模型、子 Agent、远程 token 计量和辅助任务均保存诊断；辅助任务每次失败还记录 `operation` 和 `attempt`。

主模型、子 Agent、辅助生成和远程 token 计量按结构化错误类别决定是否重试：连接中断、限流及服务暂时不可用（包括 HTTP 429、500、503）每次失败后固定等待5秒，持续重试直到成功或被取消；鉴权、余额、请求参数、输出长度等不可重试错误直接报告。重试只重发失败的模型操作，不重复执行已经完成的工具，不把断流残片拼入下一次响应。主会话和子 Agent 以 `retry` 事件保存失败尝试，辅助任务继续用私有 `error` 事件记录；Ctrl-C、SIGTERM 和外层任务时限仍有效。

诊断不会保存请求正文或完整认证头。当前请求凭据、常见密钥／token／密码／Cookie 字段和 URL 中的账号、查询参数及 fragment 会被脱敏；SSE 错误帧进入 `_raw` 前同样清洗。终端及历史回放继续显示简短报错，详细诊断需读取 JSONL；已存在的历史错误无法补回当时未保存的信息。

内置 [workspace skill](skills_agent/builtin_skills/workspace/SKILL.md) 提供可直接调用的文本读取、检索、任务所需可执行文件与顶层 Python 模块探测，以及 JSON、CSV、DOCX、XLSX、PDF 的定位提取脚本。长记录可按返回游标续读；文档读取范围与限制见[提取参考](skills_agent/builtin_skills/workspace/references/extraction.md)。JSON 选择返回序列化值，CSV 记录保留逻辑行与物理行位置，DOCX 定位文档体段落／单元格，XLSX 读取已保存值而不计算公式。PDF 使用已安装的 pypdf／PyPDF2／pdftotext，缺少读取器时明确失败，不自动安装。

`context/history_skill` 提供原始会话检索和报告方法，仅在 `subagent(history=true)` 的检索子任务中加载；主 Agent 通过委派获得结论，不直接执行检索流程。主会话索引包含已返回的子任务报告，不索引私有子会话。中断后先确认已保存产物和原执行结果，再继续下一步；复杂任务可按需保存进度，不强制建立计划文件。

内置 [acceptance-criteria skill](skills_agent/builtin_skills/acceptance-criteria/SKILL.md) 要求在执行复杂任务前使用：读取理解需求所需的输入，在实质制作或修改前将原始需求、已接受的修正、模型列出的验收标准及验证方式保存为任务项目内的 Markdown 文档，例如 `docs/acceptance/<task>.md`。复杂性来自步骤依赖、相互影响的约束或需要多种证据的不确定性；简单问答、直接查找和局部明确修改无需建立文档。继续同一任务时复用文档，新增要求或发现遗漏时更新，不能为了迁就已有结果而降低标准。

内置 [review skill](skills_agent/builtin_skills/review/SKILL.md) 要求在复杂任务交付前使用，通过现有 `subagent` 独立核对原始需求、项目内的验收标准文档及实际产物。主 Agent 提供文档与附件的实际路径、需求来源和验证证据；需求不完整时先委派 `history=true` 检索。审查者既检查成果，也检查验收标准是否遗漏或曲解需求，返回有依据的缺陷和未验证事项，主 Agent 据此修正并按需定向复查。简单任务适度自检，明确要求审查时也可使用此技能；已受派的审查者不再建文档或委派。上述要求通过技能描述与正文引导，不增加运行时关卡。

内置 [file-editing skill](skills_agent/builtin_skills/file-editing/SKILL.md) 提供新建、精确替换、锚点前后插入、删除内容和删除文件的 Python 脚本，通过现有 shell 调用，不新增 tools。替换和插入要求原文或锚点唯一匹配，失败不写入；成功返回改动位置与有限行号片段。它还提供[跨回合渐进编辑示例](skills_agent/builtin_skills/file-editing/references/incremental-editing.md)：先保存有用产物，读取操作结果，再选择下一段扩展。

内置 [feedback skill](skills_agent/builtin_skills/feedback/SKILL.md) 提供获取实际工作结果的基础能力，同样通过现有 shell 调用：`file` 读取文件或目录元数据，`text` 按行列读取实际 UTF-8 文本，`run` 执行检查并保存完整、分离的 stdout／stderr 日志，`image` 获取已有图片，`render` 使用已安装的 Chrome／Chromium 截取本地 HTML 或 SVG。长文本返回续读行列；命令失败和超时保留真实状态。[跨回合反馈示例](skills_agent/builtin_skills/feedback/references/feedback-loop.md) 展示保存、获取、观察和修改之间的衔接。

反馈技能的 `scripts/check.py` 检查明确的文件、文本和 JSON 验收条件，保留实际观察与源位置，区分条件失败和获取错误；[验收检查参考](skills_agent/builtin_skills/feedback/references/acceptance-checks.md) 说明如何检查需求，以及失败后选择下一步。通过指定条件只证明该检查范围，行为要求仍需真实运行。

图片获取返回一个明确标记的反馈 JSON；该次 shell 的 stdout 应只包含这一个对象。运行时在文本筛选前读取所声明的绝对宿主机路径，识别 PNG／JPEG／GIF／WebP 图片头并保存独立会话快照，将图片字节作为原生输入送入下一次模型请求。原文件后续修改或删除不会改变已保存的观察。图片保留在所属工具结果中，辅助报告、上下文压缩和历史检索也能获取它；供应商请求中的只读媒体投影不改变当前真实用户任务。没有反馈标记的普通输出保持原有行为，获取失败明确返回诊断。

文件元数据、文本检查、截图和视觉判断各有适用范围：保存或渲染成功不能证明画面正确，单张截图不能证明动画流畅。视觉判断要求所用模型支持图片输入；`render` 需要现有浏览器，不自动安装依赖。PDF、办公文件等需用适当的已安装读取器提取文字或渲染页面，再获取相应文本或图片。

内置 [process-management skill](skills_agent/builtin_skills/process-management/SKILL.md) 通过 `start/status/wait/logs/stop` 跨 shell 调用管理任务自有进程。独立 supervisor 保存实际状态和完整 stdout／stderr 日志，后台启动不会阻塞当前 shell；可以等待日志标记、TCP 可达或实际退出，再在后续回合读取状态、日志并停止进程。支持具备 waitid/WNOWAIT 的宿主 POSIX，已在 macOS 验证；不支持交互输入或自行脱离进程组的 daemon。停止仅由确认归属的 supervisor 清理自己的进程组，归属丢失时明确报告。分回合使用见[进程生命周期示例](skills_agent/builtin_skills/process-management/references/across-turns.md)。

通过 Agent 的 shell 启动的后台任务会登记所属回合。回合失败或取消时清理该回合新建的任务，子 Agent 的任务也在此范围内；成功回合的后台服务可以继续供后续回合使用。普通 CLI 会话退出时清理本次会话登记的非持久任务。用户明确需要跨会话运行的服务，可用 helper 的 `start --persistent`；该选项仅在普通会话中允许，任务所属回合仍须成功完成。benchmark 总是在退出前清理所有登记任务，并拒绝 `--persistent`。直接在 Agent 之外运行 helper 时，进程仍由调用者通过 `stop` 管理。

内置 [investigation skill](skills_agent/builtin_skills/investigation/SKILL.md) 增加已知 HTTP(S) URL 的获取脚本，分别保存响应体、来源元数据和可选提取文本，保留实际 HTTP 状态、重定向、完整性与哈希；有界预览不足时可继续读取保存资料。默认响应上限 64 MiB，网络 deadline 覆盖重定向与渐进读取，超限或失败保留已取得的资料并明确标记不完整。它提供资料获取，不提供搜索、登录或页面脚本执行。

### Sandbox

```sh
.venv/bin/skills-agent run --sandbox-image python:3.12-slim
> 在 sandbox 中运行 Python 脚本分析当前目录数据
```

提供 `--sandbox-image` 才注册 `sandbox` 工具，需要可运行的 Docker。每次调用启动一个临时容器：禁用网络，工作目录读写挂载，外部技能和会话附件目录只读挂载。脚本依赖需预装在指定镜像中。`shell` 始终在宿主机执行；模型通过 `sandbox` 工具明确选择容器执行，容器失败会返回错误。

## 提示词版本

[提示词版本时间线](docs/prompt-history/README.md) 按独立提示词维护 Git 历史、当前工作区基线和后续版本，每个版本记录发现的问题、目标、效果及原文。主工作提示词的 v1、v2 保留增量执行规范更新前后的文本，v3 增加文件编辑引导，v4 增加实际反馈获取引导，v5 增加通用跨回合进程管理引导，v6 明确按当前任务组合基础能力，移除固定的产物开发和故障验证路线，v7 调整技能选择时机并加入交付前独立审查。新增技能与参考说明各自从 v1 建档，后续实际修改沿各自版本继续追加。

## 验证

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
```

测试通过本地模拟模型覆盖两种协议：读取技能与参考源码、返回新脚本源码、写入执行、根据错误修改、验证结果并续聊。同时验证多轮交互与恢复历史、长命令实际执行与完整记录、工作上下文增量保存、压缩程序检查与历史检索、附件编码、工具配对、子会话显示隔离、进程中断清理和 Docker 参数。进程生命周期回归会实际启动后台写入任务，检查正常结束、失败和信号中断后的退出与文件稳定性，并验证跨回合和跨会话的所有权边界。ALE 测试需要本地官方源码，运行方法见接入说明。模拟模型与容器内回归验证协议、运行环境及执行链路；真实模型的任务完成质量和 ALE 评分仍需另行运行 smoke 验收。

协议依据：[OpenAI token计量](https://developers.openai.com/api/docs/guides/token-counting)、[OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)、[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[OpenAI 流事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[OpenAI 文件输入](https://developers.openai.com/api/docs/guides/file-inputs)、[Anthropic 流事件](https://platform.claude.com/docs/en/build-with-claude/streaming)、[Anthropic 工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Anthropic 图片](https://platform.claude.com/docs/en/build-with-claude/vision)、[Anthropic PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support)。
