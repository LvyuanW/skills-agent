# ALE04 最新 skills-agent 测评启动记录

正式测评于 **2026-10-16 20:23:43 北京时间**启动。首次运行核查确认：95 题中 12 题正在执行、83 题排队、0 题结束，12 个独立 Docker 均已启动并进入 Agent 执行阶段。此记录确认启动，不代表最终评分已经完成。

## 固定配置

| 项目 | 本轮值 |
| --- | --- |
| 服务器 | ALE04，`8.209.250.103`，x86-64 Ubuntu 22.04 |
| 硬件 | 64 vCPU，实测 MemTotal 247.587 GiB，500 GB 独立数据盘挂载 `/data` |
| skills-agent | 本地提交 `6b1c55e2aaa07c246a949307c4f40844ef35b53d` |
| wheel SHA256 | `a41ca59573efa513c69b6f8680b8c844691eab490def8a7eda5382b7c4907d64` |
| release | `20261016T114346Z-6b1c55e2aaa0-a41ca59573ef` |
| 模型 | `deepseek-flash`，Chat API，`effort=low` |
| 样本 | 与此前相同的 95 题，variant 0，单题一次 Agent 尝试 |
| 并发 | 最多 12 题，每题一个独立容器及控制进程 |
| 题目资源 | 93 题 4 CPU / 15 GiB；SEC 4 CPU / 28 GiB；材料科学 8 CPU / 28 GiB |
| CPU 分配 | 每题独立 CPU 集合，容器与宿主评分 worker 使用相同集合；预留前 8 个 CPU 给宿主 |

```toml
context_tokens = 524000
model_context_window = 524000
max_output_tokens = 131072
summary_output_tokens = 8192
```

Agent 源码从本机 Git 归档构建，未包含未提交文档；通过 SSH 传包部署，没有为服务器配置 GitHub SSH。ALE 框架仍固定 `d9abc0734b56ea34116c5bfcbdd0b808269ab9e2`，保留既有 sandbox 取消兼容补丁。任务数据 revision 为 `c5cfd7a8fa41d3c86afbeac23764f47932b2101e`；复制后对完整 task-data 树做 `rsync -aHnc` 校验，无内容差异。

官方基础镜像已从 Docker registry 按 digest 拉取，并核对 RepoDigests、Linux/amd64：

```text
agentslastexam/ale-ubuntu22-docker@sha256:78ec11afeb0008ed8bc2b59cf9c90c05e63d1ac66b9d3e7cb0fada10695fca6f
```

Docker 为 29.9.0、`ale` 用户的 rootless daemon、cgroup v2，存储位于 `/data/docker`。本机使用 overlay2；ALE01 使用 containerd overlayfs 存储。镜像内容固定，宿主存储实现差异留档。

## 已通过的启动检查

- DeepSeek 1、2、4、12 路短请求均成功，接受 `max_tokens=131072`；不保存回复正文或推理内容。
- 1、2、4、12 个真实官方容器的启动、CUA 就绪、资源限制和清理全部通过。12 容器测试覆盖 52 CPU / 206 GiB 配额，读取内核 `cpu.max`、`memory.max` 验证实际限制。
- 修复新机默认缺少 CPU/cpuset 委派的配置，确认 Docker 的 CPU quota、cpuset、内存控制均可用。
- 并发控制器 25 项测试在 ALE04 Python 3.12 中通过；端口冲突重选、取消顺序和不确定结果处理有合成测试，未将它们冒充实机故障演练。
- 95 个独立 worker 的 ALE framework dry-run 全部通过，确认原任务名单、资源、配置和代码哈希。
- PDE、DiT、会话返回码及 sim 结果读取的合成检查在实际宿主评分 Python 中通过。
- 官方容器中 Ranking 冻结锁与 pytest 检查通过；Sim 使用题目原生 `uv run --project` 检查通过；R 4.3.2 和 LTMLE 公开 manifest 的 41 个包版本检查通过。
- 独立 Agent 烟测 `computing_math/os_log_permission_guard_v1` 用时 **34.94 秒**，`completed`、评分成功、得分 **1.0**，容器已清理，pip 缓存权限警告没有复现。烟测不计入正式 95 题。

Sim 原始 pyproject 声明 `package=false`，其原锁文件仍把根项目记为 editable。正式评分原本使用非 frozen 的 `uv run`，会将根项目元数据调整为 virtual。最终依赖探针遵循这一原生命令，并确认依赖版本和来源均未变。前几次预检的权限或命令问题及失败记录保留，未改动正式任务数据，也没有创建空包绕过构建。

## 避免重复的环境问题

上一轮的详细归因见 [ALE01 环境审计](ale01-environment-audit-2026-10-16.md)。本轮使用全局锁保护的 loopback 端口池 `20000–20999`，确认删除容器后才释放端口与资源。只有 Agent 尚未开始前的特定端口冲突允许最多三次 provisioning 尝试；不自动重跑整题。

PDE 结构化要求、DiT 公开接口、Ranking 缓存、Sim 缓存及结果读取修复均固定哈希并保留原件。LTMLE 的 Rscript wrapper 在评分阶段注入，选择官方镜像已有的 R 4.3.2。最终题目评分仍待实际执行验证。

主模型请求显式设置最多 6 次、120 秒重试预算；预算控制是否开始下次请求，不强行终止阻塞中的请求。遇到终止性的 429/5xx，调度器暂停新派发，冷却后只放行一个待运行任务；认证错误停止派发。整题时限保持不变。

容器删除前保存白名单 inspect 和 cgroup 内存事件，以便区分容器额度 OOM 与宿主资源故障。本机内核没有 `memory.peak`，保存可用的 `memory.current` 和 `memory.events`，不伪造内存峰值。

## 进度页与运行位置

极简进度页由 `ale-progress.service` 在端口 80 提供，每 10 秒刷新。随机访问路径见本地部署记录及本次回复；网页只提供白名单进度，不提供目录、配置、原始 trace 或密钥。

页面显示已结束任务进度、题目并发数、实际运行 Docker 数、每个容器的 CPU/内存分配上限，以及每题原有状态与分数。`failed`、`timeout`、`uncertain` 直接沿用运行状态；不把 0 分推断为异常。Docker inspect 暂不可用时显示“未知”。

- 正式批次：`skills_agent_formal_20261016T114449Z_6b1c55e_ale04`
- 控制目录：`/data/ale/my-agent/skills-agent/batches/skills_agent_formal_20261016T114449Z_6b1c55e_ale04`
- 结果目录：`/data/ale/runs/skills_agent_formal_20261016T114449Z_6b1c55e_ale04`
- 进程服务：`ale-benchmark.service`，初始 supervisor PID `39797`，不自动重启或重放任务。
- 审计证据：[启动回执](/Users/apple/dev/learn-ai-agent/tmp/ale04-20261016/launch-receipt.json)、[部署记录](/Users/apple/dev/learn-ai-agent/tmp/ale04-20261016/deployment.json)。

停止时只向 supervisor leader 发 SIGTERM，让它通知 worker 并完成所属容器清理；不要全机删除 Docker 容器或杀整个进程组。

本轮改变了 Agent 版本、输出上限、硬件、并发及部分评分修复。与旧批次比较时必须统一评分协议、保留覆盖率和外部失败分类，不能直接把差异全部归因为 Agent 能力。12 槽位以约 8–10 小时为历史参考目标，不能保证新配置或上游故障情况下的完成时间。
