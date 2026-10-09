"""Clipboard attachments represented by editable markers in the input buffer."""

import mimetypes
from pathlib import Path

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.key_binding import KeyBindings

from .clipboard import read_clipboard


class ClipboardDraft:
    def __init__(self, directory: Path, on_error):
        self.directory = directory
        self.on_error = on_error
        self._files = {}
        self.bindings = KeyBindings()
        self.bindings.add("c-v")(self.paste)

    def paste(self, event):
        try:
            text, files = read_clipboard(self.directory)
            paths = [path.resolve(strict=True) for path in files]
            for path in paths:
                if not path.is_file():
                    raise ValueError(f"Attachment is not a file: {path}")
        except (ValueError, OSError) as exc:
            message = str(exc)
            run_in_terminal(lambda: self.on_error(message))
            return
        if paths:
            markers = []
            for path in paths:
                number = len(self._files) + 1
                mime_type = mimetypes.guess_type(path.name)[0]
                if mime_type in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                    marker = f"[图片{number}]"
                else:
                    name = "".join(c for c in path.name if c.isprintable())
                    name = name.replace("[", "(").replace("]", ")")
                    if len(name) > 40:
                        name = name[:39] + "…"
                    marker = f"[文件{number}:{name}]"
                self._files[marker] = path
                markers.append(marker)
            text = " ".join(markers)
        event.current_buffer.insert_text(text)

    def attachments(self, text):
        """Only intact markers in the submitted text attach their files."""
        return sorted(
            ((marker, path) for marker, path in self._files.items() if marker in text),
            key=lambda item: text.index(item[0]),
        )
