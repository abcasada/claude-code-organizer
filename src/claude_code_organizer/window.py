"""The main window: a searchable, optionally grouped list of sessions and the actions on them."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QFile, QModelIndex, QSortFilterProxyModel, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QBrush, QColor, QDesktopServices, QFont, QGuiApplication, QKeySequence, QPalette, QStandardItem,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu,
    QMessageBox, QStyle, QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem, QToolBar, QTreeView, QWidget,
)

from claude_code_organizer import APP_NAME, REPO_URL, launcher, operations, paths, shortcuts, theme
from claude_code_organizer.dialogs import SettingsDialog, show_about
from claude_code_organizer.launcher import LaunchError
from claude_code_organizer.operations import OperationError
from claude_code_organizer.sessions import LiveSession, Session, SessionStore
from claude_code_organizer.settings import Settings

REFRESH_INTERVAL_MS = 2000

COL_TITLE, COL_FOLDER, COL_ACTIVE, COL_STATUS, COL_ID, COL_RESUME = range(6)
HEADERS = ["Session", "Folder", "Last active", "Status", "ID", ""]

KEY_ROLE = Qt.UserRole + 1       # session key; None on group rows
SORT_ROLE = Qt.UserRole + 2
INFO_ROLE = Qt.UserRole + 3      # RowInfo on session rows


@dataclass(frozen=True)
class RowInfo:
    archived: bool
    hidden: bool
    resumable: bool
    search_text: str


def relative_time(timestamp: float, now: float) -> str:
    age = now - timestamp
    if age < 60:
        return "just now"
    if age < 3600:
        return f"{int(age // 60)} min ago"
    if age < 86400:
        return f"{int(age // 3600)} h ago"
    when = time.localtime(timestamp)
    if age < 7 * 86400:
        return time.strftime("%a %H:%M", when)
    return time.strftime("%Y-%m-%d", when)


def trash_label() -> str:
    return "Recycle Bin" if sys.platform == "win32" else "Trash"


@lru_cache(maxsize=None)
def _brush(color: str) -> QBrush:
    return QBrush(QColor(color))


class FolderChecker:
    """Whether session folders exist, checked in the background: a disconnected network drive can
    take seconds to answer, which must not freeze the list."""

    MAX_AGE = 30.0

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="folder-check")
        self._results: dict[str, tuple[bool, float]] = {}
        self._pending: set[str] = set()

    def exists(self, folder: str) -> bool | None:
        """The last known answer (None if not known yet); schedules a re-check when it's stale."""
        known = self._results.get(folder)
        if (known is None or time.monotonic() - known[1] > self.MAX_AGE) and folder not in self._pending:
            self._pending.add(folder)
            self._pool.submit(self._check, folder)
        return known[0] if known else None

    def _check(self, folder: str) -> None:
        self._results[folder] = (os.path.isdir(folder), time.monotonic())
        self._pending.discard(folder)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# --- Model -----------------------------------------------------------------------------------------


