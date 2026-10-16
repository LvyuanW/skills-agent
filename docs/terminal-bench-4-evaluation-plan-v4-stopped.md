# Terminal-Bench 4.0：三种 Agent 的 56 题评测方案

版本：2026-10-16 · `tb4-56-flash-max-v4` · 时区：Asia/Shanghai。

本方案比较 Codex CLI、Claude Code 和 skills-agent 在同一模型底座上的完整任务执行能力。三台服务器各跑一种 Agent，每种 Agent 完成 56 题 × 3 次，共 504 个正式执行槽位。模型统一使用 `deepseek-v4-1-flash-260910`，主 Agent 和子 Agent 的思考档位显式设为 **max**。

本文记录评测协议和执行状态。三个 Agent 的适配层和资源调度器已部署，三台各 112 个环境阶段已全部通过，真实合成基础预检已通过；skills-agent 的图片语义预检 0 分及其它限制已保留并披露。用户随后明确授权启动，三台于北京时间 **2026-10-16 03:48:14** 同时启动正式评测，每台 168 个固定槽位；**11:48:41 按用户要求停止，11:49:08 确认三台全部停止**。启动与准备证据见 [服务器记录](terminal-bench-4-evaluation/server-preparation-2026-10-16.md)，实时进度以各机正式 SQLite 状态为准。耗时和费用预算仍为规划值。

## 1. 固定的实验条件

| 项目 | 约定 |
| --- | --- |
| 测试集 | Terminal-Bench `v4.0.0` 的固定 56 题子集 |
| 数据集 commit | `452bf305c6daa62fc59061d22133a7cbc7c1572e` |
| 筛选 | 无 GPU；Agent 主环境为 2 或 4 vCPU；Verifier 主环境不超过 4 vCPU |
| 重复次数 | 每题每 Agent 3 次，彼此独立 |
| 工作量 | 每台 168 个正式槽位，三台合计 504 个；基础设施补跑另计 |
| 模型 | `deepseek-v4-1-flash-260910`；用户指定的网关版本 ID |
| API 网关 | 用户确认 `https://rehdasu.cn/`，三协议真实推理已验证；不涉及站点部署 |
| 思考 | 主 Agent、子 Agent 均为 `max`；max 仍开启思考 |
| 服务器 A / tbench-1 | `47.91.21.152`，32 vCPU / 标称 128G，运行 Codex CLI |
| 服务器 B / tbench-2 | `8.216.13.123`，32 vCPU / 标称 128G，运行 Claude Code |
| 服务器 C / tbench-3 | `8.216.11.161`，32 vCPU / 标称 128G，运行 skills-agent |
| 预留 | 每台 2 vCPU / 8 GiB，供操作系统及其它服务共同使用 |
| 评测总上限 | 每台 30 vCPU / 115 GiB，涵盖所有评测相关进程和容器 |
| 初始任务准入预算 | 每台 28 vCPU / 107 GiB，剩余 2 vCPU / 8 GiB 留给评测控制及额外开销 |
| 单次 Agent 时间 | 保留任务官方 28,800 秒，即 8 小时 |
| 验证时间 | 保留每题各自的官方 verifier timeout，不统一缩短 |
| 主指标 | 168 个正式槽位的 reward 均值，表达为成功率 |

2026-10-16 SSH 实测：三台 `MemTotal` 均约 123.66 GiB。扣除预留的 8 GiB 后向下取整，将评测总上限设为 115 GiB，任务准入预算设为 107 GiB。CPU 0–1 留作其它服务，评测进程与容器限制在 CPU 2–31。部署映射见 [servers.json](terminal-bench-4-evaluation/servers.json)。

这次比较的是 Agent 系统，包括原生提示词、工具使用、压缩、技能加载和子 Agent 调度。相同模型与 max 档不等于相同 token 消耗，所以成功率、成本和耗时必须一起报告。

## 2. 测试集与版本冻结

### 2.1 为什么是 56 题

