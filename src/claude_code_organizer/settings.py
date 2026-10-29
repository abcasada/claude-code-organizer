"""User preferences, stored as JSON in the organizer's config folder."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

from claude_code_organizer import paths

THEMES = {"system": "Follow system", "light": "Light", "dark": "Dark"}
GROUP_BY = ("none", "folder", "group")


@dataclass
class Settings:
    theme: str = "system"
    use_tabs: bool = True            # open sessions as tabs of an existing terminal window
    linux_terminal: str = "auto"
    group_by: str = "none"           # one of GROUP_BY
    show_archived: bool = False
    show_hidden: bool = False        # sessions with no prompt, and `claude -p` / SDK runs
    show_desktop_app: bool = False   # sessions only ever used in the Claude desktop app
    window_geometry: str = ""        # base64 QByteArray
    header_state: str = ""

    @classmethod
    def load(cls) -> Settings:
        settings = cls()
        try:
            saved = json.loads((paths.app_config_dir() / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return settings  # missing or corrupt: defaults
        if isinstance(saved, dict):
            for f in fields(cls):
                value = saved.get(f.name)
                if isinstance(value, type(getattr(settings, f.name))):
                    setattr(settings, f.name, value)
            if "group_by" not in saved and saved.get("group_by_folder") is True:  # 1.0.0 setting
                settings.group_by = "folder"
        if settings.theme not in THEMES:
            settings.theme = "system"
        if settings.group_by not in GROUP_BY:
            settings.group_by = "none"
        return settings

    def save(self) -> None:
        folder = paths.app_config_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "settings.json").write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError:
            pass  # preferences are a convenience; never fail over them
