from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.segment import Segment, Segments
from rich.style import Style


_BODY_STYLE = Style(color="black", bgcolor="bright_white")
_THINKING_STYLE = Style(color="bright_black", bgcolor="default", reverse=False)


class _Body:
    def __init__(self, text, *, thinking=False):
        self.text = text
        self.style = _THINKING_STYLE if thinking else _BODY_STYLE

    def __rich_console__(self, console, options):
        for line in console.render_lines(Markdown(self.text, code_theme="bw"), options, pad=True):
            for segment in line:
                yield Segment(segment.text, (segment.style or Style()) + self.style, segment.control)
            yield Segment.line()


def _console(output):
    return Console(file=output, force_terminal=True, color_system="standard",
                   no_color=False, highlight=False)


def print_markdown(text, output, *, thinking=False):
    _console(output).print(_Body(text, thinking=thinking))


class MarkdownStream:
    """Preview the current text span, then leave one complete Markdown rendering."""

    def __init__(self, output, *, live=True, thinking=False):
        self.console = _console(output)
        self.text = ""
        self.thinking = thinking
        self.live = Live(console=self.console, transient=True, auto_refresh=False,
                         redirect_stdout=False, redirect_stderr=False) if live else None
        if self.live:
            self.live.start()

    def write(self, text):
        self.text += text
        if self.live:
            lines = self.console.render_lines(_Body(self.text, thinking=self.thinking), pad=False)
            # Keep the preview within the screen so clearing it cannot duplicate scrollback.
            tail = lines[-max(1, self.console.size.height - 1):]
            preview = [segment for line in tail for segment in [*line, Segment.line()]]
            self.live.update(Segments(preview[:-1]), refresh=True)

    def finish(self):
        if self.live:
            self.live.stop()
        self.console.print(_Body(self.text, thinking=self.thinking))
