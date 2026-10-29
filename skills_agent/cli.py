import argparse
import json
from math import isfinite
from pathlib import Path
import shlex
import signal
import sys
import threading
from tempfile import TemporaryDirectory

from rich.text import Text

from .runtime import add_agent_arguments, create_agent
from .config import normalize_paths
from .input import ClipboardDraft
from .markdown import MarkdownStream, _console, print_markdown
from .session import Session
from .storage import WorkspaceStorage


_COLORS = {"user": "\033[97;40m", "assistant": "\033[30;107m", "tool": "\033[34;49m",
           "thinking": "\033[90;49m", "error": "\033[31;49m"}
_RESET = "\033[0m"


def _print(text, role, output):
    if hasattr(output, "append_line"):
        output.append_line(text, role)
        return
    if output.isatty():
        if role in {"user", "assistant"}:
            console = _console(output)
            lines = Text(text).wrap(console, console.width, overflow="fold")
            text = "\n".join(line.plain + " " * max(0, console.width - line.cell_len) for line in lines)
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
    except (ValueError, TypeError, RecursionError):
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


def _tool_note(text, output, *, key=None, failed=False):
    role = "error" if failed else "tool"
    if key is not None and hasattr(output, "upsert_tool"):
        output.upsert_tool(key, text, role)
    else:
        _print(text, role, output)


def _tool_result(message):
    content = message.get("content")
    blocks = content if isinstance(content, list) else []
    text = next((block.get("text") for block in blocks
                 if isinstance(block, dict) and block.get("type") == "text"), None) if blocks else content
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        result = None
    return result, blocks


def _tool_failed(result):
    return isinstance(result, dict) and bool(result.get("error") or result.get("feedback_errors") or result.get("cleanup_required") or
                                            "exit_code" in result and result["exit_code"] != 0)


def _subagent_summary(event, output, *, key=None):
    failed = event.get("status") in {"failed", "interrupted"}
    name = _purpose(event, "name")
    if name:
        status = {"completed": "已返回报告", "failed": "失败", "interrupted": "已中断"}.get(
            event.get("status"), "未完成")
        _tool_note(f"· sub-agent「{name}」：{status}", output, key=key, failed=failed)
    else:
        action = "处理了" if event.get("status") == "completed" else "未完成"
        _tool_note(f"sub-agent {action}：{_brief(event.get('goal', '子任务'))}", output,
                   key=key, failed=failed)


def _tool_images(blocks, output):
    paths = [block.get("path") or block.get("source_path") for block in blocks
             if isinstance(block, dict) and block.get("type") == "image"]
    paths = [path for path in paths if isinstance(path, str) and path]
    for index, path in enumerate(paths, 1):
        label = "[图片]" if len(paths) == 1 else f"[图片{index}]"
        if hasattr(output, "append_image_link"):
            output.append_image_link(label, path)
        else:
            _print(f"  {label} {path}", "tool", output)


def _background_result(message):
    """Read the existing runtime observation, without showing its user envelope."""
    content = message.get("content", [])
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        try:
            result = json.loads(block.get("text"))
        except (TypeError, ValueError):
            continue
        if isinstance(result, dict) and ("job_id" in result or "state" in result):
            return result, blocks
    return {}, blocks


def _background_result_key(message, result):
    uncertain = message.get("runtime_event") == "background_job_uncertainty"
    identifier = (message.get("background_uncertainty_id") if uncertain
                  else message.get("background_result_id")) or result.get("job_id") or message.get("background_job_id")
    namespace = "background-uncertainty" if uncertain else "background-result"
    return (namespace, identifier) if isinstance(identifier, str) and identifier else None


def _render_background_uncertainty(message, output, *, full=False):
    result, blocks = _background_result(message)
    label = "后台服务" if result.get("kind") == "service" else "后台任务"
    name = _purpose(result, "display_name", "name")
    if name:
        label += f"「{name}」"
    delegated = _purpose(result, "delegated_task_name")
    if delegated:
        label += f"（子任务「{delegated}」）"
    _tool_note(f"· {label}：监管状态未确认；错误已加入上下文，仍需诊断和恢复", output,
               key=_background_result_key(message, result), failed=True)
    if full:
        for block in blocks:
            if (isinstance(block, dict) and block.get("type") == "text"
                    and not block.get("text", "").startswith("[Runtime background job uncertainty")):
                _print(block["text"], "error", output)


