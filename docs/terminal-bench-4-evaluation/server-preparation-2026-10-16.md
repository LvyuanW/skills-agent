# 三台 Terminal-Bench 服务器的准备记录

日期：2026-10-16（Asia/Shanghai）。实验：`tb4-56-flash-max-v4`。**三台于北京时间 03:48:14 启动正式评测，11:48:41 按用户要求停止；11:49:08 已确认控制器、worker 和任务容器全部停止。** 本文保留启动前准备快照，并另列正式启动记录。

下文是 v4 历史记录。停止后的依赖、重试、分类与并发修复使用独立 v5 发布，见 [修复说明](v5/remediation-report.md) 和 [最新验收状态](v5/readiness.json)。v4 的独占规则和旧发布包不代表当前 v5 协议。

## 网关、模型和凭据

用户确认 `https://rehdasu.cn/` 为密钥签发网关，并在 `deepseek-flash` 返回 503 后指定模型 ID `deepseek-v4-1-flash-260910`。该 ID 在 Chat、Responses、Anthropic 三种接口均真实返回 200，响应模型名相同且包含思考内容。主 Agent、子 Agent 的请求档位统一为 **max**。网关返回名称并不能独立证明上游权重版本，保留实际请求和响应元数据供复核。

| 服务器 | Agent / 固定版本 | API Base | 运行协议 |
| --- | --- | --- | --- |
| `tbench-1` / `47.91.21.152` / serverA | Codex CLI `0.149.1` | `https://rehdasu.cn/v1` | Responses |
| `tbench-2` / `8.216.13.123` / serverB | Claude Code `2.1.300` | `https://rehdasu.cn` | Anthropic Messages |
| `tbench-3` / `8.216.11.161` / serverC | skills-agent `0.1.0` 固定 wheel | `https://rehdasu.cn/v1` | Chat Completions |

三把 key 已仅向各自服务器下发，位于 `/data/tb4/secrets/deepseek.env`，由 tbench 持有，文件权限 0600、目录 0700。仓库、命令参数和报告不保存密钥值；三台均通过 `/v1/models` 鉴权。已检查本机仓库及服务器控制代码、计划、日志、运行结果和产物，未发现 key 泄漏。原始本机笔记保持不变。

公开源码使用 HTTPS；本机构建发布包后上传。没有向服务器复制个人 GitHub 私钥、配置 GitHub 登录或启用 SSH agent forwarding。用户提供域名只用于 API 访问，没有修改 DNS 或部署站点。

网关费率和并发额度尚无明确资料，不能套用 DeepSeek 官方账号的额度或价格。保留 token 用量；未知实际费用记为 null。Claude 客户端自带的美元估算保存在轨迹 `extra.native_estimated_cost_usd`，不作为网关实际收费。

## 主机、磁盘和资源隔离

三台均为 Ubuntu 22.04.5 LTS x86_64、同款 Intel Xeon、32 vCPU，系统可见内存约 123.66 GiB，数据盘 500 GiB。

三台 `/dev/vdb` 均为 536,870,912,000 字节。初始化前核实无分区、无文件系统签名，首尾各 1 MiB 均为零；随后创建 ext4、挂载为 `/data`，并以 UUID 写入 `/etc/fstab`。

资源边界为 `tb4.slice`：`AllowedCPUs=2-31`、`CPUQuota=3000%`、`MemoryMax=115G`、`MemorySwapMax=0`。CPU 0–1 和至少 8 GiB 实际内存留给系统及其它服务。Docker、containerd、任务容器、控制器及临时审计代理都在该边界内。

任务准入预算为 **28 vCPU / 107 GiB / 最多 14 个 trial**，其余 2 vCPU / 8 GiB 用作评测内部控制和构建余量。47 道单容器题按 Agent/verifier 较大资源加权；9 道多容器题每次预留完整准入池、独占运行。启动样本不等于负载峰值，因此采用独占策略，不使用空载数据为高并发背书。

控制器具备主机级互斥锁、SQLite WAL 持久化、504 槽位过滤、唯一尝试名、配置哈希冻结与恢复检查。冻结记录同时包含预热清单摘要，显式重预热改变清单后不能继续原批次。运行中断的槽位必须人工核对，不会盲目再领；仅显式分类的基础设施故障最多增加两次尝试，零分或 Agent 失败不自动重采样。正式入口要求匹配批准文件、112 阶段预热成功与镜像保留检查、审阅后的侧车预算和配置冻结；准备阶段不生成批准文件，正式启动阶段依据用户最新授权单独生成。

