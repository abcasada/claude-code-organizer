# Reads Claude Code session transcripts from disk and builds the commands that resume them.
# Dot-source this file; it has no UI dependencies so it can be used and tested on its own.

$script:SessionIdPattern = '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
# User prompts larger than this are pasted blobs or tool output, not worth parsing for a title.
$script:MaxParsedLineBytes = 256KB
# How many candidate user lines to hand back while still looking for the cwd and opening prompt.
$script:MaxCandidatePrompts = 25

# Transcripts can be hundreds of MB, and PowerShell is far too slow to split them line by line,
# so the byte-level scan is done in C#. Only the handful of lines we care about come back as strings.
if (-not ('ClaudeCodeOrganizer.TranscriptScanner' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace ClaudeCodeOrganizer
{
    public class ScanResult
    {
        // Title lines, plus the first few user-message lines (as raw JSON).
        public List<string> Lines = new List<string>();
        // Distinct "cwd" values seen on messages, still JSON-escaped, in order of first appearance.
        public List<string> Cwds = new List<string>();
    }

    public static class TranscriptScanner
    {
        private static readonly byte[][] TitlePrefixes =
        {
            Encoding.ASCII.GetBytes("{\"type\":\"custom-title\""),
            Encoding.ASCII.GetBytes("{\"type\":\"ai-title\""),
            Encoding.ASCII.GetBytes("{\"type\":\"summary\""),
        };
        private static readonly byte[] MessagePrefix = Encoding.ASCII.GetBytes("{\"parentUuid\"");
        private static readonly byte[] CwdKey = Encoding.ASCII.GetBytes("\"cwd\":\"");

        // Reads the complete lines between offset and end of file and returns the ones of interest:
        // every title line, plus up to maxPrompts user-message lines no larger than maxPromptBytes.
        // offset is advanced past the last complete line; a partially written last line is left
        // for the next call, since Claude Code may be appending to the file while we read it.
        public static ScanResult Scan(string path, ref long offset, int maxPrompts, int maxPromptBytes)
        {
            var result = new ScanResult();
            var share = FileShare.ReadWrite | FileShare.Delete;
            using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, share, 1 << 16))
            {
                long end = stream.Length;
                if (offset > end) offset = 0;
                stream.Seek(offset, SeekOrigin.Begin);

                var buffer = new byte[4 << 20];
                var pending = new MemoryStream();
                int prompts = 0;

                while (stream.Position < end)
                {
                    int read = stream.Read(buffer, 0, (int)Math.Min(buffer.Length, end - stream.Position));
                    if (read <= 0) break;

                    int start = 0;
                    while (true)
                    {
                        int newline = Array.IndexOf(buffer, (byte)'\n', start, read - start);
                        if (newline < 0) break;

                        if (pending.Length > 0)
                        {
                            pending.Write(buffer, start, newline - start);
                            byte[] line = pending.ToArray();
                            Consider(line, 0, line.Length, result, ref prompts, maxPrompts, maxPromptBytes);
                            pending.SetLength(0);
                        }
                        else
                        {
                            Consider(buffer, start, newline - start, result, ref prompts, maxPrompts, maxPromptBytes);
                        }
                        start = newline + 1;
                    }

                    offset = stream.Position - (read - start) - pending.Length;
                    if (start < read) pending.Write(buffer, start, read - start);
                }
            }
            return result;
        }

        private static void Consider(byte[] bytes, int start, int count, ScanResult result,
                                     ref int prompts, int maxPrompts, int maxPromptBytes)
        {
            foreach (var prefix in TitlePrefixes)
            {
                if (StartsWith(bytes, start, count, prefix))
                {
                    result.Lines.Add(Encoding.UTF8.GetString(bytes, start, count));
                    return;
                }
            }

            if (!StartsWith(bytes, start, count, MessagePrefix)) return;

            // The working directory can change within a session (and a transcript copied into another
            // project folder keeps its old history), so collect every distinct cwd.
            string cwd = FindCwd(bytes, start, count);
            if (cwd != null && !result.Cwds.Contains(cwd)) result.Cwds.Add(cwd);

            if (prompts >= maxPrompts || count > maxPromptBytes) return;
            string text = Encoding.UTF8.GetString(bytes, start, count);
            if (text.IndexOf("\"type\":\"user\"", StringComparison.Ordinal) < 0) return;
            if (text.IndexOf("\"tool_result\"", StringComparison.Ordinal) >= 0) return;
            result.Lines.Add(text);
            prompts++;
        }

        // Returns the raw (still JSON-escaped) value of the last "cwd" key on the line. The top-level
        // key comes after the message body, and a literal "cwd":" cannot occur inside a JSON string.
        private static string FindCwd(byte[] bytes, int start, int count)
        {
            for (int i = start + count - CwdKey.Length; i >= start; i--)
            {
                if (bytes[i] != (byte)'"' || !StartsWith(bytes, i, start + count - i, CwdKey)) continue;

                int valueStart = i + CwdKey.Length;
                for (int j = valueStart; j < start + count; j++)
                {
                    if (bytes[j] == (byte)'\\') { j++; continue; }
                    if (bytes[j] == (byte)'"') return Encoding.UTF8.GetString(bytes, valueStart, j - valueStart);
                }
                return null;
            }
            return null;
        }

        private static bool StartsWith(byte[] bytes, int start, int count, byte[] prefix)
        {
            if (count < prefix.Length) return false;
            for (int i = 0; i < prefix.Length; i++)
            {
                if (bytes[start + i] != prefix[i]) return false;
            }
            return true;
        }
    }
}
'@
}

