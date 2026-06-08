@echo off
setlocal
cd /d "%~dp0"

set "LOGFILE=%~dp0edgedocktool-debug.log"
set "LIVELOG=%~dp0edgedocktool-debug-live.log"
set "EDGE_DOCK_DEBUG_RESIZE=1"

if exist "%LIVELOG%" del "%LIVELOG%"

echo [EdgeDockTool] Debug mode starting...
echo Log file: %LOGFILE%
echo.

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo PySide6 is not installed.
    echo Please run: python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

python "%~dp0main.py" 1>>"%LOGFILE%" 2>&1
set "EXITCODE=%ERRORLEVEL%"

echo.
echo EdgeDockTool exited with code: %EXITCODE%
echo Recent log output:
echo ------------------------------
powershell -NoProfile -Command "if (Test-Path '%LIVELOG%') { Get-Content -Path '%LIVELOG%' -Tail 120 } elseif (Test-Path '%LOGFILE%') { Get-Content -Path '%LOGFILE%' -Tail 120 }"
echo ------------------------------
pause
exit /b %EXITCODE%
