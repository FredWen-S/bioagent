@echo off
setlocal
cd /d "%~dp0"

set "POWERSHELL_EXE="
where pwsh.exe >nul 2>nul
if not errorlevel 1 (
    pwsh.exe -NoLogo -NoProfile -Command "exit 0" >nul 2>nul
    if not errorlevel 1 set "POWERSHELL_EXE=pwsh.exe"
)
if not defined POWERSHELL_EXE set "POWERSHELL_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"

%POWERSHELL_EXE% -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_web_ui.ps1" %*
set "BIOAGENT_EXIT_CODE=%ERRORLEVEL%"

if not "%BIOAGENT_EXIT_CODE%"=="0" (
    echo.
    echo BioRender GUI Agent stopped with an error.
    if not "%BIOAGENT_NO_PAUSE%"=="1" pause
)
exit /b %BIOAGENT_EXIT_CODE%
