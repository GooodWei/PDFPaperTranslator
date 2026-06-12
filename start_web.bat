@echo off
chcp 65001 > NUL
cd /d "%~dp0"
echo.
echo ==============================================
echo   PDFPaperTranslator - Web Server
echo ==============================================
echo.

REM Try python, py, python3 in order
where python > NUL 2>&1 && (
    python web_server.py %*
    goto :end
)
where py > NUL 2>&1 && (
    py web_server.py %*
    goto :end
)
where python3 > NUL 2>&1 && (
    python3 web_server.py %*
    goto :end
)
echo [ERROR] Python not found! Please install Python 3.

:end
pause
