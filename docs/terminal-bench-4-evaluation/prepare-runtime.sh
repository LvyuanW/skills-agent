#!/usr/bin/env bash
# Run as root after prepare-base.sh on the three supplied Ubuntu 22.04 hosts.
# Installs public, pinned host dependencies only: no credentials, model calls,
# task-image builds, or evaluation trials. Requires /data and tb4.slice.
set -euo pipefail
export LC_ALL=C LANG=C
umask 022

TB4_UV_VERSION=0.13.1
TB4_PYTHON_VERSION=3.12.15
TB4_HARBOR_COMMIT=aad19e235922ee5007ac635a96a1c2f72eef9dfc
TB4_DATASET_COMMIT=452bf305c6daa62fc59061d22133a7cbc7c1572e
TB4_UV_WHEEL=uv-0.13.1-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
TB4_UV_URL=https://files.pythonhosted.org/packages/14/0b/6602a613533602e7a083e908c4375f9b0e78a94ba2e21c29b21c41a0a611/uv-0.13.1-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
TB4_UV_SHA256=696dcf6d807d85da978f4db8472da81dccc1254b18b52cbd1367c4d923c421e0
TB4_UV_ENV=/usr/local/lib/tb4-uv
TB4_ROOT=/data/tb4

[[ $(id -u) == 0 ]]
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 22.04 ]]
[[ $(uname -m) == x86_64 ]]
mountpoint -q /data
id tbench >/dev/null
[[ $(stat -fc %T /sys/fs/cgroup) == cgroup2fs ]]
systemctl is-active --quiet tb4.slice
for tb4_command in curl git python3 systemd-run sha256sum; do
  command -v "$tb4_command" >/dev/null
done

install -d -m 0755 "$TB4_ROOT/system-records" "$TB4_ROOT/system-records/downloads"
install -d -o tbench -g tbench -m 0755 \
  "$TB4_ROOT/src" "$TB4_ROOT/venvs" "$TB4_ROOT/cache/uv" \
  "$TB4_ROOT/python" "$TB4_ROOT/artifacts/runtime" "$TB4_ROOT/bin" \
  "$TB4_ROOT/plans"

# Bootstrap uv through the system's Python venv and a hash-pinned PyPI wheel.
# No pip upgrade and no remote installer script are needed.
tb4_uv_download="$TB4_ROOT/system-records/downloads/$TB4_UV_WHEEL"
if [[ ! -f $tb4_uv_download ]]; then
  curl --fail --location --silent --show-error --retry 3 \
    --proto '=https' --tlsv1.2 "$TB4_UV_URL" -o "$tb4_uv_download.part"
  printf '%s  %s\n' "$TB4_UV_SHA256" "$tb4_uv_download.part" | sha256sum --check --status
  mv "$tb4_uv_download.part" "$tb4_uv_download"
fi
printf '%s  %s\n' "$TB4_UV_SHA256" "$tb4_uv_download" | sha256sum --check --status
if [[ -e $TB4_UV_ENV && ! -f $TB4_UV_ENV/pyvenv.cfg ]]; then
  printf 'Refusing to overwrite a non-venv path: %s\n' "$TB4_UV_ENV" >&2
  exit 1
fi
if [[ ! -f $TB4_UV_ENV/pyvenv.cfg ]]; then
  python3 -m venv "$TB4_UV_ENV"
fi
"$TB4_UV_ENV/bin/python" -m pip --isolated --disable-pip-version-check install \
  --no-index --no-deps "$tb4_uv_download"
[[ $("$TB4_UV_ENV/bin/python" -c 'import importlib.metadata; print(importlib.metadata.version("uv"))') == "$TB4_UV_VERSION" ]]
if [[ -e /usr/local/bin/tb4-uv && ! -L /usr/local/bin/tb4-uv ]]; then
  printf 'Refusing to overwrite /usr/local/bin/tb4-uv\n' >&2
  exit 1
fi
ln -sfn "$TB4_UV_ENV/bin/uv" /usr/local/bin/tb4-uv
if [[ -e /usr/local/bin/uv && $(readlink -f /usr/local/bin/uv) != "$TB4_UV_ENV/bin/uv" ]]; then
  printf 'Refusing to overwrite an unrelated /usr/local/bin/uv\n' >&2
  exit 1
fi
ln -sfn "$TB4_UV_ENV/bin/uv" /usr/local/bin/uv

