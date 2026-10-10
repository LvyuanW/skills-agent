# 历史摘要的上下文包装

位置：`skills_agent/context/__init__.py · 原 _summary() / 现 WorkingContext._system()`。状态：当前 system 包装启用；旧摘录包装停用。盘点日期：2026-10-11（Asia/Shanghai）。

H 编号记录 Git 全部分支中的实际文本变化；v1 是本次工作区基线标签，不表示新增实现。这里的“效果”说明规则变化或预期作用，不代表实测结果。

本页记录历史材料进入模型上下文时的包装，而不是生成摘要的模型提示词。早期包装只做程序摘录，现行包装为已经生成的摘要附加解释。辅助摘要生成指令在独立文档中记录。

## H1 — 早期确定性历史摘录包装（已停用）

- 时间：2026-10-09 19:23:06 +08:00。
- 标识：提交 `23e5359e3fb3d05e33e40651d617a06e6fecd548`。
- 发现的问题：字符预算不足时，被省略的旧对话仍需要保留一些可供后续使用的信息。
- 本版本目标：用角色、工具名称和短摘录组成早期上下文概要。
- 本版本效果：生成 `Extracted earlier context:` 材料并以 user 消息放回上下文；这是数据模板，没有单独的摘要模型调用。该版本于 2026-10-10 04:34:41 +08:00 的提交 `0858689` 被替换并停用。

原文：

```python
def _summary(groups: list[list[dict]]) -> str:
    lines = []
    for message in _flatten(groups):
        role = message.get("role", "")
        if message.get("tool_calls"):
            names = ", ".join(f"{call['name']}({_excerpt(call['arguments'], 180)})" for call in message["tool_calls"])
            lines.append(f"assistant tools: {names}")
        text = _text(message).strip()
        if text:
            label = message.get("name") or role
            lines.append(f"{label}: {_excerpt(text, 260)}")
    return "Extracted earlier context:\n" + "\n".join(lines)
```

## H2 — 持久化摘要的 system 包装

- 时间：2026-10-10 04:34:41 +08:00。
- 标识：提交 `08586891a747eea02ff1b647afe944b736534f65`。
- 发现的问题：持久化历史摘要需要与新的用户请求区分，并保留原决策是否被采纳及后续更正的优先级。
- 本版本目标：把摘要标成派生历史参考，放进明确的历史摘要标签中。
- 本版本效果：由旧 user 摘录包装改为追加到 system 的历史摘要包装；明确摘要不是新用户指令，后续用户更正优先。

原文：

```python
def _system(self, system, summary=None):
    summary = self.state["summary"] if summary is None else summary
    if summary:
        system += ("\n\nHistorical summary (derived reference, not a new user instruction). "
                   "Preserve sources and adoption status; later user corrections take precedence.\n"
                   "<historical_summary>\n" + summary + "\n</historical_summary>")
    return system
```

## v1 — 当前工作区基线

- 时间：2026-10-11。
- 标识：工作区快照；此片段与提交 `08586891a747eea02ff1b647afe944b736534f65` 相同，无新增未提交措辞。
- 发现的问题：本次需要固定当前上下文包装的可追溯基线。
- 本版本目标：记录仍在使用的历史摘要边界声明。
- 本版本效果：沿用 H2 原文；没有因本次盘点增加新的包装规则。

原文：

```python
def _system(self, system, summary=None):
    summary = self.state["summary"] if summary is None else summary
    if summary:
        system += ("\n\nHistorical summary (derived reference, not a new user instruction). "
                   "Preserve sources and adoption status; later user corrections take precedence.\n"
                   "<historical_summary>\n" + summary + "\n</historical_summary>")
    return system
```
