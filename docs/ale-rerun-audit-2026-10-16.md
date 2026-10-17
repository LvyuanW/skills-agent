# ALE02 / ALE03：补测与重新评分审计

> 以下首先保留初次只读审计快照；用户随后授权“先重评分，再补跑”，实际修复、重评分和恢复批次记录见文末“恢复执行记录”。

核查日期：2026-10-16。范围为已结束的 Codex CLI、Claude Code 两个正式 95 题批次。此次只检查日志、公开任务与评分代码、已保存产物，没有启动补测、执行候选代码、重新评分或读取 reference 答案。

**已确认的最小处理范围：2 次 Agent 补跑、8 次原产物重新评分，共涉及 10 条“客户端 × 题目”运行记录、6 种题目。** 已保存的原始结果保持为审计依据；下面四类评分器修复应先完成，再用原文件评分，实际得分仍未知。

| 题目 | 受影响服务器 | 操作 | 原因 |
| --- | --- | --- | --- |
| `transport_safety/capacitated_vehicle_routing_problems` | ALE02 / Codex | 补跑 Agent 一次 | RootlessKit 端口 32966 冲突，环境未启动，Agent 没有执行机会 |
| `life_sciences/amber_three_stage_mmgbsa_workflow_instance_1` | ALE03 / Claude | 补跑 Agent 一次 | RootlessKit 端口 32902 冲突，环境未启动，无交付产物 |
| `education_info/homework_grading_numerical_pdes_instance_02` | 两台 | 修复评分器后，重评分原产物 | 五个必要文件齐全，但评分器出现 `dict.lower` 异常 |
| `computing_math/ranking_node_feature_parity_recovery_instance_1` | 两台 | 修复评分环境后，重评分原脚本 | verifier 在 `uv sync --frozen` 环境准备阶段失败，尚未执行候选；包装层把异常吞成 0 |
| `health_medicine/ltmle_targeted_bootstrap_simulation_study` | 两台 | 修复评分器 R 环境后，重评分原产物 | public check 通过，但隐藏评分器安装自身固定版本 `lmtp 1.4.2` 失败，发生在候选脚本执行前 |
| `computing_math/dit_pipeline_cfg_alignment_fid_256_001` | 两台 | 修复评分器模拟接口后，重评分原文件 | 公开 starter 使用 `DiTTransformer2DModel`，评分器却用不包含该接口的 stub 覆盖真实包；候选尚未进入行为测试就导入失败 |

## 评分器故障的判定依据

PDE 的 `scripts/score_outputs.py:28` 对 `phrase` 调用 `.lower()`。候选反馈在第 85 行已显式转为字符串，候选摘要在第 57 行由 `read_text()` 得到；调用处第 89、94 行的 phrase 来自评分参考要求。Codex 的反馈文件五个值也均为字符串。因此不能将此次 `dict.lower` 当作 Agent 交付了错误反馈类型。修复必须按参考要求的实际结构与语义处理，不能通过对任意字典调用 `str()` 来绕过异常。

Ranking 两边本轮的 batch.log 都明确出现 verifier 的环境准备异常。`verify_safe_recover.py` 中环境准备在复制、执行候选之前；`main.py:298–305` 对不可解析的 verifier 输出返回零。两个客户端的候选 `safe_recover.py` 已保存，修复后的真实分数尚未知。

LTMLE 的版本要求来自 `verify_hidden_smoke.py:109` 读取的评分器固定 contract，不是候选自报的依赖文件。第 339–344 行准备 R 库，候选命令在第 366 行以后才执行。Codex batch.log 第 2349 行和 Claude 第 2254 行都记录 `r_library_prepare_failed`，以及 `version '1.4.2' is invalid for package 'lmtp'`。两边 public check 均通过；因此应修正评分运行时和包准备，而不是重新生成候选。

DiT 的公开 `input/pipeline_dit.py:23` 原本就导入 `AutoencoderKL, DiTTransformer2DModel`，第 60 行使用 DiT 类型注解。任务要求文件能够在原 Diffusers 包目录之外导入，同时保持模型家族，没有禁止 Diffusers 依赖。候选采用绝对导入符合这一目的。但评分器只提供 `AutoencoderKL`，并覆盖 `diffusers.models`，随后在加载候选时失败，日志显示没有执行行为测试（`cases=[]`）。这是评分器接口缺口；修复不代表候选一定得满分。

