param(
    [int]$MinFilesChanged = 5,
    [int]$MinTotalLinesChanged = 120,
    [int]$QuietSeconds = 90,
    [int]$PollSeconds = 15,
    [string]$CommitPrefix = "auto-sync",
    [switch]$RunOnce
)

$ErrorActionPreference = "Stop"
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

function Invoke-Git {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Args
    )

    $stdoutPath = [System.IO.Path]::GetTempFileName()
    $stderrPath = [System.IO.Path]::GetTempFileName()
    $workingDirectory = if ([string]::IsNullOrWhiteSpace($script:RepoRoot)) {
        (Get-Location).Path
    } else {
        $script:RepoRoot
    }
    $argumentString = ($Args | ForEach-Object {
        if ($_ -match '[\s"]') {
            '"' + ($_.Replace('"', '\"')) + '"'
        } else {
            $_
        }
    }) -join " "

    try {
        $process = Start-Process `
            -FilePath "git.exe" `
            -ArgumentList $argumentString `
            -WorkingDirectory $workingDirectory `
            -Wait `
            -PassThru `
            -NoNewWindow `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath

        $output = @()
        if (Test-Path -LiteralPath $stdoutPath) {
            $output += @(Get-Content -LiteralPath $stdoutPath)
        }

        if (Test-Path -LiteralPath $stderrPath) {
            $output += @(Get-Content -LiteralPath $stderrPath)
        }
    } finally {
        Remove-Item -LiteralPath $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
    }

    [pscustomobject]@{
        ExitCode = $process.ExitCode
        Lines    = @($output)
        Text     = (@($output) -join "`n").Trim()
    }
}

$script:RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoCheckResult = Invoke-Git rev-parse --is-inside-work-tree
if ($repoCheckResult.ExitCode -ne 0 -or $repoCheckResult.Text.Trim() -ne "true") {
    throw "git_autosync.ps1 must be started inside a git repository."
}

$script:LogPath = Join-Path $script:RepoRoot ".git\autosync.log"
Set-Location -LiteralPath $script:RepoRoot

function Write-Log {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[${timestamp}] $Message"
    Add-Content -LiteralPath $script:LogPath -Value $line
    Write-Host $line
}

function Test-ExistingWatcher {
    $repoRootPattern = $script:RepoRoot.Replace("/", "\")
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'pwsh.exe'" -ErrorAction SilentlyContinue

    foreach ($process in $processes) {
        if ($process.ProcessId -eq $PID) {
            continue
        }

        $commandLine = $process.CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            continue
        }

        if ($commandLine -like "*tools\git_autosync.ps1*" -and $commandLine -like "*$repoRootPattern*") {
            return $true
        }
    }

    return $false
}

function Get-StatusLines {
    $result = Invoke-Git status --porcelain=v1 --untracked-files=all
    if ($result.ExitCode -ne 0) {
        throw "Unable to read git status: $($result.Text)"
    }

    @($result.Lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Test-HasUnmergedChanges {
    param([string[]]$StatusLines)

    foreach ($line in $StatusLines) {
        if ($line -match "^(DD|AU|UD|UA|DU|AA|UU)\s") {
            return $true
        }
    }

    return $false
}

function Get-ChangeSummary {
    $statusLines = Get-StatusLines
    $fileCount = @($statusLines).Count
    $shortStatResult = Invoke-Git diff --shortstat HEAD
    $untrackedResult = Invoke-Git ls-files --others --exclude-standard

    $insertions = 0
    $deletions = 0
    $untrackedLines = 0

    if ($shortStatResult.Text -match "(\d+)\s+insertion") {
        $insertions = [int]$Matches[1]
    }

    if ($shortStatResult.Text -match "(\d+)\s+deletion") {
        $deletions = [int]$Matches[1]
    }

    foreach ($relativePath in @($untrackedResult.Lines | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })) {
        $absolutePath = Join-Path $script:RepoRoot $relativePath

        if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
            continue
        }

        try {
            $lineCount = ([System.IO.File]::ReadLines($absolutePath) | Measure-Object -Line).Lines
            $untrackedLines += $lineCount
        } catch {
            continue
        }
    }

    [pscustomobject]@{
        StatusLines = $statusLines
        Snapshot    = ($statusLines -join "`n")
        FileCount   = $fileCount
        TotalLines  = $insertions + $deletions + $untrackedLines
        ShortStat   = $shortStatResult.Text
    }
}

function Test-ThresholdReached {
    param($Summary)

    if ($Summary.FileCount -ge $MinFilesChanged) {
        return $true
    }

    if ($Summary.TotalLines -ge $MinTotalLinesChanged) {
        return $true
    }

    return $false
}

function Get-BranchContext {
    $branchResult = Invoke-Git rev-parse --abbrev-ref HEAD
    if ($branchResult.ExitCode -ne 0) {
        Write-Log "Skipping sync because current branch could not be detected."
        return $null
    }

    $branch = $branchResult.Text.Trim()
    if ($branch -eq "HEAD") {
        Write-Log "Skipping sync because repository is in detached HEAD state."
        return $null
    }

    $originResult = Invoke-Git remote get-url origin
    $hasOrigin = $originResult.ExitCode -eq 0

    if (-not $hasOrigin) {
        Write-Log "Skipping sync because remote 'origin' is missing."
        return $null
    }

    $upstreamResult = Invoke-Git rev-parse --abbrev-ref --symbolic-full-name "@{u}"
    $hasUpstream = $upstreamResult.ExitCode -eq 0

    $pushArgs = if ($hasUpstream) {
        @("push")
    } else {
        @("push", "--set-upstream", "origin", $branch)
    }

    [pscustomobject]@{
        Branch      = $branch
        HasUpstream = $hasUpstream
        PushArgs    = $pushArgs
    }
}

