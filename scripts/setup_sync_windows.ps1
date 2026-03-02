# =============================================================================
# setup_sync_windows.ps1 — Platinum Tier
# =============================================================================
# Registers vault_sync as a Windows Task Scheduler task (runs every 5 min).
# Run ONCE in PowerShell as Administrator:
#   powershell -ExecutionPolicy Bypass -File scripts\setup_sync_windows.ps1
# =============================================================================

$RepoRoot  = Split-Path -Parent $PSScriptRoot
$SyncScript = Join-Path $RepoRoot "scripts\vault_sync_windows.py"
$PythonExe  = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogFile    = Join-Path $RepoRoot "vault\Logs\sync_windows.log"
$TaskName   = "AIEmployee-VaultSync"

# ---------------------------------------------------------------------------
# Use Python-based sync on Windows (vault_sync_windows.py)
# ---------------------------------------------------------------------------
$Action  = New-ScheduledTaskAction -Execute $PythonExe -Argument $SyncScript `
           -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 5) `
           -Once -At (Get-Date)
$Settings = New-ScheduledTaskSettingsSet `
           -ExecutionTimeLimit (New-TimeSpan -Minutes 4) `
           -RestartCount 2 `
           -RestartInterval (New-TimeSpan -Minutes 1) `
           -StartWhenAvailable

# Remove old task if exists
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[+] Removed old task: $TaskName"
}

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $Action `
    -Trigger   $Trigger `
    -Settings  $Settings `
    -RunLevel  Highest `
    -Description "AI Employee Vault Git Sync - every 5 minutes"

Write-Host "[+] Registered Task Scheduler task: $TaskName"
Write-Host "    Runs every 5 minutes."
Write-Host "    Log: $LogFile"
Write-Host ""
Write-Host "To view: schtasks /query /tn $TaskName /fo LIST"
Write-Host "To run now: Start-ScheduledTask -TaskName '$TaskName'"
