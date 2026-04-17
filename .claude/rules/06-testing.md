---
description: "Step 5: Testing — define DoD, drive the app via voxtap-control MCP, always close when done"
---

# Step 5: Testing

**Every code change must be tested before reporting completion. No exceptions.**

## 5a. Define your Definition of Done

Before testing, **write out your DoD checklist in the conversation** so the user can see what you intend to verify. Example:

> **Definition of Done for this task:**
> - [ ] The polish button is enabled after a transcript is entered
> - [ ] Clicking it replaces the editor text with the polished version
> - [ ] `status_label` shows "Polished — Esc to copy & close" when done

## 5b. How to test: the voxtap-control MCP

Voxtap is a PyQt6 desktop app. It is driven by the `voxtap-control` MCP server registered in `.mcp.json`. Tools are exposed under the `mcp__voxtap-control__*` prefix.

The MCP launches voxtap itself — do not start the app manually for testing. See `docs/testability_via_mcp.md` for the full architecture and tool list.

### Rules

1. **Always close the app when finished.** End every testing session with `mcp__voxtap-control__close_app`. Leaving voxtap running blocks port 29998 for the next launch and holds a Whisper model in memory.
2. **Never claim "task done" without exercising the change.** For changes to `src/voxtap/app.py`, `control_server.py`, `clipboard.py`, or `toggle.py`: launch → verify → close.
3. **Add `setObjectName()` before testing a new widget.** Unnamed widgets are invisible to the MCP. Naming conventions are in `docs/testability_via_mcp.md`.
4. **Prefer `set_transcript` + `click("polish_button")` over real audio.** Whisper + `sounddevice` are slow and non-deterministic. Drive the editor directly and assert on the polished text unless the audio path itself is what you're testing.

### Typical flow

```
launch_app()
# Poll get_recording_state() until state == "idle" (Whisper finished loading).
# First launch downloads the model and takes a minute or two — be patient.

<exercise the feature using click / fill / trigger_action / set_transcript / trigger_toggle>

take_screenshot(save_path="/tmp/voxtap_check.png")   # optional visual evidence
close_app()
```

### Tool summary

| Category | Tools |
| --- | --- |
| Lifecycle | `launch_app`, `close_app` |
| Inspect | `get_snapshot`, `get_window_info`, `get_text`, `take_screenshot`, `get_recording_state` |
| Interact | `click`, `fill`, `clear`, `trigger_action` |
| Voxtap-specific | `trigger_toggle` (hotkey path), `set_transcript` (bypass Whisper) |

## 5c. Changes that do not need an MCP run

- `toggle.py` changes — test manually via `uv run voxtap-toggle` against a running instance.
- `clipboard.py` changes — test manually via `uv run python -c "from voxtap import clipboard; clipboard.copy('test')"` and paste somewhere.
- `pyproject.toml` metadata, README, or doc changes — state explicitly that no runtime test is needed.
- Pure refactors that don't touch `app.py` — run `uv run python -m voxtap --help` to at least confirm imports still work.

## 5d. Fix and repeat

If a test fails: fix the issue, then retest. Repeat until all DoD items pass. If you cannot resolve a problem after a couple of attempts, stop and ask the user.

## 5e. Debugging

- **voxtap stdout/stderr**: captured by the MCP's `launch_app` (on failure, `launch_app` returns the captured output). For deeper debugging, run the app yourself in a terminal: `VOXTAP_CONTROL_PORT=29998 uv run voxtap`.
- **Control server readiness**: `launch_app` prints `VOXTAP_CONTROL_SERVER_READY 127.0.0.1:29998` on stdout once the TCP port is accepting connections. Whisper may still be loading after that — poll `get_recording_state()` for `state == "idle"`.
- **Port collisions**: If a previous voxtap is still running on 29998, `launch_app` returns `already_running: true`. Close it first with `close_app()` and relaunch.
