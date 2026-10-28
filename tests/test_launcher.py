import sys

import pytest

from claude_code_organizer import launcher
from claude_code_organizer.launcher import LaunchError

SID = "39e8a3f5-9a2c-44e3-b3eb-56aa56fe311c"


def test_claude_command():
    assert launcher.claude_command(SID) == ["claude", "--resume", SID]
    assert launcher.claude_command(SID, job_id="job_1") == ["claude", "attach", "job_1"]
    with pytest.raises(LaunchError):
        launcher.claude_command("..")
    with pytest.raises(LaunchError):
        launcher.claude_command(SID, job_id="x; rm -rf ~")


def test_wt_quoting():
    assert launcher._wt_quote("plain") == "plain"
    assert launcher._wt_quote("a;b") == "a\\;b"
    assert launcher._wt_quote(r"C:\Program Files\x") == r'"C:\Program Files\x"'
    assert launcher._wt_quote("D:\\with space\\") == '"D:\\with space\\\\"'


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launch command")
def test_windows_tab_command(monkeypatch):
    monkeypatch.setattr(launcher, "find_windows_terminal", lambda: r"C:\wt.exe")
    spec = launcher.build_spec(launcher.claude_command(SID), r"C:\my dir", 'Title; "x"', use_tabs=True)
    assert spec.args.startswith(r"C:\wt.exe -w 0 new-tab --title ")
    assert r'--startingDirectory "C:\my dir"' in spec.args
    assert spec.args.endswith(f"-NoExit -ExecutionPolicy Bypass -Command claude --resume {SID}")
    assert "CLAUDECODE" not in spec.env


def test_linux_konsole_command(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/usr/bin/{name}" if name == "konsole" else None)
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "KDE")
    spec = launcher._linux_spec(launcher.claude_command(SID), "/home/me/my proj", "t", True, "auto")
    assert spec.args[:5] == ["konsole", "--new-tab", "--workdir", "/home/me/my proj", "-e"]
    assert spec.args[5:9] == ["/bin/zsh", "-l", "-i", "-c"]
    assert spec.args[9] == f"claude --resume {SID}; exec /bin/zsh -l -i"


def test_linux_reports_missing_terminal(monkeypatch):
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    with pytest.raises(LaunchError, match="No terminal"):
        launcher._linux_spec(["claude"], "/tmp", "t", True, "auto")


def test_clean_posix_environment_drops_claude_variables():
    env = launcher.clean_posix_environment({
        "CLAUDECODE": "1", "CLAUDE_CODE_CHILD_SESSION": "1", "NO_COLOR": "1", "PATH": "/bin",
        "LD_LIBRARY_PATH": "/bundle", "LD_LIBRARY_PATH_ORIG": "/usr/lib",
    })
    assert env == {"PATH": "/bin", "LD_LIBRARY_PATH": "/usr/lib"}
    # Outside Claude Code, the user's own settings are left alone.
    assert launcher.clean_posix_environment({"NO_COLOR": "1"}) == {"NO_COLOR": "1"}
