# ALE 接入

适配器使用 ALE 的 `sandbox` executor，在每道题的任务容器内执行。对应官方源码版本：[`d9abc073`](https://github.com/rdi-berkeley/agents-last-exam/tree/d9abc0734b56ea34116c5bfcbdd0b808269ab9e2)。

## 安装配置

在宿主机指定两个仓库的路径，将适配器复制到 ALE 会上传的源码目录：

```sh
AGENT_REPO=/path/to/skills-agent
ALE_REPO=/path/to/agents-last-exam

cp -R "$AGENT_REPO/adapters/ale/skills_agent" "$ALE_REPO/ale_run/agents/"
cp "$AGENT_REPO/adapters/ale/skills_agent.yaml" "$ALE_REPO/configs/agents/"
cp "$AGENT_REPO/adapters/ale/smoke.yaml" "$ALE_REPO/skills_agent_smoke.yaml"
```

设置容器可安装的包地址与模型配置。下面使用当前本地 commit 对应的源码包，因此该 commit 必须已推送到 GitHub：

```sh
export SKILLS_AGENT_PACKAGE="https://github.com/LvyuanW/skills-agent/archive/$(git -C "$AGENT_REPO" rev-parse HEAD).tar.gz"
export SKILLS_AGENT_MODEL='你的模型名称'
export OPENAI_BASE_URL='https://api.openai.com/v1'
export OPENAI_API_KEY='你的密钥'
```

也可在本项目运行 `python3 -m pip wheel --no-deps . -w dist` 构建 wheel，将 `SKILLS_AGENT_PACKAGE` 设为容器可访问的 wheel URL。宿主机上的本地包路径不会自动出现在容器里。

`skills_agent.yaml` 默认使用 OpenAI 兼容协议。使用 Anthropic 时，将 `provider` 改为 `anthropic`，`base_url` 和 `api_key` 分别改为 `${env:ANTHROPIC_BASE_URL}` 和 `${env:ANTHROPIC_API_KEY}`，并设置这些环境变量。base URL 包含 API 前缀，例如 `https://api.anthropic.com/v1`。密钥通过环境传给 Agent，不写入命令参数。

可选配置：

| 字段 | 默认值 / 用途 |
| --- | --- |
| `workspace` | ALE 的 `sandbox.task_data_root`，作为脚本当前目录 |
| `context_chars` | `48000` |
| `max_output_tokens` | `4096` |
| `skill_dirs` | `[]`；额外技能目录，必须已在任务容器内可见 |

## 运行一题

先按 [ALE Docker 指南](https://agents-last-exam.org/docs?p=pages/local-docker.html) 准备 Docker 和任务数据。smoke 使用官方 `configs/environments/docker.yaml`，它从 ALE 仓库的 `task-data` 目录读取题目数据。

```sh
cd "$ALE_REPO"
uv sync --all-packages
uv run --no-sync python -m ale_run run skills_agent_smoke.yaml --dry-run
uv run --no-sync python -m ale_run run skills_agent_smoke.yaml
```

配置只运行 `computing_math/os_log_permission_guard_v1` 的 variant 0，并发为 1，不自动续跑或重试。`--dry-run` 只展开运行列表；真实运行才会检查容器、安装、模型调用、任务产物和评分。

ALE 传入的题目文本完整写入 `prompt.txt`，适配器启动 `python -m skills_agent.benchmark`。模型按题目中的路径读取输入、写入要求的 `output`；适配器不改写任务路径。`shell` 直接在 ALE 任务容器中执行，不需要配置本项目的 `--sandbox-image`。

## 日志与退出

以下文件位于 ALE 的 `executor.work_dir`，由 ALE 收集到实验日志：

```text
install.log                 包安装输出
prompt.txt                  原始任务文本
transcript.log              主会话显示记录（中间过程为摘要）
stderr.log                  进程错误输出
session/events.jsonl        原始会话事件与模型响应
session/subagents/*/        独立子会话日志
```

`parse_artifacts()` 从原始事件日志将完整模型回答、工具调用、工具结果和 token usage 转为 ALE 轨迹，不受终端摘要显示影响。子会话转为嵌套轨迹；主会话终端仍只展示 sub-agent 的目标摘要。截断或未完成的日志会在轨迹中标记。

适配器收到异步取消或 SIGTERM 时终止并回收启动的进程；benchmark 随之清理当前脚本。上述 ALE 版本的外层 executor 超时可能不向容器内进程发送信号，smoke 使用 `cleanup_mode: delete`，由 ALE 删除任务容器。适配器无法处理没有传递到自身的取消。

## 本地验证

使用本项目虚拟环境，并安装 ALE 接口所需的测试依赖：

```sh
.venv/bin/python -m pip install pydantic requests
ALE_SOURCE_DIR=/path/to/agents-last-exam \
  .venv/bin/python -m unittest discover -s tests -v
```

测试使用官方 ALE 的配置、工厂和轨迹类型，以及本地模拟模型，验证两种协议的任务执行、文件产出、日志解析和进程清理。不设置 `ALE_SOURCE_DIR` 时跳过 ALE 接口测试。真实 Docker 评分需要另行运行上述 smoke。