调度器的边界、互斥、重启冻结、错误分类、镜像现状复核和完整 168 槽位假 worker 模拟已通过（本机及三台均 16 项）。详细实现及命令见 [控制器说明](../../adapters/harbor/tb4_control/README.md)。

## 固定的软件与发布资产

| 组件 | 版本或 commit |
| --- | --- |
| Docker Engine / CLI | `29.9.1` |
| Docker Compose plugin | `5.6.0` |
| containerd / Buildx | `2.4.2` / `0.38.0` |
| uv / Python | `0.13.1` / `3.12.15` |
| Harbor | `0.24.0`；`aad19e235922ee5007ac635a96a1c2f72eef9dfc` |
| Terminal-Bench | `v4.0.0`；`452bf305c6daa62fc59061d22133a7cbc7c1572e` |
| skills-agent 发布包 | `skills_cli_agent-0.1.0-py3-none-any.whl` |
| wheel SHA256 | `31829dde914bd50997ca5d7d72304ce176ca70dfaba5f60c0a152e28bd4820ae` |
| skills-agent 依赖锁 SHA256 | `206ab4c6c79f186726eed05298e8f6fe4a4317ed636f7c4163f4dc568875cad9` |
| tokenizer 来源 commit | `deepseek-ai/deepseek-recipe@8cadfede7063c896b944e7bae05daa3549ae97ea` |
| tokenizer SHA256 | `81f64d1248a68ce3663e07ab3ee48b851e5df0e32d27cb98e4c9a268151e8d99` |

Docker 相关包已记录 Debian 完整版本并 hold。两个原生 CLI 包均按 registry 完整性校验：Codex musl 工具链、Claude glibc/musl 二进制离线下发，不要求每题重新联网安装 CLI。skills-agent 的核心 wheel 不改，网关参数、MCP 桥接和日志归集由 Harbor 适配层完成。本地后续开发改动不自动进入该固定 wheel。用户本轮要求更新 skills-agent，因此新包从 commit `41a6bf87f3eca29c73a876c43af8dfd2a2c50d63` 加尚未提交的 CLI/terminal 更新冻结；源码树摘要 `2591210afb868d20506a912d28e4034ed0991ec8a550aa5ae7632d989c417fd8`，新包 136,813 字节，依赖元数据及锁文件与旧包一致。53 个包文件逐字匹配源码快照、wheel 和 C 机实际安装文件，89 项相关离线测试通过。原包和其预检结果另存。新版已独立完成离线集成检查和真实合成预检。

## 接入与预检

三个适配器保留各 Agent 原生执行逻辑。Codex 为缺少系统 CA 的任务镜像离线上传固定公共证书，仅作用于原生客户端的 `CODEX_CA_CERTIFICATE`；skills-agent 的 managed venv 默认 HTTPS context 使用已固定 certifi 补充根证书，不改任务 Python 和操作系统证书。三个无 CA 代表镜像通过原生 TLS 检查，默认校验保持开启。Codex 保留官方模型目录原始快照，派生实验目录只允许指定模型和 max；离线验证确认 Pro 或 low 子 Agent 被原生拒绝。Claude 的 Opus/Sonnet/Haiku/Fable/Small Fast/子 Agent 路由统一到指定模型；关闭 WebSearch、保留 WebFetch。skills-agent 使用原生 v41 tokenizer，适配层补齐网关 max 透传，并提供通用、会话持续的 MCP 工具桥。

真实预检使用自行创建的合成任务，**不属于 56 道正式题**，不读取正式解题答案。共享任务验证终端读写、artifact 收集和独立 verifier；另有图片和子 Agent 能力检查。metadata 审计只保留模型、effort、工具名、图片存在性、状态和用量，不保存授权头、提示词或回复正文。

