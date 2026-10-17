# ALE01 环境与评分故障审计及新服务器验收清单

审计日期：2026-10-16。主审计批次为 `skills_agent_formal_20261013T090106Z_73666d7`，旧批次 `skills_agent_formal_20261010T231233Z` 仅用于核对重复出现的问题。时间均使用北京时间。

ALE01 本轮存在 **32 题 DeepSeek HTTP 503 中断、6 题 Docker 端口冲突、4 题评分器或评分环境故障**。三个集合互不重叠，共影响 **42 道不同题目**。这不能表述为“42 题全部与 Agent 无关”：503 是上游不可用，但旧版主循环缺少重试和批次熔断放大了影响；Ranking 题同时存在 Agent 请求 EOF 和评分环境故障。普通零分、交付质量不足和真正超时没有因为分数低就被归为环境失败。

此次故障审计只汇总诊断证据，不执行候选、不读取 reference 答案、不重新评分。后续 ALE04 已完成启动验收，并于 **20:23:43 北京时间**启动新一轮正式测评，首次确认 12 个任务容器正在执行。详见 [ALE04 启动记录](ale04-evaluation-launch-2026-10-16.md)；最终题目评分仍在进行。

## 1. 故障范围与处理方式

| 类别 | 影响 | 已确认事实 | 应采取的措施 |
| --- | ---: | --- | --- |
| DeepSeek 服务不可用 | 32 题 | 主会话记录 HTTP 503、`service_unavailable_error`；旧主循环一次错误后结束，控制器继续派下一题 | 启动前验证真实吞吐，设置有限请求恢复、全局熔断和暂停派发；有外部中断的旧记录单独标记 |
| RootlessKit 端口冲突 | 6 题 | `env_start` 阶段 bind 失败；没有 Agent 主会话，评分未执行 | 独立端口池、进程锁、失败换端口；并发全生命周期烟测 |
| PDE 评分代码错误 | 1 题 | `dict.lower` 导致评分失败、分数为空 | 修复结构化要求匹配；统一修复版评分器重评原文件 |
| Ranking 评分环境缺口 | 1 题 | verifier 在 `uv sync --frozen` 阶段失败，外层吞成 0；候选尚未执行 | 可写的任务内 `UV_CACHE_DIR`；保留冻结锁及完整诊断 |
| LTMLE 运行时选择缺口 | 1 题 | 固定 R 包准备失败，发生于候选 R 脚本运行之前 | 补任务 `software/Rscript` wrapper，选中镜像内 R 4.3.2 |
| DiT 评分代码接口缺口 | 1 题 | 评分器 stub 缺公开模型导出，候选导入失败、`cases=[]` | 补齐公开导出；保持行为测试和阈值不变 |

四类评分问题的原交付文件都还在；修复评分环境通常只需要重评原文件，不需要给 Agent 一次新的生成机会。修复后实际得分仍未知。

## 2. DeepSeek 故障：扩容服务器不能消除上游中断

32 次主会话 503 集中在 **10 月 15 日 09:21:05 至 09:55:53**，约 34 分 48 秒。这只是已观察到的错误窗口，不能反推服务故障的完整起止时间，也不能代表现在的服务健康状况。审计未观察到主会话 HTTP 401、402、403、413 或 429；503 不是密钥错误、余额不足或主机内存不足的证据。

旧版 `73666d7` 在主请求失败后直接结束整题，随后控制器继续新题，导致一次上游故障扩散成 32 次受损尝试。最新待测版本 `6b1c55e2aaa07c246a949307c4f40844ef35b53d` 已在主请求使用 `retry_call`，但默认无限尝试、固定最少 5 秒等待，并不等于具备批次级熔断，也不是硬性请求总时限。

新批次应将请求级恢复策略冻结进启动清单：例如最多 6 次、每次操作重试预算 120 秒，最终数值以实际验收和启动清单为准。预算只控制是否开始下一次请求，正在阻塞的请求仍可能越过预算。不得重放已完成的工具副作用，也不得接受不完整工具参数。请求等待仍计入原题的 ALE 时限。

并发控制需分为任务并发与模型请求并发。12 个任务可能还产生辅助请求，不能假设等于 12 个 API 请求。应逐级提升到目标并发，观察 token 吞吐、首包与完整响应延迟、429/503 和断流率；持续上游故障时停止派新题，采用统一半开探测恢复。多把同账户密钥不自动拥有独立额度。

另有一次 HTTP 200 流提前结束、一次只有 reasoning 的空 `stop`、一次旧 32768 输出上限耗尽。这些是响应可用性和 Agent 恢复能力问题，不能全部归为服务器环境没装好。Ranking 的 EOF 与下文的 verifier 环境失败同时存在。

