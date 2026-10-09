"""Interactive terminal transcript with expandable thinking blocks."""

from io import StringIO
import os
from queue import Queue
import signal
from threading import Event, RLock, Thread

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.data_structures import Point
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import ANSI, fragment_list_to_text, fragment_list_width, to_formatted_text
from prompt_toolkit.formatted_text.utils import split_lines
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import ConditionalKeyBindings, KeyBindings, merge_key_bindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import BufferControl, UIContent, UIControl
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

    def create_content(self, width, height):
        view = self.view
        with view._lock:
            lines = view._lines(width)
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
    def __init__(self, key_bindings=None, *, input=None, output=None):
        self.blocks = []
        self._lock = RLock()
        self._stream = None
        self._response = []
        self._headers = {}
        self._scroll = self._bottom = 0
        self._height = 1
        self._follow_tail = True
        self._busy = False
        self._submitted = Queue()
        self._ready = Event()
        self._thread = None
        self._failure = None

        self.buffer = Buffer(multiline=False, history=InMemoryHistory(),
                             read_only=Condition(lambda: self._busy), accept_handler=self._submit)
        self.control = _Transcript(self)
        self.window = Window(self.control, get_vertical_scroll=lambda _: self._scroll,
                             style="fg:ansiblack bg:ansiwhite")
        editor = Window(BufferControl(self.buffer, input_processors=[BeforeInput("> ")]),
                        dont_extend_height=True, wrap_lines=True,
                        style="fg:ansiwhite bg:ansiblack")
        bindings = KeyBindings()

        @bindings.add("c-c")
        def interrupt(event):
            if self._busy:
                os.kill(os.getpid(), signal.SIGINT)
            else:
                self.buffer.reset()

        @bindings.add("c-d")
        def end(event):
            if not self._busy:
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
            merged.append(ConditionalKeyBindings(key_bindings, Condition(lambda: not self._busy)))
        self.app = Application(
            layout=Layout(HSplit([self.window, editor]), focused_element=editor),
            key_bindings=merge_key_bindings(merged), full_screen=True, mouse_support=True,
            input=input, output=output, min_redraw_interval=0.03,
        )

    def _submit(self, buffer):
        if not self._busy:
            self._busy = True
            self._follow_tail = True
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
        for block in self.blocks:
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
