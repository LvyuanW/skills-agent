# ALE 接入

适配器使用 ALE 的 `sandbox` executor，在每道题的任务容器内执行。对应官方源码版本：[`d9abc073`](https://github.com/rdi-berkeley/agents-last-exam/tree/d9abc0734b56ea34116c5bfcbdd0b808269ab9e2)。

该版本的 sandbox executor 还需应用本目录的 [取消清理补丁](patches/sandbox-cancellation.patch)。补丁处理外层超时、启动期间取消和启动后的执行异常，等待容器内运行停止后再返回；无法确认停止时跳过评分。已有修改或其他 ALE 版本应先检查补丁适用性；已应用的仓库不要重复应用。

## 安装配置

在宿主机指定两个仓库的路径，将适配器复制到 ALE 会上传的源码目录：

```sh
AGENT_REPO=/path/to/skills-agent
ALE_REPO=/path/to/agents-last-exam

git -C "$ALE_REPO" apply --check "$AGENT_REPO/adapters/ale/patches/sandbox-cancellation.patch"
git -C "$ALE_REPO" apply "$AGENT_REPO/adapters/ale/patches/sandbox-cancellation.patch"

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
| `context_tokens` | `1000000`；完整工作上下文的 token 预算，可设为任意正整数，达到 90% 时压缩；有效输入预算更小时提前触发 |
| `api_format` | `auto`；OpenAI 协议可选择 `responses` 或 `chat` |
| `token_counter` | `auto`；使用官方接口 `official` 或 DeepSeek 本地计量 `deepseek` |
| `tokenizer_path` | 未设置；DeepSeek tokenizer 文件的容器内路径 |
| `tokenizer_encoding` | 未设置；匹配实际 DeepSeek 模型的 `v4` 或 `v41` 编码器 |
| `model_context_window` | 未设置；实际模型窗口上限，用于校验输入与输出额度之和 |
| `max_output_tokens` | `32768`；单次输出额度，包含思考与回答 |
| `summary_output_tokens` | `8192`；辅助生成额度，压缩摘要关闭思考并以此限制单次生成长度 |
| `skill_dirs` | `[]`；额外技能目录，必须已在任务容器内可见 |

ALE 继续通过 `skills_agent.yaml` 的 `config` 字段配置，由适配器传给 benchmark 入口；无需依赖宿主机的 `skills-agent.toml`。

`context_tokens` 与实际模型能力分开配置。设置 `model_context_window` 后，有效输入预算为 `min(context_tokens, model_context_window - max_output_tokens)`；未设置时使用 `context_tokens`，仍需确认服务端限制。普通子任务和历史检索子任务继承上下文预算；辅助调用跟随该配置，同时受实际模型窗口约束并预留辅助输出空间。调整上下文预算不会改变默认的 `max_output_tokens: 32768`。

OpenAI 和 Anthropic 使用官方请求前计量接口；OpenAI 网关需要支持 Responses 及其计量接口，并设置 `api_format: responses`。DeepSeek 使用匹配实际模型的本地 tokenizer 和消息模板。使用 DeepSeek 时，在配置中设置 `api_format: chat`、`token_counter: deepseek`、`tokenizer_path` 和对应的 `tokenizer_encoding`；安装包需要包含 `deepseek` 可选依赖，tokenizer 文件必须已在任务容器内可用，宿主机路径不会自动传入。

## 运行一题

先按 [ALE Docker 指南](https://agents-last-exam.org/docs?p=pages/local-docker.html) 准备 Docker 和任务数据。smoke 使用服务器上已准备好的 `configs/environments/docker.local.yaml`；该文件需位于 ALE 仓库中，并指向服务器实际的任务数据目录和环境配置。本项目不提供或覆盖该文件。

```sh
cd "$ALE_REPO"
uv sync --all-packages
uv run --no-sync python -m ale_run run skills_agent_smoke.yaml --dry-run
uv run --no-sync python -m ale_run run skills_agent_smoke.yaml
```

配置只运行 `computing_math/os_log_permission_guard_v1` 的 variant 0，并发为 1，不自动续跑或重试。`--dry-run` 只展开运行列表；真实运行才会检查容器、安装、模型调用、任务产物和评分。

正常运行后检查该次日志中的 `run.json`、`eval_result.json` 和 `trajectory.json`，确认模型及工具调用、任务产物和评分均已产生。模拟模型测试通过不代表真实 Docker 评分已通过。

ALE 传入的题目文本完整写入 `prompt.txt`，适配器启动 `python -m skills_agent.benchmark`。模型按题目中的路径读取输入、写入要求的 `output`；适配器不改写任务路径。`shell` 直接在 ALE 任务容器中执行，不需要配置本项目的 `--sandbox-image`。

## 日志与退出

以下文件位于 ALE 的 `executor.work_dir`，由 ALE 收集到实验日志：

```text
install.log                 包安装输出
prompt.txt                  原始任务文本
transcript.log              主会话显示记录（正文流式显示，工具为摘要）
stderr.log                  进程错误输出
session/events.jsonl        原始会话事件与模型响应
session/context.json        增量维护、按阈值压缩的工作上下文
session/history.sqlite      按需创建和增量更新的原始历史索引
session/subagents/*/        独立子会话日志
```

`parse_artifacts()` 从原始事件日志将完整模型回答、工具调用、工具结果和 token usage 转为 ALE 轨迹，不受终端摘要显示影响。图片工具结果保留文本与图片内容块，从已收集的会话快照读取图片，由 ALE 保存到运行日志的相对路径；快照缺失或不可读时保留文本并记录原因。子会话转为嵌套轨迹；主会话终端仍只展示 sub-agent 的目标摘要。截断或未完成的日志会在轨迹中标记。

适配器收到异步取消或 SIGTERM 时终止并回收启动的进程；benchmark 在成功、失败和中断退出前清理当前脚本及本任务通过 process-management helper 登记的后台进程。benchmark 拒绝 `start --persistent`，需要留给评分器访问的长期服务应由题目环境管理。上述补丁负责将宿主机取消传递到容器运行并完成清理。smoke 的 `cleanup_mode: delete` 在评分后才删除容器，不能替代评分前的进程清理。

超时验收需在独立实验副本中设置较短的 `wall_time_s`，让 Agent 执行超过该时限的脚本，并在 ALE 开始评分前检查容器中的 Agent、shell 及其子进程均已退出，同时确认输出文件停止变化。仅看到 `timeout` 状态或最终容器被删除不算通过。

## 本地验证

使用本项目虚拟环境，并安装 ALE 接口所需的测试依赖：

```sh
.venv/bin/python -m pip install pydantic requests
ALE_SOURCE_DIR=/path/to/agents-last-exam \
  .venv/bin/python -m unittest discover -s tests -v
```

测试使用官方 ALE 的配置、工厂和轨迹类型，以及本地模拟模型，验证两种协议的任务执行、文件产出、日志解析和进程清理。不设置 `ALE_SOURCE_DIR` 时跳过 ALE 接口测试。真实 Docker 评分需要另行运行上述 smoke。
