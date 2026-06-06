@echo off
REM ============================================================
REM ETF Rotation Bot -- Daily Signal Check
REM Windows Task Scheduler entry point
REM
REM Advisory only: no orders, no auto-trade, no brokerage.
REM Default mode: watchlist.csv is NOT updated (--dry-run).
REM Use --allow-watchlist-update to enable watchlist updates.
REM
REM Usage:
REM   daily_signal_check.bat             (full run with Slack)
REM   daily_signal_check.bat --no-slack  (suppress Slack)
REM   daily_signal_check.bat --skip-market-data --no-slack
REM ============================================================

REM Move to project root (parent of scripts\)
cd /d %~dp0..

REM Ensure logs directory exists
if not exist logs mkdir logs

REM Combined rolling console log (appended on every run, one file)
set CONSOLE_LOG=logs\daily_signal_check_console.log

REM Header line with timestamp
echo ============================================================ >> %CONSOLE_LOG%
echo [%DATE% %TIME%] Daily Signal Check starting >> %CONSOLE_LOG%

REM Run Python script, forwarding all bat arguments (%*)
REM stdout + stderr both appended to console log
python scripts\daily_signal_check.py %* >> %CONSOLE_LOG% 2>&1
set EXIT_CODE=%ERRORLEVEL%

echo [%DATE% %TIME%] Daily Signal Check finished (exit=%EXIT_CODE%) >> %CONSOLE_LOG%
echo ============================================================ >> %CONSOLE_LOG%

exit /b %EXIT_CODE%
