import json
import os
import tempfile
import urllib.request
import wave
import winsound
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import Field

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("google-gemini-tts")
BRIDGE_LOG_PATH = Path(__file__).parent / "tars_bridge_log.json"


def _log_bridge(sender: str, text: str) -> None:
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


def _try_announce_via_tars_live(text: str) -> bool:
    """Prueft, ob das TARS Live-Cockpit (Port 8765) aktiv verbunden ist, und speist die Antwort live ein."""
    try:
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8765/api/tars/announce",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok") and data.get("delivered") == "live_stream":
                return True
    except Exception:
        pass
    return False


@mcp.tool()
def speak(
    text: str = Field(
        ...,
        description="Kurzer, natürlicher Text, der dem Nutzer über TARS (oder direkt per Lautsprecher) vorgelesen werden soll.",
    ),
    voice_name: str = Field(
        default="Charon",
        description="Name der Google-Stimme: Charon (TARS Standard), Puck, Kore, Fenrir oder Aoede.",
    ),
    style_prompt: str = Field(
        default="Sprich ruhig, präzise, direkt und kollegial wie TARS auf Deutsch",
        description="Regieanweisung für Tonfall und Sprechstil.",
    ),
    output_wav_path: str = Field(
        default="",
        description="Optionaler Dateipfad zum Speichern der generierten 24kHz WAV-Audiodatei.",
    ),
) -> str:
    """Sendet Status-Updates und Antworten von Antigravity direkt an TARS (Live-Stream) oder liest sie laut vor."""
    if not isinstance(voice_name, str):
        voice_name = "Charon"
    if not isinstance(style_prompt, str):
        style_prompt = "Sprich ruhig, präzise, direkt und kollegial wie TARS auf Deutsch"
    if not isinstance(output_wav_path, str):
        output_wav_path = ""

    # Wenn keine WAV-Datei gespeichert werden soll, zuerst über die offene TARS-Live-Sitzung senden
    if not output_wav_path and _try_announce_via_tars_live(text):
        return "Erfolgreich an aktive TARS-Live-Sitzung übergeben und vorgelesen."

    _log_bridge("Antigravity -> TARS (Direct Audio)", text)

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return "Fehler: Umgebungsvariable GEMINI_API_KEY fehlt in der mcp_config.json."

    try:
        client = genai.Client(api_key=api_key)
        prompt = f"[{style_prompt}] {text}"

        response = client.models.generate_content(
            model="gemini-2.5-flash-preview-tts",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice_name
                        )
                    )
                ),
            ),
        )

        audio_bytes = response.candidates[0].content.parts[0].inline_data.data

        if output_wav_path:
            target_path = output_wav_path
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                target_path = tmp_file.name

        with wave.open(target_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(audio_bytes)

        if not output_wav_path:
            winsound.PlaySound(target_path, winsound.SND_FILENAME)
            try:
                os.remove(target_path)
            except OSError:
                pass
            return f"Erfolgreich mit TARS-Stimme '{voice_name}' gesprochen."
        else:
            return f"Erfolgreich mit Stimme '{voice_name}' in '{target_path}' gespeichert."
    except Exception as e:
        return f"TTS-Fehler bei der Sprachausgabe: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
