# 提示词版本时间线

每个独立提示词或模型输入模板维护一份文档，保留版本原文，并说明发现的问题、本版本目标、本版本效果。建档日期为 2026-10-11，时区为 Asia/Shanghai。

六个实验分支自 2026-10-27 起共同使用 [工作原则 v3](agent-working-principles-20261026.md)，以自然、直接的英文表达用户确认的八节原则。全部技能及配套参考使用共享的英文正文，明确适用与跳过条件；旧版本继续保留。除主动注入选择外，系统模板与技能内容逐字一致。

## 版本约定

- **H1、H2……**：从 Git 全部可达提交中归档的历史修订，按时间先后排列。只记录模型接收的文本、工具说明或输入模板实际变化；同文件中无关的代码改动不产生新版本。迁移、替换和停用也保留在历史中。
- **v1**：本次建档的工作区基线。主工作提示词的 v1 特指本轮增量执行更新前的文本，包含此前已有的未提交沟通规范，因此不能用 Git HEAD 代替。
- **v2**：主工作提示词本轮增量执行更新后的文本。其他提示词没有在本轮修改，不人为新增 v2。
- **v3**：主工作提示词增加 file-editing 技能及现成操作脚本的使用引导；新增技能和其跨回合参考示例分别从 v1 建档。
- **v4**：主工作提示词增加实际反馈获取引导；新增 feedback 技能、分回合反馈示例和图片来源包装各自从 v1 建档，工具报告提示词追加 v2。
- **v5**：主工作提示词增加通用跨回合进程管理引导；workspace、feedback、investigation 各自追加 v2，新增 process-management 和通用提取、验收、进程参考各自从 v1 建档。
- **v6**：主工作提示词明确按当前任务组合基础能力；file-editing、code-repair、history 各自追加 v2，feedback 追加 v3，渐进编辑、跨回合反馈和验收参考各自追加 v2。移除特定产物开发顺序及固定故障验证路线，保留真实操作契约。
- **v7**：主工作提示词改为按需发现技能并引导实质交付前独立审查，新增 review 技能从 v1 建档；六项既有技能按各自版本追加，参考文件未修改。
- **2026-10-13 后续更新**：review 追加 v2，明确角色和附件交接；feedback 追加 v5、process-management 追加 v3，增加计算试跑与停止条件指导；workspace 追加 v4、定位提取参考追加 v2，同步环境探测脚本改名。主工作提示词保持 v7。
- 后续实际修改沿各自文档继续追加 v2、v3……，不覆盖既有版本。仅补充使用反馈时更新对应版本的效果说明，不另增提示词版本。

已读取 `git --all` 中的全部 43 个提交，目前可达分支为 `main` 和 `origin/main`，截止提交为 `0d88799`（2026-10-11 01:18:05 +08:00）。文档另外记录当前未提交工作区；没有记录精确修改时刻的工作区条目采用建档日期，不冒充提交时间。

历史条目的问题与目标按原文和相邻改动归纳，不代表每个问题都有当时的运行记录。效果先说明规则带来的变化；尚未观察的实际模型表现写为待观察。本次不展开效果证据和验证方法。

## 当前启用的提示词

