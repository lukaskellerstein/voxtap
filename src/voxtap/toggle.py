"""Pure-Python toggle for voxtap.

If voxtap is already running, send SIGUSR1 (POSIX) or a named event (Windows)
to toggle recording.  Otherwise, launch a new voxtap instance.

PID file: <cache_dir>/voxtap.pid
"""

import os
import signal
import subprocess
import sys


if sys.platform == "win32":
    _base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
else:
    _base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
CACHE_DIR = os.path.join(_base, "voxtap")
PIDFILE = os.path.join(CACHE_DIR, "voxtap.pid")


def _read_pid() -> int | None:
    """Read PID from the pidfile, return None if missing or stale."""
    try:
        with open(PIDFILE) as f:
            pid = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return None

    # Check if process is alive
    try:
        os.kill(pid, 0)
    except OSError:
        # Stale pidfile
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        return None
    return pid


def _signal_windows() -> bool:
    """Signal a running voxtap via Win32 named event. Return True on success."""
    import ctypes
    kernel32 = ctypes.windll.kernel32
    EVENT_MODIFY_STATE = 0x0002
    event = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, "voxtap_toggle_event")
    if event:
        kernel32.SetEvent(event)
        kernel32.CloseHandle(event)
        return True
    return False


def main() -> None:
    if sys.platform == "win32":
        if _signal_windows():
            print("Signaled running voxtap instance via named event")
            return
        # Fall through to launch new instance
    else:
        pid = _read_pid()
        if pid is not None:
            os.kill(pid, signal.SIGUSR1)
            print(f"Sent SIGUSR1 to voxtap (pid {pid})")
            return

    # Launch new instance — forward any extra CLI args
    cmd = [sys.executable, "-m", "voxtap"] + sys.argv[1:]
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        subprocess.Popen(
            cmd,
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
        )
    else:
        subprocess.Popen(cmd, start_new_session=True)
    print("Started new voxtap instance")


if __name__ == "__main__":
    main()
