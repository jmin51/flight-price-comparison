@echo off
chcp 936 >nul
cd /d "%~dp0"
title 机票监控 - 快速扫描

echo ============================================================
echo   快速扫描：只查上次最优组合及前后 2 天的邻近日期，约 1 分钟
echo ============================================================
echo.

"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" run.py --mode quick %*

if errorlevel 1 (
  echo.
  echo [失败] 一个价格都没取到，未生成新报告。
  echo        请确认梯子已开启，浏览器能打开 google.com
) else (
  echo.
  echo 报告已生成并自动打开。
)
echo.
pause
