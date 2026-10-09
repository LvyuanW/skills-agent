from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.segment import Segment, Segments
from rich.style import Style


_BODY_STYLE = Style(color="black", bgcolor="bright_white")


class _Body:
    def __init__(self, text):
        self.text = text

    def __rich_console__(self, console, options):
        for segment in console.render(Markdown(self.text, code_theme="bw"), options):
            yield Segment(segment.text, (segment.style or Style()) + _BODY_STYLE, segment.control)


def _console(output):
    return Console(file=output, force_terminal=True, color_system="standard",
                   no_color=False, highlight=False)


def print_markdown(text, output):
    _console(output).print(_Body(text))


class MarkdownStream:
    """Preview the current text span, then leave one complete Markdown rendering."""

    def __init__(self, output, *, live=True):
        self.console = _console(output)
        self.text = ""
        self.live = Live(console=self.console, transient=True, auto_refresh=False,
                         redirect_stdout=False, redirect_stderr=False) if live else None
        if self.live:
            self.live.start()

    def write(self, text):
        self.text += text
        if self.live:
            lines = self.console.render_lines(_Body(self.text), pad=False)
            # Keep the preview within the screen so clearing it cannot duplicate scrollback.
            tail = lines[-max(1, self.console.size.height - 1):]
            preview = [segment for line in tail for segment in [*line, Segment.line()]]
            self.live.update(Segments(preview[:-1]), refresh=True)

    def finish(self):
        if self.live:
            self.live.stop()
        self.console.print(_Body(self.text))
