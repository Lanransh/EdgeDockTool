@echo off
setlocal
cd /d "%~dp0"

set "PYW="

if exist "%~dp0.venv\Scripts\pythonw.exe" (
    set "PYW=%~dp0.venv\Scripts\pythonw.exe"
) else (
    where pythonw >nul 2>nul
    if not errorlevel 1 (
        set "PYW=pythonw"
    ) else (
        if exist "%LocalAppData%\Programs\Python\Python312\pythonw.exe" set "PYW=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
    )
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

"%PYW%" -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo PySide6 is not installed for the selected Python.
    echo Please run: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

start "" "%PYW%" "%~dp0main.py"
exit /b 0
