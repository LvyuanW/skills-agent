## 1. 采用的方案

代码库围绕 `skills_agent/providers/base.py` 中的 **`ProviderError`** 层次结构组织所有 LLM Provider 调用异常，并通过 `skills_agent/providers/retry.py` 的 `retry_call` 实现统一的、可配置的自动重试。其他子系统（工具执行、进程管理、沙箱、配置解析等）直接使用 Python 内置异常（`ValueError`、`RuntimeError`、`FileNotFoundError`、`PermissionError`），没有自定义业务异常类。

- Provider 层：自定义异常层次 + HTTP/结构化错误码 → 类别映射 → 可重试判定。
- 上层（agent、tools、processes、config 等）：直接 raise 内置异常，由调用方决定展示或转换。
- 无全局中间件；重试通过显式装饰/包装 `retry_call(operation, ...)` 完成。

## 2. 关键文件与包

| 文件 | 作用 |
|---|---|
| `skills_agent/providers/base.py` | `ProviderError` 基类、`ProviderConnectionError`、`ProviderOutputLimitError`、`ProviderImageError`，HTTP/结构化错误码→category 映射表 `_ERROR_CATEGORIES`、`_RETRYABLE_CATEGORIES`、`_FINISH_CATEGORIES`，以及 `post_json` / `post_stream` / `stream_error` / `stream_ended` / `received_text` / `validate_calls` 等辅助函数 |
| `skills_agent/providers/__init__.py` | 统一导出 `ProviderError`、`ProviderConnectionError`、`ProviderOutputLimitError` |
| `skills_agent/providers/retry.py` | `retry_call`、`retry_event`、默认 `RETRY_DELAY_SECONDS=5`，支持环境变量 `SKILLS_AGENT_PROVIDER_MAX_ATTEMPTS`、`SKILLS_AGENT_PROVIDER_RETRY_BUDGET_SECONDS` |
| `skills_agent/providers/anthropic.py` | Anthropic Messages API 专用流处理，使用 `stream_error` / `stream_ended` |
| `skills_agent/providers/openai.py` | OpenAI Chat Completions 流处理 |
| `skills_agent/providers/responses.py` | OpenAI Responses 协议专用流处理，含 `ResponsesMixin` |
| `skills_agent/providers/diagnostics.py` | `RequestDiagnostics`、`DiagnosticStream`、`Redactor`，对敏感头/body 做脱敏 |
| `adapters/harbor/assets/codex/probe-responses.py` | 将 provider 侧 `provider_error_code` 暴露给外部评测框架 |
| 各 `test_provider_*` 测试 | 覆盖错误分类、重试预算、诊断字段、流截断等场景 |

## 3. 架构与约定

### 3.1 异常层次

```text
ProviderError(RuntimeError)
├── ProviderConnectionError   # 传输失败或流提前结束
├── ProviderOutputLimitError  # 输出/上下文长度限制导致终止
└── ProviderImageError        # 本地图片无法包含到请求中 (同时继承 ImageRequestError)
```

每个 `ProviderError` 携带：
- `raw`：原始响应事件（被保存后在重试路径上保留）
- `partial_content`：流中断前已收到的文本块，用于 UI 显示
- `diagnostics`：带脱敏的 `RequestDiagnostics` 对象
- `finish_reason`：上游 finish reason
- `http_status`、`provider_code`、`provider_type`：从 diagnostics/response 派生的只读属性

### 3.2 错误分类（category）决策链

`ProviderError.category` 按以下优先级计算：
1. HTTP status ≥ 400 → `_HTTP_CATEGORIES`（400=`invalid_request`, 401=`authentication`, 429=`rate_limited`, 502=`bad_gateway`, 503=`service_unavailable`, 504/524=`gateway_timeout` 等）
2. `isinstance(self, ProviderConnectionError)` → `"connection"`
3. `isinstance(self, ProviderOutputLimitError)` → `"output_limit"`
4. `finish_reason` ∈ `_FINISH_CATEGORIES`（length/max_tokens/max_output_tokens/model_context_window_exceeded/content_filter/refusal/abort/cancelled 等）
5. 结构化 error.code/type → `_ERROR_CATEGORIES`（如 `invalid_request_error`、`rate_limit_exceeded`、`context_length_exceeded`、`content_filter`、`upstream_stream_read_error` 等）
6. `diagnostics.kind == "protocol"` → `"protocol"`
7. 兜底 → `"provider_error"`

可重试性：`category ∈ _RETRYABLE_CATEGORIES = {connection, rate_limited, server_error, bad_gateway, service_unavailable, gateway_timeout}`。

### 3.3 重试策略

