@echo off
chcp 936 >nul
cd /d "%~dp0"
title 机票监控 - 控制面板（关闭此窗口即停止服务）

echo ============================================================
echo   机票监控 · 控制面板
echo   正在检查是否已有面板在运行…
echo ============================================================
echo.

"C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" panel.py %*
set RC=%ERRORLEVEL%

if "%RC%"=="3" (
  echo 已复用正在运行的面板，本窗口 3 秒后自动关闭。
  timeout /t 3 >nul
  exit /b 0
)

echo.
echo [面板已退出]
echo 若上面提示缺 flask，执行下面这行安装：
echo   "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m pip install flask
pause