- Codex：共享真实任务 reward=1；图片、子 Agent、apply_patch 的独立真实合成任务 reward=1，全部请求使用指定模型/max。
- Claude：共享真实任务 reward=1；指定 Haiku 的子 Agent 实际仍走指定 Flash/max，并真实读取 PNG、写颜色文件，独立 verifier reward=1。11 个真实请求中 2 个包含图片，全部 max + adaptive thinking。
- skills-agent 旧包（已归档，不能代表新包）：共享真实任务 reward=1；独立图片/子 Agent 预检真实发送 10 次指定模型/max 请求，3 次包含 image_url，传输错误为零。子 Agent 确实读图并写文件，但将纯蓝识别成 purple，独立 verifier reward=0；原图、snapshot 和离线重建的发送图片 SHA256 与 RGB 一致。这是保留的能力观察，不能宣称颜色识别成功。
- skills-agent 新包：共享真实任务 reward=1、无异常，元数据确认新 wheel `31829dde914b…`、新 bundle `4d179e78b724…` 和冻结源码摘要。图片/子 Agent 预检 9 次请求、2 次含图片、1 个普通子 Agent 完成；8 次开启思考的请求均 max，1 次原生工具报告辅助调用 disabled。原始蓝图被答为 green，独立 verifier reward=0；字节链路一致、无传输错误，结果保留。
- 图片协议控制：同一蓝图在直接 Chat/Responses/Anthropic、普通多轮及工具后普通图片消息中都被识别为 Blue；带工具观察包装的极小 Chat 控制答为 White。因此基本图片接口可用，但 Agent 观察上下文中的颜色识别失败原因尚未确定，不能直接定性为网关故障或 Agent 实现缺陷。没有改 Agent 提示、协议或结果，也没有为获得正确答案重跑原任务。
- 三台另各有一次确定性 shell transport 检查，均 reward=1、模型请求为零；这不是三个 Agent 的成绩。
- MCP：选中集唯一 MCP 题的真实 Playwright 侧车，三台均完成 SSE initialize、tools/list（22 个工具）及只读 browser_tabs；没有调用模型或运行正式任务解答。skills-agent 通用桥的会话持续与清理、原生图片反馈路径也通过离线测试。 [无模型基础设施验证汇总](../../adapters/harbor/tb4_control/infrastructure-preflight-evidence.json)。

此前官方域名 401、网关旧 alias 503 的记录保留为连接失败；其 verifier 零分不属于能力分数。准备阶段在新 ID 通过后只做必要预检，没有启动正式 trial。

## 环境预热状态

<!-- prewarm-status -->
三台各 **112/112 个环境阶段通过，失败数为 0，112 个阶段的保留镜像均已逐一核对当前 image ID**。每道题的 Agent 与独立 verifier 环境均已构建、启动、探针检查并清理，合计 336 个阶段。三台各 3 个 FreeCAD verifier（共 9 个）均已通过披露的兼容依赖、几何、NumPy↔VTK 和真实离屏渲染探针，原始失败记录另存。

| 服务器 | 成功 / 失败 | 保留镜像阶段 | 数据盘已用 GiB | 数据盘空闲 GiB |
| --- | ---: | ---: | ---: | ---: |
| serverA / `47.91.21.152` | 112 / 0 | 112 | 261.25 | 229.81 |
| serverB / `8.216.13.123` | 112 / 0 | 112 | 261.85 | 229.22 |
| serverC / `8.216.11.161` | 112 / 0 | 112 | 261.47 | 229.60 |

启动前的最终准备核验确认：预热和 finalize 服务均已退出；任务临时容器已清理；cgroup OOM 与 oom_kill 均为 0；CPU/内存边界、正式题源码 commit 和工作树、manifest、504 个待执行槽位均匹配。当时没有正式批准文件、正式运行目录或正式状态数据库，正式运行数为 0；这是启动前快照。

[最终机器状态与验收证据](preparation-status.json)。预热未调用模型，也未运行正式评分；合成预检的成绩另列，不能视为 56 题评测结果。
<!-- /prewarm-status -->

预热只调用 Harbor 的环境创建、启动、健康探针、停止接口，不创建 Agent、不发模型请求、不运行官方评分测试、不复制 solution。

