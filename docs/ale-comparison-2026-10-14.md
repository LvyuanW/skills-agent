# ALE 新旧 skills-agent 阶段结果与 trace 分析

统计快照：北京时间 2026-10-14 10:42:14；运行状态再次核查于 10:49:05。此次是对正在执行的复测做阶段分析，不是最终 95 题成绩。所有检查均只读，未中断、重启测评或修改 Agent，也未读取任务 reference 答案。

**上一次完整测评实际用了 51 小时 29 分 42 秒。** 北京时间 2026-10-11 07:23:19.982 开始，10 月 13 日 10:53:02.356 完成。95 题的 `duration_s` 合计 51 小时 19 分 41 秒，其余约 10 分钟是题间调度等间隔。

本次于 10 月 13 日 17:06:39 启动，快照时已运行约 17 小时 35 分，已结束 33/95 题，1 题执行中、61 题待执行。10:49 再次核查仍是此进度，控制器进程存活。当前 `engineering/aerospace_low_thrust_trajectory` 从当天 07:01 左右开始，task card 的时限为 28,800 秒（8 小时）。10:42 容器约使用 400% CPU（4 核配额）；10:44 的 solver 日志出现第 1 次迭代，cost 从约 1567.3 降至 320.46。因此有实际计算进展，不能仅凭运行时间判定调度器卡死；这也不等于算法一定会及时收敛。

**新版在已完成的相同题目上确实更慢。** 以下按相同 33 题计算，包括失败、超时和未评分题的实际耗时；不包含当前仍运行的航空题。

| 耗时口径 | 上一次 | 本次 | 变化 |
| --- | ---: | ---: | ---: |
| 同 33 题累计耗时 | 9 小时 37 分 26 秒 | 13 小时 50 分 57 秒 | +43.90% |
| 单题耗时中位数 | 234.15 秒 | 492.25 秒 | +110.23% |
| K3 abelian extensions | 144.62 秒 | 7217.65 秒 | 两次均 0 分，本次超时 |
| SEC 10-K financial parsing | 1920.73 秒 | 6533.00 秒 | 显著提分，但更慢 |
| Particle filter | 1803.48 秒 | 3664.20 秒 | 两次均 0 分 |
| Branch and bound ATSP | 1388.32 秒 | 3218.37 秒 | 两次均 1 分 |

当前串行执行且各题耗时差异很大，不宜把已完成比例线性外推为剩余时间。

## 比较范围与环境

| 项目 | 上一次 | 本次 |
| --- | --- | --- |
| 批次 | `skills_agent_formal_20261010T231233Z` | `skills_agent_formal_20261013T090106Z_73666d7` |
| Agent 版本证据 | release `20261010T224923Z-40f68f26f4b3`，包含当时未提交改动，应以 wheel 为准 | commit `73666d7f2b02c68f64c7343f6301211bb4ff4c43`，运行包与该提交逐文件核对 |
| wheel SHA256 | `40f68f26f4b37ea72e4e85f43a9bfb3b332aaa957ecef1329289cd7c1fe31c51` | `ef572d47b588529deca69b1c11a6e5cbb1c6b7a98c117791d21b601ec0272d5c` |
| 任务协议 | 95 题，variant 0，串行，每题一次 | 相同 |
| 模型 | deepseek-flash，effort low，chat API | 相同 |
| token 配置 | context 524000、单次输出 32768、summary 8192 | 相同 |

两轮均使用 ALE 官方基础镜像，并固定相同 digest：

```text
agentslastexam/ale-ubuntu22-docker@sha256:78ec11afeb0008ed8bc2b59cf9c90c05e63d1ac66b9d3e7cb0fada10695fca6f
```

这是同协议复测；仍保留原有两个 28g 内存覆盖、ALE 兼容和取消清理补丁，不能称为全部官方默认资源的 ALE 全量成绩。运行时安装 Agent 依赖也没有实现完整依赖锁定。具体环境和启动流程见 [测评启动流程](ale-evaluation-runbook.md)。本轮证据不支持把慢或失败归因于“没有使用 ALE 标准基础容器”。

## 阶段成绩：先排除已证实的评分故障

原始快照有 33 题结束、32 个有限数值评分，旧版与之共同有分的为 31 题。进一步审查发现 ranking 题两轮评分器都在候选程序执行前，因 `uv sync --frozen` 准备环境失败而退出；任务包装层将非 JSON 结果转换为 `[0.0]`，使 `eval_status=success` 掩盖了基础设施错误。因此保留原始成绩，但在能力比较中排除这对伪零分。

| 比较口径 | 上一次均分 | 本次均分 | 变化 |
| --- | ---: | ---: | ---: |
| 原始共同有分 31 题 | 0.566323 | 0.582939 | +0.016616 |
| 排除已证实评分环境故障，共同 30 题 | 0.585200 | 0.602370 | +0.017170 |
| 再去除提升最大的 SEC 题，共同 29 题 | 0.591079 | 0.590318 | −0.000762 |

