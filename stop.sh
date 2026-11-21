#!/usr/bin/env bash
# Stop the DrishtiGuide server started with ./start.sh (or ./run.sh) in this folder.
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
ROOT=$PWD
PIDFILE=.server.pid

env_value() {
    [[ -f .env ]] || return 0
    { grep -E "^$1=" .env || true; } | tail -n1 | cut -d= -f2- \
        | sed -e 's/[[:space:]]*#.*$//' -e "s/^[\"']//" -e "s/[\"']$//"
}

PORT=${PORT:-$(env_value PORT)}
PORT=${PORT:-8000}

is_ours() {
    local pid=$1
    [[ -n $pid && -d /proc/$pid ]] || return 1
    [[ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" == "$ROOT" ]] \
        && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'app\.py'
}

pids=()
listener=$(ss -ltnp "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n1 || true)
if [[ -n $listener ]] && is_ours "$listener"; then
    pids+=("$listener")
fi
if [[ -f $PIDFILE ]]; then
    recorded=$(cat "$PIDFILE" 2>/dev/null || true)
    if [[ -n $recorded && " ${pids[*]:-} " != *" $recorded "* ]] && is_ours "$recorded"; then
        pids+=("$recorded")
    fi
fi

if (( ${#pids[@]} == 0 )); then
    rm -f "$PIDFILE"
    if [[ -n $listener ]]; then
        printf 'Port %s is used by another process (pid %s), not by this project. Leaving it alone.\n' "$PORT" "$listener"
        exit 1
    fi
    echo 'Server is not running.'
    exit 0
fi

for pid in "${pids[@]}"; do
    kill -TERM "$pid" 2>/dev/null || true
done

for (( i = 0; i < 40; i++ )); do
    alive=0
    for pid in "${pids[@]}"; do
        kill -0 "$pid" 2>/dev/null && alive=1
    done
    (( alive )) || break
    sleep 0.25
done

for pid in "${pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
        echo "Process $pid ignored SIGTERM, forcing it to stop."
        kill -KILL "$pid" 2>/dev/null || true
    fi
done

rm -f "$PIDFILE"
echo 'Server stopped.'