三道 FreeCAD verifier 的构建修复采用外置 Compose `dockerfile_inline`：只在官方那一条 validator 安装命令中追加 `pyvista==0.48.4`、`vtk==9.2.6`，其余 Dockerfile、题目、评分器代码及资源保持不变，原始任务 Git 工作树仍干净。依赖配置为 `freecad-conda-vtk92-compat-v1`，实现、配置、探针和有效 Dockerfile 均记录哈希，正式 verifier 会继承同一适配器并在准入前核对。无模型探针检查固定版本、NumPy↔VTK、FreeCAD 合成 box 和 128px Xvfb 离屏截图；这不是运行正式评分测试。原始失败日志存入各阶段 `attempt-history`，不会覆盖丢弃。
并发最多 2 题，同题两阶段顺序执行，构建及侧车余量纳入准入池。保留 `tb4-prewarm/*` 镜像别名，避免 Harbor 清理 project image 时删除预热标签；阶段完成后删除容器与 volume。

一次性 finalize 服务等待首轮结束，通过正常续跑补齐早期缺失的保留镜像别名，并检查 `retained_image_phases=112`。它仅允许显式审阅过的 FreeCAD 依赖故障进入修复续跑，遇到其它失败或不完整批次即停止。官方 `medical-claims-processing` Compose 使用 no_cache/pull_policy=build，后续正式 trial 仍会触发这部分构建。

## 路径与运行入口

| 内容 | 路径 |
| --- | --- |
| Docker / containerd 数据 | `/data/docker`、`/data/containerd` |
| 评测用户 home | `/data/home/tbench` |
| 固定源码 | `/data/tb4/src/{harbor,terminal-bench}` |
| Harbor 环境与入口 | `/data/tb4/venvs/harbor`、`/data/tb4/bin/harbor` |
| 资源边界内的 root 启动入口 | `/usr/local/sbin/tb4-exec`，降权为 tbench |
| 控制代码 / 发布资产 | `/data/tb4/control`、`/data/tb4/releases` |
| 方案与冻结清单 | `/data/tb4/plans/terminal-bench-4-evaluation` |
| 密钥文件 | `/data/tb4/secrets/deepseek.env`，仅各机自己的 key |
| 预热记录 | `/data/tb4/runs/prewarm/summary.json` 及各题两阶段报告 |
| 合成预检 | `/data/tb4/runs/preflight`、C 新包的 `/data/tb4/runs/preflight-skills-updated-31829dde914b`；旧包结果另存 |
| 安全元数据报告 | `/data/tb4/logs`；集成验证记录归档于 [evidence/initial-integration](evidence/initial-integration)，外部产物范围见 [来源说明](evidence-provenance.json) |
| 系统与依赖记录 | `/data/tb4/system-records`、`/data/tb4/artifacts/runtime` |

只读检查示例（不调用模型）：

```sh
ssh root@47.91.21.152 \
  '/usr/local/sbin/tb4-exec env PYTHONPATH=/data/tb4/control /data/tb4/venvs/harbor/bin/python -m adapters.harbor.tb4_control status'
```

## 正式启动与运行

用户明确授权在排除可预见阻断后启动。启动前再次核对三台当前镜像、源码、资源、锁、时间同步、固定发布资产和 tbench 路径权限；三个正式 API 协议各以本机自己的 key 真实请求一次，均返回 200、指定模型 ID 和 max。C 的全部 56 个 retained main 镜像均运行固定 Python 和实际依赖导入短检查，56/56 成功，无 key、无网络、无模型调用。

三台于 **2026-10-16 03:48:14（Asia/Shanghai）** 同时启动 `tb4-formal.service`，每台创建 168 个固定槽位。每个服务以 tbench 运行在 `tb4.slice`，显式加载各自 `deepseek.env`、双 PYTHONPATH 和直连网关的正式 Agent 配置。没有使用指向已关闭临时代理的 smoke 配置。systemd 后台服务与当前 SSH/对话断开无关，`Restart=no`；中断后须按已冻结状态核对恢复。

正式状态数据库为 `/data/tb4/runs/formal/state.sqlite3`，控制器日志为 `/data/tb4/logs/formal-controller.log`。初始每台均为 `running=1 / pending=167`，首题 `ctr-optimization` 属于独占多容器任务，因此占满 28 CPU / 107 GiB 准入池是预定调度行为。该计数是启动快照，后续实时进度以 SQLite 为准。

