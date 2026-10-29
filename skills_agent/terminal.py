"""Interactive terminal transcript with expandable thinking blocks."""

import asyncio
from collections import deque
from dataclasses import dataclass
from functools import lru_cache, wraps
from io import StringIO
from math import isfinite
import os
import signal
import subprocess
import sys
from threading import Condition as ThreadCondition, Event, RLock, Thread, get_ident
from time import monotonic

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.cursor_shapes import CursorShape
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, fragment_list_to_text, fragment_list_width, to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import ConditionalKeyBindings, KeyBindings, merge_key_bindings
from prompt_toolkit.layout import Dimension, HSplit, Layout, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl, UIContent, UIControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.mouse_events import MouseButton, MouseEventType
from prompt_toolkit.utils import get_cwidth
from rich.cells import set_cell_size
from rich.console import Console
from rich.text import Text

from .markdown import _Body
from .display import DisplayQueue


_STYLES = {"user": "bright_white on black", "assistant": "black on bright_white",
           "tool": "blue on default", "error": "red on default"}
_LINE_STYLES = {"user": "fg:ansiwhite bg:ansiblack",
                "assistant": "fg:ansiblack bg:ansiwhite",
                "tool": "fg:ansiblue bg:default",
                "error": "fg:ansired bg:default",
                "thinking": "fg:ansibrightblack bg:default"}
_RUNNING_JOB_STATES = {"starting", "running", "stopping"}
_THINKING_PREVIEW_LINES = 8


@lru_cache(maxsize=8192)
def _cached_fragment_width(fragments):
    return fragment_list_width(fragments)


def _row_width(fragments):
    # Width depends only on style/text; never retain mouse handlers or views.
    return _cached_fragment_width(tuple(item[:2] for item in fragments))


def _display_only(method):
    """Display mutations belong to the UI thread once its event loop is running."""
    @wraps(method)
    def invoke(self, *args, **kwargs):
        if self._closed:
            return
        if self._thread is not None and get_ident() != self._ui_thread_id:
            self._display.post(method, self, *args, **kwargs)
            return
        return method(self, *args, **kwargs)
    return invoke


class _PlainRows:
    """Wrap only newly appended text, retaining a bounded tail for thinking."""
    def __init__(self, width, *, tail=None):
        self.width = width
        self.rows = deque([""], maxlen=tail) if tail is not None else [""]
        self.columns = 0
        self.processed = 0

    def append(self, text):
        for character in text:
            if character == "\n":
                self.rows.append("")
                self.columns = 0
                continue
            if character == "\t":
                self.append(" " * (8 - self.columns % 8))
                continue
            size = get_cwidth(character)
            if not character.isprintable() and size <= 0:
                continue
            if size > self.width:
                character, size = "�", 1
            if self.columns + size > self.width:
                self.rows.append("")
                self.columns = 0
            self.rows[-1] += character
            self.columns += size


@dataclass(eq=False, frozen=True)
class _QueuedMessage:
    text: str
    submitted_at: float


class _MessageQueue(UIControl):
    def __init__(self, view):
        self.view = view
        self._items = []
        self._starts = []

    def items(self):
        with self.view._lock:
            if not self.view._selecting:
                self._items = [item for item in self.view._submitted if isinstance(item, _QueuedMessage)]
            return self._items

    def create_content(self, width, height):
        rows = []
        self._starts = []
        for item in self.items():
            text = " ".join(item.text.split())
            text = "".join(char for char in text if char.isprintable())
            available = max(0, width - 8)
            if get_cwidth(text) > available:
                text = set_cell_size(text, max(0, available - 1)) + ("…" if available else "")
            prefix = set_cell_size(text, max(0, width - 6))
            self._starts.append(len(prefix))
            hover = self.view._hovered is item
            rows.append([("fg:ansibrightblack bg:default", prefix),
                         ("fg:ansired bg:default" if hover else "fg:ansiblue bg:default",
                          "撤回  " if hover else "排队中")])
        rows = rows or [[]]
        return UIContent(get_line=lambda index: rows[index], line_count=len(rows), show_cursor=False)

    def mouse_handler(self, event):
        if self.view._selecting:
            return NotImplemented
        y = event.position.y
        if 0 <= y < len(self._items) and event.position.x >= self._starts[y]:
            item = self._items[y]
            self.view._hovered = item
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self.view.cancel_message(item)
            self.view.flush()
            return None
        return NotImplemented