详细证据：[provider-failures.json](/Users/apple/dev/learn-ai-agent/tmp/ale01-infra-audit-20261016/provider-failures.json)。

## 3. Docker 端口：Agent 尚未启动就失败

本轮以下六题在约 0.6–0.7 秒内于 `env_start` 失败，没有 Agent 主会话，也没有有效评分：

| 题目 | 冲突宿主端口 | `batch.log` 行 |
| --- | ---: | ---: |
| `life_sciences/WGS_Variant_Calling` | 33094 | 2646 |
| `life_sciences/amber_three_stage_mmgbsa_workflow_instance_1` | 33098 | 2703 |
| `life_sciences/merfish_image_decoding_segmentation_1` | 33112 | 2982 |
| `life_sciences/tcga_brca_deg_analysis` | 33124 | 3196 |
| `life_sciences/tp53_locus_variant_histone_browser_svg` | 33128 | 3249 |
| `physical_sciences/phonon_dispersion_thermodynamics` | 33152 | 3684 |

日志位于 `/data/ale/my-agent/skills-agent/batches/skills_agent_formal_20261013T090106Z_73666d7/batch.log`。错误均为 `RootlessKit PortManager.AddPort(): ... bind: address already in use`。个别上层分类写为 `auth_failed` 或 `transport_error`，不能据此误诊为 API 密钥失效。旧批次也曾有两题同类冲突。

ALE 的 Docker provider 为 CUA/VNC 使用 `-p 0:<内部端口>`，非零返回直接抛异常。当时冲突端口都在主机临时端口范围 `32768–60999` 内；审计时保留端口列表为空。**bind 冲突已证明，但历史监听者没有被记录，不能断言是残留容器、RootlessKit 泄漏、出站连接或 TIME_WAIT 中的某一项。**

新机需要任务拥有的端口池，绑定 `127.0.0.1`，优先选择临时端口范围之外；如有重叠，应将池加入保留端口设置。分配和归还用跨进程锁，遇到 `EADDRINUSE` 在 Agent 启动前换一个端口并保留诊断。不能让多个 worker 共用全局端口变量或执行全局清理。

原串行控制器还会临时修改共享 provider 配置，不能直接复制后把并发数改成 12。并行版必须隔离 worker、容器拥有权、环境准备与评分目录，并使用资源账本。

详细证据：[environment-failures.json](/Users/apple/dev/learn-ai-agent/tmp/ale01-infra-audit-20261016/environment-failures.json)。

## 4. 四题评分修复：代码错误与部署缺口分别处理

### PDE：结构化匹配要求被当成字符串

`batch.log:1290` 记录 `'dict' object has no attribute 'lower'`。`scripts/score_outputs.py:28` 调用 `phrase.lower()`；候选反馈在第 85 行已转字符串，摘要由 `read_text()` 得到，ALE01 的反馈值也均为字符串。故障来自评分器对结构化要求的处理，不能归为候选提交字典代替文本。

已准备的修复资产将 `_lower_contains` 改为结构化要求的 OR-of-AND 匹配，其余评分 AST 保持一致；不以 `str(dict)` 掩盖问题。部署应固定修复哈希 `29d8613a4677c2c69b59260324834455b1011dc517a7842f6f2938140beb543a`，新机仍需执行独立验收。

### Ranking：官方镜像缓存权限可复现，但历史错误尾部丢失

ALE01 `batch.log:1177–1189` 显示 verifier 在 `prepare_workspace()` 的 `uv sync --frozen` 阶段中断，尚未复制运行候选 `safe_recover.py`。外层只保存 stderr 前 1000 字符并将非 JSON 输出返回 0，掩盖了评分环境故障。

已有 **ALE02 独立 bootstrap 容器**的复现证明，同官方镜像下 UID 1000 无法写 root 所有、模式 0700 的 `/home/user/.cache`：默认缓存命令退出码 2，报 Permission denied。仅改 `UV_CACHE_DIR` 为可写评估目录，原冻结锁命令在 0.191 秒内成功，项目和锁文件哈希不变。保留 uv 0.11.3、其自动选择的 Python 3.14.3、pytest 8.3.5 等原依赖，不用升级依赖来绕过权限错误。

可直接使用已有隔离 overlay 的 `runtime-cache.patch`，并在每个 worker 配独立缓存、venv 和评估目录。这个复现为 ALE01 的历史故障提供了高度匹配的解释，但 ALE01 原错误尾部已被截断，不能声称它是当时唯一的底层原因。Agent 侧 EOF 也需要作为独立问题保留。

### LTMLE：缺少 Rscript wrapper，回退至不匹配的默认 R

