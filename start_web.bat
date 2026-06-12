@echo off
chcp 65001 > NUL
cd /d "%~dp0"

echo.
echo ==============================================
echo   PDFPaperTranslator - Web Server
echo ==============================================
echo.

REM ---- 自动检测 Python ----
set PYTHON=
where python > NUL 2>&1 && set PYTHON=python
where py > NUL 2>&1 && if "%PYTHON%"=="" set PYTHON=py
where python3 > NUL 2>&1 && if "%PYTHON%"=="" set PYTHON=python3
if "%PYTHON%"=="" (
    echo [ERROR] Python not found! Please install Python 3.10+
    pause
    exit /b 1
)

REM ---- 自动创建虚拟环境 ----
if not exist ".venv\Scripts\python.exe" (
    echo [INFO] Creating virtual environment...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

REM ---- 激活虚拟环境 ----
call ".venv\Scripts\activate.bat"

REM ---- 自动安装依赖 ----
if not exist ".venv\.deps_installed" (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt -q
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    type nul > ".venv\.deps_installed"
    echo [INFO] Dependencies installed.
)

echo [INFO] Starting web server at http://127.0.0.1:5000
echo [INFO] Press Ctrl+C to stop.
echo.

python web_server.py %*

pause
