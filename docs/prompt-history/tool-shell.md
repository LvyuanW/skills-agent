# shell 工具说明版本史

本文按 Git `--all` 可达提交中的实际说明文本变化整理历史；H1、H2…按提交记录的时间升序排列。未改变说明文本的实现调整不单列版本。v1 是 2026-10-11 建档时的当前工作区基线。

“发现的问题”按相邻版本的差异归纳其要处理的缺口；“本版本效果”描述规则变化或预期，不表示已测得实际改善。

源位置：`skills_agent/tools/__init__.py` 中 `ToolRegistry` 的 `shell` 工具定义。完整提示词以模型接收的工具 schema 表示，保留工具 description 及每个参数 description 的完整字符串。

共享脚本参数的说明在本文件中完整保留，不需要跳转到另一工具文档。

## H1

日期：2026-10-09 19:23:06+08:00\
提交：`23e5359e3fb3d05e33e40651d617a06e6fecd548`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：此前没有主机工作区命令执行的工具说明。
- 本版本目标：说明 shell 的执行环境和命令参数。
- 本版本效果：将工具定义为在主机工作区运行 shell 命令，使用 command 传入内容。

### 完整提示词

```json
{
  "name": "shell",
  "description": "Run a shell command on the host in the workspace.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "Shell command to execute."
      }
    },
    "required": [
      "command"
    ],
    "additionalProperties": false
  }
}
```

## H2

日期：2026-10-09 19:49:51+08:00\
提交：`5fffe1403f3521e846911091e7e00ea9b0e09f6c`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：“执行命令”的描述没有明确支持读取参考源码和编写任务脚本。
- 本版本目标：引导模型把 shell 用作自由脚本执行入口。
- 本版本效果：工具说明增加读取技能、运行多行内联代码及编写新脚本，command 说明同步扩展。

### 完整提示词

```json
{
  "name": "shell",
  "description": "Run arbitrary shell commands on the host in the workspace. Read skill instructions and reference source code, execute multiline inline code, or write and run new scripts for the current task.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "Shell command or multiline script to execute."
      }
    },
    "required": [
      "command"
    ],
    "additionalProperties": false
  }
}
```

## H3

日期：2026-10-09 23:29:51+08:00\
提交：`667653a716921a2cdcb9ce6bfee4b3fd4dafe7aa`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：命令参数没有单独的面向用户的动作说明。
- 本版本目标：为执行动作提供简短的用户可读描述。
- 本版本效果：增加可选 summary 参数，要求使用用户语言，避免代码和详细推理。

### 完整提示词

```json
{
  "name": "shell",
  "description": "Run arbitrary shell commands on the host in the workspace. Read skill instructions and reference source code, execute multiline inline code, or write and run new scripts for the current task.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "Shell command or multiline script to execute."
      },
      "summary": {
        "type": "string",
        "description": "Brief user-facing description of the action, in the user's language; no code or detailed reasoning."
      }
    },
    "required": [
      "command"
    ],
    "additionalProperties": false
  }
}
```

## H4

日期：2026-10-10 04:34:41+08:00\
提交：`08586891a747eea02ff1b647afe944b736534f65`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：既有描述缺少输出裁剪和返回模式指引，summary 也未统一为任务名称。
- 本版本目标：统一具名脚本调用并说明输出选择。
- 本版本效果：改用 name 和 command，加入 result_mode；要求在脚本中筛选大数据并返回相关内容。

### 完整提示词

```json
{
  "name": "shell",
  "description": "Run arbitrary scripts on the host in the workspace. Read skills and reference source, write task-specific code, inspect results. Filter large data in your script and print relevant evidence.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short action and purpose, shown to the user."
      },
      "command": {
        "type": "string",
        "description": "Arbitrary shell command or multiline script."
      },
      "result_mode": {
        "type": "string",
        "enum": [
          "auto",
          "direct",
          "report"
        ],
        "description": "auto: original up to 8192 tokens, otherwise report. direct: exact source (skills/code/data), or a source reference if it cannot fit. report: concise conclusions with evidence. Default auto."
      }
    },
    "required": [
      "name",
      "command"
    ],
    "additionalProperties": false
  }
}
```

## v1 — 当前工作区基线

日期：2026-10-11\
提交：`工作区基线；与 H4 同文（源文件无未提交改动）`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：本次需要为现有工具说明建立独立版本记录。
- 本版本目标：将当前生效说明固定为 v1，并保留 H4 及之前的历史。
- 本版本效果：与 H4 完全相同；本次不改变工具说明或参数规则。

### 完整提示词

```json
{
  "name": "shell",
  "description": "Run arbitrary scripts on the host in the workspace. Read skills and reference source, write task-specific code, inspect results. Filter large data in your script and print relevant evidence.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short action and purpose, shown to the user."
      },
      "command": {
        "type": "string",
        "description": "Arbitrary shell command or multiline script."
      },
      "result_mode": {
        "type": "string",
        "enum": [
          "auto",
          "direct",
          "report"
        ],
        "description": "auto: original up to 8192 tokens, otherwise report. direct: exact source (skills/code/data), or a source reference if it cannot fit. report: concise conclusions with evidence. Default auto."
      }
    },
    "required": [
      "name",
      "command"
    ],
    "additionalProperties": false
  }
}
```

