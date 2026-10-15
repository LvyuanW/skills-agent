#!/bin/sh
set -eu
mkdir -p /logs/verifier
python3 - <<'PY'
from pathlib import Path
answer = Path('/app/answer.txt')
ok = answer.exists() and answer.read_bytes() == b'TB4_PREFLIGHT_OK\n'
Path('/logs/verifier/reward.txt').write_text('1' if ok else '0')
PY
