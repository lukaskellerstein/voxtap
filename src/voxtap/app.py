"""Main voxtap GUI — speech-to-text with Whisper and tkinter."""

import argparse
import os
import queue
import signal
import sys
import threading
import tkinter as tk

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from voxtap import clipboard

# PID file location
CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "voxtap"
)
PIDFILE = os.path.join(CACHE_DIR, "voxtap.pid")

# UI Colors
BG_DARK = "#1e1e2e"
BG_MEDIUM = "#2e2e3e"
BG_LIGHT = "#3e3e4e"
FG_PRIMARY = "#cdd6f4"
FG_SECONDARY = "#a6adc8"
ACCENT_RED = "#ff6b6b"
ACCENT_GREEN = "#51cf66"
ACCENT_BLUE = "#4dabf7"
BORDER_COLOR = "#45475a"

SAMPLE_RATE = 16000
CHUNK_INTERVAL = 3.0  # seconds between transcription runs


class SpeechToText:
    def __init__(self, model_name="small", language="en", device=None):
        self.model_name = model_name
        self.language = language
        self.device_override = device
        self.model = None
        self.audio_queue: queue.Queue = queue.Queue()
        self.audio_buffer: list = []
        self.recording = False
        self.stream = None
        self.transcription_thread = None
        self.full_text = ""

        # Create main window
        self.root = tk.Tk()
        self.root.title("voxtap")
        self.root.geometry("700x400")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BG_DARK)

        self._setup_gui()

        self.root.bind("<Escape>", lambda e: self.close())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        # Handle SIGUSR1 for toggle (POSIX only)
        if sys.platform != "win32":
            signal.signal(signal.SIGUSR1, self._handle_sigusr1)

        # Write PID file
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))

        # Start loading model in background
        self.root.after(100, self._start_model_load)

    def _setup_gui(self):
        # Title bar
        title_frame = tk.Frame(self.root, bg=BG_MEDIUM, height=50)
        title_frame.pack(fill=tk.X)
        title_frame.pack_propagate(False)

        title_label = tk.Label(
            title_frame,
            text="voxtap",
            font=("Sans", 14, "bold"),
            bg=BG_MEDIUM,
            fg=FG_PRIMARY,
        )
        title_label.pack(side=tk.LEFT, padx=15, expand=False)

        self.status_label = tk.Label(
            title_frame,
            text="Loading model...",
            font=("Sans", 11),
            bg=BG_MEDIUM,
            fg=FG_SECONDARY,
        )
        self.status_label.pack(side=tk.RIGHT, padx=15)

        # Text area
        text_frame = tk.Frame(self.root, bg=BG_DARK)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=10)

        self.text_widget = tk.Text(
            text_frame,
            font=("Sans", 12),
            bg=BG_LIGHT,
            fg=FG_PRIMARY,
            insertbackground=FG_PRIMARY,
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=BORDER_COLOR,
            highlightcolor=BORDER_COLOR,
            wrap=tk.WORD,
            padx=10,
            pady=10,
        )
        self.text_widget.pack(fill=tk.BOTH, expand=True)

        self.scrollbar = tk.Scrollbar(
            self.text_widget, command=self.text_widget.yview
        )
        self.text_widget.configure(yscrollcommand=self._on_scroll_set)

        # Auto-copy to clipboard when user edits text
        self.text_widget.bind("<<Modified>>", self._on_text_modified)

        # Button bar
        button_frame = tk.Frame(self.root, bg=BG_MEDIUM, height=55)
        button_frame.pack(fill=tk.X, side=tk.BOTTOM)
        button_frame.pack_propagate(False)

        btn_container = tk.Frame(button_frame, bg=BG_MEDIUM)
        btn_container.pack(expand=True)

        btn_style = dict(
            font=("Sans", 11, "bold"),
            relief=tk.FLAT,
            borderwidth=0,
            padx=15,
            pady=6,
            cursor="hand2",
        )

        self.copy_btn = tk.Button(
            btn_container,
            text="Copy to Clipboard",
            bg=ACCENT_BLUE,
            fg=BG_DARK,
            command=self.copy_to_clipboard,
            **btn_style,
        )
        self.copy_btn.pack(side=tk.LEFT, padx=8)

        self.stop_btn = tk.Button(
            btn_container,
            text="Stop",
            bg=ACCENT_RED,
            fg=BG_DARK,
            command=self.stop_recording,
            state=tk.DISABLED,
            **btn_style,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=8)

        self.close_btn = tk.Button(
            btn_container,
            text="Close",
            bg=FG_SECONDARY,
            fg=BG_DARK,
            command=self.close,
            **btn_style,
        )
        self.close_btn.pack(side=tk.LEFT, padx=8)

    def _on_scroll_set(self, first, last):
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.scrollbar.pack_forget()
        else:
            self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.scrollbar.set(first, last)

    def _on_text_modified(self, event=None):
        if self.text_widget.edit_modified():
            self.text_widget.edit_modified(False)
            self._sync_clipboard()

    def _sync_clipboard(self):
        text = self.text_widget.get("1.0", tk.END).strip()
        if text:
            try:
                clipboard.copy(text)
            except RuntimeError:
                pass  # No clipboard available — silently skip

    def _set_text(self, text):
        self.text_widget.edit_modified(False)
        self.text_widget.delete("1.0", tk.END)
        self.text_widget.insert(tk.END, text)
        self.text_widget.edit_modified(False)
        self.text_widget.see(tk.END)
        self._sync_clipboard()

    def _start_model_load(self):
        def load():
            try:
                device, compute_type = self._resolve_device()
                self.model = WhisperModel(
                    self.model_name, device=device, compute_type=compute_type
                )
                self.root.after(0, self._on_model_loaded)
            except Exception as e:
                self.root.after(
                    0, lambda: self._set_text(f"Error loading model: {e}")
                )

        threading.Thread(target=load, daemon=True).start()

    def _resolve_device(self) -> tuple[str, str]:
        """Determine device and compute type. Try CUDA, fall back to CPU."""
        if self.device_override:
            if self.device_override == "cuda":
                return "cuda", "float16"
            return self.device_override, "int8"

        # Auto-detect: try CUDA first
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda", "float16"
        except ImportError:
            pass

        return "cpu", "int8"

    def _on_model_loaded(self):
        self._set_text("")
        self.start_recording()

    def start_recording(self):
        if self.recording or self.model is None:
            return
        self.recording = True
        self.audio_buffer = []
        self.status_label.configure(text="\u25cf Recording...", fg=ACCENT_RED)
        self.stop_btn.configure(state=tk.NORMAL)

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
        )
        self.stream.start()

        self.transcription_thread = threading.Thread(
            target=self._transcription_loop, daemon=True
        )
        self.transcription_thread.start()

    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            self.audio_queue.put(indata.copy())

    def _transcription_loop(self):
        while self.recording:
            chunks = []
            try:
                while True:
                    chunks.append(self.audio_queue.get_nowait())
            except queue.Empty:
                pass

            if chunks:
                self.audio_buffer.extend(chunks)

            if self.audio_buffer:
                total_samples = sum(c.shape[0] for c in self.audio_buffer)
                if total_samples >= SAMPLE_RATE * CHUNK_INTERVAL:
                    audio = np.concatenate(self.audio_buffer, axis=0).flatten()
                    self._run_transcription(audio)

            threading.Event().wait(0.5)

        # Final transcription of remaining audio
        chunks = []
        try:
            while True:
                chunks.append(self.audio_queue.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            self.audio_buffer.extend(chunks)

        if self.audio_buffer:
            audio = np.concatenate(self.audio_buffer, axis=0).flatten()
            self._run_transcription(audio)

    def _run_transcription(self, audio):
        try:
            segments, _ = self.model.transcribe(
                audio,
                language=self.language,
                vad_filter=True,
                beam_size=5,
            )
            text_parts = [segment.text.strip() for segment in segments]
            if text_parts:
                self.full_text = " ".join(text_parts)
                self.root.after(0, lambda t=self.full_text: self._set_text(t))
        except Exception as e:
            self.root.after(
                0, lambda: self._set_text(f"Transcription error: {e}")
            )

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        self.status_label.configure(text="\u25cf Stopped", fg=ACCENT_GREEN)
        self.stop_btn.configure(state=tk.DISABLED)

    def _handle_sigusr1(self, signum, frame):
        self.root.after(0, self.stop_recording)

    def copy_to_clipboard(self):
        self._sync_clipboard()
        self.status_label.configure(text="Copied!", fg=ACCENT_GREEN)
        self.root.after(2000, self._restore_status)

    def _restore_status(self):
        if self.recording:
            self.status_label.configure(
                text="\u25cf Recording...", fg=ACCENT_RED
            )
        else:
            self.status_label.configure(text="\u25cf Stopped", fg=ACCENT_GREEN)

    def close(self):
        self.stop_recording()
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(
        prog="voxtap",
        description="Speech-to-text with Whisper. Tap a key, get voice transcribed.",
    )
    parser.add_argument(
        "--model",
        default="small",
        help="Whisper model name (default: small)",
    )
    parser.add_argument(
        "--language",
        default="en",
        help="Language code (default: en)",
    )
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda", "auto"],
        default=None,
        help="Force device (default: auto-detect CUDA, fall back to CPU)",
    )
    args = parser.parse_args()

    device = args.device if args.device != "auto" else None

    app = SpeechToText(
        model_name=args.model, language=args.language, device=device
    )
    app.run()


if __name__ == "__main__":
    main()
