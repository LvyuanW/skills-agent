# skills-agent 在 ALE 服务器上的测评启动流程

核查日期：2026-10-13，时区 Asia/Shanghai。适用服务器：`8.211.167.153`。本文依据服务器实际配置、启动器、运行日志和评分记录编写；原始服务器笔记仅用作连接线索，里面的“尚未执行真实测评”等旧状态已过时。

## 1. 已有正式测评与标准 Docker 核查

**结论：已有正式测评确实使用了 ALE 官方 Docker 基础镜像，并固定了 digest。** 但它是本机筛选的 95 题、variant 0 测评，包含资源覆盖和框架兼容补丁，不能称为“全部官方默认条件下的 ALE 全量测评”。

| 项目 | 服务器核查结果 |
| --- | --- |
| 批次 | `skills_agent_formal_20261010T231233Z` |
| 实际运行时间（北京时间） | 2026-10-11 07:23:20 至 2026-10-13 10:53:02 |
| ALE 源码基线 | `d9abc0734b56ea34116c5bfcbdd0b808269ab9e2`，有本地补丁 |
| Agent release | `20261010T224923Z-40f68f26f4b3` |
| Agent 版本证据 | release 记录 Git HEAD 为 `68676abbe5c853952b4303943ea6ba1b3e2f4cfa`，且 `working_tree_changes_included: true`；复现以 wheel 的 SHA256 为准，不能仅按此 commit 重建 |
| 模型 | `deepseek-flash`，`effort=low` |
| 协议 | OpenAI compatible，`https://api.deepseek.com`，`api_format=chat` |
| 范围 | 官方 Docker 99 题中的本机可运行 95 题，各 variant 0；非全部 ALE 152 题 |
| 执行 | 串行，并发 1，每题最多一次尝试；无低分自动重试 |
| 结束状态 | `state=complete`，95/95 已结束，无 pending/nonterminal |
| 框架记录为评分成功的有限结果 | 91/95，覆盖率 95.789%；这 91 题原始平均分 0.535934 |
| Agent 执行状态 | completed 72、failed 12、timeout 11；这些状态与评分成功是不同指标 |

2026-10-14 补充审计：ranking 题评分器环境准备失败被包装层转换为 0 分，所以上表“评分成功”只是框架记录，不能全部当作有效能力评分。新旧复测的耗时、共同题目比较和 trace 证据见 [阶段结果分析](ale-comparison-2026-10-14.md)。

实际基础镜像：

```text
agentslastexam/ale-ubuntu22-docker@sha256:78ec11afeb0008ed8bc2b59cf9c90c05e63d1ac66b9d3e7cb0fada10695fca6f
```

证据链（以下绝对路径均在服务器上）：

1. `/data/ale/agents-last-exam/skills_agent_formal_20261010T231233Z.yaml:5` 指向专用环境配置。
2. `configs/environments/docker.skills-agent.formal.skills_agent_formal_20261010T231233Z.yaml:1–13` 将 `cpu-free-ubuntu` 映射到 `provider: docker`，并固定上述 `image_ref`。
3. `/data/ale/my-agent/skills-agent/batches/skills_agent_formal_20261010T231233Z/batch.log` 中有 **95 次 Creating Docker container**，全部使用上述 digest；93 次成功启动，93 次删除。第一组证据在第 99–101 行，最后一组在第 4165–4167 行。两次创建因 RootlessKit 本地端口占用失败。
4. 当前 Docker image 的 RepoDigests 与此 digest 一致；Docker 是 `ale` 用户的 rootless daemon。历史容器均已删除，无法补做逐容器 `docker inspect`，历史实际启动以日志为证。
5. release 的 `Dockerfile` 曾尝试构建 `skills-agent-ale:...` 派生镜像，但构建被取消；`manifest.json` 的 `image` 和 `base_image` 最终都指向官方镜像，`deployment=runtime-assets-wrapper`。不能用这个 Dockerfile 推断正式测评用了自制镜像。

“标准”的具体边界：

