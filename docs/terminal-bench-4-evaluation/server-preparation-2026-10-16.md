# 三台 Terminal-Bench 服务器的准备记录

日期：2026-10-16（Asia/Shanghai）。实验：`tb4-56-flash-max-v2`。

## 补充的基础信息

用户在 `tbench 服务器.txt` 中提供站点域名 `https://rehdasu.cn/`、模型 `deepseek-flash` 以及三把按服务器命名的 API key。评测思考档位按会话约定为 **max**。域名仅登记为后续站点地址，其解析、HTTPS 和站点部署尚未核验。

三把 key 分别对应同名服务器。评测文档与 `servers.json` 仅保存 `tbench-1`、`tbench-2`、`tbench-3` 这些凭据引用，实际值仍保留在用户提供的本机笔记中，尚未下发服务器。

## 服务器分配

| 服务器（名称 / IP / 原编号） | 计划运行的 Agent | 系统 | CPU | 系统可见内存 | 数据盘 |
| --- | --- | --- | ---: | ---: | ---: |
| `tbench-1` / `47.91.21.152` / serverA | Codex CLI | Ubuntu 22.04.5 LTS x86_64 | 32 vCPU | 约 123.66 GiB | 500 GiB |
| `tbench-2` / `8.216.13.123` / serverB | Claude Code | 同上 | 32 vCPU | 约 123.66 GiB | 500 GiB |
| `tbench-3` / `8.216.11.161` / serverC | skills-agent | 同上 | 32 vCPU | 约 123.66 GiB | 500 GiB |

已通过本机 root SSH 访问三台。公开 Harbor 和 Terminal-Bench 源码使用 HTTPS 下载；skills-agent 在本机构建 wheel 后上传。没有向服务器复制个人 GitHub 私钥、配置 GitHub 登录或启用 SSH agent forwarding。

## 磁盘与资源

三台 `/dev/vdb` 均为 536,870,912,000 字节。初始化前核实无分区、无文件系统签名，首尾各 1 MiB 均为零；随后创建 ext4、挂载为 `/data`，并以 UUID 写入 `/etc/fstab`。

资源边界为 `tb4.slice`：`AllowedCPUs=2-31`、`CPUQuota=3000%`、`MemoryMax=115G`、`MemorySwapMax=0`。CPU 0–1 和至少 8 GiB 实际内存留给系统及其它服务。任务准入预算为 28 vCPU / 107 GiB；最多 14 个活动 trial，仍需外层资源调度器实现加权准入。

Docker、containerd 服务及 Docker 默认创建的容器均进入此 slice。已用真实 Alpine 容器验证进程 cgroup 路径位于 `/tb4.slice/`，CPU 亲和性为 `2-31`，并核实父级内存上限。正式控制器通过 root 入口 `/usr/local/sbin/tb4-exec` 以 `tbench` 用户运行，避免控制进程落在限制之外。

## 固定的软件与源码

| 组件 | 版本或 commit |
| --- | --- |
| Docker Engine / CLI | `29.9.1` |
| Docker Compose plugin | `5.6.0` |
| containerd | `2.4.2` |
| Docker Buildx | `0.38.0` |
| uv | `0.13.1`，下载 wheel 校验 SHA256 |
| Python | `3.12.15`，uv 管理的独立运行时 |
| Harbor | `0.24.0`；`aad19e235922ee5007ac635a96a1c2f72eef9dfc` |
| Terminal-Bench | `v4.0.0`；`452bf305c6daa62fc59061d22133a7cbc7c1572e` |
| skills-agent 发布包 | `skills_cli_agent-0.1.0-py3-none-any.whl` |

Docker 相关五个包已记录完整 Debian 版本并设为 hold。Harbor 从固定源码和锁文件安装宿主 CLI 依赖，未安装 cloud/dev extras。安装与版本记录以服务器实际文件为准。

## 目录与入口

| 内容 | 路径 |
| --- | --- |
| Docker 数据 | `/data/docker` |
| containerd 镜像与快照 | `/data/containerd` |
| 评测用户 home | `/data/home/tbench` |
| 固定源码 | `/data/tb4/src/{harbor,terminal-bench}` |
| Harbor 虚拟环境 | `/data/tb4/venvs/harbor` |
| Harbor 入口 | `/data/tb4/bin/harbor` |
| 受资源限制的 root 启动入口 | `/usr/local/sbin/tb4-exec` |
| Python / uv 缓存 | `/data/tb4/python`、`/data/tb4/cache/uv` |
| 方案、清单、配置 | `/data/tb4/plans` |
| 执行结果 / 日志 | `/data/tb4/runs`、`/data/tb4/logs` |
| 发布资产 | `/data/tb4/releases`，skills-agent wheel 已上传 serverC |
| 系统核查记录 | `/data/tb4/system-records/environment.json`、`runtime-versions.json` |
| Harbor 安装日志和依赖清单 | `/data/tb4/artifacts/runtime/` |

基础入口核查示例（不调用模型）：

```sh
ssh root@47.91.21.152 \
  '/usr/local/sbin/tb4-exec /data/tb4/bin/harbor run --help'
```

## 后续执行前的工作

尚需完成 Codex 模型目录注入、skills-agent 的 Harbor 适配、资源加权调度器、9 道多容器题资源标定和 56 题环境预热。DeepSeek tokenizer 尚未就位；Agent CLI 的精确版本、模型请求参数和任务工具接入需在合成 smoke 中验收。

后续模型调用需要将用户已提供的三把 DeepSeek API key 按上述映射分别注入；GitHub 凭据不是当前准备工作的依赖。准备阶段没有下发模型 key、发送模型推理请求或启动正式 trial。

可复用的准备脚本为 [prepare-base.sh](prepare-base.sh)、[prepare-runtime.sh](prepare-runtime.sh)，核查脚本为 [inspect-environment.py](inspect-environment.py)。这些脚本仅适用于本文已核实的环境，不应用于含现有数据或现有 Docker 工作负载的服务器。
