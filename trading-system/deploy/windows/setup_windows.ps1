# =====================================================================
#  One-time Windows setup for running the trading system 24/7.
#
#  Run in an ADMIN PowerShell:
#      Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#      .\setup_windows.ps1
#
#  What it configures:
#    - power settings so the machine never sleeps or hibernates
#    - a Scheduled Task that starts the supervisor at boot AND at logon,
#      and restarts it if it ever stops
#    - a firewall rule for the API so your phone can reach it on the LAN
#
#  It does NOT disable Windows Update reboots. You can't fully prevent
#  those; what protects you is that stop-losses live broker-side and the
#  runner reconciles on restart. See deploy/windows/README.md.
# =====================================================================

param(
    [string]$ProjectDir = "C:\trading-system",
    [int]$ApiPort = 8000
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $ProjectDir)) {
    throw "ProjectDir '$ProjectDir' does not exist. Clone the repo there first, or pass -ProjectDir."
}

$batch = Join-Path $ProjectDir "deploy\windows\run_trading_system.bat"
if (-not (Test-Path $batch)) {
    throw "Could not find $batch"
}

Write-Host "== Power settings: never sleep ==" -ForegroundColor Cyan
# Applies to both AC and battery. A laptop that sleeps is a laptop that
# stops trading and stops watching its own positions.
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off

Write-Host "== Lid close action: do nothing ==" -ForegroundColor Cyan
# 0 = do nothing. Without this, closing the lid suspends everything.
powercfg /setacvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 5ca83367-6e45-459f-a27b-476b1d01c936 0
powercfg /setdcvalueindex SCHEME_CURRENT 4f971e89-eebd-4455-a8de-9e59040e7347 5ca83367-6e45-459f-a27b-476b1d01c936 0
powercfg /setactive SCHEME_CURRENT

Write-Host "== Scheduled Task: start at boot + logon, auto-restart ==" -ForegroundColor Cyan
$taskName = "TradingSystemRunner"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Write-Host "  removing existing task"
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $batch
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn)
)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

# Run as the logged-in user: the MT5 terminal runs in your desktop session,
# and the Python API attaches to that running terminal. A SYSTEM-account task
# cannot see it.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers `
    -Settings $settings -Principal $principal `
    -Description "24/7 MT5 trading system supervisor" | Out-Null

Write-Host "== Firewall: allow API on port $ApiPort (private networks) ==" -ForegroundColor Cyan
$ruleName = "Trading System API"
if (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $ruleName
}
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $ApiPort -Profile Private | Out-Null

Write-Host ""
Write-Host "Done." -ForegroundColor Green
Write-Host ""
Write-Host "Remaining manual steps:" -ForegroundColor Yellow
Write-Host "  1. Open the MT5 terminal, log in, and enable:"
Write-Host "     Tools > Options > Expert Advisors > Allow algorithmic trading"
Write-Host "  2. Set MT5_LOGIN / MT5_PASSWORD / MT5_SERVER in $ProjectDir\.env"
Write-Host "  3. Enable instruments: mobile Assets tab, or POST /watchlist"
Write-Host "  4. Start it now without rebooting:  Start-ScheduledTask -TaskName $taskName"
Write-Host ""
Write-Host "Verify it is running:  Get-ScheduledTask -TaskName $taskName"
Write-Host "Logs:                  $ProjectDir\logs\"
