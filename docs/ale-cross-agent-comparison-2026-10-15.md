# 三台 ALE 服务器：正常完成题的同题成绩比较

快照时间：2026-10-15 16:14:55–16:16:27（北京时间）。本报告使用本次三台服务器自己的结果，不使用独立研究数据，也不以旧批次补齐缺失分数。检查全程只读，未启动、停止或修改测试。

## 结论

在保守筛选后的**相同 26 道题**上，skills-agent 均分 **73.7927%**，Codex 为 **65.8021%**，Claude Code 为 **71.8362%**。skills-agent 分别领先 **7.9906** 和 **1.9565** 个百分点，通过数为 **12、10、10**。

这只描述当前三方正常结束且评分可解释的共同题目。根据用户要求排除了异常结束，不能用它衡量整批任务的失败率、运行稳定性或最终完整成绩。样本主要是商业金融与计算数学，尚未覆盖后续医学、生命科学等领域。

## 1. 使用的批次及快照


| 服务器 / Agent | 观测时间（北京时间） | 当前批次 |
| --- | --- | --- |
| ALE01 / skills-agent | 2026-10-15T16:14:55.326836+08:00 | `skills_agent_formal_20261013T090106Z_73666d7` |
| ALE02 / Codex | 2026-10-15T16:15:58.453450+08:00 | `codex_cli_formal_20261014T135421Z` |
| ALE03 / Claude Code | 2026-10-15T16:16:27.690551+08:00 | `claude_code_formal_20261014T135421Z` |

三方均使用 deepseek-flash，配置请求 Low。ALE01 使用 10 月 13 日启动的新版本批次，不使用 10 月 10 日的旧结果。三方 95 题清单一致，题目实例为 variant 0。

快照时，ALE01 全 95 题已结束（46 completed、7 timeout、42 failed）；ALE02 结束 35 题；ALE03 结束 38 题。三方都已结束的交集为 35 题。已结束不等于正常完成，也不等于通过。

## 2. 纳入标准与 DiT 的特殊处理

纳入条件同时满足：

1. 三方 `run_status=completed`，原生客户端正常退出，无终止性错误。
2. 三方 `eval_status=success`，分数为有限的 0–1 数值。
3. 没有已证实的评分器基础设施故障或伪零。

正常判出的零分保留。例如 Basel、FF5、并购一致性审计三方都是零，仍在主比较中。执行过程中的工具报错或重连，只要恢复并正常完成，不会自动排除。

按上述标准得到 27 题。其中 DiT 三方均有正式结构化零分，但候选导入均与评分器的隐藏依赖 stub 不兼容。该题 starter、参考实现及评分 stub 之间存在导入方式差异，任务说明没有明确列出这些限制。**它不是已证实的评分器崩溃，也不能直接宣布分数无效。** 为符合“只比较有意义结果”的要求，暂将它列为待核，主比较使用剩余相同 26 题；保留它的 27 题结果作为敏感性分析。

评分器异常的 ranking 题与上述情况不同：它在候选评分前的 `prepare_workspace → uv sync --frozen` 阶段失败，却被包装成 success/0，因此明确排除。

## 3. 相同 26 题总成绩


| 配置 | 均分 | 通过题数 | 通过率 | 零分题数 | 相对 skills-agent（百分点） |
| --- | --- | --- | --- | --- | --- |
| ALE01 / skills-agent | 73.7927% | 12/26 | 46.1538% | 3 | +0.0000 |
| ALE02 / Codex | 65.8021% | 10/26 | 38.4615% | 4 | -7.9906 |
| ALE03 / Claude Code | 71.8362% | 10/26 | 38.4615% | 3 | -1.9565 |

通过阈值为原始 score ≥ 0.999。按题等权，统计使用未舍入原始分数；胜平负的浮点容差为 1e-9。

| 比较（从 skills-agent 角度） | 胜 | 平 | 负 |
| --- | --- | --- | --- |
| 对 Codex | 6 | 16 | 4 |
| 对 Claude Code | 3 | 19 | 4 |

skills-agent 对 Claude Code 的均分优势来自较少几题上的较大领先，并非多数差异题都赢。尤其 Chisel 一题为 100% 对 20%；若仅作影响分析，把这一题从双方同时去掉，剩余 25 题 Claude Code 反而领先约 1.17 个百分点。因此当前 1.96 个百分点的总体领先不能视为稳定的广泛优势。

## 4. 按领域看当前差异


| 领域 | 题数 | skills-agent | Codex | Claude Code |
| --- | --- | --- | --- | --- |
| 商业金融 | 11 | 61.1539% | 53.1770% | 60.6179% |
| 计算数学 | 12 | 78.8265% | 68.8257% | 81.7454% |
| 教育信息 | 1 | 100.0000% | 100.0000% | 100.0000% |
| 工程 | 2 | 100.0000% | 100.0000% | 60.0000% |

