# ALE02 / ALE03：Codex CLI 与 Claude Code 的 DeepSeek 测评启动记录

启动日期：2026-10-14，以下时间使用北京时间。两台服务器于 **21:57:07** 分别启动了正式测评。此前均完成独立 ALE 烟测，得分 1.0；95 题配置 dry-run 也均通过。这份文件记录启动和复现信息，不是最终成绩。

| 项目 | ALE02 | ALE03 |
| --- | --- | --- |
| IP | `47.245.11.41` | `47.74.13.178` |
| 客户端 | 官方 Codex CLI | 官方 Claude Code |
| 固定版本 | `0.162.1` | `2.1.299` |
| 模型 | `deepseek-flash` | `deepseek-flash` |
| 原生接口 | `https://api.deepseek.com/responses` | `https://api.deepseek.com/anthropic/v1/messages` |
| 推理 effort | `low` | `low` |
| 配置上下文窗口 | 524000 | 524000 |
| 批次 ID | `codex_cli_formal_20261014T135421Z` | `claude_code_formal_20261014T135421Z` |
| Controller PID | 22329 | 17610 |
| Supervisor PID | 22316 | 17597 |
| tmux session | `ale-codex-cli-20261014T135421Z` | `ale-claude-code-20261014T135421Z` |

每台服务器内部串行执行，两台服务器可同时运行。任务清单、variant、资源配额与题目时限沿用现有 skills-agent 的 95 题正式协议：variant 0、concurrency 1、每题最多一次尝试、关闭自动恢复和整题自动重试。SEC 财报解析及计算材料科学保留 28g 内存覆盖，silicon 题保留原有 CompatSession。没有为 CLI 额外设置任务轮数或美元预算上限。

## 环境与版本固定

ALE 基线提交为 `d9abc0734b56ea34116c5bfcbdd0b808269ab9e2`，保留 ALE01 已有的框架兼容和取消清理补丁。两台主机均为 8 vCPU、约 32 GB 内存，`ale` 用户使用 rootless Docker。任务数据来自相同 HF revision `c5cfd7a8fa41d3c86afbeac23764f47932b2101e`，归档 SHA256 `0bfd0fe8d7d0bb5840097a8db233ec26f46707f4e1e90925692a12cfad7afb6f`，先前复制部署已核对归档及文件。

每题创建新的 ALE 官方基础容器，固定镜像：

```text
agentslastexam/ale-ubuntu22-docker@sha256:78ec11afeb0008ed8bc2b59cf9c90c05e63d1ac66b9d3e7cb0fada10695fca6f
```

两个客户端的原生二进制都固定到发布包，逐题上传、校验 SHA256 后安装到容器内 `user` 用户目录，复用 ALE 内置 deployer 和日志解析器。没有重新构建基础镜像，也没有引入 API 转换代理。正式配置和核心运行文件、release 资产、smoke 证据均记录哈希。

- Codex release：`/data/ale/my-agent/codex-cli/releases/20261014T134000Z-codex-0.162.1`；manifest SHA256 `f100c31a14719a228be343a8b2e2d0aefea57a9ef048e423bc4b840dfa1f43c1`。原生 binary SHA256 `628e35487d888a28f0427fc37501da529dd3d81fcbad7a2c3421c1e8670d92e1`。
- Claude release：`/data/ale/my-agent/claude-code/releases/20261014T134400Z-2.1.299`；manifest SHA256 `19624cc0d62ab120a3f4e8d12a84801b4901320724de99707c5d9429578ce6cd`。原生 binary SHA256 `2d894a4b1bfaaa9aab72aeff7803e3c73bc2510f279a171f6b8f59f72d17d077`。

专用 DeepSeek 凭据保存在各客户端 `secret/deepseek.env`，属于 `ale`，目录权限 700、文件权限 600。清单、报告和 Git 文件只引用凭据路径。Codex 的 ALE 配置段名为 `openrouter`，但实际请求直接发送给 DeepSeek；它并没有通过 OpenRouter。Claude 的主模型、模型档位和 subagent 环境配置都指向 `deepseek-flash`。

## 已完成的启动验证

两个正式配置均通过 95 题 dry-run，核查 variant 0、串行、每题一次和资源范围。首次状态核查（21:58）显示两边 Controller 存活、`state=running`，当前均为第一题 `business_finance/american_option_pricing_ls`。

**22:02 的再次核查：两边均已完成 1/95 题，第一题均 completed、评分 success、得分 1.0，正在执行第二题 `business_finance/basel_operational_risk_bia_cn`。** Codex 首题耗时 252.04 秒，Claude 首题 135.45 秒；单题数据不能推断整个批次的速度或能力。两边 PID/startticks 均与启动记录吻合。

独立烟测题为 `computing_math/os_log_permission_guard_v1`，烟测的成绩不并入正式批次：