30 题中 **3 升、5 降、22 不变**；两轮均为零分 8、满分 10、部分分 12。这里“排除故障”不表示已穷尽审计其他所有评分器。每版只有一次尝试且当前覆盖不全，暂不能判断新版整体稳定改善，更不能把所有差异因果归结为新 skill 或 prompt。

| 变化任务 | 上一次 | 本次 |
| --- | ---: | ---: |
| sec_10k_financial_parsing | 0.414700 | 0.951900 |
| cp_test_gen_1 | 0.300000 | 0.500000 |
| digital_marketing_audience_segmentation_1 | 0.904200 | 0.941700 |
| recsys_cold_start_instance_1 | 0.666667 | 0.500000 |
| moodle_gradebook_closeout_reconciliation | 0.950000 | 0.885000 |
| k8s_payment_api_root_cause_analysis | 0.736538 | 0.722727 |
| synthetic_causal_structure_inference | 0.244316 | 0.237040 |
| cost_optimization_1 | 0.872915 | 0.866075 |

财报重建题旧版因 RootlessKit 端口冲突未评分、新版为 1.0，不能作为配对提分；PDE 批改题两轮均是 evaluator 的 `dict.lower` 错误，不能归为 Agent 解题失败。旧版全量 91 个原始评分均分 0.535934 与本次局部 32 个评分均分 0.595972 覆盖不同，不应直接比较。排除 ranking 后的非配对均分也不能用于整体版本结论。

## Trace 确认的 skills-agent 问题

### 1. 长工具调用缺少可恢复的等待机制，也缺少剩余时间预算

`skills_agent/tools/__init__.py:79–98` 使用 `Popen` 后直接 `process.communicate()`，第 86 行没有 timeout 或定期返回机制；工具参数也没有等待上限。外部终止可以清理进程，但主 Agent 在调用期间不能根据进展调整策略。`benchmark.py` 与当前调用链未把 ALE 剩余时限作为 Agent 预算传入。

K3 新 trace 的 `events.jsonl:5136` 于 UTC 17:31:21.649 开始 `cd /tmp/work && python3 enum.py`，直到第 5137 行 UTC 19:30:21.210 才收到中断，共 **7139.56 秒（118 分 59.56 秒）**。命令没有 timeout，最终回收的输出目录为空，整题超时。证据能证明长时间失去控制，不能仅据此断定生成算法具体在哪一处死循环。

Clustered cyclic 的第 3456 行在整题仅剩约 15 分钟时执行 `sleep 1800`，约 909 秒后被外层时限中断。最后存在一份部分 CSV，但整题超时、0 分。当前 aerospace 的第 159016 行同样执行 `sleep 1500 && cat work/ms.log`；这是长轮询行为，当前还不能作为失败结果。

优先改进方向：向 Agent 和工具层传递任务 deadline；长作业返回 session/进程句柄并定期收集状态；等待应有上限并受剩余预算约束；在硬截止前预留写出产物与验证时间。严格保留题目计算要求，不能靠减少规定样本数来制造完成。实际并行度应结合容器 CPU 配额，不能仅依据宿主机 `nproc`。

### 2. 主模型调用遇到断流、空回复或输出耗尽后直接结束整题

`skills_agent/agent.py:288` 直接调用模型；第 296–297 行把空答案视为错误；第 307–328 行记录后重新抛出，主路径没有有限恢复。相比之下，`context/auxiliary.py:264–267` 对连接错误已有三次尝试。

| 任务 | 已观察到的终止原因 | 对结果的正确解读 |
| --- | --- | --- |
| Ranking | 新 trace 第 24183 行：HTTP 200 后 3.29 秒 SSE 提前 EOF，未收到 `[DONE]`，review 工具参数未完整生成 | 残缺调用没有被执行；拒绝残缺参数是正确的，但一次断流就退出暴露恢复缺口。其官方 0 分另有评分器故障，不能归因于此异常 |
| Moodle | 第 10328 行 `finish_reason=stop`，205 个输出 token 全是 reasoning，无正文或工具；第 10329 行报空回复 | 已保留 13 个产物，真实得分 0.885；failed 不等于无产出，也不能把与旧版 0.95 的全部差值归于空回复 |
| Go reconstruction | 第 123814 行，单次 32768 个输出 token 全用于 reasoning，因 `length` 终止，输出目录为空 | 旧版也有同样问题，属于重复缺陷；输入约 104k，小于 524k 上下文，耗尽的是单次输出预算 |

应对尚未接受的模型生成做有限重试或明确的继续策略，保存失败诊断；不能执行残缺工具 JSON，也不能重放已经执行成功的 shell。`length` 不能只盲目重试同一长推理，应分阶段形成可执行行动和持久产物。Moodle 的最后一次模型请求持续约 607 秒；底层 120 秒 socket timeout 并不是整次请求 deadline，现有证据不能确定服务端慢的细因。