## 待核项目与不应自动重跑的项目

**Claude 的 `health_medicine/simglucose_safe_basal_control_instance_1` 暂列待核。** Agent 确实达到 7200 秒时限，但已有 10 个提交文件。后续 evaluator 外层 stdout/stderr 为空、解析失败，被包装成 0。这里不能直接断定是评分基础设施问题：`main.py:89` 将 uv 和 evaluator 的所有输出重定向到内部日志，环境准备失败、候选导入失败或运行失败都可能造成外层为空。应先检查可保留的诊断信息，必要时对原产物做隔离评分验证，不直接给 Agent 第二次生成机会。

Codex 的同一 simglucose 题则没有收集到产物，日志显示正常达到任务时限，模型请求均成功；目前按有效超时保留。两者不能因题名相同而一并判为评分器故障。

Codex 的 WSI 题最终遭遇 HTTP 413（请求过大）。这不是已证实的暂时服务不可用；目前应视为该客户端处理输入/请求上限的失败，不能靠择机重跑来消除。若之后证明是独立的部署适配缺陷，应记录修复和版本变化后按新协议处理。

其余普通零分、质量不达标和真正的任务超时，没有仅因分数低而补跑的理由。Codex 日志中观察到的 46 次 503 没有造成最终服务中断，不能据此把相关任务全部作废。此审计也不等于已经独立证明其余所有评分规则都正确。

## 如何保持结果可比

- 原批次及分数保持不变；补跑和重新评分使用单独的恢复批次与记录，记录明确的故障原因和规则。
- 四类评分器修复应固定同一版本，对参与比较的相同题目统一使用；做三方比较时也包括 skills-agent 的原始产物。
- 不改动已保存产物来迎合评分器。补跑只针对 Agent 未能真正开始或已明确被外部服务故障中断的任务；正常失败不择优重试。
- 未评分和待核结果不应当作已确认的数值零分，也不应悄悄从分母删除。最终同时报告覆盖率，并在共同核验题目集合上做配对比较。

ALE01 的 skills-agent 另已确认有 **32 个 HTTP 503 中断任务、6 个容器端口冲突任务**需要补跑，四组既有记录互不重叠的说明见其审计文件。此次又核实 ALE01 的 LTMLE 同样有评分器固定 R 依赖准备失败（batch.log 第 2240 行）；三方对比时应一起修复并重评分。不能将 ALE01 的服务故障零分直接拿来与另外两批比较。

## 可复核证据

最终处理分类以合并决策文件为准；两个客户端的原始审计保留了独立检查时的初步分类，DiT 在后续交叉审查后确认需要修复评分器重评。

- [最终补测与重评分决策](/Users/apple/dev/learn-ai-agent/tmp/ale-rerun-audit-20261016/audit-decisions.json)
- [ALE02 全批审计](/Users/apple/dev/learn-ai-agent/tmp/ale-rerun-audit-20261016/ale02.json)
- [ALE03 全批审计](/Users/apple/dev/learn-ai-agent/tmp/ale-rerun-audit-20261016/ale03.json)
- [PDE 评分契约分析](/Users/apple/dev/learn-ai-agent/tmp/ale-rerun-audit-20261016/pde-scorer-contract.json)
- [DiT 独立契约分析](/Users/apple/dev/learn-ai-agent/tmp/ale-rerun-audit-20261016/dit-audit.json)
- [simglucose 独立复核](/Users/apple/dev/learn-ai-agent/tmp/ale-rerun-audit-20261016/sim-audit.json)
- [ALE01 既有服务与环境故障清单](/Users/apple/dev/learn-ai-agent/tmp/ale-rerun-audit-20261016/ale01.json)
- [ALE01 新增 LTMLE 核查](/Users/apple/dev/learn-ai-agent/tmp/ale-rerun-audit-20261016/ale01-addendum.json)

## 恢复执行记录

2026-10-16 19:36（北京时间），12 条三方共同题目记录及 Claude SIM 共 **13 条有效重评分**已完成。评分只使用保存的原产物，没有重新启动被测 Agent；复制前后及结束后的 SHA256 检查确认原产物未变。DiT 的早期修复试评保留为历史记录，最终三份均采用相同的完整修复版本，没有挑选最高分。

