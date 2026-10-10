#!/usr/bin/env python3
"""Fetch a known HTTP(S) source and preserve its response as local evidence."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import io
import json
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def checked_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        # Accessing port also validates malformed numeric ports.
        parsed.port
    except ValueError as error:
        raise ValueError("URL must be a valid absolute HTTP(S) address") from error
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute HTTP(S) address")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Credentials in URLs are not supported")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("URL must not contain whitespace or control characters")
    return urllib.parse.urldefrag(value)[0]


def remaining_time(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Total request timed out")
    return remaining


def read_chunk(response, size: int, deadline: float) -> bytes:
    remaining = remaining_time(deadline)
    stream = response.fp if isinstance(response, urllib.error.HTTPError) else response
    # urllib's HTTPResponse exposes no public setter for a timeout after opening.
    # Update its actual socket rather than allowing each read another full timeout.
    buffered = getattr(stream, "fp", None)
    connection = getattr(getattr(buffered, "raw", None), "_sock", None)
    if connection is not None:
        connection.settimeout(remaining)
    return stream.read1(size)


class Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, deadline: float) -> None:
        self.observed: list[dict] = []
        self.deadline = deadline

    def redirect_request(self, request, fp, code, message, headers, new_url):
        # HTTPRedirectHandler opens the next request with the current request's timeout.
        request.timeout = remaining_time(self.deadline)
        # Validate every hop, including credentials introduced by a server.
        try:
            destination = checked_url(new_url)
        except ValueError:
            self.observed.append({"from": request.full_url, "status": code,
                                  "blocked": True})
            raise ValueError("Redirect target is not an unauthenticated HTTP(S) URL")
        self.observed.append({"from": request.full_url, "status": code,
                              "to": destination})
        return super().redirect_request(request, fp, code, message, headers, destination)

    def http_error_302(self, request, fp, code, message, headers):
        location = headers.get("Location") or headers.get("URI")
        if location is None:
            return None
        try:
            destination = urllib.parse.urljoin(request.full_url, location)
            redirected = self.redirect_request(request, fp, code, message, headers, destination)
        except ValueError:
            fp.close()
            raise
        visited = getattr(request, "redirect_dict", {})
        if visited.get(redirected.full_url, 0) >= self.max_repeats or len(visited) >= self.max_redirections:
            # Retain a real HTTPError response for the common bounded-body acquisition path.
            raise urllib.error.HTTPError(request.full_url, code, "Redirect limit reached", headers, fp)
        visited[redirected.full_url] = visited.get(redirected.full_url, 0) + 1
        redirected.redirect_dict = visited
        # The standard handler drains this body without our size/deadline checks.
        # A redirected body is not the final source; close it and fetch the destination.
        fp.close()
        return self.parent.open(redirected, timeout=remaining_time(self.deadline))

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class ReadableHTML(HTMLParser):
    """Static text extraction; this deliberately does not emulate a browser."""

    BLOCKS = {"address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
              "dt", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr",
              "li", "main", "nav", "ol", "p", "pre", "section", "table", "td", "th",
              "title", "tr", "ul"}
    IGNORED = {"script", "style", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored: list[str] = []

    def handle_starttag(self, tag, attributes):
        if tag in self.IGNORED:
            self.ignored.append(tag)
        if not self.ignored and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        lines = [re.sub(r" +", " ", line).strip() for line in "".join(self.parts).splitlines()]
        return "\n".join(line for line in lines if line) + "\n"


def extraction_mode(requested: str, media_type: str) -> str | None:
    if requested == "none":
        return None
    if requested != "auto":
        return requested
    if media_type in {"text/html", "application/xhtml+xml"}:
        return "html"
    if media_type == "application/json" or media_type.endswith("+json"):
        return "json"
    return "text" if media_type.startswith("text/") else None


def extract(body: Path, mode: str, encoding: str, content_encoding: str,
            limit: int, destination: Path) -> dict:
    if body.stat().st_size > limit:
        raise ValueError(f"Response exceeds extraction byte limit ({limit}); raw body is retained")
    raw = body.read_bytes()
    if content_encoding in {"", "identity"}:
        decoded = raw
    elif content_encoding == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            decoded = stream.read(limit + 1)
        if len(decoded) > limit:
            raise ValueError(f"Decoded body exceeds extraction byte limit ({limit})")
    else:
        raise ValueError(f"Unsupported Content-Encoding for extraction: {content_encoding}")
    text = decoded.decode(encoding, errors="strict")
    if "\0" in text:
        raise ValueError("Decoded source contains NUL characters; use a suitable binary reader")
    if mode == "html":
        parser = ReadableHTML()
        parser.feed(text)
        parser.close()
        text = parser.text()
        transformation = "Static HTML text; whitespace normalized and script/style/template omitted"
    elif mode == "json":
        def invalid_constant(value):
            raise ValueError(f"Invalid JSON constant: {value}")
        # Validate, but retain exact text rather than changing keys or number representations.
        json.loads(text, parse_constant=invalid_constant)
        transformation = "Validated JSON; decoded text and formatting preserved"
    else:
        transformation = "Decoded text; characters and line endings preserved"
    with destination.open("x", encoding="utf-8", newline="") as stream:
        stream.write(text)
    return {"path": str(destination), "format": mode, "encoding": encoding,
            "characters": len(text), "transformation": transformation}


def write_metadata(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def acquire(arguments) -> tuple[dict, int]:
    url = checked_url(arguments.url)
    output_root = Path(arguments.output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="source-", dir=output_root))
    body_path = directory / "body.bin"
    metadata_path = directory / "metadata.json"
    deadline = time.monotonic() + arguments.timeout
    redirects = Redirects(deadline)
    metadata = {"requested_url": arguments.url, "request_url": url, "final_url": None,
                "started_at": timestamp(), "retrieved_at": None, "finished_at": None, "status": None,
                "headers": [], "redirects": redirects.observed,
                "max_body_bytes": arguments.max_body_bytes, "timeout_seconds": arguments.timeout,
                "response_body_complete": False, "body_path": str(body_path),
                "body_bytes": 0, "sha256": None, "extraction": None, "error": None}
    write_metadata(metadata_path, metadata)
    # Do not import proxy credentials, cookies, authorization or custom headers from the environment.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), redirects)
    request = urllib.request.Request(url, headers={"User-Agent": "skills-agent-source-fetch/1",
                                                  "Accept-Encoding": "identity"}, method="GET")
    response = None
    digest = hashlib.sha256()
    media_type = ""
    content_encoding = ""
    encoding = arguments.encoding or "utf-8-sig"
    exit_code = 1
    try:
        try:
            response = opener.open(request, timeout=remaining_time(deadline))
        except urllib.error.HTTPError as error:
            response = error
        metadata["final_url"] = response.geturl()
        metadata["status"] = response.status
        metadata["reason"] = str(response.reason)
        metadata["headers"] = list(response.headers.items())
        media_type = response.headers.get_content_type()
        content_encoding = response.headers.get("Content-Encoding", "").strip().lower()
        encoding = arguments.encoding or response.headers.get_content_charset() or "utf-8-sig"
        metadata["content_type"] = media_type
        metadata["content_encoding"] = content_encoding
        expected = response.headers.get("Content-Length")
        expected_bytes = int(expected) if expected and expected.isdecimal() else None
        with body_path.open("xb") as body:
            while True:
                try:
                    chunk = read_chunk(response, min(65536, arguments.max_body_bytes
                                                     - metadata["body_bytes"] + 1), deadline)
                except http.client.IncompleteRead as error:
                    if error.partial:
                        partial = error.partial[:arguments.max_body_bytes - metadata["body_bytes"]]
                        body.write(partial)
                        digest.update(partial)
                        metadata["body_bytes"] += len(partial)
                    raise
                if not chunk:
                    remaining_time(deadline)
                    break
                bounded = chunk[:arguments.max_body_bytes - metadata["body_bytes"]]
                body.write(bounded)
                digest.update(bounded)
                metadata["body_bytes"] += len(bounded)
                if len(bounded) != len(chunk):
                    raise ValueError(f"Response exceeds body byte limit ({arguments.max_body_bytes}); "
                                     "only the acquired prefix is preserved")
                remaining_time(deadline)
        if expected_bytes is not None and metadata["body_bytes"] != expected_bytes:
            raise ValueError(f"Incomplete response body: received {metadata['body_bytes']} "
                             f"bytes; Content-Length is {expected_bytes}")
        metadata["response_body_complete"] = True
        if not 200 <= metadata["status"] < 300:
            metadata["error"] = (f"HTTP {metadata['status']} ({metadata['reason'][:200]}): "
                                 "response body retained")
        else:
            exit_code = 0
        mode = extraction_mode(arguments.extract, media_type)
        if mode:
            try:
                metadata["extraction"] = extract(body_path, mode, encoding, content_encoding,
                                                  arguments.max_extract_bytes,
                                                  directory / "extracted.txt")
            except (ValueError, LookupError, OSError, EOFError, RecursionError) as error:
                metadata["extraction"] = {"error": str(error)[:1000], "format": mode}
                if arguments.extract != "auto":
                    exit_code = 1
        else:
            metadata["extraction"] = {"skipped": True,
                                      "reason": "Disabled or unsupported source type"}
    except (OSError, ValueError, urllib.error.URLError, http.client.HTTPException) as error:
        metadata["error"] = str(error)[:1000]
    finally:
        if response is not None:
            response.close()
        metadata["finished_at"] = timestamp()
        if metadata["status"] is not None:
            metadata["retrieved_at"] = metadata["finished_at"]
        metadata["sha256"] = digest.hexdigest() if body_path.exists() else None
        write_metadata(metadata_path, metadata)
    preview = None
    extraction = metadata["extraction"]
    if extraction and "path" in extraction:
        with Path(extraction["path"]).open(encoding="utf-8", newline="") as stream:
            text = stream.read(arguments.max_chars + 1)
        preview = {**extraction, "text": text[:arguments.max_chars],
                   "truncated": len(text) > arguments.max_chars}
    elif extraction:
        preview = extraction
    result = {"ok": exit_code == 0, "metadata_path": str(metadata_path),
              "body_path": str(body_path) if body_path.exists() else None,
              "requested_url": arguments.url[:2048],
              "final_url": (metadata["final_url"] or "")[:2048] or None,
              "status": metadata["status"], "retrieved_at": metadata["retrieved_at"],
              "finished_at": metadata["finished_at"],
              "response_body_complete": metadata["response_body_complete"],
              "body_bytes": metadata["body_bytes"], "sha256": metadata["sha256"],
              "content_type": media_type, "preview": preview,
              "error": metadata["error"] or (preview.get("error") if exit_code and preview else None)}
    return result, exit_code


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def positive_timeout(value):
    number = float(value)
    if number <= 0 or not number < float("inf"):
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Known HTTP(S) URL; credentials are not accepted")
    parser.add_argument("--output-dir", required=True, help="Parent for a fresh evidence directory")
    parser.add_argument("--extract", choices=("auto", "none", "text", "html", "json"), default="auto")
    parser.add_argument("--encoding", help="Override source character encoding when known")
    parser.add_argument("--max-chars", type=positive_int, default=8000, help="Preview character limit")
    parser.add_argument("--max-extract-bytes", type=positive_int, default=5 * 1024 * 1024,
                        help="Extraction size limit; full response is still saved")
    parser.add_argument("--max-body-bytes", type=positive_int, default=64 * 1024 * 1024,
                        help="Maximum saved response bytes; exceeding this fails as incomplete")
    parser.add_argument("--timeout", type=positive_timeout, default=30,
                        help="Total network request deadline in seconds, including redirects and body")
    arguments = parser.parse_args()
    try:
        result, code = acquire(arguments)
    except (OSError, ValueError) as error:
        result, code = {"ok": False, "error": str(error)[:1000]}, 1
    print(json.dumps(result, ensure_ascii=False))
    if code:
        print(result.get("error") or "Source extraction failed; inspect preserved metadata", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
