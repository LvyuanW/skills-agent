#!/usr/bin/env python3
"""Check explicit acceptance conditions against saved files, text or JSON values."""

import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import sys


def strict_json(text):
    def reject(value):
        raise ValueError(f"Nonstandard JSON constant: {value}")
    return json.loads(text, parse_float=Decimal, parse_constant=reject)


def pointer(value, path):
    if path == "":
        return value
    if not path.startswith("/"):
        raise ValueError("JSON Pointer must be empty or begin with /")
    for token in path[1:].split("/"):
        index = 0
        while index < len(token):
            if token[index] == "~":
                if index + 1 == len(token) or token[index + 1] not in "01":
                    raise ValueError("JSON Pointer accepts only ~0 and ~1 escapes")
                index += 1
            index += 1
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            if key not in value:
                raise KeyError(f"Missing object key: {key}")
            value = value[key]
        elif isinstance(value, list):
            if not key.isascii() or not key.isdigit() or (len(key) > 1 and key.startswith("0")):
                raise KeyError(f"Invalid array index: {key}")
            position = int(key)
            if position >= len(value):
                raise KeyError(f"Array index out of range: {key}")
            value = value[position]
        else:
            raise KeyError(f"Cannot descend into a scalar at: {key}")
    return value


def equal(actual, expected):
    # Python equates True and 1, including inside containers; JSON does not.
    if type(actual) in {int, Decimal} and type(expected) in {int, Decimal}:
        return actual == expected
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(equal(actual[key], expected[key]) for key in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def json_text(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return '[' + ','.join(json_text(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(key, ensure_ascii=False) + ':' + json_text(item)
                              for key, item in value.items()) + '}'
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def excerpt(value):
    text = json_text(value)
    return {"text": text[:1200], "truncated": len(text) > 1200}


def condition(name, passed, **evidence):
    return {"condition": name, "passed": bool(passed), **evidence}


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("expected an integer >= 1")
    return number


def parser():
    cli = argparse.ArgumentParser(description=__doc__, epilog=(
        "Exit 0: supplied conditions passed; 1: a condition failed; 2: acquisition or input error. "
        "Only the named conditions are checked, not overall task completion."))
    commands = cli.add_subparsers(dest="kind", required=True)
    file = commands.add_parser("file", help="check regular file existence and optional minimum size")
    file.add_argument("paths", nargs="+")
    file.add_argument("--min-bytes", type=positive)
    text = commands.add_parser("text", help="check literal text presence or absence in actual UTF-8 content")
    text.add_argument("path")
    text.add_argument("--contains", action="append", default=[])
    text.add_argument("--absent", action="append", default=[])
    value = commands.add_parser("json", help="check an actual JSON value by RFC 6901 pointer")
    value.add_argument("path")
    value.add_argument("--pointer", default="")
    value.add_argument("--equals-json", required=True, help="expected JSON value; strings need JSON quotes")
    return cli


def main():
    args = parser().parse_args()
    source = [str(Path(value).expanduser().resolve()) for value in (
        args.paths if args.kind == "file" else [args.path])]
    checks = []
    observations = {"checks": checks, "scope": "Only the supplied acceptance conditions."}
    status = 0
    try:
        if args.kind == "file":
            for value in source:
                path = Path(value)
                regular = path.is_file()
                checks.append(condition("regular_file", regular, path=value))
                if regular and args.min_bytes is not None:
                    size = path.stat().st_size
                    checks.append(condition("minimum_bytes", size >= args.min_bytes,
                                            path=value, actual=size, expected=args.min_bytes))
        elif args.kind == "text":
            if not args.contains and not args.absent:
                raise ValueError("text requires at least one --contains or --absent condition")
            if any(value == "" for value in [*args.contains, *args.absent]):
                raise ValueError("Text conditions must be nonempty")
            with Path(source[0]).open(encoding="utf-8", newline="") as stream:
                actual = stream.read()
            if "\0" in actual:
                raise ValueError("NUL bytes found; acquire content through an appropriate binary reader")
            for expected in args.contains:
                position = actual.find(expected)
                evidence = {"expected": excerpt(expected)}
                if position >= 0:
                    line, line_start = 1, 0
                    for newline in re.finditer(r"\r\n|\r|\n", actual):
                        if newline.end() > position:
                            break
                        line, line_start = line + 1, newline.end()
                    evidence.update(line=line, column=position - line_start + 1)
                checks.append(condition("contains", position >= 0, **evidence))
            for unwanted in args.absent:
                checks.append(condition("absent", unwanted not in actual, expected=excerpt(unwanted)))
        else:
            expected = strict_json(args.equals_json)
            actual = strict_json(Path(source[0]).read_text(encoding="utf-8"))
            try:
                selected = pointer(actual, args.pointer)
            except KeyError as exc:
                checks.append(condition("json_value", False, pointer=args.pointer,
                                        expected=excerpt(expected), missing=str(exc)))
            else:
                checks.append(condition("json_value", equal(selected, expected), pointer=args.pointer,
                                        actual=excerpt(selected), expected=excerpt(expected)))
        status = 0 if all(check["passed"] for check in checks) else 1
        observations["passed"] = status == 0
    except (OSError, UnicodeError, ValueError) as exc:
        observations.update(error=str(exc), passed=False)
        status = 2
    if status:
        print("check.py: " + (observations.get("error") or "a supplied condition failed; inspect checks"), file=sys.stderr)
    print(json.dumps({"skills_agent_feedback": 1, "kind": "check", "source": source,
                      "observations": observations, "attachments": []}, ensure_ascii=False, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
