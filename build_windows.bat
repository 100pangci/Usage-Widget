@echo off
setlocal
REM ============================================================
REM  usage-widget Windows build script (PyInstaller)
REM  Usage: build_windows.bat [onefile] [console]
REM    onefile  - single-file mode (default onedir, faster start)
REM    console  - keep console window for logs (default GUI-only)
REM  Output: dist\usage-widget\usage-widget.exe
REM  NOTE: keep this file ASCII-only (cmd.exe codepage issues)
REM ============================================================
cd /d "%~dp0"

set "MODE=onedir"
set "CONSOLE_FLAG=--noconsole"
for %%a in (%*) do (
    if /I "%%a"=="onefile" set "MODE=onefile"
    if /I "%%a"=="console" set "CONSOLE_FLAG="
)

set "VENV=.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo [1/3] Creating venv .venv ...
    py -3 -m venv "%VENV%"
    if errorlevel 1 (
        echo ERROR: Python 3.10+ required, and the py launcher must be on PATH.
        echo Install: https://www.python.org/downloads/windows/
        exit /b 1
    )
)
set "PY=%VENV%\Scripts\python.exe"

echo [2/3] Installing deps (PySide6 + pyinstaller) ...
"%PY%" -m pip install --upgrade pip >nul
"%PY%" -m pip install -e . pyinstaller
if errorlevel 1 exit /b 1

echo [3/3] PyInstaller build (%MODE%) ...
set "EXTRA="
if "%MODE%"=="onefile" set "EXTRA=--onefile"
"%PY%" -m PyInstaller --noconfirm --clean --name usage-widget %CONSOLE_FLAG% ^
    --hidden-import urllib.request ^
    --hidden-import urllib.error ^
    --hidden-import configparser ^
    --hidden-import concurrent.futures ^
    --hidden-import winreg ^
    --exclude-module pytest ^
    %EXTRA% ^
    main.py
if errorlevel 1 exit /b 1

echo [4/4] Copying external plugins to plugin\ ...
robocopy "plugins" "dist\usage-widget\plugin" /E /XD __pycache__ .pytest_cache >nul
if errorlevel 8 exit /b 1

echo.
echo Done: dist\usage-widget\usage-widget.exe
echo Plugins (external, editable without rebuild): dist\usage-widget\plugin
echo Config dir: %APPDATA%\usage-widget
echo Plugin data: %USERPROFILE%\.usage-widget\plugin
endlocal