ALE01 `batch.log:2211` 的公开摘要检查通过，但第 2240 行隐藏评分环境报 `r_library_prepare_failed`，固定 `lmtp 1.4.2` 的 CRAN 解析失败；当时尚未执行候选 R 脚本。这不是候选自行选择了错误依赖。

任务数据缺 `base/software`，原 `main.py:421–422` 找不到 `software/Rscript` 就回退 `/usr/bin/Rscript`。保存的失败日志出现 R 4.5.3 编译路径；评分器原代码则声明使用 `/opt/R/4.3.2` 的兼容预装库。已有 ALE02 bootstrap 证据确认：同官方镜像内正确的 R 4.3.2 与公开 manifest 所列全部 41 个包均已存在，包括本地 lmtp 1.4.2，版本零不匹配，没有安装或升级任何包。

修复是将现有 `software/Rscript` wrapper 以模式 **0755** 放入该任务的隔离软件目录，让原 `main.py` 通过已有路径选中 `/opt/R/4.3.2/bin/Rscript`；wrapper SHA-256 为 `9c5b2ec95a34806f288211c44c746be9c1a54d4ab61b0b61a2e2fd2581a62849`。无需修改评分 Python、放宽数值阈值或更换包版本。新机每个评分容器仍须跑已有 `verify_runtime.py`，核验 R 4.3.2 与 41 个公开 manifest 版本。

ALE01 历史路径和日志强匹配这个部署缺口；成功依赖探针来自 ALE02，不能冒充 ALE01 历史重现。

### DiT：评分 stub 缺公开导出，装真实包也无效

ALE01 `batch.log:782` 的具体错误是从顶层 `diffusers` 导入 `AutoencoderKL` 失败，`cases=[]`。ALE02/ALE03 另遇到 `diffusers.models.DiTTransformer2DModel` 缺失，是相关但不同的导入错误。公开 starter 本来就依赖这两个模型接口；评分器使用 `sys.modules.update(injected)` 替换真实包，却没有提供完整公开导出。

现有 **v2** 修复只补三个公开导出 alias，覆盖顶层及 `models` 路径，评分行为、权重、阈值和候选均不改变；固定文件哈希为 `4a150dd2bf2cfc7320ba9fe88c7dd41faeb4486de248d1956abafae78e89d56b`。本地合成测试记录 9/9 组通过，运行环境是 macOS，不代表新机官方容器已通过验收。单独 `pip install diffusers` 不能解决被 stub 覆盖的问题。

四题详细证据与推断边界：[grader-failures.json](/Users/apple/dev/learn-ai-agent/tmp/ale01-infra-audit-20261016/grader-failures.json)、[bootstrap 补充证据](/Users/apple/dev/learn-ai-agent/tmp/ale01-infra-audit-20261016/grader-bootstrap-addendum.json)。

修复资产清单：[PDE](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/grader-fixes/pde/manifest.json)、[Ranking](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/grader-fixes/ranking/manifest.json)、[LTMLE](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/grader-fixes/ltmle/manifest.json)、[DiT v2](/Users/apple/dev/learn-ai-agent/tmp/ale-recovery-20261016/grader-fixes/dit/manifest.json)。

## 5. OOM、安装与磁盘：不要把资源限制误认为主机故障

本轮内核记录到三个任务容器中的四次 `CONSTRAINT_MEMCG` OOM：`cp_test_gen_1` 两次、`humanoid_wbc_policy_evaluation` 一次、`prostate_imrt_matrad_reproduction` 一次。它们对应容器的内存配额限制，不是主机整体 OOM 的证据，也不自动等于三道题的最终失败都由环境造成。给宿主机加内存而保留单题配额，仍可能发生候选算法超出容器额度。需要保存 `memory.current`、`memory.peak`、`memory.events`、OOMKilled 和进程退出码，不能只看整机剩余内存。

本轮 89 次实际进入部署的 skills-agent 安装全部成功；另外六题在容器启动阶段已失败。89 份安装日志也都提示 pip 缓存不可写并禁用缓存，这是重复下载与复现成本问题，目前没有证据说它直接造成了安装失败。新机应使用可写的 worker 缓存或预先验证的 wheelhouse，固定 wheel、依赖与 tokenizer 哈希。

环境审计时 ALE01 剩余容器数为 0，`/data` 磁盘使用 55%、inode 使用 8%。框架日志未找到磁盘满、Agent 安装失败或 owned-container 清理失败的签名。**当前健康快照不能证明历史没有资源峰值、短暂磁盘压力或未记录的问题。**

## 6. ALE04 计划配置与启动前验收

