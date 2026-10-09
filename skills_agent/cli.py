import argparse
import os
from pathlib import Path
import sys

from .agent import Agent
from .providers import create_provider
from .sandbox import DockerSandbox
from .session import Session
from .skills import SkillRegistry


def render_event(event, output=None):
    """Render original public content; child calls/results stay in the session log."""
    output = output or sys.stdout
    kind = event.get("type")
    if kind == "subagent_summary":
        action = "处理了" if event["status"] == "completed" else "未完成"
        print(f"sub-agent {action}：{event['goal']}", file=output, flush=True)
    elif kind == "error":
        if event.get("partial_content"):
            render_event({"type": "message", "message": {
                "role": "assistant", "content": event["partial_content"],
            }}, output)
        print(f"error: {event['message']}", file=output, flush=True)
    elif kind == "message":
        message = event["message"]
        role = message["role"]
        if role == "tool" and message.get("name") == "subagent":
            return
        content = message.get("content", [])
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if blocks:
            label = f"tool {message['name']}" if role == "tool" else role
            print(f"{label}:", file=output)
            for block in blocks:
                if block["type"] == "text":
                    print(block["text"], file=output)
                else:
                    print(f"[{block['type']}] {block['path']}", file=output)
        for call in message.get("tool_calls", []):
            if call["name"] != "subagent":
                print(f"tool_call {call['name']}: {call['arguments']}", file=output)
        output.flush()


def parser():
    result = argparse.ArgumentParser(prog="skills-agent")
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="执行任务；使用同一 session 续聊")
    history = commands.add_parser("history", help="显示完整主会话记录")
    for command in (run, history):
        command.add_argument("--workspace", type=Path, default=Path.cwd())
        command.add_argument("--sessions-dir", type=Path)
        command.add_argument("--session", default="default")
    run.add_argument("prompt", nargs="?", help="省略时从 stdin 读取")
    run.add_argument("--file", action="append", type=Path, default=[])
    run.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    run.add_argument("--model", help="也可设置 OPENAI_MODEL 或 ANTHROPIC_MODEL")
    run.add_argument("--base-url", help="兼容服务的 API base URL")
    run.add_argument("--skill-dir", action="append", type=Path, default=[])
    run.add_argument("--context-chars", type=int, default=48000)
    run.add_argument("--max-output-tokens", type=int, default=4096)
    run.add_argument("--sandbox-image", help="提供 sandbox 工具，使用指定 Docker 镜像")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        workspace = args.workspace.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"工作目录不存在：{workspace}")
        root = (args.sessions_dir or workspace / ".agent" / "sessions").expanduser().resolve()
        if args.command == "history" and not (root / args.session / "events.jsonl").is_file():
            raise ValueError(f"会话不存在：{args.session}")
        session = Session(root, args.session)
        if args.command == "history":
            for event in session.events():
                render_event(event)
            return 0
        prompt = args.prompt
        if prompt is None:
            prompt = "" if sys.stdin.isatty() else sys.stdin.read()
        if not prompt.strip() and not args.file:
            raise ValueError("需要任务文本或 --file 输入")
        prefix = args.provider.upper()
        model_name = args.model or os.environ.get(f"{prefix}_MODEL", "")
        if not model_name:
            raise ValueError(f"需要 --model 或 {prefix}_MODEL")
        model = create_provider(
            args.provider, model_name, os.environ.get(f"{prefix}_API_KEY", ""),
            base_url=args.base_url or os.environ.get(f"{prefix}_BASE_URL"),
            max_output_tokens=args.max_output_tokens,
        )
        roots = [Path(__file__).parent / "builtin_skills", workspace / "skills", *args.skill_dir]
        skills = SkillRegistry(roots)
        sandbox = DockerSandbox(
            image=args.sandbox_image, workspace=workspace,
            skill_roots=[path for path in skills.roots if path.is_dir()],
            read_roots=[root],
        ) if args.sandbox_image else None
        user_message = session.make_user_message(prompt, args.file)
        agent = Agent(model, session, skills, workspace, context_chars=args.context_chars,
                      sandbox=sandbox, on_event=render_event)
        agent.run(user_message)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        # Agent errors were already recorded and rendered.
        if "agent" not in locals():
            print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
