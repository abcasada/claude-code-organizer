"""Build dist/ClaudeCodeOrganizer.exe, a single-file windowed exe, with PyInstaller.

Needs PySide6 and PyInstaller in the running interpreter:
    pip install -e . pyinstaller
"""

import re
import struct
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QImage, QImageReader

ROOT = Path(__file__).resolve().parents[2]
PKG_SRC = ROOT / "src" / "claude_code_organizer"
WORK = ROOT / "build" / "pyinstaller"
NAME = "ClaudeCodeOrganizer"
ICO_SIZES = (256, 64, 48, 32, 16)

# Modules PySide6-Essentials ships that the app never imports.
EXCLUDES = (
    "tkinter",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtOpenGL",
    "PySide6.QtPdf",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtXml",
)

VERSION_INFO = """\
VSVersionInfo(
  ffi=FixedFileInfo(filevers={ver}, prodvers={ver}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Adam Casada'),
      StringStruct('FileDescription', 'Claude Code Organizer'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{name}'),
      StringStruct('LegalCopyright', 'Copyright (c) Adam Casada. MIT License.'),
      StringStruct('OriginalFilename', '{name}.exe'),
      StringStruct('ProductName', 'Claude Code Organizer'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def read_version() -> str:
    text = (PKG_SRC / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M).group(1)


def render_png(svg: Path, size: int) -> bytes:
    reader = QImageReader(str(svg))
    reader.setScaledSize(QSize(size, size))
    image = reader.read()
    if image.isNull():
        sys.exit(f"Cannot render {svg}: {reader.errorString()}")
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.convertToFormat(QImage.Format.Format_ARGB32).save(buffer, "PNG")
    return bytes(data)


def write_ico(svg: Path, dest: Path) -> None:
    # Qt's ico writer stores only one image per file, so assemble a multi-size
    # .ico from PNG-compressed entries (supported since Windows Vista).
    images = [render_png(svg, size) for size in ICO_SIZES]
    offset = 6 + 16 * len(images)
    header = struct.pack("<HHH", 0, 1, len(images))
    entries = b""
    for size, png in zip(ICO_SIZES, images):
        dim = size % 256  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    dest.write_bytes(header + entries + b"".join(images))


def write_version_info(version: str, dest: Path) -> None:
    parts = [int(n) for n in re.findall(r"\d+", version)[:3]]
    ver = tuple(parts + [0] * (4 - len(parts)))
    dest.write_text(VERSION_INFO.format(ver=ver, version=version, name=NAME), encoding="utf-8")


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    icon = WORK / "icon.ico"
    version_file = WORK / "version_info.txt"
    write_ico(PKG_SRC / "resources" / "icon.svg", icon)
    write_version_info(read_version(), version_file)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--paths", str(ROOT / "src"),
        "--add-data", f"{PKG_SRC / 'resources'};claude_code_organizer/resources",
        "--icon", str(icon),
        "--version-file", str(version_file),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(WORK),
        "--specpath", str(WORK),
    ]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    args.append(str(Path(__file__).with_name("launcher.py")))
    subprocess.run(args, check=True)


if __name__ == "__main__":
    main()
