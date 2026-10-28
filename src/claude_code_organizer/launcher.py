"""Opens a terminal running `claude --resume <id>` (or `claude attach <job>`) in a session's folder."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

from claude_code_organizer import paths

_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Linux terminals in the order "Auto" tries them (after the desktop's own terminal).
LINUX_TERMINALS = ["konsole", "ptyxis", "gnome-terminal", "xfce4-terminal", "kitty", "alacritty",
                   "wezterm", "x-terminal-emulator", "xterm"]
_TABBED_TERMINALS = {"konsole", "ptyxis", "gnome-terminal", "xfce4-terminal"}


class LaunchError(Exception):
    pass


@dataclass
class LaunchSpec:
    args: list[str] | str    # a string is a raw Windows command line
    cwd: str
    env: dict[str, str]
    creationflags: int = 0
    new_session: bool = False


def claude_command(session_id: str, job_id: str | None = None) -> list[str]:
    """The claude invocation for a session. Background sessions are attached rather than resumed,
    since resuming one would start a second process writing the same transcript."""
    if job_id:
        if not _JOB_ID_RE.match(job_id):
            raise LaunchError(f"Unexpected background job id: {job_id}")
        return ["claude", "attach", job_id]
    if not paths.is_session_id(session_id):
        raise LaunchError(f"Unexpected session id: {session_id}")
    return ["claude", "--resume", session_id]


def build_spec(command: list[str], cwd: str, title: str, use_tabs: bool, terminal: str = "auto") -> LaunchSpec:
    if sys.platform == "win32":
        return _windows_spec(command, cwd, title, use_tabs)
    return _linux_spec(command, cwd, title, use_tabs, terminal)


def launch(spec: LaunchSpec) -> None:
    try:
        subprocess.Popen(
            spec.args,
            cwd=spec.cwd,
            env=spec.env,
            creationflags=spec.creationflags,
            start_new_session=spec.new_session,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError as e:
        raise LaunchError(str(e)) from e


# --- Windows ---------------------------------------------------------------------------------------


def find_windows_terminal() -> str | None:
    return shutil.which("wt.exe")


def _windows_shell() -> str:
    # Prefer PowerShell 7 when installed.
    return shutil.which("pwsh.exe") or shutil.which("powershell.exe") or "powershell.exe"


def _wt_quote(value: str) -> str:
    # wt.exe splits its command line on ';' even inside quotes, so escape it.
    value = value.replace(";", "\\;")
    if not re.search(r'[\s"]', value):
        return value
    if value.endswith("\\"):
        value += "\\"  # a trailing backslash would escape the closing quote
    return '"' + value.replace('"', '\\"') + '"'


def _windows_spec(command: list[str], cwd: str, title: str, use_tabs: bool) -> LaunchSpec:
    if '"' in cwd:
        raise LaunchError(f"Unexpected folder name: {cwd}")
    shell = _windows_shell()
    # -ExecutionPolicy Bypass: an npm-installed claude is a .ps1 shim, which a Restricted policy blocks.
    shell_args = f"{_wt_quote(shell)} -NoExit -ExecutionPolicy Bypass -Command {' '.join(command)}"
    env = windows_login_environment() or dict(os.environ)

    wt = find_windows_terminal() if use_tabs else None
    if wt:
        safe_title = " ".join(re.sub(r'[";]', " ", title).split())[:40].strip() or "Claude"
        # -w 0 targets the most recently used Terminal window, or opens one if none is open.
        args = (f"{_wt_quote(wt)} -w 0 new-tab --title {_wt_quote(safe_title)} "
                f"--startingDirectory {_wt_quote(cwd)} {shell_args}")
        return LaunchSpec(args, cwd, env)
    return LaunchSpec(shell_args, cwd, env, creationflags=subprocess.CREATE_NEW_CONSOLE)


def windows_login_environment() -> dict[str, str] | None:
    """The environment a freshly started program gets from Windows (system and user variables).

    Windows Terminal hands the caller's environment to the new tab, so without this a session
    resumed from an organizer that was itself started inside Claude Code would inherit variables
    like CLAUDE_CODE_CHILD_SESSION (which turns off transcript saving) and NO_COLOR.
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    userenv.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.BOOL]
    userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008 | 0x0002, ctypes.byref(token)):
        return None
    try:
        block = ctypes.c_void_p()
        if not userenv.CreateEnvironmentBlock(ctypes.byref(block), token, False):
            return None
        try:
            # A sequence of "NAME=value\0" strings ending with an empty one.
            env: dict[str, str] = {}
            address = block.value
            while True:
                entry = ctypes.wstring_at(address)
                if not entry:
                    break
                name, sep, value = entry.partition("=") if not entry.startswith("=") else ("", "", "")
                if sep and name:
                    env[name] = value
                address += (len(entry) + 1) * ctypes.sizeof(ctypes.c_wchar)
            return env
        finally:
            userenv.DestroyEnvironmentBlock(block)
    finally:
        kernel32.CloseHandle(token)