| 烟测 | Codex | Claude Code |
| --- | ---: | ---: |
| 执行状态 | completed | completed |
| 评分状态 | success | success |
| 得分 | 1.0 | 1.0 |
| 最终验证耗时 | 64.03 秒 | 49.05 秒 |
| 实际客户端版本 | 0.162.1 | 2.1.299 |
| 观察到的模型 | deepseek-flash | deepseek-flash |

Codex 最终烟测实际执行了 shell、apply_patch 和 CUA 截图，9 个 Responses 请求均成功。Claude 烟测执行了 9 个工具调用，6 个 API 请求的模型和 effort 均符合配置，产物和原始 trace 正常收集。Claude 的独立请求捕获还确认了 `max_tokens=32768` 和 `thinking.type=adaptive`。

启动器保留全机独占锁、每批 launch-once、PID startticks、进度 checkpoint，以及仅清理本批 owned containers 的逻辑。新增的原生客户端错误审查只读取明确的 CLI 错误事件，不读取工具输出；11 个隔离 fixture 验证了认证/余额错误能识别，工具输出中的 HTTP 401 不会误触发。若出现明确认证或余额错误，批次停止继续发起新题。

独立最终审计也通过：两边 95 题有序清单、variant、task card 哈希、资源覆盖及 CompatSession 一致；配置与发布资产哈希没有 mismatch；smoke 与客户端、模型、最终 release 哈希正确绑定，当前各只有一个活动任务容器。

## 后续比较时必须保留的边界

任务与环境协议相同，客户端本身的系统指令、工具、上下文压缩和请求恢复逻辑属于被比较的 harness，不能声称这些内部机制也相同。

Codex 0.162.1 原生 CLI 没有暴露或发送 `max_output_tokens`，无法将其单次输出上限等同于 skills-agent 显式设置的 32768。它按原生策略在上下文窗口约 90% 时触发压缩。这些差异已记录在 release manifest，保持官方客户端原生行为。

Claude Code 2.1.299 的 stream JSON 会在同一 message 的多个内容块重复附带 usage，当前 ALE 内置 parser 逐事件求和会重复计算部分输入/cache token。后续 token 比较应读取客户端最终 result usage，或按 OTel 的真实 API 请求汇总。CLI 对未知模型显示的 `costUSD` 是其估价，不是 DeepSeek 实际账单。评分和原始工具 trace 正常；没有为了修统计而改动官方 parser。

已有测评中发现的 ranking 评分器准备失败吞成 0 分、PDE evaluator 异常等问题仍需在最终结果审计时单独标记，不能直接将所有零分都归为客户端能力失败。

## 进度、结果与恢复

以下路径在对应服务器上：

```text
ALE02 batch:
/data/ale/my-agent/codex-cli/batches/codex_cli_formal_20261014T135421Z

ALE03 batch:
/data/ale/my-agent/claude-code/batches/claude_code_formal_20261014T135421Z

正式原始 run:
/data/ale/runs/<batch_id>/<agent_id>/deepseek-flash/<task_id>/v0/<timestamp>
```

每批目录内的 `progress.json`、`results_summary.json`、`audited_summary.json` 和 `execution_status.json` 持续更新，`batch.log` 记录任务调度。每题 run 保留原始 CLI transcript、stderr、OTel 和 ALE trajectory，支持后续 trace 分析。

完整结束后，supervisor 写出 controller、reporter 和 audit 的 exitcode。tmux 与服务器上的 supervisor 使运行不依赖本地聊天持续在线。恢复前须核查终态与 `started/uncertain` 记录，不能直接删除 checkpoint 或再次启动同一批，避免重复收费及重复样本。

本地安全证据：

- [两批部署计划](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/deployment-plan.json)
- [后台调度回执](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/dispatch-receipts.json)
- [启动状态核查](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/launch-verification.json)
- [22:02 正式进度回执](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/launch-receipt.json)
- [Codex 烟测证据](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/codex/smoke-evidence.json)
- [Codex 正式首题实际运行证据](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/codex/formal-start-evidence.json)
- [Claude 正式首题实际运行证据](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/claude/formal-start-evidence.json)
- [Claude 日志兼容说明](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/claude/compatibility-notes.json)
- [最终独立协议审计](/Users/apple/dev/learn-ai-agent/tmp/ale-cross-agent-20261014/shared/final-protocol-audit.json)

配置依据：[OpenAI 官方自定义 provider 文档](https://learn.chatgpt.com/docs/config-file/config-advanced)、[DeepSeek Codex 集成](https://api-docs.deepseek.com/quick_start/agent_integrations/codex/)、[DeepSeek Claude Code 集成](https://api-docs.deepseek.com/quick_start/agent_integrations/claude_code/)。实际运行状态与版本以服务器记录和烟测 trace 为准。
