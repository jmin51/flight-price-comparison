@echo off
chcp 936 >nul
cd /d "%~dp0"
title 核价测试（看浏览器窗口）
echo ==========================================================
echo   扫描 -^> 最低价 -^> 开浏览器去携程/同程/飞猪抓真实价
echo   浏览器窗口会显示出来，能看清它卡在哪一步
echo ==========================================================
echo.
"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" run.py --mode quick --verify --verify-headed --no-mail --no-open
echo.
echo 报告在 reports\latest.html
pause
