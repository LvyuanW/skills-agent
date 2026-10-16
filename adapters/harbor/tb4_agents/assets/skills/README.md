# skills-agent 的 Harbor 接入资产

`tb4_agents.skills:SkillsAgent` 在任务容器中运行冻结 wheel，不修改 Agent 核心，不创建第二层 Docker sandbox。正式运行状态见仓库的 `docs/terminal-bench-4-evaluation/current-run.json`；下方旧预检只作历史证据。

## 当前发布与配置

当前配置以同目录 [`agent.json`](agent.json) 和仓库根目录 [`skills-agent.toml`](../../../../../skills-agent.toml) 为准：

| 项目 | 固定值 |
|---|---|
| 网关 / 模型 | `https://yuzhiapi.xyz/v1` / `deepseek-v4.1-flash` |
| 主 Agent、普通子 Agent | `reasoning_effort=max`，`thinking.type=enabled` |
| 辅助摘要、tool report | 沿用原生 `thinking.type=disabled` |
| 工作上下文 / 模型总窗口 | 524,000 / 524,000 tokens |
| 单次输出 / 辅助输出上限 | 131,072 / 8,192 tokens |
| 有效输入预算 / 压缩触发值 | 392,928 / 392,928 tokens |
| Python / 编码库 / MCP SDK | 3.12.15 / `deepseek-recipe==0.1.1` / `mcp==1.30.0` |
| wheel SHA256 | `7441b0691f21a78c49520b4b94865eb9d8d95ec842e4fe9a5a0a423c2190ec34` |
| 源码快照 tree SHA256 | `01147222332d76ca3070aac044dc409d9d84e1ebfec0538c8ab1e0b9fe8005c7` |
| bundle SHA256 | `c21f880cb717c066e58db343f01044db0f2eb818bbb633eafb8ee058b887b767` |
| bundle 大小 | 61,748,213 bytes |

预算只从随发布上传的 `skills-agent.toml` 读取，入口使用 `--config /opt/tb4-skills/skills-agent.toml`，不再以 CLI 覆盖这四项预算。有效输入取 `min(context_tokens, model_context_window - max_output_tokens)`；压缩触发取 `min(context_tokens * 0.9, model_context_window - max_output_tokens)`。密钥仅来自 `DEEPSEEK_API_KEY`。

本目录提交适配器、配置、依赖锁定、校验值和合成预检源码，**不包含可直接安装的完整 runtime**。wheel、Python、37 个第三方 wheels、tokenizer 与 runtime bundle 独立保存在服务器的 `agent.json` 所指定目录；发布库存由外部 `runtime-manifest.json` 记录。来源和历史 SHA 见 [`runtime-source.json`](runtime-source.json)，当前 bundle 校验见 [`runtime-bundle.sha256`](runtime-bundle.sha256)。缺少外部发布文件时必须先恢复该发布，不能将本目录当成 `assets_path`。版本号仍为 0.1.0，以源码/wheel/bundle SHA 区分。

预检从当前脚本旁读取配置、hook 与 fixture；需要在已准备的 Harbor / skills-agent 运行时和相应服务器路径下显式运行。`run_vision_task.py` 会调用模型，不能用作普通离线单元测试。

## 历史：首次接入配置和预检（已被当前发布替代）

```json
{
  "import_path": "tb4_agents.skills:SkillsAgent",
  "model_name": "deepseek-v4-1-flash-260910",
  "kwargs": {
    "assets_path": "/data/tb4/releases/skills-assets-31829dde914b",
    "api_base": "https://rehdasu.cn/v1"
  }
}
```

密钥只从进程环境 `DEEPSEEK_API_KEY` 读取。Chat 端点为 `https://rehdasu.cn/v1/chat/completions`。适配器仅允许明确配置的网关或官方端点，不把凭据从普通文档中的域名自动转发。

| 项目 | 固定值 |
|---|---|
| 模型 | `deepseek-v4-1-flash-260910` |
| 主 Agent、普通子 Agent | `reasoning_effort=max`，`thinking.type=enabled` |
| 辅助摘要、tool report | 沿用新版原生 `thinking.type=disabled` |
| 工作上下文 / 模型窗口 | 1,000,000 / 1,048,576 |
| 主请求 / 辅助输出上限 | 32,768 / 8,192 tokens |
| Python | 3.12.15，随离线包安装 |
| DeepSeek 编码库 / MCP SDK | `deepseek-recipe==0.1.1` / `mcp==1.30.0` |
| 当时 wheel SHA256 | `31829dde914bd50997ca5d7d72304ce176ca70dfaba5f60c0a152e28bd4820ae` |
| wheel 源码 snapshot tree | `2591210afb868d20506a912d28e4034ed0991ec8a550aa5ae7632d989c417fd8` |
| 当时 bundle SHA256 | `4d179e78b72462efe6f3cd319f9cb17c7dc056b27a4c8f2897ca1105288200f6` |
| bundle 大小 | 64,398,308 bytes |
| v41 tokenizer SHA256 | `81f64d1248a68ce3663e07ab3ee48b851e5df0e32d27cb98e4c9a268151e8d99` |

新 wheel 与旧 wheel 的 Requires-Dist、项目 uv.lock 相同；复用已固定的依赖 wheelhouse、Python、tokenizer，仅替换 Agent wheel，重建独立 bundle。已核对新 venv 的 53 个包文件逐字匹配冻结 wheel。包版本仍为 0.1.0，因此使用 wheel/bundle/source SHA 区分发布。`runtime-manifest.json`、`requirements.lock`、源快照和部署 freeze 共同记录版本；不以包版本字符串代替源码身份。