class SessionModel(QStandardItemModel):
    """Session rows, either flat or under one group row per folder. Updated in place on refresh
    so selection, scrolling and expanded groups survive."""

    def __init__(self, parent=None) -> None:
        super().__init__(0, len(HEADERS), parent)
        self.setHorizontalHeaderLabels(HEADERS)
        self.grouped = False
        self.scheme = "light"
        self.folders = FolderChecker()
        self.rows: dict[str, list[QStandardItem]] = {}
        self._groups: dict[str, list[QStandardItem]] = {}

    def set_grouped(self, grouped: bool) -> None:
        if grouped == self.grouped:
            return
        self.grouped = grouped
        self.removeRows(0, self.rowCount())
        self.rows.clear()
        self._groups.clear()

    def sync(self, store: SessionStore) -> list[QStandardItem]:
        """Brings the rows up to date with the store. Returns any group rows that were just added."""
        now = time.time()
        seen = set()
        new_groups: list[QStandardItem] = []
        for session in store.sessions.values():
            seen.add(session.key)
            parent = self._group_for(session, new_groups) if self.grouped else self.invisibleRootItem()
            row = self.rows.get(session.key)
            if row is None:
                row = [QStandardItem() for _ in HEADERS]
                for item in row:
                    item.setEditable(False)
                parent.appendRow(row)
                self.rows[session.key] = row
            elif (row[0].parent() or self.invisibleRootItem()) is not parent:
                old_parent = row[0].parent() or self.invisibleRootItem()
                row = old_parent.takeRow(row[0].row())
                parent.appendRow(row)
                self.rows[session.key] = row
            self._fill(row, session, store.live_session(session), now)

        for key in [k for k in self.rows if k not in seen]:
            row = self.rows.pop(key)
            (row[0].parent() or self.invisibleRootItem()).removeRow(row[0].row())
        if self.grouped:
            self._update_groups(now)
        return new_groups

    def _group_for(self, session: Session, new_groups: list[QStandardItem]) -> QStandardItem:
        key = paths.folder_key(session.cwd) if session.cwd else ""
        group = self._groups.get(key)
        if group is None:
            group = [QStandardItem() for _ in HEADERS]
            for item in group:
                item.setEditable(False)
            group[COL_TITLE].setText(session.cwd or "(unknown folder)")
            group[COL_TITLE].setToolTip(session.cwd or "")
            group[COL_TITLE].setIcon(QApplication.style().standardIcon(QStyle.SP_DirIcon))
            font = QFont()
            font.setBold(True)
            group[COL_TITLE].setFont(font)
            self.invisibleRootItem().appendRow(group)
            self._groups[key] = group
            new_groups.append(group[COL_TITLE])
        return group[COL_TITLE]

    def _update_groups(self, now: float) -> None:
        for key, group in list(self._groups.items()):
            head = group[COL_TITLE]
            if head.rowCount() == 0:
                self.invisibleRootItem().removeRow(head.row())
                del self._groups[key]
                continue
            latest = max(head.child(i, COL_ACTIVE).data(SORT_ROLE) or 0 for i in range(head.rowCount()))
            head.setData(head.text().lower(), SORT_ROLE)
            group[COL_ACTIVE].setText(relative_time(latest, now))
            group[COL_ACTIVE].setData(latest, SORT_ROLE)
            group[COL_FOLDER].setData(head.text().lower(), SORT_ROLE)

    def _fill(self, row: list[QStandardItem], session: Session, live: LiveSession | None, now: float) -> None:
        # Unknown until the background check answers; assume it's there.
        folder_ok = bool(session.cwd) and self.folders.exists(session.cwd) is not False
        if session.archived:
            status, color = "Archived", None
        elif live:
            status = live.label
            kind = "background" if live.kind == "bg" else {"busy": "working", "shell": "working",
                                                             "waiting": "waiting"}.get(live.status, "idle")
            color = theme.STATUS_COLORS[self.scheme][kind]
        elif not folder_ok:
            status, color = "Folder missing", None
        else:
            status, color = "", None

        title = session.title
        info = RowInfo(
            archived=session.archived,
            hidden=not session.has_content or session.is_scripted,
            resumable=folder_ok,
            search_text=f"{title} {session.cwd or ''} {session.session_id}".lower(),
        )
        values = {
            COL_TITLE: (title, title.lower()),
            COL_FOLDER: (session.cwd or "", (session.cwd or "").lower()),
            COL_ACTIVE: (relative_time(session.last_active, now), session.last_active),
            COL_STATUS: (status, status),
            COL_ID: (session.session_id[:8], session.session_id),
            COL_RESUME: ("Resume", 0),
        }
        for column, (text, sort_value) in values.items():
            item = row[column]
            if item.text() != text:
                item.setText(text)
            item.setData(sort_value, SORT_ROLE)
        row[COL_TITLE].setData(session.key, KEY_ROLE)
        row[COL_TITLE].setData(info, INFO_ROLE)
        row[COL_RESUME].setData(info, INFO_ROLE)
        row[COL_TITLE].setToolTip(title)
        row[COL_FOLDER].setToolTip(session.cwd or "")
        row[COL_ACTIVE].setToolTip(time.strftime("%A %d %B %Y, %H:%M", time.localtime(session.last_active)))
        row[COL_ID].setToolTip(session.session_id)

        muted = theme.MUTED[self.scheme]
        for column in (COL_FOLDER, COL_ID):
            row[column].setForeground(_brush(muted))
        row[COL_STATUS].setForeground(_brush(color or muted))
        font = QFont()
        font.setItalic(session.archived)
        row[COL_TITLE].setFont(font)
        if session.archived:
            row[COL_TITLE].setForeground(_brush(muted))
        else:
            row[COL_TITLE].setData(None, Qt.ForegroundRole)