# --- Linux -----------------------------------------------------------------------------------------

# Variables Claude Code sets for the commands it runs. If the organizer was started from inside a
# Claude Code session they would leak into the resumed one (turning off transcript saving, colors
# and the git editor), so drop them. The login shell then re-applies anything the user set themselves.
_LEAKED_PREFIXES = ("CLAUDE_CODE_", "CLAUDE_AGENT_SDK_")
_LEAKED_NAMES = {"CLAUDECODE", "CLAUDE_PID", "AI_AGENT", "NO_COLOR", "GIT_EDITOR", "GIT_TERMINAL_PROMPT",
                 "GCM_INTERACTIVE", "GIT_ASKPASS", "DISABLE_AUTOUPDATER", "PYTHONDONTWRITEBYTECODE"}


def clean_posix_environment(environ: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if environ is None else environ)
    if "CLAUDECODE" in env:
        for name in list(env):
            if name in _LEAKED_NAMES or name.startswith(_LEAKED_PREFIXES):
                del env[name]
    # A bundled build (PyInstaller, AppImage) points LD_LIBRARY_PATH at its own Qt; the terminal
    # must not load those libraries.
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    elif getattr(sys, "frozen", False):
        env.pop("LD_LIBRARY_PATH", None)
    return env


def available_linux_terminals() -> list[str]:
    return [name for name in LINUX_TERMINALS if shutil.which(name)]


def _pick_linux_terminal(preferred: str) -> str:
    if preferred != "auto":
        if shutil.which(preferred):
            return preferred
        raise LaunchError(f"The terminal '{preferred}' isn't installed. Choose another one in Settings.")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
    order = list(LINUX_TERMINALS)
    if "KDE" in desktop:
        order.insert(0, "konsole")
    elif "GNOME" in desktop or "UNITY" in desktop:
        order[:0] = ["ptyxis", "gnome-terminal"]
    for name in order:
        if shutil.which(name):
            return name
    raise LaunchError("No terminal program was found. Install one (for example konsole or gnome-terminal).")


def _linux_spec(command: list[str], cwd: str, title: str, use_tabs: bool, preferred: str) -> LaunchSpec:
    terminal = _pick_linux_terminal(preferred)
    shell = os.environ.get("SHELL") or "/bin/bash"
    # A login + interactive shell picks up the PATH from the user's profile (nvm, ~/.local/bin...),
    # which a program started from the desktop may not have. Afterwards, stay in a shell.
    inner = f"{shlex.join(command)}; exec {shlex.quote(shell)} -l -i"
    run = [shell, "-l", "-i", "-c", inner]
    tabs = use_tabs and terminal in _TABBED_TERMINALS

    if terminal == "konsole":
        args = ["konsole"] + (["--new-tab"] if tabs else []) + ["--workdir", cwd, "-e", *run]
    elif terminal == "ptyxis":
        args = ["ptyxis", "--tab" if tabs else "--new-window", "-d", cwd, "--", *run]
    elif terminal == "gnome-terminal":
        args = ["gnome-terminal", "--tab" if tabs else "--window", f"--working-directory={cwd}", "--", *run]
    elif terminal == "xfce4-terminal":
        args = ["xfce4-terminal"] + (["--tab"] if tabs else []) + [f"--working-directory={cwd}", "-T", title, "-x", *run]
    elif terminal == "kitty":
        args = ["kitty", "--directory", cwd, "--title", title, *run]
    elif terminal == "alacritty":
        args = ["alacritty", "--working-directory", cwd, "--title", title, "-e", *run]
    elif terminal == "wezterm":
        args = ["wezterm", "start", "--cwd", cwd, "--", *run]
    else:  # x-terminal-emulator, xterm
        args = [terminal, "-e", *run]
    return LaunchSpec(args, cwd, clean_posix_environment(), new_session=True)
