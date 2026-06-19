#Requires -Version 5.1
<#
.SYNOPSIS
    ETF Rotation Bot - Daily Signal Check (PowerShell entry point)

.DESCRIPTION
    Runs daily_signal_check.py from the project root.
    Advisory only: no orders, no auto-trade, no brokerage.
    Default mode: watchlist.csv is NOT updated (--dry-run).

.PARAMETER NoSlack
    Suppress Slack notifications.

.PARAMETER SkipMarketData
    Skip --update-market-data step (use existing data/market_data_latest.csv).

.PARAMETER AllowWatchlistUpdate
    Remove --dry-run, allowing watchlist.csv updates.
    CAUTION: use only after manual review of signal output.

.PARAMETER FullCommitteeScan
    Run LLM committee for ALL candidate symbols (overrides default trigger gate).

.EXAMPLE
    .\scripts\daily_signal_check.ps1
    Full run with Slack enabled.

.EXAMPLE
    .\scripts\daily_signal_check.ps1 -NoSlack
    Full run without Slack.

.EXAMPLE
    .\scripts\daily_signal_check.ps1 -SkipMarketData -NoSlack
    Fast re-run using existing market data, no Slack.
#>

[CmdletBinding()]
param(
    [switch]$NoSlack,
    [switch]$SkipMarketData,
    [switch]$AllowWatchlistUpdate,
    [switch]$FullCommitteeScan
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Project root (parent of scripts\) ───────────────────────────────────────
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

# ── Log setup ────────────────────────────────────────────────────────────────
$LogsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir -Force | Out-Null
}
$ConsoleLog = Join-Path $LogsDir "daily_signal_check_console.log"

$Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$Divider   = "=" * 60

# ── Build argument list ───────────────────────────────────────────────────────
$PythonArgs = @("scripts\daily_signal_check.py")

if ($NoSlack)              { $PythonArgs += "--no-slack" }
if ($SkipMarketData)       { $PythonArgs += "--skip-market-data" }
if ($AllowWatchlistUpdate) { $PythonArgs += "--allow-watchlist-update" }
if ($FullCommitteeScan)    { $PythonArgs += "--full-committee-scan" }

# ── Header ────────────────────────────────────────────────────────────────────
$Header = @"
$Divider
[$Timestamp] Daily Signal Check starting
Project root : $ProjectRoot
Arguments    : $($PythonArgs -join ' ')
Advisory only: no orders / no auto-trade / no brokerage
$Divider
"@

Write-Host $Header
Add-Content -Path $ConsoleLog -Value $Header -Encoding UTF8

# ── Execute ───────────────────────────────────────────────────────────────────
try {
    # Redirect both stdout and stderr; tee to console and log file
    $Output = python @PythonArgs 2>&1
    $ExitCode = $LASTEXITCODE

    $Output | ForEach-Object {
        Write-Host $_
        Add-Content -Path $ConsoleLog -Value $_ -Encoding UTF8
    }
}
catch {
    $ExitCode = 1
    $ErrMsg = "Exception: $_"
    Write-Warning $ErrMsg
    Add-Content -Path $ConsoleLog -Value $ErrMsg -Encoding UTF8
}

# ── Footer ────────────────────────────────────────────────────────────────────
$FinishTs = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
$Footer = @"
[$FinishTs] Daily Signal Check finished (exit=$ExitCode)
$Divider
"@

Write-Host $Footer
Add-Content -Path $ConsoleLog -Value $Footer -Encoding UTF8

# Propagate exit code to Task Scheduler
exit $ExitCode
