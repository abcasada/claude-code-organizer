"""Drives the main window's actions against a temporary Claude folder."""

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from conftest import user_line  # noqa: E402
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from claude_code_organizer import launcher, paths  # noqa: E402
from claude_code_organizer import window as window_module  # noqa: E402
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


def test_delete_moves_to_trash(win, fx, tmp_path, monkeypatch):
    sid, _ = make_session(fx, tmp_path)
    session = select(win, sid)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    trashed = []

    class FakeQFile:
        @staticmethod
        def moveToTrash(path):
            trashed.append(path)
            shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
            return True

    monkeypatch.setattr(window_module, "QFile", FakeQFile)
    win.act_delete.trigger()
    assert not session.path.exists()
    assert trashed
    assert session.key not in win.store.sessions


def test_group_by_folder(win, fx, tmp_path):
    sid, cwd = make_session(fx, tmp_path)
    win.act_group.trigger()
    win.refresh()
    assert win.proxy.rowCount() == 1
    group = win.proxy.index(0, 0)
    assert group.data() == cwd
    assert win.proxy.rowCount(group) == 1
    assert win.view.isExpanded(group)
