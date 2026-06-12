@echo off
chcp 65001 > NUL 2>&1
cd /d "%~dp0"

echo.
echo ==============================================
echo   PDFPaperTranslator - Web Server
echo ==============================================
echo.

REM ---- Auto-detect Python ----
set PYTHON=

REM Try python first
where python > NUL 2>&1
if %errorlevel% equ 0 (
    set PYTHON=python
    goto :python_found
)

REM Try py
where py > NUL 2>&1
if %errorlevel% equ 0 (
    set PYTHON=py
    goto :python_found
)

REM Try python3
where python3 > NUL 2>&1
if %errorlevel% equ 0 (
    set PYTHON=python3
    goto :python_found
)

echo [ERROR] Python not found! Please install Python 3.10+
echo [ERROR] Make sure "Add Python to PATH" is checked during installation.
pause
exit /b 1

:python_found
echo [INFO] Using Python: %PYTHON%

REM ---- Auto-create virtual environment ----
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        echo [ERROR] If using Microsoft Store Python, install from python.org instead.
        pause
        exit /b 1
    )
    echo [INFO] Virtual environment created.
)

REM ---- Activate virtual environment ----
if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo [ERROR] Virtual environment activate.bat not found.
    echo [ERROR] Try deleting .venv folder and run again.
    pause
    exit /b 1
)

REM ---- Auto-install dependencies ----
if not exist ".venv\.deps_installed" (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt -q
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        echo [ERROR] Check your network connection and try again.
        pause
        exit /b 1
    )
    type nul > ".venv\.deps_installed"
    echo [INFO] Dependencies installed successfully.
)

echo.
echo [INFO] Starting web server at http://127.0.0.1:5000
echo [INFO] Press Ctrl+C to stop.
echo.

python web_server.py %*

pause