`retry_call(operation, *, on_retry=None, max_attempts=None, max_elapsed_seconds=None)`：
- 捕获 `ProviderError`，仅当 `exc.retryable` 为真时重试。
- 延迟取 `max(RETRY_DELAY_SECONDS, exc.retry_after_seconds or 0)`，其中 `retry_after_seconds` 解析 `Retry-After` 响应头（数值或 RFC-1123 日期）。
- 支持两种预算上限：`max_attempts` 与 `max_elapsed_seconds`，任一耗尽即停止并记录 `reason ∈ {attempt_limit, elapsed_limit, retry_after_exceeds_budget}`。
- 每次重试把元数据写入 `exc.diagnostics["retry"]`（attempts、elapsed_seconds、next_delay_seconds 等），不会替换原 `diagnostics`。
- **明确不捕获 `KeyboardInterrupt` / `SystemExit`**，保证取消和 benchmark SIGTERM 直接传播。
- 通过 `on_retry` 回调暴露 `retry_event(exc, attempt, operation)` 构造的事件，供上层 UI/日志消费。

### 3.4 流式错误与部分结果

`post_stream` / `_read_stream` 在以下情况抛出异常：
- JSON 解析失败 → `ProviderError("API returned invalid stream JSON", diagnostics=protocol_error(...))`
- 非 dict 事件 → `ProviderError("API stream event must be a JSON object")`
- SSE 中出现 `error` 或 `type ∈ {"error", "response.failed", "response.incomplete"}` → 经 `diagnostic.stream_error` 脱敏后返回 `ProviderError`（具体类型由调用方用 `stream_error(event, ..., error_type=...)` 指定）
- 流提前结束 → `stream_ended(stream, message)` 返回 `ProviderConnectionError`

`responses.py` 在捕获 `ProviderError` / `KeyboardInterrupt` 后把 `raw` 和 `partial_content` 回填到异常上，并对 `ProviderOutputLimitError` 且无文本的部分内容尝试从 raw output 恢复 terminal text。

### 3.5 诊断与脱敏

`RequestDiagnostics` 记录 URL、headers、request_bytes、phase、response_bytes、stream_frames、elapsed_seconds、timeout_seconds；`DiagnosticStream` 作为上下文管理器包裹迭代器；`Redactor` 对 Authorization、Bearer 等敏感 header 脱敏。所有 `ProviderError` 的 `diagnostics` 都经过此管线，避免原始请求体泄露到日志或重试事件。

### 3.6 非 Provider 层的错误

其他模块（`skills_agent/config.py`、`skills_agent/session/__init__.py`、`skills_agent/skills/__init__.py`、`adapters/ale/skills_agent/deployer.py`、`adapters/harbor/tb4_control/environment.py`、`skills_agent/agent.py`、`skills_agent/clipboard.py` 等）直接使用 Python 内置异常，通常是 `ValueError`（参数校验）、`RuntimeError`（运行时不变量违反）。这些不是通过分类器处理的业务错误，而是调用方的契约违规。

## 4. 约定与约束

- **Provider 异常必须继承 `ProviderError`**：`openai.py`、`anthropic.py`、`responses.py` 中所有 Provider 错误均通过 `ProviderError`、`ProviderConnectionError`、`ProviderOutputLimitError` 或 `stream_error(..., error_type=...)` 抛出，未观察到直接 raise `Exception` 的情况。
- **不可变错误分类**：`_ERROR_CATEGORIES`、`_RETRYABLE_CATEGORIES`、`_HTTP_CATEGORIES`、`_FINISH_CATEGORIES` 均为模块级 `frozenset`/`dict`，不允许运行时修改，确保分类稳定。
- **结构化错误码优先于人类可读消息**：`base.py` 注释明确指出「Human-readable messages are not a contract and must never turn an otherwise permanent failure into a retry」，分类仅基于 code/type/status。
- **`retry_after_seconds` 只解析数值或标准日期**：非法值（包括布尔、非有限浮点）直接返回 `None`，回退到 `RETRY_DELAY_SECONDS=5`。
- **`ProviderError.provider_code` / `provider_type` 严格类型检查**：仅接受 `str` 或 `int`（code），拒绝其他类型，防止脏数据污染重试元数据。
- **ProviderError 的 provider_error_fields 最多保留 2048 字符**：`__init__` 中对字符串字段截断，避免诊断体过大。
- **重试预算校验**：`retry_call` 对 `max_attempts`（正整数）和 `max_elapsed_seconds`（正有限数）进行 `raise ValueError` 校验，并在传入来源为环境变量时同样应用。
- **取消语义**：重试循环刻意不捕获 `KeyboardInterrupt` / `SystemExit`，保证 Ctrl-C 与 benchmark 终止立即生效。
- **`stream_error` 会清空原始 event**：先复制脱敏后的 diagnostics，再 `event.clear()` + 更新，避免下游误用未脱敏的原始事件。
- **`validate_calls` 强制 tool call 参数为 string**：`responses.py` 中 `arguments` 非 string 时直接 `raise ProviderError('Responses tool arguments must be a string')`。
- **非 Provider 层不使用自定义异常**：配置、会话、技能加载、进程管理等处统一使用 `ValueError` / `RuntimeError`，保持分层边界清晰。