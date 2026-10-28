# Claude Code Organizer

A small Windows app that lists every [Claude Code](https://claude.com/claude-code) session on your computer and reopens any of them with one click.

Each session has a **Resume** button. It opens a new PowerShell tab in your most recently used Windows Terminal window (or a new window if none is open), changes to the folder the session was started in, and runs `claude --resume <session-id>`.

- Sessions are listed by title, with the most recently active first. The title is your `/rename` title, Claude's auto-generated title, or else your first prompt.
- Shows which sessions are open right now in another Claude Code window, and whether they're working or idle.
- Search box filters by title, folder or session ID.
- Only one copy runs at a time. Opening it again brings the existing window to the front.
- The list refreshes itself every few seconds. It only reads the new part of each transcript, so it stays fast even with very large sessions.
- No dependencies: plain PowerShell and WinForms, both built into Windows.

## Requirements

- Windows 10 or 11
- [Claude Code](https://docs.claude.com/en/docs/claude-code/setup) installed, with `claude` on your PATH
- Windows Terminal (optional, built into Windows 11). Without it, sessions open in a new PowerShell window instead of a tab.

## Install

1. Get the code, either:
   - `git clone https://github.com/abcasada/claude-code-organizer.git`, or
   - **Code > Download ZIP** on GitHub, then extract it somewhere permanent (for example `Documents\claude-code-organizer`).
2. In that folder, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\Install.ps1
   ```

   Add `-Desktop` to also get a desktop shortcut.
3. Open **Claude Code Organizer** from the Start menu.

The installer only creates shortcuts that point at this folder. If you move the folder, run it again.

To run it without installing:

```powershell
powershell -ExecutionPolicy Bypass -File .\ClaudeCodeOrganizer.ps1
```

## Using it

| Action | How |
| --- | --- |
| Resume a session | **Resume** button, double-click the row, or select it and press **Enter** |
| Search | Type in the search box (**Ctrl+F** jumps there, **Esc** clears it) |
| Refresh now | **Refresh** button or **F5** |
| Copy the resume command, copy the session ID, or open the folder in Explorer | Right-click a session |

**Open as a tab in Windows Terminal**: untick this to open each session in its own PowerShell window instead.
**Show empty sessions**: also list sessions that were started but never got a prompt (for example, ones where you only ran `/model`).

Your choices and the window size are saved in `%APPDATA%\ClaudeCodeOrganizer\settings.json`.

## How it works

Claude Code saves each conversation as a JSON Lines transcript under `%USERPROFILE%\.claude\projects\<project-folder>\<session-id>.jsonl`. If you set `CLAUDE_CONFIG_DIR`, it uses that folder instead, and so does this app. The organizer reads those transcripts without modifying them, to find each session's title and working directory.

`claude --resume <id>` only finds a session from the folder it belongs to, so the organizer starts the new tab in that folder. When a session has been continued from more than one folder, each copy is listed with its own folder.

Open sessions are detected from the small status files Claude Code keeps in `.claude\sessions\`.

## Uninstall

```powershell
powershell -ExecutionPolicy Bypass -File .\Install.ps1 -Uninstall
```

This removes the shortcuts and saved settings. Then delete the folder. Your Claude Code sessions are never touched.

## Troubleshooting

- **"The session's folder isn't available"**: the session was started in a folder that no longer exists or is on a drive that isn't connected (for example a network or Remote Desktop drive). Reconnect it and try again.
- **The tab opens but says `claude` is not recognized**: Claude Code isn't on your PATH. Check that `claude --version` works in a new PowerShell window.
- **Sessions open in a new Terminal window instead of a tab**: no Terminal window was open. The organizer always uses your most recently used window.

## License

[MIT](LICENSE)
