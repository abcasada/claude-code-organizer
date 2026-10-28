"""Settings and About dialogs."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QMessageBox, QWidget

from claude_code_organizer import APP_NAME, REPO_URL, __version__, launcher, paths
from claude_code_organizer.settings import THEMES, Settings


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        layout = QFormLayout(self)
        layout.setVerticalSpacing(12)

        self.theme = QComboBox()
        for key, label in THEMES.items():
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(list(THEMES).index(settings.theme))
        layout.addRow("Theme:", self.theme)

        if sys.platform == "win32":
            tabs_text = "Open sessions as tabs in Windows Terminal"
        else:
            tabs_text = "Open sessions as tabs in an existing terminal window"
        self.use_tabs = QCheckBox(tabs_text)
        self.use_tabs.setChecked(settings.use_tabs)
        if sys.platform == "win32" and not launcher.find_windows_terminal():
            self.use_tabs.setEnabled(False)
            self.use_tabs.setToolTip("Windows Terminal isn't installed; sessions open in a PowerShell window.")
        layout.addRow("", self.use_tabs)

        self.terminal = None
        if sys.platform != "win32":
            self.terminal = QComboBox()
            self.terminal.addItem("Automatic", "auto")
            for name in launcher.available_linux_terminals():
                self.terminal.addItem(name, name)
            index = self.terminal.findData(settings.linux_terminal)
            self.terminal.setCurrentIndex(max(index, 0))
            layout.addRow("Terminal:", self.terminal)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def apply(self) -> None:
        self.settings.theme = self.theme.currentData()
        self.settings.use_tabs = self.use_tabs.isChecked()
        if self.terminal is not None:
            self.settings.linux_terminal = self.terminal.currentData()


def show_about(parent: QWidget) -> None:
    QMessageBox.about(
        parent,
        f"About {APP_NAME}",
        f"<h3>{APP_NAME} {__version__}</h3>"
        "<p>Lists your Claude Code sessions and resumes, renames, archives or deletes them.</p>"
        f'<p><a href="{REPO_URL}">{REPO_URL}</a></p>'
        f"<p>Sessions are read from <code>{paths.claude_dir()}</code><br>"
        f"Archived sessions are kept in <code>{paths.archive_dir()}</code></p>"
        "<p>MIT License. Not affiliated with Anthropic.</p>",
    )