| 文档 | 源位置或用途 | 当前版本 |
| --- | --- | --- |
| [主工作提示词](agent-system.md) | `Agent._system()`；共同原则、工具协议及按配置选择的共享skill正文 | v17（共享英文版） |
| [子 Agent 最终报告](subagent-report.md) | `Agent._subagent()` 的公共角色追加指令 | v1 |
| [历史检索子 Agent 角色](history-retrieval-role.md) | `history=True` 的专用角色追加指令 | v1 |
| [子 Agent 任务输入](subagent-task-input.md) | 子任务名称、任务文本、只读快照和原生附件标签 | v1 |
| [上下文压缩](context-compression.md) | `Auxiliary` 的 `_SUMMARY` | v1，工作区重写 |
| [历史摘要包装](context-summary-wrapper.md) | 摘要进入工作上下文的边界说明，含已停用的早期摘录包装 | v1 |
| [direct 结果读取提示](context-direct-result-guidance.md) | 原始结果无法装入上下文时的缩小读取范围提示 | v1 |
| [工具结果报告](tool-report.md) | `Auxiliary` 的 `_REPORT` | v2 |
| [工具报告缩短指令](tool-report-rewrite.md) | 报告超出额度时追加的改写要求 | v1 |
| [辅助调用附件包装](auxiliary-attachments.md) | 辅助摘要与工具报告中的只读原生附件标签 | v1 |
| [普通二进制附件读取提示](attachment-read-guidance.md) | `Session.make_user_message()` 的脚本读取要求 | v1 |
| [shell 工具说明](tool-shell.md) | 宿主机执行说明及参数描述 | v6 |
| [sandbox 工具说明](tool-sandbox.md) | Docker 执行说明及参数描述 | v2 |
| [subagent 工具说明](tool-subagent.md) | 委派范围、输入、报告和历史模式说明 | v1 |
| [要求与证据记录技能](skill-acceptance-criteria.md) | builtin_skills/acceptance-criteria/SKILL.md；按需保存可见要求与证据 | v2（共享英文版） |
| [workspace 技能](skill-workspace.md) | `builtin_skills/workspace/SKILL.md` | v6（现行技能名称同步） |
| [manage-compute-resources 技能](skill-resource-management.md) | `builtin_skills/manage-compute-resources/SKILL.md` | v5（名称与召回条件更新） |
| [investigation 技能](skill-investigation.md) | `builtin_skills/investigation/SKILL.md` | v8（召回条件与主动比较） |
| [code-repair 技能](skill-code-repair.md) | `builtin_skills/code-repair/SKILL.md` | v5（现行技能名称同步） |
| [file-editing 技能](skill-file-editing.md) | `builtin_skills/file-editing/SKILL.md` | v4（共享英文版） |
| [file-editing 渐进编辑示例](skill-file-editing-incremental-examples.md) | `builtin_skills/file-editing/references/incremental-editing.md` | v3（共享英文版） |
| [images 技能](skill-images.md) | `builtin_skills/images/SKILL.md` | v5（现行技能名称与路径同步） |
| [check-results 技能](skill-feedback.md) | `builtin_skills/check-results/SKILL.md` | v13（名称与召回条件更新） |
| [check-results 跨回合反馈示例](skill-feedback-loop.md) | `builtin_skills/check-results/references/feedback-loop.md` | v6（现行目录路径同步） |
| [check-results 验收检查参考](skill-feedback-acceptance.md) | `builtin_skills/check-results/references/acceptance-checks.md` | v4（现行目录路径同步） |
| [workspace 定位提取参考](skill-workspace-extraction.md) | `builtin_skills/workspace/references/extraction.md` | v3（共享英文版） |
| [后台任务技能](skill-background-tasks.md) | `builtin_skills/background-tasks/SKILL.md` | v8（共享英文版） |
| [process-management 技能](skill-process-management.md) | `builtin_skills/process-management/SKILL.md` | v9（共享英文版） |
| [process-management 跨回合参考](skill-process-management-across-turns.md) | `builtin_skills/process-management/references/across-turns.md` | v4（共享英文版） |
| [后台作业完成观察](background-job-result.md) | `BackgroundResults.drain()` 的运行时结果包装 | v1 |
| [后台作业监管未确认观察](background-job-uncertainty.md) | `BackgroundResults._report_uncertainty()` 的可恢复错误包装 | v1 |
| [反馈图片来源标签](feedback-image-label.md) | `feedback_content()` 的源文件与会话快照标签 | v2 |
| [图片引用](image-reference.md) | `images.image_reference()` 与工作上下文的图片 URL/事件引用 | v1 |
| [工具附件观察包装](tool-feedback-observation.md) | 请求中工具产生的只读观察与附件来源标签 | v1 |
| [history 技能](skill-history.md) | `context/history_skill/SKILL.md`，包含旧目录历史 | v3（共享英文版） |

## 已停用的提示词