- 商业金融：skills-agent 领先 Codex 约 7.98 个百分点，与 Claude Code 基本接近（领先约 0.54）。
- 计算数学：skills-agent 领先 Codex 约 10.00 个百分点，但低于 Claude Code 约 2.92 个百分点。
- 工程目前只有两题，差异完全来自 Chisel；教育只有一道，三方相同。不能把它们概括成普遍领域优劣。

## 5. 全部 26 道纳入题目

数值均为百分制。最后两列为对应 Agent 减 skills-agent，单位为百分点。


| 序号 | 题目 | task ID | skills-agent | Codex | Claude Code | Codex差 | Claude差 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 美式期权定价 | `business_finance/american_option_pricing_ls` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 2 | Basel 操作风险 BIA | `business_finance/basel_operational_risk_bia_cn` | 0.00 | 0.00 | 0.00 | +0.00 | +0.00 |
| 3 | 数字营销 A/B 分析 | `business_finance/digital_marketing_ab_test_analysis_1` | 100.00 | 65.00 | 65.00 | -35.00 | -35.00 |
| 4 | 数字营销受众分群 | `business_finance/digital_marketing_audience_segmentation_1` | 94.17 | 86.67 | 94.17 | -7.50 | +0.00 |
| 5 | FF5 公开数据重构 | `business_finance/ff5_public_reconstruction` | 0.00 | 0.00 | 0.00 | +0.00 | +0.00 |
| 6 | 苹果 FY2024 财务报表重构 | `business_finance/financial_stmt_reconstruction_aapl_fy2024` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 7 | 内部员工 Agent 任务 | `business_finance/internal_employee_agent_instance_1` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 8 | 并购文件一致性审计 | `business_finance/legal_ma_consistency_audit_01` | 0.00 | 0.00 | 0.00 | +0.00 | +0.00 |
| 9 | LLM 生态隐私审计 | `business_finance/llm_ecosystem_privacy_audit_realdata_1` | 50.00 | 50.00 | 50.00 | +0.00 | +0.00 |
| 10 | SEC 10-K 财务解析 | `business_finance/sec_10k_financial_parsing` | 95.19 | 16.61 | 90.96 | -78.58 | -4.23 |
| 11 | 北向程序化交易合规 | `business_finance/sse_northbound_programmatic_trading_01` | 33.33 | 66.67 | 66.67 | +33.33 | +33.33 |
| 12 | 分支定界 ATSP | `computing_math/branch_bound_atsp` | 100.00 | 33.75 | 100.00 | -66.25 | +0.00 |
| 13 | CFR 博弈均衡 | `computing_math/cfr_game_theory_equilibrium` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 14 | 成本优化 | `computing_math/cost_optimization_1` | 86.61 | 86.61 | 86.61 | +0.00 | +0.00 |
| 15 | 竞赛程序测试生成 | `computing_math/cp_test_gen_1` | 50.00 | 50.00 | 60.00 | +0.00 | +10.00 |
| 16 | ETL 数据流水线 | `computing_math/data_pipeline_etl_instance_1` | 83.33 | 0.00 | 83.33 | -83.33 | +0.00 |
| 17 | Ising 测量后分析 | `computing_math/ising_post_measurement_1` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 18 | K8s 支付 API 根因分析 | `computing_math/k8s_payment_api_root_cause_analysis` | 72.27 | 67.27 | 94.77 | -5.00 | +22.50 |
| 19 | 模型检查点合并 | `computing_math/mp_checkpoint_consolidation_v2` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 20 | 系统日志权限防护 | `computing_math/os_log_permission_guard_v1` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 21 | 论文复现 | `computing_math/paper_reproduction_instance_1` | 80.00 | 94.50 | 80.00 | +14.50 | +0.00 |
| 22 | 冷启动推荐 | `computing_math/recsys_cold_start_instance_1` | 50.00 | 66.67 | 50.00 | +16.67 | +0.00 |
| 23 | 合成因果结构推断 | `computing_math/synthetic_causal_structure_inference` | 23.70 | 27.11 | 26.23 | +3.41 | +2.53 |
| 24 | MARC 书目修复与 FOLIO 覆盖更新 | `education_info/marc_remediation_folio_overlay` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 25 | ABB 机器人 URDF 重构 | `engineering/abb_irb6700_asset_to_urdf_instance_1` | 100.00 | 100.00 | 100.00 | +0.00 | +0.00 |
| 26 | Chisel / Verilog 序列对齐 | `engineering/chisel_verilog_alignment_seq_1` | 100.00 | 100.00 | 20.00 | +0.00 | -80.00 |

