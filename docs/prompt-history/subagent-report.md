# 子代理最终报告提示词

位置：`skills_agent/agent.py · Agent._subagent() · instructions`。状态：启用。盘点日期：2026-10-11（Asia/Shanghai）。

H 编号记录 Git 全部分支中的实际文本变化；v1 是本次工作区基线标签，不表示新增实现。这里的“效果”说明规则变化或预期作用，不代表实测结果。

该段作为子代理的附加 system 指令，普通子代理与历史检索子代理共用。2026-10-09 初版已有子代理调用，但当时没有这段独立报告规范；任务名称和任务正文的包装不等同于报告要求。

## H1 — 建立向父代理交付的报告规则

- 时间：2026-10-10 04:34:41 +08:00。
- 标识：提交 `08586891a747eea02ff1b647afe944b736534f65`。
- 发现的问题：子代理只能回传最终响应，需要约定父代理能够接续使用的内容与完成状态。
- 本版本目标：把结论、来源、产物和未解决事项组织成 Markdown 报告，并区分完成程度。
- 本版本效果：新增明确的交接格式；禁止仅凭意图或进程退出码声称成功。

原文：

```python
instructions = (
    "Your final response is a Markdown report for the parent: conclusions, concrete evidence "
    "(sources, paths, checks), artifacts and unresolved matters where applicable. Distinguish "
    "completed, partially completed, failed and unverified work. Do not claim success from "
    "an intention or process exit code alone."
)
```

## v1 — 当前工作区基线

- 时间：2026-10-11。
- 标识：工作区快照；此片段与提交 `08586891a747eea02ff1b647afe944b736534f65` 相同，无新增未提交措辞。
- 发现的问题：本次需要为现有提示词建立可维护的版本记录。
- 本版本目标：保留当前公共报告规范作为基线。
- 本版本效果：沿用 H1；没有新增报告规则，也没有 v2。

原文：

```python
instructions = (
    "Your final response is a Markdown report for the parent: conclusions, concrete evidence "
    "(sources, paths, checks), artifacts and unresolved matters where applicable. Distinguish "
    "completed, partially completed, failed and unverified work. Do not claim success from "
    "an intention or process exit code alone."
)
```
