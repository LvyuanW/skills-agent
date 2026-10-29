## 结论

本仓库没有引入任何第三方日志框架（未使用 `logging`、`loguru`、`structlog` 等），也没有集中式 logger 初始化。程序通过两种机制产出“日志”：
1. **交互式终端输出**：`skills_agent/cli.py` 中的 `_print()` / `_assistant_content()` / `_tool_note()` 等函数把 Agent 对话、工具调用、错误等消息以带颜色的文本写入 `sys.stdout`（或自定义 `output` 对象）。
2. **命令执行产物目录**：子进程/沙箱运行结果按时间戳写入 `<log_dir>/command-<uuid>/` 下的 stdout/stderr 等文件，由 `skills_agent/execution.py` 和 `adapters/harbor/assets/codex/offline-subagent-smoke.py` 等处的 `log_dir` 参数驱动。

## 关键文件与职责

- `skills_agent/cli.py`：唯一面向用户的 CLI 入口；所有“可见输出”均经 `_print(text, role, output)` 处理，role 取值为 `user` / `assistant` / `thinking` / `tool` / `error`，TTY 下用 ANSI 颜色 + rich `Text` 包装，非 TTY 下原样打印。
- `skills_agent/execution.py`：核心子进程执行器，接受 `log_dir: Path | None` 参数。当 `log_dir` 非空时创建临时目录 `Path(tempfile.mkdtemp(prefix="command-", dir=log_dir))` 并写入该命令的 stdout/stderr 等；`timeout_seconds=DEFAULT_TIMEOUT_SECONDS, log_dir=None, cleanup=None` 是默认签名。
- `skills_agent/sandbox/__init__.py`：DockerSandbox 透传 `log_dir` 给底层执行器，并提供 `_remove_container(name, log_dir)` 在异常路径上清理容器及对应日志目录。
- `skills_agent/tools/__init__.py`：`ToolRegistry.__init__` 接收 `log_dir=None`，并在调用 sandbox.run() 时透传 `self.log_dir`，使每个工具执行都有独立子目录。
- `skills_agent/builtin_skills/feedback/scripts/feedback.py`：技能脚本也遵循同一模式——根据 `args.log_dir` 创建 run 目录 `Path(tempfile.mkdtemp(prefix="run-", dir=log_dir))`。
- `adapters/harbor/assets/common/audit_proxy.py`：提供 `log_message(self, format, *args)` 钩子供外部测试替换，属于测试替身约定而非真实日志系统。
- `adapters/harbor/assets/codex/offline-subagent-smoke.py`：同样实现 `log_message(self, *args)` 作为 mock。

## 架构与约定

- **无全局日志级别控制**：CLI 不提供 `--log-level` / `--debug` 参数；`runtime.add_agent_arguments()` 只暴露 provider、model、sandbox、skill 等运行时选项。
- **结构化字段通过 JSON 事件传递**：Agent 内部通过 `on_event` 回调和 `message` dict（含 `type` / `content` / `background_job_id` / `status` / `exit_code` 等键）在模块间传播结构化数据，而不是写结构化日志行。
- **可观测性依赖外部 sink**：终端渲染层支持两类 sink：
  - 标准 `sys.stdout`（人类可读）。
  - 实现 `append_line` / `append_body` / `upsert_tool` / `complete_background_job` / `append_image_link` 等方法的对象（见大量测试如 `test_stop_terminal.py`、`test_cli_terminal.py`、`test_stream_integration.py`、`tests/background_jobs_fixture.py` 中 `def log_message(self, *args): pass` 的替身），用于自动化消费。
- **持久化日志位置**：`log_dir` 始终由上层（`tools.ToolRegistry`、`feedback` 脚本、测试 fixture）传入；执行器自身不决定根目录，仅负责创建 `command-<uuid>` 或 `run-<uuid>` 子目录。

## 观察到的约束（基于代码事实）

- 子进程/沙箱命令的输出不会直接写到终端，而是先落盘到 `log_dir/command-<uuid>/` 子目录（`execution.py:316-319`）。
- 若 `log_dir` 为 `None`，则不创建任何持久化日志目录（`execution.py` 仅在 `if log_dir is not None:` 分支内创建目录）。
- CLI 对错误的统一展示路径是 `_print(f"error: {event['message']}", "assistant", output)`（`cli.py`），role 固定为 `assistant`，并非通过日志框架分级。
- 测试中对“日志”的断言通常通过覆盖 `log_message` 方法或使用 `assertLogs` 之外的纯行为断言完成，说明本项目不把 Python `logging` 模块视为 API 边界。

简言之：这是一个**零日志框架**的实现——用户可见输出走 CLI 终端渲染，诊断输出走文件系统上的 `command-<uuid>/` 目录，跨组件通信走结构化 `message` 字典，不存在统一的日志级别、格式化器或 sink 注册机制。