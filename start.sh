#!/usr/bin/env bash
# Start the DrishtiGuide server in the background and wait until it answers.
# Usage: ./start.sh            start in the background (logs in logs/server.log)
#        ./start.sh --foreground   run in this terminal instead
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
ROOT=$PWD
LOG=logs/server.log
PIDFILE=.server.pid

env_value() {
    [[ -f .env ]] || return 0
    { grep -E "^$1=" .env || true; } | tail -n1 | cut -d= -f2- \
        | sed -e 's/[[:space:]]*#.*$//' -e "s/^[\"']//" -e "s/[\"']$//"
}

PORT=${PORT:-$(env_value PORT)}
PORT=${PORT:-8000}
HOST_VALUE=${HOST:-$(env_value HOST)}
HOST_VALUE=${HOST_VALUE:-127.0.0.1}

listener_pid() {
    ss -ltnp "sport = :$PORT" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n1
}

is_ours() {
    local pid=$1
    [[ -n $pid && -d /proc/$pid ]] || return 1
    [[ "$(readlink -f "/proc/$pid/cwd" 2>/dev/null)" == "$ROOT" ]] \
        && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q 'app\.py'
}

show_urls() {
    printf '\n  Server   http://127.0.0.1:%s\n  Pair     http://127.0.0.1:%s/pair\n  Debug    http://127.0.0.1:%s/debug\n  Logs     tail -f %s\n  Stop     ./stop.sh\n\n' \
        "$PORT" "$PORT" "$PORT" "$LOG"
}

if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        cp .env.example .env
        chmod 600 .env
        printf '%s\n' 'Created .env from .env.example. Set SARVAM_API_KEY (and PHONE_TOKEN for the phone), then run ./start.sh again.'
    else
        printf '%s\n' 'No .env file found.'
    fi
    exit 1
fi

if [[ -z "$(env_value SARVAM_API_KEY)" && -z "${SARVAM_API_KEY:-}" ]]; then
    printf '%s\n' 'SARVAM_API_KEY is empty in .env. Add your key, then run ./start.sh again.'
    exit 1
fi

if [[ $HOST_VALUE != 127.0.0.1 && $HOST_VALUE != localhost ]]; then
    token=${PHONE_TOKEN:-$(env_value PHONE_TOKEN)}
    if (( ${#token} < 24 )); then
        printf '%s\n' "HOST is $HOST_VALUE, which needs PHONE_TOKEN of at least 24 characters in .env."
        printf '%s\n' 'Generate one with: python -c "import secrets;print(secrets.token_urlsafe(32))"'
        exit 1
    fi
fi

existing=$(listener_pid || true)
if [[ -n $existing ]]; then
    if is_ours "$existing"; then
        echo "$existing" > "$PIDFILE"
        printf 'Already running (pid %s).\n' "$existing"
        show_urls
        exit 0
    fi
    printf 'Port %s is already used by another process (pid %s). Stop it or set PORT in .env.\n' "$PORT" "$existing"
    exit 1
fi

if [[ ${1:-} == --foreground || ${1:-} == -f ]]; then
    exec ./run.sh
fi

mkdir -p logs
if [[ -f $LOG && $(stat -c %s "$LOG") -gt 5242880 ]]; then
    mv -f "$LOG" "$LOG.old"
fi
printf '\n=== start %s ===\n' "$(date '+%F %T')" >> "$LOG"

first_run=0
[[ -x .venv/bin/python ]] || first_run=1
setsid nohup ./run.sh >> "$LOG" 2>&1 < /dev/null &
launcher=$!

limit=40
(( first_run )) && limit=240
printf 'Starting server'
for (( i = 0; i < limit; i++ )); do
    if curl -fsS -m 2 -o /dev/null "http://127.0.0.1:$PORT/api/status" 2>/dev/null; then
        pid=$(listener_pid || true)
        [[ -n $pid ]] && echo "$pid" > "$PIDFILE"
        printf '\nServer is up (pid %s).' "${pid:-?}"
        show_urls
        exit 0
    fi
    if ! kill -0 "$launcher" 2>/dev/null && [[ -z $(listener_pid || true) ]]; then
        printf '\nThe server exited during start-up. Last log lines:\n\n'
        tail -n 25 "$LOG"
        exit 1
    fi
    printf '.'
    sleep 0.5
done

printf '\nThe server did not answer within %s seconds. Last log lines:\n\n' "$(( limit / 2 ))"
tail -n 25 "$LOG"
exit 1