def _render_background_result(message, output, *, full=False):
    result, blocks = _background_result(message)
    if hasattr(output, "complete_background_job"):
        output.complete_background_job(message.get("background_job_id") or result.get("job_id") or result.get("id"))
        resolved = message.get("resolved_uncertainty_ids", [])
        if isinstance(resolved, list):
            for identifier in resolved:
                output.complete_background_job(identifier)
    state, code = result.get("state"), result.get("exit_code")
    cleanup = result.get("cleanup_required") is True
    timed_out = state == "timed_out" or result.get("timed_out") is True
    if timed_out:
        status = "超时"
    elif state == "stopped":
        status = "已停止"
    elif state == "failed" or code is not None and code != 0:
        status = "失败" + (f"（退出码 {code}）" if code is not None else "")
    elif cleanup:
        status = "入口已退出" + (f"（退出码 {code}）" if code is not None else "")
    elif code == 0:
        status = "完成"
    else:
        status = "已退出（退出码未知）"
    if cleanup:
        status += "，已清理残留进程"
    duration = result.get("duration_seconds")
    if (not isinstance(duration, bool) and isinstance(duration, (int, float))
            and isfinite(duration)):
        status += f" · {max(0, duration):.1f} 秒"
    label = "后台服务" if result.get("kind") == "service" else "后台任务"
    name = _purpose(result, "display_name", "name")
    if name:
        label += f"「{name}」"
    delegated = _purpose(result, "delegated_task_name")
    if delegated:
        label += f"（子任务「{delegated}」）"
    failed = timed_out or cleanup or state in {"failed", "stopped", "orphaned", "unknown"} or code != 0
    _tool_note(f"· {label}：{status}；结果已加入上下文", output,
               key=_background_result_key(message, result), failed=failed)
    if full:
        role = "error" if failed else "tool"
        for block in blocks:
            if block.get("type") == "text":
                # The first block is an internal English instruction envelope.
                # Full history expands the public result and attachments only.
                if block.get("text", "").startswith("[Runtime background job result"):
                    continue
                _print(block["text"], role, output)
            elif block.get("type") != "image":
                _print(f"[{block.get('type')}] {block.get('path', '')}", role, output)
    _tool_images(blocks, output)


def _render_full_event(event, output):
    """Render original public content; child calls/results stay in the session log."""
    output = output or sys.stdout
    kind = event.get("type")
    if kind == "retry":
        _render_retry(event, output)
    elif kind == "subagent_summary":
        _subagent_summary(event, output)
    elif kind == "error":
        if event.get("partial_content"):
            _render_full_event({"type": "message", "message": {
                "role": "assistant", "content": event["partial_content"],
            }}, output)
        _print(f"error: {event['message']}", "assistant", output)
    elif kind == "message":
        message = event["message"]
        if message.get("runtime_event") == "background_job_uncertainty":
            return _render_background_uncertainty(message, output, full=True)
        if message.get("runtime_event") == "background_job_result":
            return _render_background_result(message, output, full=True)
        role = message["role"]
        if role == "tool" and message.get("name") == "subagent":
            return
        content = message.get("content", [])
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if role == "assistant":
            _assistant_content(message, output)
        elif blocks:
            label = f"tool {message['name']}" if role == "tool" else role
            display_role = "error" if role == "tool" and _tool_failed(_tool_result(message)[0]) else role
            _print(f"{label}:", display_role, output)
            for block in blocks:
                if block["type"] == "text":
                    _print(block["text"], display_role, output)
                elif role == "tool" and block["type"] == "image":
                    continue
                else:
                    _print(f"[{block['type']}] {block['path']}", role, output)
            if role == "tool":
                _tool_images(blocks, output)
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


def _render_retry(event, output):
    _print(f"· 请求失败（{_brief(event.get('category', 'provider_error'))}）："
           f"{_brief(event['message'])}；{event['retry_in_seconds']} 秒后重试"
           f"（已失败 {event['attempt']} 次）", "tool", output)


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(block["text"] for block in content or [] if block["type"] == "text")


def _partial_counts(content):
    """Characters of public body and readable thinking saved before an interrupt."""
    text = thinking = 0
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "thinking":
            thinking += len(block.get("thinking") or "")
        elif block.get("type") == "text":
            text += len(block.get("text") or "")
    return text, thinking


