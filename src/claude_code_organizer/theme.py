"""Light and dark palettes on Qt's Fusion style, which looks the same on every platform."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

ACCENT = "#C96442"

_LIGHT = {
    "window": "#F4F3EF", "base": "#FFFFFF", "alt": "#F8F7F4", "text": "#1F1E1C", "button": "#ECEAE4",
    "light": "#FFFFFF", "midlight": "#F1EFEA", "mid": "#C9C6BE", "dark": "#A7A49C", "shadow": "#6E6B64",
    "disabled": "#A29F98", "placeholder": "#8C8982", "link": "#A9512F", "tooltip": "#FFFFFF",
}
_DARK = {
    "window": "#202124", "base": "#18191B", "alt": "#1D1E21", "text": "#E9E7E2", "button": "#2C2D30",
    "light": "#3B3C40", "midlight": "#333438", "mid": "#28292C", "dark": "#141517", "shadow": "#000000",
    "disabled": "#6E6E6E", "placeholder": "#8A8A8A", "link": "#E59372", "tooltip": "#2C2D30",
}

# Muted text and status colors the views use, per scheme.
MUTED = {"light": "#6F6C66", "dark": "#9C9A95"}
STATUS_COLORS = {
    "light": {"working": "#B4532F", "waiting": "#B7791F", "idle": "#2F7D4F", "background": "#3B6EA8"},
    "dark": {"working": "#E59372", "waiting": "#E5B567", "idle": "#6CC28E", "background": "#7FA8DA"},
}


def effective_scheme(theme: str) -> str:
    if theme in ("light", "dark"):
        return theme
    hints = QApplication.styleHints()
    if hasattr(hints, "colorScheme") and hints.colorScheme() == Qt.ColorScheme.Dark:
        return "dark"
    return "light"


def apply_theme(app: QApplication, theme: str) -> str:
    """Applies the theme and returns the scheme actually in use ("light" or "dark")."""
    app.setStyle("Fusion")
    hints = app.styleHints()
    # Qt 6.8+: also switches native parts such as the Windows title bar.
    if hasattr(hints, "setColorScheme"):
        if theme == "system":
            hints.unsetColorScheme()
        else:
            hints.setColorScheme(Qt.ColorScheme.Dark if theme == "dark" else Qt.ColorScheme.Light)

    scheme = effective_scheme(theme)
    app.setPalette(_palette(_DARK if scheme == "dark" else _LIGHT))
    return scheme


def _palette(c: dict[str, str]) -> QPalette:
    p = QPalette()
    roles = {
        QPalette.Window: c["window"], QPalette.WindowText: c["text"], QPalette.Base: c["base"],
        QPalette.AlternateBase: c["alt"], QPalette.Text: c["text"], QPalette.Button: c["button"],
        QPalette.ButtonText: c["text"], QPalette.BrightText: "#FFFFFF", QPalette.Light: c["light"],
        QPalette.Midlight: c["midlight"], QPalette.Mid: c["mid"], QPalette.Dark: c["dark"],
        QPalette.Shadow: c["shadow"], QPalette.Highlight: ACCENT, QPalette.HighlightedText: "#FFFFFF",
        QPalette.Link: c["link"], QPalette.LinkVisited: c["link"], QPalette.ToolTipBase: c["tooltip"],
        QPalette.ToolTipText: c["text"], QPalette.PlaceholderText: c["placeholder"],
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(c["disabled"]))
    return p
