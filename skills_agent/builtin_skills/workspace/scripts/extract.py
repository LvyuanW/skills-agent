"""Read bounded, located content from JSON, CSV, DOCX, XLSX, and PDF files."""

import argparse
import csv
import importlib
import importlib.util
import json
import posixpath
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from decimal import Decimal
from pathlib import Path


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def select_pointer(value, pointer):
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must be empty or start with /")
    for raw in pointer.split("/")[1:]:
        for index, char in enumerate(raw):
            if char == "~" and (index + 1 == len(raw) or raw[index + 1] not in "01"):
                raise ValueError(f"Invalid JSON Pointer escape: {raw!r}")
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if token not in value:
                raise ValueError(f"Missing JSON member: {token!r}")
            value = value[token]
        elif isinstance(value, list):
            if not token.isascii() or not token.isdigit() or (len(token) > 1 and token[0] == "0"):
                raise ValueError(f"Invalid array index: {token!r}")
            index = int(token)
            if index >= len(value):
                raise ValueError(f"Array index out of range: {index}")
            value = value[index]
        else:
            raise ValueError(f"Cannot descend through scalar at {token!r}")
    return value


def reject_constant(token):
    raise ValueError(f"Non-standard JSON number: {token}")


def exact_json(value, depth=0):
    """Serialize parsed JSON without converting exact decimals to floats."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("JSON numbers must be finite")
        return str(value)
    indent = "  " * (depth + 1)
    closing = "  " * depth
    if isinstance(value, list):
        items = [exact_json(item, depth + 1) for item in value]
        return "[\n" + ",\n".join(indent + item for item in items) + "\n" + closing + "]" if items else "[]"
    if isinstance(value, dict):
        items = [json.dumps(key, ensure_ascii=False) + ": " + exact_json(item, depth + 1)
                 for key, item in value.items()]
        return "{\n" + ",\n".join(indent + item for item in items) + "\n" + closing + "}" if items else "{}"
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def json_selection(args):
    with args.path.open(encoding="utf-8-sig") as handle:
        value = select_pointer(json.load(handle, parse_float=Decimal,
                                         parse_constant=reject_constant), args.pointer)
    text = exact_json(value)
    if args.offset > len(text):
        raise ValueError("Offset exceeds the selected JSON text")
    end = min(len(text), args.offset + args.max_chars)
    return {"source": str(args.path), "format": "json", "pointer": args.pointer,
            "offset": args.offset, "text": text[args.offset:end],
            "truncated": end < len(text),
            "next": {"offset": end} if end < len(text) else None}


def csv_records(args):
    # Output is bounded separately; CSV parsing still needs a complete field.
    csv.field_size_limit(sys.maxsize)
    with args.path.open(encoding=args.encoding, newline="") as handle:
        reader = csv.reader(handle, delimiter=args.delimiter)
        previous_line = 0
        for index, row in enumerate(reader, 1):
            location = {"row": index, "line_start": previous_line + 1,
                        "line_end": reader.line_num}
            previous_line = reader.line_num
            yield index, location, json.dumps(row, ensure_ascii=False), {}


def word_text(node):
    chunks = []
    for item in node.iter():
        if item.tag == W + "t":
            chunks.append(item.text or "")
        elif item.tag == W + "tab":
            chunks.append("\t")
        elif item.tag in (W + "br", W + "cr"):
            chunks.append("\n")
    return "".join(chunks)


def docx_records(args):
    with zipfile.ZipFile(args.path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find(W + "body")
    if body is None:
        raise ValueError("DOCX has no word/document.xml body")
    index = paragraph = table = 0
    for item in body:
        if item.tag == W + "p":
            index += 1
            paragraph += 1
            yield index, {"part": "word/document.xml", "paragraph": paragraph}, word_text(item), {}
        elif item.tag == W + "tbl":
            table += 1
            for row_index, row in enumerate(item.findall(W + "tr"), 1):
                for column, cell in enumerate(row.findall(W + "tc"), 1):
                    index += 1
                    yield index, {"part": "word/document.xml", "table": table,
                                  "row": row_index, "column": column}, \
                        "\n".join(word_text(p) for p in cell.iter(W + "p")), {}


def xlsx_records(args):
    with zipfile.ZipFile(args.path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships
                   if item.attrib.get("TargetMode") != "External"}
        sheets = workbook.find(S + "sheets")
        if sheets is None or not len(sheets):
            raise ValueError("XLSX has no worksheets")
        selected = next((sheet for sheet in sheets if sheet.attrib["name"] == args.sheet), None)
        if selected is None:
            raise ValueError(f"Unknown sheet {args.sheet!r}; available: "
                             + ", ".join(sheet.attrib["name"] for sheet in sheets))
        target = targets.get(selected.attrib.get(R + "id"))
        if target is None:
            raise ValueError("Worksheet relationship is missing or external")
        part = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                 else posixpath.join("xl", target))
        if part.startswith("../"):
            raise ValueError("Invalid worksheet part path")
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            with archive.open("xl/sharedStrings.xml") as handle:
                for _, item in ET.iterparse(handle, events=("end",)):
                    if item.tag == S + "si":
                        shared.append("".join(t.text or "" for t in item.iter(S + "t")))
                        item.clear()
        index = 0
        with archive.open(part) as handle:
            for _, item in ET.iterparse(handle, events=("end",)):
                if item.tag != S + "c":
                    continue
                value = item.findtext(S + "v")
                formula = item.findtext(S + "f")
                data_type = item.attrib.get("t", "n")
                if data_type == "inlineStr":
                    text = "".join(t.text or "" for t in item.iter(S + "t"))
                elif data_type == "s":
                    if value is None or not value.isdigit() or int(value) >= len(shared):
                        raise ValueError(f"Invalid shared string index at {item.attrib.get('r')}")
                    text = shared[int(value)]
                elif data_type == "b":
                    if value not in ("0", "1"):
                        raise ValueError(f"Invalid boolean at {item.attrib.get('r')}")
                    text = "true" if value == "1" else "false"
                else:
                    text = value or ""
                if value is not None or data_type == "inlineStr" or formula is not None:
                    index += 1
                    extra = {"type": data_type}
                    if formula is not None:
                        extra.update(formula=formula, cached_value=value)
                    yield index, {"sheet": selected.attrib["name"],
                                  "cell": item.attrib.get("r"), "part": part}, text, extra
                item.clear()


def pdf_records(args):
    reader = args.reader
    if reader == "auto":
        reader = next((name for name in ("pypdf", "PyPDF2")
                       if importlib.util.find_spec(name) is not None), None)
        reader = reader or ("pdftotext" if shutil.which("pdftotext") else None)
    if reader in ("pypdf", "PyPDF2"):
        if importlib.util.find_spec(reader) is None:
            raise ValueError(f"Requested PDF reader {reader} is not installed")
        pdf = importlib.import_module(reader).PdfReader(str(args.path))
        if pdf.is_encrypted:
            raise ValueError("Encrypted PDF requires an explicit password workflow")
        for index in range(args.start - 1, min(len(pdf.pages), args.start + args.limit)):
            yield index + 1, {"page": index + 1, "reader": reader}, \
                pdf.pages[index].extract_text() or "", {}
    elif reader == "pdftotext" and shutil.which("pdftotext"):
        with tempfile.TemporaryDirectory(prefix="workspace-pdf-") as directory:
            target = Path(directory) / "pages.txt"
            result = subprocess.run(
                [shutil.which("pdftotext"), "-enc", "UTF-8", "-f", str(args.start),
                 "-l", str(args.start + args.limit), str(args.path), str(target)],
                capture_output=True, timeout=args.timeout,
            )
            if result.returncode:
                raise ValueError(f"pdftotext failed (exit {result.returncode}): "
                                 + result.stderr.decode("utf-8", errors="replace")[-2000:])
            text = target.read_text(encoding="utf-8")
            pages = text.split("\f")
            if pages and pages[-1] == "":
                pages.pop()
            for index, page in enumerate(pages, args.start):
                yield index, {"page": index, "reader": reader}, page, {}
    else:
        raise ValueError("No supported PDF reader available; use an already installed "
                         "pypdf, PyPDF2, or pdftotext. Nothing was installed.")


def bounded_records(args):
    records = {"csv": csv_records, "docx": docx_records,
               "xlsx": xlsx_records, "pdf": pdf_records}[args.format](args)
    entries = []
    remaining = args.max_chars
    cursor = None
    selected_count = 0
    found_start = False
    try:
        for index, location, text, extra in records:
            if index < args.start:
                continue
            if selected_count == args.limit or remaining == 0:
                cursor = {"start": index, "offset": 0}
                break
            offset = args.offset if index == args.start else 0
            if offset > len(text):
                raise ValueError("Offset exceeds the selected record's text")
            found_start = True
            end = min(len(text), offset + remaining)
            entry = {"index": index, "location": location, "offset": offset,
                     "text": text[offset:end], "partial": end < len(text), **extra}
            entries.append(entry)
            remaining -= end - offset
            selected_count += 1
            if end < len(text):
                cursor = {"start": index, "offset": end}
                break
        if args.offset and not found_start:
            raise ValueError("Cannot apply an offset to a missing record")
    finally:
        records.close()
    return {"source": str(args.path), "format": args.format, "records": entries,
            "truncated": cursor is not None, "next": cursor}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="format", required=True)
    for name in ("json", "csv", "docx", "xlsx", "pdf"):
        command = commands.add_parser(name)
        command.add_argument("path", type=Path)
        command.add_argument("--offset", type=int, default=0)
        command.add_argument("--max-chars", type=int, default=12000)
        if name == "json":
            command.add_argument("--pointer", default="")
        else:
            command.add_argument("--start", type=int, default=1)
            command.add_argument("--limit", type=int, default=50)
        if name == "csv":
            command.add_argument("--encoding", default="utf-8-sig")
            command.add_argument("--delimiter", default=",")
        elif name == "xlsx":
            command.add_argument("--sheet", required=True)
        elif name == "pdf":
            command.add_argument("--reader", choices=("auto", "pypdf", "PyPDF2", "pdftotext"), default="auto")
            command.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.offset < 0 or args.max_chars < 1:
        parser.error("Require offset >= 0 and max-chars >= 1")
    if args.format != "json" and (args.start < 1 or args.limit < 1):
        parser.error("Require start >= 1 and limit >= 1")
    if args.format == "csv" and len(args.delimiter) != 1:
        parser.error("Delimiter must be one character")
    if args.format == "pdf" and args.timeout <= 0:
        parser.error("Timeout must be positive")
    try:
        args.path = args.path.resolve()
        args.path = args.path.resolve(strict=True)
        if not args.path.is_file():
            raise ValueError("Source must be a regular file")
        result = json_selection(args) if args.format == "json" else bounded_records(args)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Reader libraries have their own corruption/decryption exceptions.
        # Keep those failures in the same command-line result contract.
        print(json.dumps({"source": str(args.path), "format": args.format,
                          "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
