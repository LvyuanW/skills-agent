# read_skill 工具说明版本史（已停用）

本文按 Git `--all` 可达提交中的实际说明文本变化整理历史；H1、H2…按提交记录的时间升序排列。未改变说明文本的实现调整不单列版本。v1 是 2026-10-11 建档时的当前工作区基线。

“发现的问题”按相邻版本的差异归纳其要处理的缺口；“本版本效果”描述规则变化或预期，不表示已测得实际改善。

历史源位置：`skills_agent/tools/__init__.py` 中 `ToolRegistry` 的 `read_skill` 工具定义。该工具已于 2026-10-09 移除；当前工作区不存在生效说明。

## H1

日期：2026-10-09 19:23:06+08:00\
提交：`23e5359e3fb3d05e33e40651d617a06e6fecd548`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：初版需要提供从技能目录读取完整指令的入口。
- 本版本目标：定义以技能名称读取完整技能指令的专用工具。
- 本版本效果：工具说明要求通过 name 提供目录中的技能名称，并读取完整说明。

### 完整提示词

```json
{
  "name": "read_skill",
  "description": "Read the complete instructions for a skill.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Skill name from the catalog."
      }
    },
    "required": [
      "name"
    ],
    "additionalProperties": false
  }
}
```

## H2 — 移除

日期：2026-10-09 19:49:51+08:00\
提交：`5fffe1403f3521e846911091e7e00ea9b0e09f6c`\
历史源位置：`skills_agent/tools/__init__.py`

- 发现的问题：专门的技能读取入口使技能更像固定接口，未统一到普通文件与参考源码的读取方式。
- 本版本目标：移除 read_skill，改由 shell 读取技能文件及参考代码。
- 本版本效果：工具目录不再提供 read_skill；其完整旧说明保留在 H1。

本版本没有生效的 read_skill 提示词。

## v1 — 当前工作区基线（已停用）

日期：2026-10-11\
状态：工作区基线；延续 H2 的移除状态。

- 发现的问题：建档需要覆盖已经移除的历史提示词。
- 本版本目标：保留旧说明和移除时间，避免把停用工具误记为当前接口。
- 本版本效果：当前仍不提供 read_skill；本次没有恢复工具或新增生效提示词。

当前无生效提示词，最后一版完整原文见 H1。
