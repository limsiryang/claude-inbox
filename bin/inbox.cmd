@echo off
rem Inbox command entry point (Windows): Claude Code puts bin\ on PATH when the plugin is enabled.
rem Each candidate is test-run, because python/python3 may be Microsoft Store placeholders
rem that exist on PATH but only print an install hint.
setlocal
set "SCRIPT=%~dp0..\inbox\inbox.py"
set "PY="
py -3 -c "" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "" >nul 2>nul && set "PY=python"
if not defined PY python3 -c "" >nul 2>nul && set "PY=python3"
if not defined PY (
  echo inbox: no working Python 3.9+ found ^(tried py -3, python, python3^) 1>&2
  exit /b 127
)
%PY% "%SCRIPT%" %*
