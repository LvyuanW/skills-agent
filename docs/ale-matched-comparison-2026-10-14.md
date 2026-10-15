# ALE 原始 95 题的同题比较核查

核查日期：2026-10-14。对象仅为原始批次 `skills_agent_formal_20261010T231233Z`，模型 `deepseek-flash`，Agent `skills-agent`，effort `low`。不混入后续重跑结果，不将未跑、未评分或已证实的评分器故障补成零分。

## 结论

**尚未取得 ALE 官方排行榜的完整逐题成绩，因此不能确定官方榜单上同题条件下最接近的模型与 Agent。** 之前按 105 题补零后的数值邻近项，不是本次用户要求的同题比较结论。

另有独立研究提供逐题成绩。在排除本地已知评分异常后的固定 90 题上，在其六个数据完整且均标记 `verified` 的配置中，最接近的是 **Claude Opus 5 + Claude Code（研究配置 High）**：54.5353%，我方 54.1889%，差 +0.3464 个百分点。**这不是 ALE 官方排名，也不能认定是该研究全部 22 个配置中最近的项。** 其他配置存在未知评分，部分仍可能更近。

## 固定比较题集

| 题集 | 我方均分 | 用途 |
|---|---:|---|
| 原始全部 95 题 | 未知 | 4 题没有数值评分，另 1 题零分已证实来自评分器环境故障；不提供伪造的 95 题均分 |
| 排除上述 5 题后的固定 90 题 | 54.18888165% | 主比较；双方必须使用这同一份 task ID 清单 |
| 原始有数值记录的固定 91 题 | 53.59339943% | 敏感性分析；包含已证实有问题的 ranking 原始零分，不能作为首选结论 |

四个未评分 task ID：

- `business_finance/financial_stmt_reconstruction_aapl_fy2024`
- `physical_sciences/computational_materials_science`
- `education_info/homework_grading_numerical_pdes_instance_02`
- `engineering/mpc_control_building_v1`

另一个已证实的评分器伪零为 `computing_math/ranking_node_feature_parity_recovery_instance_1`：评分环境在评价候选答案前执行 `uv sync --frozen` 失败，包装器把非 JSON 结果转成 `[0.0]`。排除它时，必须从双方的题集中同时排除。

题目清单与原始分数：[local-cohort-manifest.json](../../tmp/ale-matched-95-20261014/local-cohort-manifest.json)。比较规则：[comparison-protocol.json](../../tmp/ale-matched-95-20261014/comparison-protocol.json)。

## 官方公开资料的覆盖情况