| 题目 | ALE01 skills-agent | ALE02 Codex CLI | ALE03 Claude Code |
| --- | ---: | ---: | ---: |
| DiT | 0 → **0.2** | 0 → **0.6** | 0 → **0.6** |
| Ranking | 0 → **1** | 0 → **1** | 0 → **1** |
| PDE | 未评分 → **0.968485** | 未评分 → **0.99** | 未评分 → **1** |
| LTMLE | 0 → **1** | 0 → **1** | 0 → **1** |

Claude 的 SIM 从被包装层吞成的 0 修正为 **0.5770250249508762**。仿真命令实际执行约 965 秒，返回码 0，但长命令的 stdout/stderr 均为空，评分包装层再次解析失败。独立监视器已保存完整 evaluator JSON；核对容器与原产物来源、JSON 完整性及文件 SHA 后，直接调用原 `score_hidden_summary` 得到上述分数。没有重跑仿真或 Agent，原 Agent 达到 7200 秒时限的历史记录保留。

修复包括：PDE 按结构化要求实现 OR 分支、分支内 AND 匹配；DiT 补齐公开合法模型导出别名；Ranking/SIM 使用可写任务缓存；LTMLE 使用官方镜像内 R 4.3.2 及 41 个完全匹配的固定包；命令适配器保留真实退出码。SIM 后续评分直接读取结果文件，避免依赖长命令 stdout。原公式、权重、阈值与候选文件均保持不变。

任务继续使用官方镜像 `agentslastexam/ale-ubuntu22-docker@sha256:78ec11afeb0008ed8bc2b59cf9c90c05e63d1ac66b9d3e7cb0fada10695fca6f`。按原 ALE 架构，PDE/DiT 的部分评分代码在宿主机执行；三台对应依赖已核对一致：Python 3.12.15、PyTorch 2.14.1、NumPy 2.5.3、cua-bench 0.2.7。任务本地命令和仿真在上述官方容器中执行。原 ALE 源目录及历史批次均未覆盖，修复位于独立的 recovery source 中。

原审计列出 40 个故障候选。用户随后明确 **“ALE01 不需要补跑”**，因此实际恢复范围调整为 **ALE02 CVRP 1 题、ALE03 AMBER 三阶段工作流 1 题**；ALE01 仅保留已经完成的重评分，未新增解题尝试，确认无运行中的恢复容器或进程。沿用原 release、客户端版本、模型配置、任务时限和资源，串行、每题一次 Agent 尝试。仅对 Agent 启动前明确的 RootlessKit bind 冲突最多尝试三次容器 provision；已恢复的 API 重试不作废成功任务，终止性服务故障会使恢复批次停止。原服务/环境故障记录由指定的新尝试替代，不取两次最高分。

恢复批次已分配，实际启动状态以后续 `launch.json`、`progress.json` 和 `supervisor-result.json` 为准：

- 首次启动的三个 `*_recovery_20261016T112317Z` 批次在 namespace package 路径校验处停止，尚无 Docker/Agent 执行，作为启动失败记录保留。
- ALE01：用户取消补跑，不重新启动。
- ALE02：`codex_cli_recovery_20261016T112317Z_s1`，1 题。
- ALE03：`claude_code_recovery_20261016T112317Z_s1`，1 题。

19:46 两个恢复控制器进入运行，19:47 已核实对应官方任务容器、`agent_run_started` 事件，以及容器内的 Codex/Claude 原生客户端进程均正常存在。ALE01 没有恢复进程；恢复工具同时禁止其再次启动。

ALE02/ALE03 补跑结束后会自动生成各服务器的 `composed-results.json`，按原 95 题、重评分及指定补跑记录汇总；未完成或无效的替代记录明确列为 unresolved，不回退挑选原高分。原运行状态是历史单元状态，可能包含评分异常，不能直接解释为 Agent 执行状态。ALE01 的 38 个原服务/环境故障不在此次补跑范围，三方比较应标注这一覆盖差异，不能直接把不同有效题目集合的均分当作最终排名。

- [最终有效重评分结果](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/regrade-results-20261016.json)
- [重评分完成门禁与证据哈希](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/regrading-complete-gate.json)
- [精确补跑计划与原产物哈希](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/rerun-plans.json)
- [恢复批次标识](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/launch-targets.json)
- [启动核验记录](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/recovery-launch-receipts.json)
