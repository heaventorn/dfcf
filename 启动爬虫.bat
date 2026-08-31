@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   Eastmoney Daily Market Crawler
echo ============================================================
echo.

REM ===== 密码验证 =====
set "CORRECT_PWD=0762"
set "INPUT_PWD="
for /f "delims=" %%i in ('powershell -NoProfile -Command "Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.Interaction]::InputBox('请输入访问密码','身份验证','')"') do set "INPUT_PWD=%%i"

if not defined INPUT_PWD (
    echo [!] 未输入密码或已点取消，程序退出。
    pause
    exit /b 1
)

if "%INPUT_PWD%"=="%CORRECT_PWD%" (
    echo [√] 密码验证通过，开始运行...
    echo.
) else (
    echo [!] 密码错误，程序退出。
    pause
    exit /b 1
)

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
