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
echo   (auth_check.py has unlocked the vault and started main.py)
echo.
echo.
echo ============================================================
echo   Done. Dashboard URL:
echo     http://127.0.0.1:8766/output/index.html
echo   Background service (auto-started, one process for both ports):
echo     page + live news + stock : http://127.0.0.1:8766   (news API: /api/news)
echo     position + watchlist mgr : http://127.0.0.1:8765
echo   Click any holding/watchlist row on the home page to open the
echo   stock terminal in the same window (back button returns home).
echo   Global news auto-refreshes every 5 minutes and updates
echo   the globe + news panel in place (no page reload).
echo   Closing this window stops all background services.
echo ============================================================
echo.
pause
goto :eof

:nopy
echo [!] Python launcher py not found. Please install Python 3.10+.
pause
