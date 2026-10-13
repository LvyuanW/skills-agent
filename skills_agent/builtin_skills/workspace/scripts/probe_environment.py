"""Probe only the executables and Python modules requested by the task."""

import argparse
import importlib.util
import json
import re
import shutil
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", action="append", default=[])
    parser.add_argument("--module", action="append", default=[])
    args = parser.parse_args()
    if not args.executable and not args.module:
        parser.error("Request at least one --executable or --module")
    if len(args.executable) + len(args.module) > 64:
        parser.error("At most 64 targeted checks are supported per call")
    results = []
    for name in args.executable:
        path = shutil.which(name)
        results.append({"kind": "executable", "name": name,
                        "available": path is not None, "path": path})
    for name in args.module:
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            parser.error(f"Request a top-level Python module name without dots: {name!r}")
        try:
            spec = importlib.util.find_spec(name)
            results.append({"kind": "module", "name": name,
                            "available": spec is not None,
                            "origin": spec.origin if spec else None})
        except (ImportError, AttributeError, ValueError) as exc:
            results.append({"kind": "module", "name": name,
                            "available": False, "error": str(exc)})
    missing = [item["name"] for item in results if not item["available"]]
    print(json.dumps({"python": sys.executable, "checks": results,
                      "missing": missing}, ensure_ascii=False))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