class SessionFilter(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(True)  # a group shows while any of its sessions does
        self.setSortRole(SORT_ROLE)
        self.words: list[str] = []
        self.show_archived = False
        self.show_hidden = False

    def update(self, text: str | None = None, show_archived: bool | None = None, show_hidden: bool | None = None) -> None:
        # Qt 6.10 replaced invalidateFilter() with begin/endFilterChange(); support both.
        modern = hasattr(self, "beginFilterChange") and hasattr(self, "endFilterChange")
        if modern:
            self.beginFilterChange()
        if text is not None:
            self.words = text.lower().split()
        if show_archived is not None:
            self.show_archived = show_archived
        if show_hidden is not None:
            self.show_hidden = show_hidden
        if modern:
            self.endFilterChange()
        else:
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        info = self.sourceModel().index(source_row, COL_TITLE, source_parent).data(INFO_ROLE)
        if info is None:
            return False  # group row: shown through its sessions
        if info.archived and not self.show_archived:
            return False
        if info.hidden and not self.show_hidden:
            return False
        return all(word in info.search_text for word in self.words)


# --- View ------------------------------------------------------------------------------------------


class RowDelegate(QStyledItemDelegate):
    """Adds some breathing room to every row."""

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), option.fontMetrics.height() + 14))
        return size


class ResumeButtonDelegate(RowDelegate):
    """Draws a Resume button in each session row and reports clicks on it."""

    clicked = Signal(QModelIndex)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pressed: QModelIndex | None = None

    def _button_rect(self, option):
        return option.rect.adjusted(6, 4, -6, -4)

    def paint(self, painter, option, index):
        # Row background first (selection, alternating color), without the cell's own text.
        background = QStyleOptionViewItem(option)
        self.initStyleOption(background, index)
        background.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, background, painter, option.widget)

        info = index.data(INFO_ROLE)
        if info is None:
            return
        button = QStyleOptionButton()
        button.rect = self._button_rect(option)
        button.text = "Resume"  # an archived session is restored first
        button.palette = QPalette(option.palette)
        button.fontMetrics = option.fontMetrics
        button.state = QStyle.State_Raised
        if info.resumable:
            button.state |= QStyle.State_Enabled
            button.palette.setCurrentColorGroup(QPalette.Active)
            if option.state & QStyle.State_MouseOver:
                button.state |= QStyle.State_MouseOver
            if self._pressed is not None and self._pressed == index:
                button.state |= QStyle.State_Sunken
        else:
            button.palette.setCurrentColorGroup(QPalette.Disabled)
        style.drawControl(QStyle.CE_PushButton, button, painter, option.widget)

    def editorEvent(self, event, model, option, index):
        info = index.data(INFO_ROLE)
        if info is None or not info.resumable:
            return False
        inside = self._button_rect(option).contains(event.position().toPoint()) if hasattr(event, "position") else False
        if event.type() == QEvent.MouseButtonPress and inside and event.button() == Qt.LeftButton:
            self._pressed = QModelIndex(index)
            return True
        if event.type() == QEvent.MouseButtonRelease and self._pressed is not None:
            was_pressed = self._pressed == index
            self._pressed = None
            if was_pressed and inside:
                self.clicked.emit(QModelIndex(index))
            return True
        if event.type() == QEvent.MouseButtonDblClick and inside:
            return True  # don't let a fast double-click on the button also trigger the row's double-click
        return False


class SessionView(QTreeView):
    def mousePressEvent(self, event) -> None:
        # Clicking empty space deselects.
        if not self.indexAt(event.position().toPoint()).isValid():
            self.clear_selection()
        super().mousePressEvent(event)

    def clear_selection(self) -> None:
        self.selectionModel().clearSelection()
        self.selectionModel().setCurrentIndex(QModelIndex(), self.selectionModel().SelectionFlag.NoUpdate)