当前分差最值得关注的题：

- **skills-agent 相对 Codex 的主要优势**：ETL（83.33% 对 0%）、SEC 10-K（95.19% 对 16.61%）、ATSP（100% 对 33.75%）、A/B 分析（100% 对 65%）。这四题解释了主要正向差距。
- **skills-agent 相对 Claude Code 的主要优势**：Chisel（100% 对 20%）、A/B 分析（100% 对 65%）；SEC 10-K 也略高（95.19% 对 90.96%）。
- **两种外部 Agent 都更好的题**：北向程序化交易合规（双方 66.67%，我方 33.33%）、合成因果结构推断（Codex 27.11%、Claude 26.23%，我方 23.70%）。
- **Codex 单独更好的题**：冷启动推荐（66.67% 对我方 50%）、论文复现（94.50% 对我方 80%）。
- **Claude Code 单独更好的题**：K8s 根因分析（94.77% 对我方 72.27%）、竞赛测试生成（60% 对我方 50%）。

这些是分数表现，未据此推断每一道题的根因。

## 6. 三方已结束交集中的排除题

35 道三方已结束题中，以下 8 题存在明确异常，另 1 题 DiT 暂列待核，共 9 题未进入 26 题主比较。任一方异常就从三方共同集合同时剔除，不用其他批次或重跑结果填补。


| task ID | 排除或待核原因 |
| --- | --- |
| `computing_math/clustered_cyclic_code_circuit_level_simulation` | ALE01 超时 |
| `computing_math/dit_pipeline_cfg_alignment_fid_256_001` | 三方正式评分均为 0；候选导入与评分 stub 兼容性有解释争议，保守暂排（非已证实无效） |
| `computing_math/go_game_reconstruction_1` | ALE01 Agent 进程异常退出；ALE02 的重连已恢复并正常结束，但共同比较仍排除 |
| `computing_math/k3_abelian_extensions` | ALE01 超时 |
| `computing_math/particle_filter_nonlinear_tracking` | ALE03 超时 |
| `computing_math/ranking_node_feature_parity_recovery_instance_1` | ALE01 运行失败；ALE02/03 评分环境准备失败，被包装为零 |
| `education_info/homework_grading_numerical_pdes_instance_02` | 评分器 dict.lower 异常，缺少有效分数 |
| `education_info/moodle_gradebook_closeout_reconciliation` | ALE01 Agent 进程异常退出，即使有 0.885 分仍排除 |
| `engineering/aerospace_low_thrust_trajectory` | ALE01 超时 |

尚未被 ALE02 或 ALE03 跑完的其他题，不属于当前三方共同集合，既不记零，也不进入均分。全 95 题每台服务器的纳入/排除原因保存在比较 JSON。

## 7. 敏感性与成对补充

若保留有正式零分但解释存疑的 DiT，使用相同 27 题：


| 配置 | 同 27 题均分 | 通过题数 |
| --- | --- | --- |
| ALE01 / skills-agent | 71.0597% | 12/27 |
| ALE02 / Codex | 63.3650% | 10/27 |
| ALE03 / Claude Code | 69.1756% | 10/27 |

三方排序不变。DiT 三方均为零，只改变分母，不改变累计分差。

如果分别最大化每对服务器的正常共同题集，并同样暂排 DiT：


| 成对比较 | 题数 | skills-agent均分 | 对方均分 | 对方−我方（百分点） | 比三方集合多出的题 |
| --- | --- | --- | --- | --- | --- |
| ALE01 对 ALE02 | 27 | 71.0597% | 63.3650% | -7.6947 | `computing_math/particle_filter_nonlinear_tracking` |
| ALE01 对 ALE03 | 27 | 74.6800% | 72.7380% | -1.9420 | `engineering/mpc_control_building_v1` |

这两个 27 题集合不同，不能横向用它们给三台机器统一排名。主结论仍使用固定相同 26 题。

## 8. 数据与复核

- [ALE01 快照](../../tmp/ale-cross-agent-20261015/ale01.json)
- [ALE02 快照](../../tmp/ale-cross-agent-20261015/ale02.json)
- [ALE03 快照](../../tmp/ale-cross-agent-20261015/ale03.json)
- [完整比较 JSON：集合、分数与全 95 题排除原因](../../tmp/ale-cross-agent-20261015/comparison.json)
- [可复算脚本](../../tmp/ale-cross-agent-20261015/compare.py)
- [独立复核](../../tmp/ale-cross-agent-20261015/verification.json)

已将汇总记录与原始 run.json、eval_result.json、原生客户端结束状态交叉核对；独立脚本用 Decimal 再次验证均分和集合。原始数据与计算不舍入，表格显示时才舍入。

