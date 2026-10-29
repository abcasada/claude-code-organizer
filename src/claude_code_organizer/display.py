"""How sessions are described to people, shared by the window and the command line."""

from __future__ import annotations

import time
from pathlib import Path

from claude_code_organizer.sessions import LiveSession, Session


def relative_time(timestamp: float, now: float) -> str:
    age = now - timestamp
    if age < 60:
        return "just now"
    if age < 3600:
        return f"{int(age // 60)} min ago"
    if age < 86400:
        return f"{int(age // 3600)} h ago"
    when = time.localtime(timestamp)
    if age < 7 * 86400:
        return time.strftime("%a %H:%M", when)
    return time.strftime("%Y-%m-%d", when)


def status_text(session: Session, live: LiveSession | None, folder_ok: bool) -> str:
    if session.archived:
        return "Archived"
    if live:
        return live.label
    if not folder_ok:
        return "Folder missing"
    return ""


def short_path(path: str | None) -> str:
    """The path with the home folder shown as ~."""
    if not path:
        return ""
    home = str(Path.home())
    if path == home:
        return "~"
    for sep in ("/", "\\"):
        if path.startswith(home + sep):
            return "~" + path[len(home):]
    return path
