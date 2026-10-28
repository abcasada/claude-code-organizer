"""Start menu shortcut for the standalone Windows build."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from claude_code_organizer import APP_NAME

# Paths are passed as arguments, never pasted into the script.
_CREATE_SHORTCUT = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($args[0]); "
    "$s.TargetPath = $args[1]; $s.WorkingDirectory = $args[2]; $s.IconLocation = $args[1] + ',0'; "
    "$s.Description = 'List and resume your Claude Code sessions'; $s.Save()"
)


def can_create() -> bool:
    return sys.platform == "win32" and getattr(sys, "frozen", False)


def start_menu_shortcut() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{APP_NAME}.lnk"


def create_start_menu_shortcut() -> Path:
    exe = Path(sys.executable)
    target = start_menu_shortcut()
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _CREATE_SHORTCUT,
         str(target), str(exe), str(exe.parent)],
        check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return target
