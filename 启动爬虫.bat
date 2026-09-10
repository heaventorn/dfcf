@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   Eastmoney Daily Market - Build Home Dashboard
echo ============================================================
echo.
where py >nul 2>nul
if errorlevel 1 goto nopy
py -3.12 auth_check.py
if errorlevel 1 (
    echo [!] Auth check failed or expired. Exit.
    pause
    exit /b 1
)
echo [OK] Auth passed. Collecting market data...
echo.
py -3.12 main.py
echo.
echo ============================================================
echo   Done. Dashboard URL:
echo     http://127.0.0.1:8766/output/index.html
echo   Background services (auto-started, keep running):
echo     page server  : http://127.0.0.1:8766
echo     position mgr : http://127.0.0.1:8765
echo ============================================================
echo.
pause
goto :eof

:nopy
echo [!] Python launcher py not found. Please install Python 3.10+.
pause
