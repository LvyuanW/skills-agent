# 摘要修复问题反馈模板历史

位置：`skills_agent/context/auxiliary.py`。历史 `_source_issues()` 生成这些固定问题模板，通过 `verification_issues` 传给 [摘要修复追加指令](summary-repair.md)。这里记录固定文本，不把模型审核临时生成的问题当成模板版本。

按 `git log --all --follow` 的时间线记录实际文本变化。H 编号是历史版本；v1 是当前工作区基线，相同文本或停用状态不会据此虚增一个历史版本。以下效果描述规则与调用流程的变化，未记录实测结论。

## H1 — 首次提供具体引用问题反馈

日期：2026-10-10 04:34:41 +08:00。提交：`08586891a747eea02ff1b647afe944b736534f65`。

以下保留固定文本与其变量占位名；运行时只发送实际命中的问题。

- **发现的问题：** 修复草稿需要知道引用或逐字引文的具体错误，而不是只得到一个失败结论。
- **本版本目标：** 分别指出未知引用、没有引用、缺少原文来源和引文不匹配。
- **本版本效果：** 修复调用收到可定位的问题描述；实际修复效果待观察。

原文：

```text
Unknown source reference: {event_id}
Summary contains no original event references
Missing source for quoted text: {event_id}
Quote does not match {event_id}: {quote}
```

## H2 — 补充恢复记录不能承载原句

日期：2026-10-10 15:48:05 +08:00。提交：`f3d12f06f062574e7d8884e1cfe4379e66baae59`。

- **发现的问题：** 原问题模板未覆盖把逐字引文附在 recovered- 派生记录上的情况。
- **本版本目标：** 新增针对派生恢复记录的引文错误提示。
- **本版本效果：** 修复调用能区别派生恢复记录和真正的原句来源；实际表现待观察。

原文：

```text
Unknown source reference: {event_id}
Summary contains no original event references
Quote attached to a derived recovery record: {event_id}
Missing source for quoted text: {event_id}
Quote does not match {event_id}: {quote}
```

## v1 — 当前基线：已停用

日期：2026-10-11（工作区快照日期）。提交：`工作区未提交`。

停用版本：2026-10-11 工作区未提交变更删除这些模板。最后启用文本见 H2。

- **发现的问题：** 当前已删除独立审核和修复流程，这批问题模板也不再送入模型。
- **本版本目标：** 移除 `_source_issues()` 与对应修复输入。
- **本版本效果：** 运行时不再生成这批固定问题反馈，不代表模型已自动避免这些错误。
