@echo off
REM ===============================================================
REM  Intraday Monitor Task Scheduler Setup (Wake from Sleep)
REM  Right-click and Run as Administrator
REM
REM  TW: weekdays 09:00-13:35, every 5 min
REM  US: daily 22:00-05:00, every 10 min (overnight)
REM ===============================================================

setlocal

set SCRIPT_DIR=%~dp0
set PY_SCRIPT=%SCRIPT_DIR%intraday_monitor.py
set TASK_TW=IntradayMonitor_TW
set TASK_US=IntradayMonitor_US

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please run as Administrator
    pause
    exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)
for /f "delims=" %%i in ('where python') do set PY_EXE=%%i & goto :found
:found
echo Python: %PY_EXE%
echo Script: %PY_SCRIPT%
echo.

echo [1/4] Power management (allow wake timers)...
powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /SETACTIVE SCHEME_CURRENT
echo   OK
echo.

echo [2/4] Removing old tasks if exist...
schtasks /Query /TN "%TASK_TW%" >nul 2>&1
if not errorlevel 1 schtasks /Delete /TN "%TASK_TW%" /F
schtasks /Query /TN "%TASK_US%" >nul 2>&1
if not errorlevel 1 schtasks /Delete /TN "%TASK_US%" /F

echo [3/4] Creating TW intraday task (09:00 + repeat every 5 min for 4h35m)...
schtasks /Create ^
    /TN "%TASK_TW%" ^
    /TR "\"%PY_EXE%\" \"%PY_SCRIPT%\"" ^
    /SC DAILY ^
    /ST 09:00 ^
    /RI 5 ^
    /DU 04:35 ^
    /RL HIGHEST ^
    /F

if errorlevel 1 (
    echo [ERROR] TW task creation failed
    pause
    exit /b 1
)

echo       Creating US intraday task (22:00 + repeat every 10 min for 7h)...
schtasks /Create ^
    /TN "%TASK_US%" ^
    /TR "\"%PY_EXE%\" \"%PY_SCRIPT%\"" ^
    /SC DAILY ^
    /ST 22:00 ^
    /RI 10 ^
    /DU 07:00 ^
    /RL HIGHEST ^
    /F

if errorlevel 1 (
    echo [ERROR] US task creation failed
    pause
    exit /b 1
)

echo [4/4] Enabling Wake-To-Run for both tasks...
powershell -ExecutionPolicy Bypass -Command ^
    "foreach ($name in @('%TASK_TW%', '%TASK_US%')) {" ^
    "  $t = Get-ScheduledTask -TaskName $name;" ^
    "  $t.Settings.WakeToRun = $true;" ^
    "  $t.Settings.StartWhenAvailable = $true;" ^
    "  $t.Settings.DisallowStartIfOnBatteries = $false;" ^
    "  $t.Settings.StopIfGoingOnBatteries = $false;" ^
    "  Set-ScheduledTask -InputObject $t | Out-Null;" ^
    "  Write-Host ('  OK: ' + $name)" ^
    "}"

echo.
echo ============================================================
echo Done! Intraday monitor scheduled
echo ============================================================
echo TW (%TASK_TW%): weekdays 09:00-13:35 every 5 min
echo US (%TASK_US%): daily 22:00-05:00 every 10 min
echo Both will wake computer from sleep
echo.
echo Manage:
echo   Run now    : schtasks /Run /TN "%TASK_TW%"
echo   Query      : schtasks /Query /TN "%TASK_TW%" /V /FO LIST
echo   Remove all : schtasks /Delete /TN "%TASK_TW%" /F ^&^& schtasks /Delete /TN "%TASK_US%" /F
echo ============================================================
pause
endlocal
