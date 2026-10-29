# skills-agent

Python CLI Agent。模型阅读 `SKILL.md` 和参考脚本源码，通过 `edit_file` 创建和修改任务文件，通过 shell 读取和执行，并根据结果修正。核心负责会话、上下文、模型调用和工具执行。

## 运行

需要 Python 3.11+ 和 Git。Python 运行依赖及锁定版本见 `pyproject.toml` 和 `uv.lock`。

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

配置文件还可填写 `provider`、`model`、`base_url`、`api_format`、`token_counter`、`skill_dirs` 和 `sandbox_image`。文件中的 `skill_dirs` 相对配置文件所在目录解析；命令行中的路径仍相对启动目录。`--skill-dir` 覆盖文件中的额外技能目录列表。API key 只从环境变量读取，可继续放在 `.env` 中，不写入 TOML。`token_counter` 可选 `auto`、`official` 或 `usage`；旧的 DeepSeek 本地计数器及 tokenizer 配置已移除。

工作上下文默认预算为 **1M（1000000 tokens）**，各项 token 额度均可独立设置为正整数。`usage` 模式仅以网关最近一次有效的主模型返回用量判断是否达到 `min(context_tokens, model_context_window) × 80%`：Chat Completions 直接读取 `usage.total_tokens`，Responses 使用 `usage.input_tokens + usage.output_tokens`；不预留输出额度而提前压缩。其他计数模式的有效输入预算为 `min(context_tokens, model_context_window - max_output_tokens)`，压缩阈值取该预算与上述 80% 阈值的较小值；未设置实际窗口时使用 `context_tokens`。配置数值仍需符合所用模型的实际能力。

两种 API 均使用流式接收。正文收到即显示；正文和工具调用按到达顺序记录、渲染。交互终端中，每次工具调用只保留一条摘要，原位更新接收、调用、执行进度和最终状态；重新打开会话时也合并为一条。工具摘要显示名称及完成状态、失败退出码和输出行数，不展开命令源码或工具 JSON；可读思考标记为“思考”，按到达顺序显示，签名和加密内容不显示。终端输入时显示 `>`，发送后清除临时输入区，显示一次与历史回放相同的 `user:` 消息块；附件只列名称。用户消息的黑底铺满整行，Agent 回复为白底黑字，段落空行同样填充白底；工具摘要为蓝字，失败或中断时为红字，均使用终端默认背景。颜色只用于终端，重定向输出及原始日志不含新增的颜色控制码。`history` 按到达顺序输出摘要；`history --full` 展开完整公开记录，两者都将失败的工具结果标红，不调用模型。原始日志始终完整保存，显示摘要不调用模型；工作上下文的压缩与长工具结果报告另有独立模型调用。

交互会话使用可滚动的终端消息区，底部输入行上下各留一行间距，最下方显示“按 F2 选中文本”。输入行上方显示本轮耗时，从发送消息起计时，包含工具和子 Agent 的执行时间，精确到 0.1 秒；整轮结束或中断后保留最终耗时，下一轮开始时切换到该轮计时，恢复会话时不回放计时。输入光标使用终端原生的闪烁竖线，实际效果取决于终端支持。思考正文和标题为灰色字、终端默认背景。思考流使用纯文本、固定8行内容区滚动显示最新部分，不解析 Markdown；转入正文或工具调用时立即停止思考动画并收起为 `▶ 思考`，不等待整条响应结束。点击标题才将完整思考渲染为 Markdown，结果按文本和终端宽度缓存；恢复会话时默认收起。折叠和滚动预览只影响显示，原始思考与消息顺序完整保留。

交互界面通过独立事件队列接收更新，每秒最多处理约10批显示事件，相邻文本增量合并但不跨越工具或完成事件。模型流接收、工具执行和下一次请求不等待界面排版；渲染期间不持有执行线程需要的队列锁或控制锁。F2冻结画面时仍消费显示事件，返回后显示最新状态。

