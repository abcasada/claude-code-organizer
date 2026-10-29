"""Drives the main window's actions against a temporary Claude folder."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import user_line  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from claude_code_organizer import launcher, paths  # noqa: E402
from claude_code_organizer import window as window_module  # noqa: E402
from claude_code_organizer.groups import Groups  # noqa: E402
from claude_code_organizer.settings import Settings  # noqa: E402
from claude_code_organizer.window import COL_TITLE, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app, fx, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "app_config_dir", lambda: tmp_path / "config")
    w = MainWindow(Settings(), fx.store)
    yield w
    w.timer.stop()
    w.model.folders.shutdown()
    w.deleteLater()


def make_session(fx, tmp_path, text="hello"):
    cwd = str(tmp_path / "project")
    os.makedirs(cwd, exist_ok=True)
    # A short project folder name keeps paths under Windows' 260-character limit.
    sid, _ = fx.write_session(cwd, [user_line(text, cwd)], project_dir="p")
    return sid, cwd


def select(win, session_id):
    win.refresh()
    key = next(k for k, s in win.store.sessions.items() if s.session_id == session_id)
    index = win.proxy.mapFromSource(win.model.rows[key][COL_TITLE].index())
    assert index.isValid(), "session row is filtered out"
    win.view.clear_selection()
    sm = win.view.selectionModel()
    sm.select(index, sm.SelectionFlag.Select | sm.SelectionFlag.Rows)
    return win.store.sessions[key]


def test_archive_hides_and_restore_brings_back(win, fx, tmp_path):
    sid, _ = make_session(fx, tmp_path)
    session = select(win, sid)
    win.act_archive.trigger()
    assert (fx.archive / "projects" / session.project_dir / f"{sid}.jsonl").exists()
    assert win.proxy.rowCount() == 0  # archived sessions are hidden by default

    win.act_show_archived.trigger()
    select(win, sid)
    assert win.act_archive.text() == "&Restore"
    win.act_archive.trigger()
    assert session.path.exists()
    assert not win.store.sessions[session.key].archived


def test_rename(win, fx, tmp_path, monkeypatch):
    sid, _ = make_session(fx, tmp_path)
    select(win, sid)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Renamed here", True))
    win.act_rename.trigger()
    assert next(s for s in win.store.sessions.values() if s.session_id == sid).title == "Renamed here"


def test_resume_launches_in_the_session_folder(win, fx, tmp_path, monkeypatch):
    sid, cwd = make_session(fx, tmp_path)
    select(win, sid)
    launched = []
    monkeypatch.setattr(launcher, "launch", launched.append)
    monkeypatch.setattr(launcher, "_pick_linux_terminal", lambda preferred: "xterm")
    win.act_resume.trigger()
    assert len(launched) == 1
    assert launched[0].cwd == cwd
    assert sid in (launched[0].args if isinstance(launched[0].args, str) else " ".join(launched[0].args))


@pytest.fixture
def trashed(monkeypatch):
    """Fakes the Recycle Bin / Trash and lists what went there."""
    paths_trashed = []

    class FakeQFile:
        @staticmethod
        def moveToTrash(path):
            paths_trashed.append(path)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            return True

    monkeypatch.setattr(window_module, "QFile", FakeQFile)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    return paths_trashed


def test_delete_moves_to_trash(win, fx, tmp_path, trashed):
    sid, _ = make_session(fx, tmp_path)
    session = select(win, sid)
    win.act_delete.trigger()
    assert not session.path.exists()
    assert trashed
    assert session.key not in win.store.sessions


def group_by(win, mode):
    next(a for a in win.group_by_actions.actions() if a.data() == mode).trigger()


def test_group_by_folder(win, fx, tmp_path):
    sid, cwd = make_session(fx, tmp_path)
    group_by(win, "folder")
    win.refresh()
    assert win.proxy.rowCount() == 1
    group = win.proxy.index(0, 0)
    assert group.data() == cwd
    assert win.proxy.rowCount(group) == 1
    assert win.view.isExpanded(group)
    assert win.group_by_button.text() == "Group by: Folder"


def test_move_to_group_and_back(win, fx, tmp_path, monkeypatch):
    sid, _ = make_session(fx, tmp_path)
    other, _ = fx.write_session(str(tmp_path / "project"), [user_line("other", str(tmp_path / "project"))],
                                project_dir="p")
    group_by(win, "group")
    select(win, sid)
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("  Client   X ", True))
    win.act_new_group.trigger()
    assert win.groups.group_of(sid) == "Client X"
    assert Groups(win.groups.path).group_of(sid) == "Client X"  # saved

    headers = {win.proxy.index(r, 0).data(): win.proxy.index(r, 0) for r in range(win.proxy.rowCount())}
    assert set(headers) == {"Client X", "Ungrouped"}
    assert win.proxy.rowCount(headers["Client X"]) == 1
    assert win.proxy.rowCount(headers["Ungrouped"]) == 1

    select(win, other)
    win._fill_group_menu()
    assert [a.text() for a in win.group_menu.actions() if a.isCheckable()] == ["Client X"]

    select(win, sid)
    win.act_ungroup.trigger()
    assert win.groups.group_of(sid) is None
    assert [win.proxy.index(r, 0).data() for r in range(win.proxy.rowCount())] == ["Ungrouped"]


def test_rename_and_delete_group(win, fx, tmp_path, monkeypatch):
    sid, _ = make_session(fx, tmp_path)
    win.groups.assign([sid], "Old")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("New", True))
    win._rename_group("Old")
    assert win.groups.group_of(sid) == "New"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win._delete_group("New")
    assert win.groups.group_of(sid) is None
    assert session_exists(win, sid)


def test_deleting_a_session_drops_its_group(win, fx, tmp_path, trashed):
    sid, _ = make_session(fx, tmp_path)
    win.groups.assign([sid], "Doomed")
    select(win, sid)
    win.act_delete.trigger()
    assert not session_exists(win, sid)
    assert win.groups.group_of(sid) is None


def test_desktop_app_sessions_hidden_by_default(win, fx, tmp_path):
    cwd = str(tmp_path / "project")
    fx.write_session(cwd, [user_line("from the app", cwd, entrypoint="claude-desktop")], project_dir="p")
    win.refresh()
    assert win.proxy.rowCount() == 0
    win.act_show_desktop_app.trigger()
    assert win.proxy.rowCount() == 1
    assert win.settings.show_desktop_app


def session_exists(win, session_id):
    win.refresh()
    return any(s.session_id == session_id for s in win.store.sessions.values())
