"""Pure-Python toggle for voxtap.

If voxtap is already running, send SIGUSR1 to stop recording.
Otherwise, launch a new voxtap instance.

PID file: ~/.cache/voxtap/voxtap.pid
"""

import os
import signal
import subprocess
import sys


CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "voxtap")
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


def main() -> None:
    pid = _read_pid()
    if pid is not None:
        # Already running — send SIGUSR1 to stop recording
        os.kill(pid, signal.SIGUSR1)
        print(f"Sent SIGUSR1 to voxtap (pid {pid})")
    else:
        # Launch new instance
        # Forward any extra CLI args
        cmd = [sys.executable, "-m", "voxtap"] + sys.argv[1:]
        subprocess.Popen(cmd, start_new_session=True)
        print("Started new voxtap instance")


if __name__ == "__main__":
    main()
