# TARS — One-Click Setup Script fuer Windows 11
$ErrorActionPreference = 'Stop'

Write-Host "=== TARS & Antigravity Bridge Setup ===" -ForegroundColor Cyan

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

if (-not (Test-Path ".venv")) {
    Write-Host "[1/3] Erstelle virtuelle Python-Umgebung (.venv)..." -ForegroundColor Yellow
    python -m venv .venv
} else {
    Write-Host "[1/3] Virtuelle Umgebung (.venv) existiert bereits." -ForegroundColor Green
}

$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
$venvPip = Join-Path $scriptDir ".venv\Scripts\pip.exe"

Write-Host "[2/3] Installiere Abhaengigkeiten aus requirements.txt..." -ForegroundColor Yellow
& $venvPip install --upgrade pip
& $venvPip install -r requirements.txt

Write-Host "[3/3] Pruefe Konfiguration..." -ForegroundColor Yellow
$cfgPath = Join-Path $env:USERPROFILE ".gemini\config\mcp_config.json"
if (Test-Path $cfgPath) {
    Write-Host "Gefunden: Antigravity mcp_config.json unter $cfgPath" -ForegroundColor Green
} else {
    Write-Host "Hinweis: Trage den Server bitte in deiner ~/.gemini/config/mcp_config.json ein (siehe mcp_config.example.json)." -ForegroundColor DarkYellow
}

Write-Host "Setup abgeschlossen! Starte TARS einfach mit: .\start_tars.bat" -ForegroundColor Cyan
