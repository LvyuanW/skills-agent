import argparse
import json
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory

from .runtime import add_agent_arguments, create_agent
from .input import ClipboardDraft
from .markdown import MarkdownStream, print_markdown
from .session import Session


_COLORS = {"user": "\033[97;40m", "assistant": "\033[30;107m", "tool": "\033[34;107m",
           "thinking": "\033[90;49m"}
_RESET = "\033[0m"


def _print(text, role, output):
    if hasattr(output, "append_line"):
        output.append_line(text, role)
        return
    if output.isatty():
        color = _COLORS[role]
        text = color + text.replace("\n", _RESET + "\n" + color) + _RESET
    print(text, file=output, flush=True)


def _assistant_content(message, output):
    content = message.get("content", [])
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning and not any(block["type"] == "thinking" for block in blocks):
        blocks = [{"type": "thinking", "thinking": reasoning}, *blocks]
    previous = None
    for block in blocks:
        kind = block["type"]
        if kind not in {"text", "thinking"}:
            continue
        text = block.get("thinking" if kind == "thinking" else "text")
        if not isinstance(text, str) or not text:
            continue
        if hasattr(output, "append_body"):
            output.append_body(text, channel=kind)
            continue
        role = "thinking" if kind == "thinking" else "assistant"
        if kind != previous:
            _print("思考:" if kind == "thinking" else "assistant:", role, output)
        if output.isatty():
            print_markdown(text, output, thinking=kind == "thinking")
        else:
            _print(text, role, output)
        previous = kind


def _arguments(call):
    try:
        value = json.loads(call.get("arguments", ""))
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _purpose(arguments, *keys):
    for key in keys:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return _brief(value)
    return None


def _tool_call_summary(name, arguments):
    """Describe the model's call; receiving arguments does not start execution."""
    purpose = _purpose(arguments, "name")
    if name == "subagent":
        purpose = purpose or _purpose(arguments, "goal")
        return f"· sub-agent「{purpose}」：调用" if purpose else "· sub-agent：接收调用"
    if purpose:
        return f"· {name}「{purpose}」：调用"
    return f"· {name}：{_purpose(arguments, 'summary') or '接收调用'}"


def _subagent_summary(event, output):
    name = _purpose(event, "name")
    if name:
        status = "已返回报告" if event.get("status") == "completed" else "未完成"
        _print(f"· sub-agent「{name}」：{status}", "tool", output)
    else:
        action = "处理了" if event.get("status") == "completed" else "未完成"
        _print(f"sub-agent {action}：{_brief(event.get('goal', '子任务'))}", "tool", output)


def _render_full_event(event, output):
    """Render original public content; child calls/results stay in the session log."""
    output = output or sys.stdout
    kind = event.get("type")
    if kind == "subagent_summary":
        _subagent_summary(event, output)
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
        if role == "assistant":
            _assistant_content(message, output)
        elif blocks:
            label = f"tool {message['name']}" if role == "tool" else role
            _print(f"{label}:", role, output)
            for block in blocks:
                if block["type"] == "text":
                    _print(block["text"], role, output)
                else:
                    _print(f"[{block['type']}] {block['path']}", role, output)
        for call in message.get("tool_calls", []):
            if call["name"] == "subagent":
                _print(_tool_call_summary(call["name"], _arguments(call)), "tool", output)
            else:
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


def render_event(event, output=None, *, full=False, echo_user=True, tool_names=None):
    """Summarize intermediate work without changing the stored events."""
    output = output or sys.stdout
    kind = event.get("type")
    if full:
        return _render_full_event(event, output)
    if kind == "subagent_summary":
        _subagent_summary(event, output)
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
            purpose = _purpose(message, "task_name") or (tool_names or {}).get(message.get("tool_call_id"))
            label = f"{message['name']}「{purpose}」" if purpose else message["name"]
            _print(f"· {label}：{status}", "tool", output)
        elif role == "assistant":
            _assistant_content(message, output)
            for call in message.get("tool_calls", []):
                _print(_tool_call_summary(call["name"], _arguments(call)), "tool", output)
        elif role == "user":
            content = message.get("content", [])
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
            text = [block for block in blocks if block["type"] == "text" and not block.get("attachment")]
            if echo_user and text:
                _render_full_event({"type": "message", "message": {"role": "user", "content": text}}, output)
            for block in blocks:
                if block.get("attachment") or block["type"] in {"image", "file"}:
                    if block.get("marker") and block["marker"] in _text(text):
                        continue
                    _print(f"[附件] {_brief(block.get('name') or block['path'])}", "user", output)
        else:
            _render_full_event(event, output)


