#!/usr/bin/env python3
"""Run an explicit entry point with small fixtures, or compare existing artifacts."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile


def sibling(name):
    spec = importlib.util.spec_from_file_location(
        f"_feedback_experiment_{name}", Path(__file__).resolve().with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


comparison = sibling("compare")
evidence = sibling("_evidence")
feedback = sibling("feedback")
MAX_BYTES = evidence.MAX_HASH_BYTES


def read_bytes(path):
    if not stat.S_ISREG(Path(path).stat().st_mode):
        raise ValueError(f"Expected a regular file, not a pipe, device or directory: {path}")
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"Input exceeds {MAX_BYTES} bytes; select a smaller relevant artifact: {path}")
    return data


def read_input(path):
    """Retain the declared alias identity alongside the exact bytes consumed."""
    path = Path(path)
    before = path.lstat()
    metadata = {"resolved_path": str(path.resolve())}
    if stat.S_ISLNK(before.st_mode):
        metadata["symlink_target"] = os.readlink(path)
    data = read_bytes(path)
    return data, {**evidence.bytes_fingerprint(path, data), **metadata}


def relative_path(value, label):
    comparison.text(value, label)
    path = Path(value)
    if path.is_absolute() or value in (".", "") or ".." in path.parts:
        raise ValueError(f"{label} must be a relative path inside its new directory")
    return path


def resolve(value, base):
    # Preserve the declared alias so later status checks observe symlink retargeting.
    return evidence.absolute(value, base)


def validate_run(spec, base):
    comparison.fields(spec, ("schema_version", "purpose", "command", "cwd", "fixtures", "outputs",
                             "timeout", "manifest"),
                      ("schema_version", "purpose", "command", "cwd", "fixtures", "outputs"), "run spec")
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("Expected run schema_version 1")
    comparison.text(spec["purpose"], "purpose")
    comparison.text(spec["cwd"], "cwd")
    command = spec["command"]
    if not isinstance(command, list) or not command:
        raise ValueError("command must be a nonempty argument list")
    for argument in command:
        if not isinstance(argument, str) or "\0" in argument:
            raise ValueError("Every command argument must be text without NUL")
    comparison.text(command[0], "command executable")
    timeout = spec.get("timeout", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, comparison.Decimal)):
        raise ValueError("timeout must be a finite positive JSON number")
    if not math.isfinite(float(timeout)) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    for key in ("fixtures", "outputs"):
        if not isinstance(spec[key], list):
            raise ValueError(f"{key} must be a list")
    seen = set()
    for item in spec["fixtures"]:
        comparison.fields(item, ("path", "text", "json", "copy_from"), ("path",), "fixture")
        path = relative_path(item["path"], "fixture.path")
        if path in seen:
            raise ValueError(f"Duplicate fixture path: {path}")
        seen.add(path)
        if len(set(item) & {"text", "json", "copy_from"}) != 1:
            raise ValueError("A fixture needs exactly one of text, json or copy_from")
        if "text" in item and not isinstance(item["text"], str):
            raise ValueError("fixture.text must be text")
        if "copy_from" in item:
            comparison.text(item["copy_from"], "copy_from")
    seen_names, seen_paths = set(), set()
    for item in spec["outputs"]:
        comparison.fields(item, ("name", "path"), ("name", "path"), "output")
        name = comparison.text(item["name"], "output.name")
        path = relative_path(item["path"], "output.path")
        if name in seen_names or path in seen_paths:
            raise ValueError("Output names and paths must be unique")
        seen_names.add(name)
        seen_paths.add(path)
    # Validate before any filesystem materialization, while preserving the caller's manifest.
    manifest = spec.get("manifest", {})
    if isinstance(manifest, dict) and isinstance(manifest.get("limits"), dict):
        manifest = {**manifest, "limits": {key: float(value) if key == "max_seconds" and isinstance(value, comparison.Decimal) else value
                                           for key, value in manifest["limits"].items()}}
    evidence.normalize_manifest(manifest, resolve(spec["cwd"], base))
    return manifest


def helper_paths():
    directory = Path(__file__).resolve().parent
    return [directory / name for name in ("experiment.py", "compare.py", "check.py", "feedback.py")]


def run_experiment(spec, source, raw_spec, root, base=None, spec_capture=None):
    base = Path(base) if base is not None else source.parent
    manifest = validate_run(spec, base)
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="experiment-", dir=root))
    setup_path = directory / "setup.json"
    spec_capture = spec_capture or evidence.bytes_fingerprint(source, raw_spec)
    setup = {"schema_version": 1, "setup_state": "started", "started_at": evidence.utc_now(),
             "purpose": spec["purpose"], "spec": spec_capture,
             "note": "Fixture preparation intent; this does not establish command execution or completion."}
    evidence.atomic_json(setup_path, setup)
    fixture, output, logs = (directory / name for name in ("fixture", "output", "logs"))
    for path in (fixture, output, logs):
        path.mkdir()
    cwd = resolve(spec["cwd"].replace("{fixture}", str(fixture)).replace("{output}", str(output)), base)
    command = [value.replace("{fixture}", str(fixture)).replace("{output}", str(output))
               for value in spec["command"]]
    copied, consumed = [], [spec_capture]
    phase = "fixture"
    setup_error = None
    try:
        for item in spec["fixtures"]:
            target = fixture / item["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if "copy_from" in item:
                original = resolve(item["copy_from"], base)
                if not original.is_file():
                    raise ValueError(f"copy_from must be a regular file: {original}")
                data, capture = read_input(original)
                consumed.append(capture)
                copied.append(str(original))
            elif "text" in item:
                data = item["text"].encode("utf-8")
            else:
                data = (comparison._check.json_text(item["json"]) + "\n").encode("utf-8")
            if len(data) > MAX_BYTES:
                raise ValueError(f"Fixture exceeds {MAX_BYTES} bytes: {item['path']}")
            with target.open("xb") as stream:
                stream.write(data)
        # Output subdirectories exist, but output files are always fresh and absent.
        for item in spec["outputs"]:
            (output / item["path"]).parent.mkdir(parents=True, exist_ok=True)
        if not cwd.is_dir():
            raise ValueError(f"cwd is not an existing directory: {cwd}")
    except (OSError, ValueError) as exc:
        setup_error = str(exc)
    setup.update(setup_state="failed" if setup_error else "prepared", ended_at=evidence.utc_now())
    if setup_error:
        setup["error"] = setup_error
    evidence.atomic_json(setup_path, setup)
    manifest = {**manifest,
                "dependencies": [*manifest.get("dependencies", []), str(source), *copied,
                                 *[str(fixture / item["path"]) for item in spec["fixtures"]]],
                "outputs": [*manifest.get("outputs", []),
                            *[{"path": str(output / item["path"]), "label": item["name"]}
                              for item in spec["outputs"]]]}
    handle = evidence.start_record(root, "experiment-run", manifest, command,
                                   scope=spec["purpose"], cwd=cwd, helper_paths=helper_paths())
    handle["record"]["experiment"] = {
        "directory": str(directory), "fixture": str(fixture), "output": str(output),
        "setup_record": str(setup_path),
        "outputs": [{"name": item["name"], "path": str(output / item["path"])}
                    for item in spec["outputs"]],
        "scope": "Explicit fixtures and outputs; no workspace snapshot or additional sandbox."}
    evidence.atomic_json(handle["path"], handle["record"])
    if setup_error:
        execution = {"exit_code": None, "timed_out": False, "startup_error": setup_error}
        observations = {"phase": phase, "status": "inconclusive", "error": setup_error}
        status = 2
    else:
        execution = feedback.run_process(command, logs / "stdout.log", logs / "stderr.log",
                                         float(spec.get("timeout", 120)), cwd=cwd)
        phase = "startup" if execution.get("startup_error") else "execution"
        observations = {"phase": phase, "status": "observed", "verdict": "not_compared"}
        if execution.get("startup_error") or execution.get("timed_out"):
            observations["status"] = "inconclusive"
        status = 2 if execution.get("startup_error") else feedback.process_status(execution)
        invalid = []
        for item in spec["outputs"]:
            path = output / item["path"]
            try:
                if not path.resolve().is_relative_to(output) or not stat.S_ISREG(path.lstat().st_mode):
                    invalid.append({"name": item["name"], "path": str(path),
                                    "error": "Not a regular file inside the output directory"})
            except OSError as exc:
                invalid.append({"name": item["name"], "path": str(path), "error": str(exc)})
        if invalid:
            observations.update(output_acquisition_errors=invalid, status="inconclusive")
            if status == 0:
                status = 2
    record = evidence.finish_record(handle, execution, observations, consumed)
    return {**observations, "record": str(record), "execution": execution,
            "experiment": handle["record"]["experiment"]}, status


def compare_experiment(spec, source, raw_spec, root=None, base=None, spec_capture=None):
    comparison.validate_spec(spec)
    base = Path(base) if base is not None else source.parent
    subjects, dependencies = [], [str(source)]
    for check in spec["checks"]:
        for selector in (check["left"], check["right"]):
            if "path" in selector:
                subjects.append(str(resolve(selector["path"], base)))
            if "run_record" in selector:
                dependencies.append(str(resolve(selector["run_record"], base)))
    manifest = {"subjects": list(dict.fromkeys(subjects)), "dependencies": list(dict.fromkeys(dependencies))}
    handle = None
    if root is not None:
        handle = evidence.start_record(root, "experiment-compare", manifest,
                                       scope=spec["basis"], cwd=base, helper_paths=helper_paths())
        handle["record"]["experiment"] = {"comparison": comparison.plain(spec)}
        evidence.atomic_json(handle["path"], handle["record"])
    consumed = {str(source): spec_capture or evidence.bytes_fingerprint(source, raw_spec)} if handle else {}
    cache, required_hashes = {}, {}

    def observed(path):
        key = str(path)
        if key not in cache:
            data, capture = read_input(path)
            if key in required_hashes and capture["sha256"] != required_hashes[key]:
                raise ValueError(f"Artifact no longer matches its execution record: {path}")
            cache[key] = data
            consumed[key] = capture
        return cache[key]

    def validate_record(path, artifact, expected_codes):
        record = comparison.strict_json(observed(path).decode("utf-8"))
        if (not isinstance(record, dict) or type(record.get("schema_version")) is not int
                or record["schema_version"] != 1):
            raise ValueError(f"Unsupported execution record: {path}")
        execution = record.get("execution", {})
        if (record.get("record_state") != "finalized" or not isinstance(execution, dict)
                or execution.get("state") != "exited" or execution.get("startup_error")
                or execution.get("timed_out") or type(execution.get("exit_code")) is not int
                or execution["exit_code"] not in expected_codes):
            raise ValueError(f"Execution did not finish with the expected exit status: {path}")
        if (execution.get("cleanup_required") is True or execution.get("cleanup_forced") is True
                or execution.get("cleanup_confirmed") is False):
            raise ValueError(f"Producer completion is unproven: residual work needed cleanup "
                             f"or cleanup was unconfirmed: {path}")
        capture = record.get("capture")
        observations = record.get("observations")
        if not isinstance(capture, dict) or not isinstance(observations, dict):
            raise ValueError(f"Malformed execution evidence: {path}")
        errors = observations.get("output_acquisition_errors", [])
        if not isinstance(errors, list):
            raise ValueError(f"Malformed output acquisition evidence: {path}")
        for error in errors:
            if not isinstance(error, dict) or not isinstance(error.get("path"), str):
                raise ValueError(f"Malformed output acquisition evidence: {path}")
            if resolve(error["path"], path.parent).resolve() == artifact.resolve():
                raise ValueError(f"Execution reported an acquisition error for artifact: {artifact}")
        if capture.get("relation") != "same":
            raise ValueError(f"Declared input/helper versions were not established as stable during execution: {path}")
        after = capture.get("after")
        if (not isinstance(after, list) or any(not isinstance(item, dict)
                or not isinstance(item.get("path"), str) for item in after)):
            raise ValueError(f"Malformed captured fingerprints: {path}")
        candidates = [item for item in after
                      if item.get("role") == "outputs"
                      and resolve(item["path"], path.parent).resolve() == artifact.resolve()]
        if len(candidates) != 1 or candidates[0].get("status") != "ok":
            raise ValueError(f"Artifact has no unambiguous captured output fingerprint: {artifact}")
        digest = candidates[0].get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"Invalid artifact fingerprint: {artifact}")
        key = str(artifact)
        if key in required_hashes and required_hashes[key] != digest:
            raise ValueError(f"Conflicting execution fingerprints for artifact: {artifact}")
        required_hashes[key] = digest
        if key in consumed and consumed[key]["sha256"] != digest:
            raise ValueError(f"Artifact no longer matches its execution record: {artifact}")

    observations, status = comparison.evaluate(spec, base, observed, validate_record)
    if handle:
        record = evidence.finish_record(handle, {"exit_code": status, "timed_out": False}, observations,
                                        list(consumed.values()))
        observations = {**observations, "record": str(record)}
    else:
        observations = {**observations, "recording": "not_requested",
                        "consumed": list(consumed.values())}
    return observations, status


def direct_comparison(args):
    """A small CLI for one existing-file comparison; advanced checks retain --spec."""
    if len(args.paths) != 2:
        raise ValueError("Direct compare needs LEFT RIGHT, or use --spec")
    comparison.text(args.basis, "--basis")
    left, right = ({"path": path, "format": args.format or "json"} for path in args.paths)
    for side, selector in (("left", left), ("right", right)):
        for name in ("pointer", "column", "key", "array", "slice"):
            value = getattr(args, name)
            if name == "pointer":
                override = getattr(args, side + "_pointer")
                value = override if override is not None else value
            if value is not None:
                selector[name] = value
        record = getattr(args, side + "_record")
        if record is not None:
            selector["run_record"] = record
        if selector["format"] == "csv" and args.check_kind == "numeric":
            selector["value_type"] = "number"
    kind = args.check_kind or "exact"
    check = {"id": "comparison", "kind": kind, "left": left, "right": right}
    numeric = {"alignment": args.alignment, "atol": args.atol, "rtol": args.rtol, "unit": args.unit}
    if kind == "numeric":
        if args.key:
            if args.alignment is not None:
                raise ValueError("CSV --key already specifies key alignment; omit --alignment")
            check["alignment"] = {"kind": "key", "key": "/key", "value": "/value"}
        elif args.alignment is not None:
            check["alignment"] = {"kind": args.alignment}
        for name in ("atol", "rtol", "unit"):
            if numeric[name] is not None:
                check[name] = numeric[name]
    elif any(value is not None for value in numeric.values()):
        raise ValueError("Numeric options require --kind numeric")
    return comparison.validate_spec({"schema_version": 1, "basis": args.basis, "checks": [check]})


def decimal_argument(value):
    try:
        number = comparison.Decimal(value)
        if not number.is_finite() or number < 0:
            raise ValueError()
        return number
    except (ArithmeticError, ValueError):
        raise argparse.ArgumentTypeError("expected a finite nonnegative number") from None


def main():
    cli = argparse.ArgumentParser(description=__doc__, epilog=(
        "run preserves command status (124 on timeout); setup/acquisition errors return 2. "
        "compare returns 0 for supplied conditions passing, 1 for mismatch, 2 for inconclusive. "
        "Neither operation certifies overall task completion."))
    commands = cli.add_subparsers(dest="kind", required=True)
    run = commands.add_parser("run")
    run.add_argument("--spec", required=True, help="JSON spec file, or - to read stdin")
    run.add_argument("--root", required=True, help="Directory for fresh run records and experiment files")
    compare = commands.add_parser("compare", help="Compare existing files; no execution or recording unless requested")
    compare.add_argument("paths", nargs="*", metavar="PATH", help="LEFT RIGHT for a direct comparison")
    compare.add_argument("--spec", help="Existing JSON comparison spec, or - to read stdin")
    compare.add_argument("--root", help="Optionally retain the comparison and consumed versions here")
    compare.add_argument("--basis", help="Independent basis for this comparison, not an inferred oracle")
    compare.add_argument("--kind", dest="check_kind", choices=("exact", "numeric", "set", "multiset"))
    compare.add_argument("--format", choices=("json", "text", "csv", "npy", "npz"))
    compare.add_argument("--pointer", help="JSON pointer on both sides")
    compare.add_argument("--left-pointer")
    compare.add_argument("--right-pointer")
    compare.add_argument("--column", help="CSV value column on both sides")
    compare.add_argument("--key", help="CSV row identifier column; numeric values align by this key")
    compare.add_argument("--array", help="NPZ array name on both sides")
    compare.add_argument("--slice", help="NPY/NPZ selection, e.g. 0:100,: (no expressions)")
    compare.add_argument("--alignment", choices=("scalar", "position"))
    compare.add_argument("--atol", type=decimal_argument)
    compare.add_argument("--rtol", type=decimal_argument)
    compare.add_argument("--unit")
    compare.add_argument("--left-record", help="Require a successful producer record for LEFT")
    compare.add_argument("--right-record", help="Require a successful producer record for RIGHT")
    args = cli.parse_args()
    source = evidence.absolute(args.spec) if args.spec and args.spec != "-" else "<stdin>" if args.spec else "<arguments>"
    display_source = source
    try:
        base = None
        if args.kind == "compare" and args.spec:
            short_options = ("basis", "check_kind", "format", "pointer", "left_pointer", "right_pointer",
                             "column", "key", "array", "slice", "alignment", "atol", "rtol", "unit",
                             "left_record", "right_record")
            if args.paths or any(getattr(args, name) is not None for name in short_options):
                raise ValueError("Do not mix --spec with direct comparison options")
        if not args.spec or args.spec == "-":
            base = Path.cwd()
            if args.spec == "-":
                raw = sys.stdin.buffer.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise ValueError(f"stdin spec exceeds {MAX_BYTES} bytes")
                spec = comparison.strict_json(raw.decode("utf-8"))
            else:
                spec = direct_comparison(args)
                raw = comparison._check.json_text(spec).encode("utf-8")
            source = base / str(source)
            if args.root:
                root = Path(args.root).expanduser().resolve()
                root.mkdir(parents=True, exist_ok=True)
                source = Path(tempfile.mkdtemp(prefix="spec-", dir=root)) / "spec.json"
                source.write_bytes(raw)
                display_source = source
            spec_capture = evidence.bytes_fingerprint(source, raw)
        else:
            raw, spec_capture = read_input(source)
            spec = comparison.strict_json(raw.decode("utf-8"))
        operation = run_experiment if args.kind == "run" else compare_experiment
        observations, status = operation(spec, source, raw, args.root, base=base, spec_capture=spec_capture)
    except (OSError, UnicodeError, ValueError, KeyError, ArithmeticError) as exc:
        observations, status = {"status": "inconclusive", "phase": "input", "error": str(exc)}, 2
    if status:
        print(f"experiment.py: {observations.get('error') or observations.get('status')}; inspect evidence", file=sys.stderr)
    print(json.dumps({"skills_agent_feedback": 1, "kind": f"experiment-{args.kind}", "source": str(display_source),
                      "observations": observations, "attachments": []}, ensure_ascii=False, allow_nan=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