- 官方基础系统、应用和 CUA 来自该固定镜像。每题新建容器，运行时再安装 Agent，复制 wheel/tokenizer。这是 Agent 部署动作，因此运行后的文件系统当然会变化。
- `sec_10k_financial_parsing` 从 task card 推导的 31g 改为 28g；`computational_materials_science` 从 30g 改为 28g，后者因端口冲突未成功启动。覆盖发生在批次控制器中，环境 YAML 本身没有该字段，单看 YAML 会漏掉这一点。
- ALE 的 sandbox 取消清理逻辑有补丁；`silicon_bse_absorption` 使用 `CompatSession` 适配任务 hook 的命令返回接口。不能把当前仓库说成“未修改的官方 ALE”。
- 使用 Docker 支持的 Linux 子集，不支持据此推断 Windows、GPU 或完整虚拟机任务的表现。官方 Docker provider 也明确与宿主机共享内核。[ALE 官方 Docker 说明](https://agents-last-exam.org/docs/ale/pages/local-docker.html)

93 个成功启动任务的事件记录均显示 `reference_stage_completed` 晚于 `agent_finished`，符合 ALE 分阶段装载参考数据的流程。这是装载时序证据，不是对历史容器全部文件或所有可能参考来源的完整审计。

如果下一轮要求**保持 task card 默认资源**，需要去掉两个内存覆盖，并为高内存题安排足够大的主机，或事先单独定义兼容子集。应另建协议和批次，不能把修改后的成绩混进这次历史结果。

## 2. 环境、目录和 skills-agent 调用链

日常运行身份是 `ale`，不是 root。SSH 登录 root 仅用于进入服务器，随后 `su - ale`。当前系统 Python 为 3.10.12，ALE 虚拟环境 Python 为 3.12.15；用 `uv run --no-sync python` 调度 ALE。

| 内容 | 实际路径 / 状态 |
| --- | --- |
| 主机 | Ubuntu 22.04，8 vCPU，约 32 GiB 内存 |
| 数据盘 | `/dev/vdb1` 挂载 `/data`，ext4；核查时约 229 GiB 可用 |
| Docker | 29.9.0，rootless，cgroup v2，存储 `/data/docker` |
| ALE 仓库 | `/data/ale/agents-last-exam` |
| Agent 发布包 | `/data/ale/my-agent/skills-agent/releases/<release_id>`；这里不是 Git checkout |
| 批次控制记录 | `/data/ale/my-agent/skills-agent/batches/<batch_id>` |
| 每题结果 | `/data/ale/runs/<batch_id>` |
| 任务数据 | `/data/ale/task-data` |
| 宿主机环境变量 | `/home/ale/.config/ale-env.sh`，包含 rootless Docker socket 和缓存位置 |
| 模型凭据 | ALE 仓库的 `secret/.env`，配置引用 `${env:DEEPSEEK_API_KEY}` |
| 浏览记录 | [任务与运行记录](http://8.211.167.153/tasks/#runs)；评分与日志核查仍以原始文件为准 |

调用顺序：

```text
ale 用户 / uv / batch_controller.py
  → 读取实验、agent、environment、manifest，校验配置哈希及资源
  → ALE DockerProvider 为每题创建官方镜像容器
  → 清理新容器内 /home/user/.ale 的遗留历史
  → ALE 准备 input/software，执行任务 setup
  → release/run_ale.py 上传并校验 wheel + tokenizer
  → SandboxExecutor 在任务容器内运行 SkillsAgentDeployer
  → pip install wheel[deepseek]
  → python -m skills_agent.benchmark，接收 prompt/workspace/log-dir
  → Agent 完成或超时，清理其进程
  → ALE 准备 reference 并调用官方任务评分器
  → 回收日志、轨迹、产物、评分，再删除容器
```

`executor: sandbox` 表示 Agent 在 ALE 分配的任务环境内执行；真正选择容器的是 environment 中的 `provider: docker`。不需要给 skills-agent 配 `--sandbox-image`，那是另一条工具沙箱机制。

此 release 的实际 Agent 参数为：

```yaml
executor: sandbox
model: deepseek-flash
config:
  provider: openai
  base_url: https://api.deepseek.com
  api_key: ${env:DEEPSEEK_API_KEY}
  api_format: chat
  token_counter: deepseek
  tokenizer_encoding: v41
  context_tokens: 524000
  model_context_window: 524000
  max_output_tokens: 32768
  summary_output_tokens: 8192
```

`package` 与 `tokenizer_path` 指向容器内 `/home/user/.ale/skills-agent-assets/<release_id>/`。`run_ale.py` 从宿主机 release 上传它们，并注入 `effort=low`。不能跳过 wrapper 直接运行普通 `python -m ale_run run ...`，否则这些容器内路径未被准备。

宿主机项目的 `skills-agent.toml` 不是此次正式运行参数来源。当前本地 skills-agent HEAD 为 `73666d7f2b02c68f64c7343f6301211bb4ff4c43`，与历史 wheel 也不是同一版本。升级 Agent 时须新建 release、更新哈希和路径、验证 adapter 兼容性，再做 smoke。

## 3. 启动前检查

以下命令是**下次启动时执行的流程**。本次核查只读取历史证据并执行了旧批次 `--dry-run`，没有启动新测评、调用模型或修改服务器配置。

### 3.1 登录并确认环境

```bash
ssh root@8.211.167.153
su - ale
source /home/ale/.config/ale-env.sh
cd /data/ale/agents-last-exam

id
findmnt /data
df -h /data
uv run --no-sync python --version
docker info --format 'Root={{.DockerRootDir}} Cgroup={{.CgroupVersion}} Security={{json .SecurityOptions}}'
docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
git rev-parse HEAD
git status --short
test -r secret/.env
```

应看到 `ale`、`/data` 已挂载、Python 3.12、Docker rootless 和 `/data/docker`。如已有其他批次运行，应避免在这台 8 核主机上叠加启动。现有虚拟环境已安装依赖；复现历史版本时不要无条件执行 `uv sync` 或升级仓库。

### 3.2 固定并验证基础镜像与 release

```bash
export ALE_REPO=/data/ale/agents-last-exam
export RELEASE_DIR=/data/ale/my-agent/skills-agent/releases/20261010T224923Z-40f68f26f4b3
export OLD_BATCH_DIR=/data/ale/my-agent/skills-agent/batches/skills_agent_formal_20261010T231233Z

docker image inspect \
  agentslastexam/ale-ubuntu22-docker@sha256:78ec11afeb0008ed8bc2b59cf9c90c05e63d1ac66b9d3e7cb0fada10695fca6f \
  --format 'ID={{.Id}} Digests={{json .RepoDigests}} OS={{.Os}} Arch={{.Architecture}}'

uv run --no-sync python - <<'PY'
import hashlib, json, os
from pathlib import Path
r = Path(os.environ['RELEASE_DIR'])
m = json.loads((r / 'manifest.json').read_text())
for name, key in [(m['wheel'], 'wheel_sha256'), ('tokenizer.json', 'tokenizer_sha256')]:
    assert hashlib.sha256((r / name).read_bytes()).hexdigest() == m[key], name
print('wheel / tokenizer SHA256 verified')
PY

uv run --no-sync python "$OLD_BATCH_DIR/batch_controller.py" \
  --spec "$ALE_REPO/skills_agent_formal_20261010T231233Z.yaml" \
  --manifest "$OLD_BATCH_DIR/batch-manifest.json" --dry-run
```

最后一条已在 2026-10-13 实际验证通过，返回 `validated_count=95`、variant 0、concurrency 1、max_attempts 1。它验证配置哈希、任务展开和资源约束，不验证模型服务、容器启动或评分器能否工作。

固定资产：wheel SHA256 为 `40f68f26f4b37ea72e4e85f43a9bfb3b332aaa957ecef1329289cd7c1fe31c51`；tokenizer SHA256 为 `81f64d1248a68ce3663e07ab3ee48b851e5df0e32d27cb98e4c9a268151e8d99`。依赖仍在容器内通过 pip 安装，wheel 固定并不等于所有第三方依赖都已锁定；须保存每题 `install.log`，更严格复现时另建固定依赖的 release。

数据版本见 `/data/ale/runs/dataset-source.json`：HF revision `c5cfd7a8fa41d3c86afbeac23764f47932b2101e`，归档 SHA256 `0bfd0fe8d7d0bb5840097a8db233ec26f46707f4e1e90925692a12cfad7afb6f`。这是下载归档的记录，不等于本次重新哈希了所有已解压文件。任务代码或数据变更后应重新核对二者匹配关系。

不要把整个 `/data`、Docker socket 或 `reference` 提前交给 Agent。保持 ALE 的输入/评分参考分阶段装载，由外层评分器使用参考答案。

## 4. 为新一轮创建独立批次

下面保留历史 95 题、旧 release 和两个 28g 覆盖，适合**同协议复测**。只创建新配置与新目录，不复用旧 `progress.json`，也不覆盖历史成绩。若要升级 Agent、改变资源协议或改变任务范围，先更新 release/清单，再生成新 manifest，不能临时修改正在运行的配置。

先在上面的 `ale` shell 中设置唯一批次 ID：

```bash
export BATCH_ID="skills_agent_formal_$(date -u +%Y%m%dT%H%M%SZ)"
export BATCH_DIR="/data/ale/my-agent/skills-agent/batches/$BATCH_ID"
```

生成新的配置、manifest 和启动脚本：

```bash
uv run --no-sync python - <<'PY'
import datetime, hashlib, json, os, shlex, shutil
from pathlib import Path
import yaml

repo = Path(os.environ['ALE_REPO'])
old = Path(os.environ['OLD_BATCH_DIR'])
dest = Path(os.environ['BATCH_DIR'])
batch = os.environ['BATCH_ID']
m = json.loads((old / 'batch-manifest.json').read_text())
assert str(Path(m['release_dir'])) == os.environ['RELEASE_DIR']
source_spec = Path(m['experiment_spec'])
assert hashlib.sha256(source_spec.read_bytes()).hexdigest() == m['experiment_spec_sha256']
for name, digest in m['config_sha256'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == digest, name
spec = yaml.safe_load(source_spec.read_text())
agent_path = repo / f'configs/agents/skills_agent.deepseek.formal.{batch}.yaml'
env_path = repo / f'configs/environments/docker.skills-agent.formal.{batch}.yaml'
spec_path = repo / f'{batch}.yaml'
assert not any(p.exists() for p in [dest, agent_path, env_path, spec_path, Path('/data/ale/runs') / batch])
dest.mkdir(mode=0o700)
shutil.copy2(repo / spec['agents'][0], agent_path)
shutil.copy2(repo / spec['environment'], env_path)
for name in ['batch_controller.py', 'compat_session.py', 'summarize_audited.py']:
    shutil.copy2(old / name, dest / name)
spec.update(name=batch, agents=[str(agent_path)], environment=str(env_path))
spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))
m.update(batch_id=batch, remote_batch_dir=str(dest), experiment_spec=str(spec_path),
         created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
         experiment_spec_sha256=hashlib.sha256(spec_path.read_bytes()).hexdigest())
pinned = [agent_path, env_path, dest / 'batch_controller.py', dest / 'compat_session.py',
          Path(m['release_dir']) / 'run_ale.py']
m['config_sha256'] = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in pinned}
(dest / 'batch-manifest.json').write_text(json.dumps(m, indent=2) + '\n')
command = shlex.join(['uv', 'run', '--no-sync', 'python', str(dest / 'batch_controller.py'),
                     '--spec', str(spec_path), '--manifest', str(dest / 'batch-manifest.json')])
audit = shlex.join(['uv', 'run', '--no-sync', 'python', str(dest / 'summarize_audited.py'),
                   '--manifest', str(dest / 'batch-manifest.json'), '--progress', str(dest / 'progress.json')])
(dest / 'run.sh').write_text(
    '#!/usr/bin/env bash\nset -uo pipefail\numask 077\n'
    'source /home/ale/.config/ale-env.sh || exit 1\n'
    f'cd {shlex.quote(str(repo))} || exit 1\n'
    f'exec 9> {shlex.quote(str(dest / "launcher.lock"))}\n'
    'flock -n 9 || exit 2\n'
    f'{command} >> {shlex.quote(str(dest / "batch.log"))} 2>&1\n'
    'result=$?\n'
    f'printf "%s\\n" "$result" > {shlex.quote(str(dest / "launcher.exitcode"))}\n'
    f'{audit} >> {shlex.quote(str(dest / "audit.log"))} 2>&1\n'
    f'printf "%s\\n" "$?" > {shlex.quote(str(dest / "audit.exitcode"))}\n'
    'exit "$result"\n')
print('Prepared', dest)
PY

uv run --no-sync python "$BATCH_DIR/batch_controller.py" \
  --spec "$ALE_REPO/$BATCH_ID.yaml" \
  --manifest "$BATCH_DIR/batch-manifest.json" --dry-run
```

检查输出仍是预期的 95 题；确认磁盘有余量。控制器逐题要求可用空间至少 `max(30 GiB, 2 × 该题 input 字节数 + 10 GiB)`。它不会替你判断 API 余额是否足够覆盖整轮。

## 5. 先 smoke，再启动正式运行

Smoke 也使用正式批次控制器，由它导入 release wrapper，从而覆盖相同的容器历史清理、资产装载和进程清理链路。为单题建立独立 controller/manifest/进度目录：

```bash
export SMOKE_DIR="$BATCH_DIR/smoke"
uv run --no-sync python - <<'PY'
import datetime, hashlib, json, os, shutil
from pathlib import Path
import yaml
repo = Path(os.environ['ALE_REPO'])
parent = Path(os.environ['BATCH_DIR'])
dest = Path(os.environ['SMOKE_DIR'])
name = os.environ['BATCH_ID'] + '_smoke'
task = 'computing_math/os_log_permission_guard_v1'
assert not dest.exists() and not (Path('/data/ale/runs') / name).exists()
m = json.loads((parent / 'batch-manifest.json').read_text())
assert task in m['eligible_tasks']
spec = yaml.safe_load(Path(m['experiment_spec']).read_text())
spec.update(name=name, tasks=[{'path': task, 'variants': [0]}], wall_time_s=1800,
            secret_file=str(repo / 'secret/.env'), concurrency=1, max_attempts=1,
            auto_resume=False, cleanup_mode='delete', output={'root': '/data/ale/runs'})
dest.mkdir(mode=0o700)
for filename in ['batch_controller.py', 'compat_session.py', 'summarize_audited.py']:
    shutil.copy2(parent / filename, dest / filename)
p = dest / 'smoke.yaml'
p.write_text(yaml.safe_dump(spec, sort_keys=False))
m.update(batch_id=name, remote_batch_dir=str(dest), experiment_spec=str(p),
         created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
         experiment_spec_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
         eligible_tasks=[task], input_bytes_by_task={task: m['input_bytes_by_task'][task]},
         resource_memory_caps={}, compat_session_tasks=[])
pinned = [Path(spec['agents'][0]), Path(spec['environment']), dest / 'batch_controller.py',
          dest / 'compat_session.py', Path(m['release_dir']) / 'run_ale.py']
m['config_sha256'] = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in pinned}
(dest / 'batch-manifest.json').write_text(json.dumps(m, indent=2) + '\n')
PY

uv run --no-sync python "$SMOKE_DIR/batch_controller.py" \
  --spec "$SMOKE_DIR/smoke.yaml" --manifest "$SMOKE_DIR/batch-manifest.json" --dry-run
# 从下一条开始会创建容器并调用模型，产生费用。
uv run --no-sync python "$SMOKE_DIR/batch_controller.py" \
  --spec "$SMOKE_DIR/smoke.yaml" --manifest "$SMOKE_DIR/batch-manifest.json" \
  >> "$SMOKE_DIR/batch.log" 2>&1
jq '{state,scored_count,mean_score,infra_or_ungraded_tasks}' "$SMOKE_DIR/results_summary.json"
```

Smoke 固定 `computing_math/os_log_permission_guard_v1`、variant 0、1800 秒、单次尝试。检查实际容器日志、`install.log`、Agent 模型/工具调用、产物、`run.json` 和 `eval_result.json`。模型鉴权/余额错误、缺少 tokenizer、安装失败或评分器错误均应先解决，不能以 `dry-run` 通过代替真实 smoke。Smoke 成绩单独存放，不合并进正式 95 题统计。

Smoke 通过后，在 tmux 启动正式批次：

```bash
tmux new-session -d -s "$BATCH_ID" bash "$BATCH_DIR/run.sh"
tail -f "$BATCH_DIR/batch.log"
```

新批次启动脚本和生成步骤已做本地语法及临时目录验证，尚未在服务器执行新一轮真实测评。复制流程时应逐段确认输出，不要整篇不加检查地连续运行。

正式实验没有统一设置 `wall_time_s`，继承各题 task card 的时限，不能把 smoke 的 1800 秒当成整轮或所有题的时限。控制器的 `complete`/退出 0 只代表队列处理结束，允许其中有失败或未评分题。

## 6. 运行监控、停止与结果验收

```bash
jq '{state,eligible_count,terminal_count,scored_count,coverage,mean_score,
     pending_tasks,nonterminal_tasks,infra_or_ungraded_tasks}' "$BATCH_DIR/results_summary.json"
docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
df -h /data
```

运行过程中可记录容器的白名单字段，给下一次审计留下创建日志之外的证据：

```bash
while IFS= read -r container; do
  docker inspect --format \
    '{{.Name}} ImageID={{.Image}} Ref={{.Config.Image}} Memory={{.HostConfig.Memory}} NanoCpus={{.HostConfig.NanoCpus}} Privileged={{.HostConfig.Privileged}} Mounts={{json .Mounts}}' \
    "$container"
done < <(docker ps --format '{{.Names}}' | grep '^ale-')
```

不要导出完整 `docker inspect` 或展开后的 agent config，它们可能包含环境凭据。正式批次的完整源码差异、adapter、`_sandbox_stop.py`、启动器和依赖安装记录也应归档；只存 Git HEAD 不够。

需要停止时先确认本批次的 controller PID，再给该进程发 SIGINT/SIGTERM，让控制器清理它记录的 owned containers。不要用 `docker rm -f $(docker ps -aq)` 清空所有容器。若进度留下 `started`/`uncertain`，先核对该题是否已调用模型和写出成绩，不能删除 checkpoint 后自动重跑。控制器会跳过已有终态结果；完成的旧批次不是新一轮复测入口。

结束后查看：

```bash
cat "$BATCH_DIR/launcher.exitcode"
cat "$BATCH_DIR/audit.exitcode"
jq '{state,eligible_count,terminal_count,scored_count,coverage,mean_score,
     excluded_count,ungraded_count,pending_count,nonterminal_count}' "$BATCH_DIR/audited_summary.json"
find "/data/ale/runs/$BATCH_ID" -name run.json -o -name eval_result.json
```

逐题结果目录中，`run.json` 记录执行状态，`eval_result.json` 记录评分器结果；`trajectory.json`、`origin_log/.../session/events.jsonl` 与实际输出用于追查行为。均分只统计 `eval_status=success` 且分数有限的题，必须同时报评分覆盖率、失败/未评分数及排除原因。`completed`、`returned`、退出码 0 或评分器执行成功均不等于题目满分。

旧批次 4 道未评分题：

| 题目 | 原因 |
| --- | --- |
| `business_finance/financial_stmt_reconstruction_aapl_fy2024` | RootlessKit 本地端口 32790 占用，容器启动失败 |
| `physical_sciences/computational_materials_science` | RootlessKit 本地端口 32944 占用，容器启动失败；虽然 termination 被标为 auth_failed，原始错误不是模型鉴权失败 |
| `education_info/homework_grading_numerical_pdes_instance_02` | 评分器错误：`'dict' object has no attribute 'lower'` |
| `engineering/mpc_control_building_v1` | MPC verifier 未生成有效结果 |

历史 `audited_summary.json` 只额外排除显式 HTTP 401/402/余额不足，不是“所有评分器正确性都已审计”的证明。旧批次 91 个评分中有 32 个零分、29 个满分、30 个部分分。上述 0.535934 是这 91 题的均分，不能写成“全部 95 题平均分”或“ALE 全量成绩”。

95 题之外的 Docker 4 题中，`pe_screening_memo_1` 缺少其官方 GPT-4o-mini judge 的 `OPENAI_API_KEY`；`limited_angle_ct_dps_reconstruction`、`idp_ensemble_scoring`、`mose2_bse_absorption_soc` 需要 16 vCPU / 60 GiB，超出当前主机资源。不能静默替换 judge 或压低资源后仍声称同一官方条件。

## 7. 核查范围与来源

本次完成：SSH 只读核查、配置与启动源码阅读、95 份 run/eval 汇总交叉核对、Docker 历史日志与当前镜像检查、wheel/tokenizer 哈希检查、历史 controller dry-run。没有重新评分、启动容器、访问模型 API 或读取任务 reference 答案。

主要服务器证据：

- `/data/ale/my-agent/skills-agent/batches/skills_agent_formal_20261010T231233Z/` 下的 `batch-manifest.json`、`batch-launch.json`、`batch_controller.py`、`compat_session.py`、`batch.log`、`progress.json`、`audited_summary.json`、`execution_status.json`、`eligibility.json`。
- `/data/ale/my-agent/skills-agent/releases/20261010T224923Z-40f68f26f4b3/` 下的 `manifest.json`、`run_ale.py`、wheel、tokenizer、`build.log`。
- `/data/ale/agents-last-exam/` 下的正式实验 YAML、对应 agent/environment YAML、Docker provider、sandbox executor、lifecycle 与官方 task card。
- `/data/ale/runs/skills_agent_formal_20261010T231233Z/` 下逐题 `run.json` / `eval_result.json`。

官方资料：[Docker provider](https://agents-last-exam.org/docs/ale/pages/local-docker.html)、[实验与 provider 选择](https://agents-last-exam.org/docs/ale/pages/providers.html)、[本次 ALE 基线源码](https://github.com/rdi-berkeley/agents-last-exam/tree/d9abc0734b56ea34116c5bfcbdd0b808269ab9e2)。网页会随版本变化，历史复现应以固定提交、镜像 digest、数据版本和批次资产为准。
