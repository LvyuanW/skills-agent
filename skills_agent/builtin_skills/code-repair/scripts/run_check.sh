#!/bin/sh
# Save a command's complete output while displaying only its tail and exit status.

if [ "$#" -lt 2 ]; then
    printf 'Usage: %s LOG [--] COMMAND [ARG ...]\n' "$0" >&2
    exit 2
fi
check_log=$1
shift
if [ "$1" = '--' ]; then shift; fi
if [ "$#" -eq 0 ]; then
    printf 'A command is required after the log path\n' >&2
    exit 2
fi
case $check_log in /*) ;; *) check_log=$PWD/$check_log ;; esac

if ( "$@" ) >"$check_log" 2>&1; then
    check_exit=0
else
    check_exit=$?
fi
printf 'Output tail:\n'
tail -c 8192 <"$check_log" | tail -n 30
printf '\nExit code: %s\nLog: %s\n' "$check_exit" "$check_log"
exit "$check_exit"