### 3. 优化指标与交付目标脱节，独立 review 会被主 Agent 的解释带偏

Recsys 从 0.666667 降到 0.5，差异集中于 warm ranking 子项：官方 NDCG 从 **0.07379 降至 0.01544**，HitRate 从 **0.395 降至 0.135**，低于 0.03/0.15 的评分门槛；冷启动指标反而提升。

旧 trace 第 22520 行比较评分预测排名与 CF 排名，采用更好的 CF 排名并保留独立评分回归。新 trace 第 29317 行围绕验证 RMSE 调参，再使用 predicted_rating 排名；第 49485 行已有偏低排名指标。优化评分预测误差并不保证推荐排名好。

新主 Agent 在第 53636 行交给 reviewer 的描述中预先说明“低 NDCG 属于预期”。reviewer 在第 53638 行接受了该解释，主要修复切分取整，而最终检查侧重列名、类型、rank、有限数值与配置。它复现了实现，但没有充分挑战结果质量。这里说明的是一次具体审查失效，不能据此否定所有 review：SEC 的 reviewer 确实检查了 schema、独立文本提取与数值来源。

改进方向：先根据原题建立验收项与基线，再做算法选择；将测量结果作为事实传给 reviewer，避免预先为弱指标辩护；要求 reviewer 独立检查任务目标、约束和质量，区分“代码可复现”“文件完整”与“目标达成”。

### 4. SEC 的提升真实，但不能掩盖效率与约束问题

SEC 从 0.4147 升至 0.9519。旧版把字段及来源摘要写成 raw extraction（第 140544 行），新版交付 PDF 全文，ANLS 从 0.1473 提升到 1.0。评分器在 ANLS < 0.6 时还会将 schema、metadata、financial、cross-validation 贡献归零，因此旧版这些分项为 0 不等于底层财务准确率为 0。

新版第 3065–3066 行的串行 pdfplumber 全量提取耗时 **5809.14 秒（96.82 分钟），占整题 88.92%**；review 本身约 213 秒，不是此题变慢的主因。旧版用 pypdf 并行缓存提取约 806 秒，但最终 raw 文件语义错误。两种提取方式准确性与速度不同，不能仅按秒数主张无条件替换；宜先抽样验证语义和质量，再按容器配额并行、逐文件保存进度。

新版财务数值还使用公开 SEC XBRL 数据，属于额外公开来源，不是 ALE 隐藏答案；不能将提分全部归功于 review 或纯 PDF 解析。另有明确约束遗漏：原题禁止写 output 以外目录，新版多次写 `/tmp`，review 报告却声称未写外部目录。高分和 review 通过都不意味着所有题目约束已满足。

## 需要单独处理的评分问题

- Ranking：两轮 verifier 都在候选执行前准备环境失败。新旧 batch.log 分别第 1176–1190、1156–1170 行；`main.py:300–305` 把不可解析结果吞为零。应保存完整 stderr 并将其报告为评分基础设施错误。现有日志只留前 1000 字符，无法确定 uv 失败的最终细因；修复后的真实分数未知。
- Recsys：两轮均因配置字段 `factors` 不在评分器维度字段白名单而丢分。原题只规定配置结构，没有规定 `n_factors` 等白名单；两版实际上都记录了潜变量维度。因此不能将这两项失败解读成“没有用潜变量模型”或“没有切分数据”，应标记为未充分说明的评分契约问题。
- PDE 的 evaluator 异常、旧财报重建的容器端口冲突，均与解题能力分开处理。

建议先处理 deadline 与长工具调用、主模型调用恢复，再改善按原题验收的 review。评分器环境和异常归类应独立修复；不能通过改当前运行的 Agent 或评分逻辑，把前后不同条件混进本次正式批次。

## 可复核证据

原始 trace 引用均是 `origin_log/skills_agent/session/events.jsonl` 的物理行号。下列 JSON 记录了完整服务器路径、行号和脱敏摘录；统计脚本核对了每题原始 run/eval 与汇总，快照中未发现二者不一致。

- [原始统计快照](/Users/apple/dev/learn-ai-agent/tmp/ale-comparison-20261014/comparison-snapshot.json)
- [剔除已证实评分故障后的统计](/Users/apple/dev/learn-ai-agent/tmp/ale-comparison-20261014/audited-comparison.json)
- [逐题比较与两版 run_dir](/Users/apple/dev/learn-ai-agent/tmp/ale-comparison-20261014/paired-task-table.csv)
- [长工具调用证据](/Users/apple/dev/learn-ai-agent/tmp/ale-comparison-20261014/runtime-trace-evidence.json)
- [断流、空回复、输出上限与评分故障证据](/Users/apple/dev/learn-ai-agent/tmp/ale-comparison-20261014/failed-trace-findings.json)
- [Recsys 与 SEC 分项、trace 和评分代码证据](/Users/apple/dev/learn-ai-agent/tmp/ale-comparison-20261014/recsys-sec-evidence.json)
