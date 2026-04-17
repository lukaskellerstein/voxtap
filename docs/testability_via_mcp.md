# Testability via MCP

Voxtap can be driven by Claude Code (or any MCP client) through the `voxtap-control` MCP server. This lets an AI agent inspect the UI, click buttons, read the transcript, and run end-to-end flows without real audio.

## Architecture

```
┌─────────────────┐     ┌────────────────────┐     ┌─────────────────────┐
│   Claude Code   │────▶│   MCP Server       │────▶│  voxtap + control   │
│                 │     │  (pyqt_mcp.py)     │     │  server (TCP)       │
└─────────────────┘     └────────────────────┘     └─────────────────────┘
      stdio                 JSON over TCP (127.0.0.1:29998)
```

- **voxtap** embeds a TCP control server (`src/voxtap/control_server.py`) that runs in a daemon thread and marshals commands onto the Qt main thread via `pyqtSignal`. It only starts when the `VOXTAP_CONTROL_PORT` env var is set, so regular users never open a port.
- **MCP server** (`mcp_server/pyqt_mcp.py`) is a stdio process registered in `.mcp.json`. It launches voxtap on demand (with the env var set), polls the control port until it responds, and forwards each MCP tool call as a newline-delimited JSON command.

## Tools

### Lifecycle
- `launch_app()` — spawns `python -m voxtap` with `VOXTAP_CONTROL_PORT=29998`. Returns once the control server accepts connections. The Whisper model may still be loading — poll with `get_recording_state()`.
- `close_app()` — sends `close`, terminates the process if needed.

### Inspection
- `get_snapshot()` — every named widget (type, visibility, enabled, text, etc.), every top-level window, every toolbar `QAction`.
- `get_window_info()` — title, geometry, visibility state of the main window.
- `get_text(object_name)` — read text from a label, button, editor, line edit, combo, etc.
- `take_screenshot(window_name="", widget_name="", save_path="")` — PNG via Qt's native `grab()`. Saves to disk if `save_path` is given, else returns base64.

### Interaction
- `click(object_name)` — `QPushButton` / `QCheckBox` / `QRadioButton`.
- `fill(object_name, text)` — `QLineEdit` / `QTextEdit` / `QPlainTextEdit`.
- `clear(object_name)` — same widget types as `fill`.
- `trigger_action(object_name)` — toolbar `QAction` (e.g. `action_bold`, `action_h1`).

### Voxtap-specific
- `trigger_toggle()` — same slot the global hotkey and the Record button call. Use this to exercise the hotkey path without a real keypress.
- `set_transcript(text)` — write directly into the transcript editor, bypassing Whisper. Lets tests drive the polish/copy/clear flows deterministically.
- `get_recording_state()` — returns `{state, has_recording, transcript}`. State is one of `loading_model`, `idle`, `recording`, `transcribing`, `polishing`, `playing`.

## Naming conventions

Every interactive or stateful widget has a stable `objectName`. Without one, the widget is invisible to MCP.

| Widget type | Convention | Example |
| --- | --- | --- |
| Main window | `voxtap_main_window` | |
| Buttons | `{purpose}_button` | `record_button`, `polish_button` |
| Labels whose text matters | `{purpose}_label` | `status_label`, `stt_model_label` |
| Text editors | `{purpose}_edit` | `transcript_edit` |
| Toolbars | `{name}_toolbar` | `main_toolbar` |
| Toolbar `QAction`s | `action_{name}` | `action_bold`, `action_h1`, `action_align_left` |
| Custom widgets with state | `{purpose}` | `waveform`, `polish_indicator`, `glow_frame` |

Rules:
- `snake_case` everywhere.
- Purely structural `QWidget` containers (layout holders) don't need names.
- Stylesheet selectors must match the object names (e.g. `QLabel#status_label`), so renaming requires updating both.

## Running

1. Install the optional dep: `uv sync --extra mcp` (installs the `mcp` Python package used by the MCP server).
2. The first time Claude Code starts in this repo it will pick up `.mcp.json` and prompt to enable `voxtap-control`. Approve it.
3. Tools appear under the `mcp__voxtap-control__*` prefix.

## Example flow

Verify the polish button works end-to-end without real audio:

```
launch_app()                              # wait for control server
# wait until get_recording_state().state == "idle"
set_transcript("um, so like this is a test")
click("polish_button")
# poll get_recording_state() until state == "idle"
get_text("transcript_edit")               # should be polished
close_app()
```

## Extending

Adding a new MCP tool is two edits:

1. `src/voxtap/control_server.py` — add a `cmd_type` branch in `_execute_command` and the corresponding `_method()`.
2. `mcp_server/pyqt_mcp.py` — add a `@mcp.tool()` that wraps `_send_command({"type": "your_cmd", ...})`.

Everything runs on the Qt main thread via `command_received.emit(command, callback)` — you can safely call any Qt API from the handler.

## Security

- The server binds `127.0.0.1` only — no remote access.
- It only starts when `VOXTAP_CONTROL_PORT` is set, so production/normal users never open a port.
- No authentication: anything on the host can drive the app while it's running. Don't set the env var outside test/dev contexts.
