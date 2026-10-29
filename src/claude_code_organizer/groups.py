"""Groups you put sessions in. Claude Code has no notion of groups, so they're kept in the organizer's
own config folder, keyed by session ID: a session keeps its group when it's archived and restored."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path

from claude_code_organizer import paths


def clean_name(name: str | None) -> str:
    return " ".join((name or "").split())


class Groups:
    """Which group each session is in. A group exists while a session is in it."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.app_config_dir() / "groups.json"
        self._members: dict[str, str] = {}
        self._mtime_ns: int | None = None
        self._unreadable = False
        self.reload()

    def reload(self) -> None:
        """Re-reads the file if it changed since it was last read."""
        try:
            mtime = self.path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime == self._mtime_ns:
            return
        self._mtime_ns = mtime
        self._members = {}
        self._unreadable = False
        if mtime is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            sessions = data["sessions"]
            if not isinstance(sessions, dict):
                raise ValueError
        except (OSError, ValueError, KeyError, TypeError):
            self._unreadable = True  # kept aside as a backup on the next save, not overwritten
            return
        for session_id, name in sessions.items():
            if paths.is_session_id(session_id) and isinstance(name, str) and clean_name(name):
                self._members[session_id] = clean_name(name)

    def group_of(self, session_id: str) -> str | None:
        return self._members.get(session_id)

    def names(self, session_ids: Iterable[str] | None = None) -> list[str]:
        """The groups in use, only by `session_ids` if given, sorted by name."""
        if session_ids is None:
            used = set(self._members.values())
        else:
            used = {self._members[s] for s in session_ids if s in self._members}
        return sorted(used, key=str.casefold)

    def assign(self, session_ids: Iterable[str], name: str | None) -> None:
        """Puts the sessions in the group `name`, or takes them out of their group if it's None."""
        name = clean_name(name)
        self.reload()
        for session_id in session_ids:
            if name:
                self._members[session_id] = name
            else:
                self._members.pop(session_id, None)
        self._save()

    def rename(self, old: str, new: str) -> None:
        """Renames a group; renaming it to an existing group's name merges the two."""
        new = clean_name(new)
        if not new:
            raise ValueError("A group needs a name.")
        self.reload()
        for session_id, name in self._members.items():
            if name == old:
                self._members[session_id] = new
        self._save()

    def remove(self, name: str) -> None:
        """Removes a group. Its sessions stay; they're just no longer in a group."""
        self.reload()
        self._members = {s: n for s, n in self._members.items() if n != name}
        self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._unreadable:
            os.replace(self.path, self.path.with_name(self.path.name + ".bak"))
            self._unreadable = False
        data = {"version": 1, "sessions": dict(sorted(self._members.items()))}
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temp, self.path)
        self._mtime_ns = self.path.stat().st_mtime_ns
