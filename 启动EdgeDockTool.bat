@echo off
setlocal
cd /d "%~dp0"

set LOGFILE=%~dp0edgedocktool-startup.log

python -c "import PySide6" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Dependency install failed.
        pause
        exit /b 1
    )
)

echo Starting EdgeDockTool...
python "%~dp0main.py" 1>>"%LOGFILE%" 2>&1
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" (
    echo.
    echo Startup failed. Exit code: %EXITCODE%
    echo Log file: %LOGFILE%
    echo.
    type "%LOGFILE%"
    echo.
    pause
    exit /b %EXITCODE%
)
