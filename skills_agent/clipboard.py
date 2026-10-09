"""Read macOS clipboard text, file references, or an image snapshot."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


_SCRIPT = r'''
ObjC.import("AppKit");

function readClipboard(pasteboard, imagePath) {
    var files = [];
    var items = pasteboard.pasteboardItems;
    if (!items.isNil()) {
        for (var i = 0; i < items.count; i++) {
            var value = items.objectAtIndex(i).stringForType($.NSPasteboardTypeFileURL);
            if (!value.isNil()) {
                var url = $.NSURL.URLWithString(value);
                if (!url.isNil() && url.isFileURL) files.push(ObjC.unwrap(url.path));
            }
        }
    }
    if (files.length) return {text: "", files: files};

    var image = $.NSImage.alloc.initWithPasteboard(pasteboard);
    if (!image.isNil()) {
        var bitmap = $.NSBitmapImageRep.imageRepWithData(image.TIFFRepresentation);
        if (bitmap.isNil()) throw new Error("Cannot decode clipboard image");
        var png = bitmap.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $({}));
        if (png.isNil() || !png.writeToFileAtomically(imagePath, true)) {
            throw new Error("Cannot save clipboard image");
        }
        return {text: "", files: [imagePath]};
    }

    var text = pasteboard.stringForType($.NSPasteboardTypeString);
    return {text: text.isNil() ? "" : ObjC.unwrap(text), files: []};
}

function run(argv) {
    return JSON.stringify(readClipboard($.NSPasteboard.generalPasteboard, argv[0]));
}
'''


def read_clipboard(directory: Path) -> tuple[str, list[Path]]:
    """Snapshot a pasted image in directory; keep copied files as source paths."""
    if sys.platform != "darwin":
        raise ValueError("Clipboard paste currently requires macOS")
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    image_path = directory / f"clipboard-{uuid4().hex}.png"
    try:
        result = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _SCRIPT, str(image_path)],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise OSError(f"Cannot read clipboard: {exc.stderr.strip()}") from exc
    try:
        value = json.loads(result.stdout)
        text, paths = value["text"], value["files"]
        if not isinstance(text, str) or not isinstance(paths, list) or any(
            not isinstance(path, str) for path in paths
        ):
            raise ValueError
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Invalid clipboard response") from exc
    return text, [Path(path) for path in paths]
