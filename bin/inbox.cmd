@echo off
rem 收件匣指令入口（Windows）：plugin 啟用時 Claude Code 會把 bin\ 放進 PATH。
setlocal
set "SCRIPT=%~dp0..\inbox\inbox.py"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY set "PY=python3"
%PY% "%SCRIPT%" %*
