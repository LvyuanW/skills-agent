#!/usr/bin/env python3
"""Prepare, compose, or attach bounded images through the shared feedback protocol."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import url2pathname


# Load by file path: shell execution does not require skills_agent on sys.path.
_helper = Path(__file__).resolve().parents[2] / "feedback/scripts/feedback.py"
_spec = importlib.util.spec_from_file_location("_images_feedback", _helper)
feedback = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(feedback)
FeedbackError = feedback.FeedbackError
FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}


def source_path(value: str) -> Path:
    if value.startswith("file:"):
        parsed = urlsplit(value)
        if (parsed.netloc not in {"", "localhost"} or parsed.query or parsed.fragment
                or not parsed.path.startswith("/")):
            raise FeedbackError("SOURCE requires a local file:// URL without a query or fragment")
        return feedback.absolute(url2pathname(parsed.path))
    if "://" in value:
        raise FeedbackError("Download HTTP(S) sources first with investigation/scripts/fetch.py; "
                            "pass the saved path and retain its --source-url")
    return feedback.absolute(value)


def output_path(value: str, sources: list[Path]) -> Path:
    output = feedback.absolute(value)
    if output.suffix.lower() not in FORMATS:
        raise FeedbackError("--output requires .jpg, .jpeg, .png, or .webp; format follows the extension")
    if output.is_symlink() or (output.exists() and not output.is_file()):
        raise FeedbackError("--output must be a regular file path, not a symlink or directory")
    for source in sources:
        if (output.resolve() == source.resolve()
                or (output.exists() and source.exists() and os.path.samefile(output, source))):
            raise FeedbackError("--output must be a separate copy; never overwrite a source image")
    return output


def pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError:
        raise FeedbackError("prepare/compose require Pillow in this Python environment; "
                            "use the project's installed Python or install its declared dependencies") from None
    return Image, ImageDraw, ImageFont, ImageOps


def read_image(path: Path):
    Image, _, _, ImageOps = pillow()
    feedback.image_format(path)
    with Image.open(path) as original:
        original.load()
        metadata = {"path": str(path), "source_url": path.as_uri(),
                    "stored_dimensions": list(original.size),
                    "exif_orientation": original.getexif().get(274, 1)}
        if getattr(original, "n_frames", 1) > 1:
            metadata["selected_frame"] = 0
        image = ImageOps.exif_transpose(original).convert("RGBA")
    metadata["source_dimensions"] = list(image.size)
    return image, metadata


def rgb(image):
    Image, _, _, _ = pillow()
    if image.mode == "RGB":
        return image
    background = Image.new("RGB", image.size, "white")
    background.paste(image, mask=image.getchannel("A") if image.mode == "RGBA" else None)
    return background


def bounded_encoding(image, format_name: str) -> tuple[bytes, tuple[int, int], int | None]:
    """Try finite quality/size ladders entirely in memory before any file write."""
    Image, _, _, _ = pillow()
    original_size = image.size
    if format_name == "JPEG":
        image = rgb(image)
    qualities = (90, 82, 72, 60, 48) if format_name in {"JPEG", "WEBP"} else (None,)
    longest = max(original_size)
    minimum = min(longest, 128)
    last_size = None
    # Original dimensions first; at most 13 sizes, with a final 128-pixel long side.
    for step in range(13):
        target = max(minimum, round(longest * 0.75 ** step)) if step < 12 else minimum
        size = tuple(max(1, round(axis * target / longest)) for axis in original_size)
        if size == last_size:
            continue
        last_size = size
        candidate = image if size == original_size else image.resize(size, Image.Resampling.LANCZOS)
        for quality in qualities:
            buffer = io.BytesIO()
            options = {"optimize": True} if format_name in {"JPEG", "PNG"} else {"method": 4}
            if quality is not None:
                options["quality"] = quality
            candidate.save(buffer, format=format_name, **options)
            encoded = buffer.getvalue()
            if len(encoded) <= feedback.MAX_IMAGE_BYTES:
                return encoded, size, quality
        if target == minimum:
            break
    raise FeedbackError("Image cannot fit within 1000000 bytes after bounded compression; "
                        "crop the relevant region or use a simpler image")


def save_image(output: Path, encoded: bytes, label: str, source_url: str | None = None) -> dict:
    if len(encoded) > feedback.MAX_IMAGE_BYTES:
        raise FeedbackError("Refusing to write an image larger than 1000000 bytes")
    feedback.validate_source_url(source_url)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".images-", suffix=output.suffix,
                                         dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
        feedback.attachment(temporary, label, source_url)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return feedback.attachment(output, label, source_url)


def save_bounded(image, output: str | Path, *, label: str = "Generated image") -> dict:
    """Save a newly generated Pillow image only once it fits, and return its feedback envelope."""
    filename = getattr(image, "filename", None)
    sources = [feedback.absolute(filename)] if filename else []
    path = output_path(str(output), sources)
    encoded, dimensions, quality = bounded_encoding(image, FORMATS[path.suffix.lower()])
    attached = save_image(path, encoded, label)
    return feedback.envelope("image", str(path), {
        "operation": "generate", "output": str(path), "bytes": len(encoded),
        "dimensions": list(dimensions), "source_dimensions": list(image.size),
        "quality": quality,
    }, [attached])


def prepare(args: argparse.Namespace) -> dict:
    source = source_path(args.source)
    output = output_path(args.output, [source])
    feedback.validate_source_url(args.source_url)
    image, metadata = read_image(source)
    metadata["source_url"] = args.source_url or source.as_uri()
    encoded, dimensions, quality = bounded_encoding(image, FORMATS[output.suffix.lower()])
    attached = save_image(output, encoded, args.label or source.name, metadata["source_url"])
    observations = {"operation": "prepare", "sources": [metadata],
                    "source_urls": [metadata["source_url"]], "output": str(output),
                    "bytes": len(encoded), "dimensions": list(dimensions), "quality": quality,
                    "scale_to_source": [image.width / dimensions[0], image.height / dimensions[1]],
                    "coordinates": "Source coordinates use the EXIF-oriented first frame; no crop."}
    return feedback.envelope("image", str(source), observations, [attached])


def compose(args: argparse.Namespace) -> dict:
    Image, ImageDraw, ImageFont, _ = pillow()
    sources = [source_path(value) for value in args.sources]
    output = output_path(args.output, sources)
    source_urls = args.source_url or [path.as_uri() for path in sources]
    if len(source_urls) != len(sources):
        raise FeedbackError("Provide one --source-url per source in the same order, or omit all")
    for source_url in source_urls:
        feedback.validate_source_url(source_url)
    columns = args.columns or math.ceil(math.sqrt(len(sources)))
    if columns > len(sources):
        raise FeedbackError("--columns cannot exceed the number of source images")
    rows = math.ceil(len(sources) / columns)
    cell = min(512, 4096 // max(columns, rows))
    if cell < 96:
        raise FeedbackError("Too many panels for a useful single image; select fewer sources or split "
                            "into several composites while keeping the request at most 5 images")
    label_height, padding = 28, 8
    canvas = Image.new("RGB", (columns * cell, rows * cell), "#e5e7eb")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default(size=20)
    panels = []
    for index, (source, source_url) in enumerate(zip(sources, source_urls)):
        image, metadata = read_image(source)
        left, top = index % columns * cell, index // columns * cell
        box = (cell - 2 * padding, cell - label_height - 2 * padding)
        scale = min(1, box[0] / image.width, box[1] / image.height)
        dimensions = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        thumbnail = rgb(image).resize(dimensions, Image.Resampling.LANCZOS)
        x = left + (cell - dimensions[0]) // 2
        y = top + label_height + (cell - label_height - dimensions[1]) // 2
        canvas.paste(thumbnail, (x, y))
        draw.rectangle((left, top, left + cell - 1, top + label_height - 1), fill="#172554")
        draw.text((left + padding, top + 3), str(index + 1), fill="white", font=font)
        metadata.update(number=index + 1, source_url=source_url,
                        panel_box=[left, top, left + cell, top + cell],
                        image_box=[x, y, x + dimensions[0], y + dimensions[1]])
        panels.append(metadata)
    encoded, dimensions, quality = bounded_encoding(canvas, FORMATS[output.suffix.lower()])
    sx, sy = dimensions[0] / canvas.width, dimensions[1] / canvas.height
    for panel in panels:
        for key in ("panel_box", "image_box"):
            panel[key] = [round(value * (sx if index % 2 == 0 else sy), 6)
                          for index, value in enumerate(panel[key])]
        left, top, right, bottom = panel["image_box"]
        panel["scale_to_source"] = [panel["source_dimensions"][0] / (right - left),
                                    panel["source_dimensions"][1] / (bottom - top)]
    attached = save_image(output, encoded, args.label or f"Numbered overview of {len(sources)} images")
    observations = {"operation": "compose", "source_urls": source_urls, "panels": panels,
                    "output": str(output), "bytes": len(encoded), "dimensions": list(dimensions),
                    "quality": quality, "columns": columns,
                    "coordinates": "Boxes are [left, top, right, bottom] edges in the sent image. "
                    "Subtract image_box origin, then multiply by scale_to_source for EXIF-oriented "
                    "source coordinates. Labels map to panel numbers; no source crop."}
    return feedback.envelope("image", str(output), observations, [attached])


def attach(args: argparse.Namespace) -> dict:
    args.paths = [str(source_path(value)) for value in args.paths]
    return feedback.image_feedback(args)


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="operation", required=True)
    prepare_cli = commands.add_parser("prepare", help="compress a separate copy and attach it")
    prepare_cli.add_argument("source")
    prepare_cli.add_argument("--output", required=True)
    prepare_cli.add_argument("--source-url")
    compose_cli = commands.add_parser("compose", help="attach one numbered grid of source images")
    compose_cli.add_argument("sources", nargs="+")
    compose_cli.add_argument("--output", required=True)
    compose_cli.add_argument("--columns", type=feedback.positive_int)
    compose_cli.add_argument("--source-url", action="append")
    attach_cli = commands.add_parser("attach", help="attach up to five already bounded images")
    attach_cli.add_argument("paths", nargs="+")
    attach_cli.add_argument("--source-url", action="append")
    for command in (prepare_cli, compose_cli, attach_cli):
        command.add_argument("--label")
    return cli


def main() -> int:
    args = parser().parse_args()
    try:
        result = {"prepare": prepare, "compose": compose, "attach": attach}[args.operation](args)
        status = 0
    except (FeedbackError, OSError, ValueError) as exc:
        print(f"images.py: {exc}", file=sys.stderr)
        source = getattr(args, "source", None) or getattr(args, "output", None) or args.paths[0]
        result = feedback.envelope("image", str(source), {"operation": args.operation, "error": str(exc)})
        status = 1
    print(json.dumps(result, ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
