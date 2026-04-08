param(
    [string]$TaskName = "GitAutoSync-avtomatization"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
$startupDir = [Environment]::GetFolderPath("Startup")
$launcherPath = Join-Path $startupDir "$TaskName.cmd"
$removed = @()

if ($null -ne $task) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    $removed += "scheduled task"
}

if (Test-Path -LiteralPath $launcherPath) {
    Remove-Item -LiteralPath $launcherPath -Force
    $removed += "startup launcher"
}

if (@($removed).Count -eq 0) {
    Write-Host "No auto-sync startup entry was installed."
    exit 0
}

Write-Host ("Removed: " + ($removed -join ", "))
