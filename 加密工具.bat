@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================================
echo   DFCF 加密保险库 · 小工具
echo   （第一次用请先选 2 创建保险库；换机器先选 5 导出备份）
echo ============================================================
echo.
where py >nul 2>nul
if errorlevel 1 goto nopy
py -3.12 secure_store.py %*
echo.
pause
goto :eof

:nopy
echo [!] Python launcher py not found. Please install Python 3.10+.
pause
