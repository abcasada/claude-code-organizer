"""Archive, restore, delete and rename sessions on disk."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from claude_code_organizer.sessions import Session, SessionStore


class OperationError(Exception):
    pass


def session_items(root: Path, session: Session) -> list[Path]:
    """Everything stored for one session under a Claude-style root (the Claude folder or the archive).

    Deliberately not included: the project's memory/ folder and history.jsonl, which are shared
    with other sessions, and sessions/<pid>.json, which belongs to a running process.
    """
    project = root / "projects" / session.project_dir
    return [
        project / f"{session.session_id}.jsonl",
        project / session.session_id,                 # subagents, tool results, custom-title.json
        root / "file-history" / session.session_id,
        root / "session-env" / session.session_id,
    ]


def _require_closed(store: SessionStore, session: Session, action: str) -> None:
    if store.live_session(session):
        raise OperationError(f"This session is open in Claude Code. Close it before you {action} it.")


def _move_all(store: SessionStore, session: Session, from_root: Path, to_root: Path) -> None:
    pairs = [(src, to_root / src.relative_to(from_root))
             for src in session_items(from_root, session) if src.exists()]
    for _, dst in pairs:
        if dst.exists():
            raise OperationError(f"Can't move the session because this already exists:\n{dst}")

    moved: list[tuple[Path, Path]] = []
    try:
        # The transcript goes first: if Claude Code has it locked, nothing else has moved yet.
        for src, dst in pairs:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append((src, dst))
    except OSError as e:
        for src, dst in reversed(moved):
            try:
                shutil.move(str(dst), str(src))
            except OSError:
                pass
        raise OperationError(f"Couldn't move the session: {e}") from e


def archive(store: SessionStore, session: Session) -> None:
    """Moves a session out of Claude's folder: hidden from `claude --resume` and safe from
    Claude Code's automatic cleanup of old sessions."""
    if session.archived:
        return
    _require_closed(store, session, "archive")
    _move_all(store, session, store.claude_root, store.archive_root)


def restore(store: SessionStore, session: Session) -> None:
    if not session.archived:
        return
    _move_all(store, session, store.archive_root, store.claude_root)


def delete(store: SessionStore, session: Session, trash: Callable[[Path], bool], permanent: bool = False) -> None:
    """Moves a session's files to the trash (or deletes them, if permanent).

    Raises OperationError if the trash isn't available, so the caller can offer a permanent delete.
    """
    _require_closed(store, session, "delete")
    root = store.archive_root if session.archived else store.claude_root
    for item in session_items(root, session):
        if not item.exists():
            continue
        if permanent:
            try:
                shutil.rmtree(item) if item.is_dir() else item.unlink()
            except OSError as e:
                raise OperationError(f"Couldn't delete {item}: {e}") from e
        elif not trash(item):
            raise OperationError(f"Couldn't move this to the trash:\n{item}")


def rename(store: SessionStore, session: Session, title: str) -> None:
    """Renames a session the same way Claude Code's /rename does, so the name also shows there."""
    title = " ".join(title.split())
    if not title:
        raise OperationError("The name can't be empty.")
    _require_closed(store, session, "rename")

    try:
        with open(session.path, "rb") as f:
            f.seek(0, 2)
            if f.tell() > 0:
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    raise OperationError("The session is still being written to. Try again in a moment.")
    except OSError as e:
        raise OperationError(f"Couldn't read the session: {e}") from e

    entries = [
        {"type": "custom-title", "customTitle": title, "sessionId": session.session_id},
        {"type": "agent-name", "agentName": title, "sessionId": session.session_id},
    ]
    lines = "".join(json.dumps(e, ensure_ascii=False, separators=(",", ":")) + "\n" for e in entries)
    try:
        with open(session.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(lines)
        session.title_file.parent.mkdir(exist_ok=True)
        session.title_file.write_text(json.dumps({"customTitle": title}, ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        raise OperationError(f"Couldn't rename the session: {e}") from e
    session.custom_title = title
