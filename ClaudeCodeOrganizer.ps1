<#
.SYNOPSIS
    Lists the Claude Code sessions on this computer and resumes any of them in a new PowerShell tab.

.DESCRIPTION
    Reads the session transcripts Claude Code stores under ~/.claude/projects (or $env:CLAUDE_CONFIG_DIR),
    shows them in a searchable list that refreshes itself, and resumes a session with
    `claude --resume <id>` in its original working directory - as a new tab in your most recently used
    Windows Terminal window when Terminal is installed, otherwise in a new PowerShell window.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version 3.0
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'src\SessionStore.ps1')

Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type -Namespace ClaudeCodeOrganizer -Name Native -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool SetProcessDPIAware();
[System.Runtime.InteropServices.DllImport("user32.dll")]
public static extern bool AllowSetForegroundWindow(int processId);
'@

# Single instance: if the organizer is already running, ask it to come to the front and exit.
# Local\ names are per logon session, so each Windows user gets their own instance.
$createdNew = $false
$script:InstanceMutex = New-Object System.Threading.Mutex($true, 'Local\ClaudeCodeOrganizer', [ref]$createdNew)
$script:ActivateEvent = New-Object System.Threading.EventWaitHandle($false, 'AutoReset', 'Local\ClaudeCodeOrganizer.Activate')
if (-not $createdNew) {
    # We were just launched by the user, so we may hand our right to take the foreground to the running copy.
    [void][ClaudeCodeOrganizer.Native]::AllowSetForegroundWindow(-1)  # ASFW_ANY
    [void]$script:ActivateEvent.Set()
    return
}

$script:AppName = 'Claude Code Organizer'
$script:RefreshIntervalMs = 2000
$script:SettingsPath = Join-Path $env:APPDATA 'ClaudeCodeOrganizer\settings.json'
$script:WindowsTerminal = Find-WindowsTerminal
$script:Records = @{}      # transcript path -> session record
$script:OpenSessions = @{}

#region Settings

function Read-Settings {
    $settings = [pscustomobject]@{ UseWindowsTerminal = $true; ShowEmpty = $false; Width = 1150; Height = 620 }
    try {
        if (Test-Path -LiteralPath $script:SettingsPath) {
            $saved = Get-Content -LiteralPath $script:SettingsPath -Raw | ConvertFrom-Json
            foreach ($p in $saved.PSObject.Properties) {
                if ($settings.PSObject.Properties.Name -contains $p.Name) { $settings.($p.Name) = $p.Value }
            }
        }
    } catch { }  # A corrupt settings file just means defaults.
    return $settings
}

function Save-Settings {
    try {
        $dir = Split-Path -Parent $script:SettingsPath
        if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $bounds = if ($form.WindowState -eq 'Normal') { $form.Bounds } else { $form.RestoreBounds }
        [pscustomobject]@{
            UseWindowsTerminal = $wtCheck.Checked
            ShowEmpty          = $emptyCheck.Checked
            Width              = $bounds.Width
            Height             = $bounds.Height
        } | ConvertTo-Json | Set-Content -LiteralPath $script:SettingsPath -Encoding UTF8
    } catch { }
}

#endregion

#region Data

$table = New-Object System.Data.DataTable
foreach ($col in @(
        @('Path', [string]), @('Title', [string]), @('Project', [string]), @('LastActive', [datetime]),
        @('Status', [string]), @('SessionId', [string]), @('HasContent', [bool]))) {
    [void]$table.Columns.Add($col[0], $col[1])
}
$table.PrimaryKey = @($table.Columns['Path'])
$view = New-Object System.Data.DataView($table)
$view.Sort = 'LastActive DESC'

function Format-Status {
    param([string]$Status)
    switch ($Status) {
        ''      { return '' }
        'busy'  { return 'Open - working' }
        'idle'  { return 'Open - idle' }
        default { return 'Open' }
    }
}

function Set-RowValue {
    # Only touch cells that changed, so periodic refreshes don't disturb sorting or selection.
    param([System.Data.DataRow]$Row, [string]$Column, $Value)
    if ($null -eq $Value) { $Value = [DBNull]::Value }
    if (-not $Row[$Column].Equals($Value)) { $Row[$Column] = $Value }
}

function Clear-GridSelection {
    $grid.CurrentCell = $null
    $grid.ClearSelection()
}

