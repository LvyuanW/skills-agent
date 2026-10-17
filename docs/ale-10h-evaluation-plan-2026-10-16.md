# ALE01 最新 skills-agent：10 小时目标测评方案

**方案更新：用户最终新增 ALE04，规格为 64 vCPU / 256 GiB；已按 12 题并发启动。** 下面保留扩容前两档容量推算，实际配置与启动证据见 [ALE04 启动记录](ale04-evaluation-launch-2026-10-16.md)。

核查日期：2026-10-16。本文是启动前的容量与调度规划；没有扩容、修改服务器、调用模型或启动新测评。

**建议把 ALE01 扩到 x86-64 的 128 vCPU / 512 GiB，准备约 2 TB SSD 数据盘，使用最多 26 个独立任务 worker，以正式运行 8–10 小时为目标。** 此目标有历史耗时和任务时限模拟支持，但目前不能承诺完整有效评分一定在 10 小时内结束。扩容、调度器开发验证和预热时间另计；如果“10 小时”包含这些准备工作，则当前没有可承诺的方案。

## 本轮固定配置

```toml
context_tokens = 524000
model_context_window = 524000
max_output_tokens = 131072
summary_output_tokens = 8192
```

使用本地最新已提交版本 `6b1c55e2aaa07c246a949307c4f40844ef35b53d`，模型继续为 `deepseek-flash`、effort `low`。核查时 GitHub main 为 `38be40e000c6da91e6722bee76b527288c60634f`；本地领先三个提交，包含额外运行时稳定性修改。正式打包须从固定提交导出，记录 wheel 和运行文件 SHA256；不把未提交的文档混入版本依据。

这四个参数在当前实现中均可设置；DeepSeek 官方 Chat API 文档允许 `max_tokens` 达到 393216，因此 131072 在文档范围内，正式启动前仍需真实小烟测。主请求为输出预留空间后，实际输入预算是 **524000 − 131072 = 392928 tokens**，不能同时容纳 524000 输入再生成 131072 输出。输出上限增加可能缓解长推理截断，也可能延长生成、增加压缩频率，不能假定它能加速。

最新实现已经加入 503/429/连接异常重试和前台 shell 超时，改善了上一批“一次 503 就结束整题”“单命令堵满整题”的问题。不过默认 provider 重试可以持续到 ALE 外层时限，服务不可用仍可能耗尽任务时间；空回复、单次输出耗尽也仍可能终止任务。

## 容量依据与两档选择

ALE01 实测为 8 vCPU、30.197 GiB MemTotal、约 28.84 GiB 可用、无 swap，当前没有任务容器，数据盘剩约 223 GiB。任务配额为：93 题各 4 CPU / 15 GiB，SEC 题 4 CPU / 28 GiB，材料科学题 8 CPU / 28 GiB。两普通题的内存上限已合计 30 GiB，当前主机无法给系统留下足够的峰值余量。

| 方案 | 建议主机与磁盘 | 并发槽 | 依据与适用条件 |
| --- | --- | ---: | --- |
| 历史耗时方案 | 48 vCPU / 192 GiB / 约 1 TB SSD | 8 | 将历史完整任务时长重新调度，约 8 小时；新版本、131072 输出与并发吞吐未测，可能超过 10 小时 |
| 推荐的保守容量方案 | 128 vCPU / 512 GiB / 约 2 TB SSD | 26 | 将所有 Agent 时限用满的模拟仍为 8 小时，给准备、收集和评分留约 2 小时目标余量 |

8 槽在两道资源例外题同时运行时，任务配额最多 36 CPU / 146 GiB。26 槽的对应峰值为 **108 CPU / 416 GiB**，低于 128 / 512 规格。具体准入仍须依据扩容后实测 MemTotal，给宿主机、调度进程、文件缓存和评分进程留余量，不能仅按标称内存计算。

阿里云官方通用实例目录包含 128 vCPU / 512 GiB 档位，例如 `ecs.g9ae.32xlarge`；是否能从 ALE01 当前型号直接升级、所在地域是否有库存，需要在 ECS 控制台确认。只选择 x86-64，保持官方 amd64 Docker 镜像原生运行。优先升级现有 ALE01，保留 `/data` 上的镜像与数据，减少重新复制约百 GB 环境的时间。

## 为什么推荐 26 槽

95 题 Agent 时限分布为 **91 × 2 小时 + 2 × 4 小时 + 2 × 8 小时 = 206 题小时**。未显式写时限的 humanoid 题使用 ALE 默认 7200 秒。

- 8 小时题：`engineering/aerospace_low_thrust_trajectory`、`physical_sciences/gillespie_gene_regulatory_network`。
- 4 小时题：`computing_math/branch_bound_atsp`、`physical_sciences/computational_materials_science`。

按“时限长的先跑、同级按历史长任务优先”的队列模拟：

| 槽数 | 所有任务用满 Agent 时限时的纯 Agent 排程 |
| ---: | ---: |
| 8 | 26 小时 |
| 16 | 14 小时 |
| 20 | 12 小时 |
| 24 | 10 小时 |
| 26 | 8 小时 |

24 槽的模型已经用满 10 小时，没有其他阶段的余量。26 槽能够把两道 8 小时题放在第一批，其余任务也在该模型中于 8 小时内完成。实际新任务时长未知，不能将模拟的时间戳当作真实完成承诺。

