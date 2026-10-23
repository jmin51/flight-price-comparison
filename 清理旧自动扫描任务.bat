@echo off
chcp 936 >nul
cd /d "%~dp0"
title 机票监控 - 清理旧的自动扫描任务

echo 本程序只在手动点击「一键扫描 / 快速扫描」时抓价，不再做定时任务。
echo 这个脚本只是备用的清理工具：如果哪台机器的旧任务还在，双击它删掉。
echo.

schtasks /Delete /TN "FlightWatch_AutoScan" /F
echo.
echo 清理完成（提示"找不到任务"说明本来就没有，属正常）。
pause