function Get-ClaudeConfigDir {
    if ($env:CLAUDE_CONFIG_DIR) { return $env:CLAUDE_CONFIG_DIR }
    return Join-Path $env:USERPROFILE '.claude'
}

function New-SessionRecord {
    param([Parameter(Mandatory)][System.IO.FileInfo]$File)

    [pscustomobject]@{
        Path         = $File.FullName
        ProjectDir   = $File.Directory.Name
        SessionId    = $File.BaseName
        Offset       = [long]0
        Length       = [long]-1
        LastWrite    = [datetime]::MinValue
        Cwd          = $null
        SeenCwds     = New-Object System.Collections.Generic.List[string]
        FirstPrompt  = $null
        AiTitle      = $null
        CustomTitle  = $null
        Summary      = $null
    }
}

function ConvertTo-ProjectDirName {
    # Mirrors how Claude Code names its per-project folders: every non-alphanumeric
    # character in the working directory becomes '-'.
    param([Parameter(Mandatory)][string]$Path)
    return $Path -replace '[^a-zA-Z0-9]', '-'
}

function Update-SessionCwd {
    # `claude --resume <id>` only finds a transcript stored under the project folder for the
    # current directory, so pick the cwd that maps to the folder this transcript lives in.
    param([Parameter(Mandatory)]$Record)

    foreach ($cwd in $Record.SeenCwds) {
        $encoded = ConvertTo-ProjectDirName $cwd
        # Very long paths are truncated (and suffixed with a hash) by Claude Code.
        if ($encoded -eq $Record.ProjectDir -or
            ($encoded.Length -gt 200 -and $Record.ProjectDir.StartsWith($encoded.Substring(0, 200)))) {
            $Record.Cwd = $cwd
            return
        }
    }
    if (-not $Record.Cwd -and $Record.SeenCwds.Count -gt 0) { $Record.Cwd = $Record.SeenCwds[0] }
}

function Test-SessionHasContent {
    # Sessions that were opened and closed without a real prompt (e.g. only /model) aren't worth listing.
    param([Parameter(Mandatory)]$Record)
    return [bool]($Record.CustomTitle -or $Record.AiTitle -or $Record.Summary -or $Record.FirstPrompt)
}

function Get-SessionTitle {
    param([Parameter(Mandatory)]$Record)

    foreach ($candidate in $Record.CustomTitle, $Record.AiTitle, $Record.Summary, $Record.FirstPrompt) {
        if ($candidate) { return $candidate }
    }
    return '(untitled)'
}

function Test-HasProperty {
    param($Object, [string]$Name)
    return ($null -ne $Object) -and ($Object.PSObject.Properties.Name -contains $Name)
}

function Get-PromptText {
    # Returns the text of a user-typed prompt, or $null for slash-command wrappers,
    # hook output and other machine-generated "user" entries.
    param($Content)

    $text = $null
    if ($Content -is [string]) {
        $text = $Content
    } else {
        foreach ($block in @($Content)) {
            if (-not (Test-HasProperty $block 'type')) { continue }
            if ($block.type -eq 'tool_result') { return $null }
            if ($block.type -eq 'text' -and (Test-HasProperty $block 'text') -and $block.text) { $text = $block.text; break }
        }
    }
    if (-not $text) { return $null }

    $text = ($text -replace '</?pasted_content[^>]*>', '').Trim()
    if ($text -match '^<[a-zA-Z][\w-]*>' -or $text.StartsWith('Caveat:')) { return $null }

    $oneLine = $text -replace '\s+', ' '
    if ($oneLine.Length -gt 200) { $oneLine = $oneLine.Substring(0, 200) + '...' }
    return $oneLine
}

