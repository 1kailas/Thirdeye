"""Load local configuration while preserving existing environment variables."""

import os
import re
import shlex
from pathlib import Path

ENV_FILE = Path(__file__).with_name(".env")
ASSIGNMENT = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")


def load_environment(path=ENV_FILE):
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return
    values = {}
    for number, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        assignment = ASSIGNMENT.fullmatch(line)
        if assignment is None:
            raise ValueError(f"Invalid .env assignment on line {number}.")
        name, value = assignment.groups()
        if value.startswith(("'", '"')):
            try:
                (value,) = shlex.split(value, comments=True)
            except ValueError:
                raise ValueError(f"Invalid quoted .env value on line {number}.") from None
        else:
            value = re.split(r"(?:^|\s+)#", value, maxsplit=1)[0].rstrip()
        values[name] = value
    for name, value in values.items():
        os.environ.setdefault(name, value)
