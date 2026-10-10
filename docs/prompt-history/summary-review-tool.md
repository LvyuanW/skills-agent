# 摘要审核历史读取工具提示词历史

位置：`skills_agent/context/auxiliary.py`。历史独立工具定义为 `_READ_TOOL` 的 `read_history`。下面保留完整模型可见定义；当前工作区已删除。

按 `git log --all --follow` 的时间线记录实际文本变化。H 编号是历史版本；v1 是当前工作区基线，相同文本或停用状态不会据此虚增一个历史版本。以下效果描述规则与调用流程的变化，未记录实测结论。

## H1 — 首次提供受限历史读取工具

日期：2026-10-10 04:34:41 +08:00。提交：`08586891a747eea02ff1b647afe944b736534f65`。

- **发现的问题：** 审核者遇到冲突或精确原句时，需要读取对应的原始事件，不能仅凭摘要猜测。
- **本版本目标：** 描述 read_history 的用途，并用 event_id 与可选行号界定读取目标。
- **本版本效果：** 旧审核调用可按引用读取事件局部内容；实际调用选择是否恰当待观察。

原文：

```json
[
  {
    "name": "read_history",
    "description": "Read a cited original event to verify a conflict or exact quote.",
    "parameters": {
      "type": "object",
      "properties": {
        "event_id": {
          "type": "string"
        },
        "start_line": {
          "type": "integer",
          "minimum": 1
        },
        "end_line": {
          "type": "integer",
          "minimum": 1
        }
      },
      "required": [
        "event_id"
      ],
      "additionalProperties": false
    }
  }
]
```

## v1 — 当前基线：已停用

日期：2026-10-11（工作区快照日期）。提交：`工作区未提交`。

停用版本：2026-10-11 工作区未提交变更删除该定义。完整历史中的工具说明与参数 schema 始终与 H1 相同。

- **发现的问题：** 当前已移除独立摘要审核调用，不再需要向该调用提供历史读取工具。
- **本版本目标：** 删除辅助审核专用的 `_READ_TOOL` 定义。
- **本版本效果：** 压缩辅助调用不再获得此工具；项目其他历史检索能力不由这份定义负责。