# systemd creates a fresh tbench service environment; caller API variables and
# SSH agents are not passed. All download/install children stay in tb4.slice.
systemd-run --quiet --collect --wait --pipe \
  --unit="tb4-runtime-prepare-$(date -u +%Y%m%dT%H%M%SZ)-$$" \
  --uid=tbench --gid=tbench --slice=tb4.slice \
  --property=SupplementaryGroups=docker \
  --working-directory="$TB4_ROOT" \
  --setenv=PATH=/usr/local/bin:/usr/bin:/bin \
  --setenv=UV_CACHE_DIR="$TB4_ROOT/cache/uv" \
  --setenv=UV_PYTHON_INSTALL_DIR="$TB4_ROOT/python" \
  --setenv=UV_PROJECT_ENVIRONMENT="$TB4_ROOT/venvs/harbor" \
  --setenv=UV_PYTHON_DOWNLOADS=automatic \
  --setenv=UV_MANAGED_PYTHON=true \
  --setenv=GIT_TERMINAL_PROMPT=0 \
  --setenv=LITELLM_LOCAL_MODEL_COST_MAP=True \
  /bin/bash -s -- "$TB4_ROOT" "$TB4_UV_ENV/bin/uv" "$TB4_UV_VERSION" \
    "$TB4_PYTHON_VERSION" "$TB4_HARBOR_COMMIT" "$TB4_DATASET_COMMIT" <<'TB4_USER'
set -euo pipefail
export LC_ALL=C LANG=C
umask 022
tb4_root=$1
tb4_uv=$2
tb4_uv_version=$3
tb4_python_version=$4
tb4_harbor_commit=$5
tb4_dataset_commit=$6
tb4_records="$tb4_root/artifacts/runtime"

checkout_pinned_public_repo() {
  local tb4_name=$1 tb4_url=$2 tb4_commit=$3
  local tb4_dest="$tb4_root/src/$tb4_name"
  if [[ -e $tb4_dest && ! -d $tb4_dest/.git ]]; then
    printf 'Refusing to overwrite non-repository: %s\n' "$tb4_dest" >&2
    return 1
  fi
  if [[ ! -d $tb4_dest/.git ]]; then
    git init --quiet "$tb4_dest"
    git -C "$tb4_dest" remote add origin "$tb4_url"
  fi
  [[ $(git -C "$tb4_dest" remote get-url origin) == "$tb4_url" ]]
  [[ -z $(git -C "$tb4_dest" status --porcelain --untracked-files=all) ]]
  if git -C "$tb4_dest" rev-parse --verify HEAD >/dev/null 2>&1; then
    [[ $(git -C "$tb4_dest" rev-parse HEAD) == "$tb4_commit" ]]
  fi
  git -C "$tb4_dest" -c credential.helper= -c core.askPass=/bin/false \
    fetch --quiet --no-tags --depth=1 origin "$tb4_commit"
  [[ $(git -C "$tb4_dest" rev-parse FETCH_HEAD) == "$tb4_commit" ]]
  git -C "$tb4_dest" checkout --quiet --detach "$tb4_commit"
}

printf 'Fetching pinned public source repositories...\n'
checkout_pinned_public_repo harbor https://github.com/harbor-framework/harbor.git "$tb4_harbor_commit"
checkout_pinned_public_repo terminal-bench https://github.com/harbor-framework/terminal-bench.git "$tb4_dataset_commit"
cd "$tb4_root/src/harbor"
printf '%s  %s\n' \
  c298b6df5a5f4a3942a278e2856215c91b11a86692ece54f6305df0f9682f9ba pyproject.toml \
  e7be66e217ac57a396775d8954d1ca69dbd4e00e33dd56cfd36d60cf9d59f7f5 uv.lock \
  | sha256sum --check --status

printf 'Installing managed CPython %s...\n' "$tb4_python_version"
"$tb4_uv" python list "$tb4_python_version" --output-format json > "$tb4_records/python-download-catalog.json"
"$tb4_uv" python install "$tb4_python_version"
tb4_python=$("$tb4_uv" python find --managed-python "$tb4_python_version")
[[ $("$tb4_python" -c 'import platform; print(platform.python_version())') == "$tb4_python_version" ]]
if [[ ! -f $UV_PROJECT_ENVIRONMENT/pyvenv.cfg ]]; then
  "$tb4_uv" venv --python "$tb4_python" "$UV_PROJECT_ENVIRONMENT"
fi
[[ $("$UV_PROJECT_ENVIRONMENT/bin/python" -c 'import platform; print(platform.python_version())') == "$tb4_python_version" ]]