历史完整批次的实际时长重新做 LPT 模拟：旧 skills-agent 约 51.50 题小时，3 槽约 17.17 小时，8 槽以上约 8.01 小时；Codex 约 43.62 题小时，3 槽约 14.54 小时，6 槽以上约 7.84 小时；Claude 约 40.74 题小时，3 槽约 13.59 小时，6 槽以上约 7.42 小时。这些是不同 harness 的历史参考，包含当时的失败及超时，不是最新版本的预测分布。上一轮 skills-agent 另有 32 题 HTTP 503 中断，其约 40.8 小时总时长会低估完整有效工作量。

现有三台各跑一题，按这些历史数据也约需 13.6–17.2 小时；不宜把它作为 10 小时方案。

## 并发实现与启动顺序

旧控制器不能仅将 `concurrency=1` 改成 26。它只有一个 `self.running`，每题结束会全局 `cleanup_owned()`，并临时修改共享 provider 配置；直接并发可能误杀其他任务。原生 ALE 的 semaphore 也在容器清理前释放，不能直接视为完整生命周期资源预留。

实施方式应为中央资源调度器加独立单题进程：每题各自使用 Runner、Provider、工作目录、日志、容器 owner 和 checkpoint；中央统一分配 CPU、内存、磁盘空间与端口租约，直到该题的评分、收集和容器清理确认结束，才释放资源。主批次仍是固定 95 题、variant 0，每题一次尝试，单独汇总原始与审计成绩。

1. 固定新 release、官方 Docker digest、数据版本和四个 token 参数；保持原单题 4/8 CPU、15/28 GiB、题目时限与 CompatSession。硬件和并发作为新协议变化记录。
2. 先完成扩容和 SSD 容量检查，预热官方镜像、Agent 资产和必要缓存。两道 8 小时题与两道 4 小时题优先进入首批，避免长尾最后启动。
3. 使用单题独立进程和资源账本，最高 26 题；容器启动和大输入复制适当错峰，避免同时打满磁盘。评分阶段也计入资源占用。
4. 为 CUA/VNC 等采用全局租约端口池，例如 20000–20999；它与主机当前 32768–60999 临时端口区间不重叠，清理后才释放租约。历史端口区间重叠是合理的冲突成因推断，尚未证明具体占用 socket；上线前还要检查现有监听和 rootless 端口残留。
5. 验证容器 CPU 亲和性及计算库线程数，使任务按自己的 4/8 CPU 配额工作，避免扩容到 128 核后每题误按宿主机核数启动上百线程。尽可能给任务分配互不重叠的 CPU 集，保留系统资源；相关设置写入协议。
6. 先做少量独立烟测，再从 2、8、26 槽逐级验证接入、资源账本、端口、日志和错误恢复。烟测与正式样本分开，正式任务不因低分择优重跑。
7. DeepSeek 服务连续异常时停止继续放行新题并保留诊断，不能将整批服务故障快速记零冒充完成。保持认证/余额错误停止、取消只影响本题及明确的服务故障分类。

当前 DeepSeek 官方文档列出的 flash 账户并发为 2500，26 个任务不接近其公布的连接上限；但同账户所有 key 共同计数，而且服务拥塞仍可能产生 503。正式前必须用实际请求确认账户可用性、延迟和吞吐，不能仅凭文档配额推断性能。

## 10 小时目标的边界

Agent 时限只覆盖 `run_deployer`，包括发送运行代码、Agent 依赖安装与执行。之前的 provision、input staging、task setup，以及之后的 gather、output pull、reference staging、评分与 cleanup，不都在这个时限内；评分另有最长 7200 秒，上游重试也可能延长其他阶段。因此即使 95 题全并行，也不能从现有时限推出“所有评分必然在 10 小时内结束”。

历史这些额外阶段通常只需秒到分钟，给 26 槽预留 2 小时有现实依据；但更大输出预算、首次高并发、API 故障和评分环境仍是未验证变量。此计划以完整运行和可审计结果为目标，不通过缩短题目时限或把服务错误算成有效零分来兑现时间指标。

已经确认的 PDE、ranking、LTMLE、DiT 评分环境或接口问题，应在准备阶段处理、固定版本，或在审计中明确标记；旧产物比较时也须使用相同评分修复。具体已知故障见 [已有补测与评分审计](ale-rerun-audit-2026-10-16.md)。更换硬件、版本、输出上限和并发后，不能把与旧批次的差异单独归因于某一个因素。

## 证据与待执行状态

- [完整资源、时限和历史调度审计](/Users/apple/dev/learn-ai-agent/tmp/ale-plan-20261016/concurrency-audit.json)
- [95 题排队方案与模拟结果](/Users/apple/dev/learn-ai-agent/tmp/ale-plan-20261016/schedule-plan.json)
- [最新 Agent 版本与配置核查](/Users/apple/dev/learn-ai-agent/tmp/ale-plan-20261016/latest-agent-audit.json)
- [ALE 生命周期时限核查](/Users/apple/dev/learn-ai-agent/tmp/ale-plan-20261016/lifecycle-timeout-audit.json)
- [DeepSeek Chat API 上限](https://api-docs.deepseek.com/api/create-chat-completion/)
- [DeepSeek 并发规则](https://api-docs.deepseek.com/quick_start/rate_limit/)
- [阿里云实例规格目录](https://www.alibabacloud.com/help/en/ecs/user-guide/general-purpose-instance-families)
- [阿里云变配说明](https://www.alibabacloud.com/help/en/ecs/user-guide/overview-of-instance-configuration-changes)

上述核查与容量推算为启动前快照：当时仅完成只读核查、容量计算和本地排程文件，尚未启动新批次或变更云资源。后续实际配置与启动情况见 [ALE04 启动记录](ale04-evaluation-launch-2026-10-16.md)。