function Push-IfNeeded {
    $branchContext = Get-BranchContext
    if ($null -eq $branchContext) {
        return $false
    }

    $shouldPush = $true

    if ($branchContext.HasUpstream) {
        $aheadResult = Invoke-Git rev-list --count "@{u}..HEAD"
        if ($aheadResult.ExitCode -ne 0) {
            Write-Log "Unable to compare with upstream: $($aheadResult.Text)"
            return $false
        }

        $aheadCount = [int]$aheadResult.Text
        $shouldPush = $aheadCount -gt 0
    }

    if (-not $shouldPush) {
        return $false
    }

    $pushResult = Invoke-Git @($branchContext.PushArgs)
    if ($pushResult.ExitCode -ne 0) {
        Write-Log "Push failed: $($pushResult.Text)"
        return $false
    }

    Write-Log "Pushed branch '$($branchContext.Branch)' to origin."
    return $true
}

function Invoke-AutoSync {
    $summary = Get-ChangeSummary

    if ($summary.FileCount -eq 0) {
        return $false
    }

    if (Test-HasUnmergedChanges -StatusLines $summary.StatusLines) {
        Write-Log "Skipping sync because there are merge conflicts."
        return $false
    }

    if (-not (Test-ThresholdReached -Summary $summary)) {
        Write-Log "Changes detected but thresholds are not met yet ($($summary.FileCount) files, $($summary.TotalLines) lines)."
        return $false
    }

    $branchContext = Get-BranchContext
    if ($null -eq $branchContext) {
        return $false
    }

    $addResult = Invoke-Git add --all
    if ($addResult.ExitCode -ne 0) {
        Write-Log "git add failed: $($addResult.Text)"
        return $false
    }

    & git diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        return $false
    }

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $message = "{0}: {1} ({2} files, {3} lines)" -f $CommitPrefix, $timestamp, $summary.FileCount, $summary.TotalLines
    $commitResult = Invoke-Git commit --message $message
    if ($commitResult.ExitCode -ne 0) {
        Write-Log "Commit failed: $($commitResult.Text)"
        return $false
    }

    Write-Log "Created commit '$message'."
    Push-IfNeeded | Out-Null
    return $true
}

function Invoke-Cycle {
    $summary = Get-ChangeSummary

    if ($summary.FileCount -eq 0) {
        Push-IfNeeded | Out-Null
        return [pscustomobject]@{
            HasChanges = $false
            Snapshot   = ""
            Synced     = $false
        }
    }

    if (Test-HasUnmergedChanges -StatusLines $summary.StatusLines) {
        Write-Log "Detected merge conflicts. Waiting for manual resolution."
        return [pscustomobject]@{
            HasChanges = $true
            Snapshot   = $summary.Snapshot
            Synced     = $false
        }
    }

    $synced = Invoke-AutoSync

    [pscustomobject]@{
        HasChanges = $true
        Snapshot   = $summary.Snapshot
        Synced     = $synced
    }
}

if (Test-ExistingWatcher) {
    Write-Log "Another auto-sync watcher is already running for this repository. Exiting."
    exit 0
}

Write-Log "Auto-sync watcher started for $script:RepoRoot"
Write-Log "Thresholds: $MinFilesChanged files or $MinTotalLinesChanged lines after $QuietSeconds seconds of inactivity."

if ($RunOnce) {
    Invoke-Cycle | Out-Null
    exit 0
}

$lastSnapshot = ""
$pendingSince = $null

while ($true) {
    Set-Location $script:RepoRoot
    $summary = Get-ChangeSummary

    if ($summary.FileCount -eq 0) {
        $lastSnapshot = ""
        $pendingSince = $null
        Push-IfNeeded | Out-Null
        Start-Sleep -Seconds $PollSeconds
        continue
    }

    if ($summary.Snapshot -ne $lastSnapshot) {
        $lastSnapshot = $summary.Snapshot
        $pendingSince = Get-Date
        Write-Log "Detected change set ($($summary.FileCount) files, $($summary.TotalLines) lines). Waiting for quiet period."
        Start-Sleep -Seconds $PollSeconds
        continue
    }

    if ($null -eq $pendingSince) {
        $pendingSince = Get-Date
        Start-Sleep -Seconds $PollSeconds
        continue
    }

    $quietFor = ((Get-Date) - $pendingSince).TotalSeconds
    if ($quietFor -lt $QuietSeconds) {
        Start-Sleep -Seconds $PollSeconds
        continue
    }

    $cycle = Invoke-Cycle

    if ($cycle.Synced) {
        $lastSnapshot = ""
    } else {
        $lastSnapshot = $cycle.Snapshot
    }

    $pendingSince = $null
    Start-Sleep -Seconds $PollSeconds
}
