import os
from pathlib import Path

from .agent import Agent
from .config import load_agent_config
from .file_history import WorkspaceHistory
from .providers import create_provider
from .sandbox import DockerSandbox
from .skills import SkillRegistry
from .storage import WorkspaceStorage


def add_agent_arguments(parser):
    parser.add_argument("--data-dir", type=Path,
                        help="运行数据根目录，默认 SKILLS_AGENT_DATA_DIR 或 ~/.skills-agent")
    parser.add_argument("--config", type=Path, help="TOML 配置文件，默认读取工作目录中的 skills-agent.toml")
    parser.add_argument("--provider", choices=["openai", "anthropic"])
    parser.add_argument("--model", help="也可设置 OPENAI_MODEL 或 ANTHROPIC_MODEL")
    parser.add_argument("--base-url", help="兼容服务的 API base URL")
    parser.add_argument("--skill-dir", dest="skill_dirs", action="append")
    parser.add_argument("--context-tokens", type=int,
                        help="工作上下文 token 预算（正整数），默认 1000000；达到预算与模型窗口较小值的 80%% 时压缩")
    parser.add_argument("--max-output-tokens", type=int,
                        help="单次输出额度（包含思考），默认 32768")
    parser.add_argument("--summary-output-tokens", type=int,
                        help="辅助生成的输出上限，默认8192 tokens；摘要调用关闭思考")
    parser.add_argument("--api-format", choices=["auto", "responses", "chat"])
    parser.add_argument("--token-counter", choices=["auto", "official", "usage"])
    parser.add_argument("--model-context-window", type=int,
                        help="模型实际窗口；发送输入加输出额度必须装得下")
    parser.add_argument("--sandbox-image", help="提供 sandbox 工具，使用指定 Docker 镜像")


def create_agent(args, session, *, on_event=None, allow_persistent_processes=False):
    args = load_agent_config(args)
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"工作目录不存在：{workspace}")
    prefix = args.provider.upper()
    model_name = args.model
    if not model_name:
        raise ValueError(f"需要配置 model，或设置 --model / {prefix}_MODEL")
    model = create_provider(
        args.provider, model_name, os.environ.get(f"{prefix}_API_KEY", ""),
        base_url=args.base_url,
        max_output_tokens=args.max_output_tokens,
        api_format=args.api_format, token_counter=args.token_counter,
        model_context_window=args.model_context_window,
        deepseek_effort=os.environ.get("effort") or None,
    )
    skills = SkillRegistry([
        Path(__file__).parent / "builtin_skills", workspace / "skills", *args.skill_dirs,
    ])
    sandbox = DockerSandbox(
        image=args.sandbox_image, workspace=workspace,
        skill_roots=[path for path in skills.roots if path.is_dir()],
        read_roots=[session.path.parent],
    ) if args.sandbox_image else None
    storage = WorkspaceStorage(workspace, getattr(args, "data_dir", None)).ensure()
    excluded = [storage.data_dir / "workspaces", session.path]
    if not workspace.is_relative_to(session.path.parent):
        excluded.append(session.path.parent)
    if getattr(args, "log_dir", None) is not None:
        log_dir = args.log_dir.expanduser().resolve()
        if log_dir == workspace:
            excluded.extend(log_dir / name for name in (
                "session", "history.git", "runtime-storage.json", "transcript.log", "stderr.log"))
        else:
            excluded.append(log_dir)
    excluded = [path for path in excluded if not workspace.is_relative_to(path)]
    history = WorkspaceHistory(workspace, storage.history, session_id=str(session.path),
                               excluded_roots=excluded)
    return Agent(model, session, skills, workspace, context_tokens=args.context_tokens,
                 summary_output_tokens=args.summary_output_tokens,
                 sandbox=sandbox, on_event=on_event,
                 file_history=history, owns_file_history=True,
                 allow_persistent_processes=allow_persistent_processes)
