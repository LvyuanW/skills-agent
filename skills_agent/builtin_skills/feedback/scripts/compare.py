"""Read-only comparisons for explicitly supplied experiment conditions (stdlib only)."""

from decimal import Decimal, localcontext
import importlib.util
import json
import os
from pathlib import Path


def sibling(name):
    spec = importlib.util.spec_from_file_location(
        f"_feedback_compare_{name}", Path(__file__).resolve().with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_check = sibling("check")


def strict_json(text):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def reject(value):
        raise ValueError(f"Nonstandard JSON constant: {value}")

    return json.loads(text, parse_float=Decimal, parse_constant=reject,
                      object_pairs_hook=object_pairs)


def plain(value):
    """Use lossless decimal strings for evidence, never rounded JSON floats."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    return value


def text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def fields(value, allowed, required, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if set(value) - set(allowed) or not set(required) <= set(value):
        raise ValueError(f"Invalid {label} fields; required: {', '.join(required)}; "
                         f"allowed: {', '.join(allowed)}")


def number(value, label):
    if type(value) not in (int, Decimal) or not Decimal(value).is_finite():
        raise ValueError(f"{label} must be a finite JSON number (not a boolean or string)")
    return Decimal(value)


def validate_selector(value, label):
    fields(value, ("path", "value", "format", "pointer", "run_record", "expected_exit_codes"), (), label)
    if ("path" in value) == ("value" in value):
        raise ValueError(f"{label} requires exactly one of path or value")
    if "value" in value:
        if set(value) != {"value"}:
            raise ValueError(f"{label}: literal value cannot have file selection fields")
        return
    text(value["path"], f"{label}.path")
    if "run_record" in value:
        text(value["run_record"], f"{label}.run_record")
    if "expected_exit_codes" in value:
        codes = value["expected_exit_codes"]
        if ("run_record" not in value or not isinstance(codes, list) or not codes
                or any(type(code) is not int or code < 0 or code > 255 for code in codes)):
            raise ValueError("expected_exit_codes needs run_record and a nonempty list of exit codes")
    if value.get("format", "json") not in ("json", "text"):
        raise ValueError(f"{label}.format must be json or text")
    pointer = value.get("pointer", "")
    if not isinstance(pointer, str):
        raise ValueError(f"{label}.pointer must be text")
    if value.get("format") == "text" and pointer:
        raise ValueError(f"{label}: text cannot use a JSON pointer")
    # Validate syntax even if the eventual input is missing or a scalar.
    if pointer:
        if not pointer.startswith("/"):
            raise ValueError("JSON Pointer must be empty or begin with /")
        for token in pointer[1:].split("/"):
            index = 0
            while index < len(token):
                if token[index] == "~":
                    if index + 1 == len(token) or token[index + 1] not in "01":
                        raise ValueError("JSON Pointer accepts only ~0 and ~1 escapes")
                    index += 1
                index += 1


def validate_spec(spec):
    fields(spec, ("schema_version", "basis", "checks", "premises"),
           ("schema_version", "basis", "checks"), "comparison")
    if type(spec["schema_version"]) is not int or spec["schema_version"] != 1:
        raise ValueError("Expected comparison schema_version 1")
    text(spec["basis"], "basis")
    if "premises" in spec:
        fields(spec["premises"], ("inputs", "randomness", "units", "side_effects", "fixed", "varying"), (), "premises")
        for key, value in spec["premises"].items():
            text(value, f"premises.{key}")
    checks = spec["checks"]
    if not isinstance(checks, list) or not checks:
        raise ValueError("checks must be a nonempty list")
    ids = set()
    for check in checks:
        fields(check, ("id", "kind", "left", "right", "alignment", "atol", "rtol", "unit",
                       "relation", "delta", "factor"), ("id", "kind", "left", "right"), "check")
        ident = text(check["id"], "check.id")
        if ident in ids:
            raise ValueError(f"Duplicate check id: {ident}")
        ids.add(ident)
        kind = check["kind"]
        if kind not in ("exact", "numeric", "set", "multiset", "paired"):
            raise ValueError(f"Unknown comparison kind: {kind}")
        validate_selector(check["left"], "left")
        validate_selector(check["right"], "right")
        extra = set(check) - {"id", "kind", "left", "right"}
        if kind in ("exact", "set", "multiset"):
            if kind != "exact" and check.get("relation", "equal") not in ("equal", "subset", "superset"):
                raise ValueError("Set relation must be equal, subset or superset")
            if extra - ({"relation"} if kind != "exact" else set()):
                raise ValueError(f"{kind} does not accept numeric or alignment fields")
            continue
        if not {"alignment", "atol", "rtol", "unit"} <= set(check):
            raise ValueError("Numeric comparisons require explicit alignment, atol, rtol and unit")
        text(check["unit"], "unit")
        for key in ("atol", "rtol"):
            if number(check[key], key) < 0:
                raise ValueError(f"{key} must be nonnegative")
        alignment = check["alignment"]
        fields(alignment, ("kind", "key", "value"), ("kind",), "alignment")
        if alignment["kind"] not in ("scalar", "position", "key"):
            raise ValueError("alignment.kind must be scalar, position or key")
        if alignment["kind"] == "key":
            if set(alignment) != {"kind", "key", "value"}:
                raise ValueError("Key alignment requires key and value JSON pointers")
            for key in ("key", "value"):
                validate_selector({"path": "unused", "pointer": alignment[key]}, f"alignment.{key}")
        elif set(alignment) != {"kind"}:
            raise ValueError("Only key alignment accepts key and value pointers")
        if kind == "numeric":
            if extra - {"alignment", "atol", "rtol", "unit"}:
                raise ValueError("numeric does not accept paired relation fields")
        else:
            relation = check.get("relation")
            if relation not in ("equal", "nondecreasing", "nonincreasing", "delta", "scale"):
                raise ValueError("paired requires equal, nondecreasing, nonincreasing, delta or scale relation")
            parameter = {"delta": "delta", "scale": "factor"}.get(relation)
            present = set(check) & {"delta", "factor"}
            if present != ({parameter} if parameter else set()):
                raise ValueError("Supply only the parameter required by the paired relation")
            if parameter:
                number(check[parameter], parameter)
    return spec


def selected(selector, base, read_bytes, validate_record=None):
    if "value" in selector:
        return selector["value"]
    path = Path(os.path.abspath(base / Path(selector["path"]).expanduser()))
    if "run_record" in selector:
        record = Path(os.path.abspath(base / Path(selector["run_record"]).expanduser()))
        if validate_record is None:
            raise ValueError("run_record needs an execution-record validator")
        validate_record(record, path, selector.get("expected_exit_codes", [0]))
    data = read_bytes(path)
    contents = data.decode("utf-8")
    if "\0" in contents:
        raise ValueError(f"NUL bytes in text input: {path}")
    if selector.get("format", "json") == "text":
        return contents
    return _check.pointer(strict_json(contents), selector.get("pointer", ""))


def canonical(value):
    # Tuple tags preserve JSON type distinctions, including true versus 1.
    if type(value) in (int, Decimal):
        return ("number", Decimal(value))
    if isinstance(value, dict):
        return ("object", tuple(sorted((key, canonical(item)) for key, item in value.items())))
    if isinstance(value, list):
        return ("array", tuple(canonical(item) for item in value))
    return (type(value).__name__, value)


def pairs(left, right, alignment):
    kind = alignment["kind"]
    if kind == "scalar":
        return [("value", left, right)]
    if not isinstance(left, list) or not isinstance(right, list) or not left or not right:
        raise ValueError("Array alignment requires nonempty arrays on both sides")
    if kind == "position":
        if len(left) != len(right):
            raise ValueError(f"Cannot align arrays of lengths {len(left)} and {len(right)}")
        return [(index, a, b) for index, (a, b) in enumerate(zip(left, right))]
    def keyed(values):
        result = {}
        for item in values:
            key = _check.pointer(item, alignment["key"])
            if isinstance(key, (list, dict)) or key is None:
                raise ValueError("Alignment keys must be non-null JSON scalars")
            token = canonical(key)
            if token in result:
                raise ValueError(f"Duplicate alignment key: {_check.json_text(key)}")
            result[token] = (key, _check.pointer(item, alignment["value"]))
        return result
    a, b = keyed(left), keyed(right)
    if a.keys() != b.keys():
        raise ValueError("Alignment keys differ; missing or extra rows cannot be silently dropped")
    return [(a[key][0], a[key][1], b[key][1]) for key in a]


def numeric_check(left, right, check):
    aligned = pairs(left, right, check["alignment"])
    prepared = [(key, number(a, "left value"), number(b, "right value")) for key, a, b in aligned]
    atol, rtol = number(check["atol"], "atol"), number(check["rtol"], "rtol")
    numbers = [atol, rtol, *[item for _, a, b in prepared for item in (a, b)]]
    if "delta" in check:
        numbers.append(number(check["delta"], "delta"))
    if "factor" in check:
        numbers.append(number(check["factor"], "factor"))
    # Enough precision for differences, products and disparate decimal exponents.
    required = max(len(n.as_tuple().digits) for n in numbers) * 3 + (
        max(n.adjusted() for n in numbers) - min(n.as_tuple().exponent for n in numbers)) + 16
    if required > 100000:
        raise ValueError("Numeric precision exceeds the bounded comparison budget")
    mismatches = []
    count = 0
    with localcontext() as context:
        context.prec = max(28, required)
        for key, a, b in prepared:
            relation = check.get("relation")
            actual, expected = a, b
            if relation == "delta":
                actual, expected = b - a, number(check["delta"], "delta")
            elif relation == "scale":
                actual, expected = b, a * number(check["factor"], "factor")
            elif relation is not None:
                actual, expected = b, a
            tolerance = atol + rtol * abs(expected)
            if relation == "nondecreasing":
                passed = actual >= expected - tolerance
            elif relation == "nonincreasing":
                passed = actual <= expected + tolerance
            else:
                passed = abs(actual - expected) <= tolerance
            if not passed:
                count += 1
                if len(mismatches) < 20:
                    mismatches.append(plain({"key": key, "left": a, "right": b,
                                             "actual": actual, "expected": expected,
                                             "tolerance": tolerance}))
    return {"passed": count == 0, "compared": len(prepared), "mismatch_count": count,
            "mismatches": mismatches, "mismatches_truncated": count > len(mismatches)}


def compare_values(left, right, check):
    kind = check["kind"]
    if kind in ("numeric", "paired"):
        return numeric_check(left, right, check)
    if kind == "exact":
        return {"passed": _check.equal(left, right), "left": _check.excerpt(left),
                "right": _check.excerpt(right)}
    if not isinstance(left, list) or not isinstance(right, list):
        raise ValueError("Set and multiset comparisons require JSON arrays")
    from collections import Counter
    a, b = Counter(map(canonical, left)), Counter(map(canonical, right))
    if kind == "set":
        a, b = Counter({key: 1 for key in a}), Counter({key: 1 for key in b})
    relation = check.get("relation", "equal")
    missing, extra = b - a, a - b
    representatives = {canonical(value): value for value in [*left, *right]}

    def examples(difference):
        return [{"value": _check.excerpt(representatives[key]), "count": count}
                for key, count in list(difference.items())[:20]]

    passed = a == b if relation == "equal" else a <= b if relation == "subset" else a >= b
    return {"passed": passed, "relation": relation, "left_count": len(left), "right_count": len(right),
            "left_distinct": len(a), "right_distinct": len(b),
            "missing_count": sum(missing.values()), "extra_count": sum(extra.values()),
            "missing": examples(missing), "extra": examples(extra),
            "differences_truncated": len(missing) > 20 or len(extra) > 20}


def evaluate(spec, base, read_bytes=None, validate_record=None):
    """Return bounded evidence; never execute a program or synthesize an oracle."""
    validate_spec(spec)
    base = Path(base).resolve()
    read_bytes = read_bytes or Path.read_bytes
    checks = []
    for check in spec["checks"]:
        observation = {"id": check["id"], "kind": check["kind"]}
        try:
            left = selected(check["left"], base, read_bytes, validate_record)
            right = selected(check["right"], base, read_bytes, validate_record)
            observation.update(compare_values(left, right, check))
            observation["status"] = "passed" if observation["passed"] else "mismatch"
        except (OSError, UnicodeError, ValueError, KeyError, ArithmeticError) as exc:
            observation.update(passed=False, status="inconclusive", error=str(exc))
        checks.append(observation)
    inconclusive = any(check["status"] == "inconclusive" for check in checks)
    passed = all(check["passed"] for check in checks)
    return {"basis": spec["basis"], "declared_premises": spec.get("premises", {}),
            "scope": "Only the supplied conditions; declared premises are not independently certified.",
            "checks": checks, "passed": passed,
            "status": "inconclusive" if inconclusive else "passed" if passed else "mismatch"}, (
                2 if inconclusive else 0 if passed else 1)