# --- Window ----------------------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, store: SessionStore | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.store = store or SessionStore()
        self.setWindowTitle(APP_NAME)
        self.resize(1150, 640)

        self.model = SessionModel(self)
        self.proxy = SessionFilter(self)
        self.proxy.setSourceModel(self.model)

        self.view = SessionView()
        self.view.setModel(self.proxy)
        self.view.setUniformRowHeights(True)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSortingEnabled(True)
        self.view.setMouseTracking(True)
        self.view.setAllColumnsShowFocus(True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.setItemDelegate(RowDelegate(self.view))
        self.resume_delegate = ResumeButtonDelegate(self.view)
        self.view.setItemDelegateForColumn(COL_RESUME, self.resume_delegate)
        header = self.view.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_RESUME, QHeaderView.Fixed)
        for column, width in ((COL_FOLDER, 360), (COL_ACTIVE, 110), (COL_STATUS, 130), (COL_ID, 90), (COL_RESUME, 96)):
            header.resizeSection(column, width)
        self.view.sortByColumn(COL_ACTIVE, Qt.DescendingOrder)
        self.setCentralWidget(self.view)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()
        self.status_label = QLabel()
        self.statusBar().addWidget(self.status_label, 1)

        self.view.doubleClicked.connect(self._on_double_click)
        self.view.customContextMenuRequested.connect(self._show_context_menu)
        self.resume_delegate.clicked.connect(lambda index: self._resume([self._session_at(index)]))
        self.view.selectionModel().selectionChanged.connect(self._update_actions)

        # Clicking empty parts of the window deselects too (not the menu bar: its menus act on the selection).
        for widget in (self.toolbar, self.statusBar()):
            widget.installEventFilter(self)

        self.timer = QTimer(self)
        self.timer.setInterval(REFRESH_INTERVAL_MS)
        self.timer.timeout.connect(self.refresh)

        self._restore_state()
        self._apply_view_settings()

    # --- setup -------------------------------------------------------------------------------------

    def _action(self, text, slot, shortcut=None, checkable=False) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.setCheckable(checkable)
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self.act_settings = self._action("&Settings...", self._open_settings, QKeySequence.Preferences)
        if self.act_settings.shortcut().isEmpty():
            self.act_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.act_open_data = self._action("Open &Claude Data Folder", lambda: self._open_folder(str(self.store.claude_root)))
        self.act_shortcut = self._action("Add to Start &Menu", self._add_shortcut)
        self.act_shortcut.setVisible(shortcuts.can_create())
        self.act_quit = self._action("&Quit", self.close, QKeySequence.Quit)
        if self.act_quit.shortcut().isEmpty():
            self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))

        self.act_group = self._action("&Group by Folder", self._toggle_group, "Ctrl+G", checkable=True)
        self.act_show_archived = self._action("Show &Archived Sessions", self._toggle_archived, checkable=True)
        self.act_show_hidden = self._action("Show &Empty and Scripted Sessions", self._toggle_hidden, checkable=True)
        self.act_refresh = self._action("&Refresh", self.refresh, QKeySequence.Refresh)
        if self.act_refresh.shortcut().isEmpty():
            self.act_refresh.setShortcut(QKeySequence("F5"))

        self.act_resume = self._action("&Resume", lambda: self._resume(self._selected()))
        self.act_resume.setShortcuts([QKeySequence(Qt.Key_Return), QKeySequence(Qt.Key_Enter)])
        self.act_resume.setShortcutContext(Qt.WidgetShortcut)
        self.view.addAction(self.act_resume)
        self.act_rename = self._action("Re&name...", self._rename, "F2")
        self.act_archive = self._action("&Archive", self._archive_or_restore, "Ctrl+E")
        self.act_delete = self._action(f"&Delete (Move to {trash_label()})...", self._delete, QKeySequence.Delete)
        self.act_delete.setShortcutContext(Qt.WidgetShortcut)
        self.view.addAction(self.act_delete)
        self.act_copy_command = self._action("Copy Resume &Command", self._copy_command)
        self.act_copy_id = self._action("Copy Session &ID", self._copy_id)
        self.act_open_folder = self._action("Open &Folder", lambda: self._open_folder(self._single().cwd))

        self.act_about = self._action(f"&About {APP_NAME}", lambda: show_about(self))
        self.act_github = self._action("Project on &GitHub", lambda: QDesktopServices.openUrl(QUrl(REPO_URL)))

    def _build_menus(self) -> None:
        menu = self.menuBar().addMenu("&File")
        menu.addActions([self.act_settings, self.act_open_data, self.act_shortcut])
        menu.addSeparator()
        menu.addAction(self.act_quit)

        menu = self.menuBar().addMenu("&View")
        menu.addActions([self.act_group, self.act_show_archived, self.act_show_hidden])
        menu.addSeparator()
        menu.addAction(self.act_refresh)

        self.session_menu = self.menuBar().addMenu("&Session")
        self._fill_session_menu(self.session_menu)

        menu = self.menuBar().addMenu("&Help")
        menu.addActions([self.act_github, self.act_about])

    def _fill_session_menu(self, menu: QMenu) -> None:
        menu.addActions([self.act_resume, self.act_rename, self.act_archive, self.act_delete])
        menu.addSeparator()
        menu.addActions([self.act_copy_command, self.act_copy_id, self.act_open_folder])

    def _build_toolbar(self) -> None:
        self.toolbar = QToolBar("Main")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.toggleViewAction().setVisible(False)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sessions, folders or IDs  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(420)
        self.search.textChanged.connect(lambda text: self.proxy.update(text=text))
        self.addAction(self._action("Find", lambda: (self.search.setFocus(), self.search.selectAll()), QKeySequence.Find))
        self.toolbar.addWidget(self.search)
        spacer = QWidget()
        spacer.setFixedWidth(12)
        self.toolbar.addWidget(spacer)
        self.toolbar.addAction(self.act_group)
        self.toolbar.addAction(self.act_show_archived)
        self.addToolBar(self.toolbar)

    def _restore_state(self) -> None:
        if self.settings.window_geometry:
            self.restoreGeometry(QByteArray.fromBase64(self.settings.window_geometry.encode()))
        if self.settings.header_state:
            self.view.header().restoreState(QByteArray.fromBase64(self.settings.header_state.encode()))
        self.act_group.setChecked(self.settings.group_by_folder)
        self.act_show_archived.setChecked(self.settings.show_archived)
        self.act_show_hidden.setChecked(self.settings.show_hidden)

    def _apply_view_settings(self) -> None:
        self.model.set_grouped(self.settings.group_by_folder)
        self.view.setRootIsDecorated(self.settings.group_by_folder)
        self.view.setColumnHidden(COL_FOLDER, self.settings.group_by_folder)
        self.proxy.update(show_archived=self.settings.show_archived, show_hidden=self.settings.show_hidden)

    # --- lifecycle ---------------------------------------------------------------------------------

    def start(self) -> None:
        self.refresh()
        self.view.clear_selection()
        self.search.setFocus()
        self.timer.start()

    def set_scheme(self, scheme: str) -> None:
        self.model.scheme = scheme  # item colors are set per row, on the next sync
        self.refresh()

    def refresh(self) -> None:
        selected = {s.key for s in self._selected()}
        try:
            self.store.refresh()
        except OSError as e:
            self.status_label.setText(f"Couldn't read sessions: {e}")
            return
        for group in self.model.sync(self.store):
            self.view.expand(self.proxy.mapFromSource(group.index()))
        self._reselect(selected)
        self._update_status()
        self._update_actions()

    def _reselect(self, keys: set[str]) -> None:
        # Rows can move when the list re-sorts; selection follows the sessions, not row numbers.
        current = {s.key for s in self._selected()}
        if current == keys:
            return
        self.view.clear_selection()
        for key, row in self.model.rows.items():
            if key in keys:
                index = self.proxy.mapFromSource(row[COL_TITLE].index())
                if index.isValid():
                    self.view.selectionModel().select(index, self.view.selectionModel().SelectionFlag.Select
                                                      | self.view.selectionModel().SelectionFlag.Rows)

    def _update_status(self) -> None:
        sessions = self.store.sessions.values()
        open_count = sum(1 for s in sessions if self.store.live_session(s))
        archived = sum(1 for s in sessions if s.archived)
        shown = self._count_visible()
        self.status_label.setText(f"{shown} shown  ·  {open_count} open  ·  {archived} archived")

    def _count_visible(self) -> int:
        count = 0
        for row in range(self.proxy.rowCount()):
            index = self.proxy.index(row, 0)
            count += self.proxy.rowCount(index) if self.model.grouped else 1
        return count

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        self.timer.stop()
        self.model.folders.shutdown()
        self.settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        self.settings.header_state = bytes(self.view.header().saveState().toBase64()).decode()
        self.settings.save()
        super().closeEvent(event)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.MouseButtonPress:
            self.view.clear_selection()
        return False

    # --- selection ---------------------------------------------------------------------------------

    def _session_at(self, proxy_index: QModelIndex) -> Session | None:
        source = self.proxy.mapToSource(proxy_index.siblingAtColumn(COL_TITLE))
        key = source.data(KEY_ROLE)
        return self.store.sessions.get(key) if key else None

    def _selected(self) -> list[Session]:
        sessions = []
        for index in self.view.selectionModel().selectedRows(COL_TITLE):
            session = self._session_at(index)
            if session:
                sessions.append(session)
        return sessions

    def _single(self) -> Session | None:
        selected = self._selected()
        return selected[0] if len(selected) == 1 else None

    def _update_actions(self) -> None:
        selected = self._selected()
        single = len(selected) == 1
        self.act_resume.setEnabled(bool(selected))
        self.act_rename.setEnabled(single)
        self.act_delete.setEnabled(bool(selected))
        self.act_archive.setEnabled(bool(selected))
        self.act_archive.setText("&Restore" if selected and all(s.archived for s in selected) else "&Archive")
        for action in (self.act_copy_command, self.act_copy_id, self.act_open_folder):
            action.setEnabled(single)

    def _on_double_click(self, index: QModelIndex) -> None:
        if index.column() == COL_RESUME:
            return
        session = self._session_at(index)
        if session:
            self._resume([session])

    def _show_context_menu(self, pos) -> None:
        index = self.view.indexAt(pos)
        if not index.isValid() or not self._session_at(index):
            return
        if not self.view.selectionModel().isRowSelected(index.row(), index.parent()):
            self.view.clear_selection()
            self.view.selectionModel().select(index, self.view.selectionModel().SelectionFlag.Select
                                              | self.view.selectionModel().SelectionFlag.Rows)
        menu = QMenu(self)
        self._fill_session_menu(menu)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    # --- actions -----------------------------------------------------------------------------------

    def _error(self, text: str) -> None:
        QMessageBox.warning(self, APP_NAME, text)

    def _resume(self, sessions: list[Session | None]) -> None:
        keys = [s.key for s in sessions if s]
        self.refresh()  # the live check must be current: a session may have been closed a moment ago
        for key in keys:
            session = self.store.sessions.get(key)
            if session:
                self._resume_one(session)

    def _resume_one(self, session: Session) -> None:
        if session.archived:
            try:
                operations.restore(self.store, session)
            except OperationError as e:
                self._error(str(e))
                return
            self.refresh()
            session = self.store.sessions.get(session.key)
            if not session:
                return
        if not session.cwd:
            self._error(f"Can't tell which folder “{session.title}” was started in, so it can't be resumed.")
            return
        if not os.path.isdir(session.cwd):
            self._error(f"The folder for “{session.title}” isn't available:\n\n{session.cwd}\n\n"
                        "Claude Code resumes a session from the folder it was started in. "
                        "Reconnect the drive or restore the folder and try again.")
            return

        live = self.store.live_session(session)
        job_id = live.job_id if live and live.kind == "bg" else None
        if live and not job_id:
            answer = QMessageBox.question(
                self, APP_NAME,
                f"“{session.title}” is already open in another Claude Code window.\n\n"
                "Running it in two places at once makes the conversations diverge. Open another copy anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        try:
            command = launcher.claude_command(session.session_id, job_id)
            spec = launcher.build_spec(command, session.cwd, session.title, self.settings.use_tabs,
                                       self.settings.linux_terminal)
            launcher.launch(spec)
        except LaunchError as e:
            self._error(f"Couldn't open a terminal:\n\n{e}")

    def _rename(self) -> None:
        session = self._single()
        if not session:
            return
        name, ok = QInputDialog.getText(self, "Rename Session", "New name:", QLineEdit.Normal, session.title)
        if not ok or name.strip() == session.title:
            return
        try:
            operations.rename(self.store, session, name)
        except OperationError as e:
            self._error(str(e))
        self.refresh()

    def _archive_or_restore(self) -> None:
        selected = self._selected()
        if not selected:
            return
        restoring = all(s.archived for s in selected)
        errors = []
        for session in selected:
            try:
                (operations.restore if restoring else operations.archive)(self.store, session)
            except OperationError as e:
                errors.append(f"“{session.title}”: {e}")
        self.refresh()
        if errors:
            self._error("\n\n".join(errors))
        elif not restoring and not self.settings.show_archived:
            noun = "session" if len(selected) == 1 else f"{len(selected)} sessions"
            self.statusBar().showMessage(f"Archived {noun}. View → Show Archived Sessions to see it again.", 6000)

    def _delete(self) -> None:
        selected = self._selected()
        if not selected:
            return
        names = "\n".join(f"• {s.title}" for s in selected[:6]) + ("\n• ..." if len(selected) > 6 else "")
        what = "this session" if len(selected) == 1 else f"these {len(selected)} sessions"
        answer = QMessageBox.question(self, APP_NAME, f"Move {what} to the {trash_label()}?\n\n{names}",
                                      QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer != QMessageBox.Yes:
            return

        errors = []
        for session in selected:
            try:
                operations.delete(self.store, session, trash=lambda p: QFile.moveToTrash(str(p)))
            except OperationError as e:
                if "trash" not in str(e).lower():
                    errors.append(f"“{session.title}”: {e}")
                    continue
                answer = QMessageBox.warning(
                    self, APP_NAME,
                    f"“{session.title}” couldn't be moved to the {trash_label()} "
                    f"(it may be disabled, or the files are on a network drive).\n\n"
                    "Delete it permanently instead? This can't be undone.",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if answer == QMessageBox.Yes:
                    try:
                        operations.delete(self.store, session, trash=lambda p: False, permanent=True)
                    except OperationError as e2:
                        errors.append(f"“{session.title}”: {e2}")
        self.refresh()
        if errors:
            self._error("\n\n".join(errors))

    def _copy_command(self) -> None:
        session = self._single()
        if not session:
            return
        command = f"claude --resume {session.session_id}"
        if session.cwd:
            if sys.platform == "win32":
                command = f"Set-Location -LiteralPath '{session.cwd.replace(chr(39), chr(39) * 2)}'; {command}"
            else:
                command = f"cd {shlex.quote(session.cwd)} && {command}"
        QGuiApplication.clipboard().setText(command)
        self.statusBar().showMessage("Resume command copied.", 3000)

    def _copy_id(self) -> None:
        session = self._single()
        if session:
            QGuiApplication.clipboard().setText(session.session_id)
            self.statusBar().showMessage("Session ID copied.", 3000)

    def _add_shortcut(self) -> None:
        try:
            path = shortcuts.create_start_menu_shortcut()
        except (OSError, subprocess.CalledProcessError) as e:
            self._error(f"Couldn't create the shortcut: {e}")
            return
        QMessageBox.information(self, APP_NAME, f"Added “{APP_NAME}” to the Start menu.\n\n{path}\n\n"
                                "If you move this program, add it again.")

    def _open_folder(self, folder: str | None) -> None:
        if folder and Path(folder).is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self._error(f"The folder isn't available:\n\n{folder}")

    # --- view options ------------------------------------------------------------------------------

    def _toggle_group(self, checked: bool) -> None:
        self.settings.group_by_folder = checked
        self._apply_view_settings()
        self.refresh()

    def _toggle_archived(self, checked: bool) -> None:
        self.settings.show_archived = checked
        self.proxy.update(show_archived=checked)
        self._update_status()

    def _toggle_hidden(self, checked: bool) -> None:
        self.settings.show_hidden = checked
        self.proxy.update(show_hidden=checked)
        self._update_status()

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            dialog.apply()
            self.settings.save()
            self.set_scheme(theme.apply_theme(QApplication.instance(), self.settings.theme))