def render_event(event, output=None, *, full=False, echo_user=True, tool_names=None, tool_keys=None):
    """Summarize intermediate work without changing the stored events."""
    output = output or sys.stdout
    kind = event.get("type")
    if full:
        return _render_full_event(event, output)
    if kind == "retry":
        _render_retry(event, output)
    elif kind == "subagent_summary":
        _subagent_summary(event, output)
    elif kind == "error":
        if event.get("partial_content"):
            text, thinking = _partial_counts(event["partial_content"])
            if text and thinking:
                detail = f"已保存正文 {text} 字符、思考 {thinking} 字符"
            elif text:
                detail = f"已保存 {text} 字符"
            elif thinking:
                detail = f"未收到正文，已保存思考 {thinking} 字符"
            else:
                detail = "已保存 0 字符"
            _print(f"· 回答未完成，{detail}", "assistant", output)
        _print(f"error: {_brief(event['message'])}", "assistant", output)
    elif kind == "message":
        message = event["message"]
        if message.get("runtime_event") == "background_job_uncertainty":
            return _render_background_uncertainty(message, output)
        if message.get("runtime_event") == "background_job_result":
            return _render_background_result(message, output)
        role = message["role"]
        if role == "tool":
            if message.get("name") == "subagent":
                return
            result, blocks = _tool_result(message)
            status = "结果已记录"
            if isinstance(result, dict):
                if result.get("error"):
                    if (message.get("name") == "edit_file"
                            and result.get("application_state") == "uncertain"):
                        status = "文件变更状态待核对："
                    elif (message.get("name") == "edit_file"
                          and (result.get("changed") is True or result.get("applied") is True)):
                        status = "文件已变更，后续步骤失败："
                    else:
                        status = "失败："
                    status += _brief(result["error"])
                elif message.get("name") == "edit_file" and type(result.get("changed")) is bool:
                    if result["changed"] and result.get("operation") == "batch":
                        status = f"已完成批量编辑（{len(result.get('edits', []))} 项）"
                    else:
                        status = ({"create": "已新建", "write": "已写入", "replace": "已修改",
                                   "insert": "已插入", "delete": "已删除", "move": "已移动", "copy": "已复制"}
                                  .get(result.get("operation"), "完成")
                                  if result["changed"] else "内容未变化")
                elif result.get("job_id") and result.get("completed") is False:
                    status = "已返回服务启动回执" if result.get("kind") == "service" else "已返回后台启动回执"
                elif "exit_code" in result:
                    code = result["exit_code"]
                    status = "完成" if code == 0 else f"失败（退出码 {code}）"
                    lines = sum(len(str(result.get(key, "")).splitlines()) for key in ("stdout", "stderr"))
                    if lines:
                        status += f"，输出 {lines} 行"
                if result.get("cleanup_required"):
                    status += "，已清理残留进程"
                if result.get("feedback_errors"):
                    status += "，反馈获取失败"
            images = sum(isinstance(block, dict) and block.get("type") == "image" for block in blocks)
            if images:
                status += f"，图片 {images} 张"
            purpose = _purpose(message, "task_name") or (tool_names or {}).get(message.get("tool_call_id"))
            label = f"{message['name']}「{purpose}」" if purpose else message["name"]
            identifier = message.get("tool_call_id")
            _tool_note(f"· {label}：{status}", output, key=(tool_keys or {}).get(identifier, identifier),
                       failed=_tool_failed(result))
            _tool_images(blocks, output)
        elif role == "assistant":
            _assistant_content(message, output)
            for call in message.get("tool_calls", []):
                identifier = call.get("id")
                _tool_note(_tool_call_summary(call["name"], _arguments(call)), output,
                           key=(tool_keys or {}).get(identifier, identifier))
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
        self._tool_keys = {}
        self._pending_tools = {}
        self._background_results = set()

    @property
    def _mutable_tools(self):
        return not self.full and hasattr(self.output, "upsert_tool")

    def _matching_tool(self, name, purpose):
        return next((identifier for identifier, call in self._pending_tools.items()
                     if call["name"] == name and
                     _purpose(_arguments(call), "name", "goal") == purpose), None)

    def _register_tools(self, calls):
        # Provider block indices can include text/thinking blocks. Match IDs first;
        # sorted stream order also supports older logs that omitted delta IDs.
        streamed = [self._calls[index] for index in sorted(self._calls)]
        by_id = {item["id"]: item for item in streamed if item["id"]}
        anonymous = iter(item for item in streamed if not item["id"])
        for call in calls:
            identifier = call["id"]
            purpose = _purpose(_arguments(call), "name")
            if purpose:
                self._tool_names[identifier] = purpose
            if not self._mutable_tools:
                continue
            delta = by_id.pop(identifier, None)
            if delta is None:
                delta = next(anonymous, None)
            self._tool_keys[identifier] = delta["key"] if delta is not None else object()
            self._pending_tools[identifier] = call

    def _finish_tool_notes(self, status):
        if not self._mutable_tools:
            return
        for call in self._calls.values():
            if call["announced"]:
                label = _tool_call_summary(call["name"], _arguments(call)).rsplit("：", 1)[0]
                _tool_note(f"{label}：{status}", self.output, key=call["key"], failed=True)
        for identifier, call in self._pending_tools.items():
            label = _tool_call_summary(call["name"], _arguments(call)).rsplit("：", 1)[0]
            _tool_note(f"{label}：{status}", self.output, key=self._tool_keys[identifier], failed=True)
        self._pending_tools.clear()
        self._tool_keys.clear()
        self._tool_names.clear()

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
            "id": "", "name": "", "arguments": "", "announced": False, "shown": False,
            "key": object(),
        })
        for key in ("id", "name", "arguments"):
            call[key] += event.get(key) or ""
        if call["name"] not in {"shell", "sandbox", "subagent", "edit_file"}:
            return
        label = "sub-agent" if call["name"] == "subagent" else call["name"]
        receiving = f"· {label}：接收调用"
        arguments = None
        # A complete argument object must end with a closing brace. Avoid
        # reparsing the growing file/script contents for every streamed token.
        # This is only a cheap candidate check; JSON still validates the object.
        if not call["shown"] and call["arguments"].rstrip().endswith("}"):
            try:
                arguments = json.loads(call["arguments"])
            except (ValueError, RecursionError):
                pass
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
            _tool_note(display, self.output, key=call["key"] if self._mutable_tools else None)
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
        if (event.get("type") == "message"
                and event["message"].get("runtime_event") in {
                    "background_job_result", "background_job_uncertainty"}):
            message = event["message"]
            key = _background_result_key(message, _background_result(message)[0])
            if key is not None:
                if key in self._background_results:
                    return
                self._background_results.add(key)
            self._finish_stream()
            render_event(event, self.output, full=self.full)
            return
        if event.get("type") == "background_jobs":
            if self.live and hasattr(self.output, "set_background_jobs"):
                self.output.set_background_jobs(event["jobs"])
            return
        if event.get("type") == "activity":
            if self.live and hasattr(self.output, "set_activity"):
                activity = event.get("activity")
                text = None
                task = None
                tool_key = None
                preview = None
                thinking = False
                if activity:
                    if activity["kind"] == "compression":
                        text = "· 正在压缩上下文"
                        if activity.get("batch"):
                            text += f" · 第 {activity['batch']} 批"
                        stage = {"waiting": "等待模型", "thinking": "思考", "summary": "摘要"}.get(
                            activity.get("phase"))
                        if stage:
                            text += f" · {stage}"
                        preview = " ".join(activity.get("preview", "").split())
                        preview = "".join(char for char in preview if char.isprintable())
                        thinking = activity.get("phase") == "thinking"
                    elif activity["name"] == "subagent":
                        task = activity["task_name"]
                        stage = {"tool": "正在执行工具", "compression": "正在压缩上下文"}.get(
                            activity.get("phase"), "执行中")
                        text = f"· sub-agent「{_purpose(activity, 'task_name')}」：{stage}"
                    else:
                        text = f"· {activity['name']}「{_purpose(activity, 'task_name')}」：执行中"
                    if activity["kind"] == "tool" and self._mutable_tools:
                        identifier = self._matching_tool(activity["name"], _purpose(activity, "task_name"))
                        tool_key = self._tool_keys.get(identifier)
                options = {"task": task, "preview": preview, "thinking": thinking}
                if self._mutable_tools:
                    options["tool_key"] = tool_key
                self.output.set_activity(text, **options)
            return
        if event.get("type") in {"text_delta", "thinking_delta"}:
            self._had_deltas = self._had_deltas or bool(event["text"])
            self._write_delta(event["text"], channel="thinking" if event["type"] == "thinking_delta" else "text")
            return
        if event.get("type") == "tool_call_delta":
            self._finish_stream()
            self._had_deltas = True
            self._tool_delta(event)
            return
        self._finish_stream()
        if (event.get("type") in {"error", "retry"} or
                event.get("type") == "message" and event["message"]["role"] == "assistant"):
            if hasattr(self.output, "finish_response"):
                self.output.finish_response()
        if event.get("type") == "message" and event["message"]["role"] == "assistant":
            calls = event["message"].get("tool_calls", [])
            self._register_tools(calls)
            streamed = event.get("streamed") or self._had_deltas
            self._had_deltas = False
            self._calls.clear()
            if streamed:
                if self._mutable_tools:
                    for call in calls:
                        _tool_note(_tool_call_summary(call["name"], _arguments(call)), self.output,
                                   key=self._tool_keys[call["id"]])
                return
        elif event.get("type") in {"error", "retry"}:
            streamed = self._had_deltas
            self._had_deltas = False
            status = ("未完成（请求重试）" if event["type"] == "retry" else
                      "已中断" if event.get("message") == "Interrupted" else "失败")
            self._finish_tool_notes(status)
            self._calls.clear()
            if self.full and streamed and event.get("partial_content"):
                event = {key: value for key, value in event.items() if key != "partial_content"}
        if event.get("type") == "subagent_summary" and self._mutable_tools:
            identifier = self._matching_tool("subagent", _purpose(event, "name", "goal"))
            _subagent_summary(event, self.output, key=self._tool_keys.get(identifier))
            self._pending_tools.pop(identifier, None)
            return
        if (self._mutable_tools and event.get("type") == "message"
                and event["message"]["role"] == "tool" and event["message"].get("name") == "subagent"):
            identifier = event["message"].get("tool_call_id")
            call = self._pending_tools.get(identifier)
            if call is not None:
                # Validation can fail before the child starts and emits a summary.
                # Keep the child's result and error details private in either case.
                failed = _tool_failed(_tool_result(event["message"])[0])
                label = _tool_call_summary(call["name"], _arguments(call)).rsplit("：", 1)[0]
                status = "失败" if failed else "已返回报告"
                _tool_note(f"{label}：{status}", self.output, key=self._tool_keys[identifier], failed=failed)
        render_event(event, self.output, full=self.full, echo_user=self.echo_user,
                     tool_names=self._tool_names, tool_keys=self._tool_keys)
        if event.get("type") == "message" and event["message"]["role"] == "tool":
            identifier = event["message"].get("tool_call_id")
            self._tool_names.pop(identifier, None)
            self._tool_keys.pop(identifier, None)
            self._pending_tools.pop(identifier, None)


