import os
from pathlib import Path

from .agent import Agent
from .context import DEFAULT_CONTEXT_TOKENS
from .providers import create_provider
from .sandbox import DockerSandbox
from .skills import SkillRegistry


def add_agent_arguments(parser):
    parser.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--model", help="也可设置 OPENAI_MODEL 或 ANTHROPIC_MODEL")
    parser.add_argument("--base-url", help="兼容服务的 API base URL")
    parser.add_argument("--skill-dir", action="append", type=Path, default=[])
    parser.add_argument("--context-tokens", type=int, default=DEFAULT_CONTEXT_TOKENS,
                        help="工作上下文 token 预算（正整数），默认 1000000；达到 90%% 时压缩")
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    parser.add_argument("--summary-output-tokens", type=int, default=32768,
                        help="辅助生成的输出额度；摘要目标篇幅另为8192 tokens")
    parser.add_argument("--api-format", choices=["auto", "responses", "chat"],
                        default=os.environ.get("SKILLS_AGENT_API_FORMAT", "auto"))
    parser.add_argument("--token-counter", choices=["auto", "official", "deepseek"],
                        default=os.environ.get("SKILLS_AGENT_TOKEN_COUNTER", "auto"))
    parser.add_argument("--tokenizer-path", default=os.environ.get("SKILLS_AGENT_TOKENIZER_PATH"))
    parser.add_argument("--tokenizer-encoding", choices=["v4", "v41"],
                        default=os.environ.get("SKILLS_AGENT_TOKENIZER_ENCODING"))
    parser.add_argument("--model-context-window", type=int,
                        help="模型实际窗口；发送输入加输出额度必须装得下")
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
        api_format=args.api_format, token_counter=args.token_counter,
        tokenizer_path=args.tokenizer_path, tokenizer_encoding=args.tokenizer_encoding,
        model_context_window=args.model_context_window,
    )
    skills = SkillRegistry([
        Path(__file__).parent / "builtin_skills", workspace / "skills", *args.skill_dir,
    ])
    sandbox = DockerSandbox(
        image=args.sandbox_image, workspace=workspace,
        skill_roots=[path for path in skills.roots if path.is_dir()],
        read_roots=[session.path.parent],
    ) if args.sandbox_image else None
    return Agent(model, session, skills, workspace, context_tokens=args.context_tokens,
                 summary_output_tokens=args.summary_output_tokens,
                 sandbox=sandbox, on_event=on_event)