class ConsoleRenderer:
    """Display public text as it arrives, then render the recorded result once."""

    def __init__(self, output=None, *, full=False, echo_user=True, live=True):
        self.output = output or sys.stdout
        self.full = full
        self.echo_user = echo_user
        self.live = live
        self._markdown = None
        self._streaming = False
        self._line_ended = False
        self._had_deltas = False
        self._calls = {}
        self._channel = None
        self._tool_names = {}

    def _write_delta(self, text, *, channel="text"):
        if not text:
            return
        if hasattr(self.output, "write_delta"):
            self.output.write_delta(text, channel=channel, live=self.live)
            self._streaming = True
            return
        if self._channel != channel:
            self._finish_stream()
        if not self._streaming:
            role = "thinking" if channel == "thinking" else "assistant"
            _print("思考:" if channel == "thinking" else "assistant:", role, self.output)
            self._streaming = True
            self._channel = channel
            if self.output.isatty():
                self._markdown = MarkdownStream(self.output, live=self.live, thinking=channel == "thinking")
        if self._markdown:
            self._markdown.write(text)
        else:
            self.output.write(text)
            self.output.flush()
        self._line_ended = text.endswith("\n")

    def _tool_delta(self, event):
        call = self._calls.setdefault(event["index"], {
            "name": "", "arguments": "", "announced": False, "shown": False,
        })
        for key in ("name", "arguments"):
            call[key] += event.get(key) or ""
        if call["name"] not in {"shell", "sandbox", "subagent"}:
            return
        label = "sub-agent" if call["name"] == "subagent" else call["name"]
        receiving = f"· {label}：接收调用"
        try:
            arguments = json.loads(call["arguments"])
        except ValueError:
            arguments = None
        display = None
        if isinstance(arguments, dict) and not call["shown"]:
            if self.full and call["name"] != "subagent":
                display = f"tool_call {call['name']}: {call['arguments']}"
            else:
                display = _tool_call_summary(call["name"], arguments)
                if display == receiving and call["announced"]:
                    display = None
            call["shown"] = True
        elif not call["announced"]:
            display = receiving
        if display is not None:
            self._finish_stream()
            _print(display, "tool", self.output)
            call["announced"] = True

    def _finish_stream(self):
        if not self._streaming:
            return
        if hasattr(self.output, "finish_stream"):
            self.output.finish_stream()
        elif self._markdown:
            self._markdown.finish()
            self._markdown = None
        else:
            if not self._line_ended:
                self.output.write("\n")
            self.output.flush()
        self._streaming = False
        self._line_ended = False
        self._channel = None

    def close(self):
        self._finish_stream()
        if hasattr(self.output, "finish_response"):
            self.output.finish_response()

    def __call__(self, event):
        if event.get("type") in {"text_delta", "thinking_delta"}:
            self._had_deltas = self._had_deltas or bool(event["text"])
            self._write_delta(event["text"], channel="thinking" if event["type"] == "thinking_delta" else "text")
            return
        if event.get("type") == "tool_call_delta":
            self._had_deltas = True
            self._tool_delta(event)
            return
        self._finish_stream()
        if (event.get("type") == "error" or
                event.get("type") == "message" and event["message"]["role"] == "assistant"):
            if hasattr(self.output, "finish_response"):
                self.output.finish_response()
        if event.get("type") == "message" and event["message"]["role"] == "assistant":
            for call in event["message"].get("tool_calls", []):
                purpose = _purpose(_arguments(call), "name")
                if purpose:
                    self._tool_names[call["id"]] = purpose
            streamed = event.get("streamed") or self._had_deltas
            self._had_deltas = False
            self._calls.clear()
            if streamed:
                return
        elif event.get("type") == "error":
            streamed = self._had_deltas
            self._had_deltas = False
            self._calls.clear()
            if self.full and streamed and event.get("partial_content"):
                event = {key: value for key, value in event.items() if key != "partial_content"}
        render_event(event, self.output, full=self.full, echo_user=self.echo_user,
                     tool_names=self._tool_names)
        if event.get("type") == "message" and event["message"]["role"] == "tool":
            self._tool_names.pop(event["message"].get("tool_call_id"), None)


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
    with TemporaryDirectory(prefix="skills-agent-clipboard-") as directory:
        draft = ClipboardDraft(
            Path(directory),
            on_error=lambda message: _print(f"error: {message}", "assistant", sys.stderr),
        )
        if sys.stdin.isatty() and sys.stdout.isatty():
            from .terminal import TerminalView

            view = TerminalView(key_bindings=draft.bindings)
            draft.on_error = lambda message: _print(f"error: {message}", "assistant", view)
            replay = ConsoleRenderer(view, live=False)
            for event in agent.session.events():
                replay(event)
            replay.close()
            agent.on_event = ConsoleRenderer(view)
            try:
                view.start()
                return _interact(agent, files, draft, read_input=view.prompt, output=view)
            finally:
                view.close()
        return _interact(agent, files, draft)


def _interact(agent, files, draft, *, read_input=None, output=None):
    read_input = read_input or (lambda: input("> " if sys.stdin.isatty() else ""))
    pending_files = []
    for path in files:
        try:
            pending_files.append(attachment_path(path, agent.workspace))
        except (ValueError, OSError) as exc:
            _print(f"error: {exc}", "assistant", output or sys.stderr)
    while True:
        try:
            prompt = read_input()
        except EOFError:
            return 0
        except KeyboardInterrupt:
            _print("", "assistant", output or sys.stdout)
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
                _print(f"error: {exc}", "assistant", output or sys.stderr)
            continue
        if not command and not pending_files:
            continue
        pasted = draft.attachments(prompt)
        attachments = [path for _, path in pasted] + pending_files
        pending_files = []
        try:
            user_message = agent.session.make_user_message(prompt, attachments)
            blocks = [block for block in user_message["content"]
                      if block.get("attachment") or block["type"] in {"image", "file"}]
            for (marker, _), block in zip(pasted, blocks):
                block["marker"] = marker
        except (ValueError, OSError) as exc:
            _print(f"error: {exc}", "assistant", output or sys.stderr)
            continue
        try:
            agent.run(user_message)
        except KeyboardInterrupt:
            _print("", "assistant", output or sys.stdout)
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
        if args.command == "history" or not (sys.stdin.isatty() and sys.stdout.isatty()):
            replay = ConsoleRenderer(full=args.command == "history" and args.full, live=False)
            for event in session.events():
                replay(event)
            replay.close()
        if args.command == "history":
            return 0
        agent = create_agent(args, session, on_event=ConsoleRenderer(
            echo_user=sys.stdin.isatty() and sys.stdout.isatty(),
        ))
        return interact(agent, args.file)
    except (ValueError, OSError, RuntimeError) as exc:
        _print(f"error: {exc}", "assistant", sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
