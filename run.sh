#!/usr/bin/env bash
set -euo pipefail

cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

venv_python=.venv/bin/python
if [[ ! -x "$venv_python" ]]; then
    python -m venv .venv
fi

if ! "$venv_python" -c 'import fastapi, uvicorn, PIL, requests; from pydantic import ConfigDict, StringConstraints' 2>/dev/null; then
    printf '%s\n' 'Installing DrishtiGuide dependencies in .venv.'
    "$venv_python" -m pip --disable-pip-version-check install --timeout 10 --retries 1 -r requirements.txt
fi

exec "$venv_python" app.py
