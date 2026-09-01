@echo off
cd /d "%~dp0"
echo ============================================================
echo   Eastmoney Daily Market Crawler
echo ============================================================
echo.

REM ===== 双密码验证（访问密码 + 本地pwd.key二级密钥）=====
where py >nul 2>nul
if errorlevel 1 goto nopy
py -3.12 auth_check.py
if errorlevel 1 (
    echo [!] 密码验证失败或已取消，程序退出。
    pause
    exit /b 1
)
echo [√] 双密码验证通过，开始运行...
echo.

where py >nul 2>nul
if errorlevel 1 goto nopy
py -3.12 main.py

echo.
echo ============================================================
echo   爬虫完成，正在启动持仓管理服务...
echo ============================================================
echo.
echo   访问地址: http://127.0.0.1:8765
echo   关闭此窗口将停止持仓管理服务
echo.
py -3.12 position_manager.py
goto done
:nopy
echo [!] Python launcher (py) not found. Please install Python 3.10+.
:done
pause
