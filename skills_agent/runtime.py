import os
from pathlib import Path

from .agent import Agent
from .providers import create_provider
from .sandbox import DockerSandbox
from .skills import SkillRegistry


def add_agent_arguments(parser):
    parser.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--model", help="也可设置 OPENAI_MODEL 或 ANTHROPIC_MODEL")
    parser.add_argument("--base-url", help="兼容服务的 API base URL")
    parser.add_argument("--skill-dir", action="append", type=Path, default=[])
    parser.add_argument("--context-chars", type=int, default=48000)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--sandbox-image", help="提供 sandbox 工具，使用指定 Docker 镜像")


def create_agent(args, session, *, on_event=None):
    workspace = args.workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError(f"工作目录不存在：{workspace}")
    prefix = args.provider.upper()
    model_name = args.model or os.environ.get(f"{prefix}_MODEL", "")
    if not model_name:
        raise ValueError(f"需要 --model 或 {prefix}_MODEL")
    model = create_provider(
        args.provider, model_name, os.environ.get(f"{prefix}_API_KEY", ""),
        base_url=args.base_url or os.environ.get(f"{prefix}_BASE_URL"),
        max_output_tokens=args.max_output_tokens,
    )
    skills = SkillRegistry([
        Path(__file__).parent / "builtin_skills", workspace / "skills", *args.skill_dir,
    ])
    sandbox = DockerSandbox(
        image=args.sandbox_image, workspace=workspace,
        skill_roots=[path for path in skills.roots if path.is_dir()],
        read_roots=[session.path.parent],
    ) if args.sandbox_image else None
    return Agent(model, session, skills, workspace, context_chars=args.context_chars,
                 sandbox=sandbox, on_event=on_event)
