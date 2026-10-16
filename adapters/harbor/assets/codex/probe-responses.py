#!/usr/bin/env python3
"""One exact-model Responses request; no SDK retry or credential output."""

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import sys

# Keep standalone helper invocations usable from outside the release directory.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from adapters.harbor.tb4_agents.gateway import DEFAULT_GATEWAY, gateway_base


MODEL = "deepseek-v4.1-flash"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        # Never forward this gateway's credential to a redirect destination.
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=DEFAULT_GATEWAY,
                        help="Explicit authorized gateway; defaults to the current gateway.")
    parser.add_argument("--report", type=Path,
                        default=Path("/data/tb4/logs/codex-current-responses-probe.json"))
    args = parser.parse_args(argv)
    # Validate before reading any credential. Legacy gateways require an explicit option.
    endpoint = gateway_base(args.api_base, protocol="responses") + "/responses"
    payload = {"model": MODEL, "input": "Reply with OK only.", "reasoning": {"effort": "max"},
               "max_output_tokens": 1024, "stream": False}
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), method="POST", headers={
        "Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"], "Content-Type": "application/json"})
    started = time.monotonic()
    report = {"kind": "single_live_responses_probe", "endpoint": endpoint, "requested_model": MODEL,
              "requested_effort": "max", "retry_count": 0}
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=120) as response:
            obj = json.load(response)
            report.update(http_status=response.status, response_model=obj.get("model"), response_status=obj.get("status"),
                          usage=obj.get("usage"), output_types=[x.get("type") for x in obj.get("output", [])])
    except urllib.error.HTTPError as error:
        report["http_status"] = error.code
        try:
            obj = json.load(error)
            info = obj.get("error") or {}
            report["provider_error_type"] = info.get("type")
            report["provider_error_code"] = info.get("code")
        except (ValueError, AttributeError):
            pass
    except (OSError, TimeoutError, urllib.error.URLError) as error:
        report["transport_error_type"] = type(error).__name__
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    if report.get("http_status") != 200 or report.get("response_model") != MODEL:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