# The default dev group includes cloud/training backends. Install only Harbor's
# locked runtime dependencies; build the root CLI separately with a pinned
# backend so the upstream pyproject.toml and uv.lock stay byte-for-byte intact.
printf 'Synchronizing locked Harbor runtime dependencies...\n'
"$tb4_uv" sync --frozen --no-dev --package harbor --no-install-project \
  --python "$tb4_python" > "$tb4_records/harbor-sync.log" 2>&1
cat > "$tb4_records/build-requirements.txt" <<'EOF'
uv-build @ https://files.pythonhosted.org/packages/7f/8a/d39f196478740efaae423fe9f333c030c97ade09a946eddb026f0b8145b5/uv_build-0.8.24-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl --hash=sha256:eba86a9a88d02e923f927eecf26f896548bf0a890118c3f00a915a5906b05f38
EOF
"$tb4_uv" pip install --python "$UV_PROJECT_ENVIRONMENT/bin/python" \
  --no-deps --require-hashes --requirements "$tb4_records/build-requirements.txt" \
  > "$tb4_records/harbor-build-backend.log" 2>&1
"$tb4_uv" pip install --python "$UV_PROJECT_ENVIRONMENT/bin/python" \
  --no-index --no-deps --no-build-isolation "$tb4_root/src/harbor" \
  > "$tb4_records/harbor-install.log" 2>&1

printf 'Checking installed host CLI without running any task...\n'
"$tb4_uv" pip check --python "$UV_PROJECT_ENVIRONMENT/bin/python" > "$tb4_records/pip-check.txt" 2>&1
"$tb4_uv" pip freeze --python "$UV_PROJECT_ENVIRONMENT/bin/python" > "$tb4_records/harbor-freeze.txt"
"$UV_PROJECT_ENVIRONMENT/bin/harbor" --help > "$tb4_records/harbor-help.txt"
"$UV_PROJECT_ENVIRONMENT/bin/harbor" run --help > "$tb4_records/harbor-run-help.txt"
[[ $(git status --porcelain --untracked-files=all | wc -c) == 0 ]]

"$UV_PROJECT_ENVIRONMENT/bin/python" - "$tb4_records/runtime-versions.json" \
  "$tb4_uv" "$tb4_uv_version" "$tb4_harbor_commit" "$tb4_dataset_commit" <<'PY'
import datetime
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys

destination, uv_path, uv_version, harbor_commit, dataset_commit = sys.argv[1:]
harbor = Path('/data/tb4/src/harbor')
record = {
    'prepared_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'uv_version': uv_version,
    'uv_binary_sha256': hashlib.sha256(Path(uv_path).read_bytes()).hexdigest(),
    'python_version': platform.python_version(),
    'python_executable': sys.executable,
    'platform': platform.platform(),
    'harbor_version': importlib.metadata.version('harbor'),
    'harbor_commit': harbor_commit,
    'terminal_bench_commit': dataset_commit,
    'uv_build_version': importlib.metadata.version('uv_build'),
    'harbor_pyproject_sha256': hashlib.sha256((harbor / 'pyproject.toml').read_bytes()).hexdigest(),
    'harbor_lock_sha256': hashlib.sha256((harbor / 'uv.lock').read_bytes()).hexdigest(),
    'cgroup': Path('/proc/self/cgroup').read_text().strip(),
    'installed_packages': sorted(
        ({'name': item.metadata['Name'], 'version': item.version}
         for item in importlib.metadata.distributions()),
        key=lambda item: item['name'].lower(),
    ),
}
assert record['harbor_version'] == '0.24.0'
assert record['uv_build_version'] == '0.8.24'
assert '/tb4.slice/' in record['cgroup'], record['cgroup']
Path(destination).write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({key: record[key] for key in
                  ['uv_version', 'python_version', 'harbor_version', 'cgroup']}))
PY

cat > "$tb4_root/bin/harbor" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export UV_CACHE_DIR=/data/tb4/cache/uv
export UV_PYTHON_INSTALL_DIR=/data/tb4/python
export UV_PROJECT_ENVIRONMENT=/data/tb4/venvs/harbor
export LITELLM_LOCAL_MODEL_COST_MAP=True
exec /data/tb4/venvs/harbor/bin/harbor "$@"
EOF
chmod 0755 "$tb4_root/bin/harbor"
printf 'TB4_RUNTIME_PROVISIONED\n'
TB4_USER

cp "$TB4_ROOT/artifacts/runtime/runtime-versions.json" "$TB4_ROOT/system-records/runtime-versions.json"
if [[ -f $0 ]]; then
  sha256sum "$0" > "$TB4_ROOT/system-records/prepare-runtime.sha256"
fi
printf 'Host CLI: /data/tb4/bin/harbor\nRuntime record: /data/tb4/system-records/runtime-versions.json\n'
