<#
.SYNOPSIS
    Adds a "Claude Code Organizer" shortcut to the Start menu (and optionally the desktop).

.DESCRIPTION
    The shortcut runs ClaudeCodeOrganizer.ps1 from this folder without a console window.
    Nothing is copied anywhere: keep this folder where it is, or re-run the installer after moving it.

.PARAMETER Desktop
    Also create a shortcut on the desktop.

.PARAMETER Uninstall
    Remove the shortcuts and saved settings instead.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Install.ps1 -Desktop
#>
[CmdletBinding()]
param(
    [switch]$Desktop,
    [switch]$Uninstall
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

$name = 'Claude Code Organizer'
$shortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath('Programs')) "$name.lnk"),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) "$name.lnk")
)

if ($Uninstall) {
    foreach ($path in $shortcutPaths) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path
            Write-Host "Removed $path"
        }
    }
    $settingsDir = Join-Path $env:APPDATA 'ClaudeCodeOrganizer'
    if (Test-Path -LiteralPath $settingsDir) {
        Remove-Item -LiteralPath $settingsDir -Recurse
        Write-Host "Removed $settingsDir"
    }
    return
}

$app = Join-Path $PSScriptRoot 'ClaudeCodeOrganizer.ps1'
if (-not (Test-Path -LiteralPath $app)) { throw "Can't find $app" }

# Files downloaded as a ZIP carry the "from the internet" mark, which blocks unsigned scripts.
Get-ChildItem -LiteralPath $PSScriptRoot -Recurse -Filter '*.ps1' | Unblock-File

if (-not (Get-Command 'claude' -ErrorAction SilentlyContinue)) {
    Write-Warning "The 'claude' command isn't on your PATH. Install Claude Code first, or resuming sessions will fail."
}

$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
$psArgs = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$app`""

$claude = Get-Command 'claude.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
$icon = if ($claude) { "$($claude.Source),0" } else { "$powershell,0" }

$targets = @($shortcutPaths[0])
if ($Desktop) { $targets += $shortcutPaths[1] }

$shell = New-Object -ComObject WScript.Shell
foreach ($path in $targets) {
    $shortcut = $shell.CreateShortcut($path)
    if (Test-Path -LiteralPath $conhost) {
        # A headless console host keeps Windows Terminal (the default console on Windows 11)
        # from opening an empty window behind the app.
        $shortcut.TargetPath = $conhost
        $shortcut.Arguments = "--headless `"$powershell`" $psArgs"
    } else {
        $shortcut.TargetPath = $powershell
        $shortcut.Arguments = $psArgs
    }
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.IconLocation = $icon
    $shortcut.Description = 'List and resume your Claude Code sessions'
    $shortcut.Save()
    Write-Host "Created $path"
}

Write-Host "`nDone. Open '$name' from the Start menu."
