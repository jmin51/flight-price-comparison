@echo off
chcp 936 >nul
cd /d "%~dp0"
title 机票监控 - 全量扫描

echo ============================================================
echo   上海 PVG  -^>  圣保罗 GRU   卡塔尔航空 / 多哈中转
echo   全量扫描：111 个日期组合，预计 2 分钟
echo ============================================================
echo.

"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" run.py --mode full %*

if errorlevel 1 (
  echo.
  echo [失败] 一个价格都没取到，未生成新报告。
  echo        请确认梯子已开启，浏览器能打开 google.com
) else (
  echo.
  echo 报告已生成并自动打开。
)
pause