function Read-SessionLine {
    param(
        [Parameter(Mandatory)]$Record,
        [Parameter(Mandatory)][string]$Line
    )

    try { $entry = $Line | ConvertFrom-Json } catch { return }
    if (-not (Test-HasProperty $entry 'type')) { return }

    switch ($entry.type) {
        'custom-title' { if ((Test-HasProperty $entry 'customTitle') -and $entry.customTitle) { $Record.CustomTitle = $entry.customTitle } }
        'ai-title'     { if ((Test-HasProperty $entry 'aiTitle') -and $entry.aiTitle) { $Record.AiTitle = $entry.aiTitle } }
        'summary'      { if ((Test-HasProperty $entry 'summary') -and $entry.summary) { $Record.Summary = $entry.summary } }
        'user' {
            if ($Record.FirstPrompt) { return }
            if ((Test-HasProperty $entry 'isMeta') -and $entry.isMeta) { return }
            if ((Test-HasProperty $entry 'isSidechain') -and $entry.isSidechain) { return }
            if ((Test-HasProperty $entry 'message') -and (Test-HasProperty $entry.message 'content')) {
                $Record.FirstPrompt = Get-PromptText $entry.message.content
            }
        }
    }
}

function Update-SessionRecord {
    # Incrementally parses whatever has been appended to the transcript since the last call.
    # Returns $true if the record changed.
    param([Parameter(Mandatory)]$Record)

    $info = New-Object System.IO.FileInfo($Record.Path)
    if (-not $info.Exists) { return $false }
    if ($info.Length -eq $Record.Length -and $info.LastWriteTimeUtc -eq $Record.LastWrite) { return $false }

    if ($info.Length -lt $Record.Offset) {
        # File was truncated or rewritten; start over.
        $fresh = New-SessionRecord -File $info
        foreach ($p in $fresh.PSObject.Properties) { $Record.($p.Name) = $p.Value }
    }

    $offset = [long]$Record.Offset
    $maxPrompts = if ($Record.FirstPrompt) { 0 } else { $script:MaxCandidatePrompts }
    $scan = [ClaudeCodeOrganizer.TranscriptScanner]::Scan($Record.Path, [ref]$offset, $maxPrompts, $script:MaxParsedLineBytes)
    foreach ($line in $scan.Lines) { Read-SessionLine -Record $Record -Line $line }

    foreach ($raw in $scan.Cwds) {
        try { $cwd = ConvertFrom-Json ('"' + $raw + '"') } catch { continue }
        # Skip POSIX-style paths reported by Git Bash; they don't name a Windows project folder.
        if ($cwd -and -not $cwd.StartsWith('/') -and -not $Record.SeenCwds.Contains($cwd)) { $Record.SeenCwds.Add($cwd) }
    }
    Update-SessionCwd -Record $Record

    $Record.Offset = $offset
    $Record.Length = $info.Length
    $Record.LastWrite = $info.LastWriteTimeUtc
    return $true
}

function Get-SessionFiles {
    param([string]$ConfigDir = (Get-ClaudeConfigDir))

    $projects = Join-Path $ConfigDir 'projects'
    if (-not (Test-Path -LiteralPath $projects)) { return @() }

    # Only top-level transcripts; subagent transcripts live in <session>\subagents\ and are not resumable.
    Get-ChildItem -LiteralPath $projects -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { Get-ChildItem -LiteralPath $_.FullName -Filter '*.jsonl' -File -ErrorAction SilentlyContinue } |
        Where-Object { $_.BaseName -match $script:SessionIdPattern }
}

