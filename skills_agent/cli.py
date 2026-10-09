import argparse
import json
from pathlib import Path
import shlex
import sys

from .runtime import add_agent_arguments, create_agent
from .session import Session


_COLORS = {"user": "\033[97;40m", "assistant": "\033[30;107m", "tool": "\033[34;107m"}
_RESET = "\033[0m"


def _print(text, role, output):
    if output.isatty():
        color = _COLORS[role]
        text = color + text.replace("\n", _RESET + "\n" + color) + _RESET
    print(text, file=output, flush=True)


def _read_input():
    colored = sys.stdin.isatty() and sys.stdout.isatty()
    if colored:
        sys.stdout.write(_COLORS["user"])
        sys.stdout.flush()
    try:
        return input("> " if sys.stdin.isatty() else "")
    finally:
        if colored:
            sys.stdout.write(_RESET)
            sys.stdout.flush()


def _render_full_event(event, output):
    """Render original public content; child calls/results stay in the session log."""
    output = output or sys.stdout
    kind = event.get("type")
    if kind == "subagent_summary":
        action = "处理了" if event["status"] == "completed" else "未完成"
        _print(f"sub-agent {action}：{event['goal']}", "tool", output)
    elif kind == "error":
        if event.get("partial_content"):
            _render_full_event({"type": "message", "message": {
                "role": "assistant", "content": event["partial_content"],
            }}, output)
        _print(f"error: {event['message']}", "assistant", output)
    elif kind == "message":
        message = event["message"]
        role = message["role"]
        if role == "tool" and message.get("name") == "subagent":
            return
        content = message.get("content", [])
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if blocks:
            label = f"tool {message['name']}" if role == "tool" else role
            _print(f"{label}:", role, output)
            for block in blocks:
                if block["type"] == "text":
                    _print(block["text"], role, output)
                else:
                    _print(f"[{block['type']}] {block['path']}", role, output)
        for call in message.get("tool_calls", []):
            if call["name"] != "subagent":
                _print(f"tool_call {call['name']}: {call['arguments']}", "tool", output)
        output.flush()


def _brief(text, limit=100):
    text = " ".join(str(text).split())
    text = "".join(character for character in text if character.isprintable())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in content or [] if block["type"] == "text")


def render_event(event, output=None, *, full=False, echo_user=True):
    """Summarize intermediate work without changing the stored events."""
    output = output or sys.stdout
    kind = event.get("type")
    if full:
        return _render_full_event(event, output)
    if kind == "subagent_summary":
        action = "处理了" if event["status"] == "completed" else "未完成"
        _print(f"sub-agent {action}：{_brief(event['goal'])}", "tool", output)
    elif kind == "error":
        if event.get("partial_content"):
            count = len(_text(event["partial_content"]))
            _print(f"· 回答未完成，已保存 {count} 字符", "assistant", output)
        _print(f"error: {_brief(event['message'])}", "assistant", output)
    elif kind == "message":
        message = event["message"]
        role = message["role"]
        if role == "tool":
            if message.get("name") == "subagent":
                return
            try:
                result = json.loads(message["content"])
            except (ValueError, TypeError):
                result = None
            status = "结果已记录"
            if isinstance(result, dict):
                if result.get("error"):
                    status = f"失败：{_brief(result['error'])}"
                elif "exit_code" in result:
                    code = result["exit_code"]
                    status = "完成" if code == 0 else f"失败（退出码 {code}）"
                    lines = sum(len(str(result.get(key, "")).splitlines()) for key in ("stdout", "stderr"))
                    if lines:
                        status += f"，输出 {lines} 行"
            _print(f"· {message['name']}：{status}", "tool", output)
        elif role == "assistant" and message.get("tool_calls"):
            for call in message["tool_calls"]:
                if call["name"] == "subagent":
                    continue
                try:
                    arguments = json.loads(call["arguments"])
                except (ValueError, TypeError):
                    arguments = {}
                summary = arguments.get("summary") if isinstance(arguments, dict) else None
                summary = summary if isinstance(summary, str) and summary.strip() else "执行脚本"
                _print(f"· {call['name']}：{_brief(summary)}", "tool", output)
        elif role == "user":
            content = message.get("content", [])
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
            text = [block for block in blocks if block["type"] == "text" and not block.get("attachment")]
            if echo_user and text:
                _render_full_event({"type": "message", "message": {"role": "user", "content": text}}, output)
            for block in blocks:
                if block.get("attachment") or block["type"] in {"image", "file"}:
                    _print(f"[附件] {_brief(block.get('name') or block['path'])}", "user", output)
        else:
            _render_full_event(event, output)


def parser():
    result = argparse.ArgumentParser(prog="skills-agent")
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="进入会话并进行多轮交互")
    history = commands.add_parser("history", help="回放主会话记录")
    history.add_argument("--full", action="store_true", help="展开完整公开消息、命令和工具输出")
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
            _print(f"error: {exc}", "assistant", sys.stderr)
    while True:
        try:
            prompt = _read_input()
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
                _print(f"error: {exc}", "assistant", sys.stderr)
            continue
        if not command and not pending_files:
            continue
        attachments, pending_files = pending_files, []
        try:
            user_message = agent.session.make_user_message(prompt, attachments)
        except (ValueError, OSError) as exc:
            _print(f"error: {exc}", "assistant", sys.stderr)
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
            render_event(event, full=args.command == "history" and args.full)
        if args.command == "history":
            return 0
        agent = create_agent(args, session, on_event=lambda event: render_event(
            event, echo_user=False,
        ))
        return interact(agent, args.file)
    except (ValueError, OSError, RuntimeError) as exc:
        _print(f"error: {exc}", "assistant", sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
