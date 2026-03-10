"""Cross-platform clipboard support.

Auto-detects the correct clipboard command:
  Wayland  -> wl-copy
  X11      -> xclip (fallback: xsel)
  macOS    -> pbcopy
"""

import os
import shutil
import subprocess
import sys


def _detect_clipboard_cmd() -> list[str]:
    """Return the command (as argv list) to pipe text into the clipboard."""
    if sys.platform == "darwin":
        if shutil.which("pbcopy"):
            return ["pbcopy"]
        raise RuntimeError("pbcopy not found — it should ship with macOS.")

    # Linux / BSD
    if os.environ.get("WAYLAND_DISPLAY"):
        if shutil.which("wl-copy"):
            return ["wl-copy"]
        raise RuntimeError(
            "Wayland detected but wl-copy not found.\n"
            "Install it:  sudo apt install wl-clipboard  (Debian/Ubuntu)\n"
            "             sudo dnf install wl-clipboard   (Fedora)\n"
            "             sudo pacman -S wl-clipboard      (Arch)"
        )

    if os.environ.get("DISPLAY"):
        if shutil.which("xclip"):
            return ["xclip", "-selection", "clipboard"]
        if shutil.which("xsel"):
            return ["xsel", "--clipboard", "--input"]
        raise RuntimeError(
            "X11 detected but neither xclip nor xsel found.\n"
            "Install one:  sudo apt install xclip   (Debian/Ubuntu)\n"
            "              sudo dnf install xclip    (Fedora)\n"
            "              sudo pacman -S xclip       (Arch)"
        )

    raise RuntimeError(
        "No display server detected (neither $WAYLAND_DISPLAY nor $DISPLAY set).\n"
        "Cannot copy to clipboard without a running display server."
    )


# Resolved once on first use.
_clipboard_cmd: list[str] | None = None


def copy(text: str) -> None:
    """Copy *text* to the system clipboard."""
    global _clipboard_cmd
    if _clipboard_cmd is None:
        _clipboard_cmd = _detect_clipboard_cmd()

    proc = subprocess.Popen(_clipboard_cmd, stdin=subprocess.PIPE)
    proc.communicate(text.encode("utf-8"))
