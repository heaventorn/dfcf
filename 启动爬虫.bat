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
echo   两页 + 左侧任务栏，同一个窗口里点着切换：
echo     大盘总览    http://127.0.0.1:8766/output/index.html
echo     自选与持仓  http://127.0.0.1:8766/output/portfolio.html
echo     个股详情    /stock?code=600941   (任务栏里也有)
echo     资产配置桶  策略执行台  http://127.0.0.1:8766/strategy
echo     全球眼      http://127.0.0.1:5180  (3D 地球，已关掉遮罩与特效文字)
echo.
echo   后台服务（自动拉起，关掉本窗口就一起停）：
echo     大盘/新闻/个股   http://127.0.0.1:8766   (快讯 API: /api/news)
echo     持仓/自选管理    http://127.0.0.1:8765
echo     全球眼 3D 地球   http://127.0.0.1:5180
echo   财经快讯每 5 分钟自动刷新；点持仓/自选/涨幅榜任意一行进个股页。
echo ============================================================
echo.
pause
goto :eof

:nopy
echo [!] Python launcher py not found. Please install Python 3.10+.
pause
