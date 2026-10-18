@echo off
title TARS — Gemini Live Bridge zu Antigravity
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "gemini_live_app.py"
) else (
    python "gemini_live_app.py"
)
pause