官方 v4.0.0 共 66 题，其中 63 题不要求 GPU。筛选后留下 **44 道 2 vCPU 题 + 12 道 4 vCPU 题**。筛选同时检查 Agent 和 Verifier；`payments-pipeline-fix` 虽然 Agent 只需 4 vCPU，但 Verifier 要 6 vCPU，因此排除。[官方版本](https://github.com/harbor-framework/terminal-bench/releases/tag/v4.0.0)

排除的 10 题：

| 原因 | 任务 |
| --- | --- |
| 要求 GPU | `fp8-rmsnorm-gemm`、`jax-speedrun-gpu`、`math-eval-grader` |
| Agent 主环境为 8 vCPU | `distributed-dedup`、`formal-crypto`、`lake-temp-glm`、`shadow-relay`、`wdm-design` |
| Agent 主环境为 16 vCPU | `live-database-cutover` |
| Verifier 为 6 vCPU | `payments-pipeline-fix` |

56 题覆盖全部 7 个一级领域：

| 领域 | 题数 | 主要内容 |
| --- | ---: | --- |
| Software | 15 | 系统调试、前端性能、数据库、算法、数据工程 |
| Science | 12 | 生物、化学、数学证明、物理、机器人、语言学 |
| Operations | 9 | 物流、财务、业务处理、合规、生产计划 |
| ML | 8 | 评估一致性、推理系统、检查点、训练故障 |
| Hardware | 5 | CAD 建模、工程制图、RTL |
| Media | 4 | 视觉布局、和声、音频转录 |
| Security | 3 | 应用安全、密码分析、取证 |

子集占全量 84.8%，占无 GPU 集 88.9%。它适合比较这三套 Agent 在该范围内的能力，但硬件筛选不是随机抽样，不能换算成官方全量分数。

发布名称使用：**Terminal-Bench 4.0 自选 56 题，Agent/Verifier 主环境 ≤4 vCPU、无 GPU，每题 3 次，DeepSeek Flash / max**。主环境资源不包括附加服务容器，不能把这一名称解释成每道题全部容器合计最多 4 vCPU。

### 2.2 随方案保存的清单

- [tasks.txt](terminal-bench-4-evaluation/tasks.txt)：排序后的 56 个任务名。
- [task-manifest.json](terminal-bench-4-evaluation/task-manifest.json)：每题领域、主环境与 Verifier 资源、超时、附加服务标识和来源。
- [trial-plan.jsonl](terminal-bench-4-evaluation/trial-plan.jsonl)：504 个预先编号的正式槽位，包含 Agent、服务器和重复序号。
- [checksums.sha256](terminal-bench-4-evaluation/checksums.sha256)：上述文件的校验值。

这些文件是本实验的清单格式，**不是 Harbor JobConfig**。正式启动器须将它们转换为所锁定 Harbor 版本支持的任务配置，不能直接传给 `harbor run -c`。

### 2.3 正式启动前冻结的内容

固定数据集 commit、Harbor commit/版本、Agent 精确版本、适配器 commit、依赖锁文件、wheel/安装包 SHA256、模型目录、运行参数、任务提示词原文和镜像 digest。安装时不能使用浮动 `latest`。任务及评分器保持官方版本，不根据某 Agent 的失败修改测试。 本次三道 FreeCAD verifier 存在上游新依赖构建冲突，统一使用外置兼容配置 `freecad-conda-vtk92-compat-v1`，只将 PyVista/VTK 固定为 0.48.4/9.2.6，validator 仍为 0.1.3、测试代码不改。该环境偏离需随结果披露，不能声称是未加约束的原始运行环境。

本次检查 Harbor 源码的基线为 `aad19e235922ee5007ac635a96a1c2f72eef9dfc`；这只是接入核查依据，正式使用的版本须通过预检后锁定。按用户最新要求，skills-agent 改用 `41a6bf87f3eca29c73a876c43af8dfd2a2c50d63` 加当前工作区尚未提交的 CLI/terminal 更新。已冻结独立源码快照，包 SHA256 为 `31829dde914bd50997ca5d7d72304ce176ca70dfaba5f60c0a152e28bd4820ae`；源码树摘要为 `2591210afb868d20506a912d28e4034ed0991ec8a550aa5ae7632d989c417fd8`。包版本字符串仍为 0.1.0，用内容哈希区分旧包；旧预检不作为新版验收。

本次由用户确认使用第三方网关 `https://rehdasu.cn`，并指定 `deepseek-v4-1-flash-260910`。该版本 ID 已在最小 Chat、Responses、Anthropic 请求中成功返回，但网关返回名称不能独立证明上游权重永久冻结。三台尽量在同一时间窗口运行，保存评测日期、官方模型说明快照及 API 返回的模型标识。若中途服务版本变化，应另列实验分组，不能隐去差异。[模型说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

## 3. 统一模型与 Agent 策略边界

### 3.1 max 的具体含义

| 客户端 | 显式设置 | 预检应看到的请求字段 |
| --- | --- | --- |
| Codex / Responses | `model_reasoning_effort="max"`；Harbor `--ak reasoning_effort=max` | `reasoning.effort = "max"` |
| Claude Code / Anthropic | `CLAUDE_CODE_EFFORT_LEVEL=max`；Harbor `--ak reasoning_effort=max` | `output_config.effort = "max"`，思考保持开启 |
| skills-agent / Chat | 小写环境变量 `effort=max` | `reasoning_effort = "max"` |

本实验在出站请求中显式发送 max，并保持主/子 Agent 思考开启；第三方网关的上游映射以其实际服务为准，不能用客户端日志证明权重和服务端实现。不要用关闭思考、`none` 或零思考预算代替 max。max 控制思考强度，不会自动提高单请求输出上限；下文列出的各 Agent 输出限制仍独立生效。预检检查脱敏后的真实出站参数，不能只看启动日志里的模型名称。[官方思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)

所有主会话、子 Agent、压缩摘要及其它辅助模型调用都必须路由到 Flash。开启思考的主/子 Agent 调用统一 max。原生关闭思考的辅助调用保留原策略并单独记录；例如新版 skills-agent 的上下文摘要及工具报告辅助调用原生关闭思考。因此结果描述不能写成“每个 API 请求都启用了 max 思考”。新版预检实际观测到工具报告 disabled，已独立记录。

### 3.2 保留并记录的策略

保留各 Agent 正常的系统提示词、工具实现、子 Agent 调度、单请求输出限制及上下文压缩策略；固定其配置快照。模型支持约 1M 上下文，但各 Agent 的实际工作窗口和压缩阈值可以不同，须在结果中披露。主评测不强制统一成 32K 单请求输出，以免将特定的输出截断策略混入实验。

skills-agent 本方案固定其当前正常配置：工作上下文 1,000,000 tokens、主输出上限 32,768、摘要上限 8,192、实际模型窗口 1,048,576；发送前应确保输入与预留输出合计能装入模型窗口。其它两个 Agent 的有效输出限制及压缩阈值在预检时导出并冻结，不假定 CLI 默认值相同。

保持相同的网络权限与任务附件访问权限，遵循每题网络设置。保留任务提供的技能、浏览器/MCP 和工具接入，不向 Agent 暴露 verifier、参考解或其它 trial 产物。固定随 Agent 发布的技能包；不加载操作者个人目录、历史会话或任务专用补丁提示词。

本实验关闭厂商专有的服务端 Web Search。正常终端联网和任务提供的浏览器能力按任务规则保留。原因是不同协议的服务端搜索支持和隐含模型调用不同；Claude Code 的 DeepSeek Web Search 还会产生额外摘要请求。关闭设置须在预检中验证。[Responses 支持范围](https://api-docs.deepseek.com/guides/responses_api/)、[Claude Code 搜索说明](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/)

## 4. 三台服务器的环境和资源调度

### 4.1 环境准备

三台使用一致的 Linux x86_64 发行版、Docker Engine / Compose、cgroup v2、Harbor 与依赖版本。Harbor 主机运行时使用 Python 3.12 或所锁定版本明确要求的版本。记录 CPU 型号、物理/逻辑核拓扑、内存实容量、磁盘类型、网络延迟；相同的“32v128g”不能自动排除硬件差异。

每台建议配置 **500 GB SSD 数据盘**，Docker 数据、镜像缓存和评测结果放在数据盘。长期保存镜像及完整轨迹可选 1 TB。500 GB 是规划建议，不是官方保证值；最终容量以所有任务环境预热后的实际占用为准。

本次三台实际配置均为 500 GiB 数据盘。完成全部预热后各已用约 261–262 GiB，空闲约 229 GiB；这是准备阶段的实测值，正式轨迹、截图及容器可写层仍会继续占用空间。

磁盘容量按以下组成核算：基础与任务镜像共享层 + 构建缓存 + 并发容器可写层/volume + 轨迹/截图/产物 + 余量。`storage_mb` 不能直接当成 Docker 宿主机已强制执行的磁盘限额。首次预热后应仍保留至少 20% 空闲空间，且大于下一批任务预计新增量；不足则扩盘或只清理已确认不用的缓存。自动删除容器不等于删除镜像和构建缓存。

### 4.2 落实 2v / 8g 预留

本次在三台宿主机建立共同 cgroup `tb4.slice`，使用 `AllowedCPUs=2-31`、`CPUQuota=3000%`、`MemoryMax=115G`、`MemorySwapMax=0`。单纯 CPUQuota 只限制总 CPU 时间，所以同时设置 cpuset，保证评测不会占用 CPU 0–1。

总上限必须覆盖 Harbor、Agent、Verifier、附加服务容器和镜像构建器。仅给 Docker daemon 设置 systemd slice 并不能保证所有容器都进入该限制；上线前用实际容器 cgroup 路径、CPU 亲和性和内存限制验证。对预算内进程关闭或限制 swap，避免靠换页把时间成绩扭曲。

预留的 8 GiB 包含操作系统与其它服务的峰值。只有其它服务峰值和系统开销确实装得下，才能说“够”；若不够，降低评测预算。把常驻服务峰值和资源隔离测试结果写进环境记录。

### 4.3 资源加权调度

初始设置：单容器题**最多 14 个活动 trial，常见实际并发约 10–12 个**；9 道多容器题每次仅运行 1 个 trial，独占准入池。控制器同时检查 CPU 与内存预算，不使用固定并发数代表资源保证。

每个 trial 的预留量按阶段计算：

```text
trial_cpu = max(Agent 主容器 + 同阶段附加服务,
                Verifier 主容器 + 同阶段附加服务)
trial_ram = max(Agent 阶段总内存, Verifier 阶段总内存)

准入条件：
  所有活动 trial 的 cpu 预留总和 + 新 trial_cpu <= 28
  所有活动 trial 的 ram 预留总和 + 新 trial_ram <= 107 GiB
  活动 trial 数 < 14
  磁盘和宿主机健康检查通过
```

Separate verifier 在 Agent 环境停止后运行时，按阶段取最大值即可；若所用版本或某任务存在阶段重叠，则按重叠峰值合并。任务原本的主容器 CPU/内存限额不降低。Docker 的 enforcement 使用 `cpu_enforcement_policy: limit` 与 `memory_enforcement_policy: limit`；不能假定 Docker 支持 Harbor 的资源 reservation/guarantee。[资源文档](https://docs.harborframework.com/tasks/resources)

无附加服务、内存允许时的装箱示例：

| 同时运行的主容器 | CPU 合计 | trial 数 |
| --- | ---: | ---: |
| 14 × 2 vCPU | 28 | 14 |
| 8 × 2 vCPU + 3 × 4 vCPU | 28 | 11 |
| 4 × 2 vCPU + 5 × 4 vCPU | 28 | 9 |

这些只是 CPU 例子，还要通过内存与附加服务检查。`takens-embedding-lean`、`medical-claims-processing`、`uefi-bootkit` 的主环境为 16 GiB，不应按普通 4 GiB 题计算。

调度器按预留 CPU×内存降序、任务名、重复序号排序，再选择当前资源池能容纳的槽位。三台使用相同规则；完成顺序允许不同，不要求整轮结束后才开始下一轮。历史时长只用于容量预测，不参与实际排序，也不按评测中观察到的成功率挑题。

**Harbor 的 `-n` 只控制 trial 数，不实现以上资源池。** 需要外层调度器统一发放 CPU/内存额度；不能开多个独立 Harbor job 后假定它们自动协调。建议调度器一次启动一个槽位对应的 `-n 1 -k 1` 作业，唯一 job-name 关联 slot-id；168 个槽位由控制器负责，不再在每个槽位上加 `-k 3`。[运行作业](https://docs.harborframework.com/jobs/run-a-job)

### 4.4 必须标定的 9 个多容器任务

`ctr-optimization`、`cumulative-layout-shift`、`freight-dispatch-shift`、`heat-pump-warranty`、`intrastat-meldung`、`kv-live-surgery`、`legacy-utility-triage`、`medical-claims-processing`、`nextjs-performance`。

它们包含浏览器、Web 服务、负载生成器等附加容器。`kv-live-surgery` 的负载生成器还显式声明了额外 1 vCPU。部分服务未声明完整限制，所以 manifest 中的附加资源预算暂为待标定值，不能当成零。

预热仅记录启动样本，不能当成完整工作负载峰值。本次 9 道多容器题采用独占准入：每个 trial 预留完整 28 vCPU / 107 GiB 池，保持官方主容器和附加服务配置。其余 47 题按声明的 Agent/verifier 较大资源加权并发。资源预算与独占规则在正式开始前冻结，批次中不直接调整；实际资源不足时先停止新准入并核对，不能把“控制器余留 2v8g”当成所有附加服务的通用预算。

在正式阶段尽量复用镜像。`medical-claims-processing` 的官方 Compose 显式设置 no_cache 与 pull_policy=build，正式 trial 仍会触发构建，不能声称预热消除了全部构建开销。预热时构建并发先设 1–2，并计入总上限。准备、Agent 运行和验证时间分别记录，首次拉镜像/安装时间另列。

## 5. 三种 Agent 的接入

以下配置已由部署的薄适配器实现。实际配置见 `adapters/harbor/assets/{codex,claude}` 及 `tb4_agents/assets/skills`；正式启动器需额外匹配批准文件，准备过程不会生成此文件。

### 5.1 Codex CLI

复用 Harbor 内置 Codex 适配器，补充模型目录上传或只读挂载。通过用户指定网关的 Responses API，固定 Codex CLI `0.149.1`。Codex 配置关键字段如下：

```toml
model = "deepseek-v4-1-flash-260910"
model_provider = "deepseek"
preferred_auth_method = "apikey"
forced_login_method = "api"
model_reasoning_effort = "max"
web_search = "disabled"
model_catalog_json = "/installed-agent/codex-home/models.json"

[model_providers.deepseek]
name = "deepseek"
base_url = "https://rehdasu.cn/v1"
wire_api = "responses"
env_key = "OPENAI_API_KEY"
```

认证字段已由固定的 Codex 版本通过预检。宿主密钥文件中的 `DEEPSEEK_API_KEY` 经 Harbor 的 Agent 环境配置映射为容器内 `OPENAI_API_KEY`。保留 DeepSeek 官方 `models.json` 原始快照，实验派生目录仅保留指定 Flash 版本及 max，分别记录来源与哈希；图片输入、1,048,576 上下文等元数据沿用官方 Flash 条目。子 Agent 指定 Pro 或 low 的离线请求已确认被原生拒绝。当前官方目录声明 Codex 最低版本 0.144.0，实际固定版本为已通过 smoke 的 0.149.1。[DeepSeek Codex 接入](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/)、[Codex provider 配置](https://developers.openai.com/codex/config-reference/)

Harbor 不会自动复制配置引用的 `models.json`。本次薄适配器在每个 trial 上传实验目录到上述容器内路径，并使用全新 Codex 会话和用户配置目录；模型目录的宿主路径只用作上传来源。

### 5.2 Claude Code

使用继承 Harbor 内置逻辑的薄适配器，固定 Claude Code `2.1.300`；通过 Agent 环境变量将全部模型路由到指定 Flash 版本：

```text
ANTHROPIC_BASE_URL=https://rehdasu.cn
ANTHROPIC_AUTH_TOKEN=<由密钥存储注入>
ANTHROPIC_MODEL=deepseek-v4-1-flash-260910[1m]
ANTHROPIC_DEFAULT_OPUS_MODEL=deepseek-v4-1-flash-260910[1m]
ANTHROPIC_DEFAULT_SONNET_MODEL=deepseek-v4-1-flash-260910[1m]
ANTHROPIC_DEFAULT_HAIKU_MODEL=deepseek-v4-1-flash-260910
CLAUDE_CODE_SUBAGENT_MODEL=deepseek-v4-1-flash-260910
ANTHROPIC_DEFAULT_FABLE_MODEL=deepseek-v4-1-flash-260910[1m]
ANTHROPIC_SMALL_FAST_MODEL=deepseek-v4-1-flash-260910[1m]
CLAUDE_CODE_EFFORT_LEVEL=max
CLAUDE_CODE_AUTO_COMPACT_WINDOW=786432
```

同时设 Harbor `--ak reasoning_effort=max`。关闭 WebSearch 工具的具体参数应按锁定 CLI 版本配置并验证。`[1m]` 是客户端上下文提示，底层仍须确认实际调用 Flash。不能只设置主模型：DeepSeek 对未覆盖的 `claude-opus*` 名称可能映射到 Pro。[官方接入与模型映射](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/)

### 5.3 skills-agent

已新增 `BaseInstalledAgent` 适配层，复用现有单任务 benchmark 入口，负责安装固定发布包/依赖/tokenizer、提交官方 prompt、调用入口、处理终止和回收日志；不要借此修改 Agent 解题逻辑。[Harbor 自定义 Agent](https://docs.harborframework.com/agents/custom-agents)

容器内调用示意，路径由适配器确定：

```sh
effort=max python -m skills_agent.benchmark \
  --prompt-file /opt/eval/instruction.txt \
  --workspace /app \
  --log-dir /logs/agent/session \
  --provider openai \
  --model deepseek-v4-1-flash-260910 \
  --base-url https://rehdasu.cn/v1 \
  --api-format chat \
  --token-counter deepseek \
  --tokenizer-path /opt/agent-assets/tokenizer.json \
  --tokenizer-encoding v41 \
  --context-tokens 1000000 \
  --model-context-window 1048576 \
  --max-output-tokens 32768 \
  --summary-output-tokens 8192
```

将 `OPENAI_API_KEY` 通过 Agent 环境注入；不要写入命令和日志。`/app` 必须替换为任务真实工作目录，日志子目录须全新。`--tokenizer-path` 指向具体 tokenizer 文件，不是目录。安装带 DeepSeek 依赖的发布包与匹配的 tokenizer，记录哈希及准确依赖版本。核心发布包仅在官方 DeepSeek host + Chat 协议注入 effort；本次由容器适配层对用户指定网关补齐同样的 max 请求字段，保持发布 wheel 不变。已对原生 tokenizer 计数与发送 payload 一致性、图片计数、辅助摘要禁用思考的例外进行离线验证。小写 effort 环境变量名称保持原生约定。

Agent 已在 Harbor 任务容器内运行，不再配置 `--sandbox-image` 创建第二层工作沙箱。适配器还须验证图片读取、任务附带技能/工具、长时子进程清理和失败日志回收；这些接入能力必须在正式运行前完成。

### 5.4 凭据与请求并发

三把网关 key 分别对应 `tbench-1`、`tbench-2`、`tbench-3`，已仅向各自服务器下发。文件 `/data/tb4/secrets/deepseek.env` 由 tbench 持有、权限 0600，目录 0700；仓库只保存凭据引用。三把 key 均通过网关 `/v1/models` 鉴权。用户提供的是第三方网关 key，DeepSeek 官方账号的 2,500 并发规则不能直接视为该网关承诺；网关配额和计费须以网关为准。[官方规则仅供参照](https://api-docs.deepseek.com/quick_start/rate_limit/)

可把每台 64 个在途 API 请求作为初始保护上限，三台合计最多 192；这是调度器/出口层待实现的保护策略，不是 Harbor 自带开关。设置对三台一致，记录排队时间；若它明显限制某 Agent 的原生并行能力，应在预检时统一提高并冻结。保留并记录三个客户端原生的 429/5xx 退避策略，所有等待都计入本次 8 小时 Agent 时间。skills-agent 部分辅助请求目前按固定间隔重试、没有内部次数上限，必须由外层超时负责终止，不能将其描述为已具备有限次数重试。

任意宿主环境变量不一定自动进入任务容器，须使用所锁定版本的 `agents[].env`/`--ae` 或适配层显式传入。密钥只给需要调用 API 的 Agent 阶段，配置归档必须脱敏。[Harbor 环境变量](https://docs.harborframework.com/jobs/environment-variables)

## 6. 执行顺序

| 阶段 | 工作 | 完成标准 |
| --- | --- | --- |
| A：部署 | 固定版本、Docker、数据盘、资源隔离，安装三个 Agent 的接入层 | 环境记录齐全；确认其它服务的实际预留空间 |
| B：预热与标定 | 构建/拉取任务环境，测试 verifier 依赖，补齐 9 题的附加资源预算 | 56 题均能创建固定环境，兼容依赖配置已披露，磁盘余量合格 |
| C：合成 smoke | 在正式 56 题之外的合成任务验证读写、命令、图片、工具往返、max、子 Agent、摘要、评分及超时 | 三种客户端实际路由正确；用量可追踪；没有会话污染 |
| D：冻结 | 保存版本、配置、manifest、镜像、预热清单摘要、调度规则及哈希 | 开始正式运行后不再按结果修改 Agent；显式重预热改变清单后拒绝续跑原批次 |
| E：正式 | 三台同时按资源池执行各自 168 个槽位 | 每个槽位有终态和日志；基础设施异常按预先规则处理 |
| F：汇总 | 对齐 56×3 结果，计算分数、稳定性、费用、时间、置信区间 | 完整结果表和可追溯报告 |

当前控制器按固定资源池准入，不提供批次中的阶梯并发开关，所有正式槽位均计入各自 168 次。若要采用更低的初始并发，应在正式开始前实现、预检并冻结相同策略。若必须修改冻结的资源规则或 Agent/适配器逻辑，则创建新实验版本并重新定义完整批次，不能混合修改前后的成绩。

任务运行期间：先新建干净环境，再安装 Agent 和提交官方指令；Agent 结束或超时后调用官方 verifier；回收结果、轨迹与资源记录，最后销毁容器及可写 volume。三次重复分别使用新工作区、用户目录、会话和日志目录。可复用不可变镜像/依赖缓存，不共享解题产物或跨题记忆。

监控每台 CPU、RAM、cgroup OOM、磁盘/IO、活动槽位、API 在途请求、429/5xx、累计 token/金额和完成数。磁盘低于阈值或总资源逼近上限时停止新任务准入，先让在途任务正常结束；不能悄悄杀掉慢题再从分母中删除。

## 7. 失败、重试与完成条件

| 情况 | 正式处理 |
| --- | --- |
| verifier 正常给出 0 | 计失败；不整题重跑求更高分 |
| Agent 自身异常、上下文耗尽、工具策略失败 | 计 Agent 结果；尽可能继续验证产物 |
| Agent 达到 8 小时 | 停止 Agent 后仍运行 verifier，采用实际 reward；超时不自动等于 0 |
| 创建环境失败、平台故障、非 Agent 引起的网络中断 | 经日志确认的基础设施错误，可替换执行该槽位，最多额外 2 次 |
| API 请求级暂时错误 | 按冻结的客户端策略重试，计入当前 trial 时间和费用；总时长受外层 8 小时限制 |
| 错误模型、effort 未生效、适配器系统性错误 | 暂停新任务，修复后另建一致的实验批次；不能把配置错误解释成能力分 |
| verifier 抛错或 reward 缺失 | 保留异常记录；核实是基础设施、评分器还是 Agent 导致，再按对应规则处理 |

每次执行都有 attempt-id，重试关联原 slot-id，并保留原因、成本和产物。基础设施替换执行采用**第一份有效终态结果**；不得在多次有效结果中挑最高分。原始失败尝试不删除。

Harbor 默认聚合将缺失 reward 视为 0，但不同版本的错误分类及重试聚合行为仍须预检。报告统一保留计划分母 168：未解决的基础设施槽位暂作 0 贡献，并明确标记“批次未完整完成”、列出覆盖率；不能静默缩小分母。只有 168 个槽位都形成有效成功或 Agent 失败终态，才把该 Agent 的主结果视为完整。[官方默认聚合](https://docs.harborframework.com/datasets/metrics)

## 8. 评分、稳定性与比较方法

令 `r[a,t,j]` 为 Agent a 在任务 t 第 j 次运行的官方 reward。该测试集采用通过/失败结果，主分为：

```text
score[a] = 100 × sum(r[a,t,j]，t=1..56，j=1..3) / 168
```

例如一道题三次结果为 `1、0、1`，该题平均成功率为 66.67%；它对全套分子的贡献是 2。若全套成功 90 次，则主分为 `90 / 168 = 53.57%`。这些是计算示例，不是本次实测。

报告以下并列指标：

| 指标 | 计算或含义 |
| --- | --- |
| 主分 | 168 次 reward 均值，可理解为重复测量的平均单次成功率 |
| 至少成功一次 | 三次中至少一次通过的题数 / 56；单独报告，不能替代主分 |
| 三次均成功 | 三次都通过的题数 / 56，反映稳定性 |
| 逐题结果 | 每题 `0/3、1/3、2/3、3/3` |
| 分领域结果 | 各领域按其任务数×3作为分母 |
| 时间 | Agent 阶段 P50/P95、总 trial 时长、整台完成时间；冷启动另列 |
| 成本 | 完整 API 成本、每槽位平均成本、每成功槽位成本；成功数为零时后者为 N/A |
| 质量与运行状态 | 超时、输出截断、Agent 崩溃、基础设施异常、模型路由异常、用量缺失 |

95% 置信区间采用 task 层面的 cluster bootstrap：按 56 道题有放回抽样，每次保留该题全部三次运行；三种 Agent 使用相同的抽样索引。固定统计种子 `20261016`，做 10,000 次重采样，报告分数及两两差值区间。不要把 168 次当作 168 道独立题。正式声明显著性时，还需处理三组两两比较的多重检验；小差距或区间跨零时描述为证据不足。

这 56 题不是全量任务的随机样本，置信区间也不能消除子集选择偏差。三遍可以衡量基本稳定性，不能把微小分差解释成确定排名。

## 9. 预计耗时与运行窗口

目前没有这三个 Agent 在 **Flash / max** 上的完整实测，不能承诺它们分别在固定小时数内结束。此前公开轨迹来自其它模型和配置，只用于服务器容量规划。

下面是此前的容量模拟：将官方历史轨迹筛到同一 56 题，从每题 5 次中抽 3 次模拟 100 组，按当时规划的 28 vCPU / 112 GiB 资源池排队。当前实机任务预算已下调为 107 GiB，以下数值仍仅作为历史参考。模拟使用主环境与 Verifier 的较大资源量，没有完整建模附加服务、IO 竞争、冷启动及失败补跑：

| 历史负载情景 | 旧策略中位数（未计侧车） | 当前独占策略中位数 | 当前策略 P10–P90 |
| --- | ---: | ---: | ---: |
| 公开 Sol / Codex 轨迹 | 7.9 小时 | 30.0 小时 | 29.3–30.6 小时 |
| 公开 Opus / Claude Code 轨迹 | 17.2 小时 | 52.7 小时 | 51.2–54.4 小时 |
| 公开 Sonnet / Claude Code 轨迹 | 22.9 小时 | 70.1 小时 | 64.3–75.2 小时 |

这些是三种**负载情景**，不是本次三个 Agent 各自的预测。max 可能增加单次思考用量，也可能改变尝试次数和成功率；耗时不能由档位按固定倍数推算，须用本次首批实测校准。基础来源：[官方 rollout 图表](https://www.tbench.ai/blog/terminal-bench-4-0/rollout-charts.html)、[Codex 轨迹](https://hub.harborframework.com/jobs/5aebbab5-1f8f-4cdb-8073-c6ce785a930d)、[Opus 轨迹](https://hub.harborframework.com/jobs/a1ac63a1-8a9b-4bc7-9906-2b63657ee1c2)、[Sonnet 轨迹](https://hub.harborframework.com/jobs/a5758f1a-9ef2-4100-9893-34a99c99bd9c)。

**当前对 9 道多容器题实行独占准入，已将相同公开轨迹按 28 vCPU / 107 GiB、100 组抽样重新回放。上述时间仍不是 Flash/max 的实测或承诺。** 建议预留 3–5 天观察窗口，并根据首批正式结果修订。[回放参数与限制](../adapters/harbor/tb4_control/historical-exclusive-schedule.json)。 27 个多容器 trial 的时长需串行累加，其余 141 个 trial 再按加权并发运行；正式首批完成后重估窗口。 三台并行，总历时取最慢一台，而不是将三台时间相加。这不是最坏时长保证；若大量任务用满 8 小时，批次可能持续数天。

每台首批正式结果完成后，按任务资源、领域、已知长尾及仍在运行的任务更新剩余工时预测；不能只拿最先结束的短任务平均值外推。粗略关系为 `剩余 trial 工时 / 有效平均并发`，最终还受最长剩余任务限制。

## 10. API 费用与预算

以下是此前核查的 DeepSeek 官方价格参照，单位为每百万 tokens。**本次使用第三方网关，这些单价、峰谷优惠和预算不构成网关实际报价。** 真实费用待网关账单或明确费率确认；现阶段只验收用量采集，不将未知费用填为零。

| 用量 | 空闲时段 | 高峰时段 |
| --- | ---: | ---: |
| 缓存命中输入 | ¥0.02 | ¥0.04 |
| 缓存未命中输入 | ¥1 | ¥2 |
| 输出，包含思考 | ¥4 | ¥8 |

北京时间工作日（不含中国法定节假日）09:00–12:00、14:00–18:00 为高峰，其余时间为空闲；周末全天空闲。正式执行前复核价格。max 没有单独价格档，影响的是实际用量。[官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

```text
空闲时段费用 = 未缓存输入 tokens / 1e6 × 1
             + 缓存输入 tokens / 1e6 × 0.02
             + 输出 tokens / 1e6 × 4
高峰时段使用对应的两倍单价。
```

按请求发生的实际时段计价并与平台账单核对，不能仅凭开始时间给整个批次套空闲价。推理 tokens 已包含在输出用量中时不再加一次；不同 API 协议的输入/缓存字段须先统一语义。成本包括主 Agent、子 Agent、摘要、重试和基础设施补跑。

把上述历史公开轨迹筛到 56 题、从 5 次线性缩放为 3 次，再套 Flash 价格，可得到以下参照：

| 历史 token 用量情景 | 168 次全部按空闲价 | 168 次全部按高峰价 |
| --- | ---: | ---: |
| Sol / Codex 用量 | 约 ¥106 | 约 ¥212 |
| Opus / Claude Code 用量 | 约 ¥257 | 约 ¥514 |
| Sonnet / Claude Code 用量 | 约 ¥602 | 约 ¥1,203 |

这不是 Flash / max 的报价或置信区间。不同模型的 token 数、缓存命中率不能保证迁移；公开数据还可能有缺失用量及未纳入的先前失败尝试，所以只能作粗略量级参照。

仅在网关确认采用上述官方费率时，旧资金参照为**空闲时段每 Agent ¥300–800、三者合计 ¥900–2,400**，另留至约 ¥3,000 的机动空间。改为 max 后，这些金额仍仅作初始资金安排，须根据首批实际用量重估完成预算。该金额不是保证完成的硬上限，也不包括服务器、磁盘、流量和部署成本；若全程高峰，同用量费用约翻倍。首批正式记录出来后，用实际 tokens 和缓存比例更新预算。

可安排在周五 18:00 后或周末开始，争取主要调用处于空闲时段。预算告警仅停止新槽位准入并说明剩余工作，不删除已花钱的失败运行，也不把尚未完成的批次报告为最终结果。

## 11. 日志与最终交付

每个槽位至少记录：`experiment_id`、`slot_id`、`attempt_id`、Agent/版本、任务/commit、repeat、host、模型请求/响应标识、实际 effort、开始/结束时间、阶段耗时、reward、异常分类、重试关系、输入/缓存/输出用量、金额、CPU/内存峰值、产物和轨迹路径。

在批次层面保存原始 Harbor result、脱敏配置、Agent 原生日志、官方 verifier 输出、安装包及镜像哈希、资源监控、API 账单核对结果。请求 ID 去重，缺失用量标为 unknown，不能悄悄按 0 元计费。缓存按三种 Agent 的实际命中记录，不把新容器等同于模型服务端冷缓存。

最终交付以下内容：

1. 三种 Agent 的总体成功率、95% CI、稳定性、成本与完成时间表。
2. 56 行逐题对照表，每个 Agent 展示三次结果、均值、时间、成本和失败类型。
3. 七个领域的对比及两两差值区间。
4. 504 个正式槽位及所有补跑尝试的可追溯记录。
5. 固定版本、配置、任务 manifest、资源策略、费用口径及已知限制。

## 12. 开始正式执行前的验收清单

- [x] 56 题名单与 commit/hash 一致；每 Agent 恰好 168 个唯一槽位。
- [x] 三台实际硬件、资源预留和磁盘容量已核对；控制进程与 Docker 默认创建的容器通过总资源上限核查，任务环境与预热后的磁盘余量已验收。
- [x] 三台 56 题的 112 个环境阶段均通过，保留镜像现状已核对；9 题采用已审阅的独占资源预算。
- [x] 资源池调度器已部署；加权准入、主机锁、SQLite 持久化、重启冻结与 168 槽位模拟通过。
- [x] Codex 的 provider、实验派生目录、容器路径、真实图片与子 Agent 已验证。
- [x] Claude Code 主模型、别名和子 Agent 均走指定 Flash/max，真实图片和 WebSearch 配置已验证。
- [x] skills-agent 新版固定包的离线与真实基础预检通过，图片字节/子 Agent 链路验证；视觉合成颜色误判、reward=0 原样保留，观察上下文影响原因未定。
- [x] 主/子 Agent 实际请求为指定 Flash / max；原生辅助调用例外已登记。
- [x] 三种 Agent 的技能、图片/工具接入、上下文与输出设置已记录并部署；正式运行前再冻结内容哈希。
- [x] 三台真实合成任务成功、独立 verifier 与清理已验证；故障分类、超时、缺 reward、恢复与防重采样经无模型测试验证。
- [x] 全新会话与跨 trial 隔离已验证；每个预热阶段均停止环境并回收容器及 volume。
- [x] 原生用量统计与 API 审计对齐，父/子会话去重和最多两次基础设施补跑策略已测试。
- [ ] 网关真实费率、额度和账单口径仍待获取，未知费用为 null；未部署所提议的 API 请求出口并发阈值或金额告警。该限制已披露，本次按用户明确授权启动固定批次。

环境准备及接入预检已完成，已按用户授权启动三台、固定 504 个槽位。主机、镜像、依赖、运行时、密钥与当前三个 API 协议均在启动前核验通过；后续 API 服务波动和额度仍可能影响运行，不能以启动预检保证整个批次无外部故障。正式批次保持已冻结的版本、配置、资源策略和预热清单，恢复时不盲目重执行既有尝试。