| 文档 | 原用途 | 停用状态 |
| --- | --- | --- |
| [review 技能](skill-review.md) | 旧独立审查技能 | 当前运行时未提供；保留历史归档 |
| [read_skill 工具说明](tool-read-skill.md) | 初版独立技能读取工具 | `5fffe14` 已移除 |
| [已执行命令摘录标签](context-command-excerpt.md) | 旧上下文中的历史命令来源标签 | `0858689` 已移除 |
| [摘要质量审核](summary-review.md) | 独立模型审核摘要 | 当前未提交工作区已移除 |
| [摘要修复追加指令](summary-repair.md) | 按审核问题修复草稿 | 当前未提交工作区已移除 |
| [摘要审核历史读取工具](summary-review-tool.md) | 审核调用专用的 `read_history` 说明 | 当前未提交工作区已移除 |
| [摘要修复问题反馈模板](summary-review-feedback.md) | 向模型提供程序产生的具体引用问题 | 当前未提交工作区已移除 |
| [摘要审核工具反馈](summary-review-tool-feedback.md) | 向审核模型说明工具调用限制或失败 | 当前未提交工作区已移除 |

## 共享内容与显式注入选择

2026-10-27 后续统一：六个 worktree 的代码、测试、全部 skill 正文和提示词记录一致，仅 `skills_agent/system_skill.py` 中的 `SYSTEM_SKILL_NAME` 值不同。共同工作原则现为 v3 英文版，全部技能及配套参考也已按直接英文表达和使用边界统一更新。旧分支归档原文完整保留，同号历史版本以来源分支区分；各文档末尾及上表列出当前共享版本。

| worktree | 显式注入选择 |
| --- | --- |
| 00-working-principles-baseline | `None`（关闭预载，仍可按需读取skill） |
| 01-data-semantics | `investigation` |
| 02-discriminating-experiments | `investigation` |
| 03-feedback-conflicts | `check-results` |
| 04-discriminating-verification | `check-results` |
| 05-measured-resources | `manage-compute-resources` |

01与02当前配置相同，03与04当前配置相同；名称保留用于对应历史实验，不再代表不同正文。

## 2026-10-28 召回条件与名称更新

本轮保留 `investigation` 名称，将 `feedback` 改名为 `check-results`，将 `resource-management` 改名为 `manage-compute-resources`。三者使用用户确认的自然语言 description，并更新方法正文；其他技能及参考中的相关名称、链接和示例路径同步到现行目录。历史文档继续沿用原文件名，所有既有版本原文保留。

20261028 的45次基线运行均可见这三个技能的目录描述，但未读取其正文。本轮针对目录触发条件与名称清晰度进行修改；没有据此认定未召回就是低分原因，也没有新一轮真实模型测评证明召回或分数已经改善。名称和描述在本轮同时变化，后续效果不能仅归因于其中一个词。

六个实验 worktree 共享本轮技能内容，仅显式注入选择按上表区分。主系统模板与八节工作原则未改，继续分别使用 v17 和 v3，不追加对应提示词版本。

本轮实现提交标识在完成提交后补入此处；提交记录作为后续独立文档提交维护，避免文档自引用其自身提交标识。

<!-- SKILL_RECALL_COMMIT_RECORDS -->

## 维护方式

修改提示词时，在对应文档末尾追加版本日期、提交或工作区状态、完整原文和以下三个说明。提交后补充提交标识，保留版本编号；如果同一提交包含多处提示词改动，各自文档分别追加。新提示词新增独立文档并加入索引；停用时保留最后原文和停用原因。

```markdown
## v5 · YYYY-MM-DD · 变更名称

来源：提交标识或工作区未提交。

- 发现的问题：触发本次修改的具体问题。
- 本版本目标：希望改变的工作方式。
- 本版本效果：已观察的表现或已确定的规则变化；暂无使用反馈时写待观察。

### 提示词原文

（保留完整原文；动态数据使用变量占位，不保存任务内容或会话数据。）
```

工具说明的参数描述归入对应工具文档；主系统提示词中的条件段归入同一份主工作提示词文档。外部加载的用户技能、临时用户任务、测试样例、终端显示文案、协议转换和配置额度不作为本项目维护的独立提示词。参考脚本的实现变化只有改变模型读取到的指令时才纳入版本。
