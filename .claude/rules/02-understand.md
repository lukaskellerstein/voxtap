---
description: "Step 1: Understand — read code, ask questions, identify gaps before any implementation"
---

# Step 1: Understand

- Read relevant code and identify impacted areas. Voxtap is small — `src/voxtap/app.py` holds the main window, audio pipeline, Whisper call, Ollama polish, and all formatting. Read it end-to-end if a change touches UI or flow.
- Ask clarifying questions if requirements are ambiguous.
- Identify gaps in the current design and opportunities for improvement.
- Understand the requirement completely before proceeding.
- **For bug reports**: reproduce the issue first. Options:
  - Drive the UI via the `voxtap-control` MCP (`mcp__voxtap-control__launch_app`, `get_snapshot`, `set_transcript`, `click`, etc.) — see `06-testing.md`.
  - Run voxtap in a terminal (`uv run voxtap`) and observe stdout/stderr for tracebacks.
  - Check the PID file location in `01-project-config.md` if behavior seems to involve multiple instances.
