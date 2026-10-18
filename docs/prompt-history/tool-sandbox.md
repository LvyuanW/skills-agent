# sandbox 工具说明版本史

本文按 Git `--all` 可达提交中的实际说明文本变化整理历史；H1、H2…按提交记录的时间升序排列。未改变说明文本的实现调整不单列版本。v1 是 2026-10-11 建档时的当前工作区基线。

“发现的问题”按相邻版本的差异归纳其要处理的缺口；“本版本效果”描述规则变化或预期，不表示已测得实际改善。

源位置：`skills_agent/tools/__init__.py` 中 `ToolRegistry` 的 `sandbox` 工具定义。完整提示词以模型接收的工具 schema 表示，保留工具 description 及每个参数 description 的完整字符串。

共享脚本参数的说明在本文件中完整保留，不需要跳转到另一工具文档。

## H1

日期：2026-10-09 19:23:06+08:00\
提交：`23e5359e3fb3d05e33e40651d617a06e6fecd548`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：此前没有配置容器内执行命令的工具说明。
- 本版本目标：标明 sandbox 的执行环境与命令输入。
- 本版本效果：将工具定义为在已配置 Docker 沙箱内运行命令，使用 command 传入内容。

### 完整提示词

```json
{
  "name": "sandbox",
  "description": "Run a command inside the configured Docker sandbox.",
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

日期：2026-10-09 23:29:51+08:00\
提交：`667653a716921a2cdcb9ce6bfee4b3fd4dafe7aa`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：沙箱执行同样缺少单独面向用户的动作说明。
- 本版本目标：使沙箱动作具备简短的用户可读描述。
- 本版本效果：增加与 shell 相同的可选 summary 参数。

### 完整提示词

```json
{
  "name": "sandbox",
  "description": "Run a command inside the configured Docker sandbox.",
  "parameters": {
    "type": "object",
    "properties": {
      "command": {
        "type": "string",
        "description": "Shell command to execute."
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

## H3

日期：2026-10-10 04:34:41+08:00\
提交：`08586891a747eea02ff1b647afe944b736534f65`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：沙箱参数与新的具名脚本及输出模式规则尚未统一。
- 本版本目标：与 shell 共享 name、command、result_mode 的说明。
- 本版本效果：允许任意脚本并提供三种返回模式；本文件完整保留共享参数说明。

### 完整提示词

```json
{
  "name": "sandbox",
  "description": "Run arbitrary scripts inside the configured Docker sandbox.",
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
提交：`工作区基线；与 H3 同文（源文件无未提交改动）`\
源位置：`skills_agent/tools/__init__.py`

- 发现的问题：本次需要为现有工具说明建立独立版本记录。
- 本版本目标：将当前生效说明固定为 v1，并保留 H3 及之前的历史。
- 本版本效果：与 H3 完全相同；本次不改变工具说明或参数规则。

### 完整提示词

```json
{
  "name": "sandbox",
  "description": "Run arbitrary scripts inside the configured Docker sandbox.",
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


## v2 · 2026-10-18 · 结果模式区分已知与未知长度

来源：当前工作区（未提交）；`skills_agent/tools/__init__.py:ToolRegistry`。

- 发现的问题：sandbox 与 shell 共用 result_mode 描述，原说明未覆盖 usage 模式下没有新结果长度的情况。
- 本版本目标：与 shell 同步明确未知长度保留原文以及 direct 预先计数的适用条件，不引入本地估算。
- 本版本效果：提示词与 reported-only 运行时保持一致：压缩仅依据网关主模型返回用量，新增内容不做本地计数或估算；89 项相关回归测试通过，实际模型遵循情况待观察。

### 完整原文

````json
{
  "name": "sandbox",
  "description": "Run arbitrary scripts inside the configured Docker sandbox.",
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
        "maximum": 600,
        "description": "Wall-clock limit; default 120 seconds, maximum 600. Timeout stops this command. Use managed background processes for longer work."
      },
      "result_mode": {
        "type": "string",
        "enum": [
          "auto",
          "direct",
          "report"
        ],
        "description": "auto: original up to 8192 tokens, otherwise report when token counts are available; preserve the original when length is unknown. direct: exact source (skills/code/data), or a source reference if advance counting shows it cannot fit. report: concise conclusions with evidence. Default auto."
      }
    },
    "required": [
      "name",
      "command"
    ],
    "additionalProperties": false
  }
}
````
