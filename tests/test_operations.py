import json
import os

import pytest
from conftest import user_line

from claude_code_organizer import operations
from claude_code_organizer.operations import OperationError
from claude_code_organizer.sessions import LiveSession, live_key

CWD = r"C:\work\proj" if os.name == "nt" else "/work/proj"


def make_full_session(fx):
    sid, transcript = fx.write_session(CWD, [user_line("hello", CWD)])
    project = transcript.parent
    (project / sid / "subagents").mkdir(parents=True)
    (project / sid / "subagents" / "agent-1.jsonl").write_text("{}\n")
    (fx.claude / "file-history" / sid).mkdir(parents=True)
    (fx.claude / "file-history" / sid / "abc@v1").write_text("x")
    (fx.claude / "session-env" / sid).mkdir(parents=True)
    (project / "memory").mkdir()
    (project / "memory" / "notes.md").write_text("keep me")
    return fx.session(sid)


def mark_live(fx, session):
    live = LiveSession(pid=1, session_id=session.session_id, cwd=CWD, status="busy", kind="interactive", job_id=None)
    fx.store.live = {live_key(session.session_id, CWD): live}


def test_archive_and_restore_round_trip(fx):
    session = make_full_session(fx)
    before = sorted(p.relative_to(fx.claude) for p in fx.claude.rglob("*"))

    operations.archive(fx.store, session)
    archived = fx.session(session.session_id)
    assert archived.archived
    assert not session.path.exists()
    assert (fx.archive / "file-history" / session.session_id / "abc@v1").exists()
    # Shared project data stays put.
    assert (fx.claude / "projects" / session.project_dir / "memory" / "notes.md").exists()

    operations.restore(fx.store, archived)
    assert not fx.session(session.session_id).archived
    after = sorted(p.relative_to(fx.claude) for p in fx.claude.rglob("*"))
    assert after == before


def test_archive_refuses_open_session(fx):
    session = make_full_session(fx)
    mark_live(fx, session)
    with pytest.raises(OperationError, match="open in Claude Code"):
        operations.archive(fx.store, session)
    assert session.path.exists()


def test_restore_refuses_to_overwrite(fx):
    session = make_full_session(fx)
    operations.archive(fx.store, session)
    archived = fx.session(session.session_id)
    session.path.write_text("new session with the same id\n")
    with pytest.raises(OperationError, match="already exists"):
        operations.restore(fx.store, archived)


def test_delete_trashes_session_files_only(fx):
    session = make_full_session(fx)
    trashed = []
    operations.delete(fx.store, session, trash=lambda p: (trashed.append(p.name), True)[1])
    # The transcript, plus three folders named after the session: its own, file-history and session-env.
    assert sorted(trashed) == sorted([f"{session.session_id}.jsonl"] + [session.session_id] * 3)


def test_delete_reports_unavailable_trash_and_permanent_delete_works(fx):
    session = make_full_session(fx)
    with pytest.raises(OperationError, match="trash"):
        operations.delete(fx.store, session, trash=lambda p: False)
    operations.delete(fx.store, session, trash=lambda p: False, permanent=True)
    assert not session.path.exists()
    assert not (fx.claude / "file-history" / session.session_id).exists()
    assert (fx.claude / "projects" / session.project_dir / "memory").exists()


def test_delete_refuses_open_session(fx):
    session = make_full_session(fx)
    mark_live(fx, session)
    with pytest.raises(OperationError):
        operations.delete(fx.store, session, trash=lambda p: True)


def test_rename_writes_like_claude_code(fx):
    session = make_full_session(fx)
    operations.rename(fx.store, session, "  New   name ")
    lines = session.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-2]) == {"type": "custom-title", "customTitle": "New name", "sessionId": session.session_id}
    assert json.loads(lines[-1])["type"] == "agent-name"
    assert json.loads(session.title_file.read_text(encoding="utf-8")) == {"customTitle": "New name"}
    assert fx.session(session.session_id).title == "New name"


def test_rename_refuses_while_a_line_is_half_written(fx):
    session = make_full_session(fx)
    with open(session.path, "a") as f:
        f.write('{"type":"assi')
    with pytest.raises(OperationError, match="still being written"):
        operations.rename(fx.store, session, "x")


def test_rename_requires_a_name(fx):
    session = make_full_session(fx)
    with pytest.raises(OperationError, match="empty"):
        operations.rename(fx.store, session, "   ")