已分别只读确认三个原生 Agent 进入指定模型/max 的真实执行：Codex 出现 16 次 exec_command 和 1 次 apply_patch；Claude 出现 16 次 Bash 及对应工具回执；新版 skills-agent 出现 5 次请求、8 次 shell 工具调用。观测时无原生错误或传输错误，未为观察额外调用模型。首槽位仍运行中，这些事件计数不是完成成绩；Claude 的中间 usage 为占位零值，最终用量尚未生成，不能解释为零消耗或零费用。

[正式启动快照](formal-launch-status.json)。可只读检查每台的实时状态：

```sh
ssh root@47.91.21.152 \
  '/usr/local/sbin/tb4-exec env PYTHONPATH=/data/tb4/control:/data/tb4/control/adapters/harbor /data/tb4/venvs/harbor/bin/python -m adapters.harbor.tb4_control status --output /data/tb4/runs/formal'
```

## 保留的限制

正式评测曾启动，现已按用户要求停止。网关真实费率、额度及金额告警阈值仍未知，按用户授权执行固定批次；费用报告保留用量及 null 金额，不能用客户端估算替代账单。API 服务波动和额度变化仍可能影响运行。独占调度会延长批次：用旧公开轨迹回放得到约 30 / 53 / 70 小时三个历史负载情景，建议正式运行预留 3–5 天观察窗口；这不是 Flash/max 实测预测。

[完整方案](../terminal-bench-4-evaluation-plan.md)、[机器映射](servers.json)、[任务与槽位校验和](checksums.sha256)。基础脚本仅适用于已核实的全新环境，不能拿去初始化含现有数据的服务器。


## 用户停止及异常原因（11:49）

已停止三台新任务准入、控制器和全部在途任务容器，归档启动批准文件；保留现有日志、原始评分、数据库、停止后的容器及 volume。三个在途槽位单列为人工中断，原始数据库结算状态未改，不得解释为新自然失败或直接重采样。[停止验证](formal-stop-status.json)。

本轮共发现 5 次自然异常结束：

| Agent / 任务 / 重复 | 已确认原因 |
| --- | --- |
| Claude Code / ctr-optimization / 2 | Cloudflare／网关源站返回 HTTP 524，CLI 以 UnknownApiError 终止；controller 正确保留 needs_review，未自动补跑。源站更深层原因未知。 |
| skills-agent / ctr-optimization / 1 | 镜像缺少 ps/procps；supervisor 启动报找不到 ps，登记停在 starting 且 PID 为空，最终原生 ProcessScope.close 清理失败、退出 1。 |
| skills-agent / ctr-optimization / 2 | HTTP 200 的 SSE 流内部返回 upstream_stream_read_error / upstream_error；随后又触发相同的缺 ps 清理故障。 |
| skills-agent / ctr-optimization / 3 | 网关 HTTP 502；随后又触发相同的缺 ps 清理故障。 |
| skills-agent / cumulative-layout-shift / 1 | 网关 HTTP 502，原生请求被中断。 |

另确认三项接入和预检缺口：

1. skills-agent 的适配层没有补齐原生后台 helper 依赖的 ps/procps。之前 56 环境矩阵只检查 Python 与包导入，没有执行该后台 helper，因此未覆盖到此故障。
2. 固定版本的原生重试分类未覆盖 HTTP 502 和上述上游流错误，导致 retryable=false 后直接退出；这些调用不是重试次数耗尽。
3. 原生 provider 错误被包装成 NonZeroAgentExitCodeError 后，未进入 controller 的系统错误表；controller 随后看到 verifier reward=0 就将四个 C 槽位消费为 complete。complete 不代表运行健康，也不能把三次上游中断当作纯能力失败。

A 的长等待没有被证实为卡死：停止前 11:47:09 已恢复事件和用量更新，无原生错误。三台无 OOM；停止期间容器退出 137 且 OOMKilled=false，属于本次人工终止，不能倒算成自然 OOM。正常结束的 A/B 首次任务 reward=0 不属于运行异常。

网关 502、524、流中断的深层原因，现有日志不足以区分上游负载、推理耗时和网络故障。[skills-agent 详细证据](diagnostics-skills.json)、[Claude 详细证据](diagnostics-claude.json)。本轮没有修改分数、重跑任务或部署修复。
