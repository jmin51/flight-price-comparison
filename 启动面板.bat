@echo off
chcp 936 >nul
cd /d "%~dp0"
title 机票监控 - 控制面板（关闭此窗口即停止服务）

echo ============================================================
echo   机票监控 · 控制面板
echo   浏览器会自动打开 http://127.0.0.1:5001/
echo   关闭这个窗口 = 停止服务
echo ============================================================
echo.

"C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" panel.py %*

echo.
echo [面板已退出]
echo 若上面提示缺 flask，执行下面这行安装：
echo   "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe" -m pip install flask
pause