function Update-Sessions {
    # Adding or re-sorting rows can make the grid pick a current row on its own; don't let a refresh
    # select something when nothing was selected.
    $hadSelection = $grid.SelectedRows.Count -gt 0
    $seen =New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($file in @(Get-SessionFiles)) {
        [void]$seen.Add($file.FullName)
        $record = $script:Records[$file.FullName]
        if (-not $record) {
            $record = New-SessionRecord -File $file
            $script:Records[$file.FullName] = $record
        }
        try { [void](Update-SessionRecord -Record $record) } catch { }  # Locked or vanished mid-read; retry next tick.
    }
    $script:OpenSessions = Get-OpenSessions

    foreach ($path in @($script:Records.Keys)) {
        if (-not $seen.Contains($path)) {
            $script:Records.Remove($path)
            $row = $table.Rows.Find($path)
            if ($row) { $table.Rows.Remove($row) }
            continue
        }

        $record = $script:Records[$path]
        $row = $table.Rows.Find($path)
        if (-not $row) {
            $row = $table.NewRow()
            $row['Path'] = $path
            $isNew = $true
        } else {
            $isNew = $false
        }
        $status = if ($record.Cwd) { $script:OpenSessions[(Get-OpenSessionKey $record.SessionId $record.Cwd)] } else { $null }

        Set-RowValue $row 'Title' (Get-SessionTitle $record)
        Set-RowValue $row 'Project' $record.Cwd
        Set-RowValue $row 'LastActive' $record.LastWrite.ToLocalTime()
        Set-RowValue $row 'Status' (Format-Status ([string]$status))
        Set-RowValue $row 'SessionId' $record.SessionId
        Set-RowValue $row 'HasContent' (Test-SessionHasContent $record)
        if ($isNew) { $table.Rows.Add($row) }
    }
    if (-not $hadSelection) { Clear-GridSelection }
    Update-StatusText
}

function Update-Filter {
    $hadSelection = $grid.SelectedRows.Count -gt 0
    $clauses = @()
    if (-not $emptyCheck.Checked) { $clauses += 'HasContent = true' }

    $search = $searchBox.Text.Trim()
    if ($search) {
        # Escape the characters that are special inside a DataView LIKE pattern.
        $escaped = ($search -replace '([\[\]*%])', '[$1]').Replace("'", "''")
        $clauses += "(Title LIKE '%$escaped%' OR Project LIKE '%$escaped%' OR SessionId LIKE '%$escaped%')"
    }
    $view.RowFilter = $clauses -join ' AND '
    if (-not $hadSelection) { Clear-GridSelection }
    Update-StatusText
}

function Update-StatusText {
    $openCount = @($table.Select("Status <> ''")).Count
    $statusLabel.Text = '{0} sessions shown  |  {1} open  |  last refreshed {2:t}' -f $view.Count, $openCount, (Get-Date)
}

#endregion

#region Actions

function Get-SelectedRecord {
    $row = $grid.CurrentRow
    if (-not $row -or $row.Index -lt 0) { return $null }
    $path = [string]$row.DataBoundItem.Row['Path']
    return $script:Records[$path]
}

function Show-Message {
    param([string]$Text, [System.Windows.Forms.MessageBoxIcon]$Icon = 'Warning')
    [void][System.Windows.Forms.MessageBox]::Show($form, $Text, $script:AppName, 'OK', $Icon)
}

