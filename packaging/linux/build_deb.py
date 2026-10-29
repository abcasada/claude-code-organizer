#!/usr/bin/env python3
"""Build dist/claude-code-organizer_<version>_all.deb.

Runs on Debian/Ubuntu (needs dpkg-deb). The package uses the distribution's
PySide6 rather than bundling Qt, and installs two commands: claude-code-organizer
(the window) and ccorg (the command line).
"""

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG_SRC = ROOT / "src" / "claude_code_organizer"
DIST = ROOT / "dist"

NAME = "claude-code-organizer"
APP_ID = "io.github.abcasada.ClaudeCodeOrganizer"

# The window's Qt packages are only Recommends: apt installs them by default, and a server
# can leave them out with --no-install-recommends and use the command line (ccorg) alone.
# qt6-svg-plugins lets Qt render the SVG window icon.
CONTROL = """\
Package: {name}
Version: {version}
Architecture: all
Maintainer: Adam Casada <40399292+abcasada@users.noreply.github.com>
Installed-Size: {installed_size}
Depends: python3 (>= 3.10)
Recommends: python3-pyside6.qtcore, python3-pyside6.qtgui, python3-pyside6.qtwidgets, python3-pyside6.qtnetwork, libxcb-cursor0, qt6-svg-plugins, konsole | gnome-terminal | x-terminal-emulator
Section: devel
Priority: optional
Homepage: https://github.com/abcasada/claude-code-organizer
Description: organizer for Claude Code sessions, as a window or on the command line
 Claude Code Organizer lists your Claude Code sessions across projects and
 lets you resume, rename, group, archive and delete them, from a small Qt
 window or with the ccorg command (which also works without a desktop).
"""

# -I keeps the user's site-packages / PYTHONPATH (e.g. a pip-installed PySide6)
# and the current directory from shadowing the distribution's modules.
WRAPPERS = {
    NAME: 'exec /usr/bin/python3 -I -m claude_code_organizer "$@"\n',
    "ccorg": 'exec /usr/bin/python3 -I -m claude_code_organizer.cli "$@"\n',
}

# On Wayland Qt uses desktopFileName (== APP_ID) as the app_id. On X11 its
# WM_CLASS class is QCoreApplication.applicationName(), i.e. APP_NAME.
DESKTOP = f"""\
[Desktop Entry]
Type=Application
Name=Claude Code Organizer
Comment=List, resume, rename, archive and delete your Claude Code sessions
Exec={NAME}
Icon={APP_ID}
Terminal=false
Categories=Development;Utility;
StartupWMClass=Claude Code Organizer
"""

COPYRIGHT = """\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: {name}
Source: https://github.com/abcasada/claude-code-organizer

Files: *
Copyright: {holder}
License: Expat
{license}
"""


def read_version() -> str:
    text = (PKG_SRC / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M).group(1)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def copyright_text() -> str:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8").strip()
    holder = re.search(r"^Copyright \(c\) (.+)$", license_text, re.M).group(1)
    # Debian control-style continuation lines: indent, and "." for blank lines.
    body = "\n".join(f" {line}" if line.strip() else " ." for line in license_text.splitlines())
    return COPYRIGHT.format(name=NAME, holder=holder, license=body)


def normalize_modes(root: Path) -> None:
    """Force 755 dirs / 644 files regardless of umask; executables are fixed up after."""
    for path in [root, *root.rglob("*")]:
        path.chmod(0o755 if path.is_dir() else 0o644)


def installed_size_kib(root: Path) -> int:
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and "DEBIAN" not in p.parts)
    return -(-total // 1024)


def build(stage: Path, version: str) -> Path:
    shutil.copytree(
        PKG_SRC,
        stage / "usr/lib/python3/dist-packages/claude_code_organizer",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    icon = stage / f"usr/share/icons/hicolor/scalable/apps/{APP_ID}.svg"
    icon.parent.mkdir(parents=True)
    shutil.copyfile(PKG_SRC / "resources" / "icon.svg", icon)
    write(stage / f"usr/share/applications/{APP_ID}.desktop", DESKTOP)
    write(stage / f"usr/share/doc/{NAME}/copyright", copyright_text())
    for command, body in WRAPPERS.items():
        write(stage / f"usr/bin/{command}", "#!/bin/sh\n" + body)
    write(
        stage / "DEBIAN/control",
        CONTROL.format(name=NAME, version=version, installed_size=installed_size_kib(stage)),
    )

    normalize_modes(stage)
    for command in WRAPPERS:
        (stage / f"usr/bin/{command}").chmod(0o755)

    DIST.mkdir(exist_ok=True)
    deb = DIST / f"{NAME}_{version}_all.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(deb)], check=True)
    return deb


def main() -> None:
    version = read_version()
    with tempfile.TemporaryDirectory() as tmp:
        deb = build(Path(tmp) / "root", version)
    print(f"Built {deb}")


if __name__ == "__main__":
    main()
