"""Entry point."""

from __future__ import annotations

import argparse
import sys
from importlib import resources

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from claude_code_organizer import APP_NAME, __version__, theme
from claude_code_organizer.settings import Settings

DESKTOP_ID = "io.github.abcasada.ClaudeCodeOrganizer"


def _app_icon() -> QIcon:
    icon = QIcon.fromTheme(DESKTOP_ID)  # installed by the Linux package
    if icon.isNull():
        icon = QIcon(str(resources.files("claude_code_organizer") / "resources" / "icon.svg"))
    return icon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claude-code-organizer", description=APP_NAME)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--smoke-test", action="store_true", help="build the window, load sessions once and exit")
    args = parser.parse_args(argv)

    if sys.platform == "win32":
        # Own taskbar identity, so Windows shows our icon rather than Python's.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(DESKTOP_ID)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setDesktopFileName(DESKTOP_ID)
    app.setWindowIcon(_app_icon())

    from claude_code_organizer import single_instance
    from claude_code_organizer.window import MainWindow

    if not args.smoke_test and single_instance.notify_running_instance():
        return 0

    settings = Settings.load()
    scheme = theme.apply_theme(app, settings.theme)
    window = MainWindow(settings)
    window.model.scheme = scheme

    if args.smoke_test:
        window.refresh()
        print(f"OK: {len(window.store.sessions)} sessions")
        return 0

    server = single_instance.InstanceServer(app)
    server.activation_requested.connect(window.bring_to_front)

    def on_scheme_changed(*_) -> None:
        if settings.theme == "system":
            window.set_scheme(theme.apply_theme(app, "system"))

    if hasattr(app.styleHints(), "colorSchemeChanged"):
        app.styleHints().colorSchemeChanged.connect(on_scheme_changed)

    window.show()
    window.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
