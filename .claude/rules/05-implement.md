---
description: "Step 4: Implement — coding rules, dev commands, project layout"
---

# Step 4: Implement

Write clean code from the start. Follow these rules during implementation:

- Do NOT commit via `git` unless explicitly instructed by the user.
- Do NOT leave voxtap running in the background yourself. If you need to test the UI, use the `voxtap-control` MCP — it launches and closes the app cleanly (see `06-testing.md`).
- When creating diagrams or graphs, use `mermaid`.
- Write clean code from the start — don't plan to "clean it up later".
- Refactor continuously — improve code structure immediately when you see issues.
- Remove dead code — delete unused functions, variables, imports, and commented code.
- After writing code: review comments, clean up imports, check for side effects.

## Project layout

```
src/voxtap/
  app.py              # Main window, audio pipeline, Whisper, Ollama polish, toolbar, editor
  toggle.py           # Entry point for `voxtap-toggle` — sends SIGUSR1 / Win32 event to the running instance
  clipboard.py        # Cross-platform "copy markdown to clipboard"
  control_server.py   # TCP control server for MCP testability (started only when VOXTAP_CONTROL_PORT is set)
  __init__.py
  __main__.py         # Makes `python -m voxtap` work

mcp_server/
  pyqt_mcp.py         # stdio MCP server that launches voxtap and proxies commands to control_server

docs/                 # Design notes (incl. testability_via_mcp.md)
assets/               # Icons, etc.
pyproject.toml        # hatchling build; `voxtap` and `voxtap-toggle` entry points; [mcp] optional extra
```

## Dev commands

All commands assume the repo root unless stated otherwise. Use `uv` exclusively — never `pip`.

| Task | Command |
| --- | --- |
| Install runtime deps | `uv sync` |
| Install runtime + MCP deps | `uv sync --extra mcp` |
| Run the app | `uv run voxtap` |
| Run the app with control server (manual testing) | `VOXTAP_CONTROL_PORT=29998 uv run voxtap` |
| Trigger record/stop from CLI | `uv run voxtap-toggle` |
| Run as a module | `uv run python -m voxtap` |

There is no test suite checked in. Verify changes via the MCP (`06-testing.md`) or by running the app manually.

## Platform notes

Voxtap targets Linux, macOS, and Windows. When editing:
- Anything touching paths, clipboard, or IPC (toggle) has a `sys.platform` branch — mirror the existing pattern. See `_base` in `app.py` and `clipboard.py`.
- `sounddevice` requires PortAudio at runtime. If audio-related code fails, first check that PortAudio is installed on the host.
- Windows uses a named Win32 event (`voxtap_toggle_event`) for IPC toggle; POSIX uses `SIGUSR1`.
