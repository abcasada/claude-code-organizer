"""The command line: list, resume, rename, group, archive and delete sessions without the window.

Doesn't use Qt, so it works on a server with no desktop. `ccorg` is always the command line;
`claude-code-organizer` opens the window unless it's given a command or there's no display.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO
from urllib.parse import quote

from claude_code_organizer import APP_NAME, __version__, launcher, operations, paths
from claude_code_organizer.display import relative_time, short_path, status_text
from claude_code_organizer.groups import Groups, clean_name
from claude_code_organizer.launcher import LaunchError
from claude_code_organizer.operations import OperationError
from claude_code_organizer.sessions import Session, SessionStore

PROG = "ccorg"
PICKER_LIMIT = 20

# Replaced in tests: os.execve doesn't return.
_exec = os.execve


class CliError(Exception):
    pass


class Cli:
    def __init__(self, store: SessionStore | None = None, groups: Groups | None = None,
                 out: TextIO | None = None, err: TextIO | None = None, inp: TextIO | None = None) -> None:
        self.store = store or SessionStore()
        self.groups = groups or Groups()
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.inp = inp or sys.stdin
        self.store.refresh()

    # --- helpers -----------------------------------------------------------------------------------

    def print(self, text: str = "") -> None:
        print(text, file=self.out)

    def note(self, text: str) -> None:
        print(text, file=self.err)

    def interactive(self) -> bool:
        return self.inp.isatty()

    def confirm(self, question: str, flag: str) -> bool:
        if not self.interactive():
            raise CliError(f"{question} Not asking without a terminal; add {flag} to go ahead.")
        print(f"{question} [y/N] ", end="", file=self.err, flush=True)
        return self.inp.readline().strip().lower() in ("y", "yes")

    def sessions(self, show_all: bool = False, archived: bool = False, group: str | None = None,
                 words: list[str] | None = None) -> list[Session]:
        """Sessions as the window lists them, most recently active first."""
        words = [w.lower() for w in words or []]
        result = []
        for session in self.store.sessions.values():
            if not show_all and (session.archived and not archived or not session.has_content
                                 or session.is_scripted or session.is_desktop_app):
                continue
            name = self.groups.group_of(session.session_id)
            if group is not None and name != group:
                continue
            text = f"{session.title} {session.cwd or ''} {session.session_id} {name or ''}".lower()
            if all(w in text for w in words):
                result.append(session)
        result.sort(key=lambda s: s.last_active, reverse=True)
        return result

    def find_group(self, name: str) -> str:
        """The existing group with this name, ignoring case."""
        name = clean_name(name)
        for existing in self.groups.names():
            if existing.casefold() == name.casefold():
                return existing
        raise CliError(f"There's no group named “{name}”. `{PROG} groups` lists them.")

    def resolve(self, ref: str) -> Session:
        """A session from a number in the last list, the start of its ID, or words from its title."""
        if ref.isdigit():
            keys = _load_last_list()
            if not keys:
                raise CliError(f"Run `{PROG} list` first, then use a number from it.")
            number = int(ref)
            if not 1 <= number <= len(keys):
                raise CliError(f"There's no #{number} in the last list (1-{len(keys)}).")
            session = self.store.sessions.get(keys[number - 1])
            if session is None:
                raise CliError(f"Session #{number} no longer exists. Run `{PROG} list` again.")
            return session

        lowered = ref.lower().strip()
        if len(lowered) >= 4 and re.fullmatch(r"[0-9a-f-]+", lowered):
            by_id = [s for s in self.store.sessions.values() if s.session_id.lower().startswith(lowered)]
            if by_id:
                return self._one(by_id, ref)
        by_title = [s for s in self.sessions(show_all=True) if lowered in s.title.lower()]
        exact = [s for s in by_title if s.title.lower() == lowered]
        return self._one(exact or by_title, ref)

    def _one(self, matches: list[Session], ref: str) -> Session:
        if not matches:
            raise CliError(f"No session matches “{ref}”. `{PROG} list` shows them.")
        if len(matches) > 1:
            lines = "\n".join(f"  {s.session_id[:8]}  {s.title}  ({short_path(s.cwd)})" for s in matches[:10])
            more = f"\n  ... and {len(matches) - 10} more" if len(matches) > 10 else ""
            raise CliError(f"“{ref}” matches {len(matches)} sessions; use a number or more of the ID:\n{lines}{more}")
        return matches[0]

    def resolve_all(self, refs: list[str]) -> list[Session]:
        sessions: dict[str, Session] = {}
        for ref in refs:
            session = self.resolve(ref)
            sessions[session.key] = session
        return list(sessions.values())

    def status(self, session: Session) -> str:
        folder_ok = bool(session.cwd) and os.path.isdir(session.cwd)
        return status_text(session, self.store.live_session(session), folder_ok)

    def table(self, sessions: list[Session]) -> None:
        now = time.time()
        color = _use_color(self.out)
        rows = [(str(i), s.session_id[:8], relative_time(s.last_active, now), self.status(s),
                 self.groups.group_of(s.session_id) or "", s.title, short_path(s.cwd), s)
                for i, s in enumerate(sessions, 1)]
        headers = ("#", "ID", "LAST ACTIVE", "STATUS", "GROUP", "TITLE", "FOLDER")
        show_group = any(r[4] for r in rows)
        fixed = [0, 1, 2, 3] + ([4] if show_group else [])
        widths = {c: max(len(headers[c]), *(len(r[c]) for r in rows)) for c in fixed}
        widths[4] = min(widths.get(4, 0), 20)

        total = shutil.get_terminal_size((120, 24)).columns
        free = total - sum(widths[c] + 2 for c in fixed)
        title_width = max(min(max(len(r[5]) for r in rows), max(free * 3 // 5, 24)), len(headers[5]))
        folder_width = free - title_width - 2
        show_folder = folder_width >= 16
        if not show_folder:
            title_width = max(free, 24)

        def line(cells: tuple, style: dict[int, str] | None = None) -> str:
            parts = []
            for c in fixed + [5] + ([6] if show_folder else []):
                width = {5: title_width, 6: folder_width}.get(c, widths.get(c, 0))
                text = _fit(cells[c], width)
                text = text.rjust(width) if c == 0 else text.ljust(width) if c != 6 else text
                if color and style and c in style:
                    text = f"{style[c]}{text}\033[0m"
                parts.append(text)
            return "  ".join(parts).rstrip()

        self.print(line(headers, {c: "\033[1m" for c in range(7)}))
        for row in rows:
            session = row[7]
            style = {1: "\033[2m", 6: "\033[2m"}
            if session.archived:
                style[5] = "\033[2m"
            status = row[3]
            if status:
                style[3] = ("\033[31m" if status == "Folder missing" else "\033[2m" if status == "Archived"
                            else "\033[33m" if "Needs input" in status else "\033[32m")
            self.print(line(row, style))

    # --- commands ----------------------------------------------------------------------------------

    def cmd_list(self, args) -> int:
        group = self.find_group(args.group) if args.group else None
        sessions = self.sessions(args.all, args.archived, group, args.search)
        if args.limit:
            sessions = sessions[:args.limit]
        _save_last_list([s.key for s in sessions])
        if args.json:
            self.print(json.dumps([self.as_json(i, s) for i, s in enumerate(sessions, 1)], indent=2,
                                  ensure_ascii=False))
            return 0
        if not sessions:
            self.note("No sessions match." if args.search or group else "No sessions found.")
            return 0
        self.table(sessions)
        return 0

    def as_json(self, number: int, session: Session) -> dict:
        live = self.store.live_session(session)
        return {
            "number": number,
            "id": session.session_id,
            "title": session.title,
            "folder": session.cwd,
            "last_active": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(session.last_active)),
            "status": self.status(session),
            "open": bool(live),
            "archived": session.archived,
            "group": self.groups.group_of(session.session_id),
        }

    def cmd_pick(self, args) -> int:
        """No command: the recent sessions, then ask which one to resume."""
        sessions = self.sessions()
        if not sessions:
            self.note("No sessions found.")
            return 0
        shown = sessions[:PICKER_LIMIT]
        _save_last_list([s.key for s in shown])
        self.table(shown)
        if len(sessions) > len(shown):
            self.note(f"... {len(sessions) - len(shown)} older; `{PROG} list` shows all.")
        if not self.interactive():
            return 0
        print("Resume which? (number, ID or title; Enter to quit) ", end="", file=self.err, flush=True)
        answer = self.inp.readline().strip()
        if not answer:
            return 0
        return self.resume(self.resolve(answer), force=False)

    def cmd_show(self, args) -> int:
        session = self.resolve(args.session)
        live = self.store.live_session(session)
        folder = session.cwd or "(unknown)"
        if session.cwd and not os.path.isdir(session.cwd):
            folder += "  (missing)"
        when = time.localtime(session.last_active)
        fields = [
            ("Title", session.title),
            ("Session ID", session.session_id),
            ("Folder", folder),
            ("Last active", f"{time.strftime('%Y-%m-%d %H:%M', when)} ({relative_time(session.last_active, time.time())})"),
            ("Status", self.status(session) or "Not open"),
            ("Group", self.groups.group_of(session.session_id) or "-"),
            ("Used from", ", ".join(sorted(session.entrypoints)) or "-"),
            ("First prompt", session.first_prompt or "-"),
            ("Transcript", str(session.path)),
        ]
        if live and live.job_id:
            fields.append(("Background job", live.job_id))
        for name, value in fields:
            self.print(f"{name + ':':<15}{value}")
        return 0

    def cmd_resume(self, args) -> int:
        return self.resume(self.resolve(args.session), force=args.force)

    def resume(self, session: Session, force: bool) -> int:
        if session.archived:
            try:
                operations.restore(self.store, session)
            except OperationError as e:
                raise CliError(str(e)) from e
            self.note("Restored from the archive.")
            self.store.refresh()
            session = self.store.sessions[session.key]
        if not session.cwd:
            raise CliError(f"Can't tell which folder “{session.title}” was started in, so it can't be resumed.")
        if not os.path.isdir(session.cwd):
            raise CliError(f"The folder for “{session.title}” isn't available: {session.cwd}\n"
                           "Claude Code resumes a session from the folder it was started in.")

        live = self.store.live_session(session)
        job_id = live.job_id if live and live.kind == "bg" else None
        if live and not job_id and not force:
            if not self.confirm(f"“{session.title}” is already open in another Claude Code window. "
                                "Running it in two places makes the conversations diverge. Open it anyway?",
                                "--force"):
                return 1

        claude = shutil.which("claude")
        if not claude:
            raise CliError("Can't find `claude` on your PATH. Is Claude Code installed?")
        try:
            command = launcher.claude_command(session.session_id, job_id)
        except LaunchError as e:
            raise CliError(str(e)) from e
        command[0] = claude
        self.note(f"Resuming “{session.title}” in {short_path(session.cwd)}")
        self.out.flush()
        self.err.flush()
        if sys.platform == "win32":
            return subprocess.call(command, cwd=session.cwd)
        os.chdir(session.cwd)
        _exec(claude, command, launcher.clean_posix_environment())
        return 0  # only reached when _exec is replaced in tests

    def cmd_rename(self, args) -> int:
        session = self.resolve(args.session)
        try:
            operations.rename(self.store, session, " ".join(args.name))
        except OperationError as e:
            raise CliError(str(e)) from e
        self.note(f"Renamed to “{clean_name(' '.join(args.name))}”.")
        return 0

    def cmd_archive(self, args) -> int:
        return self._each(args.sessions, operations.archive, "Archived")

    def cmd_restore(self, args) -> int:
        return self._each(args.sessions, operations.restore, "Restored")

    def _each(self, refs: list[str], action, verb: str) -> int:
        failed = 0
        for session in self.resolve_all(refs):
            try:
                action(self.store, session)
                self.note(f"{verb} “{session.title}”.")
            except OperationError as e:
                self.note(f"“{session.title}”: {e}")
                failed += 1
        return 1 if failed else 0

    def cmd_delete(self, args) -> int:
        sessions = self.resolve_all(args.sessions)
        names = "\n".join(f"  {s.title}  ({short_path(s.cwd)})" for s in sessions)
        if not args.yes:
            self.note(f"{'Delete permanently' if args.permanent else 'Move to the trash'}:\n{names}")
            if not self.confirm("Go ahead?", "--yes"):
                return 1
        failed = 0
        for session in sessions:
            try:
                operations.delete(self.store, session, trash=trash, permanent=args.permanent)
                self.note(f"Deleted “{session.title}”." if args.permanent
                          else f"Moved “{session.title}” to the trash.")
            except OperationError as e:
                hint = " Use --permanent to delete it for good." if "trash" in str(e).lower() else ""
                self.note(f"“{session.title}”: {e}{hint}")
                failed += 1
        self.store.refresh()
        gone = {s.session_id for s in sessions} - {s.session_id for s in self.store.sessions.values()}
        if gone:
            try:
                self.groups.assign(gone, None)
            except OSError:
                pass  # a leftover entry for a deleted session is harmless
        return 1 if failed else 0

    def cmd_group(self, args) -> int:
        name = clean_name(args.group)
        if not name:
            raise CliError("A group needs a name.")
        try:
            name = self.find_group(name)  # reuse an existing group's spelling
        except CliError:
            pass
        sessions = self.resolve_all(args.sessions)
        self._save_groups(lambda: self.groups.assign([s.session_id for s in sessions], name))
        self.note(f"Moved {_count(sessions)} to “{name}”.")
        return 0

    def cmd_ungroup(self, args) -> int:
        sessions = self.resolve_all(args.sessions)
        self._save_groups(lambda: self.groups.assign([s.session_id for s in sessions], None))
        self.note(f"Removed {_count(sessions)} from their group.")
        return 0

    def cmd_groups(self, args) -> int:
        present = [s.session_id for s in self.store.sessions.values()]
        names = self.groups.names(present)
        if not names:
            self.note(f"No groups yet. `{PROG} group NAME SESSION...` makes one.")
            return 0
        for name in names:
            count = sum(1 for s in present if self.groups.group_of(s) == name)
            self.print(f"{name}  ({count})")
        return 0

    def cmd_rename_group(self, args) -> int:
        old, new = self.find_group(args.old), clean_name(args.new)
        if not new:
            raise CliError("A group needs a name.")
        self._save_groups(lambda: self.groups.rename(old, new))
        self.note(f"Renamed “{old}” to “{new}”.")
        return 0

    def cmd_delete_group(self, args) -> int:
        name = self.find_group(args.group)
        self._save_groups(lambda: self.groups.remove(name))
        self.note(f"Deleted the group “{name}”. Its sessions are now ungrouped.")
        return 0

    def _save_groups(self, change) -> None:
        try:
            change()
        except OSError as e:
            raise CliError(f"Couldn't save the groups: {e}") from e


# --- trash ------------------------------------------------------------------------------------------


def trash(path: Path) -> bool:
    """Moves a file or folder to the desktop trash (the freedesktop.org Trash spec, which needs no
    desktop running). False where that isn't possible, e.g. on Windows or across filesystems."""
    if sys.platform in ("win32", "darwin"):
        return False
    trash_dir = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    files, info = trash_dir / "files", trash_dir / "info"
    try:
        files.mkdir(parents=True, exist_ok=True, mode=0o700)
        info.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.stat().st_dev != files.stat().st_dev:
            return False
        number = 1
        while True:
            name = path.name if number == 1 else f"{path.name}.{number}"
            info_file = info / f"{name}.trashinfo"
            if not (files / name).exists():
                try:
                    fd = os.open(info_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    break
                except FileExistsError:
                    pass
            number += 1
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"[Trash Info]\nPath={quote(str(path.absolute()))}\n"
                    f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
        try:
            os.rename(path, files / name)
        except OSError:
            info_file.unlink(missing_ok=True)
            return False
        return True
    except OSError:
        return False


# --- the last list, so numbers from `list` can be used in the next command ----------------------


def _last_list_path() -> Path:
    return paths.app_data_dir() / "last-list.json"


def _save_last_list(keys: list[str]) -> None:
    try:
        path = _last_list_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(keys), encoding="utf-8")
    except OSError:
        pass  # numbers just won't work; IDs and titles still do


