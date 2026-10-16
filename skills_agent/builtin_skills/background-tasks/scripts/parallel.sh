#!/usr/bin/env bash
# Foreground example: wait for every child, aggregate failure, and clean on cancel.
if (( $# < 1 || $# > 4 )); then
    echo "usage: parallel.sh OUTPUT_DIR [FIRST_EXIT_CODE [STEPS [DELAY]]]" >&2
    exit 2
fi
output_dir=$1
first_exit_code=${2:-0}
steps=${3:-5}
delay=${4:-0.1}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
children=()

cancel() {
    local status=$1 pid alive deadline
    # Keep children in the runtime's process group; do not create a new session.
    trap '' INT TERM
    for pid in "${children[@]}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done
    deadline=$((SECONDS + 3))
    while (( SECONDS < deadline )); do
        alive=0
        for pid in "${children[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then alive=1; fi
        done
        (( alive == 0 )) && break
        sleep 0.05
    done
    for pid in "${children[@]}"; do
        kill -KILL "$pid" 2>/dev/null || true
    done
    for pid in "${children[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
    echo "cancelled; waited for child processes" >&2
    exit "$status"
}
trap 'cancel 130' INT
trap 'cancel 143' TERM

python3 -u "$script_dir/no_children.py" --output "$output_dir/a.json" \
    --steps "$steps" --delay "$delay" --exit-code "$first_exit_code" &
children+=("$!")
python3 -u "$script_dir/no_children.py" --output "$output_dir/b.json" \
    --steps "$steps" --delay "$delay" &
children+=("$!")
printf 'child_pids=%s,%s\n' "${children[0]}" "${children[1]}"

# Do not use `set -e`: one failing wait must not skip waiting for other children.
rc=0
for index in "${!children[@]}"; do
    pid=${children[$index]}
    if wait "$pid"; then
        echo "child $pid finished successfully"
    else
        child_status=$?
        echo "child $pid exited $child_status" >&2
        if (( rc == 0 )); then rc=$child_status; fi
    fi
    unset 'children[index]'
done
exit "$rc"
