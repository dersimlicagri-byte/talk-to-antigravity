# TARS — Bidirectional Gemini Live Voice & Screen Bridge for Google Antigravity

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-brightgreen.svg)](https://python.org)
[![Gemini Live API](https://img.shields.io/badge/Model-Gemini%203.1%20Flash%20Live-orange.svg)](https://ai.google.dev)
[![Antigravity IDE](https://img.shields.io/badge/Platform-Google%20Antigravity%20IDE-purple.svg)](https://antigravity.google)

TARS is an open-source, bidirectional real-time voice, vision, and command bridge connecting the Google Gemini Multimodal Live API (`gemini-3.1-flash-live-preview`) directly to the **Google Antigravity IDE** on Windows 11.

It acts as an interactive spoken partner (styled after TARS from Interstellar) that allows you to discuss ideas, share your screen, and dispatch commands straight into your active Antigravity IDE coding session.

---

## Architecture Overview

```
                      +----------------------------------------+
                      |               User (Voice)             |
                      +----------------------------------------+
                                      ^        |
                              24 kHz  |        | 16 kHz PCM
                              Speaker |        | Microphone
                                      |        v
               +------------------------------------------------------+
               |             TARS Live Cockpit (Browser UI)           |
               |             http://127.0.0.1:8765                    |
               +------------------------------------------------------+
                         ^                                    |
          HTTP /api/tars |                                    | WebSocket
          Reverse Audio  |                                    | (Audio, Video,
                         |                                    |  JSON Control)
                         v                                    v
     +-------------------------------+         +-------------------------------+
     |      Antigravity MCP Server   |         |    Google Gemini Live API     |
     |           (server.py)         |         | (gemini-3.1-flash-live-prev)  |
     +-------------------------------+         +-------------------------------+
                         ^                                    |
                         | Win32 Focus & Paste Injection      | Tool Call:
                         | (Set-Clipboard + Ctrl+V + Enter)   | send_to_antigravity(prompt)
                         +------------------------------------+
                                      |
                                      v
                      +--------------------------------+
                      |     Antigravity IDE Window     |
                      |        (Antigravity.exe)       |
                      +--------------------------------+
```

---

## Key Features

1. **Continuous Multi-Turn Voice Streaming:**
   - Full-duplex WebSocket connection via `google-genai` SDK (`gemini-3.1-flash-live-preview`).
   - Native 16 kHz audio capture and 24 kHz speech synthesis.
   - Real-time barge-in: Interrupt TARS mid-sentence and it immediately halts speech playback to listen.

2. **Configurable Silence Inactivity Window:**
   - Does not prematurely close during thinking pauses.
   - Configurable timeout: 60s, 120s (default), 300s, or unlimited / permanently open.

3. **Live Desktop Screen Sharing:**
   - One-click screen sharing (`getDisplayMedia`) streaming scaled 1024px JPEG frames directly to Gemini Live every 1.5 seconds.
   - Allows TARS to see your code, compiler errors, and browser layout in real time.

4. **Direct Antigravity IDE Window Injection:**
   - TARS automatically invokes its `send_to_antigravity(prompt)` tool when you say *"TARS, pass this to Antigravity"* or *"Tell Antigravity to build..."*.
   - Uses native Win32 API calls (`SetForegroundWindow` with dummy `ALT` foreground-lock bypass, `Ctrl+V`, `Enter`) to type and execute the prompt in your Antigravity chat input.
   - Toggle between **Intermediary Mode** (relays on voice trigger) and **Direct Dictation Mode** (transcribes every spoken sentence straight into Antigravity).

5. **Reverse Audio Bridge:**
   - Antigravity's MCP `speak` tool automatically pushes completed agent answers back into the active TARS live session via `POST /api/tars/announce`.
   - TARS recites Antigravity's response through your speakers in real time.

---

## Installation & Setup

### Prerequisites
* Windows 10 or Windows 11
* Python 3.10 or newer
* Google AI Studio API Key ([aistudio.google.com](https://aistudio.google.com))

### 1. Clone & Install
```powershell
git clone https://github.com/YOUR_USERNAME/tars-antigravity-live-bridge.git
cd tars-antigravity-live-bridge
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

### 2. Configure MCP in Antigravity
Add the server to your `~/.gemini/config/mcp_config.json`:
```json
{
  "mcpServers": {
    "google-tts": {
      "command": "C:\\path\\to\\tars-antigravity-live-bridge\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\path\\to\\tars-antigravity-live-bridge\\server.py"
      ],
      "env": {
        "GEMINI_API_KEY": "YOUR_AI_STUDIO_API_KEY"
      }
    }
  }
}
```

### 3. Start TARS
Double-click `start_tars.bat` or run:
```powershell
.\start_tars.bat
```
TARS will open automatically at [http://127.0.0.1:8765](http://127.0.0.1:8765).

---

## License
Released under the [MIT License](LICENSE).
