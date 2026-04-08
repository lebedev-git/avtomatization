param(
    [string]$TaskName = "GitAutoSync-avtomatization",
    [int]$MinFilesChanged = 5,
    [int]$MinTotalLinesChanged = 120,
    [int]$QuietSeconds = 90,
    [int]$PollSeconds = 15
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

$scriptPath = Join-Path $repoRoot "tools\git_autosync.ps1"
if (-not (Test-Path -LiteralPath $scriptPath)) {
    throw "Auto-sync script not found: $scriptPath"
}

$startupDir = [Environment]::GetFolderPath("Startup")
$launcherPath = Join-Path $startupDir "$TaskName.cmd"

$userId = if ($env:USERDOMAIN) {
    "$($env:USERDOMAIN)\$($env:USERNAME)"
} else {
    $env:USERNAME
}

$arguments = @(
    "-NoProfile"
    "-ExecutionPolicy Bypass"
    "-WindowStyle Hidden"
    ('-File "{0}"' -f $scriptPath)
    "-MinFilesChanged $MinFilesChanged"
    "-MinTotalLinesChanged $MinTotalLinesChanged"
    "-QuietSeconds $QuietSeconds"
    "-PollSeconds $PollSeconds"
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited

try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "Auto-commit and push large changes for $repoRoot" `
        -Force | Out-Null

    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Scheduled task '$TaskName' installed and started."
    exit 0
} catch {
    $launcherContent = @(
        "@echo off"
        "start """" powershell.exe $arguments"
    ) -join "`r`n"

    Set-Content -LiteralPath $launcherPath -Value $launcherContent -Encoding Ascii
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments -WindowStyle Hidden
    Write-Host "Scheduled task install was denied, so Startup launcher was created: $launcherPath"
}