def _load_last_list() -> list[str]:
    try:
        keys = json.loads(_last_list_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []


# --- formatting -------------------------------------------------------------------------------------


def _fit(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[:max(width - 1, 0)] + "…"


def _count(sessions: list[Session]) -> str:
    return "1 session" if len(sessions) == 1 else f"{len(sessions)} sessions"


def _use_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb" or sys.platform == "win32":
        return False
    return hasattr(stream, "isatty") and stream.isatty()


# --- arguments --------------------------------------------------------------------------------------


SESSION_HELP = "a number from the last list, the start of the session ID, or words from its title"


def build_parser(prog: str = PROG) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description=f"{APP_NAME}: list, resume, rename, group, archive and delete your Claude Code sessions. "
                    "With no command, lists recent sessions and asks which one to resume.",
        epilog=f"SESSION is {SESSION_HELP}. Run `{prog} COMMAND -h` for a command's options.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")

    def command(name: str, help_text: str, handler: str, aliases=()) -> argparse.ArgumentParser:
        sub = commands.add_parser(name, help=help_text, description=help_text, aliases=list(aliases))
        sub.set_defaults(handler=handler)
        return sub

    sub = command("list", "list sessions, most recently active first", "cmd_list", ["ls"])
    sub.add_argument("search", nargs="*", help="only sessions whose title, folder, ID or group has all these words")
    sub.add_argument("-g", "--group", help="only sessions in this group")
    sub.add_argument("--archived", action="store_true", help="include archived sessions")
    sub.add_argument("-a", "--all", action="store_true",
                     help="include everything: archived, desktop-app, empty and scripted sessions")
    sub.add_argument("-n", "--limit", type=int, metavar="N", help="show at most N sessions")
    sub.add_argument("--json", action="store_true", help="print JSON instead of a table")

    sub = command("resume", "resume a session here, in the folder it was started in", "cmd_resume", ["r"])
    sub.add_argument("session", metavar="SESSION")
    sub.add_argument("-f", "--force", action="store_true", help="resume even if it's already open elsewhere")

    sub = command("show", "show details of a session", "cmd_show")
    sub.add_argument("session", metavar="SESSION")

    sub = command("rename", "rename a session, the same way /rename does", "cmd_rename")
    sub.add_argument("session", metavar="SESSION")
    sub.add_argument("name", nargs="+", metavar="NAME")

    sub = command("archive", "move sessions out of Claude Code's folder, safe from automatic cleanup", "cmd_archive")
    sub.add_argument("sessions", nargs="+", metavar="SESSION")

    sub = command("restore", "bring archived sessions back", "cmd_restore")
    sub.add_argument("sessions", nargs="+", metavar="SESSION")

    sub = command("delete", "move sessions to the trash", "cmd_delete", ["rm"])
    sub.add_argument("sessions", nargs="+", metavar="SESSION")
    sub.add_argument("-y", "--yes", action="store_true", help="don't ask first")
    sub.add_argument("--permanent", action="store_true", help="delete for good instead of moving to the trash")

    sub = command("group", "put sessions in a group (made if it doesn't exist)", "cmd_group")
    sub.add_argument("group", metavar="GROUP")
    sub.add_argument("sessions", nargs="+", metavar="SESSION")

    sub = command("ungroup", "take sessions out of their group", "cmd_ungroup")
    sub.add_argument("sessions", nargs="+", metavar="SESSION")

    command("groups", "list your groups", "cmd_groups")

    sub = command("rename-group", "rename a group", "cmd_rename_group")
    sub.add_argument("old", metavar="GROUP")
    sub.add_argument("new", metavar="NEW_NAME")

    sub = command("delete-group", "delete a group (its sessions just become ungrouped)", "cmd_delete_group")
    sub.add_argument("group", metavar="GROUP")
    return parser


COMMANDS = {"list", "ls", "resume", "r", "show", "rename", "archive", "restore", "delete", "rm", "group",
            "ungroup", "groups", "rename-group", "delete-group"}


def main(argv: list[str] | None = None, prog: str = PROG) -> int:
    args = build_parser(prog).parse_args(argv)
    try:
        cli = Cli()
        if args.command is None:
            return cli.cmd_pick(args)
        return getattr(cli, args.handler)(args)
    except CliError as e:
        print(f"{prog}: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _has_display() -> bool:
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def entry(argv: list[str] | None = None) -> int:
    """`claude-code-organizer`: the window, or the command line when given a command or there's no display."""
    argv = sys.argv[1:] if argv is None else argv
    prog = "claude-code-organizer"
    if argv and (argv[0] in COMMANDS or argv[0] in ("-h", "--help")):
        return main(argv, prog)
    if not _has_display() and argv in ([], ["--version"]):
        return main(argv, prog)
    try:
        from claude_code_organizer import app
    except ImportError as e:
        print(f"{prog}: the window needs Qt for Python (PySide6), which isn't available: {e}\n"
              f"Install it (Ubuntu: sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork), "
              f"or use the command line: {PROG} --help", file=sys.stderr)
        return main([], prog) if not argv and sys.stdin.isatty() else 1
    return app.main(argv)


if __name__ == "__main__":
    sys.exit(main())
