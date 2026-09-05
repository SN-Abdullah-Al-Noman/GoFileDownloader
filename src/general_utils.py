"""Module to handle common tasks."""

import os
import subprocess


def clear_terminal() -> None:
"""Clear the terminal screen when a terminal is available."""
if not os.environ.get("TERM") and os.name != "nt":
return

commands = {
    "nt": "cls",
    "posix": "clear",
}

command = commands.get(os.name)

if command:
    subprocess.run(command, shell=True, check=False)  # noqa: S603