class _Transcript(UIControl):
    def __init__(self, view):
        self.view = view
        self.lines = [[]]

    def create_content(self, width, height):
        view = self.view
        with view._lock:
            selecting = view._selecting
        # Rendering is UI-owned; never hold the short control lock during Rich.
        # Keep the displayed frame stable while the terminal owns selection.
        if not selecting:
            self.lines = view._lines(width)
        with view._lock:
            lines = self.lines
            view._height = max(1, height)
            view._bottom = max(0, len(lines) - view._height)
            view._scroll = view._bottom if view._follow_tail else min(view._scroll, view._bottom)
            return UIContent(get_line=lambda index: lines[index], line_count=len(lines),
                             cursor_position=Point(x=0, y=view._scroll), show_cursor=False)

    def mouse_handler(self, event):
        view = self.view
        if view._selecting:
            return NotImplemented
        if event.event_type == MouseEventType.SCROLL_UP:
            view.scroll(-3)
        elif event.event_type == MouseEventType.SCROLL_DOWN:
            view.scroll(3)
        elif event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            with view._lock:
                link = view._image_links.get(event.position.y)
                path = link[2] if link and link[0] <= event.position.x < link[1] else None
                if path is None:
                    block = view._headers.get(event.position.y)
                    if block is None:
                        return NotImplemented
                    block["expanded"] = not block["expanded"]
                    view._follow_tail = False
            if path is not None:
                view.open_image(path)
            view.flush()
        else:
            return NotImplemented
        return None