def parser():
    result = argparse.ArgumentParser(prog="skills-agent")
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="进入会话并进行多轮交互")
    history = commands.add_parser("history", help="回放主会话记录")
    history.add_argument("--full", action="store_true", help="展开完整公开消息、命令和工具输出")
    for command in (run, history):
        command.add_argument("--workspace", type=Path, help="工作目录，默认启动时的当前目录")
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

            view = TerminalView(key_bindings=draft.bindings,
                                context_usage=lambda: agent.context.state.get("last_usage"))
            draft.on_error = lambda message: _print(f"error: {message}", "assistant", view)
            replay = ConsoleRenderer(view, live=False)
            for event in agent.session.replay_events():
                replay(event)
            replay.close()
            renderer = ConsoleRenderer(view)
            agent.on_event = lambda event: view.post_event(renderer, event)
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
        try:
            try:
                if hasattr(output, "start_turn"):
                    output.start_turn()
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
                except Exception:
                    # Agent.run records and renders each turn's error.
                    continue
            finally:
                if hasattr(output, "finish_turn"):
                    output.finish_turn()
        except KeyboardInterrupt:
            # Cover preparation and the final redraw as well as model/tool work.
            if hasattr(output, "finish_turn"):
                output.finish_turn()
            _print("", "assistant", output or sys.stdout)


