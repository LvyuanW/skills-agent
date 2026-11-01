# skills-agent

Python CLI Agent。模型阅读 `SKILL.md` 和参考脚本，通过 `edit_file` 编辑文件，通过 shell 读取和执行，并根据结果修正。核心负责会话、上下文、模型调用和工具执行。

## 安装和运行

需要 Python 3.11+ 和 Git。Python 依赖及锁定版本见 [pyproject.toml](pyproject.toml) 和 [uv.lock](uv.lock)。

```sh
# 在源码目录安装一次，全局提供 skagt 和 skills-agent 两个等价命令。
uv tool install --editable .

export OPENAI_API_KEY='你的密钥'
export OPENAI_MODEL='支持工具调用的模型名称'
skagt run --session chat_demo
```

安装后可在任何目录执行 `skagt`，不需要先进入源码目录或激活虚拟环境。默认以当前目录作为工作目录；用 `--workspace /path/to/task` 可指定另一目录。若命令不在 PATH 中，执行 `uv tool update-shell` 后重新打开终端。可编辑安装直接使用当前源码，后续源码修改不需要重装；入口或依赖变化时执行 `uv tool install --reinstall --editable .`。

需要长期使用同一套模型配置时，将配置放在 `~/.skills-agent/config.toml`，将 API key、模型名称和连接地址等环境变量放在 `~/.skills-agent/.env`（建议权限 `600`），后续只需执行上面的 `skagt run`。无需每次传入 `uv run --env-file`。仍可使用 `python3 -m venv .venv` 和 `.venv/bin/python -m pip install -e .` 安装到项目虚拟环境。

进入后输入自然语言，每次发送一轮；`/exit` 或 Ctrl-D 退出，Ctrl-C 停止当前任务。再次使用同一会话名可恢复记录。

```sh
# 回放会话，无需 API 配置；--full 展开命令和完整工具输出。
skagt history --session chat_demo
skagt history --session chat_demo --full

# 在指定目录运行，也可通过 --file 提供附件。
skagt run --session task --workspace /path/to/task --file ./input.txt
```

Anthropic 接口使用 `--provider anthropic`，并设置 `ANTHROPIC_API_KEY` 与 `ANTHROPIC_MODEL`。DeepSeek 的 Chat 协议、网关返回用量计量和思考强度配置见 [完整使用说明](docs/usage.md#会话与上下文)。

## 配置

`run` 和 `benchmark` 自动读取工作目录中的 [skills-agent.toml](skills-agent.toml)，缺少时读取 `~/.skills-agent/config.toml`；`--config PATH` 可指定另一份配置，每次只读取一份 TOML。优先级为 **命令行参数 > 配置文件 > 已支持的环境变量 > 默认值**。用户级 `.env` 补充尚未设置的模型环境变量，不覆盖已有环境变量，也不自动读取任务目录的 `.env`；API key 仍经环境变量传给模型。用户级配置随 `SKILLS_AGENT_DATA_DIR` 使用同一个 runtime 数据根目录，完整说明见[使用说明](docs/usage.md)。

启动时用 `--workspace` 选择工作目录、`--session` 选择会话；未指定工作目录时使用启动时的当前目录。后续相对任务路径均以所选工作目录解析。会话和工具编辑历史统一保存在 runtime 管理的 `~/.skills-agent/workspaces/<工作区标识>/`，CLI 不提供历史目录参数。部署环境可通过 `SKILLS_AGENT_DATA_DIR` 配置 runtime 数据根目录。模型窗口、上下文预算和输出额度必须与实际模型匹配；完整配置、界面操作、图片附件、后台作业与中断恢复见 [使用说明](docs/usage.md)。

`edit_file` 用于编辑交付物、工程源码和配置，支持任意目录中的多行文本、完整文件和二进制编辑，以及插入、删除、移动和复制。单条和批量编辑统一使用 `name`、`purpose`、`edits`；每项编辑都填写自己的 `path`、`operation` 和操作参数，不使用顶层默认值或跨项继承。必填 `purpose` 说明本批修改了什么、为什么修改，并作为历史提交说明。有改动的成功批次保存一次 Git 提交，并保留修改前内容，不改变项目自身的 Git 分支或暂存区。shell 可以在用户允许的写入范围内创建、运行临时分析或诊断脚本；这些脚本不能绕过编辑工具直接改写交付物、工程源码或配置。程序生成物、shell 写入和人工修改不自动记录。系统提示词提供历史仓库路径和查询命令；`read-file-history` skill 说明如何查找、比较旧内容，再通过 `edit_file` 恢复需要的部分。记录范围和查询示例见[工作区文件历史](docs/usage.md#工作区文件历史)。

生成图片、截图和处理后的图片副本默认保存在 runtime 的 `images/<会话名>/`，与 `sessions/` 同级。系统提示词提供绝对图片输出目录，shell 和 sandbox 提供 `SKILLS_AGENT_IMAGE_DIR` 环境变量；用户指定交付位置时优先使用该位置，保留源图片。已发送的图片另有会话附件快照，终端图片链接优先打开该快照。

运行时自动采集工作目录、是否位于 Git 仓库、平台、实际执行 shell 和操作系统版本，加入主 Agent 与子 Agent 的系统提示词；交互 CLI 与 `benchmark` 共用这套环境信息。系统提示引导模型按任务需要适度检查实际结果，并如实说明验证范围、未验证事项和剩余限制。

## 单次任务与测评

```sh
.venv/bin/python -m skills_agent.benchmark \
  --prompt-file /path/to/prompt.txt \
  --workspace /path/to/task \
  --log-dir /path/to/run-logs
```

每次任务使用独立日志目录，正常结束、失败或中断时清理本任务登记的进程。会话和文件历史使用统一运行数据目录，退出时将会话、已有图片目录和历史导出到 `<log-dir>/session`、`<log-dir>/images` 和 `<log-dir>/history.git`，供测评归档；没有图片目录或历史仓库时不创建对应导出目录。`runtime-storage.json` 记录原运行数据位置及图片导出位置。`benchmark` 不允许跨任务保留后台服务。

- [ALE 适配器与安装说明](adapters/ale/README.md)
- [ALE 当前环境、隔离和评分验收流程](../benchmark/ale/evaluation-protocol-20261017.txt)
- [Harbor 测评控制器](adapters/harbor/tb4_control/README.md)

ALE 历史启动记录、比较报告与本地临时审计已迁出运行项目；[归档清单](../benchmark/ale/archive/skills-agent-cleanup-20261017/manifest.json)记录原路径、SHA256 和可恢复备份。历史报告不作为当前环境有效性结论。

## 开发与验证

```sh
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
```

测试使用本地模拟模型；ALE 接口测试另需 `ALE_SOURCE_DIR`，见适配器说明。协议测试通过不能替代真实任务、容器隔离及评分验收。

- [完整使用说明与模块索引](docs/usage.md)
- [上下文管理设计](docs/context-management-design.md)
- [提示词版本记录](docs/prompt-history/README.md)
- [运行时代码](skills_agent/)、[内置技能](skills_agent/builtin_skills/)、[测试](tests/)
