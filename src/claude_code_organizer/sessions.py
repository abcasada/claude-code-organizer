"""Reads Claude Code session transcripts and the live-session registry. No UI code here."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from claude_code_organizer import paths

# Lines are identified by their first bytes; only the few we need are JSON-parsed.
_CUSTOM_TITLE = b'{"type":"custom-title"'
_AGENT_NAME = b'{"type":"agent-name"'
_AI_TITLE = b'{"type":"ai-title"'
_MESSAGE = b'{"parentUuid"'
_CWD_KEY = b'"cwd":"'
_ENTRYPOINT_RE = re.compile(rb'"entrypoint":"([^"\\]*)"')
# User lines bigger than this are pasted blobs or tool output, never worth parsing for a title.
_MAX_PROMPT_LINE = 256 * 1024

# Wrappers Claude Code puts around machine-generated "user" text.
_WRAPPER_BLOCK_RE = re.compile(
    r"<(system-reminder|local-command-stdout|local-command-stderr|local-command-caveat|"
    r"command-name|command-message|command-args|bash-stdout|bash-stderr)>.*?</\1>",
    re.DOTALL,
)
_PASTED_TAG_RE = re.compile(r"</?pasted_content[^>]*>")
_BOILERPLATE_PREFIXES = (
    "This session is being continued from a previous conversation",
    "Caveat: The messages below",
    "Base directory for this skill",
    "Below is a conversation log",
)
_MAX_TITLE_LENGTH = 200


@dataclass
class Session:
    path: Path               # the .jsonl transcript
    project_dir: str         # name of its folder under projects/
    archived: bool
    session_id: str = field(init=False)
    offset: int = 0          # bytes of complete lines already parsed
    size: int = -1
    mtime_ns: int = 0
    title_file_mtime_ns: int = 0
    cwds: list[str] = field(default_factory=list)
    cwd: str | None = None
    first_prompt: str | None = None
    ai_title: str | None = None
    agent_name: str | None = None
    custom_title: str | None = None
    entrypoint: str | None = None

    def __post_init__(self) -> None:
        self.session_id = self.path.stem

    @property
    def key(self) -> str:
        # Stable across archive/restore, so the UI can keep its selection.
        return f"{self.project_dir}/{self.session_id}"

    @property
    def title(self) -> str:
        return self.custom_title or self.agent_name or self.ai_title or self.first_prompt or "(untitled)"

    @property
    def has_content(self) -> bool:
        return bool(self.custom_title or self.agent_name or self.ai_title or self.first_prompt)

    @property
    def is_scripted(self) -> bool:
        # `claude -p` and Agent SDK runs; not something you'd resume by hand.
        return bool(self.entrypoint and self.entrypoint.startswith("sdk"))

    @property
    def last_active(self) -> float:
        return self.mtime_ns / 1e9

    @property
    def title_file(self) -> Path:
        return self.path.with_suffix("") / "custom-title.json"

    def update(self) -> bool:
        """Parses whatever was appended since the last call. Returns True if anything changed."""
        try:
            stat = self.path.stat()
        except OSError:
            return False

        changed = self._update_title_file()
        if stat.st_size == self.size and stat.st_mtime_ns == self.mtime_ns:
            return changed

        if stat.st_size < self.offset:
            self._reset()
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                for line in f:
                    # A line without its newline is still being written; pick it up next time.
                    if not line.endswith(b"\n"):
                        break
                    self.offset += len(line)
                    self._read_line(line)
        except OSError:
            return changed

        self.size = stat.st_size
        self.mtime_ns = stat.st_mtime_ns
        self._resolve_cwd()
        return True

    def _reset(self) -> None:
        fresh = Session(self.path, self.project_dir, self.archived)
        self.__dict__.update(fresh.__dict__)

    def _update_title_file(self) -> bool:
        # Newer Claude Code versions also keep the /rename title in <id>/custom-title.json.
        try:
            mtime = self.title_file.stat().st_mtime_ns
        except OSError:
            return False
        if mtime == self.title_file_mtime_ns:
            return False
        self.title_file_mtime_ns = mtime
        try:
            title = json.loads(self.title_file.read_text(encoding="utf-8")).get("customTitle")
        except (OSError, ValueError, AttributeError):
            return False
        if isinstance(title, str) and title.strip():
            self.custom_title = title.strip()
        return True

    def _read_line(self, line: bytes) -> None:
        if line.startswith(_CUSTOM_TITLE):
            self.custom_title = _string_field(line, "customTitle") or self.custom_title
        elif line.startswith(_AGENT_NAME):
            self.agent_name = _string_field(line, "agentName") or self.agent_name
        elif line.startswith(_AI_TITLE):
            self.ai_title = _string_field(line, "aiTitle") or self.ai_title
        elif line.startswith(_MESSAGE):
            self._read_message(line)

    def _read_message(self, line: bytes) -> None:
        # The working directory can change within a session, and a transcript copied into another
        # project folder keeps its old history, so collect every distinct cwd.
        cwd = _find_cwd(line)
        # On Windows, skip POSIX-style paths reported by Git Bash; they don't name a project folder.
        if cwd and cwd not in self.cwds and not (sys.platform == "win32" and cwd.startswith("/")):
            self.cwds.append(cwd)

        if self.entrypoint is None:
            match = _ENTRYPOINT_RE.search(line)
            if match:
                self.entrypoint = match.group(1).decode("utf-8", "replace")

        if self.first_prompt is not None or len(line) > _MAX_PROMPT_LINE:
            return
        if b'"type":"user"' not in line or b'"tool_result"' in line:
            return
        try:
            entry = json.loads(line)
        except ValueError:
            return
        if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
            return
        message = entry.get("message")
        if isinstance(message, dict):
            self.first_prompt = prompt_text(message.get("content"))

    def _resolve_cwd(self) -> None:
        # `claude --resume <id>` only finds a transcript stored under the project folder for the
        # current directory, so prefer the cwd that maps to the folder this transcript lives in.
        for cwd in self.cwds:
            if paths.matches_project_dir(cwd, self.project_dir):
                self.cwd = cwd
                return
        if self.cwd is None and self.cwds:
            self.cwd = self.cwds[0]


def prompt_text(content: object) -> str | None:
    """The text of a prompt the user typed, or None for machine-generated "user" entries."""
    text = None
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                return None
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                break
    if not text:
        return None

    text = _PASTED_TAG_RE.sub("", _WRAPPER_BLOCK_RE.sub("", text)).strip()
    if not text or text.startswith(_BOILERPLATE_PREFIXES) or re.match(r"^<[a-zA-Z][\w-]*[ >]", text):
        return None
    text = " ".join(text.split())
    if len(text) > _MAX_TITLE_LENGTH:
        text = text[:_MAX_TITLE_LENGTH].rstrip() + "..."
    return text


def _string_field(line: bytes, name: str) -> str | None:
    try:
        value = json.loads(line).get(name)
    except (ValueError, AttributeError):
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _find_cwd(line: bytes) -> str | None:
    # The last "cwd" key on the line is the top-level one (it follows the message body), and the
    # literal "cwd":" can't appear inside a JSON string, where quotes are escaped.
    start = line.rfind(_CWD_KEY)
    if start < 0:
        return None
    i = start + len(_CWD_KEY)
    while i < len(line):
        c = line[i]
        if c == 0x5C:  # backslash escape
            i += 2
            continue
        if c == 0x22:  # closing quote
            try:
                return json.loads(line[start + len(_CWD_KEY) - 1 : i + 1])
            except ValueError:
                return None
        i += 1
    return None


# --- Live sessions ---------------------------------------------------------------------------------

STATUS_LABELS = {"busy": "Working", "waiting": "Needs input", "idle": "Idle", "shell": "Running a command"}


@dataclass
class LiveSession:
    pid: int
    session_id: str
    cwd: str
    status: str
    kind: str
    job_id: str | None

    @property
    def label(self) -> str:
        label = STATUS_LABELS.get(self.status, "Open")
        return f"Background - {label}" if self.kind == "bg" else label


def live_key(session_id: str, cwd: str | None) -> tuple[str, str]:
    return session_id.lower(), paths.folder_key(cwd) if cwd else ""


def find_live_sessions(claude_root: Path) -> dict[tuple[str, str], LiveSession]:
    """Sessions with a running Claude Code process, from the <pid>.json files Claude keeps in sessions/."""
    live: dict[tuple[str, str], LiveSession] = {}
    try:
        entries = list((claude_root / "sessions").glob("*.json"))
    except OSError:
        return live
    for entry_path in entries:
        try:
            entry = json.loads(entry_path.read_text(encoding="utf-8"))
            pid = int(entry["pid"])
            session_id = str(entry["sessionId"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not _process_running(pid, entry.get("procStart")):
            continue
        cwd = str(entry.get("cwd") or "")
        job_id = entry.get("jobId")
        live[live_key(session_id, cwd)] = LiveSession(
            pid=pid,
            session_id=session_id,
            cwd=cwd,
            status=str(entry.get("status") or ""),
            kind=str(entry.get("kind") or "interactive"),
            job_id=str(job_id) if job_id else None,
        )
    return live


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def _process_running(pid: int, proc_start: object) -> bool:
        handle = _kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
                return False
            # Guard against PID reuse: procStart is the process creation time as a FILETIME.
            if proc_start is not None:
                times = [wintypes.FILETIME() for _ in range(4)]
                if _kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                    created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
                    try:
                        if abs(created - int(proc_start)) > 20_000_000:  # 2 seconds
                            return False
                    except (TypeError, ValueError):
                        pass
            return True
        finally:
            _kernel32.CloseHandle(handle)

else:

    def _process_running(pid: int, proc_start: object) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass
        # Guard against PID reuse where we can see the command line.
        try:
            return b"claude" in Path(f"/proc/{pid}/cmdline").read_bytes().lower()
        except OSError:
            return True


# --- Store -----------------------------------------------------------------------------------------


class SessionStore:
    """All sessions on disk, active and archived, updated incrementally by refresh()."""

    def __init__(self, claude_root: Path | None = None, archive_root: Path | None = None) -> None:
        self.claude_root = claude_root or paths.claude_dir()
        self.archive_root = archive_root or paths.archive_dir()
        self.sessions: dict[str, Session] = {}
        self.live: dict[tuple[str, str], LiveSession] = {}

    def refresh(self) -> None:
        found: dict[str, tuple[Path, str, bool]] = {}
        for root, archived in ((self.claude_root, False), (self.archive_root, True)):
            for transcript in _transcripts(root / "projects"):
                project_dir = transcript.parent.name
                key = f"{project_dir}/{transcript.stem}"
                # If a session exists both active and archived (e.g. restored by hand), show the active one.
                if key not in found or not archived:
                    found[key] = (transcript, project_dir, archived)

        for key in list(self.sessions):
            if key not in found:
                del self.sessions[key]
        for key, (transcript, project_dir, archived) in found.items():
            session = self.sessions.get(key)
            if session is None or session.path != transcript:
                session = Session(transcript, project_dir, archived)
                self.sessions[key] = session
            session.update()

        self.live = find_live_sessions(self.claude_root)

    def live_session(self, session: Session) -> LiveSession | None:
        if session.archived:
            return None
        exact = self.live.get(live_key(session.session_id, session.cwd))
        if exact:
            return exact
        # The live cwd can differ from the transcript's (e.g. after a cd). Match on the id alone
        # when that's unambiguous.
        session_id = session.session_id.lower()
        candidates = [live for (live_id, _), live in self.live.items() if live_id == session_id]
        same_id = sum(1 for s in self.sessions.values() if s.session_id.lower() == session_id and not s.archived)
        return candidates[0] if len(candidates) == 1 and same_id == 1 else None


def _transcripts(projects: Path):
    try:
        project_dirs = [p for p in projects.iterdir() if p.is_dir()]
    except OSError:
        return
    for project_dir in project_dirs:
        try:
            # Top level only: subagent transcripts live in <id>/subagents/ and aren't resumable.
            for transcript in project_dir.glob("*.jsonl"):
                if paths.is_session_id(transcript.stem) and transcript.is_file():
                    yield transcript
        except OSError:
            continue
