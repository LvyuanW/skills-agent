"""Interactive terminal transcript with expandable thinking blocks."""

from collections import deque
from dataclasses import dataclass
from io import StringIO
import os
import signal
import subprocess
import sys
from threading import Condition as ThreadCondition, Event, RLock, Thread
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


_STYLES = {"user": "bright_white on black", "assistant": "black on bright_white",
           "tool": "blue on default"}
_LINE_STYLES = {"user": "fg:ansiwhite bg:ansiblack",
                "assistant": "fg:ansiblack bg:ansiwhite",
                "tool": "fg:ansiblue bg:default",
                "thinking": "fg:ansibrightblack bg:default"}


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
            # Keep the displayed frame stable while the terminal owns selection.
            if not view._selecting:
                self.lines = view._lines(width)
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
        self._lock = RLock()
        self._stream = None
        self._response = []
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
        self._failure = None
        self._context_usage = context_usage or (lambda: None)
        self._last_usage = None
        self._activity = None
        self._activity_preview = None
        self._activity_thinking = False
        self._activity_task = None
        self._activity_started = 0
        self._animation_scheduled = False

        self.buffer = Buffer(multiline=False, history=InMemoryHistory(),
                             read_only=Condition(lambda: self._selecting),
                             accept_handler=self._submit)
        self.control = _Transcript(self)
        self.window = Window(self.control, get_vertical_scroll=lambda _: self._scroll,
                             style="fg:ansiblack bg:ansiwhite")
        editor = Window(BufferControl(self.buffer, input_processors=[BeforeInput("> ")]),
                        dont_extend_height=True, wrap_lines=True,
                        style="fg:ansiwhite bg:ansiblack")
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
                        height=lambda: Dimension(min=0, preferred=len(self.queue_control.items())),
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
                if ((self._activity or self._turn_started is not None)
                        and not self._selecting and not self._animation_scheduled):
                    self._animation_scheduled = True
                    app.loop.call_later(0.1 if self._turn_started is not None or self._activity_task else 0.4,
                                       self._animate)

        self.app = Application(
            layout=Layout(HSplit([
                self.window, elapsed, queued,
                editor, Window(height=1, style="bg:default"), VSplit([hint, usage], height=1),
            ]), focused_element=editor),
            key_bindings=merge_key_bindings(merged), full_screen=True,
            mouse_support=Condition(lambda: not self._selecting),
            after_render=after_render,
            input=input, output=output, min_redraw_interval=0.03,
        )
        self.app.key_processor.before_key_press += self._clear_hover

    def _clear_hover(self, event):
        if self._hovered is not None and not self._selecting:
            self._hovered = None
            self.flush()

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

    def set_activity(self, text, *, task=None, preview=None, thinking=False):
        with self._lock:
            if not self._activity or task != self._activity_task:
                self._activity_started = monotonic()
            self._activity = text
            self._activity_task = task if text else None
            self._activity_preview = preview if text else None
            self._activity_thinking = thinking
        self.flush()

    def _animate(self):
        self._animation_scheduled = False
        with self._lock:
            if self._selecting or (not self._activity and self._turn_started is None):
                return
        self.flush()

    def _submit(self, buffer):
        if not self._selecting:
            self._follow_tail = True
            self._enqueue(_QueuedMessage(buffer.text, monotonic()))
        return False

    def append_line(self, text, role):
        with self._lock:
            self.finish_stream()
            self.blocks.append({"kind": "line", "text": str(text), "role": role})
        self.flush()

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
        if channel == "thinking" and live:
            self._response.append(block)
        return block

    def append_body(self, text, channel="text"):
        with self._lock:
            self.finish_stream()
            self._body(text, channel, False)
        self.flush()

    def write_delta(self, text, channel="text", live=True):
        if not text:
            return
        with self._lock:
            if self._stream is None or self._stream["kind"] != channel:
                self._stream = self._body("", channel, live)
            self._stream["text"] += text
        self.flush()

    def finish_stream(self):
        with self._lock:
            self._stream = None

    def finish_response(self):
        with self._lock:
            self.finish_stream()
            for block in self._response:
                block["complete"] = True
                block["expanded"] = False
            self._response.clear()
        self.flush()

    def scroll(self, amount):
        with self._lock:
            self._scroll = max(0, min(self._bottom, self._scroll + amount))
            self._follow_tail = self._scroll == self._bottom
        self.flush()

    def _render(self, block, width):
        key = (width, block["text"])
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

    def _lines(self, width):
        lines = []
        self._headers = {}
        self._image_links = {}
        blocks = self.blocks
        activity_row = None
        if self._activity:
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
                blocks = [*blocks, {"kind": "line", "role": "tool",
                                   "text": self._activity + "." * dots + duration}]
        for block in blocks:
            role = "thinking" if block["kind"] == "thinking" else block.get("role", "assistant")
            style = _LINE_STYLES[role]
            if block["kind"] == "thinking":
                self._headers[len(lines)] = block
                rows = [[(style, "▼ 思考" if block["expanded"] else "▶ 思考")]]
                if block["expanded"]:
                    rows.extend(self._render(block, max(1, width)))
            else:
                rows = [[(style, "assistant:")]] if block["kind"] == "text" else []
                rows.extend(self._render(block, max(1, width)))
            if "image_path" in block:
                for index, row in enumerate(rows):
                    text = fragment_list_to_text(row)
                    start, end = len(text) - len(text.lstrip()), len(text.rstrip())
                    if start < end:
                        # Window already converts terminal cells to character indices.
                        self._image_links[len(lines) + index] = (start, end, block["image_path"])
            # Paint blank lines and row tails with the message's own background.
            lines.extend([*row, (style, " " * max(0, width - fragment_list_width(row)))]
                         for row in rows)
        if activity_row is not None:
            lines.append(activity_row)
        return lines or [[]]

    def isatty(self):
        return False

    def flush(self):
        if not self._selecting:
            self.app.invalidate()

    def start(self):
        def run():
            try:
                self.app.run(pre_run=self._ready.set, handle_sigint=False, set_exception_handler=False)
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
        if self._thread is not None:
            if self.app.is_running:
                self.app.loop.call_soon_threadsafe(self.app.exit)
            self._thread.join()
            self._thread = None