用户最终新增的服务器为 **ALE04 `8.209.250.103`，64 vCPU / 256 GiB**，已取代此前 48 vCPU / 192 GiB 的设想。计划最多 **12 个任务槽位**；沿用既有每题资源协议时，最坏同时容纳两道特殊题与十道普通题合计 **52 vCPU、206 GiB**，为系统、Docker、控制器和评分保留余量。这里包含此前已记录的两题 28 GiB 内存覆盖；不能在未记录情况下改回其他额度。

槽位仅是上限，实际调度必须同时满足 CPU、内存、磁盘和模型吞吐预算。准备与评分阶段也会消耗资源。10 小时属于运行目标，不能只凭槽位数作硬保证；上游故障、单题接近完整时限及新 131072 输出上限都可能改变总时间。

正式批次固定：

```toml
# skills-agent source
# 6b1c55e2aaa07c246a949307c4f40844ef35b53d
context_tokens = 524000
model_context_window = 524000
max_output_tokens = 131072
summary_output_tokens = 8192
```

模型沿用 `deepseek-flash`，并记录实际 provider、effort、请求格式与返回的有效参数。固定同一 95 题、variant 0、任务数据版本及单题时限；官方基础镜像必须使用完整 digest：

```text
agentslastexam/ale-ubuntu22-docker@sha256:78ec11afeb0008ed8bc2b59cf9c90c05e63d1ac66b9d3e7cb0fada10695fca6f
```

以下保留审计时制定的启动检查清单。实际已完成的项目、检查方法和验证边界以 [ALE04 启动记录](ale04-evaluation-launch-2026-10-16.md) 为准；未将合成测试或公共依赖探针等同于所有题目最终评分已经通过：

- [ ] 核实真实 CPU、可用内存、数据盘与 inode；Docker rootless 用户/socket、cgroup v2 和 CPU/内存限制实际生效；保存镜像 digest 与资源清单。
- [ ] 固定任务数据版本、95 题名单、variant、时限、两项内存覆盖和排序；冻结 skills-agent 源码、wheel、tokenizer、adapter、控制器及评分 overlay 哈希。
- [ ] 端口池与分配锁通过目标 12 并发的反复创建、就绪、执行、采集、删除测试；覆盖故意启动失败和取消；要求无端口冲突、无误删其他 worker、无残留容器或端口。
- [ ] 调度器按完整生命周期记账，能在资源不足时排队；每个 worker 的任务目录、venv、缓存、R 库、评分 scratch 与日志独立；禁止全局清理和共享可变 provider 配置。
- [ ] 89 份历史安装日志暴露的缓存权限问题已处理；正式安装使用可写缓存或验证过的 wheelhouse，依赖和 tokenizer 可追溯。
- [ ] Ranking 依赖探针在正式用户及实际任务路径下成功，冻结锁和项目哈希不变；完整 stdout/stderr 和退出码落盘。
- [ ] LTMLE wrapper 存在、可执行且选中 R 4.3.2；41 个公开 manifest 包版本一致，无默认 R 4.5.3 回退或临时 CRAN 替代。
- [ ] PDE 与 DiT 部署哈希对应固定修复版；在实际负责评分的宿主 ALE Python 中执行合成接口与行为验收，失败正反例均能正确区分。容器中的依赖准备另用固定官方镜像验收。
- [ ] wrapper 验证全部四个 token 参数；真实 API 烟测接受 `max_tokens=131072`，认证和余额正常；按 1→2→4→12 逐级观察任务与请求吞吐，冻结重试、全局熔断及恢复规则。
- [ ] 保存任务内存峰值/OOM 事件、宿主资源压力、端口冲突时的拥有者、全量评分诊断；分别记录运行状态、基础设施有效性和评分状态。
- [ ] supervisor、报告器、取消与故障恢复验收通过；正式启动后保存批次 ID、PID 身份、开始时间、12 槽位配置、版本和退出码规则。

## 7. 与旧结果比较的规则

原始批次、产物和原分数保留。对 PDE、Ranking、LTMLE、DiT，比较各客户端时应统一使用同一修复版评分协议，重评未改动的原产物；不能只给新 Agent 使用修复环境、同时保留旧 Agent 的故障零分作为能力对照。

对于容器未启动或已明确外部服务中断的旧尝试，可按预先声明的规则放入独立恢复批次，保留新旧 run ID 和原因。真实候选失败、质量零分或时限耗尽不能择优重试。评分器准备异常应为未核验/未评分，不能静默转成有效数值零，也不能悄悄从分母删除；最终应同时报告覆盖率，并在共同核验题目集合比较。

本次从串行变为并行且输出上限改变，需明确标记为新配置批次。保留单题配额能控制一部分变量，但宿主资源竞争与 API 请求竞争仍需记录；总批次时间缩短不能全部归因于 Agent 能力提升。
