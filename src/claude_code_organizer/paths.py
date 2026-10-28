"""Where Claude Code keeps its data, where the organizer keeps its own, and path helpers."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

SESSION_ID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

# Claude Code truncates very long project folder names and appends a hash.
_MAX_ENCODED_LENGTH = 200


def claude_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def app_config_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "ClaudeCodeOrganizer"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "claude-code-organizer"


def app_data_dir() -> Path:
    if sys.platform == "win32":
        return app_config_dir()
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "claude-code-organizer"


def archive_dir() -> Path:
    # Mirrors the layout of the Claude folder (projects/, file-history/, session-env/), so archiving
    # and restoring are plain moves between the two roots.
    return app_data_dir() / "archive"


def is_session_id(value: str) -> bool:
    return bool(SESSION_ID_RE.match(value))


def encode_project_dir(path: str) -> str:
    """Name of the folder under projects/ that Claude Code uses for a working directory."""
    return re.sub(r"[^a-zA-Z0-9]", "-", path)


def matches_project_dir(cwd: str, project_dir: str) -> bool:
    encoded = encode_project_dir(cwd)
    if encoded == project_dir:
        return True
    return len(encoded) > _MAX_ENCODED_LENGTH and project_dir.startswith(encoded[:_MAX_ENCODED_LENGTH])


def folder_key(path: str) -> str:
    """Normalizes a folder path so different spellings of the same folder group together."""
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    normalized = os.path.normpath(path)
    if sys.platform == "win32":
        normalized = os.path.normcase(normalized)
    return normalized.rstrip("\\/") or normalized
