@echo off
chcp 936 >nul
cd /d "%~dp0"
title 注册每 6 小时自动扫描

set TASK=FlightWatch_AutoScan
set RUNNER=%~dp0_silent_run.bat

echo 将注册 Windows 计划任务：%TASK%
echo 触发时间：每天 08:30 / 14:30 / 20:30 / 02:30（每 6 小时）
echo 运行时静默执行，不弹窗；只有价格出现新低或跌幅时才发邮件。
echo.

schtasks /Create /TN "%TASK%" /TR "%RUNNER%" /SC HOURLY /MO 6 /ST 08:30 /F

echo.
schtasks /Query /TN "%TASK%" /FO LIST
echo.
echo 注册完成。取消请双击「取消自动扫描.bat」
pause
