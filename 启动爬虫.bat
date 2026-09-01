@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   Eastmoney Daily Market Crawler
echo ============================================================
echo.

REM ===== 双密码验证（访问密码 + 本地pwd.key二级密钥）=====
where python >nul 2>nul
if errorlevel 1 goto nopy
python auth_check.py
if errorlevel 1 (
    echo [!] 密码验证失败或已取消，程序退出。
    pause
    exit /b 1
)
echo [√] 双密码验证通过，开始运行...
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
