#!/usr/bin/env python3
"""MCP server for controlling voxtap via its embedded TCP control server.

Launched over stdio by Claude Code (see `.mcp.json`). Each MCP tool call is
translated into a JSON command sent to voxtap's control server on
`VOXTAP_CONTROL_PORT` (default 29998).
"""

import base64
import json
import os
import socket
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("voxtap-control")

DEFAULT_HOST = os.getenv("VOXTAP_CONTROL_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("VOXTAP_CONTROL_PORT", "29998"))

_app_process: Optional[subprocess.Popen] = None


def _send_command(
    command: Dict[str, Any],
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.sendall((json.dumps(command) + "\n").encode("utf-8"))
        buf = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"\n" in buf:
                break
        return json.loads(buf.decode("utf-8").strip())
    finally:
        sock.close()


def _is_app_responding(host: str, port: int) -> bool:
    try:
        resp = _send_command({"type": "get_window_info"}, host, port, timeout=1.0)
        return resp.get("status") == "success"
    except Exception:
        return False


def _wait_until_ready(host: str, port: int, timeout_s: float) -> bool:
    """Poll the control server until it accepts connections or timeout elapses."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _is_app_responding(host, port):
            return True
        time.sleep(0.2)
    return False


# =============================================================================
# Lifecycle
# =============================================================================


@mcp.tool()
def launch_app(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    timeout: float = 15.0,
) -> Dict[str, Any]:
    """Launch voxtap with its control server enabled.

    If voxtap is already responding on the given port, returns early.
    Waits up to `timeout` seconds for the control server to become responsive.
    The Whisper model may still be loading when this returns — use
    get_recording_state() to check for the "loading_model" → "idle" transition.
    """
    global _app_process

    if _is_app_responding(host, port):
        return {
            "status": "success",
            "message": f"voxtap already running on {host}:{port}",
            "already_running": True,
            "port": port,
        }

    env = os.environ.copy()
    env["VOXTAP_CONTROL_PORT"] = str(port)
    env["VOXTAP_CONTROL_HOST"] = host

    try:
        _app_process = subprocess.Popen(
            [sys.executable, "-m", "voxtap"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except Exception as e:
        return {"status": "error", "message": f"Failed to launch voxtap: {e}"}

    if not _wait_until_ready(host, port, timeout):
        stdout = b""
        stderr = b""
        if _app_process.poll() is not None:
            stdout, stderr = _app_process.communicate(timeout=1)
        return {
            "status": "error",
            "message": "voxtap launched but control server did not become ready",
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": stderr.decode("utf-8", errors="replace"),
        }

    snapshot = _send_command({"type": "get_snapshot"}, host, port)
    result = snapshot.get("result", {}) if snapshot.get("status") == "success" else {}

    return {
        "status": "success",
        "message": f"voxtap launched on {host}:{port}",
        "window_title": result.get("window_title"),
        "widget_count": len(result.get("widgets", [])),
        "port": port,
    }


@mcp.tool()
def close_app(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> Dict[str, Any]:
    """Close voxtap cleanly. Terminates the subprocess if it does not exit."""
    global _app_process

    try:
        _send_command({"type": "close"}, host, port, timeout=2.0)
    except Exception:
        pass

    if _app_process:
        time.sleep(0.5)
        if _app_process.poll() is None:
            _app_process.terminate()
            time.sleep(0.5)
            if _app_process.poll() is None:
                _app_process.kill()
        _app_process = None

    return {"status": "success", "message": "voxtap closed"}


# =============================================================================
# Inspection
# =============================================================================


@mcp.tool()
def get_snapshot(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> Dict[str, Any]:
    """Return all named widgets, their properties, and toolbar/menu actions."""
    try:
        response = _send_command({"type": "get_snapshot"}, host, port)
        if response.get("status") == "success":
            return response.get("result", {})
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_window_info(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> Dict[str, Any]:
    """Return window title, geometry, and visibility state."""
    try:
        response = _send_command({"type": "get_window_info"}, host, port)
        if response.get("status") == "success":
            return {"status": "success", **response.get("result", {})}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_text(
    object_name: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> Dict[str, Any]:
    """Read text from a widget (QLabel, QLineEdit, QPushButton, QTextEdit, etc.)."""
    try:
        response = _send_command(
            {"type": "get_text", "object_name": object_name}, host, port
        )
        if response.get("status") == "success":
            return {"status": "success", "text": response.get("result")}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def take_screenshot(
    window_name: str = "",
    widget_name: str = "",
    save_path: str = "",
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> Dict[str, Any]:
    """Capture a PNG screenshot. If save_path is given, writes to disk; otherwise returns base64.

    widget_name takes precedence over window_name. With neither, the main window is captured.
    """
    try:
        cmd: Dict[str, Any] = {"type": "take_screenshot"}
        if widget_name:
            cmd["widget_name"] = widget_name
        elif window_name:
            cmd["window_name"] = window_name

        response = _send_command(cmd, host, port)
        if response.get("status") != "success":
            return {"status": "error", "message": response.get("message")}

        result = response.get("result", {})
        if save_path:
            with open(save_path, "wb") as f:
                f.write(base64.b64decode(result.get("image_base64", "")))
            return {
                "status": "success",
                "file_path": save_path,
                "width": result.get("width"),
                "height": result.get("height"),
                "target": result.get("target"),
            }
        return {
            "status": "success",
            "image_base64": result.get("image_base64"),
            "width": result.get("width"),
            "height": result.get("height"),
            "target": result.get("target"),
            "format": result.get("format", "png"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Interaction
# =============================================================================


@mcp.tool()
def click(
    object_name: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> Dict[str, Any]:
    """Click a QPushButton, QCheckBox, or QRadioButton by object_name."""
    try:
        response = _send_command(
            {"type": "click", "object_name": object_name}, host, port
        )
        if response.get("status") == "success":
            return {"status": "success", "message": f"Clicked {object_name}"}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def fill(
    object_name: str,
    text: str,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> Dict[str, Any]:
    """Set the text of a QLineEdit, QTextEdit, or QPlainTextEdit."""
    try:
        response = _send_command(
            {"type": "fill", "object_name": object_name, "text": text}, host, port
        )
        if response.get("status") == "success":
            return {"status": "success", "message": f"Filled {object_name}"}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def clear(
    object_name: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> Dict[str, Any]:
    """Clear the text of a QLineEdit, QTextEdit, or QPlainTextEdit."""
    try:
        response = _send_command(
            {"type": "clear", "object_name": object_name}, host, port
        )
        if response.get("status") == "success":
            return {"status": "success", "message": f"Cleared {object_name}"}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def trigger_action(
    object_name: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> Dict[str, Any]:
    """Trigger a toolbar QAction by object_name (e.g. action_bold, action_h1)."""
    try:
        response = _send_command(
            {"type": "trigger_action", "object_name": object_name}, host, port
        )
        if response.get("status") == "success":
            return {"status": "success", "message": f"Triggered {object_name}"}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# =============================================================================
# Voxtap-specific
# =============================================================================


@mcp.tool()
def trigger_toggle(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> Dict[str, Any]:
    """Start or stop recording — same path the global hotkey and Record button use.

    Use this instead of click("record_button") when you want to exercise the hotkey flow.
    """
    try:
        response = _send_command({"type": "trigger_toggle"}, host, port)
        if response.get("status") == "success":
            return {"status": "success", "message": "Toggled recording"}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def set_transcript(
    text: str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> Dict[str, Any]:
    """Overwrite the transcript editor with `text`, bypassing Whisper.

    Lets tests drive the polish/copy/clear flows deterministically without real audio.
    """
    try:
        response = _send_command(
            {"type": "set_transcript", "text": text}, host, port
        )
        if response.get("status") == "success":
            return {"status": "success", "message": "Transcript set"}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
def get_recording_state(
    host: str = DEFAULT_HOST, port: int = DEFAULT_PORT
) -> Dict[str, Any]:
    """Return voxtap's current state.

    State is one of: loading_model, idle, recording, transcribing, polishing, playing.
    Also returns has_recording (bool) and transcript (str).
    """
    try:
        response = _send_command({"type": "get_recording_state"}, host, port)
        if response.get("status") == "success":
            return {"status": "success", **response.get("result", {})}
        return {"status": "error", "message": response.get("message")}
    except Exception as e:
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    mcp.run(transport="stdio")
