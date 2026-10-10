# subagent 工具说明版本史

本文按 Git `--all` 可达提交中的实际说明文本变化整理历史；H1、H2…按提交记录的时间升序排列。未改变说明文本的实现调整不单列版本。v1 是 2026-10-11 建档时的当前工作区基线。

“发现的问题”按相邻版本的差异归纳其要处理的缺口；“本版本效果”描述规则变化或预期，不表示已测得实际改善。

源位置：`skills_agent/tools/__init__.py` 中 `ToolRegistry` 的 `subagent` 工具定义。完整提示词以模型接收的工具 schema 表示，保留工具 description 及每个参数 description 的完整字符串。

## H1

日期：2026-10-09 19:23:06+08:00\
提交：`23e5359e3fb3d05e33e40651d617a06e6fecd548`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：此前没有独立任务委派工具的说明。
- 本版本目标：定义子任务目标、上下文及最终结果的返回方式。
- 本版本效果：通过 goal 和 task 委派任务，task 要求自包含的指令和相关背景。

### 完整提示词

```json
{
  "name": "subagent",
  "description": "Delegate a task and return its final result.",
  "parameters": {
    "type": "object",
    "properties": {
      "goal": {
        "type": "string",
        "description": "Short summary of the delegated goal, shown to the user."
      },
      "task": {
        "type": "string",
        "description": "Self-contained instructions and relevant context for the subagent."
      }
    },
    "required": [
      "goal",
      "task"
    ],
    "additionalProperties": false
  }
}
```

## H2

日期：2026-10-10 04:34:41+08:00\
提交：`08586891a747eea02ff1b647afe944b736534f65`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：普通委派接口没有专门的历史检索模式。
- 本版本目标：增加具名历史检索任务和只读背景传递方式。
- 本版本效果：goal 改为 name，新增 history 参数，并说明仅最终报告返回。

### 完整提示词

```json
{
  "name": "subagent",
  "description": "Delegate a named task; only its final report returns. For history retrieval, set history=true to include the complete current working context as read-only background and use the history skill.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short task name, shown to the user."
      },
      "task": {
        "type": "string",
        "description": "Goal, input, boundaries and required evidence."
      },
      "history": {
        "type": "boolean",
        "description": "Retrieve details from this session's original history. Default false."
      }
    },
    "required": [
      "name",
      "task"
    ],
    "additionalProperties": false
  }
}
```

## H3

日期：2026-10-10 06:20:46+08:00\
提交：`b82571dd623e04c65583d5ce894d950f02071732`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：历史模式说明依赖“使用 history 技能”，未描述检索工作的具体职责。
- 本版本目标：直接说明历史检索代理需要完成的任务。
- 本版本效果：要求检索原始内容、检查相关性和后续修正后返回报告。

### 完整提示词

```json
{
  "name": "subagent",
  "description": "Delegate a named task; only its final report returns. For conversation-history retrieval, use history=true. The child receives the complete working context as read-only background, searches original evidence, checks relevance and later corrections, and returns a report.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short task name, shown to the user."
      },
      "task": {
        "type": "string",
        "description": "Goal, input, boundaries and required evidence."
      },
      "history": {
        "type": "boolean",
        "description": "Retrieve details from this session's original history. Default false."
      }
    },
    "required": [
      "name",
      "task"
    ],
    "additionalProperties": false
  }
}
```

## H4

日期：2026-10-10 06:45:14+08:00\
提交：`b3d07c7b928248606c63b2938de7732c8fd4ff2f`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：普通子代理是否继承父会话及委派输入应包含哪些内容仍不明确。
- 本版本目标：明确上下文继承边界和自包含任务输入。
- 本版本效果：普通子代理不继承父会话；task 需包含约束、材料、操作边界与预期交付物。

### 完整提示词

```json
{
  "name": "subagent",
  "description": "Delegate a named task; only its final report returns. Ordinary subagents do not inherit the parent conversation; provide the necessary background in task. For conversation-history retrieval, use history=true. Only this mode supplies the complete current working context as read-only background. The child searches original evidence, checks relevance and later corrections, and returns a report.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short task name, shown to the user."
      },
      "task": {
        "type": "string",
        "description": "Self-contained goal, relevant constraints, necessary materials (content or accessible paths), operation boundaries (what may be read or changed), and expected deliverable with required evidence."
      },
      "history": {
        "type": "boolean",
        "description": "Retrieve original session history with the complete current working context as read-only background. Default false: no parent conversation is inherited."
      }
    },
    "required": [
      "name",
      "task"
    ],
    "additionalProperties": false
  }
}
```

## H5

日期：2026-10-10 06:47:47+08:00\
提交：`ac570dc9defe9b442ac0180bd62382542508630d`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：接口说明缺少何时委派、何时直接执行的选择标准。
- 本版本目标：把委派用于可独立完成且边界清晰的子任务。
- 本版本效果：新增调查、日志分析、代码探索和独立审查场景；简单查找直接执行，紧耦合决策留在主代理。

### 完整提示词

```json
{
  "name": "subagent",
  "description": "Delegate a named task; only its final report returns. Prefer delegation for a bounded task that can be completed from explicit inputs and handed back as findings with concrete evidence, such as multi-step investigation, log analysis, codebase exploration or independent review. Use direct tools for simple targeted lookups; keep tightly coupled steps that need frequent decisions or new information from the parent conversation in the main agent. Ordinary subagents do not inherit the parent conversation; provide the necessary background in task. For conversation-history retrieval, use history=true. Only this mode supplies the complete current working context as read-only background. The child searches original evidence, checks relevance and later corrections, and returns a report.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short task name, shown to the user."
      },
      "task": {
        "type": "string",
        "description": "Self-contained goal, relevant constraints, necessary materials (content or accessible paths), operation boundaries (what may be read or changed), and expected deliverable with required evidence."
      },
      "history": {
        "type": "boolean",
        "description": "Retrieve original session history with the complete current working context as read-only background. Default false: no parent conversation is inherited."
      }
    },
    "required": [
      "name",
      "task"
    ],
    "additionalProperties": false
  }
}
```

## v1 — 当前工作区基线

日期：2026-10-11\
提交：`工作区基线；与 H5 同文（源文件无未提交改动）`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：本次需要为现有工具说明建立独立版本记录。
- 本版本目标：将当前生效说明固定为 v1，并保留 H5 及之前的历史。
- 本版本效果：与 H5 完全相同；本次不改变工具说明或参数规则。

### 完整提示词

```json
{
  "name": "subagent",
  "description": "Delegate a named task; only its final report returns. Prefer delegation for a bounded task that can be completed from explicit inputs and handed back as findings with concrete evidence, such as multi-step investigation, log analysis, codebase exploration or independent review. Use direct tools for simple targeted lookups; keep tightly coupled steps that need frequent decisions or new information from the parent conversation in the main agent. Ordinary subagents do not inherit the parent conversation; provide the necessary background in task. For conversation-history retrieval, use history=true. Only this mode supplies the complete current working context as read-only background. The child searches original evidence, checks relevance and later corrections, and returns a report.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short task name, shown to the user."
      },
      "task": {
        "type": "string",
        "description": "Self-contained goal, relevant constraints, necessary materials (content or accessible paths), operation boundaries (what may be read or changed), and expected deliverable with required evidence."
      },
      "history": {
        "type": "boolean",
        "description": "Retrieve original session history with the complete current working context as read-only background. Default false: no parent conversation is inherited."
      }
    },
    "required": [
      "name",
      "task"
    ],
    "additionalProperties": false
  }
}
```
