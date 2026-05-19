@echo off
REM ===============================================================
REM  Morning Report Task Scheduler Setup (Wake from Sleep)
REM  Right-click and Run as Administrator
REM ===============================================================

setlocal

set TASK_NAME=MorningStockReport
set SCRIPT_DIR=%~dp0
set PY_SCRIPT=%SCRIPT_DIR%main.py

net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Please run as Administrator
    pause
    exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH
    pause
    exit /b 1
)
for /f "delims=" %%i in ('where python') do set PY_EXE=%%i & goto :found
:found

echo Python : %PY_EXE%
echo Script : %PY_SCRIPT%
echo.

echo [1/3] Configuring power management (allow wake timers)...
powercfg /SETACVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /SETDCVALUEINDEX SCHEME_CURRENT SUB_SLEEP RTCWAKE 1
powercfg /SETACTIVE SCHEME_CURRENT
echo   OK
echo.

echo [2/3] Creating scheduled task...
schtasks /Query /TN "%TASK_NAME%" >nul 2>&1
if not errorlevel 1 (
    echo   Removing old task...
    schtasks /Delete /TN "%TASK_NAME%" /F
)

schtasks /Create ^
    /TN "%TASK_NAME%" ^
    /TR "\"%PY_EXE%\" \"%PY_SCRIPT%\"" ^
    /SC DAILY ^
    /ST 08:00 ^
    /RL HIGHEST ^
    /F

if errorlevel 1 (
    echo [ERROR] Task creation failed
    pause
    exit /b 1
)

echo [3/3] Enabling Wake-To-Run, StartWhenAvailable, allow battery...
powershell -ExecutionPolicy Bypass -Command ^
    "$t = Get-ScheduledTask -TaskName '%TASK_NAME%';" ^
    "$t.Settings.WakeToRun = $true;" ^
    "$t.Settings.StartWhenAvailable = $true;" ^
    "$t.Settings.DisallowStartIfOnBatteries = $false;" ^
    "$t.Settings.StopIfGoingOnBatteries = $false;" ^
    "Set-ScheduledTask -InputObject $t | Out-Null;" ^
    "Write-Host '  OK'"

echo.
echo ============================================================
echo Done! Morning report will run daily at 08:00
echo Computer will auto-wake from sleep if needed
echo ============================================================
echo Manage:
echo   Run now : schtasks /Run /TN "%TASK_NAME%"
echo   Status  : schtasks /Query /TN "%TASK_NAME%" /V /FO LIST
echo   Remove  : schtasks /Delete /TN "%TASK_NAME%" /F
echo ============================================================
pause
endlocal
