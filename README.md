# Claude Code Organizer

A small desktop app for Windows and Linux that lists every [Claude Code](https://claude.com/claude-code) session on your computer, so you can reopen, rename, archive or delete one with a click.

**Resume** opens the session in a new terminal tab, in the folder it was started in, running `claude --resume <session-id>`. On Windows that's a tab in Windows Terminal; on Linux, Konsole, GNOME Terminal or Ptyxis, or another terminal if those aren't installed.

- Sessions show their `/rename` title, Claude's automatic title, or else your first prompt. The most recently active session is listed first.
- Shows which sessions are open right now, and whether they're working, idle or waiting for you.
- Search by title, folder or session ID. Optionally group the list by folder.
- **Rename** works the same way as Claude Code's `/rename`, so the new name also shows in `claude --resume`.
- **Archive** moves a session out of Claude Code's folder. Claude Code automatically deletes sessions older than 30 days ([`cleanupPeriodDays`](https://docs.claude.com/en/docs/claude-code/settings)), so archiving keeps the ones you want. Resuming an archived session restores it first.
- **Delete** moves a session to the Recycle Bin or Trash, so you can undo it.
- Light, dark, or follow-the-system theme.

## Install

### Windows 10/11

1. Download `ClaudeCodeOrganizer.exe` from the [latest release](https://github.com/abcasada/claude-code-organizer/releases/latest) and put it somewhere permanent, for example `Documents\Apps`.
2. Run it. Windows may warn that the app is from an unknown publisher, because it isn't code-signed: choose **More info > Run anyway**.
3. Optional: **File > Add to Start Menu**.

### Ubuntu / Debian (KDE, GNOME and others)

Download the `.deb` from the [latest release](https://github.com/abcasada/claude-code-organizer/releases/latest), then:

```bash
sudo apt install ./claude-code-organizer_*_all.deb
```

It uses your distribution's Qt for Python packages (Ubuntu 26.04 or newer). Open **Claude Code Organizer** from your app menu.

### From source (any platform with Python 3.10+)

```bash
pip install git+https://github.com/abcasada/claude-code-organizer.git
claude-code-organizer
```

You also need [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) itself, with `claude` on your PATH.

## Using it

| Action | How |
| --- | --- |
| Resume | **Resume** button, double-click, or **Enter** |
| Rename | **F2**, or right-click > Rename |
| Archive or restore | **Ctrl+E**, or right-click |
| Delete (to the Recycle Bin or Trash) | **Delete**, or right-click |
| Search | Type in the search box (**Ctrl+F**) |
| Group by folder | **Ctrl+G** |
| Show archived sessions | View menu, or the toolbar button |
| Copy the resume command or session ID, open the folder | Right-click |

You can select several sessions to archive, restore or delete them together. Renaming, archiving and deleting are refused while a session is open in Claude Code; close it first (or use `/rename` inside it).

**Settings** (File menu) has the theme, whether sessions open as tabs in an existing terminal window, and on Linux which terminal to use.

## Where things are stored

| What | Windows | Linux |
| --- | --- | --- |
| Claude Code's sessions (read, and changed only when you rename, archive or delete) | `%USERPROFILE%\.claude` | `~/.claude` |
| Archived sessions | `%APPDATA%\ClaudeCodeOrganizer\archive` | `~/.local/share/claude-code-organizer/archive` |
| Settings | `%APPDATA%\ClaudeCodeOrganizer\settings.json` | `~/.config/claude-code-organizer/settings.json` |

If you set `CLAUDE_CONFIG_DIR`, the organizer uses that folder instead of `.claude`.

A session's files are its transcript (`projects/<folder>/<id>.jsonl`), its folder of subagent transcripts and tool output, and its `file-history` and `session-env` entries. Archive and delete act on exactly those. They never touch the project's `memory` folder or your prompt history.

## How it works

Claude Code saves each conversation as a JSON Lines transcript. The organizer reads only the parts it needs (titles, the working folder, the first prompt) and only what's new since the last refresh, so it stays fast with very large sessions. Open sessions are detected from the status files Claude Code keeps in `.claude/sessions`.

`claude --resume <id>` only finds a session from the folder it belongs to, so the organizer starts the terminal there. A resumed session gets your normal login environment, even if the organizer itself was started from inside Claude Code.

## Troubleshooting

- **"Folder missing"**: the session was started in a folder that has since been moved or deleted, or is on a drive that isn't connected. Resume is disabled until the folder is back.
- **The tab opens but `claude` isn't found**: make sure `claude --version` works in a new terminal. On Linux the session starts in a login shell, so PATH changes in `~/.profile` or `~/.bashrc` apply.
- **On Linux nothing opens**: choose a terminal explicitly in **File > Settings**.

## Development

```bash
python -m venv .venv
.venv/bin/pip install -e .[dev]      # Windows: .venv\Scripts\pip
.venv/bin/pytest
.venv/bin/python -m claude_code_organizer
```

Packaging and releases are described in [packaging/README.md](packaging/README.md).

## License

[MIT](LICENSE). Not affiliated with Anthropic.