class TerminalView:
    def __init__(self, key_bindings=None, *, context_usage=None, input=None, output=None):
        self.blocks = []
        self._tool_blocks = {}
        self._lock = RLock()
        self._stream = None
        self._headers = {}
        self._image_links = {}
        self._scroll = self._bottom = 0
        self._height = 1
        self._follow_tail = True
        self._busy = False
        self._selecting = False
        self._submitted = deque()
        self._input_ready = ThreadCondition(self._lock)
        self._hovered = None
        self._submitted_at = None
        self._turn_started = None
        self._turn_elapsed = None
        self._last_turn_status = ""
        self._last_turn_controls = []
        self._turn_id = None
        self._stop_requested = False
        self._ready = Event()
        self._thread = None
        self._ui_thread_id = None
        self._display_scheduled = False
        self._last_display_drain = 0
        self._display = DisplayQueue(self._wake_display)
        self._failure = None
        self._context_usage = context_usage or (lambda: None)
        self._last_usage = None
        self._activity = None
        self._activity_preview = None
        self._activity_thinking = False
        self._activity_task = None
        self._activity_tool_key = None
        self._activity_started = 0
        self._background_jobs = {}
        self._background_rows = {}
        self._finished_background_jobs = set()
        self._animation_scheduled = False
        self._closed = False

        self.buffer = Buffer(multiline=False, history=InMemoryHistory(),
                             read_only=Condition(lambda: self._selecting),
                             accept_handler=self._submit)
        self.control = _Transcript(self)
        self.window = Window(self.control, get_vertical_scroll=lambda _: self._scroll,
                             height=Dimension(min=1),
                             style="fg:ansiblack bg:ansiwhite")
        editor = Window(BufferControl(self.buffer, input_processors=[BeforeInput("> ")]),
                        height=lambda: Dimension(min=1, max=max(1, min(8, self.app.output.get_size().rows // 3))),
                        dont_extend_height=True, wrap_lines=True,
                        style="fg:default bg:default")
        # The frame supplies the boundary; keep the draft in the terminal's
        # own colors. Smaller terminals give padding back to the conversation.
        input_area = VSplit([
            Window(width=1),
            HSplit([
                Window(height=1, char="─", style="fg:ansibrightblack bg:default"),
                Window(height=self._input_padding),
                editor,
                Window(height=self._input_padding),
                Window(height=1, char="─", style="fg:ansibrightblack bg:default"),
            ]),
            Window(width=1),
        ], style="fg:default bg:default")
        hint = Window(FormattedTextControl(
            lambda: "选择文本（F2 返回）" if self._selecting else "按 F2 选中文本"
        ), height=1, style="fg:ansibrightblack bg:default")
        usage = Window(FormattedTextControl(self._context_status), height=1,
                       dont_extend_width=True, align=WindowAlign.RIGHT,
                       style="fg:ansibrightblack bg:default")
        elapsed = Window(FormattedTextControl(self._turn_controls), height=1,
                         style="fg:ansibrightblack bg:default")
        self.queue_control = _MessageQueue(self)
        queued = Window(self.queue_control,
                        height=lambda: Dimension(min=0, max=max(0, self.app.output.get_size().rows // 4),
                                                 preferred=len(self.queue_control.items())),
                        dont_extend_height=True, wrap_lines=False, style="bg:default")
        bindings = KeyBindings()

        @bindings.add("f2")
        def selection(event):
            with self._lock:
                self._selecting = not self._selecting
            self.app.invalidate()

        @bindings.add("enter", filter=Condition(lambda: self._selecting))
        def keep_draft(event):
            pass

        @bindings.add("c-c")
        def interrupt(event):
            if self._busy:
                self.stop_turn()
            else:
                self.buffer.reset()

        @bindings.add("c-d")
        def end(event):
            if not self._busy and not self._selecting:
                if self.buffer.text:
                    self.buffer.delete()
                else:
                    self._enqueue(EOFError())

        @bindings.add("pageup")
        def page_up(event):
            self.scroll(-self._height)

        @bindings.add("pagedown")
        def page_down(event):
            self.scroll(self._height)

        @bindings.add("c-end")
        def bottom(event):
            self._follow_tail = True

        merged = [bindings]
        if key_bindings is not None:
            merged.append(ConditionalKeyBindings(
                key_bindings, Condition(lambda: not self._selecting)))

        def after_render(app):
            # prompt-toolkit disables blinking when showing the cursor on redraw.
            if not app.is_done:
                app.output.set_cursor_shape(CursorShape.BLINKING_BEAM)
                app.output.flush()
                if ((self._activity or self._turn_started is not None or self._running_jobs()
                     or self._thinking_active())
                        and not self._selecting and not self._animation_scheduled):
                    self._animation_scheduled = True
                    app.loop.call_later(0.1 if self._turn_started is not None or self._activity_task
                                       or self._running_jobs() else 0.4,
                                       self._animate)

        self.app = Application(
            layout=Layout(HSplit([
                self.window, elapsed, queued,
                input_area, VSplit([hint, usage], height=1),
            ]), focused_element=editor),
            key_bindings=merge_key_bindings(merged), full_screen=True,
            mouse_support=Condition(lambda: not self._selecting),
            after_render=after_render,
            input=input, output=output, min_redraw_interval=0.1,
            terminal_size_polling_interval=None,
        )
        self.app.key_processor.before_key_press += self._clear_hover

    def _input_padding(self):
        return 1 if self.app.output.get_size().rows >= 18 else 0

    def _clear_hover(self, event):
        if self._hovered is not None and not self._selecting:
            self._hovered = None
            self.flush()

    def post_event(self, renderer, event):
        if self._closed:
            return
        if self._thread is None:
            renderer(event)
        else:
            self._display.post_event(renderer, event)

    def _wake_display(self):
        loop = self.app.loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._schedule_display)
            except RuntimeError:
                # Closing the loop can race a producer that already enqueued
                # its last event. A disappearing display must not fail a run.
                if not self._closed and not loop.is_closed():
                    raise

    def _schedule_display(self):
        if self._closed or self._display_scheduled:
            return
        self._display_scheduled = True
        delay = max(0, 0.1 - (monotonic() - self._last_display_drain))
        self.app.loop.call_later(delay, self._drain_display)

    def _drain_display(self):
        self._display_scheduled = False
        if self._closed:
            return
        self._last_display_drain = monotonic()
        try:
            self._display.drain()
        except BaseException as exc:
            self._display_failed(exc)

    def _display_failed(self, error):
        self._failure = error
        self._display.close()
        self._closed = True
        if self.app.is_running:
            self.app.exit()

    def _thinking_active(self):
        return self._stream is not None and self._stream["kind"] == "thinking" and not self._stream["complete"]

    def _enqueue(self, value):
        with self._input_ready:
            self._submitted.append(value)
            self._input_ready.notify()
        self.flush()

    def cancel_message(self, item):
        with self._input_ready:
            try:
                self._submitted.remove(item)
            except ValueError:
                return
            self._hovered = None
        self.flush()

    def _context_status(self):
        if not self._selecting:
            self._last_usage = self._context_usage()
        usage = self._last_usage
        if not usage or usage["limit"] <= 0:
            return "上下文窗口 —"
        return f"上下文窗口 {usage['tokens'] / usage['limit'] * 100:.1f}%"

    def start_turn(self):
        with self._lock:
            self._turn_id = object()
            self._stop_requested = False
            self._turn_started = self._submitted_at if self._submitted_at is not None else monotonic()
            self._submitted_at = None
            self._turn_elapsed = None
        self.flush()

    def finish_turn(self):
        with self._lock:
            self._turn_id = None
            if self._turn_started is not None:
                self._turn_elapsed = monotonic() - self._turn_started
                self._turn_started = None
        self.flush()

    def _turn_status(self):
        with self._lock:
            if not self._selecting:
                elapsed = (monotonic() - self._turn_started if self._turn_started is not None
                           else self._turn_elapsed)
                self._last_turn_status = "" if elapsed is None else f"耗时 {elapsed:.1f} 秒"
            return self._last_turn_status

    def _turn_controls(self):
        with self._lock:
            if not self._selecting:
                fragments = [("", self._turn_status())]
                turn_id = self._turn_id
                if turn_id is not None:
                    fragments.append(("", "  "))
                    if self._stop_requested:
                        fragments.append(("", "停止中"))
                    else:
                        style = "fg:ansired" if self._hovered is turn_id else "fg:ansiblue"
                        fragments.append((style, "停止",
                                          lambda event: self._stop_mouse(event, turn_id)))
                self._last_turn_controls = fragments
            return self._last_turn_controls

    def _stop_mouse(self, event, turn_id):
        with self._lock:
            if self._selecting or self._turn_id is not turn_id or self._stop_requested:
                return NotImplemented
            self._hovered = turn_id
            if event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
                self.stop_turn()
        self.flush()
        return None

    def stop_turn(self):
        with self._lock:
            if self._turn_id is None or self._stop_requested:
                return
            self._stop_requested = True
            # One interrupt per turn lets the existing cleanup finish undisturbed.
            os.kill(os.getpid(), signal.SIGINT)
        self.flush()

    @_display_only
    def set_activity(self, text, *, task=None, preview=None, thinking=False, tool_key=None):
        with self._lock:
            if (not self._activity or task != self._activity_task
                    or tool_key != self._activity_tool_key):
                self._activity_started = monotonic()
            self._activity = text
            self._activity_task = task if text else None
            self._activity_tool_key = tool_key if text else None
            self._activity_preview = preview if text else None
            self._activity_thinking = thinking
        self.flush()

    @_display_only
    def set_background_jobs(self, jobs):
        """Anchor each job at its first observation and update that row in place."""
        with self._lock:
            if self._closed:
                return
            observed = monotonic()
            current = {}
            for job in jobs:
                identifier = job["id"]
                if identifier in self._finished_background_jobs:
                    continue
                elapsed = job.get("elapsed_seconds")
                if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not isfinite(elapsed):
                    elapsed = None
                current[identifier] = {**job, "elapsed_seconds": elapsed, "observed": observed}
                if identifier not in self._background_rows:
                    block = {"kind": "line", "role": "tool", "text": "",
                             "background_job_id": identifier}
                    self._background_rows[identifier] = block
                    self.blocks.append(block)
            for identifier, previous in self._background_jobs.items():
                if identifier in current:
                    continue
                # A missing snapshot must not silently erase unconfirmed work.
                previous = dict(previous)
                if previous["state"] in _RUNNING_JOB_STATES:
                    elapsed = previous["elapsed_seconds"]
                    if elapsed is not None:
                        previous["elapsed_seconds"] += max(0, observed - previous["observed"])
                    previous.update(state="unknown", observed=observed)
                current[identifier] = previous
            self._background_jobs = current
        self.flush()

    @_display_only
    def complete_background_job(self, identifier):
        """Retire the live row when its existing completion enters the transcript."""
        if not isinstance(identifier, str) or not identifier:
            return
        with self._lock:
            self._finished_background_jobs.add(identifier)
            self._background_jobs.pop(identifier, None)
            block = self._background_rows.pop(identifier, None)
            if block is not None:
                self.blocks[:] = [item for item in self.blocks if item is not block]
        self.flush()

    def _running_jobs(self):
        return any(job["state"] in _RUNNING_JOB_STATES
                   for job in self._background_jobs.values())

    def _background_blocks(self):
        now = monotonic()
        for job in self._background_jobs.values():
            state, code = job["state"], job.get("returncode")
            failed = state in {"failed", "timed_out"} or (state == "exited" and code is not None and code != 0)
            status = {"starting": "启动中", "running": "运行中", "stopping": "停止中",
                      "exited": "完成", "stopped": "已停止", "orphaned": "失去进程归属",
                      "unconfirmed": "监管状态未确认"}.get(
                          state, "状态未知")
            if state == "exited" and code is None:
                status = "已退出（退出码未知）"
            if failed:
                status = ("已超时" if state == "timed_out" else "失败")
                if state != "timed_out" and code is not None:
                    status += f"（退出码 {code}）"
            cleanup_required = job.get("cleanup_required") is True
            if state == "exited" and cleanup_required:
                status = (status if failed else "入口已退出") + "（已清理残留进程）"
            name = " ".join(str(job.get("display_name", job["name"])).split())
            name = "".join(char for char in name if char.isprintable())
            if len(name) > 100:
                name = name[:99] + "…"
            label = "服务" if job.get("kind") == "service" else "后台"
            text = f"· {label}「{name}」：{status}"
            elapsed = job["elapsed_seconds"]
            if elapsed is not None:
                if state in _RUNNING_JOB_STATES:
                    elapsed += max(0, now - job["observed"])
                text += f" · {max(0, elapsed):.1f} 秒"
            yield {"kind": "line", "text": text, "background_job_id": job["id"],
                   "role": "error" if failed or cleanup_required or state in {"orphaned", "unknown", "unconfirmed", "stopped"} else "tool"}

    def _animate(self):
        self._animation_scheduled = False
        with self._lock:
            if self._selecting or (not self._activity and self._turn_started is None
                                   and not self._running_jobs() and not self._thinking_active()):
                return
        self.flush()

    def _submit(self, buffer):
        if not self._selecting:
            self._follow_tail = True
            self._enqueue(_QueuedMessage(buffer.text, monotonic()))
        return False

    @_display_only
    def append_line(self, text, role):
        with self._lock:
            self.finish_stream()
            self.blocks.append({"kind": "line", "text": str(text), "role": role})
        self.flush()

    @_display_only
    def upsert_tool(self, key, text, role="tool"):
        with self._lock:
            self.finish_stream()
            block = self._tool_blocks.get(key)
            if block is None:
                block = {"kind": "line", "text": str(text), "role": role}
                self.blocks.append(block)
                self._tool_blocks[key] = block
            else:
                block.update(text=str(text), role=role)
            if self._activity_tool_key is not None and self._activity_tool_key == key:
                self._activity = None
                self._activity_task = None
                self._activity_tool_key = None
                self._activity_preview = None
                self._activity_thinking = False
        self.flush()

    @_display_only
    def append_image_link(self, label, path):
        with self._lock:
            self.finish_stream()
            self.blocks.append({"kind": "line", "text": f"  {label}", "role": "tool",
                                "image_path": path})
        self.flush()

    def open_image(self, path):
        def open_file():
            try:
                if sys.platform == "win32":
                    os.startfile(path)
                else:
                    command = "open" if sys.platform == "darwin" else "xdg-open"
                    subprocess.run([command, path], check=True, timeout=5,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.SubprocessError):
                self.append_line(f"· 图片打开失败：{path}", "tool")

        Thread(target=open_file, daemon=True).start()

    def _body(self, text, channel, live):
        block = {"kind": channel, "text": text, "expanded": channel != "thinking" or live,
                 "complete": not live}
        self.blocks.append(block)
        return block

    @_display_only
    def append_body(self, text, channel="text"):
        with self._lock:
            self.finish_stream()
            self._body(text, channel, False)
        self.flush()

    @_display_only
    def write_delta(self, text, channel="text", live=True):
        if not text:
            return
        with self._lock:
            if self._stream is None or self._stream["kind"] != channel:
                self.finish_stream()
                self._stream = self._body("", channel, live)
                self._stream["chunks"] = []
            self._stream["chunks"].append(text)
        self.flush()

    @_display_only
    def finish_stream(self):
        with self._lock:
            changed = self._stream is not None
            if self._stream is not None:
                block = self._stream
                block["text"] = "".join(block.pop("chunks", []))
                block["complete"] = True
                if block["kind"] == "thinking":
                    block["expanded"] = False
                block.pop("plain_rows", None)
            self._stream = None
        if changed:
            self.flush()

    @_display_only
    def finish_response(self):
        with self._lock:
            self.finish_stream()
        self.flush()

    def scroll(self, amount):
        with self._lock:
            self._scroll = max(0, min(self._bottom, self._scroll + amount))
            self._follow_tail = self._scroll == self._bottom
        self.flush()

    def _render(self, block, width):
        key = (width, block["text"], block.get("role"))
        if block.get("render_key") != key:
            output = StringIO()
            console = Console(file=output, width=width, height=1, force_terminal=True,
                              color_system="standard", no_color=False, highlight=False)
            line_style = _STYLES.get(block.get("role"), "")
            if "image_path" in block:
                line_style += " underline"
            renderable = (Text(block["text"], style=line_style) if block["kind"] == "line"
                          else _Body(block["text"], thinking=block["kind"] == "thinking"))
            console.print(renderable)
            lines = list(split_lines(to_formatted_text(ANSI(output.getvalue()))))
            if lines and not fragment_list_to_text(lines[-1]):
                lines.pop()
            block["render_key"] = key
            block["lines"] = lines
        return block["lines"]

    def _plain_rows(self, block, width):
        preview = block["kind"] == "thinking"
        cache = block.get("plain_rows")
        if cache is None or cache.width != width:
            cache = block["plain_rows"] = _PlainRows(width, tail=_THINKING_PREVIEW_LINES if preview else None)
        chunks = block.get("chunks", [block["text"]])
        for index in range(cache.processed, len(chunks)):
            cache.append(chunks[index])
        cache.processed = len(chunks)
        rows = list(cache.rows)
        if preview:
            rows.extend([""] * (_THINKING_PREVIEW_LINES - len(rows)))
        style = _LINE_STYLES["thinking" if preview else "assistant"]
        return [[(style, text)] for text in rows]

    def _lines(self, width):
        lines = []
        self._headers = {}
        self._image_links = {}
        for status in self._background_blocks():
            self._background_rows[status["background_job_id"]].update(status)
        blocks = list(self.blocks)
        activity_row = None
        activity_block = None
        activity_target = None
        if self._activity:
            if self._activity_tool_key is not None:
                activity_target = self._tool_blocks.get(self._activity_tool_key)
            elapsed = monotonic() - self._activity_started
            dots = int(elapsed / 0.4) % 3 + 1
            duration = f" · {elapsed:.1f} 秒" if self._activity_task else ""
            if self._activity_preview is not None:
                suffix = "." * min(dots, width)
                prefix = self._activity + ("：" if self._activity_preview else "")
                prefix = set_cell_size(prefix, min(get_cwidth(prefix), max(0, width - len(suffix))))
                available = max(0, width - get_cwidth(prefix) - len(suffix))
                preview = self._activity_preview
                if get_cwidth(preview) > available:
                    while preview and get_cwidth(preview) > max(0, available - 1):
                        preview = preview[1:]
                    preview = ("…" + preview) if available else ""
                style = _LINE_STYLES["tool"]
                preview_style = _LINE_STYLES["thinking"] if self._activity_thinking else style
                activity_row = [(style, prefix), (preview_style, preview), (style, suffix)]
                activity_row.append((style, " " * max(0, width - fragment_list_width(activity_row))))
            else:
                activity_block = {"kind": "line", "role": "tool",
                                  "text": self._activity + "." * dots + duration}
                if activity_target is None:
                    blocks = [*blocks, activity_block]
        for block in blocks:
            if block is activity_target:
                if activity_row is not None:
                    lines.append(activity_row)
                    activity_row = None
                    continue
                block = activity_block
            role = "thinking" if block["kind"] == "thinking" else block.get("role", "assistant")
            style = _LINE_STYLES[role]
            if block["kind"] == "thinking":
                self._headers[len(lines)] = block
                active = not block["complete"]
                header = "▼ 思考" if block["expanded"] else "▶ 思考"
                if active:
                    header += "." * (int(monotonic() / 0.4) % 3 + 1)
                rows = [[(style, header)]]
                if block["expanded"]:
                    rows.extend(self._plain_rows(block, max(1, width)) if active
                                else self._render(block, max(1, width)))
            else:
                rows = [[(style, "assistant:")]] if block["kind"] == "text" else []
                rows.extend(self._plain_rows(block, max(1, width))
                            if block["kind"] == "text" and not block["complete"]
                            else self._render(block, max(1, width)))
            if "image_path" in block:
                for index, row in enumerate(rows):
                    text = fragment_list_to_text(row)
                    start, end = len(text) - len(text.lstrip()), len(text.rstrip())
                    if start < end:
                        # Window already converts terminal cells to character indices.
                        self._image_links[len(lines) + index] = (start, end, block["image_path"])
            # Paint blank lines and row tails with the message's own background.
            lines.extend([*row, (style, " " * max(0, width - _row_width(row)))]
                         for row in rows)
        if activity_row is not None:
            lines.append(activity_row)
        return lines or [[]]

    def isatty(self):
        return False

    def flush(self):
        if not self._selecting and not self._closed:
            self.app.invalidate()

    async def _watch_terminal_size(self, size):
        while True:
            await asyncio.sleep(0.5)
            current = self.app.output.get_size()
            if current != size:
                size = current
                self.app.invalidate()

    def start(self):
        def ready():
            # Sample before announcing readiness. The UI runs off the main
            # thread, so an early resize must not become the poller's baseline.
            self.app.create_background_task(self._watch_terminal_size(self.app.output.get_size()))
            self._schedule_display()
            self._ready.set()

        def run():
            self._ui_thread_id = get_ident()
            try:
                self.app.run(pre_run=ready, handle_sigint=False, set_exception_handler=False)
            except BaseException as exc:
                self._failure = exc
            finally:
                try:
                    self._display.close()
                    if self._failure is None:
                        self._display.drain()
                except BaseException as exc:
                    self._failure = exc
                finally:
                    self._ready.set()
                    with self._input_ready:
                        self._submitted.clear()
                        self._submitted.append(self._failure or EOFError())
                        self._input_ready.notify()

        self._thread = Thread(target=run, name="terminal-ui")
        self._thread.start()
        self._ready.wait()
        if self._failure is not None:
            raise self._failure
        return self

    def prompt(self):
        with self._input_ready:
            self._busy = False
            self.flush()
            while not self._submitted:
                self._input_ready.wait()
            value = self._submitted.popleft()
            self._busy = True
            if isinstance(value, _QueuedMessage):
                self._submitted_at = value.submitted_at
            if self._hovered is value:
                self._hovered = None
        self.flush()
        if isinstance(value, BaseException):
            raise value
        return value.text

    def close(self):
        def finish():
            try:
                self._display.close()
                if self._failure is None:
                    self._display.drain()
                    self.finish_response()
            except BaseException as exc:
                self._failure = exc
            finally:
                self._closed = True
                if self.app.is_running:
                    self.app.exit()

        if self._thread is not None:
            if self.app.is_running:
                if get_ident() == self._ui_thread_id:
                    finish()
                    return
                self.app.loop.call_soon_threadsafe(finish)
            if get_ident() != self._ui_thread_id:
                self._thread.join()
            self._thread = None
        else:
            finish()
