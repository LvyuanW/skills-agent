"""Bounded, credential-redacted diagnostics for failed provider requests."""

import base64
import json
import re
import time
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit, urlunsplit


BODY_LIMIT = 65536
REDACTED = "[REDACTED]"
_SENSITIVE = re.compile(
    r"^(?:authorization|proxyauthorization|xapikey|apikey|key|token|accesstoken|"
    r"refreshtoken|idtoken|secret|secretkey|clientsecret|password|passwd|cookie|setcookie|"
    r"credentials?|.*(?:apikey|token|secret|password|passwd|cookie|credentials?))$",
    re.I,
)
_URL = re.compile(r"\b(?:https?|wss?)://[^\s<>\"'`)}]+", re.I)
_AUTH = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", re.I)
_COOKIE = re.compile(r"\b((?:set[-_])?cookie\s*[:=]\s*)[^\r\n\"'<>]+", re.I)
_ASSIGNMENT = re.compile(
    r"(?<![\w-])([\"']?(?:authorization|proxy[-_]authorization|x[-_]api[-_]key|"
    r"api[-_]?key|access[-_]?token|refresh[-_]?token|id[-_]?token|token|secret|"
    r"client[-_]?secret|password|passwd|cookie|set[-_]?cookie|credentials?|key)"
    r"[\"']?\s*[:=]\s*)(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;<>]+)",
    re.I,
)
_KEY = re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{8,}")
_JSON_FIELD = re.compile(r'"((?:\\.|[^"\\])*)"\s*:')
_RESPONSE_HEADERS = (
    "content-type", "date", "retry-after", "request-id", "x-request-id",
    "x-amzn-requestid", "x-amz-request-id", "cf-ray",
)


