@echo off
REM Launch the VC-500W label designer web app (http://localhost:5001).
REM Port 5001 (not 5000): chgeo and other Flask apps default to 5000 and collide
REM -- opening localhost:5000 would show the WRONG app. See CLAUDE.md lesson #12.
REM Any extra args are forwarded / override, e.g.  run.bat -p 8080 -d
cd /d "%~dp0"
uv run labeler-web %*
