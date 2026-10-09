import argparse
from pathlib import Path
import shlex
import sys

from .runtime import add_agent_arguments, create_agent
from .session import Session


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
    run = commands.add_parser("run", help="进入会话并进行多轮交互")
    history = commands.add_parser("history", help="显示完整主会话记录")
    for command in (run, history):
        command.add_argument("--workspace", type=Path, default=Path.cwd())
        command.add_argument("--sessions-dir", type=Path)
        command.add_argument("--session", default="default")
    run.add_argument("--file", action="append", type=Path, default=[], help="作为下一条消息的附件")
    add_agent_arguments(run)
    return result


def attachment_path(value, workspace):
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else workspace / path).resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"Attachment is not a file: {path}")
    return path


def interact(agent, files):
    pending_files = []
    for path in files:
        try:
            pending_files.append(attachment_path(path, agent.workspace))
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
    while True:
        try:
            prompt = input("> " if sys.stdin.isatty() else "")
        except EOFError:
            return 0
        except KeyboardInterrupt:
            print()
            continue
        command = prompt.strip()
        if command == "/exit":
            return 0
        if command == "/file" or command.startswith("/file "):
            try:
                parts = shlex.split(command)
                if len(parts) != 2:
                    raise ValueError("用法：/file PATH；路径包含空格时使用引号")
                pending_files.append(attachment_path(parts[1], agent.workspace))
            except (ValueError, OSError) as exc:
                print(f"error: {exc}", file=sys.stderr)
            continue
        if not command and not pending_files:
            continue
        attachments, pending_files = pending_files, []
        try:
            user_message = agent.session.make_user_message(prompt, attachments)
        except (ValueError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            continue
        try:
            agent.run(user_message)
        except KeyboardInterrupt:
            print()
        except Exception:
            # Agent.run records and renders each turn's error.
            continue


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
        for event in session.events():
            render_event(event)
        if args.command == "history":
            return 0
        agent = create_agent(args, session, on_event=render_event)
        return interact(agent, args.file)
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
