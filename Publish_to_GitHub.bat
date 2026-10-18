@echo off
setlocal enabledelayedexpansion
title TARS — Auf GitHub veroeffentlichen
cd /d "%~dp0"

echo ===================================================
echo     TARS - Auf GitHub veroeffentlichen
echo ===================================================
echo.

git --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Fehler: Git ist nicht auf dem System installiert.
    pause
    exit /b 1
)

gh --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo Fehler: GitHub CLI (gh) ist nicht installiert.
    pause
    exit /b 1
)

echo [1/4] Bereite lokales Git-Repository vor...
if not exist ".git" (
    git init -b main
)
git config user.name >nul 2>&1
if %ERRORLEVEL% neq 0 (
    git config user.name "TARS User"
    git config user.email "tars@local"
)

git add .
git commit -m "Initial commit: TARS Gemini Live Voice & Antigravity IDE Bridge" >nul 2>&1

echo [2/4] Pruefe GitHub-Anmeldung...
gh auth status >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo Bitte bestaetige den Login im Browser...
    gh auth login --hostname github.com --git-protocol https --web
)

echo.
echo [3/4] Erstelle Repository 'tars-antigravity-live-bridge' auf GitHub...
gh repo create tars-antigravity-live-bridge --public --source=. --remote=origin --push --description "TARS - Bidirectional Gemini Live Voice, Screen & Command Bridge for Google Antigravity IDE"

if %ERRORLEVEL% equ 0 (
    echo.
    echo ===================================================
    echo     Erfolgreich auf GitHub veroeffentlicht!
    echo ===================================================
    gh repo view --web
) else (
    echo.
    echo Falls das Repository bereits existiert, versuche direkten Push:
    git push -u origin main
    gh repo view --web
)

echo.
pause
