#!/usr/bin/env python3
"""Builds VocalDesk into a standalone Windows .exe using PyInstaller."""

import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
ENTRY_POINT = ROOT / "gemini_live_app.py"
OUTPUT_NAME = "VocalDesk"

def main():
    print("=" * 60)
    print("Building VocalDesk Standalone Windows Executable...")
    print("=" * 60)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name",
        OUTPUT_NAME,
        "--onefile",
        "--clean",
        "--noconfirm",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.loops.asyncio",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.http.h11_impl",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.protocols.websockets.websockets_impl",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "google.genai",
        "--hidden-import", "google.genai.types",
        "--hidden-import", "starlette",
        "--hidden-import", "starlette.applications",
        "--hidden-import", "starlette.routing",
        "--hidden-import", "starlette.responses",
        "--hidden-import", "starlette.websockets",
        "--collect-all", "google.genai",
        "--collect-all", "starlette",
        "--collect-all", "uvicorn",
        str(ENTRY_POINT),
    ]

    print("Running command:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode == 0:
        exe_path = ROOT / "dist" / f"{OUTPUT_NAME}.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print("=" * 60)
            print(f"BUILD SUCCESS! Output: {exe_path} ({size_mb:.1f} MB)")
            print("=" * 60)
            return 0
    print("BUILD FAILED with code:", result.returncode)
    return result.returncode

if __name__ == "__main__":
    raise SystemExit(main())
