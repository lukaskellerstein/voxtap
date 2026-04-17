---
description: "Reference: Technology stack — PyQt6, faster-whisper, sounddevice, Ollama, uv"
---

# Reference: Technology Stack

## Project Structure

```
src/voxtap/           # Python package — app code
mcp_server/           # MCP server that drives voxtap for testing
docs/                 # Design notes
assets/               # Icons
pyproject.toml        # hatchling build config, entry points, optional deps
```

## Runtime dependencies

Declared in `pyproject.toml`. Always managed with `uv` — never `pip`.

| Package | Purpose |
| --- | --- |
| `PyQt6 >= 6.5.0` | GUI toolkit (main window, toolbar, rich-text editor, custom paint widgets) |
| `faster-whisper >= 1.0.0` | Speech-to-text inference (CUDA or CPU via CTranslate2) |
| `sounddevice >= 0.4.6` | Audio capture (PortAudio binding) — also used for playback |
| `numpy >= 1.24` | Audio buffer handling |

### Indirect / transitive deps worth knowing

- `huggingface_hub` — model download; voxtap wraps its `tqdm_class` to report progress to the download dialog.
- `torch` (optional) — used only to detect CUDA availability. Voxtap falls back to CPU if torch isn't installed.
- `tqdm` — subclassed to pipe download progress into the Qt signal bridge.

## Optional dependencies

Declared under `[project.optional-dependencies]` in `pyproject.toml`. Install with `uv sync --extra <name>`.

| Extra | Packages | Purpose |
| --- | --- | --- |
| `mcp` | `mcp >= 1.0.0` | MCP server SDK used by `mcp_server/pyqt_mcp.py` to expose voxtap to Claude Code |

## External runtime services (not Python deps)

- **Ollama daemon** on `http://localhost:11434` — used for the "polish" step (post-processing the raw Whisper transcript). Default model `gpt-oss:20b`. If Ollama is unreachable, voxtap returns the raw text unchanged.
- **PortAudio** — required by `sounddevice` for audio I/O. Install via the OS package manager.
- **`xclip`** (Linux only) — required for image-paste handling in the editor.
- **`dbus-send`** (Linux only) — required for Spotify auto-pause/resume during recording.

## Language / build

- Python: 3.9+ (per `requires-python` in `pyproject.toml`).
- Build backend: `hatchling`.
- Package manager: `uv` (CRITICAL — never `pip`).
- No linter or test suite is checked in. If you add one, ruff + pytest are the preferred choices (consistent with the user's other projects).

## Platforms

Voxtap supports Linux, macOS, and Windows. Platform branches live in:
- `src/voxtap/app.py` — cache-dir resolution, IPC toggle (SIGUSR1 vs Win32 event), clipboard image paste.
- `src/voxtap/clipboard.py` — cross-platform markdown copy.
- `src/voxtap/toggle.py` — cross-platform IPC sender.

## MCP testability layer

- `src/voxtap/control_server.py` — in-process TCP server on `127.0.0.1:29998`, started only when `VOXTAP_CONTROL_PORT` is set.
- `mcp_server/pyqt_mcp.py` — stdio MCP server that launches voxtap and forwards MCP tool calls as JSON over TCP.
- See `docs/testability_via_mcp.md` for architecture and the full tool list.
