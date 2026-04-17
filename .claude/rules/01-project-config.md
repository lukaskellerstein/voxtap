---
description: Project configuration — what voxtap is, how to run it, where its data lives
---

# Project Config

- **Project**: voxtap — Tap a key, get voice transcribed. A PyQt6 desktop app built on `faster-whisper` for speech-to-text and (optionally) a local Ollama LLM for post-processing ("polish").
- **Language / stack**: Python 3.9+, PyQt6, managed with `uv` (never `pip`). See `10-tech-stack.md`.
- **Entry points** (defined in `pyproject.toml`):
  - `voxtap` → `voxtap.app:main` — launches the GUI.
  - `voxtap-toggle` → `voxtap.toggle:main` — IPC toggle used by the global hotkey.
- **Run from source**: `uv run voxtap` (or `uv run python -m voxtap`).
- **PID / cache dir**:
  - Linux: `$XDG_CACHE_HOME/voxtap/` (usually `~/.cache/voxtap/`)
  - Windows: `%LOCALAPPDATA%\voxtap\`
  - macOS: `~/.cache/voxtap/`
  - PID file: `<cache_dir>/voxtap.pid`
  - Pasted-image dumps: `<cache_dir>/images/`
- **Local dependencies (runtime, not bundled)**:
  - `ollama` daemon on `http://localhost:11434` for the polish step (optional; app falls back to raw text if unreachable). Default model: `gpt-oss:20b`.
  - On Linux: `xclip` for image-paste handling; `dbus-send` for Spotify auto-pause (both optional).
- **Testability**: voxtap exposes a TCP control server when `VOXTAP_CONTROL_PORT` is set. The `voxtap-control` MCP server wraps it — see `06-testing.md` and `docs/testability_via_mcp.md`.
