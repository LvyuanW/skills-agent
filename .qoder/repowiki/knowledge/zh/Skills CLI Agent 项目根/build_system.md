## 1. 使用的系统与工具

- **Python 包构建**：`setuptools`（build-backend `setuptools.build_meta`，要求 `>=68`），通过根目录的 `pyproject.toml` 声明。
- **依赖管理**：`uv`，由 `uv.lock` 提供锁定版本；README 明确“依赖及锁定版本见 pyproject.toml 和 uv.lock”，ALE adapter README 使用 `uv sync --all-packages` / `uv run` 运行 smoke 任务。
- **可执行入口**：`[project.scripts]` 暴露 `skills-agent = "skills_agent.cli:main"`。
- **运行时约束**：`requires-python = ">=3.11"`。
- **容器化**：无根级 Dockerfile；评测与预检任务在各子模块内自带 `Dockerfile`（基于 `python:3.12-slim-bookworm`）。
- **部署/评测编排**：bash 脚本 + systemd slice 资源隔离 + Docker/containerd，集中在 `docs/terminal-bench-4-evaluation/` 下。

## 2. 关键文件

| 文件 | 作用 |
|---|---|
| `pyproject.toml` | 项目元数据、依赖、可选 test 依赖、setuptools 打包配置、package-data 白名单 |
| `uv.lock` | 确定性依赖锁定 |
| `skills-agent.toml` | 运行时配置文件 |
| `docs/terminal-bench-4-evaluation/prepare-base.sh` | 三台 Ubuntu 22.04 主机的基础系统预配（apt、Docker、containerd、systemd slice、用户/分区） |
| `docs/terminal-bench-4-evaluation/tb4-exec.sh` | 评测控制器在共享边界内的 root SSH 启动器，设置 `UV_CACHE_DIR` / `UV_PYTHON_INSTALL_DIR` |
| `adapters/harbor/tb4_control/smoke_task/environment/Dockerfile` | smoke 测试镜像 |
| `adapters/harbor/assets/codex/capability-smoke-task/environment/Dockerfile` | Codex 能力预检镜像 |
| `adapters/harbor/tb4_agents/assets/skills/preflight/vision_task/environment/Dockerfile` | 视觉任务预检镜像 |
| `adapters/ale/pyproject.toml` | ALE 子包的独立打包定义 |

## 3. 架构与约定

### 3.1 包构建与分发
- 仅有一个顶层 Python 包 `skills_agent`，通过 `[tool.setuptools.packages.find].include = ["skills_agent*"]` 发现。
- 非 `.py` 资源（`SKILL.md`、`scripts/*.py`、`scripts/*.sh`、`references/*.md`）通过 `[tool.setuptools.package-data]` 显式白名单纳入 wheel，确保安装后可被运行时加载。
- 包版本硬编码为 `0.1.0`；Harbor 技能 bundle 文档说明“包版本仍为 0.1.0，因此使用 wheel/bundle/source SHA 区分发布”。

### 3.2 依赖锁定策略
- 顶层 `pyproject.toml` 只声明运行时依赖与可选 `test` 依赖，实际版本全部来自 `uv.lock`。
- ALE adapter 有自己的 `adapters/ale/skills_agent/pyproject.toml`，体现子包独立依赖集合。
- Harbor 技能 bundle 侧维护 `requirements.lock`、`runtime-source.json`、`tokenizer-source.json`、`runtime-bundle.sha256`，将 Agent wheel、Python、tokenizer 三者分别固定并校验。

### 3.3 评测与部署流水线
- 基础环境由 `prepare-base.sh` 一次性准备：校验主机为 Ubuntu 22.04、32 核、cgroup2、挂载空 `/data` 磁盘（500 GiB ext4），安装固定版本的 Docker/containerd 并通过 `apt-mark hold` 锁定，随后以 `tb4.slice`（CPU 2–31、内存 115G、无 swap）调度。
- 评测执行通过 `tb4-exec.sh` 以 root SSH 进入，经 `systemd-run` 在 `tb4.slice` 中以 `tbench` 用户运行命令，并将 PATH 指向 `venvs/harbor/bin`、缓存路径指向 `/data/tb4/cache/uv`。
- 各 smoke/preflight 任务自带 `environment/Dockerfile` + `tests/Dockerfile` + `tests/test.sh` + `task.toml`，形成“镜像 → 测试容器 → 测试脚本”的统一结构。

### 3.4 测试运行
- 测试位于 `tests/`，命名 `test_*.py`，按 pytest 约定组织；可选依赖 `pyte` 在 `pyproject.toml` 的 `test` extras 中声明。
- 没有 Makefile、CI YAML 或 GitHub Actions 文件——仓库未内置自动化 CI 流水线。

## 4. 约定与约束（经验证）

- **Python 版本**：`requires-python = ">=3.11"`，由 setuptools 在构建时强制。评测镜像统一使用 `python:3.12-slim-bookworm`。
- **依赖来源**：README 明确要求“依赖及锁定版本见 pyproject.toml 和 uv.lock”；新增依赖应同时更新两者。
- **包数据范围**：只有 `[tool.setuptools.package-data]` 白名单中的资源才会随 wheel 分发；新增 `builtin_skills` 下的 .md/.sh 必须在此追加路径。
- **评测主机规格**：`prepare-base.sh` 开头断言 ID=ubuntu、VERSION_ID=22.04、nproc==32、cgroup2 存在，且 `/data` 必须是 500 GiB 空 ext4 分区——不满足即直接退出。
- **Docker 包冻结**：`prepare-base.sh` 查询 apt 候选版本后 `apt-mark hold` 锁定 docker-ce、docker-ce-cli、containerd.io、docker-buildx-plugin、docker-compose-plugin，防止后续升级破坏评测一致性。
- **评测进程资源隔离**：所有评测任务通过 `systemd-run ... --slice=tb4.slice` 启动，受 `AllowedCPUs=2-31`、`MemoryMax=115G`、`MemorySwapMax=0` 限制。
- **uv 缓存路径**：`tb4-exec.sh` 显式导出 `UV_CACHE_DIR=/data/tb4/cache/uv` 与 `UV_PYTHON_INSTALL_DIR=/data/tb4/python`，使 uv 安装可跨 trial 复用。
- **Bundle 版本标识**：Harbor 技能 bundle 不以 Python 包版本号区分发布，而依赖 `runtime-manifest.json`、`requirements.lock`、源快照、freeze 产物与 `runtime-bundle.sha256` 共同标识身份。

## 5. 缺失项

- 仓库不含 `Makefile`、GitHub Actions / GitLab CI 等持续集成配置，也未包含根级 `Dockerfile`。
- 没有独立的 release/publish 脚本；当前版本字符串与发布流程未在仓库内实现为可重复构建步骤。