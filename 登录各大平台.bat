@echo off
chcp 936 >nul
cd /d "%~dp0"
title 登录各大平台（只需一次）
echo ==========================================================
echo   打开真浏览器，用手机号+验证码登录携程/同程/飞猪
echo   登录态会保存在 data\browser_profile\ ，长期复用
echo ==========================================================
echo.
"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" login_setup.py
echo.
pause
