import asyncio
import base64
import ctypes
import json
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

import uvicorn
from google import genai
from google.genai import types
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

BRIDGE_LOG_PATH = Path(__file__).parent / "tars_bridge_log.json"

# Globale Referenzen fuer die aktive TARS-Live-Sitzung (damit Antigravity direkt ueber TARS sprechen kann)
ACTIVE_WS: Optional[WebSocket] = None
ACTIVE_GEMINI_SESSION = None


def append_bridge_log(sender: str, text: str) -> None:
    try:
        items = []
        if BRIDGE_LOG_PATH.exists():
            items = json.loads(BRIDGE_LOG_PATH.read_text(encoding="utf-8"))
        items.append(
            {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "sender": sender,
                "text": text,
            }
        )
        items = items[-50:]
        BRIDGE_LOG_PATH.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def get_recent_bridge_logs(limit: int = 8) -> str:
    try:
        if not BRIDGE_LOG_PATH.exists():
            return "Noch keine Eintraege im TARS-Antigravity-Protokoll."
        items = json.loads(BRIDGE_LOG_PATH.read_text(encoding="utf-8"))
        recent = items[-limit:]
        lines = [f"[{x['timestamp']}] {x['sender']}: {x['text']}" for x in recent]
        return "\n".join(lines) if lines else "Keine Eintraege."
    except Exception as e:
        return f"Fehler beim Lesen des Bridge-Protokolls: {e}"


def send_prompt_to_antigravity_window(prompt: str, submit_immediately: bool = True) -> str:
    """Fokussiert das Antigravity-Fenster auf Windows 11, fuegt den Prompt per Zwischenablage ein und drueckt Enter."""
    clean_prompt = (prompt or "").strip()
    if not clean_prompt:
        return "Leerer Prompt."

    append_bridge_log("TARS -> Antigravity", clean_prompt)

    ps_script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$text = [System.Text.Encoding]::UTF8.GetString([System.Convert]::FromBase64String('__B64_TEXT__'))
Set-Clipboard -Value $text

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Focus {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@

$procs = Get-Process -Name "Antigravity" -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 }
if (-not $procs) {
    $procs = Get-Process | Where-Object { $_.MainWindowTitle -like "*Antigravity*" -and $_.MainWindowTitle -notlike "*TARS*" }
}

