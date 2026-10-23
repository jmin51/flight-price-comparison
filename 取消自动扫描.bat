@echo off
chcp 936 >nul
schtasks /Delete /TN "FlightWatch_AutoScan" /F
echo.
echo 已取消自动扫描任务。
pause