Tokenizer 只下载单个 JSON，来自 [DeepSeek 官方 deepseek-recipe 固定提交](https://github.com/deepseek-ai/deepseek-recipe/tree/8cadfede7063c896b944e7bae05daa3549ae97ea/static/tokenizers/v41)。说明与 MIT 许可保存在服务器 assets 中，没有下载模型权重。

## 适配边界

原生 provider 对非官方 host 不设置 DeepSeek effort；`sitecustomize.py` 仅为明确指定的网关，在原生 `_chat_payload` 加入 max 与 enabled 字段。原生 disabled 辅助请求保留。计量与发送调用同一个 payload 方法，继续使用 v41 tokenizer/编码器，没有字符估算。

任务技能通过原生 `--skill-dir` 暴露。`IMAGE-SKILL.md` 介绍已有 shell image-feedback 协议；图片会复制进原始 session，然后以原始像素发送。`MCP-SKILL.md` 与通用桥接使用官方 MCP SDK，支持 SSE、streamable HTTP、stdio，在任务期间保留客户端会话并将图片返回为原生反馈。56 题中仅 medical-claims-processing 声明非空 MCP（playwright SSE）。桥接没有解题脚本、领域规则或预期答案。

审计仅在 `/opt/tb4-skills/venv` 自动加载，避免干扰任务自带 Python。只记录 endpoint、模型、effort、输出上限、工具名、图片数量、usage 和错误类别，不记录 key、header、正文或图片数据；原始 Agent 轨迹按其既有日志机制另存。重复 usage 按请求去重，正常 GeneratorExit 不算传输错误。Agent 原生 SIGTERM/进程范围清理与 MCP 优雅退出保留。

## CA 兼容性

离线审阅 56 个 main Dockerfile，并检查 C 当时已有的 30 个任务镜像。bun-sourcemap-leak、ks-solver-cpp、nextjs-performance 缺 OS CA 根。为此仅在 Agent venv 进程内，为 urllib 默认 HTTPS context 补充已固定的 `certifi==2026.7.22` 根证书；保留原有 OS 根、CERT_REQUIRED、主机名校验和显式传入的 SSLContext。不安装或修改任务 OS 包，也不设置会继承到任务 Python 的 SSL_CERT_FILE。

真实缺 CA 的 Ubuntu24 镜像默认已加载根为 0，补充后为 121；Fedora42 从 146 补充为 153。两者以无 key GET `/v1/models` 得到 401，证明 TLS 验证链路成功；没有发送推理请求。新版离线回归也验证系统 Python 默认 context 未改、显式 context 保留。

## 2026-10-16 新版预检

服务器 C：`8.216.11.161`。全部新版结果保存在 `/data/tb4/runs/preflight-skills-updated-31829dde914b`，补充证据在 `/data/tb4/artifacts/skills-native-preflight-updated-31829dde914b`。

| 检查 | 结果 |
|---|---|
| canonical 合成任务 | `synthetic-20261015T185213`：原生工具执行成功，独立 verifier reward=1，无异常；3 请求、usage 3/3 |
| 新版离线检查 | v41 文本/图片计量、网关 max、原生 disabled 辅助调用、metadata 审计、CA、安全作用域、系统 Python、显式 context 均通过 |
| 通用 MCP | 新版 venv 的 SSE 发现/调用/持久状态测试通过，连续 counter 返回 1、2 |
| 真实 MCP 侧车 | 复用环境预热证据：playwright SSE initialize、22 tools、browser_tabs 成功；此项未运行正式 Agent 题目 |
| 真实图片与普通子 Agent | `vision-child-20261015T185335`：9 请求、usage 9/9，2 次含原生 image_url，普通子 Agent 完成 1；8 个 thinking 请求全 max，1 个辅助 report 原生 disabled；传输错误 0 |
| 图片任务语义评分 | **reward=0**：Agent 写 `green\n`，独立 verifier 期望 blue；不修改答案或为改变评分重跑 |

蓝色 fixture、实际 session snapshot、按原生 encoder 离线重建的 image_url 解码结果 SHA 一致：`09fe43177780ae8870512a285fed35df07c36bf0e7571500484fb473ee3d0230`。图像 192×192、全部 RGB `(0,0,255)`。这确认本地原生图片反馈、payload 构造和普通子 Agent 调用链路，以及网关接受请求且未返回协议错误。它不证明网关内部正确转发了图片，不能直接归咎模型或 Agent 能力。独立最小控制中，同图在直接 Chat、Responses、Anthropic 输入、多轮无 tool 输入、tool 后去掉观察包装的输入均识别为 Blue；带原生图片观察包装的极小 Chat 控制回答 White。标准 API 和图片字节链路通过，但 Agent 原生观察上下文语义检查失败、原因未定。不能认定网关整体不支持工具后图片，也不能直接定性模型故障或 Agent 实现错误。所有原始 reward 保留，未为此改写 Agent 协议、提示或工具反馈。报告明确标注是离线重建，未声称保存了完整历史请求正文。

新版 canonical 记录输入 8,697、缓存输入 7,936、输出 411 tokens；图片预检输入 30,687、缓存输入 25,088、输出 3,931 tokens。费用保持未知（cost_usd=None），不把 native 估算当网关账单。

旧 wheel `e4733411709e…`、旧 bundle `318f7db05270…` 保存在原 `/data/tb4/releases/skills-assets`；旧 canonical、图片语义失败、首次审计作用域失败以及全部已知 usage 原样保留。新目录没有覆盖旧预检。

上述记录产生时正式 56 题评测尚未启动；它不表示当前评测状态。
