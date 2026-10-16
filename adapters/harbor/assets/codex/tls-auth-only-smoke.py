#!/usr/bin/env python3
"""Check native Codex TLS with a rejected literal dummy key, never inference."""

import argparse
import json
import shutil
import subprocess
import tempfile
import time
import uuid
from types import SimpleNamespace
from pathlib import Path

import sys

# Keep standalone helper invocations usable from outside the release directory.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from adapters.harbor.tb4_agents.gateway import DEFAULT_GATEWAY, gateway_base


parser = argparse.ArgumentParser()
parser.add_argument("--image", required=True)
parser.add_argument("--ca-bundle", type=Path)
parser.add_argument("--report", type=Path, required=True)
parser.add_argument("--api-base", default=DEFAULT_GATEWAY)
args = parser.parse_args()
api_base = gateway_base(args.api_base, protocol="responses")
root = Path("/data/tb4")
with tempfile.TemporaryDirectory(prefix="tb4-codex-tls-", dir=root / "cache") as temporary:
    fixture = Path(temporary)
    fixture.chmod(0o755)
    shutil.copyfile(Path(__file__).with_name("models.json"), fixture / "models.json")
    (fixture / "config.toml").write_text('''model = "deepseek-v4.1-flash"
model_provider = "tls_probe"
model_reasoning_effort = "max"
web_search = "disabled"
model_catalog_json = "/fixture/models.json"
[model_providers.tls_probe]
name = "tls_probe"
base_url = "TB4_VALIDATED_API_BASE"
wire_api = "responses"
env_key = "DUMMY_API_KEY"
request_max_retries = 0
stream_max_retries = 0
'''.replace("TB4_VALIDATED_API_BASE", api_base))
    container_name = "tb4-codex-tls-probe-" + uuid.uuid4().hex[:8]
    command = ["docker", "run", "--rm", "--name", container_name, "--cpus", "2", "--memory", "4g", "--cgroup-parent", "tb4.slice",
               "--user", "0", "--entrypoint", "bash", "-v", f"{fixture}:/fixture:ro",
               "-v", f"{root}/artifacts/runtime:/runtime:ro", "-e", "DUMMY_API_KEY=dummy-tb4-auth-only",
               "-e", "CODEX_HOME=/tmp/tb4-tls-codex-home"]
    if args.ca_bundle:
        shutil.copyfile(args.ca_bundle, fixture / "ca-bundle.pem")
        command += ["-e", "CODEX_CA_CERTIFICATE=/fixture/ca-bundle.pem"]
    command += [args.image, "-lc", '''set -eu
if test -s /etc/ssl/certs/ca-certificates.crt || test -s /etc/pki/tls/certs/ca-bundle.crt || test -s /etc/ssl/cert.pem; then
  printf 'TB4_OS_CA_PRESENT=true\\n'
else
  printf 'TB4_OS_CA_PRESENT=false\\n'
fi
mkdir -p /tmp/tb4-tls-codex-home /tmp/tb4-codex
cp /fixture/config.toml /tmp/tb4-tls-codex-home/config.toml
tar -xzf /runtime/codex-0.149.1-linux-x64.tgz -C /tmp/tb4-codex
timeout 4s /tmp/tb4-codex/package/vendor/x86_64-unknown-linux-musl/bin/codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --json "Authentication-only probe using an invalid dummy key; no inference should occur."
''']
    started = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        result = SimpleNamespace(returncode=124, stdout=decoded(error.stdout), stderr=decoded(error.stderr))
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=20)
    reached_401 = "401" in result.stdout and "Unauthorized" in result.stdout
    report = {"kind": "native_tls_dummy_auth_only", "image": args.image, "cli_version": "0.149.1",
              "real_credentials_loaded": False, "model_inference_calls": 0,
              "request_max_retries": 0, "stream_max_retries": 0, "ca_bundle_configured": bool(args.ca_bundle),
              "os_ca_present": "TB4_OS_CA_PRESENT=true" in result.stdout,
              "exit_code": result.returncode, "tls_reached_expected_dummy_key_401": reached_401,
              "elapsed_seconds": round(time.monotonic() - started, 3),
              "stdout": result.stdout, "stderr": result.stderr}
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k:v for k,v in report.items() if k not in ["stdout", "stderr"]}))
