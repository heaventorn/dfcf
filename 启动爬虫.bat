@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   Eastmoney Daily Market Crawler - starting...
echo ============================================================
echo.
where python >nul 2>nul
if errorlevel 1 goto nopy
python main.py
goto done
:nopy
echo [!] Python not found. Please install Python 3.10+.
:done
echo.
echo ============================================================
echo   Done. You may close this window.
echo   HTML report is in the "output" folder - open it in browser.
echo ============================================================
pause