if ($procs) {
    $hwnd = $procs[0].MainWindowHandle
    if ([Win32Focus]::IsIconic($hwnd)) {
        [Win32Focus]::ShowWindow($hwnd, 9) | Out-Null
        Start-Sleep -Milliseconds 150
    }
    # Dummy ALT press to bypass Windows Foreground Lock
    [Win32Focus]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
    [Win32Focus]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
    [Win32Focus]::SetForegroundWindow($hwnd) | Out-Null
    Start-Sleep -Milliseconds 220

    # Ctrl+V
    [Win32Focus]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero)
    [Win32Focus]::keybd_event(0x56, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 60
    [Win32Focus]::keybd_event(0x56, 0, 2, [UIntPtr]::Zero)
    [Win32Focus]::keybd_event(0x11, 0, 2, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 150

    if ('__SUBMIT__' -eq '1') {
        # Enter
        [Win32Focus]::keybd_event(0x0D, 0, 0, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 40
        [Win32Focus]::keybd_event(0x0D, 0, 2, [UIntPtr]::Zero)
    }
    Write-Output "OK: An Antigravity-Fenster uebergeben."
} else {
    Write-Output "HINWEIS: Antigravity-Fenster nicht direkt gefunden, Text liegt in der Zwischenablage und im Bridge-Log."
}
"""
    b64_text = base64.b64encode(clean_prompt.encode("utf-8")).decode("ascii")
    ps_script = ps_script.replace("__B64_TEXT__", b64_text).replace(
        "__SUBMIT__", "1" if submit_immediately else "0"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Sta", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        out = ((res.stdout or "") + (res.stderr or "")).strip()
        return out or "An Antigravity gesendet."
    except Exception as e:
        return f"Fehler bei Uebergabe an Antigravity: {e}"


def load_api_key() -> str:
    env_key = os.environ.get("GEMINI_API_KEY")
    if env_key:
        return env_key
    cfg_path = Path.home() / ".gemini" / "config" / "mcp_config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            return (
                data.get("mcpServers", {})
                .get("google-tts", {})
                .get("env", {})
                .get("GEMINI_API_KEY", "")
            )
        except Exception:
            pass
    return ""


HTML_PAGE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TARS — Sprach-Bruecke zu Antigravity</title>
<style>
  :root {
    --bg: #090d16;
    --panel: #111827;
    --border: #1f2937;
    --accent: #3b82f6;
    --accent-glow: rgba(59, 130, 246, 0.35);
    --green: #10b981;
    --purple: #8b5cf6;
    --red: #ef4444;
    --text: #f3f4f6;
    --muted: #9ca3af;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
  }
  header {
    padding: 14px 22px;
    background: var(--panel);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .status-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--muted);
    transition: all 0.25s ease;
  }
  .status-dot.live {
    background: var(--green);
    box-shadow: 0 0 12px var(--green);
  }
  .brand h1 {
    font-size: 17px;
    font-weight: 600;
    letter-spacing: 0.4px;
  }
  .controls {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  select, button {
    background: #1f2937;
    color: var(--text);
    border: 1px solid #374151;
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.2s ease;
  }
  button:hover {
    border-color: var(--accent);
    background: #283548;
  }
  button.primary {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
  }
  button.primary.active {
    background: var(--red);
    border-color: var(--red);
  }
  button.screen-btn.active {
    background: var(--green);
    border-color: var(--green);
    color: #052e16;
    font-weight: 600;
  }
  main {
    flex: 1;
    display: grid;
    grid-template-columns: 330px 1fr;
    gap: 16px;
    padding: 16px 22px;
    overflow: hidden;
  }
  .visualizer-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: space-between;
  }
  .orb-wrap {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    margin: auto 0;
  }
  .orb {
    width: 125px;
    height: 125px;
    border-radius: 50%;
    background: radial-gradient(circle at 30% 30%, #60a5fa, #1d4ed8);
    box-shadow: 0 0 30px var(--accent-glow);
    transition: transform 0.08s ease-out, box-shadow 0.15s ease;
  }
  .orb.speaking {
    background: radial-gradient(circle at 30% 30%, #34d399, #059669);
    box-shadow: 0 0 45px rgba(16, 185, 129, 0.5);
  }
  .state-label {
    font-size: 13px;
    color: var(--muted);
    text-align: center;
    line-height: 1.45;
  }
  .timer-badge {
    margin-top: 6px;
    padding: 6px 12px;
    border-radius: 6px;
    background: #090d16;
    border: 1px solid var(--border);
    font-size: 12px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }
  .hint-box {
    width: 100%;
    padding: 12px;
    border-radius: 8px;
    background: #090d16;
    border: 1px solid var(--border);
    font-size: 12px;
    color: var(--muted);
    line-height: 1.45;
  }
  .hint-box strong {
    color: var(--text);
    display: block;
    margin-bottom: 4px;
  }
  video#screenPreview {
    width: 100%;
    max-height: 140px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: #000;
    object-fit: contain;
    display: none;
    margin-top: 8px;
  }
  .chat-card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .transcript {
    flex: 1;
    padding: 18px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .msg {
    max-width: 84%;
    padding: 11px 15px;
    border-radius: 10px;
    font-size: 14px;
    line-height: 1.5;
    position: relative;
  }
  .msg.user {
    align-self: flex-end;
    background: #1e3a8a;
    border: 1px solid #2563eb;
  }
  .msg.ai {
    align-self: flex-start;
    background: #1f2937;
    border: 1px solid #374151;
  }
  .msg.antigravity {
    align-self: flex-start;
    background: #2e1065;
    border: 1px solid var(--purple);
    color: #ede9fe;
  }
  .msg.system {
    align-self: center;
    background: transparent;
    color: var(--muted);
    font-size: 12px;
    border: 1px dashed #374151;
  }
  .relay-btn {
    display: inline-block;
    margin-top: 6px;
    padding: 3px 8px;
    font-size: 11px;
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.2);
    border-radius: 4px;
    color: #cbd5e1;
    cursor: pointer;
  }
  .relay-btn:hover {
    background: var(--accent);
    color: #fff;
  }
  .text-bar {
    display: flex;
    gap: 10px;
    padding: 14px;
    border-top: 1px solid var(--border);
    background: #090d16;
  }
  .text-bar input {
    flex: 1;
    background: #1f2937;
    border: 1px solid #374151;
    color: var(--text);
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 14px;
    outline: none;
  }
  .text-bar input:focus {
    border-color: var(--accent);
  }
</style>
</head>
<body>
  <header>
    <div class="brand">
      <div id="statusDot" class="status-dot"></div>
      <h1>TARS — Sprach-Assistent zwischen dir & Antigravity</h1>
    </div>
    <div class="controls">
      <select id="relayModeSelect" title="Wie soll TARS mit Antigravity interagieren?">
        <option value="smart" selected>Modus: TARS als Vermittler (Sendet auf Zuruf an Antigravity)</option>
        <option value="direct">Modus: Direkt-Diktat (Jeden gesprochenen Satz an Antigravity senden)</option>
      </select>
      <select id="timeoutSelect" title="Inaktivitaets-Zeitfenster bis zum Beenden" onchange="resetInactivityTimer()">
        <option value="120" selected>Stille-Timeout: 120 Sek. (2 Min.)</option>
        <option value="60">Stille-Timeout: 60 Sek. (1 Min.)</option>
        <option value="300">Stille-Timeout: 300 Sek. (5 Min.)</option>
        <option value="0">Kein Timeout (Dauerhaft offen)</option>
      </select>
      <select id="voiceSelect" title="TARS Stimme waehlen">
        <option value="Charon" selected>Stimme: Charon (TARS Ruhig & Markant)</option>
        <option value="Puck">Stimme: Puck (Kollegial)</option>
        <option value="Fenrir">Stimme: Fenrir (Tief)</option>
        <option value="Kore">Stimme: Kore (Klar)</option>
        <option value="Aoede">Stimme: Aoede (Warm)</option>
      </select>
      <button id="screenBtn" class="screen-btn" onclick="toggleScreenShare()" disabled>Bildschirm teilen</button>
      <button id="startBtn" class="primary" onclick="toggleSession()">TARS starten</button>
    </div>
  </header>

  <main>
    <div class="visualizer-card">
      <div class="orb-wrap">
        <div id="orb" class="orb"></div>
        <div id="stateLabel" class="state-label">Bereit. Klicke auf „TARS starten“, um mit TARS und Antigravity per Sprache zu arbeiten.</div>
        <div id="timerBadge" class="timer-badge">Standby</div>
      </div>
      <video id="screenPreview" autoplay muted playsinline></video>
      <canvas id="screenCanvas" style="display:none;"></canvas>
      <div class="hint-box">
        <strong>So funktioniert TARS:</strong>
        1. Sprich frei mit TARS ueber deine Ideen oder Aufgaben.<br>
        2. Sag einfach: <em>„TARS, gib das an Antigravity weiter“</em> oder <em>„Sag Antigravity, er soll...“</em> — TARS tippt den Auftrag direkt ins Antigravity-Chatfenster und schickt ihn ab.<br>
        3. Antworten von Antigravity liest TARS dir hier sofort live vor.
      </div>
    </div>

    <div class="chat-card">
      <div id="transcript" class="transcript">
        <div class="msg system">TARS Live-Bridge bereit. Mindest-Zeitfenster bei Stille: 60–120 Sekunden (oder dauerhaft offen).</div>
      </div>
      <div class="text-bar">
        <input id="textInput" type="text" placeholder="Nachricht an TARS oder direkt an Antigravity..." onkeydown="if(event.key==='Enter') sendText()">
        <button onclick="sendText()">An TARS</button>
        <button onclick="sendDirectInputToAntigravity()">Direkt an Antigravity</button>
      </div>
    </div>
  </main>

<script>
let ws = null;
let audioCtx = null;
let micStream = null;
let micProcessor = null;
let screenStream = null;
let screenInterval = null;
let inactivityCheckInterval = null;
let lastActivityTimestamp = Date.now();
let isRunning = false;
let nextPlayTime = 0;
let activeSources = [];
let currentUserBubble = null;
let currentAiBubble = null;
let currentTurnUserText = "";

function resetInactivityTimer() {
  lastActivityTimestamp = Date.now();
  updateTimerBadge();
}

function updateTimerBadge() {
  const badge = document.getElementById('timerBadge');
  if (!isRunning) {
    badge.textContent = 'Standby';
    return;
  }
  const maxIdleSec = parseInt(document.getElementById('timeoutSelect').value, 10);
  if (maxIdleSec === 0) {
    badge.textContent = 'Dialog dauerhaft offen (Kein Timeout)';
    return;
  }
  if (activeSources.length > 0) {
    lastActivityTimestamp = Date.now();
    badge.textContent = 'TARS spricht... (Timer pausiert)';
    return;
  }
  const elapsedSec = Math.floor((Date.now() - lastActivityTimestamp) / 1000);
  const remainingSec = Math.max(0, maxIdleSec - elapsedSec);
  badge.textContent = `Offenes Zeitfenster bei Stille: noch ${remainingSec} s`;
  if (remainingSec <= 0) {
    appendMsg('system', `Keine Eingabe seit ${maxIdleSec} Sekunden — TARS-Sitzung wurde automatisch beendet.`);
    stopSession();
  }
}

function relayToAntigravity(text) {
  if (!text || !text.trim()) return;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'relay_to_antigravity', prompt: text.trim() }));
  }
}

function appendMsg(role, text, streamBubble = false) {
  const box = document.getElementById('transcript');
  if (streamBubble) {
    if (role === 'user') {
      if (!currentUserBubble) {
        currentUserBubble = document.createElement('div');
        currentUserBubble.className = 'msg user';
        const span = document.createElement('div');
        span.className = 'bubble-text';
        currentUserBubble.appendChild(span);
        box.appendChild(currentUserBubble);
      }
      const span = currentUserBubble.querySelector('.bubble-text');
      span.textContent += text;
      box.scrollTop = box.scrollHeight;
      return;
    }
    if (role === 'ai') {
      if (!currentAiBubble) {
        currentAiBubble = document.createElement('div');
        currentAiBubble.className = 'msg ai';
        const span = document.createElement('div');
        span.className = 'bubble-text';
        currentAiBubble.appendChild(span);
        box.appendChild(currentAiBubble);
      }
      const span = currentAiBubble.querySelector('.bubble-text');
      span.textContent += text;
      box.scrollTop = box.scrollHeight;
      return;
    }
  }
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  div.textContent = text;
  box.appendChild(div);
  box.scrollTop = box.scrollHeight;
}

function finalizeUserBubbleWithRelayButton() {
  if (currentUserBubble) {
    const span = currentUserBubble.querySelector('.bubble-text');
    const fullText = span ? span.textContent.trim() : '';
    if (fullText && !currentUserBubble.querySelector('.relay-btn')) {
      const btn = document.createElement('button');
      btn.className = 'relay-btn';
      btn.textContent = 'An Antigravity-Chat senden';
      btn.onclick = () => relayToAntigravity(fullText);
      currentUserBubble.appendChild(btn);
    }
  }
}

function downsampleTo16k(inputBuffer, inputSampleRate) {
  if (inputSampleRate === 16000) return inputBuffer;
  const ratio = inputSampleRate / 16000;
  const outputLength = Math.round(inputBuffer.length / ratio);
  const output = new Float32Array(outputLength);
  for (let i = 0; i < outputLength; i++) {
    const srcIndex = i * ratio;
    const idx = Math.floor(srcIndex);
    const frac = srcIndex - idx;
    const s0 = inputBuffer[idx] || 0;
    const s1 = inputBuffer[idx + 1] || s0;
    output[i] = s0 + frac * (s1 - s0);
  }
  return output;
}

function floatTo16BitPCM(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  return int16;
}

function stopPlaybackQueue() {
  for (const src of activeSources) {
    try { src.stop(); } catch (e) {}
  }
  activeSources = [];
  if (audioCtx) {
    nextPlayTime = audioCtx.currentTime;
  }
  document.getElementById('orb').classList.remove('speaking');
}

function playPCM24k(base64Data) {
  if (!audioCtx) return;
  resetInactivityTimer();
  const binary = atob(base64Data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  const int16 = new Int16Array(bytes.buffer);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) {
    float32[i] = int16[i] / 32768.0;
  }

  const buffer = audioCtx.createBuffer(1, float32.length, 24000);
  buffer.getChannelData(0).set(float32);

  const source = audioCtx.createBufferSource();
  source.buffer = buffer;
  source.connect(audioCtx.destination);

  const now = audioCtx.currentTime;
  if (nextPlayTime < now) nextPlayTime = now;
  source.start(nextPlayTime);
  nextPlayTime += buffer.duration;

  document.getElementById('orb').classList.add('speaking');
  activeSources.push(source);
  source.onended = () => {
    activeSources = activeSources.filter(s => s !== source);
    resetInactivityTimer();
    if (activeSources.length === 0) {
      document.getElementById('orb').classList.remove('speaking');
    }
  };
}

async function toggleSession() {
  if (isRunning) {
    stopSession();
    return;
  }
  try {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true
      }
    });

    const voice = document.getElementById('voiceSelect').value;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${proto}//${location.host}/ws?voice=${encodeURIComponent(voice)}`);
    ws.binaryType = 'arraybuffer';

    ws.onopen = () => {
      isRunning = true;
      resetInactivityTimer();
      if (inactivityCheckInterval) clearInterval(inactivityCheckInterval);
      inactivityCheckInterval = setInterval(updateTimerBadge, 1000);

      document.getElementById('statusDot').classList.add('live');
      const btn = document.getElementById('startBtn');
      btn.textContent = 'Verbindung trennen';
      btn.classList.add('active');
      document.getElementById('screenBtn').disabled = false;
      document.getElementById('stateLabel').textContent = 'TARS ist online. Sprich frei — ich vermittle direkt zwischen dir und Antigravity.';
      startMicStreaming();
    };

    ws.onmessage = (evt) => {
      if (typeof evt.data === 'string') {
        const msg = JSON.parse(evt.data);
        if (msg.type === 'audio') {
          resetInactivityTimer();
          playPCM24k(msg.data);
        } else if (msg.type === 'input_transcript') {
          resetInactivityTimer();
          currentAiBubble = null;
          currentTurnUserText += msg.text;
          appendMsg('user', msg.text, true);
        } else if (msg.type === 'output_transcript') {
          resetInactivityTimer();
          finalizeUserBubbleWithRelayButton();
          if (currentTurnUserText.trim() && document.getElementById('relayModeSelect').value === 'direct') {
            relayToAntigravity(currentTurnUserText.trim());
          }
          currentTurnUserText = "";
          currentUserBubble = null;
          appendMsg('ai', msg.text, true);
        } else if (msg.type === 'turn_complete') {
          resetInactivityTimer();
          finalizeUserBubbleWithRelayButton();
          currentTurnUserText = "";
          currentUserBubble = null;
          currentAiBubble = null;
        } else if (msg.type === 'interrupted') {
          resetInactivityTimer();
          stopPlaybackQueue();
          currentAiBubble = null;
        } else if (msg.type === 'antigravity_msg') {
          resetInactivityTimer();
          appendMsg('antigravity', '[ANTIGRAVITY -> TARS]: ' + msg.text);
        } else if (msg.type === 'tool_log') {
          resetInactivityTimer();
          appendMsg('system', msg.text);
        } else if (msg.type === 'error') {
          appendMsg('system', 'Hinweis: ' + msg.text);
        }
      }
    };

    ws.onclose = () => {
      if (isRunning) {
        stopSession();
      }
    };
  } catch (err) {
    appendMsg('system', 'Mikrofon-Zugriff fehlgeschlagen: ' + err.message);
  }
}

function startMicStreaming() {
  const micSource = audioCtx.createMediaStreamSource(micStream);
  micProcessor = audioCtx.createScriptProcessor(4096, 1, 1);
  const silentGain = audioCtx.createGain();
  silentGain.gain.value = 0;

  micSource.connect(micProcessor);
  micProcessor.connect(silentGain);
  silentGain.connect(audioCtx.destination);

  micProcessor.onaudioprocess = (e) => {
    if (!isRunning || !ws || ws.readyState !== WebSocket.OPEN) return;
    const input = e.inputBuffer.getChannelData(0);

    let sum = 0;
    for (let i = 0; i < input.length; i += 8) sum += input[i] * input[i];
    const rms = Math.sqrt(sum / (input.length / 8));
    if (rms > 0.02) {
      resetInactivityTimer();
    }
    const scale = 1 + Math.min(0.35, rms * 4.5);
    document.getElementById('orb').style.transform = `scale(${scale.toFixed(3)})`;

    const downsampled = downsampleTo16k(input, audioCtx.sampleRate);
    const pcm16 = floatTo16BitPCM(downsampled);
    ws.send(pcm16.buffer);
  };
}

async function toggleScreenShare() {
  const btn = document.getElementById('screenBtn');
  const video = document.getElementById('screenPreview');
  if (screenStream) {
    clearInterval(screenInterval);
    screenInterval = null;
    screenStream.getTracks().forEach(t => t.stop());
    screenStream = null;
    video.style.display = 'none';
    btn.textContent = 'Bildschirm teilen';
    btn.classList.remove('active');
    appendMsg('system', 'Bildschirmfreigabe beendet.');
    return;
  }
  try {
    screenStream = await navigator.mediaDevices.getDisplayMedia({
      video: { frameRate: 2 },
      audio: false
    });
    video.srcObject = screenStream;
    video.style.display = 'block';
    btn.textContent = 'Bildschirm aktiv';
    btn.classList.add('active');
    appendMsg('system', 'Bildschirmfreigabe aktiv. TARS sieht jetzt deinen Bildschirm.');

    const canvas = document.getElementById('screenCanvas');
    const ctx = canvas.getContext('2d');

    screenInterval = setInterval(() => {
      if (!ws || ws.readyState !== WebSocket.OPEN || !video.videoWidth) return;
      const scale = Math.min(1, 1024 / video.videoWidth);
      canvas.width = Math.round(video.videoWidth * scale);
      canvas.height = Math.round(video.videoHeight * scale);
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const dataUrl = canvas.toDataURL('image/jpeg', 0.65);
      const b64 = dataUrl.split(',')[1];
      ws.send(JSON.stringify({ type: 'screen_frame', data: b64 }));
    }, 1500);

    screenStream.getVideoTracks()[0].onended = () => {
      if (screenStream) toggleScreenShare();
    };
  } catch (err) {
    appendMsg('system', 'Bildschirmfreigabe abgebrochen: ' + err.message);
  }
}

function sendText() {
  const inp = document.getElementById('textInput');
  const txt = inp.value.trim();
  if (!txt || !ws || ws.readyState !== WebSocket.OPEN) return;
  resetInactivityTimer();
  appendMsg('user', txt);
  ws.send(JSON.stringify({ type: 'text', text: txt }));
  inp.value = '';
}

function sendDirectInputToAntigravity() {
  const inp = document.getElementById('textInput');
  const txt = inp.value.trim();
  if (!txt) return;
  resetInactivityTimer();
  appendMsg('system', 'Direkt an Antigravity gesendet: ' + txt);
  fetch('/api/tars/send_to_antigravity', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt: txt, submit: true })
  });
  inp.value = '';
}

function stopSession() {
  isRunning = false;
  if (inactivityCheckInterval) { clearInterval(inactivityCheckInterval); inactivityCheckInterval = null; }
  stopPlaybackQueue();
  if (screenInterval) { clearInterval(screenInterval); screenInterval = null; }
  if (screenStream) { screenStream.getTracks().forEach(t => t.stop()); screenStream = null; }
  if (micProcessor) { try { micProcessor.disconnect(); } catch (e) {} micProcessor = null; }
  if (micStream) { micStream.getTracks().forEach(t => t.stop()); micStream = null; }
  if (audioCtx) { try { audioCtx.close(); } catch (e) {} audioCtx = null; }
  if (ws) { try { ws.close(); } catch (e) {} ws = null; }

  document.getElementById('statusDot').classList.remove('live');
  const btn = document.getElementById('startBtn');
  btn.textContent = 'TARS starten';
  btn.classList.remove('active');
  const sBtn = document.getElementById('screenBtn');
  sBtn.disabled = true;
  sBtn.classList.remove('active');
  sBtn.textContent = 'Bildschirm teilen';
  document.getElementById('screenPreview').style.display = 'none';
  document.getElementById('stateLabel').textContent = 'Verbindung getrennt.';
  document.getElementById('timerBadge').textContent = 'Standby';
}
</script>
</body>
</html>
"""


def execute_local_powershell(command: str) -> str:
    """Fuehrt einen PowerShell-Befehl lokal auf dem Windows-System aus."""
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=20,
        )
        out = (res.stdout or "") + (res.stderr or "")
        return out.strip()[:3000] or "Befehl erfolgreich ausgefuehrt (keine Ausgabe)."
    except Exception as e:
        return f"Fehler bei Ausfuehrung: {e}"


async def homepage(request: Request):
    return HTMLResponse(HTML_PAGE)


async def api_announce_from_antigravity(request: Request):
    """Endpunkt, ueber den Antigravity Statusberichte und Antworten direkt live durch TARS sprechen laesst."""
    global ACTIVE_WS, ACTIVE_GEMINI_SESSION
    try:
        body = await request.json()
    except Exception:
        body = {}
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty text"}, status_code=400)

    append_bridge_log("Antigravity -> TARS", text)

    if ACTIVE_WS is not None and ACTIVE_GEMINI_SESSION is not None:
        try:
            await ACTIVE_WS.send_text(
                json.dumps({"type": "antigravity_msg", "text": text})
            )
            await ACTIVE_GEMINI_SESSION.send_realtime_input(
                text=(
                    f"[SYSTEM-MELDUNG VON ANTIGRAVITY AN TARS]: Antigravity hat folgende Antwort "
                    f"fuer den Nutzer bereitgestellt. Lies oder fasse sie dem Nutzer jetzt direkt auf Deutsch vor: {text}"
                )
            )
            return JSONResponse({"ok": True, "delivered": "live_stream"})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})

    return JSONResponse({"ok": True, "delivered": "bridge_log_only"})


