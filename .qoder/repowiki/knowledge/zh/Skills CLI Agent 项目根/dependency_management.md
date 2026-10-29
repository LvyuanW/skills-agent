## 概述

本仓库使用两套 Python 依赖管理体系，分别服务于「项目主包」与「Harbor/TB4 Agent 子环境」：

- **主项目**（`skills-agent`）：使用 `uv` 作为包管理器与锁文件生成器，配合 `pyproject.toml` 声明运行时依赖、可选测试依赖与可执行入口。
- **Harbor TB4 Agent 技能包**（`adapters/harbor/tb4_agents/assets/skills/`）：使用传统 `requirements.txt` 风格的 `requirements.lock` 锁定该子环境的运行依赖。

没有发现任何 Go (`go.mod`)、Node (`package.json`)、npm/yarn lockfile、Rust Cargo、pipenv/Poetry 或私有 registry 配置。所有第三方包均来自 PyPI。

## 关键文件

- `pyproject.toml` — 主包元数据、构建后端、依赖声明、可选依赖、`setuptools.packages.find` 包含规则、打包资源清单。
- `uv.lock` — `uv` 生成的确定性锁文件，记录每个包的版本、来源（`https://pypi.org/simple`）、sdist/wheel URL 及 SHA256 哈希。
- `adapters/harbor/tb4_agents/assets/skills/requirements.lock` — Harbor 侧 skills 运行时的精确依赖快照（如 `mcp==1.30.0`、`pydantic==2.14.1`、`httpx==0.28.1` 等），由评测脚本在离线环境中消费。
- `adapters/ale/skills_agent/pyproject.toml` — ALE 适配器的独立子包定义（存在但内容未展开检查）。

## 架构与约定

### 主包依赖策略

- 构建后端固定为 `setuptools.build_meta`，要求 `setuptools>=68`。
- `requires-python = ">=3.11"` 约束最低 Python 版本。
- 运行时依赖采用「宽松上限 + 精确下限」的区间写法：例如 `rich>=14,<15`、`prompt-toolkit>=3.0,<4`、`Pillow>=11,<13`；`PyYAML>=6.0` 无上限。
- 可选依赖通过 `[project.optional-dependencies]` 的 `test` 分组引入 `pyte>=0.8,<0.9`，仅用于测试环境。
- 可执行命令通过 `[project.scripts]` 暴露 `skills-agent = "skills_agent.cli:main"`。
- 包发现范围限定为 `include = ["skills_agent*"]`，避免把 `adapters/` 等目录误打包进主包。
- 非 Python 资源（`SKILL.md`、`scripts/*.py`、`scripts/*.sh`、`references/*.md`）通过 `[tool.setuptools.package-data]` 显式纳入，确保安装后可被运行时加载。

### 锁文件与溯源

- `uv.lock` 以 v1 格式记录，`revision = 3`；每个 `[[package]]` 条目同时给出 sdist 和 wheel 的 `url` 与 `hash`（SHA256），并标注 `source = { registry = "https://pypi.org/simple" }`，保证可重现安装。
- 未发现 `.gitignore` 中排除 `uv.lock` 的配置，因此锁文件随代码库版本化。

### Harbor/TB4 子环境依赖

- `adapters/harbor/tb4_agents/assets/skills/requirements.lock` 是扁平的 `name==version` 列表，覆盖 MCP 客户端（`mcp==1.30.0`）、HTTP 层（`httpx==0.28.1`、`httpcore==1.0.9`、`h11==0.16.0`）、验证（`jsonschema==4.26.0`、`pydantic==2.14.1`）、Web 框架（`starlette==1.8.0`、`uvicorn==0.54.0`、`sse-starlette==3.5.0`）以及评测所需工具（`deepseek-recipe==0.1.1`、`python-dotenv==1.2.4`）。
- 同目录下还存在 `runtime-source.json`、`runtime-bundle.sha256`、`tokenizer-source.json` 等辅助文件，说明该环境还依赖外部二进制/模型资产，但不在 Python 依赖范围内。

## 约定与约束

- **包来源**：所有包来自 PyPI 官方索引（`https://pypi.org/simple`），未见自定义 index URL、`PIP_INDEX_URL`、`UV_INDEX_URL` 或 `PYPI_TOKEN` 相关配置。
- **版本范围**：主包对易变库（`rich`、`prompt-toolkit`、`Pillow`）使用上界截断以避免破坏性升级；对稳定库（`PyYAML`）不设上限。
- **可重现性**：通过 `uv.lock` 的 sdist/wheel 哈希校验保证安装可复现；Harbor 侧则通过 `requirements.lock` 的精确版本号保证。
- **测试依赖隔离**：测试专用库（`pyte`）放在 `optional-dependencies.test`，不进入主包运行时依赖。
- **包发现白名单**：`setuptools.packages.find` 仅包含 `skills_agent*`，明确排除 `adapters/`、`tests/`、`docs/` 等非主包目录。
- **资源打包白名单**：只有列出的 `builtin_skills/*` 和 `context/history_skill/*` 路径下的 `.md`/`.py`/`.sh` 文件会被打包，未列入的资源在安装后不可用。

## 未发现的实践

- 无自动化更新依赖的 CI 任务或 bot（如 Dependabot、Renovate）可见于当前树。
- 无私有镜像、内源或 `pip.conf` / `uv.toml` 中的 registry 重定向配置。
- 无 `setup.py`、`requirements.txt`（主包层面）、`poetry.lock`、`Pipfile` 等其他依赖描述文件。