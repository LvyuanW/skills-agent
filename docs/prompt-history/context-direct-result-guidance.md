# direct 原始结果过大时的读取提示

位置：`skills_agent/context/__init__.py · WorkingContext.prepare() · notice`。状态：启用。盘点日期：2026-10-11（Asia/Shanghai）。

H 编号记录 Git 全部分支中的实际文本变化；v1 是本次工作区基线标签，不表示新增实现。这里的“效果”说明规则变化或预期作用，不代表实测结果。

这是程序写入工具结果、随后由模型读到的短指令，不是 UI 提示，也不是单独的模型调用。`source` 提供原始结果位置，内容在运行时填入。

## H1 — 保留原始位置并要求缩小读取范围

- 时间：2026-10-10 04:34:41 +08:00。
- 标识：提交 `08586891a747eea02ff1b647afe944b736534f65`。
- 发现的问题：`result_mode=direct` 的完整原文可能无法装入活动上下文。
- 本版本目标：用带原始位置的提示替代当前过大的结果，让模型继续按更小范围读取。
- 本版本效果：新增 `Read a narrower range.` 行动指引；提示保留原始结果的位置，不把该结果改写为摘要。

原文：

```python
record["message"]["content"] = json.dumps({
    "notice": "Requested original result does not fit the active context. Read a narrower range.",
    "source": record["source"],
}, ensure_ascii=False)
```

## v1 — 当前工作区基线

- 时间：2026-10-11。
- 标识：工作区快照；此片段与提交 `08586891a747eea02ff1b647afe944b736534f65` 相同，无新增未提交措辞。
- 发现的问题：本次盘点需要包括工具结果内部的模型指令。
- 本版本目标：记录当前超大 direct 结果的后续读取规则。
- 本版本效果：沿用 H1 原文；没有新的提示词版本。

原文：

```python
record["message"]["content"] = json.dumps({
    "notice": "Requested original result does not fit the active context. Read a narrower range.",
    "source": record["source"],
}, ensure_ascii=False)
```