鼠标点击需要终端允许鼠标报告；Apple Terminal 默认开启，可在 [View → Allow Mouse Reporting](https://support.apple.com/en-mide/guide/terminal/trmlc69728a5/mac) 中检查。滚轮或 PageUp / PageDown 浏览消息，Ctrl-End 返回最新输出。

工具返回图片时，其结果下方显示蓝色带下划线的 `[图片]`；多张图片依次显示 `[图片1]`、`[图片2]`。在交互会话中单击即可用系统默认应用打开工具返回的原路径，恢复会话后也可点击。原文件已移动或删除时显示打开失败，不查找文件或改用会话副本。F2 选择模式下不触发打开；剪贴板附件维持原有显示。独立 `history` 命令和重定向输出显示图片标签与路径，方便复制。

运行期间，耗时右侧显示蓝字“停止”，鼠标移入变红；点击与 Ctrl-C 使用同一中断流程，停止本轮模型接收和正在执行的工具。首次触发后显示“停止中”，清理完成前不重复中断。已收到的正文和思考留在原始会话中；中断正文进入工作上下文，并标记为未完成，不把未完成的工具参数或思考状态作为可续接的响应。已经执行的文件修改不回滚。

模型运行时仍可输入和粘贴附件。提交的消息按顺序排队，在输入行正上方各显示一行预览，超长内容以省略号结尾，右侧蓝字显示“排队中”；鼠标移到状态文字上变为红字“撤回”，点击将该条移出队列，不改动正在输入的草稿。当前轮结束或停止后依次发送，届时才写入会话并展示完整用户消息。排队保存在内存中；该条消息的总耗时包含等待时间。

按 F2 进入文本选择模式，拖选文字后使用终端的复制快捷键（macOS 为 Cmd-C）；再按 F2 返回。选择期间画面保持不变，模型运行和日志记录继续，返回后显示最新内容并恢复思考标题的点击操作。

右下角“上下文窗口 x%”在 `usage` 模式下显示最近一次有效的主模型返回用量，占配置预算与模型窗口中较小者的比例；新增消息不改变这个数值，没有有效返回用量时显示 `—`。Chat 读取 `usage.total_tokens`，Responses 使用返回的输入与输出之和。其他计数模式显示最近一次主模型请求前计量的输入 token 数占有效输入预算的比例，不包含该次新生成的回答。显示本身不额外调用计量接口；恢复会话沿用对应模式最近一次有效数值。

工具通过参数校验并开始执行后，消息区尾部显示带省略号动画的执行状态；上下文压缩期间显示“正在压缩上下文”。结束、失败或中断时清除该状态。动画不写入会话记录，F2 选择期间暂停，返回后恢复。

压缩状态行显示当前阶段、批次和流式生成的最新摘要片段，使用蓝字和终端默认背景。预览保持一行，超出宽度时显示末尾片段和省略号；等待模型时保留动画。预览只用于临时状态行，完整过程仍保存在压缩日志中，不增加模型调用。子 Agent 内部压缩只向主界面提供阶段。

子 Agent 状态行显示任务名、实际阶段（执行中、正在执行工具、正在压缩上下文）和运行秒数，精确到 0.1 秒，阶段切换不重置计时。子 Agent 的内部对话、工具名称、参数和输出不展示；返回时显示“已返回报告”，失败或取消时显示“失败”或“已中断”。阶段和计时只用于实时显示，不进入会话记录或模型上下文。

交互终端中的 Agent 正文在流式接收时增量显示纯文本，段落结束后渲染 Markdown，包括标题、加粗、斜体、列表、引用、链接、代码和表格。消息区默认跟随最新输出，向上滚动可以阅读此前内容。`history` 命令直接渲染完整正文和可读思考；重定向输出和日志保留 Markdown 源文。

stdin 每行作为一轮输入，EOF 退出。需要将多行任务作为一次输入并执行到结束时，使用下方的 benchmark 入口。

终端输入使用 prompt-toolkit，支持方向键移动光标、编辑文字和上下键切换本次运行中的输入历史。

Anthropic：

```sh
export ANTHROPIC_API_KEY='你的密钥'
export ANTHROPIC_MODEL='支持工具调用的模型名称'
.venv/bin/skills-agent run --provider anthropic --session claude
```

未在命令行或配置文件指定时，模型名称读取对应的 `OPENAI_MODEL` / `ANTHROPIC_MODEL`，API 地址读取 `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL`。`base_url` 为包含 API 前缀的根地址，例如 `http://localhost:8000/v1`。OpenAI 官方接入默认使用 `/v1/responses`，并以 `/v1/responses/input_tokens` 计量同一份输入；Anthropic 使用 `/v1/messages` 和 `/v1/messages/count_tokens`。本地兼容服务可以不设置密钥。网关可通过 `api_format = "responses"` 使用对应接口；Chat 协议在 `token_counter = "auto"` 时选择 `usage`，直接读取生成响应中的 `usage.total_tokens`，不调用独立计数接口。

OpenRouter Responses 自动使用 `usage` 计量，无需独立的计数接口。压缩只依据最近一次有效的主模型返回用量 `usage.input_tokens + usage.output_tokens`，不估算文本、图片、新增消息或首次请求的长度。本轮缺少有效用量时沿用已有有效快照；尚无有效快照时长度未知，不作本地长度阈值判定。例如上次返回用量为窗口的 79.999%，即使再加入相当于窗口 5% 的消息，也不因此压缩；等下一次主模型回复报告 90% 时，才按 80% 阈值触发压缩。

有效用量随主会话保存，重启后恢复；辅助生成和子 Agent 的用量不会替换主会话数值。压缩后旧快照失效，等待下一次主模型回复提供新用量，不估算压缩后的长度，也不以旧快照重复触发压缩。其他 Responses 网关也可显式设置 `token_counter = "usage"` 或 `--token-counter usage`；Chat 同样可显式选择 `usage`。Chat 与 Responses 的用量来源分别按上面的字段读取。

例如通过单独的 TOML 配置运行 GPT-6 Luna（密钥使用 `OPENAI_API_KEY`）：

```toml
provider = "openai"
model = "openai/gpt-6-luna"
base_url = "https://openrouter.ai/api/v1"
api_format = "responses"
token_counter = "usage"
context_tokens = 1050000
model_context_window = 1050000
max_output_tokens = 128000
summary_output_tokens = 8192
```

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

`--file` 和会话内的 `/file PATH` 将文件加入下一条消息的附件，可添加多个；有附件时直接输入空行也可发送。每个附件先完整复制到会话目录。PNG、JPEG、GIF、WebP 和 PDF 使用模型原生输入，需要所选模型及兼容服务支持该格式。UTF-8 文本作为完整文本附件输入；请求前计数器发现受保护输入超出上下文预算时明确报错，不机械截断。`usage` 模式不预判新增附件的 token 长度。其他格式保留副本路径，由模型编写脚本或使用适合的现有代码读取。

`--workspace` 指定 shell 工作目录，默认当前目录。附件的相对路径按工作目录解析。Agent 的安装目录和任务目录可以分开：

```sh
uv run --project /path/to/skills-agent skills-agent run --workspace /path/to/task
```

会话、附件、执行日志和文件历史默认保存在 `~/.skills-agent/workspaces/<工作区标识>/`。工作区标识根据规范绝对路径生成，同名但不同位置的目录互不混用。`workspace.json` 记录对应路径；该目录下的 `sessions/<会话名>/` 保存每个会话，`history.git/` 保存同一工作区通过 `edit_file` 编辑的文件版本。

`--data-dir PATH` 可更改运行数据根目录，其优先级高于 `SKILLS_AGENT_DATA_DIR` 环境变量；未设置时使用 `~/.skills-agent`。`--sessions-dir PATH` 仅覆盖会话存储位置，文件历史仍使用运行数据根目录。回放时使用相同的 `--workspace` 和数据位置。

普通会话回放使用会话目录中的 `replay-cache.json`，恢复时只解析原始日志中新追加的事件。旧会话首次使用或缓存失效时需要重建一次；删除缓存不会删除原始记录或模型上下文。`history --full` 始终读取原始日志。用户消息先保存并显示，模型和工具仍需等待运行准备完成后才开始。

旧版 `<workspace>/.agent/sessions` 中的会话不会自动搬移，以保留记录中的绝对路径。新位置没有同名会话而旧位置存在时，程序提示显式选择旧位置：

```sh
skills-agent run --workspace /path/to/task --sessions-dir /path/to/task/.agent/sessions --session demo
skills-agent history --workspace /path/to/task --sessions-dir /path/to/task/.agent/sessions --session demo
```

运行时在 Agent 初始化时自动采集工作目录、是否位于 Git 仓库、运行平台、实际执行 shell 和操作系统版本，并加入系统提示词，供模型选择适合当前环境的命令。主 Agent 与子 Agent、交互 CLI 与 `benchmark` 共用这套环境信息；执行 shell 以运行时实际使用的 shell 为准（POSIX 通常为 `/bin/sh`，不直接采用登录环境的 `$SHELL`），无法确定的字段为 `unknown`。系统提示还包含下载文件和解压资料的隔离处理、读取它们时使用 Python `-I` 的指导；用户要求制作或运行的代码不受这条资料处理规则限制。

## 工作区文件历史

runtime 仅为 `edit_file` 的新建、替换、删除和移动操作记录文件历史。操作前保存所操作文件的真实内容，成功后保存修改结果；内容未变时复用已有检查点。操作记录关联会话、工具调用、路径和前后检查点，失败时保留已保存的修改前内容供核查。历史仓库按需建立在运行数据目录，不在工作区创建 `.git`，不修改项目已有仓库的分支、提交或暂存区。

没有文件监听或启动、退出时的工作区扫描。程序生成物和人工修改不会自动进入历史；之后如果通过 `edit_file` 编辑其中某个文件，会先保存该文件当时的内容。检查点只覆盖已记录的文件状态，不能据此推断整个工作区在那个时刻的内容，也不代表计算正确、测试通过或任务完成。旧版已保存的检查点保留，新操作继续按工具编辑范围记录。

系统提示词提供本次历史仓库的绝对路径和可执行查询命令。可通过 shell 使用 [read-file-history skill](../skills_agent/builtin_skills/read-file-history/SKILL.md) 的只读辅助脚本，也可使用运行环境中的 Python 模块：

```sh
python -m skills_agent.file_history --repository /absolute/history.git status
python -m skills_agent.file_history --repository /absolute/history.git log --path analysis.py --limit 10
python -m skills_agent.file_history --repository /absolute/history.git diff BEFORE AFTER --path analysis.py
python -m skills_agent.file_history --repository /absolute/history.git show BEFORE analysis.py
```

`status` 提供记录模式、检查点和错误；`mode: editing-only` 表示仅记录编辑工具操作，`monitoring: false` 表示没有文件监听。尚未执行编辑时可能没有历史仓库或检查点。`BEFORE`、`AFTER` 替换为 `log` 返回的检查点编号；文件路径相对工作区。`show` 原样输出保存的内容。先核对旧版本与当前文件的差异，需要恢复文本时通过 `edit_file` 修改相应部分，保留有效的当前工作；不要重定向覆盖文件或通过 Git checkout/reset 绕过编辑工具。文件历史独立于对话历史：查看旧文件不需要回放或删除会话。

## 单次任务与 ALE

```sh
.venv/bin/python -m skills_agent.benchmark \
  --prompt-file /path/to/prompt.txt \
  --workspace /path/to/task \
  --log-dir /path/to/run-logs
```

完整任务从 UTF-8 文件读取，执行到模型返回最终回答后退出。配置文件的读取规则、模型、技能、附件及 `--data-dir` 参数与 `run` 相同。每次运行在统一运行数据目录中建立独立会话，退出时将会话和已有文件历史的独立副本导出到 `<log-dir>/session` 和 `<log-dir>/history.git`，供 ALE 等环境归档；未产生历史仓库时不导出该仓库，已使用的日志目录不能重用。退出码为成功 `0`、失败 `1`、中断 `130`。正常结束、失败或接到 SIGTERM 时，均清理当前脚本及通过 shell 登记的本任务后台进程，保留日志后退出；benchmark 明确拒绝 `background_lifecycle=service`，不允许创建跨任务保留的服务。容器运行时可将 `--data-dir` 指向持久化挂载目录；销毁容器前也可归档日志目录中的导出副本。

[ALE 接入说明](../adapters/ale/README.md) 包含可复制到 ALE 仓库的 Deployer、Agent 配置及单任务 smoke 配置。适配器在 ALE 的任务 sandbox 内安装并运行本项目，产物由 Agent 按题目指定路径写入，日志写入 ALE 的 `executor.work_dir`。

## 模块

```text
skills_agent/
  cli.py             命令行输入、摘要显示与完整记录回放
  terminal.py        可滚动的交互消息区与思考折叠
  display.py         非阻塞的界面事件队列与增量合并
  input.py           输入行的剪贴板快捷键与附件标记
  clipboard.py       macOS 剪贴板读取
  markdown.py        终端 Markdown 渲染与流式预览
  benchmark.py       从任务文件运行一次并退出
  config.py          TOML 配置读取、校验与参数优先级
  runtime.py         两个入口共用的参数与 Agent 构造
  storage.py         工作区标识与统一运行数据目录
  file_history.py    编辑工具操作的独立 Git 历史和只读查询
  agent.py           同步模型/工具循环、独立子会话
  session/           追加式事件日志、输入附件副本
  context/           持久化工作上下文、模型压缩与程序检查、原始历史检索及专用 history_skill
  providers/         OpenAI 与 Anthropic 协议转换
  tools/             edit_file、shell、subagent、sandbox
  skills/            技能发现与系统提示词中的技能目录
  sandbox/           Docker 命令执行
  builtin_skills/    通用读取、文件历史查询、反馈、调查、修复、后台任务、进程管理和验收标准技能
tests/
adapters/ale/        ALE Deployer 与运行配置
```

执行顺序：记录用户输入 → 构建模型上下文 → 流式接收、记录和显示模型事件 → 响应完整后执行工具并记录结果 → 再次调用模型，直到模型返回最终回答。

### 会话与上下文

实现依据见 [上下文管理设计与调研](context-management-design.md)。

- `events.jsonl` 只追加原始消息、流式事件和完整工具输出；事件 ID 是稳定的字节偏移引用。`attachments/` 保存输入副本，`subagents/` 保存子任务和辅助调用的私有记录。
- `context.json` 保存已挑选的工作记录、摘要、覆盖事件、已处理位置和最近计量。每次只处理新增事件；已压缩内容不会从原始日志重新填回。
- 会话在工具调用与结果之间被强制结束时，下一次准备请求会给这些调用补记中断结果，使记录组可发送。补记记录是派生状态：ID 形如 `recovered-<原调用事件偏移>-<调用 ID>`，含原响应因而跨响应唯一；记录自身带 `recovered_from` 指向可回查的原调用事件。派生记录由程序生成，摘要不能将它当作用户原话。调用 ID 中的其他字符会被替换，并始终附 8 位摘要（对原始调用 ID 取 sha256 前缀），使不同调用 ID 得到不同引用。补记只进入工作上下文，原始日志不改。只修复被后续记录越过的组；未被越过的尾组仍按原样拒绝发送。同一响应内重复的调用 ID 仍由记录组校验报错。
- `--context-tokens` 默认 **1000000**。`usage` 模式只在最近一次有效的主模型返回用量达到配置预算与模型窗口中较小者的 **80%（默认 800000）** 时触发压缩；Chat 使用 `usage.total_tokens`，Responses 使用返回的输入与输出之和。新增消息不改变判定，也不因输出预留空间提前触发。其他计数模式在请求前计量完整输入，若有效输入预算更小，则提前在该上限触发。以已有摘要和较早的已挑选记录生成新摘要；保留最近 **10 次主模型响应及全部对应工具结果**，另保留最新用户任务原文。辅助模型调用不计入这10次。
- 压缩摘要关闭思考，默认生成上限为8192 tokens，提示词要求正文和引用合计在本次额度内完成。用旧摘要和本次移出的工作记录生成新摘要；使用请求前计数器时，材料过长按完整调用组分批合并。摘要期望格式为 `{"summary": "正文，可使用【R1】", "references": {"R1": ["event-123"]}}`；引用作为原文定位线索，JSON 格式、引用或引文错误不阻断压缩，旧 Markdown 摘要仍可继续使用。程序直接保存模型返回文本，不调用独立模型核验。达到生成上限而正常结束时，已返回的非空文本仍可采用，结束原因保存在私有日志中。请求前计数器发现候选请求仍超预算时，在同一摘要流程中继续缩短摘要；`usage` 模式不估算候选长度，摘要采用后等待下一次主模型返回新用量。网络断流、取消或程序异常时保留旧上下文；辅助生成和远程 token 计量遇到可重试故障时，每次失败后固定等待5秒，无次数上限；不拼接残片，不重跑工具。Ctrl-C、进程退出和 ALE 等外层运行时限仍可终止等待。
- `--summary-output-tokens` 默认8192，是辅助生成的输出额度，与工具报告正文预算分开；需符合实际模型输出能力。使用请求前计数器时，摘要请求的实际额度还受模型剩余窗口和候选主请求空间约束；受保护内容本身过长时明确报告预算限制。`usage` 模式不在本地估算这些长度。两种模式均不缩短受保护记录范围。
- Anthropic 客户端压缩在协议允许的边界移除失效 thinking 的发送副本，原始历史不变。工具结果尚待回送且需要原签名时延后压缩；硬预算仍不能超出。
- shell/sandbox 必填 `name`，shell 另必填布尔值 `run_in_background`；可选择 `result_mode: auto | direct | report`。请求前计数模式下，`auto` 默认保留不超过8192 tokens的结果，超过则生成2048 tokens以内的报告；`direct` 保留原文，装不下时返回原始事件和行范围。`usage` 模式不估算工具结果长度，`auto` 直接保留原文，`direct` 不做本地空间预测。两种模式均可显式选择 `report` 生成报告，`usage` 模式用 `max_output_tokens=2048` 限制该次生成，不以本地计数判断报告长度。工具报告的生成与计量均关闭思考，截断正文不作为完整报告采用；报告失败时返回明确的 `report_error`、原执行状态及原始结果读取位置，保留图片，当前任务继续。原始结果总是先完整落盘，取消仍立即中断。官方接口对报告正文的独立计量包含少量请求开销，因此是保守预算。
- sub-agent 必填 `name + task`，最终返回 Markdown 报告，说明适用的结论、证据、产物和未解决事项；内部对话不进入主上下文或 UI。`history=true` 时，加载检索专用 history skill，并携带完整工作上下文只读快照搜索父会话；快照装不下就报错，不截断。主 Agent 和普通子任务的技能目录不包含该技能，普通子任务只接收明确交代的输入。
- 普通子任务和历史检索子任务均继承主 Agent 的上下文预算配置。普通子任务不继承主对话，`task` 须交代目标、相关约束、必要材料、操作边界和预期交付。主 Agent 根据报告中的证据、执行结果和未解决事项继续工作，定点核验关键或矛盾之处，不盲信完成声明，也不重做整个子任务。
- `history.sqlite` 增量索引原始主会话，支持中文与英文检索、事件/路径精确查找、行范围、邻近记录及工具配对。检索子任务通过查询扩展、回看证据和检查后续修正生成报告，只把最终报告带回主上下文。任何时候均可检索，不必等到压缩后。
- `max_output_tokens` / `--max-output-tokens` 默认32768，包含思考和回答。主模型输出截断时保留已收到的公开内容和原始响应，记录错误，不执行不完整的工具调用。

DeepSeek 自动使用 Chat Completions，并以最近一次有效的主模型返回的 `usage.total_tokens` 作为压缩依据，无需安装额外计数依赖或下载 tokenizer：

```sh
export OPENAI_MODEL='deepseek-flash'
export OPENAI_BASE_URL='https://api.deepseek.com'
uv run --env-file .env skills-agent run --session demo
```

其他 DeepSeek 网关可显式设置 `--api-format chat --token-counter usage`。Chat 只接受网关返回的 `usage.total_tokens`，不自行把 `prompt_tokens` 与 `completion_tokens` 相加作为替代；本轮缺少有效字段时沿用已有有效快照，尚无有效快照时长度未知，不作本地长度判定。首次请求和新增用户消息、工具结果、图片等均不在本地计数或估算；重启恢复同一主会话的有效用量，压缩后等待下一次主模型回复刷新。阈值固定为上述预算与实际窗口中较小者的 80%，不会扣除输出额度而提前触发。

当前 DeepSeek 官方 Chat 链路支持在 `.env` 中设置小写 `effort`：

```dotenv
effort=low
```

可选 `low`、`high`、`max`，分别控制思考强度；`none` 关闭思考。未设置或留空时不传思考控制参数，使用服务端默认值；其他非空值会在启动时明确报错。此字段目前仅从环境变量读取，不提供 TOML 或命令行选项，仅对 `provider=openai`、主机名为 `api.deepseek.com` 的 Chat 接口生效（包含 `/v1` 地址），其他接口忽略它。

通过 `uv run --env-file .env skills-agent run --session demo` 加载，修改后需重启进程。子 Agent 和工具报告继承该设置；上下文压缩摘要仍按原有规则关闭思考。`effort` 控制思考强度，`max_output_tokens` 仍限制单次总输出（含思考），两者用途不同。参数语义见 [DeepSeek 思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/)。

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
可以阅读源码理解方法，直接调用，或通过 edit_file 保存和修改任务脚本后再运行；调用现成脚本时可用 --help 查看参数。
根据执行结果决定下一步。
```

`SKILL.md` 提供知识与方法，`scripts/` 提供可复用或改写的参考源码，`references/` 提供按需读取的细节。模型可以根据任务自行编写脚本，保留适用的输入输出约定。需要保存脚本时，先通过 `edit_file` 创建或修改文件，再通过 shell 执行。例如，先调用 `edit_file`：

```json
{
  "name": "Save input analysis script",
  "operation": "create",
  "path": "analyze.py",
  "content": "import csv\nfrom pathlib import Path\n\nwith Path(\"data.csv\").open(encoding=\"utf-8\", newline=\"\") as source:\n    rows = list(csv.DictReader(source))\nprint({\"rows\": len(rows), \"columns\": list(rows[0]) if rows else []})\n"
}
```

确认保存成功后调用 shell：

```json
{
  "name": "Inspect analysis input",
  "command": "python3 analyze.py",
  "run_in_background": false
}
```

脚本可使用当前环境中安装的语言与库，无需固定入口或统一返回格式。同步 shell 返回退出码、stdout 和 stderr，后台 shell 返回启动回执，实际完成结果由运行时随后交付；模型据此继续修改代码。配置的技能说明作为任务指导，普通输入资料与执行结果作为数据。

模型返回完整响应但工具参数不是合法 JSON 对象时，该调用不执行，以原调用 ID 返回工具错误，让模型继续修正；同批其他有效调用照常处理。这类参数错误不进入网络重试流程。

shell 和 sandbox 必填 `name`，用简短自然语言说明脚本用途，供终端显示。shell 另必填布尔值 `run_in_background`：`false` 同步等待入口主进程，退出并清理同进程组残留后返回；`true` 自动登记后台作业，返回启动回执，由运行时继续监管。参数尚未收全时显示“接收调用”，完整后更新为名称和“调用”，实际开始执行后更新为“执行中”；收到工具结果后更新为完成或失败，交互终端不另留调用占位行。断流或中断时保留已经显示的正文，工具摘要更新为失败或中断，并记录错误，不执行不完整的工具调用。

同步 shell（`run_in_background=false`）和 sandbox 支持可选 `timeout_seconds`：默认120秒，须为正有限数值，最多600秒。达到时限后终止本次进程组，必要时从 TERM 升级 KILL；Docker sandbox 还须确认容器已移除。执行与清理均有时间边界，正常超时返回 `timed_out: true`、`exit_code: 124`、真实 `process_returncode`、已取得的 stdout/stderr 和完整日志路径，让模型继续调整方法；不自动重跑命令。同步超时结果中每个输出流至多返回末尾1 MiB，以 `stdout_truncated`／`stderr_truncated` 标记截断，完整字节日志仍保留。清理无法确认时终止当前 Agent 并明确报错。同步命令输出保存在会话 `tool-runs/` 中，用户取消仍在清理后传播；进程组清理需要宿主提供 POSIX `waitid/WNOWAIT` 和 `ps`。后台 shell 的执行期限独立于启动回执：task 默认3600秒，service 默认无执行期限，两者均可用正有限 `timeout_seconds` 显式设置，且可超过600秒；启动回执另有10秒时限。管理 helper 的 `wait --timeout` 只结束一次观察，不改变作业期限。递归搜索应先定位相关目录，在遍历端排除无关路径；下游过滤和 `head` 不构成执行时限。

普通同步调用示例：

```json
{
  "name": "Inspect analysis inputs",
  "command": "python3 -c 'from pathlib import Path; print(list(Path(\"input\").glob(\"*.csv\")))'",
  "run_in_background": false,
  "timeout_seconds": 30
}
```

DeepSeek 不再通过本地编码库转换或计数请求，普通工具文本中的保留标记不会触发本地 tokenizer 转换错误；实际发送请求及原始记录保留原文。

模型 HTTP 请求统一携带 `User-Agent`，格式如 `skills-agent/0.1.0 (Linux; x86_64) Python/3.12.0`。产品版本从安装包 `skills-cli-agent` 的元数据读取，操作系统、架构和 Python 版本根据运行环境生成；直接运行未安装的源码时，产品版本显示为 `dev`。共享请求层覆盖主 Agent、子 Agent、辅助生成、远程 token 计量及重试请求。

API 错误的脱敏诊断保存在对应会话 `events.jsonl` 的 `error.diagnostics` 或 `retry.diagnostics` 中：HTTP 状态、错误正文、请求 ID 等少量响应头，以及底层异常类型、原因、`errno`、请求阶段、耗时、请求大小和已收到的流数据量。阶段 `open_request` 包含 DNS、TLS、发送请求和等待响应头；`read_response`／`read_stream` 表示读取响应。错误正文最多读取 64 KiB 加一个截断检测字节，保存上限为 65536 字符，超限标记 `body_truncated`；读取错误正文失败时仍保留原 HTTP 状态和 `body_read_error`。主模型、子 Agent、远程 token 计量和辅助任务均保存诊断；辅助任务每次失败还记录 `operation` 和 `attempt`。

主模型、子 Agent、辅助生成和远程 token 计量按结构化错误类别决定是否重试：连接中断、限流、服务暂时不可用及网关故障或超时（包括 HTTP 429、500、502、503、504、524）每次失败后固定等待5秒，持续重试直到成功或被取消；鉴权、余额、请求参数、输出长度等不可重试错误直接报告。重试只重发失败的模型操作，不重复执行已经完成的工具，不把断流残片拼入下一次响应。主会话和子 Agent 以 `retry` 事件保存失败尝试，辅助任务继续用私有 `error` 事件记录；Ctrl-C、SIGTERM 和外层任务时限仍有效。

诊断不会保存请求正文或完整认证头。当前请求凭据、常见密钥／token／密码／Cookie 字段和 URL 中的账号、查询参数及 fragment 会被脱敏；SSE 错误帧进入 `_raw` 前同样清洗。终端及历史回放继续显示简短报错，详细诊断需读取 JSONL；已存在的历史错误无法补回当时未保存的信息。

内置 [workspace skill](../skills_agent/builtin_skills/workspace/SKILL.md) 提供可直接调用的文本读取、检索、任务所需可执行文件与顶层 Python 模块探测，以及 JSON、CSV、DOCX、XLSX、PDF 的定位提取脚本。长记录可按返回游标续读；文档读取范围与限制见[提取参考](../skills_agent/builtin_skills/workspace/references/extraction.md)。JSON 选择返回序列化值，CSV 记录保留逻辑行与物理行位置，DOCX 定位文档体段落／单元格，XLSX 读取已保存值而不计算公式。PDF 使用已安装的 pypdf／PyPDF2／pdftotext，缺少读取器时明确失败，不自动安装。

`context/history_skill` 提供原始会话检索和报告方法，仅在 `subagent(history=true)` 的检索子任务中加载；主 Agent 通过委派获得结论，不直接执行检索流程。主会话索引包含已返回的子任务报告，不索引私有子会话。中断后先确认已保存产物和原执行结果，再继续下一步；复杂任务可按需保存进度，不强制建立计划文件。

内置 [acceptance-criteria skill](../skills_agent/builtin_skills/acceptance-criteria/SKILL.md) 在持久记录需求和证据有助于协作、后续继续，或用户要求时使用，优先复用已有任务记录；需求仍然清楚时无需另建文档。记录保留原始需求、已接受的修正及来源，区分必要条件、假设、未决问题和可选改进，验收条件用于明确需求而非扩大交付范围。需求变化或解释得到纠正时记录原因和依据；不能为迁就结果降低有效要求，也不能仅因自定条件已经写入文档就继续把它视为必需条件。独立验收文档不是开展授权工作的前置要求。

系统提示引导模型根据原始需求和已接受的修正，按任务需要适度检查实际结果，并依据观察修正问题。最终回答如实说明结果、相关验证和剩余限制，只对已经取得证据的范围作出结论。这些检查由模型结合当前任务选择，不要求独立 reviewer、额外交接或专门报告。

`edit_file` 是文件编辑的专用工具。传入简短的操作目的 `name`、操作 `operation` 和工作区内路径 `path`：

| 操作 | 额外参数与行为 |
| --- | --- |
| `create` | `content` 为完整文本，可为空；不覆盖已有文件。 |
| `replace` | `old_text` 必须在当前文件中唯一匹配；仅当前文件为空时允许 `old_text` 为空，用于写入空文件。`new_text` 可为空，用于删除匹配内容。 |
| `delete` | 删除指定文件，无需内容参数。 |
| `move` | `destination` 为工作区内的新路径；不覆盖目标文件。 |

新文件和移动目标的父目录须已存在，可以先通过 shell 的 `mkdir` 准备目录。文本创建和替换使用 UTF-8，拒绝 NUL 字节；工具不跟随符号链接编辑文件。

例如，先读取现有文件，再针对真实内容调用：

```json
{
  "name": "Correct the report heading",
  "operation": "replace",
  "path": "report.md",
  "old_text": "# Anaylsis result",
  "new_text": "# Analysis result"
}
```

检查工具结果后再选择依赖该结果的下一步；唯一匹配失败时重新读取相应区域。shell 负责文件读取、程序执行和检查，不得通过 shell、sandbox、内联 Python、重定向或先写脚本再执行的方式，绕过 `edit_file` 直接新建、改写、删除或重命名文件。正常运行程序、测试、编译、分析和渲染仍可生成任务产物；这些产物不自动进入工具编辑历史。

旧 `file-editing` skill 和 shell 编辑脚本已移除。内置 [read-file-history skill](../skills_agent/builtin_skills/read-file-history/SKILL.md) 提供只读的 `status`、`log`、`diff` 和 `show` 查询，用于比较已记录版本，再通过编辑工具恢复需要的内容。

内置 [feedback skill](../skills_agent/builtin_skills/feedback/SKILL.md) 提供获取实际工作结果的基础能力，同样通过现有 shell 调用：`file` 读取文件或目录元数据，`text` 按行列读取实际 UTF-8 文本，`run` 执行检查并保存完整、分离的 stdout／stderr 日志，`render` 使用已安装的 Chrome／Chromium 截取本地 HTML 或 SVG。附图统一使用下面的 images 技能和 shell 的 `images` 参数，截图命令负责准备文件。长文本返回续读行列；命令失败和超时保留真实状态。[跨回合反馈示例](../skills_agent/builtin_skills/feedback/references/feedback-loop.md) 展示保存、获取、观察和修改之间的衔接。

内置 [images skill](../skills_agent/builtin_skills/images/SKILL.md) 要求每次附图时使用，覆盖已有图片、下载图片、生成图片、截图和渲染结果。每个模型请求最多10张图，每张编码图片文件最多1,000,000字节（1 MB，base64前）；多次工具调用不能叠加该额度。禁止制作超过1 MB的新图片；[images.py](../skills_agent/builtin_skills/images/scripts/images.py) 的 `save_bounded` 支持内存生成后合限才写盘，`prepare SOURCE --output PATH` 将已有大图压缩为副本，保留原件。确需同时检查超过10张时，`compose SOURCE... --output PATH [--columns N]` 生成一张合限的编号拼图，返回原图URL、尺寸及最终图片中的区域坐标，便于回查与坐标换算。上述 helper 和 `feedback.py render` 负责准备文件及元数据，发送哪些图片由 shell 的 `images` 参数声明，无需再调用 attach。图片处理使用项目依赖 Pillow，压缩次数有界，不保存超限中间图。helper可从任意工作目录运行，无需导入 skills-agent 包。

shell 可选 `images` 参数的元素为 `{"path":"/absolute/host/image.png","label":"可选说明","source_url":"可选原始URL"}`；`path` 接受绝对宿主机路径或本地 `file://` URL，包括历史图片快照地址。运行时在同步命令结束后读取、校验并保存快照；命令可以先生成图片，也可以用 `command: "true"` 直接查看已有图片。远程图片先用现有 fetch helper 下载，再声明实际文件路径和原始 `source_url`。此参数仅用于图片，不接受 PDF 或任意文件。技能包含生成并打印日志、多图准备后一次声明、合图、历史URL重附、渲染及后台任务范例。脚本不会上传图片，也不会自动安装依赖；拼图用于概览，需要细节时可裁剪原图再声明。

反馈技能的 `scripts/check.py` 检查明确的文件、文本和 JSON 验收条件，保留实际观察与源位置，区分条件失败和获取错误；[验收检查参考](../skills_agent/builtin_skills/feedback/references/acceptance-checks.md) 说明如何检查需求，以及失败后选择下一步。通过指定条件只证明该检查范围，行为要求仍需真实运行。

显式 `images`（包括空数组 `[]`）优先于 stdout 的旧反馈协议，普通日志、多段JSON或文本筛选不会改变显式声明的图片；`images: []` 表示只准备文件、不附图。未提供该参数时，暂时兼容 stdout 为单个反馈JSON对象的旧调用，新技能只推荐参数入口。运行时在文本筛选前识别 PNG／JPEG／GIF／WebP 图片头并保存独立会话快照，在同一次模型请求中附上图片与快照的 `file://` URL，保留可选原始 `source_url`，获取失败明确返回诊断。原文件后续修改或删除不会改变已保存的观察。每次 assistant 回复（包括继续调用工具）都会消费该轮图片，之后仅保留可回查引用；需要再次查看时，在新的 `images` 中声明记录的快照URL或准备好的裁剪图。辅助报告、上下文压缩和历史检索遵循相同的图片限制；供应商请求中的只读媒体投影不改变当前真实用户任务。

文件元数据、文本检查、截图和视觉判断各有适用范围：保存或渲染成功不能证明画面正确，单张截图不能证明动画流畅。视觉判断要求所用模型支持图片输入；`render` 需要现有浏览器，不自动安装依赖。PDF、办公文件等需用适当的已安装读取器提取文字或渲染页面，再获取相应文本或图片。

内置 [background-tasks skill](../skills_agent/builtin_skills/background-tasks/SKILL.md) 是后台执行的启动说明：当 shell 需要先交还控制权而入口命令继续运行时必须使用，与命令运行多久、是否有子进程无关。普通同步命令无需使用；脚本内部并行但会等待全部子任务，也不因此要求使用。无需新增工具，在 shell 指定 `run_in_background=true`，可选 `background_lifecycle`：

| 生命周期 | 默认执行期限 | 最终回复与保留规则 |
| --- | --- | --- |
| `task`（默认） | 3600秒 | 必须结束或停止，收到实际结果后才能完成会话 |
| `service` | 无；可显式限制 | 验证健康和实际行为后允许交付，成功交付和正常 CLI 退出后由独立 supervisor 保持运行 |

`run_in_background` 决定工具是否先返回；生命周期决定最终回复是否等待作业结束。`background_lifecycle` 用于后台调用，同步调用指定 `service` 会报错。不能把待完成的计算标记为 service 绕过等待。后台 task 示例：

```json
{
  "name": "Build analysis outputs",
  "command": "python3 -u analyze.py",
  "run_in_background": true,
  "background_lifecycle": "task",
  "timeout_seconds": 7200,
  "images": [{"path": "/absolute/workspace/plot.png", "label": "分析完成后的图表"}]
}
```

启动回执只表示启动成功，启动确认期限独立为10秒，包含稳定 `job_id`、显示名称 `display_name`、`process_record_path`、`stdout_path`、`stderr_path`，以及管理所需的 `state_dir` 和内部安全 `name`。运行时登记所属回合、Agent 和原启动工具调用。脚本实际工作保持前台运行，脚本内部子进程必须逐个等待并汇总失败；入口退出后统一清理同组残留，再保存结果。清理过残留必须明确记录，不能仅以入口返回0认定全部自然完成。禁止 `nohup`、`setsid`、自行 daemonize 或把实际计算放出去后提前退出；允许内部并行的 `&`，但入口必须等待每个子任务。这是进程组与脚本协作的契约，不能硬性阻止更换进程组、创建新会话或委托外部服务的脱离行为，不是任意脚本都无法绕过的沙箱。技能附有无子进程、同步子程序、并行等待、失败汇总及取消清理的可运行范例。

后台调用的 `images` 在启动时登记，可指向尚未生成的文件；运行时只在作业真正结束后读取并附在完成结果中，启动回执不附图。入口脚本须在退出前保存最终合限图片，模型收到完成结果后检查附图获取状态。常驻服务运行期间需要查看截图时，另发同步 shell 调用并声明当前图片，不等待服务结束。

后台 helper 先写本次启动的权威登记，再创建 supervisor 和实际命令。启动回执为空、损坏或启动 helper 中断时，运行时核对本次登记及 supervisor 身份，恢复同一作业的回执，后续仍只交付一次完成结果；不会重新执行命令。若确认启动 helper 已退出且没有登记，返回普通启动失败；登记不匹配、进程归属或清理状态无法确认时，返回 `unconfirmed` 错误及已有登记、日志位置，让模型继续诊断和恢复，不终止整个 Agent。未确认的必需 task 保留正常结束门槛，恢复已确认的监管状态或确认退出及清理后才解除；已核实的 service 保持原本不阻塞结束的生命周期，但状态未确认不能作为服务就绪证据。停止或替换已登记任务应使用回执中的 `state_dir` 和内部 `name` 调用管理 helper 的 `stop`，避免 `pkill -f` 按命令文本误杀新启动链。

完成后，Agent 在工具结果配对完成、下一次模型请求之前将真实状态、有限日志及完整日志路径送入工作上下文；模型响应期间完成的结果先保存，安全边界再交付。每次运行有稳定ID，原始会话中的投递记录用于去重。存在未完成 task 时，无工具调用的回复会进入等待，收到结果后继续请求模型；不把启动成功替代为任务完成，也不在无新结果时反复请求模型。

后台 supervisor 只保存作业记录，界面观察线程只更新显示，工作上下文由 Agent 主循环统一写入。当前回复的整组工具调用必须全部配齐结果后，才能追加后台完成观察；模型正在输出时不改动在途请求。上下文计数、压缩和拼接结束后再次收取结果，有新结果则重建请求；API 每次重试发送前也执行此检查。观察是标明运行时来源的新消息，不重复返回已经收到启动回执的旧工具调用，也不替换真实用户任务。最后一次检查之后才完成的任务在下一安全边界投递，尚未结束的 task 仍会阻止正常结束。

常驻服务使用同一 shell 接口的 `run_in_background=true, background_lifecycle=service`，启动后还须检查就绪和实际业务行为，再交付地址、作业ID、绝对状态/日志路径和准确的停止命令。成功回合的服务可跨回合及正常 CLI 退出继续运行；失败或取消回合清理本回合新建任务和服务，保留此前成功交付的服务。benchmark 明确拒绝 service，退出时清理本任务登记工作。当前 Docker sandbox 工具每次使用短生命周期容器，不支持这套后台协议；整个 Agent 在 ALE 容器内运行时，其 shell 仍可使用受管后台 task。

内置 [process-management skill](../skills_agent/builtin_skills/process-management/SKILL.md) 保留为已登记作业的管理能力，通过 `status/wait/logs/stop` 读取状态和日志、等待就绪或退出、停止任务。模型需要中途管理时才读取，不要求每次后台启动额外读取；不再通过 helper 的 start 启动作业。使用回执中的 `state_dir` 和内部 `name`，不要以显示名称推测。管理命令本身使用同步 shell。停止仅由确认归属的 supervisor 清理自己的进程组，归属丢失时明确报告；不支持交互输入或自行脱离监管的 daemon。分回合使用见[进程生命周期示例](../skills_agent/builtin_skills/process-management/references/across-turns.md)。

交互界面为 shell 自动登记的每个后台作业单独显示名称、状态和耗时，包括启动中、运行中、停止中、监管状态未确认、完成、失败、已超时和已停止；失败时显示退出码。状态行固定在首次出现的聊天位置，后台计时精确到0.1秒，多个作业并行、模型回复或等待下一条用户消息时仍原位更新。监管状态未确认的错误进入上下文时，在交付节点显示诊断记录，保留原动态状态行；它不是完成通知。作业退出但结果尚未交付时，原位置保留最终状态和耗时；结果进入工作上下文时撤下动态状态行，在该交付节点显示一条完成记录，后续对话继续排在它之后。完成记录随聊天滚动，不固定在末尾；迟到的旧状态快照不会让已交付作业重新出现。F2选择模式冻结画面，返回后显示最新状态。动画和轮询快照不增加模型调用，也不写入对话或历史回放；完成结果则独立持久化并进入工作上下文。自行绕开后台接口启动的进程不具备这套登记和结果通知。

内置 [investigation skill](../skills_agent/builtin_skills/investigation/SKILL.md) 增加已知 HTTP(S) URL 的获取脚本，分别保存响应体、来源元数据和可选提取文本，保留实际 HTTP 状态、重定向、完整性与哈希；有界预览不足时可继续读取保存资料。默认响应上限 64 MiB，网络 deadline 覆盖重定向与渐进读取，超限或失败保留已取得的资料并明确标记不完整。它提供资料获取，不提供搜索、登录或页面脚本执行。

### Sandbox

```sh
.venv/bin/skills-agent run --sandbox-image python:3.12-slim
> 在 sandbox 中运行 Python 脚本分析当前目录数据
```

提供 `--sandbox-image` 才注册 `sandbox` 工具，需要可运行的 Docker。每次调用启动一个临时容器：禁用网络，工作目录读写挂载，外部技能和会话附件目录只读挂载。脚本依赖需预装在指定镜像中。`shell` 始终在宿主机执行；模型通过 `sandbox` 工具明确选择容器执行，容器失败会返回错误。

## 提示词版本

[提示词版本时间线](prompt-history/README.md) 按独立提示词保存历史版本、变更目的和当时的完整文本。历史记录可能包含已经移除的流程；当前行为以运行时代码和现有技能为准。

## 验证

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
```

测试通过本地模拟模型覆盖两种协议：读取技能与参考源码、返回新脚本源码、写入执行、根据错误修改、验证结果并续聊。同时验证多轮交互与恢复历史、长命令实际执行与完整记录、工作上下文增量保存、压缩程序检查与历史检索、附件编码、工具配对、子会话显示隔离、进程中断清理和 Docker 参数。进程生命周期回归会实际启动后台写入任务，检查正常结束、失败和信号中断后的退出与文件稳定性，并验证跨回合和跨会话的所有权边界。ALE 测试需要本地官方源码，运行方法见接入说明。模拟模型与容器内回归验证协议、运行环境及执行链路；真实模型的任务完成质量和 ALE 评分仍需另行运行 smoke 验收。

协议依据：[OpenAI token计量](https://developers.openai.com/api/docs/guides/token-counting)、[OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create)、[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[OpenAI 流事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[OpenAI 文件输入](https://developers.openai.com/api/docs/guides/file-inputs)、[Anthropic 流事件](https://platform.claude.com/docs/en/build-with-claude/streaming)、[Anthropic 工具调用](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)、[Anthropic 图片](https://platform.claude.com/docs/en/build-with-claude/vision)、[Anthropic PDF](https://platform.claude.com/docs/en/build-with-claude/pdf-support)。
