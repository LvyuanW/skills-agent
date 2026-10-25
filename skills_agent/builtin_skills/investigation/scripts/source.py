#!/usr/bin/env python3
"""Capture sources and locate replayable evidence, without judging semantic support."""

from __future__ import annotations

import argparse
import bisect
import csv
from collections import deque
from decimal import Decimal
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import uuid


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HERE = Path(__file__).resolve().parent
COMMON = load_module("source_evidence_common", HERE.parents[1] / "feedback/scripts/_evidence.py")
MAX_BYTES = 64 * 1024 * 1024
MAX_REQUEST_BYTES = 65536
MAX_BINDINGS = 100


class SourceError(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(text, *, decimals=False):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SourceError(f"Duplicate JSON key: {key!r}")
            result[key] = value
        return result

    def constant(value):
        raise SourceError(f"Nonstandard JSON constant: {value}")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise SourceError("Metadata/request number exceeds finite float range")
        return number

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant,
                      parse_float=Decimal if decimals else finite_float)


def json_value(value):
    """Serialize selected values without losing decimal precision; not original JSON text."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return "[" + ",".join(json_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(key, ensure_ascii=False) + ":" + json_value(item)
                               for key, item in value.items()) + "}"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def read_stable(path, max_bytes=MAX_BYTES, prefix=None):
    """Read once through one descriptor and reject observed identity changes."""
    path = COMMON.absolute(path)
    link_before = path.lstat()
    if not stat.S_ISREG(path.stat().st_mode):
        raise SourceError("Expected a regular file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise SourceError("Expected a regular file")
        wanted = before.st_size if prefix is None else min(before.st_size, prefix)
        if wanted > max_bytes:
            raise SourceError(f"Source exceeds byte budget ({max_bytes}); select an explicit prefix or raise it")
        data = stream.read(wanted + (1 if prefix is None else 0))
        after = os.fstat(stream.fileno())
    if (COMMON.stat_identity(before) != COMMON.stat_identity(after)
            or COMMON.stat_identity(after) != COMMON.stat_identity(path.stat())
            or COMMON.stat_identity(link_before) != COMMON.stat_identity(path.lstat())
            or len(data) != wanted):
        raise SourceError("Source changed during capture; no stable version was recorded")
    return data, {"size": before.st_size, "mtime_ns": before.st_mtime_ns,
                  "resolved_path": str(path.resolve()), "consistency": "metadata_stable_during_read"}


def read_json(path, limit=MAX_REQUEST_BYTES):
    raw, _ = read_stable(path, limit)
    return strict_json(raw.decode("utf-8-sig"))


def object_value(value, label):
    if not isinstance(value, dict):
        raise SourceError(f"{label} must be an object")
    return value


def new_directory(store):
    root = COMMON.absolute(store)
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="source-", dir=root))


def save_object(directory, filename, data, representation, **extra):
    path = directory / filename
    with path.open("xb") as stream:
        stream.write(data)
    return {"path": filename, "sha256": digest(data), "bytes": len(data),
            "representation": representation, **extra}


def save_source(directory, origin, acquisition, objects):
    manifest = {"source_evidence": 1, "source_id": str(uuid.uuid4()),
                "captured_at": COMMON.utc_now(), "origin": origin,
                "acquisition": acquisition, "objects": objects}
    path = directory / "source.json"
    COMMON.atomic_json(path, manifest)
    return {"source_evidence": 1, "operation": "capture", "status": "captured",
            "source": str(path), "source_id": manifest["source_id"],
            "acquisition": acquisition,
            "objects": {key: {k: v for k, v in value.items() if k != "transformation"}
                        for key, value in objects.items()},
            "semantic_support": "not_evaluated"}


def capture(args):
    original = COMMON.absolute(args.path)
    raw, observation = read_stable(original, args.max_bytes, args.prefix_bytes)
    directory = new_directory(args.store)
    obj = save_object(directory, "raw.bin", raw, "raw_bytes")
    complete = len(raw) == observation["size"]
    return save_source(directory,
                       {"kind": "file", "path": str(original), **observation},
                       {"complete": complete, "scope": "full_file" if complete else "byte_prefix",
                        "byte_start": 0, "byte_end": len(raw),
                        "consistency": observation["consistency"]}, {"raw": obj})


def import_fetch(args):
    metadata_path = COMMON.absolute(args.path)
    metadata_raw, _ = read_stable(metadata_path, 1024 * 1024)
    metadata = object_value(strict_json(metadata_raw.decode("utf-8-sig")), "fetch metadata")
    if not isinstance(metadata.get("body_path"), str):
        raise SourceError("fetch metadata has no body_path")
    body_path = Path(metadata["body_path"])
    if not body_path.is_absolute():
        body_path = metadata_path.parent / body_path
    raw, _ = read_stable(body_path, args.max_bytes)
    if digest(raw) != metadata.get("sha256") or len(raw) != metadata.get("body_bytes"):
        raise SourceError("Fetched body does not match its recorded SHA-256/size")
    directory = new_directory(args.store)
    objects = {"raw": save_object(directory, "raw.bin", raw, "http_response_body")}
    save_object(directory, "fetch-metadata.json", metadata_raw, "fetch_metadata")
    extraction = metadata.get("extraction")
    extraction_error = None
    if isinstance(extraction, dict) and "path" in extraction:
        mode = extraction.get("format")
        if mode not in {"text", "html", "json"}:
            raise SourceError("Unsupported fetch extraction format")
        # Rebuild from the hash-checked body, rather than trusting a separately edited extracted.txt.
        fetch = load_module("source_fetch", HERE / "fetch.py")
        try:
            transformed = fetch.extract(directory / "raw.bin", mode,
                                        extraction.get("encoding") or "utf-8-sig",
                                        metadata.get("content_encoding", ""),
                                        args.max_view_bytes, directory / "text.txt")
            view, _ = read_stable(directory / "text.txt", args.max_view_bytes)
            objects["text"] = {"path": "text.txt", "sha256": digest(view), "bytes": len(view),
                               "representation": "derived_text", "parent_sha256": digest(raw),
                               "encoding": "utf-8", "transformation": transformed["transformation"],
                               "extractor_sha256": digest((HERE / "fetch.py").read_bytes()),
                               "input_encoding": transformed["encoding"], "format": mode}
        except (ValueError, OSError, LookupError, EOFError, RecursionError) as error:
            extraction_error = str(error)[:1000]
    elif isinstance(extraction, dict):
        extraction_error = extraction.get("error")
    result = save_source(directory,
                         {"kind": "http", "requested_url": metadata.get("requested_url"),
                          "final_url": metadata.get("final_url"),
                          "retrieved_at": metadata.get("retrieved_at"),
                          "fetch_metadata": "fetch-metadata.json",
                          "fetch_metadata_sha256": digest(metadata_raw)},
                         {"complete": metadata.get("response_body_complete") is True,
                          "scope": "response_body", "http_status": metadata.get("status"),
                          "acquisition_error": metadata.get("error"),
                          "extraction_error": extraction_error}, objects)
    result["operation"] = "import-fetch"
    return result


class Budget:
    def __init__(self, limit):
        self.limit = self.remaining = limit
        self.truncated = False

    def excerpt(self, text):
        visible = text[:self.remaining]
        self.remaining -= len(visible)
        partial = len(visible) < len(text)
        self.truncated |= partial
        return {"text": visible, "truncated": partial, "characters": len(text)}


def source_object(source_path, view, max_bytes):
    path = COMMON.absolute(source_path)
    manifest_raw, _ = read_stable(path, 1024 * 1024)
    manifest = object_value(strict_json(manifest_raw.decode("utf-8-sig")), "source manifest")
    if type(manifest.get("source_evidence")) is not int or manifest["source_evidence"] != 1:
        raise SourceError("Unsupported source manifest version")
    origin = object_value(manifest.get("origin"), "source origin")
    if origin.get("kind") not in {"file", "http"}:
        raise SourceError("Source origin kind must be file or http")
    if origin["kind"] == "file" and (not isinstance(origin.get("path"), str) or not origin["path"]):
        raise SourceError("Local source origin path must be a nonempty string")
    acquisition = object_value(manifest.get("acquisition"), "source acquisition")
    if type(acquisition.get("complete")) is not bool:
        raise SourceError("Source acquisition.complete must be a boolean")
    if acquisition.get("scope") not in {"full_file", "byte_prefix", "response_body"}:
        raise SourceError("Unknown source acquisition scope")
    if not isinstance(manifest.get("source_id"), str) or len(manifest["source_id"]) > 256:
        raise SourceError("Source id must be a string of at most 256 characters")
    objects = object_value(manifest.get("objects"), "source objects")
    view = view or ("text" if "text" in objects else "raw")
    if view not in {"raw", "text"} or view not in objects:
        raise SourceError(f"Unavailable source view: {view}")
    selected = object_value(objects[view], "source object")
    # Always verify the raw parent, including when quoting a derived view.
    contents = {}
    for key in dict.fromkeys(["raw", view]):
        obj = object_value(objects.get(key), "source object")
        if not isinstance(obj.get("path"), str) or not obj["path"]:
            raise SourceError("Snapshot path must be a nonempty string")
        integer(obj.get("bytes"), "snapshot byte count", 0)
        if not isinstance(obj.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", obj["sha256"]):
            raise SourceError("Snapshot needs a SHA-256 digest")
        candidate = (path.parent / obj["path"]).resolve()
        if not candidate.is_relative_to(path.parent.resolve()):
            raise SourceError("Snapshot path escapes its source directory")
        raw, _ = read_stable(candidate, max_bytes)
        if digest(raw) != obj.get("sha256") or len(raw) != obj.get("bytes"):
            raise SourceError(f"Snapshot integrity mismatch: {key}")
        contents[key] = raw
    if view == "text" and selected.get("parent_sha256") != objects["raw"]["sha256"]:
        raise SourceError("Derived view does not identify its raw parent")
    identity = {"manifest": str(path), "manifest_sha256": digest(manifest_raw),
                "source_id": manifest["source_id"], "view": view,
                "sha256": selected["sha256"], "raw_sha256": objects["raw"]["sha256"],
                "representation": selected["representation"],
                "acquisition": manifest["acquisition"]}
    if "transformation" in selected:
        identity["transformation"] = str(selected["transformation"])[:1000]
    return manifest, identity, contents[view]


def decode(raw, encoding):
    if encoding not in {"utf-8", "utf-8-sig"}:
        raise SourceError("Only UTF-8/UTF-8-SIG views are supported; use a recorded extraction for other formats")
    if raw.startswith((b"%PDF-", b"PK\x03\x04", b"\x89PNG", b"\xff\xd8\xff")):
        raise SourceError("Binary document requires a located extraction; this tool does not perform OCR")
    text = raw.decode(encoding, errors="strict")
    if "\0" in text:
        raise SourceError("NUL characters require an appropriate binary reader")
    return text


def lines_of(text):
    lines, start = [], 0
    for match in re.finditer(r"\r\n|\r|\n", text):
        lines.append((start, match.end()))
        start = match.end()
    if start < len(text) or not lines:
        lines.append((start, len(text)))
    return lines


def integer(value, label, minimum=1, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise SourceError(f"{label} must be an integer from {minimum}" +
                          (f" to {maximum}" if maximum is not None else ""))
    return value


def span_for_range(lines, selection):
    object_value(selection, "range")
    if set(selection) - {"start_line", "end_line", "start_column", "end_column"}:
        raise SourceError("Unknown text range field")
    start_line = integer(selection.get("start_line", 1), "start_line", maximum=len(lines))
    end_line = integer(selection.get("end_line", start_line), "end_line", start_line, len(lines))
    start = lines[start_line - 1][0]
    end = lines[end_line - 1][1]
    start += integer(selection.get("start_column", 1), "start_column", maximum=lines[start_line - 1][1] - start + 1) - 1
    if "end_column" in selection:
        base = lines[end_line - 1][0]
        end = base + integer(selection["end_column"], "end_column", maximum=end - base + 1) - 1
    if end < start:
        raise SourceError("End precedes start")
    return start, end


def text_locator(lines, start, end):
    starts = [line[0] for line in lines]
    a = bisect.bisect_right(starts, start) - 1
    b = bisect.bisect_right(starts, end) - 1
    return {"format": "text", "start_offset": start, "end_offset": end,
            "offset_unit": "Unicode characters",
            "start_line": a + 1, "start_column": start - starts[a] + 1,
            "end_line": b + 1, "end_column": end - starts[b] + 1}


def text_context(text, lines, start, end, count):
    starts = [line[0] for line in lines]
    a = bisect.bisect_right(starts, start) - 1
    b = bisect.bisect_right(starts, max(start, end - 1)) - 1
    left = lines[max(0, a - count)][0]
    right = lines[min(len(lines) - 1, b + count)][1]
    return text[left:right]


def pointer(value, path):
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise SourceError("JSON Pointer must be empty or start with /")
    current, parent = value, value
    for token in path.split("/")[1:]:
        if re.search(r"~(?![01])", token):
            raise SourceError("JSON Pointer only accepts ~0 and ~1 escapes")
        key = token.replace("~1", "/").replace("~0", "~")
        parent = current
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(current):
            current = current[int(key)]
        else:
            raise KeyError(f"Missing JSON Pointer member: {key}")
    return current, parent


def item(locator, content, context, representation):
    return {"locator": locator, "content": content, "context": context,
            "representation": representation, "content_sha256": digest(content.encode("utf-8"))}


def select_text(text, request, max_matches):
    lines = lines_of(text)
    if "offsets" in request:
        offsets = request["offsets"]
        if not isinstance(offsets, list) or len(offsets) != 2:
            raise SourceError("offsets requires [start, end] character offsets")
        start = integer(offsets[0], "start offset", 0, len(text))
        end = integer(offsets[1], "end offset", start, len(text))
    elif "range" in request:
        start, end = span_for_range(lines, request["range"])
    else:
        start, end = 0, len(text)
    scope = {"start_offset": start, "end_offset": end, "unit": "Unicode characters"}
    if "quote" not in request:
        if "range" not in request and "offsets" not in request:
            raise SourceError("Text selection needs a quote, range or offsets")
        matches = [(start, end)]
        mode = "exact"
        complete = True
    else:
        query = request["quote"]
        if not isinstance(query, str) or not query.strip() or len(query) > 4096:
            raise SourceError("quote must contain 1-4096 characters and not only whitespace")
        mode = request.get("match", "exact")
        matches, complete = [], True
        if mode == "exact":
            position = start
            while True:
                found = text.find(query, position, end)
                if found < 0:
                    break
                matches.append((found, found + len(query)))
                if len(matches) > max_matches:
                    complete = False
                    break
                position = found + 1
        elif mode == "whitespace":
            pattern = "".join(r"[ \t\r\n]+" if re.fullmatch(r"[ \t\r\n]+", part)
                              else re.escape(part) for part in re.split(r"([ \t\r\n]+)", query))
            for match in re.finditer(pattern, text[start:end]):
                matches.append((start + match.start(), start + match.end()))
                if len(matches) > max_matches:
                    complete = False
                    break
        else:
            raise SourceError("match must be exact or whitespace")
    count = len(matches)
    status = ("not_found" if not count else "normalized_candidate" if mode == "whitespace"
              else "ambiguous" if count > 1 else "resolved")
    context_count = request.get("context", 2)
    records = [item(text_locator(lines, a, b), text[a:b],
                    text_context(text, lines, a, b, context_count), "decoded_text")
               for a, b in matches[:max_matches]]
    return status, records, {"complete": complete, "scope": scope,
                             "matches": count, "count_is_lower_bound": not complete}


def select_json(text, request):
    pointer_path = request.get("pointer", "")
    if request["format"] == "jsonl":
        lines = lines_of(text)
        line = integer(request.get("line"), "line", maximum=len(lines))
        a, b = lines[line - 1]
        selected = text[a:b]
        locator = {"format": "jsonl", "line": line, "pointer": pointer_path}
        context = text_context(text, lines, a, b, request.get("context", 2))
    else:
        selected = text
        locator = {"format": "json", "pointer": pointer_path}
        context = None
    value = strict_json(selected, decimals=True)
    try:
        actual, parent = pointer(value, pointer_path)
    except KeyError:
        return "not_found", [], {"complete": True, "scope": locator, "matches": 0}
    return "resolved", [item(locator, json_value(actual),
                              context if context is not None else json_value(parent),
                              "parsed_json_value")], {"complete": True, "scope": locator, "matches": 1}


def select_csv(text, request, max_matches):
    if ("row" in request) == ("key" in request):
        raise SourceError("CSV selection requires row or key, not both")
    row = integer(request["row"], "row") if "row" in request else None
    key = request.get("key")
    if key is not None:
        object_value(key, "CSV key")
        if (set(key) != {"column", "equals"} or not isinstance(key["equals"], str)
                or len(key["equals"]) > 4096):
            raise SourceError("CSV key requires a column number and a string equals value")
        integer(key["column"], "key column")
    column = request.get("column")
    if column is not None:
        integer(column, "column")
    delimiter = request.get("delimiter", "\t" if request["format"] == "tsv" else ",")
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in "\r\n\0":
        raise SourceError("CSV delimiter must be one non-newline character")
    count = request.get("context", 2)
    lines = lines_of(text)
    records, pending, previous, matches = [], [], 0, 0
    recent = deque(maxlen=count + 1)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    for index, fields in enumerate(reader, 1):
        first, last = previous + 1, reader.line_num
        previous = last
        for record in pending:
            record["context_end"] = last
            record["remaining"] -= 1
        pending = [record for record in pending if record["remaining"]]
        recent.append((first, last))
        selected = index == row if row is not None else (
            key["column"] <= len(fields) and fields[key["column"] - 1] == key["equals"])
        if selected:
            matches += 1
            if column is not None and column > len(fields):
                if row is not None:
                    return "not_found", [], {"complete": True, "scope": {"row": row}, "matches": 0}
                raise SourceError(f"Selected column is missing in matching CSV row {index}")
            if len(records) < max_matches:
                locator = {"format": "csv", "row": index, "column": column, "delimiter": delimiter,
                           "line_start": first, "line_end": last}
                record = {"locator": locator,
                          "content": json_value(fields[column - 1] if column is not None else fields),
                          "context_start": recent[0][0], "context_end": last, "remaining": count}
                records.append(record)
                if count:
                    pending.append(record)
        if row is not None and index >= row + count:
            break
    scope = {"column": column, "delimiter": delimiter,
             **({"row": row} if row is not None else {"key": key})}
    if not matches:
        return "not_found", [], {"complete": True, "scope": scope, "matches": 0}
    selected = [item(record["locator"], record["content"],
                     text[lines[record["context_start"] - 1][0]:lines[record["context_end"] - 1][1]],
                     "parsed_csv_value") for record in records]
    return "resolved" if matches == 1 else "ambiguous", selected, {
        "complete": True, "scope": scope, "matches": matches, "returned_matches": len(selected)}


def validate_request(request):
    object_value(request, "selection request")
    common = {"format", "view", "encoding", "context", "claim_id"}
    selectors = {"text": {"quote", "match", "range", "offsets"},
                 "json": {"pointer"}, "jsonl": {"line", "pointer"},
                 "csv": {"row", "key", "column", "delimiter"},
                 "tsv": {"row", "key", "column", "delimiter"}}
    format_name = request.get("format", "text")
    if not isinstance(format_name, str) or format_name not in selectors:
        raise SourceError("Supported formats: text, json, csv, tsv, jsonl; use a recorded view for other formats")
    if "view" in request and (not isinstance(request["view"], str) or request["view"] not in {"raw", "text"}):
        raise SourceError("view must be raw or text")
    unknown = set(request) - common - selectors[format_name]
    if unknown:
        raise SourceError("Unknown selection fields: " + ", ".join(sorted(unknown)))
    if "range" in request and "offsets" in request:
        raise SourceError("Use range or offsets, not both")
    if "match" in request and "quote" not in request:
        raise SourceError("match requires a quote")
    integer(request.get("context", 2), "context", 0, 20)
    if "claim_id" in request and (not isinstance(request["claim_id"], str) or len(request["claim_id"]) > 256):
        raise SourceError("claim_id must be a string of at most 256 characters")
    return {**request, "format": format_name}


def locate(source_path, request, budget, max_matches=20, max_bytes=MAX_BYTES):
    request = validate_request(request)
    manifest, identity, raw = source_object(source_path, request.get("view"), max_bytes)
    encoding = request.get("encoding", "utf-8-sig")
    text = decode(raw, encoding)
    if request["format"] == "text":
        status, records, search = select_text(text, request, max_matches)
    elif request["format"] in {"json", "jsonl"}:
        status, records, search = select_json(text, request)
    else:
        status, records, search = select_csv(text, request, max_matches)
    if request["format"] == "text":
        # Map only the selected character boundaries, without building a per-character table.
        offsets = sorted({record["locator"][field] for record in records
                          for field in ["start_offset", "end_offset"]})
        byte_offsets, previous = {}, 0
        position = 3 if encoding == "utf-8-sig" and raw.startswith(b"\xef\xbb\xbf") else 0
        for offset in offsets:
            position += len(text[previous:offset].encode("utf-8"))
            byte_offsets[offset] = position
            previous = offset
        for record in records:
            locator = record["locator"]
            locator["byte_start"] = byte_offsets[locator["start_offset"]]
            locator["byte_end"] = byte_offsets[locator["end_offset"]]
            locator["byte_basis"] = "hash-identified source view"
    result = {"source_evidence": 1, "operation": "locate", "status": status,
              "source": identity, "search": search, "matches": [],
              "semantic_support": "not_evaluated"}
    for record in records:
        result["matches"].append({"locator": record["locator"],
                                  "representation": record["representation"],
                                  "content_sha256": record["content_sha256"],
                                  "excerpt": budget.excerpt(record["content"]),
                                  "context": budget.excerpt(record["context"])})
    if status == "resolved":
        record = records[0]
        selector = {"view": identity["view"], "encoding": encoding, "context": 0,
                    "format": record["locator"]["format"]}
        if selector["format"] == "text":
            selector["offsets"] = [record["locator"]["start_offset"], record["locator"]["end_offset"]]
        elif selector["format"] in {"json", "jsonl"}:
            selector["pointer"] = record["locator"]["pointer"]
            if selector["format"] == "jsonl":
                selector["line"] = record["locator"]["line"]
        else:
            selector.update({key: record["locator"][key] for key in ["row", "delimiter"]})
            if record["locator"]["column"] is not None:
                selector["column"] = record["locator"]["column"]
        result["binding"] = {"source_evidence": 1, "source": identity,
                             "selector": selector, "content_sha256": record["content_sha256"],
                             "representation": record["representation"],
                             "claim_id": request.get("claim_id"), "semantic_support": "not_evaluated"}
    result["preview"] = {"character_budget": budget.limit, "truncated": budget.truncated}
    return result


def verify(args, budget):
    value = read_json(args.bindings, 2 * 1024 * 1024)
    values = value if isinstance(value, list) else [value]
    if not values or len(values) > MAX_BINDINGS:
        raise SourceError(f"Supply between 1 and {MAX_BINDINGS} bindings/locate results")
    checks = []
    for index, saved in enumerate(values):
        check = {"index": index, "status": "unresolved", "semantic_support": "not_evaluated"}
        try:
            object_value(saved, "binding")
            binding = object_value(saved.get("binding", saved), "binding")
            identity = object_value(binding.get("source"), "binding source")
            if binding.get("source_evidence") != 1 or "selector" not in binding:
                raise SourceError("A resolved locate result or its binding is required")
            current = locate(identity["manifest"], binding["selector"], budget,
                             max_matches=1, max_bytes=args.max_bytes)
            for field in ["manifest_sha256", "source_id", "view", "sha256", "raw_sha256"]:
                if current["source"].get(field) != identity.get(field):
                    raise SourceError(f"Recorded source version changed: {field}")
            if (current["status"] != "resolved"
                    or current["binding"]["content_sha256"] != binding.get("content_sha256")
                    or current["binding"]["representation"] != binding.get("representation")):
                raise SourceError("Selected content no longer matches the binding")
            check.update(status="resolved", claim_id=binding.get("claim_id"),
                         snapshot="unchanged", source=current["source"],
                         selection=current["matches"][0])
            if args.check_origin:
                manifest = read_json(identity["manifest"], 1024 * 1024)
                if manifest["origin"]["kind"] != "file":
                    check["origin"] = {"status": "not_checked", "reason": "HTTP refresh requires a new fetch"}
                    check["status"] = "historical_only"
                elif manifest["acquisition"].get("scope") != "full_file":
                    check["origin"] = {"status": "not_checked", "reason": "A byte prefix is not a full-file version"}
                    check["status"] = "historical_only"
                else:
                    observed = COMMON.fingerprint(manifest["origin"]["path"], max_bytes=args.max_bytes)
                    origin_status = ("unchanged" if observed.get("sha256") == identity["raw_sha256"]
                                     else "changed") if observed["status"] == "ok" else observed["status"]
                    check["origin"] = {**observed, "status": origin_status}
                    if origin_status != "unchanged":
                        check["status"] = "historical_only"
        except (OSError, ValueError, KeyError, TypeError, csv.Error, RecursionError) as error:
            check["status"] = "unresolved"
            check["error"] = str(error)[:1000]
        checks.append(check)
    return {"source_evidence": 1, "operation": "verify",
            "status": "resolved" if all(check["status"] == "resolved" for check in checks) else "unresolved",
            "checks": checks, "semantic_support": "not_evaluated",
            "preview": {"character_budget": budget.limit, "truncated": budget.truncated}}


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("Expected a positive integer")
    return number


def parser():
    cli = argparse.ArgumentParser(description=__doc__, epilog=(
        "Exit 0: requested capture or unique location resolved in its stated scope; "
        "1: missing/ambiguous/candidate/stale binding; 2: input, acquisition or integrity error. "
        "No result establishes semantic support or overall task completion."))
    commands = cli.add_subparsers(dest="operation", required=True)
    for command in ["capture", "import-fetch"]:
        sub = commands.add_parser(command)
        sub.add_argument("path")
        sub.add_argument("--store", required=True)
        sub.add_argument("--max-bytes", type=positive, default=MAX_BYTES)
        if command == "capture":
            sub.add_argument("--prefix-bytes", type=positive)
        else:
            sub.add_argument("--max-view-bytes", type=positive, default=5 * 1024 * 1024)
    sub = commands.add_parser("locate")
    sub.add_argument("--source", required=True)
    sub.add_argument("--request", required=True)
    sub.add_argument("--max-matches", type=positive, default=20)
    sub.add_argument("--max-chars", type=positive, default=12000)
    sub.add_argument("--max-bytes", type=positive, default=MAX_BYTES)
    sub = commands.add_parser("verify")
    sub.add_argument("--bindings", required=True)
    sub.add_argument("--check-origin", action="store_true")
    sub.add_argument("--max-chars", type=positive, default=12000)
    sub.add_argument("--max-bytes", type=positive, default=MAX_BYTES)
    return cli


def main():
    args = parser().parse_args()
    try:
        if args.operation == "capture":
            result = capture(args)
        elif args.operation == "import-fetch":
            result = import_fetch(args)
        elif args.operation == "locate":
            if args.max_matches > 100:
                raise SourceError("max-matches cannot exceed 100")
            result = locate(args.source, read_json(args.request), Budget(args.max_chars),
                            args.max_matches, args.max_bytes)
        else:
            result = verify(args, Budget(args.max_chars))
        status = 0 if result["status"] in {"captured", "resolved"} else 1
    except (OSError, ValueError, KeyError, TypeError, csv.Error, RecursionError) as error:
        result = {"source_evidence": 1, "operation": args.operation, "status": "error",
                  "error": str(error)[:1000], "semantic_support": "not_evaluated"}
        status = 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    if status:
        print("source.py: " + result.get("error", result["status"]), file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
