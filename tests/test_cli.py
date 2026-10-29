import io
import os
import sys
import time

import pytest
from conftest import user_line

from claude_code_organizer import cli as cli_module
from claude_code_organizer import paths
from claude_code_organizer.cli import Cli, CliError, build_parser
from claude_code_organizer.groups import Groups


@pytest.fixture
def run(fx, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_data_dir", lambda: tmp_path / "data")
    groups = Groups(tmp_path / "groups.json")

    def run(*argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        cli = Cli(fx.store, groups, out, err, io.StringIO(stdin))
        args = build_parser().parse_args(argv)
        code = cli.cmd_pick(args) if args.command is None else getattr(cli, args.handler)(args)
        return code, out.getvalue(), err.getvalue()

    run.groups = groups
    return run


def make(fx, tmp_path, text, folder="project", age=0, **extra):
    cwd = str(tmp_path / folder)
    os.makedirs(cwd, exist_ok=True)
    sid, transcript = fx.write_session(cwd, [user_line(text, cwd, **extra)], project_dir="p")
    then = time.time() - age
    os.utime(transcript, (then, then))
    return sid, cwd


@pytest.fixture
def execs(monkeypatch):
    """Records the claude process `resume` would replace itself with."""
    calls = []
    monkeypatch.setattr(cli_module.sys, "platform", "linux")
    monkeypatch.setattr(cli_module.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(cli_module.os, "chdir", lambda path: calls.append(("chdir", path)))
    monkeypatch.setattr(cli_module, "_exec", lambda path, args, env: calls.append(("exec", path, args, env)))
    return calls


def test_list_newest_first_and_hides_desktop_app_sessions(fx, tmp_path, run):
    make(fx, tmp_path, "older one", age=3600)
    make(fx, tmp_path, "newer one")
    make(fx, tmp_path, "desktop only", entrypoint="claude-desktop")
    code, out, _ = run("list")
    assert code == 0
    lines = out.splitlines()
    assert lines[0].split()[:2] == ["#", "ID"]
    assert "newer one" in lines[1] and "older one" in lines[2]
    assert "desktop only" not in out
    assert "desktop only" in run("list", "--all")[1]


def test_resume_by_number_execs_claude_in_the_folder(fx, tmp_path, run, execs):
    sid, cwd = make(fx, tmp_path, "the one")
    run("list")
    code, _, err = run("resume", "1")
    assert code == 0
    assert ("chdir", cwd) in execs
    _, path, args, env = execs[-1]
    assert (path, args) == ("/usr/bin/claude", ["/usr/bin/claude", "--resume", sid])
    assert "Resuming “the one”" in err


def test_picker_resumes_the_chosen_number(fx, tmp_path, run, execs):
    sid, _ = make(fx, tmp_path, "pick me")
    make(fx, tmp_path, "not me", age=60)

    class Tty(io.StringIO):
        def isatty(self):
            return True

    out, err = io.StringIO(), io.StringIO()
    code = Cli(fx.store, run.groups, out, err, Tty("1\n")).cmd_pick(build_parser().parse_args([]))
    assert code == 0
    assert execs[-1][2][-1] == sid


def test_resolve_by_id_prefix_title_and_ambiguity(fx, tmp_path, run):
    sid, _ = make(fx, tmp_path, "Fix the parser")
    make(fx, tmp_path, "Fix the printer")
    assert sid in run("show", sid[:8])[1]
    assert sid in run("show", "parser")[1]
    with pytest.raises(CliError, match="matches 2 sessions"):
        run("show", "fix the")
    with pytest.raises(CliError, match="No session matches"):
        run("show", "nothing like it")
    with pytest.raises(CliError, match="list` first"):
        run("show", "1")


def test_groups(fx, tmp_path, run):
    a, _ = make(fx, tmp_path, "alpha")
    b, _ = make(fx, tmp_path, "beta")
    run("group", "Client X", "alpha", "beta")
    run("group", "client x", a[:8])  # reuses the existing spelling
    assert run.groups.names() == ["Client X"]
    assert "Client X  (2)" in run("groups")[1]
    out = run("list", "-g", "client X")[1]
    assert "alpha" in out and "beta" in out and "GROUP" in out

    run("rename-group", "client x", "Work")
    assert run.groups.group_of(a) == "Work"
    run("ungroup", "beta")
    assert run.groups.group_of(b) is None
    run("delete-group", "work")
    assert run.groups.names() == []
    with pytest.raises(CliError, match="no group named"):
        run("list", "-g", "Work")


def test_rename_archive_restore(fx, tmp_path, run):
    sid, _ = make(fx, tmp_path, "old name")
    run("rename", "old", "New", "name")
    assert fx.session(sid).title == "New name"
    run("archive", sid)
    assert fx.session(sid).archived
    assert "New name" not in run("list")[1]
    assert "Archived" in run("list", "--archived")[1]
    run("restore", sid)
    assert not fx.session(sid).archived


def test_delete_needs_a_terminal_or_yes(fx, tmp_path, run):
    sid, _ = make(fx, tmp_path, "doomed")
    run("group", "G", sid)
    with pytest.raises(CliError, match="--yes"):
        run("delete", sid)
    assert run("delete", "--yes", "--permanent", sid)[0] == 0
    fx.store.refresh()
    assert not any(s.session_id == sid for s in fx.store.sessions.values())
    assert run.groups.group_of(sid) is None


@pytest.mark.skipif(sys.platform == "win32", reason="freedesktop.org trash is for Linux")
def test_trash_follows_the_freedesktop_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    for _ in range(2):
        victim = tmp_path / "victim.jsonl"
        victim.write_text("x")
        assert cli_module.trash(victim)
        assert not victim.exists()
    trash = tmp_path / "share" / "Trash"
    assert sorted(p.name for p in (trash / "files").iterdir()) == ["victim.jsonl", "victim.jsonl.2"]
    info = (trash / "info" / "victim.jsonl.trashinfo").read_text()
    assert info.startswith("[Trash Info]\nPath=/") and "victim.jsonl" in info and "DeletionDate=" in info


def test_json_output(fx, tmp_path, run):
    import json

    sid, cwd = make(fx, tmp_path, "as json")
    (item,) = json.loads(run("list", "--json")[1])
    assert item["id"] == sid and item["folder"] == cwd and item["number"] == 1 and item["group"] is None


def test_entry_picks_window_or_command_line(monkeypatch):
    calls = []
    monkeypatch.setattr(cli_module, "main", lambda argv, prog="ccorg": calls.append(("cli", argv)) or 0)
    monkeypatch.setattr(cli_module, "_has_display", lambda: False)
    cli_module.entry([])
    cli_module.entry(["list", "-a"])
    assert calls == [("cli", []), ("cli", ["list", "-a"])]

    from claude_code_organizer import app
    monkeypatch.setattr(app, "main", lambda argv: calls.append(("window", argv)) or 0)
    monkeypatch.setattr(cli_module, "_has_display", lambda: True)
    cli_module.entry([])
    cli_module.entry(["--smoke-test"])
    assert calls[2:] == [("window", []), ("window", ["--smoke-test"])]
