#!/bin/sh
set -eu
mkdir -p /logs/verifier
python3 - <<'PY'
from pathlib import Path
path = Path('/app/answer.txt')
success = path.is_file() and path.read_bytes() == b'blue\n'
Path('/logs/verifier/reward.txt').write_text('1\n' if success else '0\n')
print('native image color answer:', 'passed' if success else 'failed')
PY
