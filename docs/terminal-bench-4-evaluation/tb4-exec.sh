#!/usr/bin/env bash
# Root entry point for running the tbench controller inside the shared boundary.
set -euo pipefail
[[ $(id -u) == 0 ]] || { printf 'Run this launcher through root SSH.\n' >&2; exit 1; }
(( $# > 0 )) || { printf 'Usage: tb4-exec COMMAND [ARG ...]\n' >&2; exit 2; }
exec systemd-run --quiet --wait --pipe --collect \
  --slice=tb4.slice --uid=tbench --gid=tbench \
  --working-directory=/data/tb4 \
  --setenv=PATH=/data/tb4/venvs/harbor/bin:/usr/local/bin:/usr/bin:/bin \
  --setenv=UV_CACHE_DIR=/data/tb4/cache/uv \
  --setenv=UV_PYTHON_INSTALL_DIR=/data/tb4/python \
  "$@"
