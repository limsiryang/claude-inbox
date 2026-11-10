@echo off
rem Inbox command entry point (Windows): Claude Code puts bin\ on PATH when the plugin is enabled.
setlocal
set "SCRIPT=%~dp0..\inbox\inbox.py"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY set "PY=python3"
%PY% "%SCRIPT%" %*
