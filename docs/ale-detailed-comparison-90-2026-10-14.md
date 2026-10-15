# 固定 90 题：六个独立研究配置与 skills-agent 的详细比较

日期：2026-10-14。原始批次：`skills_agent_formal_20261010T231233Z`；我方为 deepseek-flash + skills-agent，Low。第三方来自 [Finding the Right Fit](https://huggingface.co/datasets/yixuanli97/finding-the-right-fit)，其研究配置请求 High。本报告是这两个来源之间的同 task ID 比较，不是 ALE 官方排行榜。

## 主要发现

我方均分 54.1889%，Claude Opus 5 + Claude Code 为 54.5353%，只差 −0.3464 个百分点。但逐题平均绝对差为 21.60 个百分点：我们领先 20 题、持平 48 题、落后 22 题。相近均分来自不同题目上的得失相互抵消。

生命科学是最有支撑的领域优势，18 题领先 18.50 个百分点，尤其细胞与成像、生物序列分析；物理科学 9 题落后 24.83 个百分点，是最大领域短板。健康医学 20 题、计算数学 17 题也落后。工程的相对优势主要由 SUMO 一题产生，不能扩展成普遍工程能力优势。教育、心理神经、社会科学等只有 1–2 题，不作广泛领域结论。

最大差距的前 10 道短板题中，4 道是超时、2 道是进程异常退出。它们保留在 90 题比较中，但更能说明执行与交付问题，不能直接归因为专业知识不足。运行完成的题中也存在实际结果错误及缺少可评分产物。

## 1. 六个配置的完整成绩

所有配置均使用同一份固定 90 题清单，逐题等权，不填补缺失。以下按均分排序，插入我方作为参照。通过阈值为原始 score ≥ 0.999；“零分”指记录中的精确数值 0。第三方六个配置的这 90 条记录全部被发布者标记为 `verified`。


| 排序 | 模型 + Agent | 请求档位 | 均分 | 通过题数 | 通过率 | 零分题数 | 相对我方均分（百分点） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | GPT-6 Astra + PI | High | 59.27% | 28/90 | 31.11% | 25 | +5.08 |
| 2 | Kimi K3 + openJiuwen | High | 56.35% | 30/90 | 33.33% | 25 | +2.16 |
| 3 | Claude Opus 5 + Claude Code | High | 54.54% | 28/90 | 31.11% | 29 | +0.35 |
| 4 | deepseek-flash + skills-agent | Low | 54.19% | 32/90 | 35.56% | 31 | +0.00 |
| 5 | Claude Opus 5 + OpenHands | High | 52.45% | 27/90 | 30.00% | 32 | -1.74 |
| 6 | DeepSeek V4 Pro + openJiuwen | High | 51.47% | 23/90 | 25.56% | 27 | -2.72 |
| 7 | Claude Opus 5 + PI | High | 50.02% | 28/90 | 31.11% | 34 | -4.17 |

我方在这七项中均分排第 4，通过题数最多（32 题）。通过更多题并不必然带来更高均分，因为各项在未通过题上的部分分不同。六个第三方配置是完整数据子集，不是对该研究全部 22 个配置的排名。

## 2. 我方与 Claude Code 的逐题关系

此后“Claude”均指 Claude Opus 5 + Claude Code（研究配置 High）。分差统一为 **我方 − Claude**，正数表示我方领先。平分容差 1e-9 仅用于胜/平/负统计，不修改原分数。


| 指标 | 结果 |
| --- | --- |
| 均分 | 54.1889% / 54.5353% |
| 胜 / 平 / 负 | 20 / 48 / 22 |
| 通过题数 | 我方 32；Claude 28 |
| 共同通过 | 21 题 |
| 仅我方通过 | 11 题 |
| 仅 Claude 通过 | 7 题 |
| 双方都零分 | 22 题 |
| 我方零分、Claude 有正分 | 9 题 |
| Claude 零分、我方有正分 | 7 题 |
| 逐题平均绝对分差 | 21.60 个百分点 |
| 领先题累计增分 | 9.5630 个原始分 |
| 落后题累计失分 | 9.8748 个原始分 |

分数尺度原为 0–1；累计增分减失分为 −0.311756 个原始分，除以 90 后即 −0.346396 个百分点。此计算没有删除难题，也没有按领域等权重算总分。

## 3. 全部一级领域

领域映射沿用 ALE `splits.json` 元数据。总分按题等权，领域对整体差的贡献 = 领域分差 × 题数 / 90；贡献可相加得到整体 −0.3464 个百分点。小样本领域仅描述这批题。

| 领域 | 题数 | 我方 | Claude | 差值（百分点） | 胜/平/负 | 整体贡献（百分点） | 样本说明 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 健康与医学 | 20 | 41.81% | 49.48% | -7.67 | 4/11/5 | -1.70 |  |
| 生命科学 | 18 | 69.13% | 50.64% | +18.50 | 7/8/3 | +3.70 |  |
| 计算与数学 | 17 | 55.61% | 62.90% | -7.29 | 2/11/4 | -1.38 |  |
| 商业与金融 | 10 | 51.52% | 47.08% | +4.44 | 2/6/2 | +0.49 |  |
| 物理科学 | 9 | 56.02% | 80.85% | -24.83 | 1/3/5 | -2.48 |  |
| 工程 | 6 | 36.67% | 25.62% | +11.04 | 1/4/1 | +0.74 |  |
| 运输与安全 | 3 | 54.64% | 53.97% | +0.67 | 1/2/0 | +0.02 |  |
| 教育与信息 | 2 | 97.50% | 57.50% | +40.00 | 1/1/0 | +0.89 | 仅 1–2 题 |
| 法律 | 2 | 76.34% | 82.30% | -5.96 | 0/1/1 | -0.13 | 仅 1–2 题 |
| 其他 | 1 | 0.00% | 0.00% | +0.00 | 0/1/0 | +0.00 | 仅 1–2 题 |
| 心理与神经 | 1 | 100.00% | 44.24% | +55.76 | 1/0/0 | +0.62 | 仅 1–2 题 |
| 社会科学 | 1 | 0.00% | 100.00% | -100.00 | 0/0/1 | -1.11 | 仅 1–2 题 |

生命科学贡献 +3.70 个百分点，是最大的正向来源；物理科学 −2.48、健康医学 −1.70、计算数学 −1.38，是主要负向来源。不能只看某个小样本领域的百分比差就判定重要性。

更细的任务类型中：

- **细胞与成像生物学**：6 题，57.40% 对 20.25%，领先 37.15 个百分点；优势集中在骨髓细胞注释、酵母菌落检测、空间转录组分区。
- **基因组与序列分析**：10 题，87.99% 对 78.98%，领先 9.01 个百分点；TCGA、基因组区间处理表现好，但 HG002 变异检测流程仍为明显短板。
- **临床诊断与影像**：3 题，0% 对 52.26%。其中 WSI 与 NSCLC 两题我方超时，CT 几何校准双方都为零；优先检查运行预算、执行进展和可交付产物。
- **数学与运筹**：5 题，60% 对 80%；差距全部来自 K3 题，其余四题双方同分，不宜解释为广泛数学能力差距。

## 4. 分差最大的优势题

以下按我方领先幅度排序；并列时按 task ID 排序。列出前 12 题，完整 90 题见后文。


| 题目 | task ID | 我方 | Claude | 差值（百分点） | 我方运行状态 |
| --- | --- | --- | --- | --- | --- |
| 美式期权定价 | `business_finance/american_option_pricing_ls` | 100.00% | 0.00% | +100.00 | completed |
| SUMO 城市早高峰交通校准 | `engineering/sumo_urban_am_peak_calibration` | 100.00% | 0.00% | +100.00 | completed |
| TCGA 乳腺癌差异表达分析 | `life_sciences/tcga_brca_deg_analysis` | 100.00% | 0.00% | +100.00 | completed |
| 骨髓细胞类型注释 | `life_sciences/tms_marrow_cell_type_annotation_instance_1` | 100.00% | 0.00% | +100.00 | completed |
| 酵母菌落检测 | `life_sciences/yeast_colony_detection` | 100.00% | 0.00% | +100.00 | completed |
| 基因组区间处理 | `life_sciences/genomic_interval_processing_1` | 100.00% | 15.00% | +85.00 | completed |
| 空间转录组空间分区 | `life_sciences/spatial_transcriptomics_spatial_domain_identification` | 82.42% | 0.00% | +82.42 | completed |
| MARC 书目修复与 FOLIO 覆盖更新 | `education_info/marc_remediation_folio_overlay` | 100.00% | 20.00% | +80.00 | completed |
| 线虫神经元跟踪 | `psychology_neuro/celegans_neuron_tracking` | 100.00% | 44.24% | +55.76 | completed |
| 冷启动推荐 | `computing_math/recsys_cold_start_instance_1` | 66.67% | 16.67% | +50.00 | completed |
| 公共卫生口罩政策比率计算 | `health_medicine/public_health_mask_mandate_ratio` | 33.33% | 0.00% | +33.33 | completed |
| CRF 到 SDTM 映射 | `health_medicine/crf_sdtm_mapping_4` | 77.27% | 56.94% | +20.33 | completed |

这些是输出结果上的优势证据。未在本次逐一复核第三方轨迹，不能断言对方为什么失分，也不能把某题 0 分解释为某个模型完全不会该专业。

## 5. 分差最大的短板题

| 题目 | task ID | 我方 | Claude | 差值（百分点） | 我方运行状态 |
| --- | --- | --- | --- | --- | --- |
| K3 阿贝尔群扩张 | `computing_math/k3_abelian_extensions` | 0.00% | 100.00% | -100.00 | completed |
| 全切片病理肿瘤定位 | `health_medicine/wsi_tumor_localization_1` | 0.00% | 100.00% | -100.00 | timeout |
| GLM 湖泊模型校准 | `physical_sciences/glm_lake_calibration` | 0.00% | 100.00% | -100.00 | failed |
| Atwood 麻疹疫苗论文复现 | `social_sciences/atwood_2022_measles_vaccine_reproduction` | 0.00% | 100.00% | -100.00 | failed |
| HG002 第 22 号染色体变异检测流程 | `life_sciences/hg002_chr22_germline_variant_pipeline` | 0.00% | 95.00% | -95.00 | timeout |
| HST ACS/WFC 天文图像归约 | `physical_sciences/hst_acs_wfc_visit_reduction` | 0.00% | 90.89% | -90.89 | completed |
| NSCLC 影像组学与 Cox 模型 | `health_medicine/nsclc_radiomics_cox_signature_v1` | 0.00% | 56.77% | -56.77 | timeout |
| 竞赛程序对抗测试生成 | `computing_math/cp_test_gen_1` | 30.00% | 80.00% | -50.00 | completed |
| MERFISH 图像解码与分割 | `life_sciences/merfish_image_decoding_segmentation_1` | 0.00% | 47.50% | -47.50 | timeout |
| NHANES 混杂敏感性分析 | `health_medicine/nhanes_confounder_sensitivity_analysis` | 57.12% | 100.00% | -42.88 | completed |
| 10kV 馈线可靠性分析 | `engineering/power_10kv_feeder_reliability_001` | 0.00% | 33.74% | -33.74 | failed |
| 北向程序化交易合规分析 | `business_finance/sse_northbound_programmatic_trading_01` | 33.33% | 66.67% | -33.33 | completed |

## 6. 短板的机制：区分结果错误、执行失败与产物交付

以下解释只引用原始批次记录，不用后续重跑替代原分数。

| 题目或类型 | 已观察到的证据 | 可以得出的结论 |
| --- | --- | --- |
| WSI、HG002、NSCLC、MERFISH | 原始运行状态为 timeout；最终数值评分为 0 | 本次未在预算内形成得分结果，是执行/交付短板；尚不能区分知识、工具使用、算力与时间分配的贡献 |
| GLM 湖泊模型校准 | 轨迹以 `OpenAI stream ended before [DONE]` 终止；评分器正常运行，RMSE 约 2.75655，高于 1.5 阈值 | 流协议异常与最终校准质量不足并存；日志中的 OpenAI 字样指兼容协议，不意味着模型换成 OpenAI |
| Atwood 疫苗论文复现 | 相同流协议异常；没有交齐 6 个必需文件 | 运行中断与交付不完整，不宜归为领域知识差 |
| K3 阿贝尔群扩张 | 运行完成，评分提示 canonicalized payload 与参考不匹配 | 存在实际结果正确性问题，值得单独检查算法/推导与输出规范 |
| HST 天文图像归约 | 运行完成，评分日志报告三个 visit 缺少所需产物 | 交付或评分运行链路没有形成可评分文件；根因尚未定位，不能认定为输出路径错误或天文学知识不足 |
| 竞赛对抗测试生成、NHANES 分析 | 运行完成，分别获 0.30、0.571215，而 Claude 为 0.80、1.00 | 存在明显部分分差；仅据分数不能确定漏项、算法还是格式是主因 |

按我方运行状态事后分层可以定位损失来源，但会产生选择偏差，不能据此删除失败题或发布新的能力排名：


| 我方状态 | 题数 | 我方 | Claude | 组内差值（百分点） | 整体贡献（百分点） |
| --- | --- | --- | --- | --- | --- |
| completed | 71 | 67.28% | 60.21% | +7.07 | +5.58 |
| failed | 8 | 12.49% | 41.71% | -29.22 | -2.60 |
| timeout | 11 | 0.00% | 27.21% | -27.21 | -3.33 |

完成的 71 题贡献 +5.58 个百分点；失败和超时的 19 题贡献 −5.92 个百分点，抵消后才成为总分的 −0.35。这里是基于我方状态的描述性分层，不能证明消除所有运行错误就能追平对方。

据此，后续排查可先集中在三件事：

1. **流中断后的恢复与产物保留**：检查 GLM、Atwood 的兼容流异常，确保中断不丢已完成结果；优化搜索保留最佳可复现产物。
2. **长流程的阶段交付与预算管理**：复盘 WSI、HG002、NSCLC、MERFISH 在何处耗尽时间，检查依赖准备、数据规模与阶段产物。建议需要日志验证，不把超时全部归咎于推理能力。
3. **结束前的交付合同检查和结果校验**：确认文件位置、必需文件、可运行入口与完整性，重点检查 HST；对 K3、竞赛测试和 NHANES 另做内容正确性诊断。

## 7. 全部细分领域

沿用官方 `subdomainName`，共 40 类，许多只有单题。英文名称保留，避免中文归并改变口径。


| 细分领域 | 题数 | 我方 | Claude | 差值（百分点） | 胜/平/负 |
| --- | --- | --- | --- | --- | --- |
| Genomics & Sequence Analysis | 10 | 87.99% | 78.98% | +9.01 | 4/4/2 |
| Public Health & Epidemiology | 7 | 65.37% | 65.47% | -0.11 | 2/3/2 |
| Cell & Imaging Biology | 6 | 57.40% | 20.25% | +37.15 | 3/1/2 |
| Mathematical & Operations Research | 5 | 60.00% | 80.00% | -20.00 | 0/4/1 |
| Data & Analytics Engineering | 4 | 62.02% | 55.86% | +6.16 | 1/1/2 |
| AI Engineering, Safety & CS Research | 3 | 60.00% | 59.50% | +0.50 | 1/2/0 |
| Biomolecular Structure & Design | 3 | 33.33% | 33.33% | +0.00 | 0/3/0 |
| Clinical Diagnostics & Imaging | 3 | 0.00% | 52.26% | -52.26 | 0/1/2 |
| Clinical Research & Trial Operations | 3 | 34.09% | 27.31% | +6.78 | 1/2/0 |
| Compliance & Regulatory | 3 | 45.34% | 60.42% | -15.08 | 0/1/2 |
| Economics & Quantitative Social Research | 3 | 32.15% | 64.21% | -32.06 | 1/1/1 |
| Quantum Computing | 3 | 66.67% | 66.67% | +0.00 | 0/3/0 |
| Chemistry & Materials Computation | 2 | 83.31% | 91.45% | -8.15 | 1/0/1 |
| Infrastructure Engineering & Cloud Operations | 2 | 93.65% | 93.65% | +0.00 | 0/2/0 |
| Physics | 2 | 50.00% | 50.00% | +0.00 | 0/2/0 |
| Robotics & Autonomous Systems | 2 | 50.00% | 50.00% | +0.00 | 0/2/0 |
| Sales & Marketing | 2 | 95.21% | 93.54% | +1.67 | 1/1/0 |
| Sports | 2 | 0.00% | 0.00% | +0.00 | 0/2/0 |
| Systems & Microbial Biology | 2 | 92.00% | 100.00% | -8.00 | 0/1/1 |
| Therapeutic & Oncology Services | 2 | 0.00% | 0.00% | +0.00 | 0/2/0 |
| Urban & Spatial Planning | 2 | 50.00% | 0.00% | +50.00 | 1/1/0 |
| Accounting & Finance | 1 | 41.47% | 67.03% | -25.56 | 0/0/1 |
| Actuarial & Risk Modeling | 1 | 0.00% | 0.00% | +0.00 | 0/1/0 |
| Aerospace & Mechanical Engineering | 1 | 0.00% | 0.00% | +0.00 | 0/1/0 |
| Astronomy & Astrophysics | 1 | 0.00% | 90.89% | -90.89 | 0/0/1 |
| Clinical Informatics & Care Operations | 1 | 0.00% | 0.00% | +0.00 | 0/1/0 |
| Computational Neuroscience | 1 | 100.00% | 44.24% | +55.76 | 1/0/0 |
| Doctrinal Legal Research | 1 | 100.00% | 100.00% | +0.00 | 0/1/0 |
| Earth & Atmospheric Sciences | 1 | 53.58% | 53.87% | -0.29 | 0/0/1 |
| Educational Technology | 1 | 95.00% | 95.00% | +0.00 | 0/1/0 |
| Energy, Power & Nuclear Engineering | 1 | 0.00% | 33.74% | -33.74 | 0/0/1 |
| Enterprise Analytics & Planning | 1 | 0.00% | 0.00% | +0.00 | 0/1/0 |
| Environmental Modeling, Engineering & Water Resources | 1 | 0.00% | 100.00% | -100.00 | 0/0/1 |
| Fire Science & Public Safety | 1 | 63.91% | 61.91% | +2.00 | 1/0/0 |
| HR & Project Management | 1 | 100.00% | 100.00% | +0.00 | 0/1/0 |
| Library & Information Science | 1 | 100.00% | 20.00% | +80.00 | 1/0/0 |
| Litigation Support & Discovery | 1 | 0.00% | 0.00% | +0.00 | 0/1/0 |
| Quantitative Finance & Trading | 1 | 100.00% | 0.00% | +100.00 | 1/0/0 |
| Semiconductor & Microelectronics Design | 1 | 20.00% | 20.00% | +0.00 | 0/1/0 |
| Software Engineering | 1 | 30.00% | 80.00% | -50.00 | 0/0/1 |

## 8. 完整 90 题 × 7 配置分数矩阵

以下全部是百分制分数；0.00 是记录中的数值零，不是填补值。显示保留两位小数，计算使用原始精度。列缩写：

- 我方：deepseek-flash + skills-agent（Low）。
- GPT-PI：GPT-6 Astra + PI；Kimi-OJ：Kimi K3 + openJiuwen。
- Opus-CC：Claude Opus 5 + Claude Code；Opus-OH：Claude Opus 5 + OpenHands；Opus-PI：Claude Opus 5 + PI。
- DS-OJ：DeepSeek V4 Pro + openJiuwen。第三方配置请求 High。

差值列为我方 − Opus-CC，单位为百分点。


| 序号 | task ID | 我方 | GPT-PI | Kimi-OJ | Opus-CC | Opus-OH | DS-OJ | Opus-PI | 我方−CC |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `business_finance/american_option_pricing_ls` | 100.00 | 0.00 | 100.00 | 0.00 | 100.00 | 100.00 | 100.00 | +100.00 |
| 2 | `business_finance/basel_operational_risk_bia_cn` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 3 | `business_finance/digital_marketing_ab_test_analysis_1` | 100.00 | 80.00 | 100.00 | 100.00 | 80.00 | 80.00 | 80.00 | +0.00 |
| 4 | `business_finance/digital_marketing_audience_segmentation_1` | 90.42 | 69.12 | 90.42 | 87.08 | 90.42 | 68.70 | 90.83 | +3.34 |
| 5 | `business_finance/ff5_public_reconstruction` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 6 | `business_finance/internal_employee_agent_instance_1` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 7 | `business_finance/legal_ma_consistency_audit_01` | 0.00 | 0.00 | 20.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 8 | `business_finance/llm_ecosystem_privacy_audit_realdata_1` | 50.00 | 50.00 | 50.00 | 50.00 | 50.00 | 50.00 | 50.00 | +0.00 |
| 9 | `business_finance/sec_10k_financial_parsing` | 41.47 | 91.50 | 67.37 | 67.03 | 64.69 | 61.68 | 65.92 | -25.56 |
| 10 | `business_finance/sse_northbound_programmatic_trading_01` | 33.33 | 100.00 | 33.33 | 66.67 | 66.67 | 33.33 | 33.33 | -33.33 |
| 11 | `computing_math/branch_bound_atsp` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 33.75 | 100.00 | +0.00 |
| 12 | `computing_math/cfr_game_theory_equilibrium` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 67.00 | 100.00 | +0.00 |
| 13 | `computing_math/clustered_cyclic_code_circuit_level_simulation` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | 0.00 | +0.00 |
| 14 | `computing_math/cost_optimization_1` | 87.29 | 85.53 | 87.29 | 87.29 | 87.29 | 85.15 | 87.29 | +0.00 |
| 15 | `computing_math/cp_test_gen_1` | 30.00 | 70.00 | 40.00 | 80.00 | 50.00 | 30.00 | 70.00 | -50.00 |
| 16 | `computing_math/data_pipeline_etl_instance_1` | 83.33 | 83.33 | 83.33 | 83.33 | 83.33 | 83.33 | 83.33 | +0.00 |
| 17 | `computing_math/dit_pipeline_cfg_alignment_fid_256_001` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 18 | `computing_math/go_game_reconstruction_1` | 0.00 | 0.00 | 12.50 | 0.00 | 0.00 | 100.00 | 0.00 | +0.00 |
| 19 | `computing_math/ising_post_measurement_1` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 20 | `computing_math/k3_abelian_extensions` | 0.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | -100.00 |
| 21 | `computing_math/k8s_payment_api_root_cause_analysis` | 73.65 | 89.77 | 72.27 | 87.50 | 92.50 | 72.27 | 73.21 | -13.85 |
| 22 | `computing_math/mp_checkpoint_consolidation_v2` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 0.00 | +0.00 |
| 23 | `computing_math/os_log_permission_guard_v1` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 24 | `computing_math/paper_reproduction_instance_1` | 80.00 | 78.50 | 78.50 | 78.50 | 80.00 | 78.50 | 80.00 | +1.50 |
| 25 | `computing_math/particle_filter_nonlinear_tracking` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 26 | `computing_math/recsys_cold_start_instance_1` | 66.67 | 66.67 | 83.33 | 16.67 | 66.67 | 50.00 | 66.67 | +50.00 |
| 27 | `computing_math/synthetic_causal_structure_inference` | 24.43 | 96.18 | 47.58 | 35.93 | 50.49 | 25.27 | 28.95 | -11.50 |
| 28 | `education_info/marc_remediation_folio_overlay` | 100.00 | 20.00 | 20.00 | 20.00 | 20.00 | 20.00 | 20.00 | +80.00 |
| 29 | `education_info/moodle_gradebook_closeout_reconciliation` | 95.00 | 81.70 | 95.00 | 95.00 | 95.00 | 95.00 | 95.00 | +0.00 |
| 30 | `engineering/abb_irb6700_asset_to_urdf_instance_1` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 31 | `engineering/aerospace_low_thrust_trajectory` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 32 | `engineering/chisel_verilog_alignment_seq_1` | 20.00 | 20.00 | 20.00 | 20.00 | 20.00 | 20.00 | 20.00 | +0.00 |
| 33 | `engineering/humanoid_wbc_policy_evaluation` | 0.00 | 0.00 | 70.00 | 0.00 | 51.25 | 0.00 | 0.00 | +0.00 |
| 34 | `engineering/power_10kv_feeder_reliability_001` | 0.00 | 32.50 | 22.08 | 33.74 | 19.51 | 13.82 | 26.29 | -33.74 |
| 35 | `engineering/sumo_urban_am_peak_calibration` | 100.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +100.00 |
| 36 | `health_medicine/Clinical_Variant_Annotation` | 80.00 | 80.00 | 80.00 | 100.00 | 100.00 | 80.00 | 100.00 | -20.00 |
| 37 | `health_medicine/causal_ihdp_ite_estimation_6a_v1` | 96.45 | 93.58 | 28.69 | 92.63 | 93.69 | 85.06 | 0.00 | +3.82 |
| 38 | `health_medicine/crf_sdtm_mapping_1` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 39 | `health_medicine/crf_sdtm_mapping_4` | 77.27 | 56.94 | 58.37 | 56.94 | 57.89 | 58.61 | 55.26 | +20.33 |
| 40 | `health_medicine/ct_geometry_calibration_catphan` | 0.00 | 100.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 41 | `health_medicine/epidemiology_forecast` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 42 | `health_medicine/flusight_offline_hosp_forecast_2024_12_14` | 67.12 | 66.09 | 77.05 | 74.31 | 72.16 | 70.42 | 70.97 | -7.20 |
| 43 | `health_medicine/healthcare_bias_audit_27a_public_replication_v1` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 44 | `health_medicine/healthcare_sap_group_sequential_nsclc` | 25.00 | 25.00 | 25.00 | 25.00 | 25.00 | 30.00 | 25.00 | +0.00 |
| 45 | `health_medicine/healthcare_tcga_luad_survival_kras` | 100.00 | 84.00 | 98.50 | 84.00 | 84.00 | 84.00 | 84.00 | +16.00 |
| 46 | `health_medicine/healthcare_variant_annotation_pipeline` | 99.90 | 79.30 | 100.00 | 99.90 | 99.90 | 100.00 | 99.90 | +0.00 |
| 47 | `health_medicine/ltmle_targeted_bootstrap_simulation_study` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 48 | `health_medicine/nhanes_confounder_sensitivity_analysis` | 57.12 | 100.00 | 97.50 | 100.00 | 97.50 | 83.44 | 100.00 | -42.88 |
| 49 | `health_medicine/nsclc_radiomics_cox_signature_v1` | 0.00 | 0.00 | 0.00 | 56.77 | 0.00 | 55.16 | 56.74 | -56.77 |
| 50 | `health_medicine/obermeyer_bias_reproduction` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 73.95 | 0.00 | +0.00 |
| 51 | `health_medicine/prostate_imrt_matrad_reproduction` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 52 | `health_medicine/public_health_mask_mandate_ratio` | 33.33 | 33.33 | 0.00 | 0.00 | 0.00 | 33.33 | 33.33 | +33.33 |
| 53 | `health_medicine/replicate_paper_1` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 54 | `health_medicine/simglucose_safe_basal_control_instance_1` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 55 | `health_medicine/wsi_tumor_localization_1` | 0.00 | 97.00 | 0.00 | 100.00 | 100.00 | 100.00 | 100.00 | -100.00 |
| 56 | `legal/agora_governance_classify_instance_1` | 52.68 | 51.63 | 37.85 | 64.59 | 69.00 | 58.98 | 0.00 | -11.92 |
| 57 | `legal/legal_dr_fees_01` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 58 | `life_sciences/WGS_Variant_Calling` | 100.00 | 90.00 | 100.00 | 80.00 | 100.00 | 100.00 | 80.00 | +20.00 |
| 59 | `life_sciences/amber_minimization_script_prep_instance_1` | 0.00 | 0.00 | 100.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 60 | `life_sciences/amber_three_stage_mmgbsa_workflow_instance_1` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 61 | `life_sciences/cell_tracking_instance_1` | 62.00 | 66.06 | 82.88 | 74.00 | 100.00 | 62.17 | 100.00 | -12.00 |
| 62 | `life_sciences/cell_translocation_analysis` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 100.00 | +0.00 |
| 63 | `life_sciences/gene_expression_differential_analysis_functional_enrichment_analysis_1` | 100.00 | 99.93 | 99.95 | 99.95 | 99.95 | 99.95 | 99.93 | +0.05 |
| 64 | `life_sciences/genomic_interval_processing_1` | 100.00 | 25.00 | 19.00 | 15.00 | 15.00 | 25.00 | 25.00 | +85.00 |
| 65 | `life_sciences/hg002_chr22_germline_variant_pipeline` | 0.00 | 90.00 | 90.00 | 95.00 | 90.00 | 90.00 | 100.00 | -95.00 |
| 66 | `life_sciences/merfish_image_decoding_segmentation_1` | 0.00 | 32.50 | 33.50 | 47.50 | 42.50 | 0.00 | 0.00 | -47.50 |
| 67 | `life_sciences/protein_function_annotation_instance_1` | 100.00 | 100.00 | 100.00 | 100.00 | 0.00 | 0.00 | 100.00 | +0.00 |
| 68 | `life_sciences/pseudotime_de` | 100.00 | 100.00 | 100.00 | 100.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 69 | `life_sciences/rgi_mcr1_colistin_v2` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 70 | `life_sciences/spatial_transcriptomics_spatial_domain_identification` | 82.42 | 100.00 | 86.81 | 0.00 | 0.00 | 0.00 | 0.00 | +82.42 |
| 71 | `life_sciences/tcga_brca_deg_analysis` | 100.00 | 100.00 | 100.00 | 0.00 | 0.00 | 100.00 | 100.00 | +100.00 |
| 72 | `life_sciences/tms_marrow_cell_type_annotation_instance_1` | 100.00 | 0.00 | 100.00 | 0.00 | 0.00 | 100.00 | 0.00 | +100.00 |
| 73 | `life_sciences/tp53_locus_variant_histone_browser_svg` | 100.00 | 80.00 | 100.00 | 100.00 | 80.00 | 80.00 | 80.00 | +0.00 |
| 74 | `life_sciences/yeast_colony_detection` | 100.00 | 0.00 | 0.00 | 0.00 | 100.00 | 0.00 | 0.00 | +100.00 |
| 75 | `life_sciences/zdock_hiv_dimer_interface_scoring_v1` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 76 | `other/aerobics_wc2026_portugal_trio_difficulty_scoring` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 77 | `physical_sciences/adapt_vqe_molecular_energy` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 78 | `physical_sciences/climate_prediction` | 53.58 | 79.32 | 53.21 | 53.87 | 0.00 | 53.60 | 0.00 | -0.29 |
| 79 | `physical_sciences/exact_diag_heisenberg_j1j2` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | +0.00 |
| 80 | `physical_sciences/gillespie_gene_regulatory_network` | 84.00 | 73.00 | 65.00 | 100.00 | 100.00 | 89.00 | 73.00 | -16.00 |
| 81 | `physical_sciences/glm_lake_calibration` | 0.00 | 100.00 | 100.00 | 100.00 | 100.00 | 0.00 | 100.00 | -100.00 |
| 82 | `physical_sciences/hst_acs_wfc_visit_reduction` | 0.00 | 90.50 | 0.00 | 90.89 | 96.00 | 0.00 | 0.00 | -90.89 |
| 83 | `physical_sciences/molecular_structure_plausibility` | 66.67 | 100.00 | 83.33 | 85.71 | 66.67 | 44.44 | 85.71 | -19.05 |
| 84 | `physical_sciences/phonon_dispersion_thermodynamics` | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 85 | `physical_sciences/silicon_bse_absorption` | 99.95 | 98.09 | 99.95 | 97.20 | 0.00 | 56.35 | 0.00 | +2.75 |
| 86 | `psychology_neuro/celegans_neuron_tracking` | 100.00 | 50.10 | 0.00 | 44.24 | 0.00 | 10.89 | 0.00 | +55.76 |
| 87 | `social_sciences/atwood_2022_measles_vaccine_reproduction` | 0.00 | 100.00 | 0.00 | 100.00 | 100.00 | 100.00 | 100.00 | -100.00 |
| 88 | `transport_safety/abm_hangzhou_metro` | 0.00 | 100.00 | 100.00 | 0.00 | 0.00 | 0.00 | 0.00 | +0.00 |
| 89 | `transport_safety/capacitated_vehicle_routing_problems` | 100.00 | 100.00 | 100.00 | 100.00 | 100.00 | 67.00 | 100.00 | +0.00 |
| 90 | `transport_safety/fds_single_compartment_detector_reconstruction` | 63.91 | 78.46 | 61.89 | 61.91 | 63.01 | 73.47 | 61.89 | +2.00 |

## 9. 口径、来源与可重复计算

- 固定 90 题来自原始 95 题，排除 4 个未评分任务及已证实为评分器伪零的 ranking 任务；双方同时排除，未混入后续重跑。有效数值 0、正常评分下的超时及进程失败仍保留，不能因表现不好再删除。
- 第三方从 22 配置中选择在这 90 题全部具有 `verified` 成绩的 6 配置。其他配置的 `unresolved_zero` 视为未知，不补零。`verified` 是发布者标签，本次未审计全部第三方评分器。
- 每题只有一个最终保留的评分记录；数据不提供完整重复运行分布，不能估计多次运行稳定性，也不作显著性或能力等价判断。
- 我方 ALE 基线 `d9abc0734b56ea34116c5bfcbdd0b808269ab9e2`，另有本地兼容修复；第三方版本 `0b6465b13c85b5a0a017d4c88bcf979a519a5e1f`。基础 Docker digest 相同，但代码、请求档位、预算及可能的实例差异仍限制因果解释；第三方表无 variant 字段。
- 一级领域和细分领域来自 [ALE 元数据](https://agents-last-exam.org/api/demo/splits)。这是同 task ID、固定样本的结果比较，不是相同环境下仅更换 Agent 的控制实验。

文件与复算：

- [逐题源数据](../../tmp/ale-matched-95-20261014/independent-task_level.tsv)
- [固定题目清单](../../tmp/ale-matched-95-20261014/local-cohort-manifest.json)
- [完整分析 JSON](../../tmp/ale-detailed-90-20261014/detailed-analysis.json)
- [主计算脚本](../../tmp/ale-detailed-90-20261014/analyze_details.py)与[独立复核脚本](../../tmp/ale-detailed-90-20261014/independent_pairwise.py)
- [原始运行审计](../../tmp/ale-audit-20261013/results-audit.json)
- [旧批次任务证据](../../tmp/ale-matched-95-20261014/task-evidence.json)

主计算与独立复核在 90 题匹配、均分、胜平负、通过数及领域加权贡献上结果一致。

