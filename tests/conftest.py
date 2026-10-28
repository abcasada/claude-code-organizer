import json
import uuid
from pathlib import Path

import pytest

from claude_code_organizer import paths
from claude_code_organizer.sessions import SessionStore


def user_line(text, cwd, **extra):
    entry = {"parentUuid": None, "isSidechain": False, "type": "user",
             "message": {"role": "user", "content": text}, "uuid": str(uuid.uuid4()),
             "cwd": cwd, "sessionId": "x", "entrypoint": "cli"}
    entry.update(extra)
    return json.dumps(entry, separators=(",", ":"))


def assistant_line(text, cwd):
    return json.dumps({"parentUuid": "p", "isSidechain": False, "type": "assistant",
                       "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
                       "cwd": cwd}, separators=(",", ":"))


def title_line(kind, value, session_id="x"):
    field = {"custom-title": "customTitle", "ai-title": "aiTitle", "agent-name": "agentName"}[kind]
    return json.dumps({"type": kind, field: value, "sessionId": session_id}, separators=(",", ":"))


class Fixture:
    def __init__(self, root: Path):
        self.claude = root / "claude"
        self.archive = root / "archive"
        self.claude.mkdir()
        self.store = SessionStore(self.claude, self.archive)

    def write_session(self, cwd, lines, session_id=None, project_dir=None):
        session_id = session_id or str(uuid.uuid4())
        folder = self.claude / "projects" / (project_dir or paths.encode_project_dir(cwd))
        folder.mkdir(parents=True, exist_ok=True)
        transcript = folder / f"{session_id}.jsonl"
        transcript.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return session_id, transcript

    def session(self, session_id):
        self.store.refresh()
        return next(s for s in self.store.sessions.values() if s.session_id == session_id)


@pytest.fixture
def fx(tmp_path):
    return Fixture(tmp_path)
