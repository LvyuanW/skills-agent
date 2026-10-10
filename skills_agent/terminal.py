"""Interactive terminal transcript with expandable thinking blocks."""

from io import StringIO
import os
from queue import Queue
import signal
from threading import Event, RLock, Thread
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
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl, UIContent, UIControl
from prompt_toolkit.layout.processors import BeforeInput
from prompt_toolkit.mouse_events import MouseButton, MouseEventType
from rich.console import Console
from rich.text import Text

from .markdown import _Body


_STYLES = {"user": "bright_white on black", "assistant": "black on bright_white",
           "tool": "blue on default"}
_LINE_STYLES = {"user": "fg:ansiwhite bg:ansiblack",
                "assistant": "fg:ansiblack bg:ansiwhite",
                "tool": "fg:ansiblue bg:default",
                "thinking": "fg:ansibrightblack bg:default"}


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
        if event.event_type == MouseEventType.SCROLL_UP:
            view.scroll(-3)
        elif event.event_type == MouseEventType.SCROLL_DOWN:
            view.scroll(3)
        elif event.event_type == MouseEventType.MOUSE_UP and event.button == MouseButton.LEFT:
            with view._lock:
                block = view._headers.get(event.position.y)
                if block is None:
                    return NotImplemented
                block["expanded"] = not block["expanded"]
                view._follow_tail = False
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
        self._scroll = self._bottom = 0
        self._height = 1
        self._follow_tail = True
        self._busy = False
        self._selecting = False
        self._submitted = Queue()
        self._submitted_at = None
        self._turn_started = None
        self._turn_elapsed = None
        self._last_turn_status = ""
        self._ready = Event()
        self._thread = None
        self._failure = None
        self._context_usage = context_usage or (lambda: None)
        self._last_usage = None
        self._activity = None
        self._activity_started = 0
        self._animation_scheduled = False

        self.buffer = Buffer(multiline=False, history=InMemoryHistory(),
                             read_only=Condition(lambda: self._busy or self._selecting),
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
        elapsed = Window(FormattedTextControl(self._turn_status), height=1,
                         style="fg:ansibrightblack bg:default")
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
                os.kill(os.getpid(), signal.SIGINT)
            else:
                self.buffer.reset()

        @bindings.add("c-d")
        def end(event):
            if not self._busy and not self._selecting:
                if self.buffer.text:
                    self.buffer.delete()
                else:
                    self._busy = True
                    self._submitted.put(EOFError())

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
                key_bindings, Condition(lambda: not self._busy and not self._selecting)))

        def after_render(app):
            # prompt-toolkit disables blinking when showing the cursor on redraw.
            if not app.is_done:
                app.output.set_cursor_shape(CursorShape.BLINKING_BEAM)
                app.output.flush()
                if ((self._activity or self._turn_started is not None)
                        and not self._selecting and not self._animation_scheduled):
                    self._animation_scheduled = True
                    app.loop.call_later(0.1 if self._turn_started is not None else 0.4,
                                       self._animate)

        self.app = Application(
            layout=Layout(HSplit([
                self.window, elapsed,
                editor, Window(height=1, style="bg:default"), VSplit([hint, usage], height=1),
            ]), focused_element=editor),
            key_bindings=merge_key_bindings(merged), full_screen=True,
            mouse_support=Condition(lambda: not self._selecting),
            after_render=after_render,
            input=input, output=output, min_redraw_interval=0.03,
        )

    def _context_status(self):
        if not self._selecting:
            self._last_usage = self._context_usage()
        usage = self._last_usage
        if not usage or usage["limit"] <= 0:
            return "上下文窗口 —"
        return f"上下文窗口 {usage['tokens'] / usage['limit'] * 100:.1f}%"

    def start_turn(self):
        with self._lock:
            self._turn_started = self._submitted_at if self._submitted_at is not None else monotonic()
            self._submitted_at = None
            self._turn_elapsed = None
        self.flush()

    def finish_turn(self):
        with self._lock:
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

    def set_activity(self, text):
        with self._lock:
            self._activity = text
            self._activity_started = monotonic()
        self.flush()

    def _animate(self):
        self._animation_scheduled = False
        with self._lock:
            if self._selecting or (not self._activity and self._turn_started is None):
                return
        self.flush()

    def _submit(self, buffer):
        if not self._busy and not self._selecting:
            self._busy = True
            self._follow_tail = True
            self._submitted_at = monotonic()
            self._submitted.put(buffer.text)
        return False

    def append_line(self, text, role):
        with self._lock:
            self.finish_stream()
            self.blocks.append({"kind": "line", "text": str(text), "role": role})
        self.flush()

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
            renderable = (Text(block["text"], style=_STYLES[block["role"]]) if block["kind"] == "line"
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
        blocks = self.blocks
        if self._activity:
            dots = int((monotonic() - self._activity_started) / 0.4) % 3 + 1
            blocks = [*blocks, {"kind": "line", "role": "tool",
                               "text": self._activity + "." * dots}]
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
            # Paint blank lines and row tails with the message's own background.
            lines.extend([*row, (style, " " * max(0, width - fragment_list_width(row)))]
                         for row in rows)
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
                self._submitted.put(self._failure or EOFError())

        self._thread = Thread(target=run, name="terminal-ui")
        self._thread.start()
        self._ready.wait()
        if self._failure is not None:
            raise self._failure
        return self

    def prompt(self):
        self._busy = False
        self.flush()
        value = self._submitted.get()
        self._busy = True
        if isinstance(value, BaseException):
            raise value
        return value

    def close(self):
        if self._thread is not None:
            if self.app.is_running:
                self.app.loop.call_soon_threadsafe(self.app.exit)
            self._thread.join()
            self._thread = None
