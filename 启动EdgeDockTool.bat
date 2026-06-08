@echo off
setlocal
cd /d "%~dp0"

set "LOGFILE=%~dp0edgedocktool-startup.log"
set "PYW="

where pythonw >nul 2>nul
if not errorlevel 1 (
    set "PYW=pythonw"
) else (
    if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" set "PYW=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
)

if exist "%~dp0dist\EdgeDockTool.exe" (
    start "" "%~dp0dist\EdgeDockTool.exe"
    exit /b 0
)

if not defined PYW (
    echo pythonw.exe not found. Please install Python or use dist\EdgeDockTool.exe.
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0main.py" 1>>"%LOGFILE%" 2>&1
exit /b 0