def safe_endpoint(url):
    """Keep the route, but never URL credentials, query values or fragments."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port is not None:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path, "", ""))
    except (ValueError, TypeError):
        return "[REDACTED URL]"


class Redactor:
    def __init__(self, url, headers):
        secrets = set()
        for name, value in headers.items():
            normalized = re.sub(r"[^a-z]", "", name.lower())
            if not _SENSITIVE.fullmatch(normalized) or not isinstance(value, str):
                continue
            secrets.add(value)
            scheme, _, credential = value.partition(" ")
            if scheme.lower() in {"bearer", "basic"} and credential:
                secrets.add(credential)
                if scheme.lower() == "basic":
                    try:
                        decoded = base64.b64decode(credential).decode("utf-8")
                        secrets.update([decoded, *decoded.split(":", 1)])
                    except (ValueError, UnicodeError):
                        pass
            if normalized in {"cookie", "setcookie"}:
                secrets.update(part.partition("=")[2].strip() for part in value.split(";"))
        try:
            parts = urlsplit(url)
            secrets.update(unquote(value) for value in (parts.username, parts.password) if value)
            secrets.update(value for _, value in parse_qsl(parts.query) if value)
            if parts.fragment:
                secrets.add(parts.fragment)
        except (ValueError, TypeError):
            pass
        variants = set()
        for value in secrets - {""}:
            variants.update((value, json.dumps(value, ensure_ascii=True)[1:-1],
                             quote(value, safe=""), quote_plus(value, safe="")))
        self.secrets = sorted(variants, key=len, reverse=True)

    def text(self, value, *, truncated=False):
        text = str(value)
        # Remove entire URLs before replacements can insert delimiters into them.
        text = _URL.sub(lambda match: safe_endpoint(match.group()), text)
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        if truncated:
            # Bounded reads may end partway through a known credential.
            longest = 0
            for secret in self.secrets:
                for length in range(min(len(secret) - 1, len(text)), longest, -1):
                    if text.endswith(secret[:length]):
                        longest = length
                        break
            if longest:
                text = text[:-longest] + REDACTED
        text = _COOKIE.sub(lambda match: match.group(1) + REDACTED, text)
        text = _AUTH.sub(lambda match: f"{match.group(1)} {REDACTED}", text)
        text = _ASSIGNMENT.sub(lambda match: match.group(1) + '"[REDACTED]"', text)
        return _KEY.sub(REDACTED, text)

    def data(self, value, depth=0):
        if depth > 16:
            return "[OMITTED: nesting limit]"
        if isinstance(value, dict):
            return {self.text(key): REDACTED if _SENSITIVE.fullmatch(
                re.sub(r"[^a-z]", "", str(key).lower())) else self.data(item, depth + 1)
                for key, item in value.items()}
        if isinstance(value, list):
            return [self.data(item, depth + 1) for item in value]
        return self.text(value) if isinstance(value, str) else value

    def body(self, value, *, truncated=False):
        try:
            # Preserve the provider's fields and message, redacting nested credentials.
            return json.dumps(self.data(json.loads(value)), ensure_ascii=False)
        except (ValueError, RecursionError):
            # Even incomplete JSON can have escaped keys and complete nested
            # credential values. Decode field names before the text fallback.
            decoder = json.JSONDecoder()
            for match in reversed(list(_JSON_FIELD.finditer(value))):
                try:
                    name = json.loads('"' + match.group(1) + '"')
                except ValueError:
                    continue
                if not _SENSITIVE.fullmatch(re.sub(r"[^a-z]", "", name.lower())):
                    continue
                start = match.end()
                while start < len(value) and value[start].isspace():
                    start += 1
                try:
                    _, size = decoder.raw_decode(value[start:])
                    end = start + size
                except (ValueError, RecursionError):
                    end = len(value)
                value = value[:start] + '"[REDACTED]"' + value[end:]
            return self.text(value, truncated=truncated)

    def exception(self, exc, seen=None):
        seen = set() if seen is None else seen
        if id(exc) in seen or len(seen) >= 4:
            return {"type": type(exc).__name__, "message": "[OMITTED: exception chain]"}
        seen.add(id(exc))
        result = {"type": type(exc).__name__, "message": self.text(exc)[:2048]}
        for name in ("errno", "verify_code"):
            value = getattr(exc, name, None)
            if isinstance(value, int):
                result[name] = value
        verify_message = getattr(exc, "verify_message", None)
        if isinstance(verify_message, str):
            result["verify_message"] = self.text(verify_message)[:2048]
        reason = getattr(exc, "reason", None)
        if isinstance(reason, BaseException):
            result["reason"] = self.exception(reason, seen)
        elif reason is not None:
            result["reason"] = self.text(reason)[:2048]
        cause = exc.__cause__ or (exc.__context__ if not exc.__suppress_context__ else None)
        if cause is not None and id(cause) not in seen:
            result["cause"] = self.exception(cause, seen)
        return result


class ErrorEvent(dict):
    """Wire JSON stays a dictionary; diagnostics are not an extra SSE field."""

    def __init__(self, event, diagnostics):
        super().__init__(event)
        self.diagnostics = diagnostics


class DiagnosticStream:
    """Keep transport metadata available when providers detect a premature EOF."""

    def __init__(self, iterator, diagnostic):
        self.iterator = iterator
        self.diagnostic = diagnostic

    def __iter__(self):
        return self

    def __next__(self):
        return next(self.iterator)

    def close(self):
        self.iterator.close()


class RequestDiagnostics:
    def __init__(self, url, headers, request_bytes, timeout=120):
        self.redactor = Redactor(url, headers)
        self.endpoint = self.redactor.text(safe_endpoint(url))
        self.request_bytes = request_bytes
        self.timeout = timeout
        self.started = time.monotonic()
        # urlopen includes DNS, TLS, sending and waiting for response headers.
        self.phase = "open_request"
        self.response = {}
        self.response_bytes = 0
        self.stream_frames = 0

    def received_headers(self, response):
        status = getattr(response, "status", getattr(response, "code", None))
        if isinstance(status, int):
            self.response["status"] = status
        headers = getattr(response, "headers", None)
        if headers is not None:
            self.response["headers"] = {name.lower(): self.redactor.text(value)[:2048]
                                        for name, value in headers.items()
                                        if name.lower() in _RESPONSE_HEADERS}

    def snapshot(self, kind):
        result = {"kind": kind, "endpoint": self.endpoint, "phase": self.phase,
                  "elapsed_seconds": round(time.monotonic() - self.started, 6),
                  "timeout_seconds": self.timeout, "request_bytes": self.request_bytes,
                  "response_bytes": self.response_bytes, "stream_frames": self.stream_frames}
        if self.response:
            result["response"] = dict(self.response)
        return result

    def connection_error(self, exc):
        result = self.snapshot("connection")
        result["exception"] = self.redactor.exception(exc)
        return result

    def http_error(self, exc):
        self.received_headers(exc)
        self.response["status"] = exc.code
        try:
            body = exc.read(BODY_LIMIT + 1)
            self.response_bytes = len(body)
            text = body.decode("utf-8", errors="replace")
            clean = self.redactor.body(text, truncated=len(body) > BODY_LIMIT)
            self.response["body"] = clean[:BODY_LIMIT]
            self.response["body_truncated"] = len(body) > BODY_LIMIT or len(clean) > BODY_LIMIT
        except Exception as read_error:
            self.response["body_read_error"] = self.redactor.exception(read_error)
        finally:
            try:
                exc.close()
            except Exception as close_error:
                self.response["close_error"] = self.redactor.exception(close_error)
        result = self.snapshot("http")
        result["exception"] = self.redactor.exception(exc)
        return result

    def stream_error(self, text, event):
        result = self.snapshot("stream")
        clean = self.redactor.body(text)
        result.setdefault("response", {})["body"] = clean[:BODY_LIMIT]
        result["response"]["body_truncated"] = len(clean) > BODY_LIMIT
        return ErrorEvent(self.redactor.data(event), result)

    def protocol_error(self, exc, body):
        result = self.snapshot("protocol")
        clean = self.redactor.body(body)
        result.setdefault("response", {})["body"] = clean[:BODY_LIMIT]
        result["response"]["body_truncated"] = len(clean) > BODY_LIMIT
        result["exception"] = self.redactor.exception(exc)
        return result