本次经用户授权完成 Cloudflare 验证后，实际打开了 [官方 Agent Trace Viewer](https://agents-last-exam.org/traces)。

| 来源 | 实际内容 | 为什么不足以重算官方同 95 题结果 |
|---|---|---|
| [排行榜 API](https://agents-last-exam.org/api/demo/leaderboard) | 按模型、Agent、档位和 split 聚合的成绩 | 没有逐题成绩；不能从均分反推出被排除题目的得分 |
| Traces / All Trace Walkthroughs | 152 题的每题最佳轨迹，题与题之间模型不同 | 属于挑选出的最佳结果，不能拼成某个固定模型的成绩 |
| Traces / Apples-to-Apples Comparison | 13 个独立题目的精选对照；GPT-5.5 + Codex xhigh 与 Opus 4.8 + Claude Code max | 覆盖不足；分组显示 8 + 5 + 1，但 ABB 题跨组重复，因此不是 14 道独立题 |
| [官方 GitHub](https://github.com/rdi-berkeley/agents-last-exam) 与 [官方 HF 数据集](https://huggingface.co/datasets/agents-last-exam/agents-last-exam) | 框架、题目、输入资产、参考资料及镜像等 | 本次检查未找到完整官方逐题结果表 |
| [论文附录 D.6](https://arxiv.org/html/2606.05405v2#A4.SS6) | 逐题热图 | 源码附带的是栅格化热图 PDF，未找到对应数值表；不能精确还原当前排行榜配置 |

页面脚本仅暴露 `index.json`、`catalog.json`、精选任务 bundle 和 trace 读取逻辑。对照视图会过滤到上述两个模型；本次没有取得其原始 bundle，不能断言 bundle 内绝无其他运行记录，但已展示的题目范围不足以支持本次完整比较。普通无会话 HTTP 请求返回 401；浏览器工具不支持导出已加载的 JSON 资源。没有绕过访问控制，也没有从最佳轨迹或热图猜测逐题数值。

已保存[验证成功截图](../../tmp/ale-matched-95-20261014/trace-access-success.jpg)、[精选题目分组](../../tmp/ale-matched-95-20261014/official-pairwise-task-lists.json)、[对照页 DOM](../../tmp/ale-matched-95-20261014/official-pairwise-dom.txt)和[官方来源证据](../../tmp/ale-matched-95-20261014/official-public-source-evidence.json)。

## 非官方的同题数值参考

来源：[Finding the Right Fit 独立研究数据集](https://huggingface.co/datasets/yixuanli97/finding-the-right-fit)。它包含 22 个配置、每配置 99 道 ALE-CLI 题，均覆盖我们的原始 95 个 task ID。每题只保留一个最终评分记录，没有可据此平均的独立重复运行。

数据中共有 83 条 `unresolved_zero`，本比较将其视为未知，不当作真实零分。在固定 90 题内，只有以下六个配置的全部记录均为发布者标记的 `verified`；它们才进入完整题集均分比较。`verified` 是该研究的标签，不代表本次独立审核过每个评分器。

| 模型 + Agent | 同 90 题均分 | 相对我方差值（百分点） |
|---|---:|---:|
| 我方：deepseek-flash + skills-agent，Low | **54.1889%** | — |
| Claude Opus 5 + Claude Code | **54.5353%** | **+0.3464** |
| Claude Opus 5 + OpenHands | 52.4454% | −1.7435 |
| Kimi K3 + openJiuwen | 56.3500% | +2.1611 |
| DeepSeek V4 Pro + openJiuwen | 51.4736% | −2.7152 |
| Claude Opus 5 + PI | 50.0176% | −4.1713 |
| GPT-6 Astra + PI | 59.2737% | +5.0848 |

第三方逐题表没有 effort 列；其 [experiment.yaml](https://github.com/liyix/finding-the-right-fit/blob/main/experiment.yaml) 请求的档位为 `high`。不能据此断言各 Agent 的有效推理预算完全一致。

敏感性分析：若保留 ranking 的原始伪零，改用固定 91 题，我方为 53.5934%；六个完整配置中最接近的项变为 Claude Opus 5 + OpenHands，52.9679%，差 −0.6255 个百分点。这说明最近项对评分故障处理敏感。

完整数值、缺失题清单与覆盖统计见 [independent-comparison-audit.json](../../tmp/ale-matched-95-20261014/independent-comparison-audit.json)，可用[本地复算脚本](../../tmp/ale-matched-95-20261014/recompute-independent-comparison.py)重现。四个不完整的 DSH 配置在 90 题上的未知得分范围仍允许其比表中第一项更近，所以不发布“全部 22 配置中最近”的结论。

## 环境与解释限制

我方 ALE 基线为 `d9abc0734b56ea34116c5bfcbdd0b808269ab9e2`，另有本地兼容修复；第三方固定版本为 `0b6465b13c85b5a0a017d4c88bcf979a519a5e1f`。双方使用相同的官方基础 Docker digest：`sha256:78ec11afeb0008ed8bc2b59cf9c90c05e63d1ac66b9d3e7cb0fada10695fca6f`，但代码版本不同，第三方表也未提供 variant 字段。因此这里只确认相同 task ID，不称为完全相同题目版本、实例、预算或运行条件，更不把均分接近称为能力等价。

要完成用户要求的官方比较，仍需要官方导出含 task ID、variant、模型、Agent、effort、run ID、有效数值成绩和状态的逐题数据。获得数据后，按相同固定题集、先逐题处理重复运行再等题权平均；覆盖不足的配置另列，不补零。