def main(argv=None):
    # SIGTERM exits the whole session; Ctrl-C remains a cancellable individual turn.
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _terminate_session)
    try:
        return _main(argv)
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


def _terminate_session(signum, frame):
    raise SystemExit(128 + signum)


def _main(argv=None):
    args = parser().parse_args(argv)
    try:
        args = normalize_paths(args)
        workspace = args.workspace
        if not workspace.is_dir():
            raise ValueError(f"工作目录不存在：{workspace}")
        storage = WorkspaceStorage(workspace)
        root = storage.session_root(args.session)
        if args.command == "history" and not (root / args.session / "events.jsonl").is_file():
            raise ValueError(f"会话不存在：{args.session}")
        if args.command == "run":
            storage.ensure()
        session = Session(root, args.session)
        if args.command == "history" or not (sys.stdin.isatty() and sys.stdout.isatty()):
            replay = ConsoleRenderer(full=args.command == "history" and args.full, live=False)
            events = session.events() if args.command == "history" and args.full else session.replay_events()
            for event in events:
                replay(event)
            replay.close()
        if args.command == "history":
            return 0
        agent = create_agent(args, session, allow_persistent_processes=True, on_event=ConsoleRenderer(
            echo_user=sys.stdin.isatty() and sys.stdout.isatty(),
        ))
        try:
            return interact(agent, args.file)
        finally:
            agent.close()
    except (ValueError, OSError, RuntimeError) as exc:
        _print(f"error: {exc}", "assistant", sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