## v2 · 2026-10-17 · 必填执行模式与后台生命周期

来源：工作区未提交；`skills_agent/tools/__init__.py:ToolRegistry`。

- 发现的问题：同步 shell 无法明确表示提前返回与任务生命周期，后台登记依赖模型选择另一脚本入口。
- 本版本目标：要求 run_in_background，并提供 task/service 生命周期与独立执行期限。
- 本版本效果：同步等待入口并清理残留；异步自动登记并返回回执；后台服务不阻挡交付。

### 完整提示词

```json
{
  "name": "shell",
  "description": "Run arbitrary scripts on the host in the workspace. Set required run_in_background=false for synchronous execution or true to return a job ID and continue working while the runtime supervises it. The entry process must wait for internal work; its exit triggers cleanup of remaining processes in the same group, recorded in the result. Read skills and reference source, write task-specific code, inspect results. Filter large data in your script and print relevant evidence.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short action and purpose, shown to the user."
      },
      "command": {
        "type": "string",
        "description": "Arbitrary shell command or multiline script."
      },
      "timeout_seconds": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Execution deadline. Foreground: default 120 seconds, maximum 600. Background task: default 3600 seconds. Service: no default deadline. Background durations may be any positive finite number, independent of the short startup receipt wait. Expiry stops the owned process group."
      },
      "result_mode": {
        "type": "string",
        "enum": [
          "auto",
          "direct",
          "report"
        ],
        "description": "auto: original up to 8192 tokens, otherwise report. direct: exact source (skills/code/data), or a source reference if it cannot fit. report: concise conclusions with evidence. Default auto."
      },
      "run_in_background": {
        "type": "boolean",
        "description": "Required. false: wait for the entry process, clean remaining processes in its group, then return its result. true: return a registered job receipt while the entry process continues; background_lifecycle determines whether it must finish before a final reply. Read the background-tasks skill before using true. Scripts must wait for their own child work and must not daemonize or detach from the process group."
      },
      "background_lifecycle": {
        "type": "string",
        "enum": [
          "task",
          "service"
        ],
        "description": "Background jobs only; default task. task: must finish before the Agent ends its work. service: intentionally stays online after delivery, does not block a final reply, and survives a successful CLI exit; failed or cancelled turns clean up new services. Verify service readiness before delivery. Persistent services are forbidden in benchmark runs. service requires run_in_background=true."
      }
    },
    "required": [
      "name",
      "command",
      "run_in_background"
    ],
    "additionalProperties": false
  }
}
```

## v3 · 2026-10-17 · 启动确认放宽至10秒

来源：工作区未提交；`skills_agent/tools/__init__.py:ToolRegistry`。

- 发现的问题：后台启动确认原为5秒，繁忙环境需要更宽的启动窗口。
- 本版本目标：明确独立的10秒启动确认期限。
- 本版本效果：启动确认统一为10秒，任务执行期限不变；启动结果仍不等于任务完成。

### 完整提示词

```json
{
  "name": "shell",
  "description": "Run arbitrary scripts on the host in the workspace. Set required run_in_background=false for synchronous execution or true to return a job ID and continue working while the runtime supervises it. The entry process must wait for internal work; its exit triggers cleanup of remaining processes in the same group, recorded in the result. Read skills and reference source, write task-specific code, inspect results. Filter large data in your script and print relevant evidence.",
  "parameters": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "Short action and purpose, shown to the user."
      },
      "command": {
        "type": "string",
        "description": "Arbitrary shell command or multiline script."
      },
      "timeout_seconds": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Execution deadline. Foreground: default 120 seconds, maximum 600. Background task: default 3600 seconds. Service: no default deadline. Background durations may be any positive finite number, independent of the 10-second startup confirmation wait. Expiry stops the owned process group."
      },
      "result_mode": {
        "type": "string",
        "enum": [
          "auto",
          "direct",
          "report"
        ],
        "description": "auto: original up to 8192 tokens, otherwise report. direct: exact source (skills/code/data), or a source reference if it cannot fit. report: concise conclusions with evidence. Default auto."
      },
      "run_in_background": {
        "type": "boolean",
        "description": "Required. false: wait for the entry process, clean remaining processes in its group, then return its result. true: return a registered job receipt while the entry process continues; background_lifecycle determines whether it must finish before a final reply. Read the background-tasks skill before using true. Scripts must wait for their own child work and must not daemonize or detach from the process group."
      },
      "background_lifecycle": {
        "type": "string",
        "enum": [
          "task",
          "service"
        ],
        "description": "Background jobs only; default task. task: must finish before the Agent ends its work. service: intentionally stays online after delivery, does not block a final reply, and survives a successful CLI exit; failed or cancelled turns clean up new services. Verify service readiness before delivery. Persistent services are forbidden in benchmark runs. service requires run_in_background=true."
      }
    },
    "required": [
      "name",
      "command",
      "run_in_background"
    ],
    "additionalProperties": false
  }
}
```
