@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Git 可视化工具

rem 先验证 python 真的可用（防止 Windows 商店的占位 python.exe 骗过 where 检查）
python --version >nul 2>nul
if errorlevel 1 goto try_py

python git_visual_tool.py
if errorlevel 1 pause
goto end

:try_py
py --version >nul 2>nul
if errorlevel 1 goto no_python

py git_visual_tool.py
if errorlevel 1 pause
goto end

:no_python
echo [错误] 未找到 Python，请先安装 Python 3（安装时勾选 "Add Python to PATH"）。
echo 下载地址: https://www.python.org/downloads/windows/
echo.
echo 同时需要安装 Git: https://git-scm.com/download/win
pause

:end
