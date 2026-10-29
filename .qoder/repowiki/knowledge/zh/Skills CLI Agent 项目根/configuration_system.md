## 1. 总体方案

Skills Agent 使用 Python 标准库 `tomllib`（3.11+）解析 TOML 配置文件，结合 `argparse` 命令行参数和 `os.environ` 环境变量，通过一个统一的 `load_agent_config()` 函数完成加载、校验与合并。没有引入第三方配置框架；所有运行时开关都归一化为 `argparse.Namespace` 后传给下游模块。

## 2. 关键文件

- `skills_agent/config.py` — 唯一配置加载入口，定义 `DEFAULTS`、`CHOICES`、`_validate()`、`load_agent_config()`。
- `skills-agent.toml` — 仓库根默认配置模板（注释掉 provider/model/base_url/skill_dirs 等可选项）。
- `pyproject.toml` — 声明 `skills-agent = "skills_agent.cli:main"` 可执行命令及包资源清单。
- `tests/test_config.py` — 覆盖优先级、类型校验、路径解析、废弃选项拒绝的测试集。
- 各 Provider 模块（如 `providers/openai.py`、`providers/anthropic.py`）通过 `{PROVIDER}_MODEL` / `{PROVIDER}_BASE_URL` 环境变量读取密钥与连接地址。

## 3. 架构与约定

### 3.1 优先级顺序（由代码明确实现）

`load_agent_config()` 中用字典展开顺序表达：

```
built-in defaults < environment variables < TOML file < explicit CLI arguments
```

即：**显式 CLI > TOML > 环境变量 > 内置默认值**。测试 `test_cli_overrides_file_even_with_builtin_default_values` 直接断言了该顺序。

### 3.2 配置文件定位

- 默认路径：`args.workspace / "skills-agent.toml"`。
- 可通过 `--config <path>` 显式指定另一个 TOML 文件。
- 若未显式指定且默认路径不存在，则跳过文件加载（不会报错），仅使用环境变量和默认值。
- `skill_dirs` 中的相对路径在加载时相对于**配置文件所在目录**解析并转为绝对路径（`path.parent / item`），因此即使从其他工作目录启动也能正确引用。

### 3.3 支持的配置键

| 键 | 类型/取值 | 来源 | 说明 |
|---|---|---|---|
| `provider` | `openai` / `anthropic` | TOML / env / CLI | 决定 `_MODEL` / `_BASE_URL` 环境变量前缀 |
| `model` | 非空字符串 | TOML / env / CLI | 模型名 |
| `base_url` | 非空字符串 | TOML / env / CLI | 自定义 API 端点 |
| `context_tokens` | 正整数 | TOML / CLI | 上下文窗口大小 |
| `model_context_window` | 正整数 | TOML / CLI | 模型上下文窗口 |
| `max_output_tokens` | 正整数 | TOML / env / CLI | 最大输出 token |
| `summary_output_tokens` | 正整数 | TOML / CLI | 摘要输出上限 |
| `api_format` | `auto` / `responses` / `chat` | TOML / env (`SKILLS_AGENT_API_FORMAT`) / CLI | Provider API 格式 |
| `token_counter` | `auto` / `official` / `usage` | TOML / env (`SKILLS_AGENT_TOKEN_COUNTER`) / CLI | Token 计数策略 |
| `skill_dirs` | 非空字符串列表 | TOML / CLI (`--skill-dir`) | 技能目录（自动解析为绝对路径） |
| `sandbox_image` | 非空字符串 | TOML / CLI | 沙箱镜像 |

新增键需在 `DEFAULTS` 与 `CHOICES` 两处同步注册，否则会被 `_validate()` 拒绝。

### 3.4 环境变量命名规则

- Provider 专属：`{PROVIDER_UPPER}_MODEL`、`{PROVIDER_UPPER}_BASE_URL`（例如 `OPENAI_MODEL`、`ANTHROPIC_BASE_URL`）。选择哪个 Provider 由 `provider` 字段决定。
- 通用开关：`SKILLS_AGENT_{KEY_UPPER}`，目前支持 `SKILLS_AGENT_API_FORMAT`、`SKILLS_AGENT_TOKEN_COUNTER`。
- API Key 不在此配置层管理——由 Provider 模块各自从对应环境变量读取（如 `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`），见测试 `test_cli_and_benchmark_load_file_into_runtime`。

### 3.5 校验规则（由 `_validate()` 强制）

1. **未知键拒绝**：不在 `DEFAULTS` 中的键抛出 `ValueError("Unknown agent config option")`。
2. **必填非空**：除 `_tokens` 结尾键和 `model_context_window` 外，所有键必须是非空字符串。
3. **正整数约束**：以 `_tokens` 结尾的键以及 `model_context_window` 必须是 `int` 且 > 0。
4. **枚举约束**：`provider`、`api_format`、`token_counter` 的值必须在 `CHOICES` 白名单内。
5. **skill_dirs 结构**：必须是字符串列表，每个元素经 `.strip()` 后非空。
6. **TOML 语法错误**：非法 TOML 由 `tomllib.TOMLDecodeError` 向上抛出。
7. **缺失的显式配置文件**：`--config` 指向不存在文件时抛出 `FileNotFoundError`。

测试 `test_invalid_configuration_is_reported_instead_of_silently_ignored` 覆盖了上述所有分支。

### 3.6 已移除的配置

测试 `test_removed_local_deepseek_configuration_is_rejected` 明确断言 `token_counter = "deepseek"`、`tokenizer_path`、`tokenizer_encoding` 三个旧键会被拒绝。这是向后兼容性的硬约束：旧配置会直接失败而非静默忽略。

## 4. 设计要点

- 单一入口：所有子模块（CLI、benchmark、runtime）统一调用 `load_agent_config(args)`，避免各自重复解析逻辑。
- 类型安全：全部值先按字符串读入 TOML，再由 `_validate()` 做运行时类型检查，确保下游 `int` / `str` 假设成立。
- Provider 隔离：`provider` 字段同时控制环境变量前缀，避免不同 Provider 的 model/base_url 互相污染。
- 路径语义：`skill_dirs` 相对路径始终相对于配置文件位置解析，使多工作区部署行为可预测。
- 最小依赖：仅依赖 Python 标准库 `tomllib`，无第三方配置库，降低运行环境要求（与 `requires-python = ">=3.11"` 一致）。