function Invoke-Resume {
    param($Record)
    if (-not $Record) { return }

    if (-not $Record.Cwd) {
        Show-Message "Can't tell which folder this session was started in, so it can't be resumed.`n`n$($Record.Path)"
        return
    }
    if (-not (Test-Path -LiteralPath $Record.Cwd -PathType Container)) {
        Show-Message "The session's folder isn't available:`n`n$($Record.Cwd)`n`nClaude Code resumes a session from the folder it was started in. Reconnect the drive or restore the folder and try again."
        return
    }
    # Re-check now rather than trusting the last refresh: the session may have been closed a moment ago.
    Update-Sessions
    if ($script:OpenSessions.ContainsKey((Get-OpenSessionKey $Record.SessionId $Record.Cwd))) {
        $answer = [System.Windows.Forms.MessageBox]::Show($form,
            "This session is already open in another Claude Code window.`n`nRunning it in two places at once makes the conversations diverge. Open another copy anyway?",
            $script:AppName, 'YesNo', 'Question', 'Button2')
        if ($answer -ne 'Yes') { return }
    }

    $terminal = if ($wtCheck.Checked) { $script:WindowsTerminal } else { $null }
    try {
        $spec = Get-ResumeLaunchSpec -SessionId $Record.SessionId -WorkingDirectory $Record.Cwd `
            -Title (Get-SessionTitle $Record) -WindowsTerminal $terminal
        Start-ResumeSession -Spec $spec
    } catch {
        Show-Message "Couldn't start the session:`n`n$($_.Exception.Message)" 'Error'
    }
}

function Copy-ResumeCommand {
    param($Record)
    if (-not $Record) { return }
    $command = "claude --resume $($Record.SessionId)"
    if ($Record.Cwd) { $command = "Set-Location -LiteralPath '$($Record.Cwd.Replace("'", "''"))'; $command" }
    [System.Windows.Forms.Clipboard]::SetText($command)
}

#endregion

#region UI

[void][ClaudeCodeOrganizer.Native]::SetProcessDPIAware()
[System.Windows.Forms.Application]::EnableVisualStyles()
$settings = Read-Settings

$form = New-Object System.Windows.Forms.Form
$form.Text = $script:AppName
$form.AutoScaleDimensions = New-Object System.Drawing.SizeF(96, 96)
$form.AutoScaleMode = 'Dpi'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 9)
$form.StartPosition = 'CenterScreen'
$form.Size = New-Object System.Drawing.Size([int]$settings.Width, [int]$settings.Height)
$form.MinimumSize = New-Object System.Drawing.Size(700, 300)
$form.KeyPreview = $true
# The form auto-scales its controls, but DataGridView column widths and row heights are raw pixels.
$graphics = $form.CreateGraphics()
$script:DpiScale = $graphics.DpiX / 96
$graphics.Dispose()
function ConvertTo-ScaledPixels([int]$Pixels) { [int][Math]::Round($Pixels * $script:DpiScale) }

$claudeExe = Get-Command 'claude.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($claudeExe) {
    try { $form.Icon = [System.Drawing.Icon]::ExtractAssociatedIcon($claudeExe.Source) } catch { }
}

$toolbar = New-Object System.Windows.Forms.FlowLayoutPanel
$toolbar.Dock = 'Top'
$toolbar.AutoSize = $true
$toolbar.WrapContents = $false
$toolbar.Padding = New-Object System.Windows.Forms.Padding(6)

$searchLabel = New-Object System.Windows.Forms.Label
$searchLabel.Text = 'Search:'
$searchLabel.AutoSize = $true
$searchLabel.Margin = New-Object System.Windows.Forms.Padding(3, 7, 3, 3)

$searchBox = New-Object System.Windows.Forms.TextBox
$searchBox.Width = 280

$refreshButton = New-Object System.Windows.Forms.Button
$refreshButton.Text = 'Refresh'
$refreshButton.AutoSize = $true

$wtCheck = New-Object System.Windows.Forms.CheckBox
$wtCheck.AutoSize = $true
$wtCheck.Margin = New-Object System.Windows.Forms.Padding(16, 6, 3, 3)
if ($script:WindowsTerminal) {
    $wtCheck.Text = 'Open as a tab in Windows Terminal'
    $wtCheck.Checked = [bool]$settings.UseWindowsTerminal
} else {
    $wtCheck.Text = 'Open as a tab in Windows Terminal (not installed)'
    $wtCheck.Enabled = $false
}

$emptyCheck = New-Object System.Windows.Forms.CheckBox
$emptyCheck.Text = 'Show empty sessions'
$emptyCheck.AutoSize = $true
$emptyCheck.Margin = New-Object System.Windows.Forms.Padding(16, 6, 3, 3)
$emptyCheck.Checked = [bool]$settings.ShowEmpty

$toolbar.Controls.AddRange(@($searchLabel, $searchBox, $refreshButton, $wtCheck, $emptyCheck))

$grid = New-Object System.Windows.Forms.DataGridView
$grid.Dock = 'Fill'
$grid.ReadOnly = $true
$grid.AllowUserToAddRows = $false
$grid.AllowUserToDeleteRows = $false
$grid.AllowUserToResizeRows = $false
$grid.MultiSelect = $false
$grid.SelectionMode = 'FullRowSelect'
$grid.RowHeadersVisible = $false
$grid.AutoGenerateColumns = $false
$grid.BackgroundColor = [System.Drawing.SystemColors]::Window
$grid.BorderStyle = 'None'
$grid.CellBorderStyle = 'SingleHorizontal'
$grid.ColumnHeadersHeightSizeMode = 'AutoSize'
$grid.ShowCellToolTips = $true
$grid.RowTemplate.Height = ConvertTo-ScaledPixels 28

$resumeColumn = New-Object System.Windows.Forms.DataGridViewButtonColumn
$resumeColumn.Name = 'Resume'
$resumeColumn.HeaderText = ''
$resumeColumn.Text = 'Resume'
$resumeColumn.UseColumnTextForButtonValue = $true
$resumeColumn.Width = ConvertTo-ScaledPixels 80
$resumeColumn.Resizable = 'False'
[void]$grid.Columns.Add($resumeColumn)

foreach ($def in @(
        @{ Name = 'Title'; Header = 'Session'; Fill = 45 },
        @{ Name = 'Project'; Header = 'Folder'; Fill = 40 },
        @{ Name = 'LastActive'; Header = 'Last active'; Width = 120 },
        @{ Name = 'Status'; Header = 'Status'; Width = 110 },
        @{ Name = 'SessionId'; Header = 'Session ID'; Width = 90 })) {
    $col = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
    $col.Name = $def.Name
    $col.DataPropertyName = $def.Name
    $col.HeaderText = $def.Header
    $col.SortMode = 'Automatic'
    if ($def.ContainsKey('Fill')) {
        $col.AutoSizeMode = 'Fill'
        $col.FillWeight = $def.Fill
    } else {
        $col.Width = ConvertTo-ScaledPixels $def.Width
    }
    [void]$grid.Columns.Add($col)
}
$grid.Columns['Title'].DefaultCellStyle.Font = New-Object System.Drawing.Font($form.Font, [System.Drawing.FontStyle]::Bold)
$grid.Columns['SessionId'].DefaultCellStyle.ForeColor = [System.Drawing.SystemColors]::GrayText
$grid.DataSource = $view

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$menuResume = $menu.Items.Add('Resume')
$menuResume.Font = New-Object System.Drawing.Font($menu.Font, [System.Drawing.FontStyle]::Bold)
$menuCopyCommand = $menu.Items.Add('Copy resume command')
$menuCopyId = $menu.Items.Add('Copy session ID')
$menuExplore = $menu.Items.Add('Open folder in Explorer')
$grid.ContextMenuStrip = $menu

$statusStrip = New-Object System.Windows.Forms.StatusStrip
$statusLabel = New-Object System.Windows.Forms.ToolStripStatusLabel
$statusLabel.Spring = $true
$statusLabel.TextAlign = 'MiddleLeft'
[void]$statusStrip.Items.Add($statusLabel)

# Dock order: the Fill control must be added first so the edges are laid out around it.
$form.Controls.Add($grid)
$form.Controls.Add($toolbar)
$form.Controls.Add($statusStrip)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = $script:RefreshIntervalMs

# Polls for "come to the front" requests from copies that were started while we're running.
$activateTimer = New-Object System.Windows.Forms.Timer
$activateTimer.Interval = 250

#endregion

#region Events

function Invoke-Safely {
    # Exceptions escaping a WinForms event handler would otherwise go to the (hidden) console.
    param([scriptblock]$Action)
    try { & $Action } catch { $statusLabel.Text = "Error: $($_.Exception.Message)" }
}

$grid.add_CellContentClick({
    param($sender, $e)
    if ($e.RowIndex -ge 0 -and $grid.Columns[$e.ColumnIndex].Name -eq 'Resume') {
        Invoke-Safely { Invoke-Resume (Get-SelectedRecord) }
    }
})

$grid.add_CellDoubleClick({
    param($sender, $e)
    if ($e.RowIndex -ge 0 -and $grid.Columns[$e.ColumnIndex].Name -ne 'Resume') {
        Invoke-Safely { Invoke-Resume (Get-SelectedRecord) }
    }
})

$grid.add_CellMouseDown({
    param($sender, $e)
    # Make right-click select the row under the mouse before the context menu opens.
    if ($e.Button -eq 'Right' -and $e.RowIndex -ge 0) {
        $grid.CurrentCell = $grid.Rows[$e.RowIndex].Cells[[Math]::Max($e.ColumnIndex, 1)]
    }
})

# Clicking anywhere that isn't a row deselects. The grid makes its first row current when it
# gains focus, and this handler runs after that, so a click on its empty area ends up with nothing selected.
$grid.add_MouseDown({
    param($sender, $e)
    if ($grid.HitTest($e.X, $e.Y).Type -ne 'Cell') { Clear-GridSelection }
})
foreach ($control in @($form, $toolbar, $searchLabel, $statusStrip)) {
    $control.add_MouseDown({ Clear-GridSelection })
}

$grid.add_CellFormatting({
    param($sender, $e)
    if ($e.RowIndex -lt 0) { return }
    $name = $grid.Columns[$e.ColumnIndex].Name
    if ($name -eq 'LastActive' -and $e.Value -is [datetime]) {
        $when = [datetime]$e.Value
        $age = (Get-Date) - $when
        $e.Value = if ($age.TotalMinutes -lt 1) { 'just now' }
            elseif ($age.TotalHours -lt 1) { '{0} min ago' -f [int][Math]::Floor($age.TotalMinutes) }
            elseif ($age.TotalDays -lt 1) { '{0} h ago' -f [int][Math]::Floor($age.TotalHours) }
            elseif ($age.TotalDays -lt 7) { $when.ToString('ddd HH:mm') }
            else { $when.ToString('yyyy-MM-dd') }
        $e.FormattingApplied = $true
        $grid.Rows[$e.RowIndex].Cells[$e.ColumnIndex].ToolTipText = $when.ToString('F')
    } elseif ($name -eq 'SessionId' -and $e.Value -is [string] -and $e.Value.Length -gt 8) {
        $grid.Rows[$e.RowIndex].Cells[$e.ColumnIndex].ToolTipText = $e.Value
        $e.Value = $e.Value.Substring(0, 8)
        $e.FormattingApplied = $true
    } elseif ($name -eq 'Status' -and $e.Value -is [string] -and $e.Value) {
        $e.CellStyle.ForeColor = [System.Drawing.Color]::SeaGreen
    }
})

$form.add_KeyDown({
    param($sender, $e)
    if ($e.KeyCode -eq 'Enter' -and $grid.Focused) {
        $e.Handled = $true
        $e.SuppressKeyPress = $true
        Invoke-Safely { Invoke-Resume (Get-SelectedRecord) }
    } elseif ($e.KeyCode -eq 'F5') {
        Invoke-Safely { Update-Sessions }
    } elseif ($e.Control -and $e.KeyCode -eq 'F') {
        $searchBox.Focus()
        $searchBox.SelectAll()
    } elseif ($e.KeyCode -eq 'Escape' -and $searchBox.Text) {
        $searchBox.Clear()
    }
})

$menu.add_Opening({
    param($sender, $e)
    if (-not (Get-SelectedRecord)) { $e.Cancel = $true }
})
$menuResume.add_Click({ Invoke-Safely { Invoke-Resume (Get-SelectedRecord) } })
$menuCopyCommand.add_Click({ Invoke-Safely { Copy-ResumeCommand (Get-SelectedRecord) } })
$menuCopyId.add_Click({
    Invoke-Safely {
        $record = Get-SelectedRecord
        if ($record) { [System.Windows.Forms.Clipboard]::SetText($record.SessionId) }
    }
})
$menuExplore.add_Click({
    Invoke-Safely {
        $record = Get-SelectedRecord
        if ($record -and $record.Cwd -and (Test-Path -LiteralPath $record.Cwd -PathType Container)) {
            Start-Process -FilePath 'explorer.exe' -ArgumentList ('"{0}"' -f $record.Cwd)
        } elseif ($record) {
            Show-Message "The session's folder isn't available:`n`n$($record.Cwd)"
        }
    }
})

$searchBox.add_TextChanged({ Invoke-Safely { Update-Filter } })
$emptyCheck.add_CheckedChanged({ Invoke-Safely { Update-Filter } })
$refreshButton.add_Click({ Invoke-Safely { Update-Sessions } })
$timer.add_Tick({ Invoke-Safely { Update-Sessions } })
$activateTimer.add_Tick({
    if ($script:ActivateEvent.WaitOne(0)) {
        if ($form.WindowState -eq 'Minimized') { $form.WindowState = 'Normal' }
        $form.Activate()
    }
})

$form.add_Shown({
    $form.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
    Invoke-Safely {
        Update-Sessions
        Update-Filter
    }
    $form.Cursor = [System.Windows.Forms.Cursors]::Default
    # Focusing the grid would make its first row current, so start in the search box with nothing selected.
    $searchBox.Focus()
    Clear-GridSelection
    $timer.Start()
    $activateTimer.Start()
})

$form.add_FormClosing({
    $timer.Stop()
    $activateTimer.Stop()
    Save-Settings
})

#endregion

try {
    [void]$form.ShowDialog()
} finally {
    $timer.Dispose()
    $activateTimer.Dispose()
    $form.Dispose()
    $script:ActivateEvent.Dispose()
    $script:InstanceMutex.ReleaseMutex()
    $script:InstanceMutex.Dispose()
}