function Get-OpenSessions {
    # Claude Code keeps a <pid>.json file in <config>\sessions for each live process.
    # Returns a hashtable of "sessionId|cwd" -> status ("busy", "idle", ...) for processes still
    # running. The same session id can be stored under more than one project folder, hence the cwd.
    param([string]$ConfigDir = (Get-ClaudeConfigDir))

    $open = @{}
    $dir = Join-Path $ConfigDir 'sessions'
    if (-not (Test-Path -LiteralPath $dir)) { return $open }

    foreach ($file in Get-ChildItem -LiteralPath $dir -Filter '*.json' -File -ErrorAction SilentlyContinue) {
        try {
            $entry = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop | ConvertFrom-Json
            if (-not ((Test-HasProperty $entry 'pid') -and (Test-HasProperty $entry 'sessionId'))) { continue }

            $proc = Get-Process -Id ([int]$entry.pid) -ErrorAction SilentlyContinue
            if (-not $proc) { continue }

            # Guard against PID reuse: procStart is the process creation time as a FILETIME.
            if (Test-HasProperty $entry 'procStart') {
                try {
                    $delta = [Math]::Abs($proc.StartTime.ToFileTimeUtc() - [long]$entry.procStart)
                    if ($delta -gt 20000000) { continue }  # more than 2 seconds apart
                } catch { }
            }

            $status = if ((Test-HasProperty $entry 'status') -and $entry.status) { [string]$entry.status } else { 'open' }
            $cwd = if (Test-HasProperty $entry 'cwd') { [string]$entry.cwd } else { '' }
            $open[(Get-OpenSessionKey -SessionId $entry.sessionId -Cwd $cwd)] = $status
        } catch { }
    }
    return $open
}

function Get-OpenSessionKey {
    param([string]$SessionId, [string]$Cwd)
    return ('{0}|{1}' -f $SessionId, $Cwd.TrimEnd('\')).ToLowerInvariant()
}

function Find-WindowsTerminal {
    $cmd = Get-Command 'wt.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cmd) { return $cmd.Source }
    return $null
}

function Find-PowerShellExe {
    # Prefer PowerShell 7 when installed, fall back to Windows PowerShell.
    foreach ($name in 'pwsh.exe', 'powershell.exe') {
        $cmd = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd) { return $cmd.Source }
    }
    return 'powershell.exe'
}

function ConvertTo-WtArgument {
    # Quotes one argument for wt.exe's command line. wt splits commands on ';', so escape it.
    param([Parameter(Mandatory)][string]$Value)

    $v = $Value.Replace(';', '\;')
    if ($v -notmatch '[\s"]') { return $v }
    # A trailing backslash would escape the closing quote.
    if ($v.EndsWith('\')) { $v += '\' }
    return '"' + $v.Replace('"', '\"') + '"'
}

function Get-ResumeLaunchSpec {
    # Builds (but does not run) the process start info used to resume a session.
    param(
        [Parameter(Mandatory)][string]$SessionId,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [string]$Title = 'Claude',
        [string]$WindowsTerminal,
        [string]$Shell = (Find-PowerShellExe)
    )

    if ($SessionId -notmatch $script:SessionIdPattern) { throw "Invalid session id: $SessionId" }
    if ($WorkingDirectory.IndexOf('"') -ge 0) { throw "Invalid working directory: $WorkingDirectory" }

    $claudeCommand = "claude --resume $SessionId"

    if ($WindowsTerminal) {
        $safeTitle = ($Title -replace '[";\r\n]', ' ').Trim()
        if ($safeTitle.Length -gt 40) { $safeTitle = $safeTitle.Substring(0, 40).Trim() }
        if (-not $safeTitle) { $safeTitle = 'Claude' }

        # -w 0 targets the most recently used Terminal window, or opens a new one if none exists.
        $wtArgs = @(
            '-w', '0', 'new-tab',
            '--title', (ConvertTo-WtArgument $safeTitle),
            '--startingDirectory', (ConvertTo-WtArgument $WorkingDirectory),
            (ConvertTo-WtArgument $Shell), '-NoExit', '-Command', $claudeCommand
        )
        return [pscustomobject]@{
            FilePath         = $WindowsTerminal
            Arguments        = $wtArgs -join ' '
            WorkingDirectory = $WorkingDirectory
        }
    }

    return [pscustomobject]@{
        FilePath         = $Shell
        Arguments        = "-NoExit -Command $claudeCommand"
        WorkingDirectory = $WorkingDirectory
    }
}

function Start-ResumeSession {
    param([Parameter(Mandatory)]$Spec)

    Start-Process -FilePath $Spec.FilePath -ArgumentList $Spec.Arguments -WorkingDirectory $Spec.WorkingDirectory
}
