@echo off
REM Launch the VC-500W label designer web app (http://localhost:5000).
REM Any extra args are forwarded, e.g.  run.bat -p 8080 -d
cd /d "%~dp0"
uv run labler-web %*
