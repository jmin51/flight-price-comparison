@echo off
chcp 936 >nul
cd /d "%~dp0"
title ¼ì²âÓÊ¼şÅäÖÃ
"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe" check_mail.py
echo.
pause