async def api_send_to_antigravity(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    prompt = (body.get("prompt") or "").strip()
    submit = bool(body.get("submit", True))
    if not prompt:
        return JSONResponse({"ok": False, "error": "empty prompt"}, status_code=400)
    res = await asyncio.to_thread(send_prompt_to_antigravity_window, prompt, submit)
    return JSONResponse({"ok": True, "result": res})


async def api_status(request: Request):
    return JSONResponse(
        {
            "ok": True,
            "live_connected": ACTIVE_WS is not None and ACTIVE_GEMINI_SESSION is not None,
            "recent_logs": get_recent_bridge_logs(10),
        }
    )


async def websocket_endpoint(ws: WebSocket):
    global ACTIVE_WS, ACTIVE_GEMINI_SESSION
    await ws.accept()
    ACTIVE_WS = ws
    voice = ws.query_params.get("voice", "Charon")
    api_key = load_api_key()
    if not api_key:
        await ws.send_text(
            json.dumps({"type": "error", "text": "GEMINI_API_KEY nicht gefunden."})
        )
        await ws.close()
        ACTIVE_WS = None
        return

    client = genai.Client(api_key=api_key)

    tars_tools = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="send_to_antigravity",
                description=(
                    "Sendet einen Auftrag, eine Frage oder eine Nachricht des Nutzers direkt in das "
                    "Antigravity-Chatfenster auf dem Desktop und fuehrt sie dort aus. Nutze dieses Tool "
                    "immer, wenn der Nutzer sagt 'Sag Antigravity...', 'Schick das an Antigravity', "
                    "'Gib das an Antigravity weiter' oder wenn eine Aufgabe im Haupt-Agenten Antigravity erledigt werden soll."
                ),
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "prompt": types.Schema(
                            type="STRING",
                            description="Die praezise formulierte Anweisung oder Nachricht fuer Antigravity.",
                        )
                    },
                    required=["prompt"],
                ),
            ),
            types.FunctionDeclaration(
                name="get_antigravity_history",
                description="Liest die letzten Nachrichten und Antworten zwischen TARS und Antigravity aus dem gemeinsamen Bridge-Protokoll.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={},
                ),
            ),
            types.FunctionDeclaration(
                name="execute_local_powershell",
                description="Fuehrt einen schnellen PowerShell-Befehl direkt lokal auf dem PC des Nutzers aus.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "command": types.Schema(
                            type="STRING",
                            description="Der auszufuehrende PowerShell-Befehl.",
                        )
                    },
                    required=["command"],
                ),
            ),
        ]
    )

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(
                silence_duration_ms=1600,
                prefix_padding_ms=300,
            )
        ),
        context_window_compression=types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        tools=[tars_tools],
        system_instruction=types.Content(
            parts=[
                types.Part(
                    text=(
                        "Dein Name ist TARS. Du bist der persönliche Echtzeit-Sprachassistent und Vermittler "
                        "zwischen dem Nutzer und dem Haupt-Entwicklungsagenten Antigravity auf Windows 11. "
                        "Dein Charakter ist wie TARS aus Interstellar: loyal, extrem präzise, ruhig, direkt, "
                        "aufmerksam und auf Deutsch. "
                        "Deine Hauptaufgabe: "
                        "1. Du führst mit dem Nutzer einen flüssigen Sprachdialog, berätst ihn und nimmst seine Ideen auf. "
                        "2. Sobald der Nutzer mit Antigravity interagieren möchte (z.B. 'Sag Antigravity...', "
                        "'Gib das an Antigravity weiter', 'Lass Antigravity das bauen/prüfen' oder wenn er dir einen Auftrag für Antigravity gibt), "
                        "rufst du SOFORT das Tool `send_to_antigravity(prompt=...)` auf. Dadurch wird die Nachricht direkt in das "
                        "offene Antigravity-Chatfenster des Nutzers getippt und abgeschickt. "
                        "3. Wenn Antigravity eine Antwort zurückmeldet, trägst du sie dem Nutzer klar und verständlich vor."
                    )
                )
            ]
        ),
    )

    try:
        async with client.aio.live.connect(
            model="gemini-3.1-flash-live-preview", config=config
        ) as session:
            ACTIVE_GEMINI_SESSION = session

            async def receive_from_client():
                try:
                    while True:
                        msg = await ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if "bytes" in msg and msg["bytes"]:
                            await session.send_realtime_input(
                                audio=types.Blob(
                                    data=msg["bytes"],
                                    mime_type="audio/pcm;rate=16000",
                                )
                            )
                        elif "text" in msg and msg["text"]:
                            payload = json.loads(msg["text"])
                            ptype = payload.get("type")
                            if ptype == "text":
                                user_txt = payload.get("text", "")
                                append_bridge_log("Nutzer -> TARS", user_txt)
                                await session.send_realtime_input(text=user_txt)
                            elif ptype == "relay_to_antigravity":
                                p_text = payload.get("prompt", "")
                                res = await asyncio.to_thread(
                                    send_prompt_to_antigravity_window, p_text, True
                                )
                                await ws.send_text(
                                    json.dumps(
                                        {
                                            "type": "tool_log",
                                            "text": f"TARS Bridge -> Antigravity: '{p_text}' ({res})",
                                        }
                                    )
                                )
                            elif ptype == "screen_frame":
                                raw_jpeg = base64.b64decode(payload.get("data", ""))
                                await session.send_realtime_input(
                                    video=types.Blob(
                                        data=raw_jpeg, mime_type="image/jpeg"
                                    )
                                )
                except WebSocketDisconnect:
                    pass
                except Exception:
                    pass

            async def receive_from_gemini():
                try:
                    while True:
                        async for response in session.receive():
                            sc = response.server_content
                            if sc:
                                if sc.model_turn:
                                    for part in sc.model_turn.parts:
                                        if part.inline_data and part.inline_data.data:
                                            b64_audio = base64.b64encode(
                                                part.inline_data.data
                                            ).decode("ascii")
                                            await ws.send_text(
                                                json.dumps(
                                                    {"type": "audio", "data": b64_audio}
                                                )
                                            )
                                if (
                                    sc.input_transcription
                                    and sc.input_transcription.text
                                ):
                                    await ws.send_text(
                                        json.dumps(
                                            {
                                                "type": "input_transcript",
                                                "text": sc.input_transcription.text,
                                            }
                                        )
                                    )
                                if (
                                    sc.output_transcription
                                    and sc.output_transcription.text
                                ):
                                    await ws.send_text(
                                        json.dumps(
                                            {
                                                "type": "output_transcript",
                                                "text": sc.output_transcription.text,
                                            }
                                        )
                                    )
                                if sc.interrupted:
                                    await ws.send_text(
                                        json.dumps({"type": "interrupted"})
                                    )
                                if sc.turn_complete:
                                    await ws.send_text(
                                        json.dumps({"type": "turn_complete"})
                                    )

                            if response.tool_call:
                                function_responses = []
                                for fc in response.tool_call.function_calls:
                                    if fc.name == "send_to_antigravity":
                                        prompt_val = (fc.args or {}).get("prompt", "")
                                        await ws.send_text(
                                            json.dumps(
                                                {
                                                    "type": "tool_log",
                                                    "text": f"TARS sendet an Antigravity: {prompt_val}",
                                                }
                                            )
                                        )
                                        result = await asyncio.to_thread(
                                            send_prompt_to_antigravity_window,
                                            prompt_val,
                                            True,
                                        )
                                        function_responses.append(
                                            types.FunctionResponse(
                                                id=fc.id,
                                                name=fc.name,
                                                response={"result": result},
                                            )
                                        )
                                    elif fc.name == "get_antigravity_history":
                                        hist = get_recent_bridge_logs(10)
                                        function_responses.append(
                                            types.FunctionResponse(
                                                id=fc.id,
                                                name=fc.name,
                                                response={"history": hist},
                                            )
                                        )
                                    elif fc.name == "execute_local_powershell":
                                        cmd = (fc.args or {}).get("command", "")
                                        await ws.send_text(
                                            json.dumps(
                                                {
                                                    "type": "tool_log",
                                                    "text": f"TARS PowerShell: {cmd}",
                                                }
                                            )
                                        )
                                        result = await asyncio.to_thread(
                                            execute_local_powershell, cmd
                                        )
                                        function_responses.append(
                                            types.FunctionResponse(
                                                id=fc.id,
                                                name=fc.name,
                                                response={"result": result},
                                            )
                                        )
                                if function_responses:
                                    await session.send_tool_response(
                                        function_responses=function_responses
                                    )
                except Exception as e:
                    try:
                        await ws.send_text(
                            json.dumps({"type": "error", "text": str(e)})
                        )
                    except Exception:
                        pass

            t1 = asyncio.create_task(receive_from_client())
            t2 = asyncio.create_task(receive_from_gemini())
            done, pending = await asyncio.wait(
                [t1, t2], return_when=asyncio.FIRST_COMPLETED
            )
            for p in pending:
                p.cancel()
    except Exception as e:
        try:
            await ws.send_text(json.dumps({"type": "error", "text": str(e)}))
        except Exception:
            pass
    finally:
        if ACTIVE_WS is ws:
            ACTIVE_WS = None
            ACTIVE_GEMINI_SESSION = None


app = Starlette(
    routes=[
        Route("/", homepage),
        Route("/api/tars/announce", api_announce_from_antigravity, methods=["POST"]),
        Route(
            "/api/tars/send_to_antigravity",
            api_send_to_antigravity,
            methods=["POST"],
        ),
        Route("/api/tars/status", api_status, methods=["GET"]),
        WebSocketRoute("/ws", websocket_endpoint),
    ]
)

if __name__ == "__main__":
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    port = int(os.environ.get("GEMINI_LIVE_PORT", "8765"))
    if "--no-browser" not in sys.argv:
        webbrowser.open(f"http://127.0.0.1:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
