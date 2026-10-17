# skills-agent

Python CLI Agent。模型阅读 `SKILL.md` 和参考脚本，编写代码，通过 shell 执行并根据结果修正。核心负责会话、上下文、模型调用和工具执行。

## 安装和运行

需要 Python 3.11+。依赖及锁定版本见 [pyproject.toml](pyproject.toml) 和 [uv.lock](uv.lock)。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .

export OPENAI_API_KEY='你的密钥'
export OPENAI_MODEL='支持工具调用的模型名称'
.venv/bin/skills-agent run --session demo
```

进入后输入自然语言，每次发送一轮；`/exit` 或 Ctrl-D 退出，Ctrl-C 停止当前任务。再次使用同一会话名可恢复记录。

```sh
# 回放会话，无需 API 配置；--full 展开命令和完整工具输出。
.venv/bin/skills-agent history --session demo
.venv/bin/skills-agent history --session demo --full

# 在指定目录运行，也可通过 --file 提供附件。
.venv/bin/skills-agent run --session task --workspace /path/to/task --file ./input.txt
```

Anthropic 接口使用 `--provider anthropic`，并设置 `ANTHROPIC_API_KEY` 与 `ANTHROPIC_MODEL`。DeepSeek 的 Chat 协议、模型匹配 tokenizer 和思考强度配置见 [完整使用说明](docs/usage.md#会话与上下文)。

## 配置

`run` 和 `benchmark` 自动读取工作目录中的 [skills-agent.toml](skills-agent.toml)；`--config PATH` 可指定另一份配置。优先级为 **命令行参数 > 配置文件 > 已支持的环境变量 > 默认值**。API key 只从环境变量读取。

工作目录默认是当前目录，会话默认写入 `<workspace>/.agent/sessions`。模型窗口、上下文预算、输出额度和 tokenizer 必须与实际模型匹配；完整配置、界面操作、图片附件、后台作业与中断恢复见 [使用说明](docs/usage.md)。

## 单次任务与测评

```sh
.venv/bin/python -m skills_agent.benchmark \
  --prompt-file /path/to/prompt.txt \
  --workspace /path/to/task \
  --log-dir /path/to/run-logs
```

每次任务使用独立日志目录，正常结束、失败或中断时清理本任务登记的进程。`benchmark` 不允许跨任务保留后台服务。

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
