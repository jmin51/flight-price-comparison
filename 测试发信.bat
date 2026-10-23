@echo off
chcp 936 >nul
cd /d "%~dp0"
title 测试发信
echo ==========================================================
echo   测试邮件通道（会真的发一封测试邮件）
echo ==========================================================
echo.
"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" check_mail.py --send
echo.
pause
