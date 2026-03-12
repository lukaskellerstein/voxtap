"""Main voxtap GUI — speech-to-text with Whisper and Qt."""

import argparse
import datetime
import inspect
import json
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import urllib.request
import urllib.error

import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from PyQt6.QtCore import (
    QEasingCurve, QObject, QPropertyAnimation, QTimer, Qt,
    pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QAction, QColor, QFont, QKeySequence, QPainter,
    QTextBlockFormat, QTextCharFormat, QTextCursor, QTextListFormat,
)
from PyQt6.QtWidgets import (
    QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout,
    QLabel, QMainWindow, QProgressBar, QPushButton, QTextEdit, QToolBar,
    QVBoxLayout, QWidget,
)

from voxtap import clipboard

# PID file location
if sys.platform == "win32":
    _base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
else:
    _base = os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache"))
CACHE_DIR = os.path.join(_base, "voxtap")
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
ACCENT_YELLOW = "#ffd43b"
ACCENT_PURPLE = "#b197fc"
BORDER_COLOR = "#45475a"

SAMPLE_RATE = 16000
CHUNK_INTERVAL = 3.0

# Initial prompt biases Whisper toward recognizing abbreviations and technical
# terms that it would otherwise expand into common speech (e.g. "UI" → "you
# are").  Keep it short — long prompts cause Whisper to hallucinate prompt
# content on silence.  Each term should appear only once.
INITIAL_PROMPT = (
    "UI, UX, API, URL, HTML, CSS, TypeScript, SDK, CLI, GPU, CPU, RAM, "
    "SQL, NoSQL, JSON, YAML, REST, GraphQL, ORM, IDE, CI, CD, DevOps, "
    "AWS, GCP, LLM, AI, ML, NLP, GPT, CUDA, OAuth, JWT, "
    "Docker, Kubernetes, kubectl, Helm, ConfigMap, DaemonSet, Dockerfile, "
    "Ollama, Whisper, Qwen, Claude, Anthropic"
)

LLM_SYSTEM_PROMPT = (
    "You are a post-processor for raw speech-to-text transcription. "
    "The input may have minor issues from automatic transcription.\n\n"
    "Your tasks:\n"
    "1. Remove filler words: um, uh, like, you know, I mean.\n"
    "2. Remove consecutively repeated words (e.g. 'kubectl kubectl kubectl' → 'kubectl').\n"
    "3. Fix punctuation, capitalization, spelling, and grammar.\n"
    "4. Keep technical terms (API, Kubernetes, Docker, Ollama, etc.) correctly cased.\n\n"
    "CRITICAL RULES:\n"
    "- NEVER remove or change content that could be intentional. "
    "When in doubt, keep it.\n"
    "- Preserve the speaker's original meaning, intent, and ALL topics exactly.\n"
    "- Do NOT add information, rephrase ideas, summarize, or change the tone.\n"
    "- Do NOT remove words or sentences just because they seem unrelated — "
    "the speaker may be discussing multiple topics.\n\n"
    "Output ONLY the cleaned text. No explanations, no commentary, no quotes."
)

# Common Whisper hallucination phrases (lowercase).
_HALLUCINATIONS = frozenset([
    "thank you for watching",
    "thanks for watching",
    "subscribe",
    "like and subscribe",
    "please subscribe",
    "thank you for listening",
    "thanks for listening",
    "see you next time",
    "bye bye",
    "goodbye",
    "the end",
    "subtitles by",
    "translated by",
    "amara.org",
    "www.mooji.org",
])

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gpt-oss:20b"

STYLESHEET = f"""
QMainWindow {{
    background-color: {BG_DARK};
}}
QTextEdit {{
    background-color: {BG_LIGHT};
    color: {FG_PRIMARY};
    border: none;
    padding: 10px;
    font-family: Sans;
    font-size: 13px;
    selection-background-color: {ACCENT_BLUE};
}}
QToolBar {{
    background-color: {BG_DARK};
    border: none;
    spacing: 4px;
    padding: 2px 4px;
}}
QToolBar QToolButton {{
    background-color: {BG_MEDIUM};
    color: {FG_PRIMARY};
    border: none;
    border-radius: 3px;
    padding: 4px 10px;
    font-size: 12px;
}}
QToolBar QToolButton:hover {{
    background-color: {BG_LIGHT};
}}
QToolBar QToolButton:checked {{
    background-color: {ACCENT_BLUE};
    color: {BG_DARK};
}}
QToolBar QToolButton:pressed {{
    background-color: {ACCENT_BLUE};
}}
QLabel#title {{
    font-size: 15px;
    font-weight: bold;
    color: {FG_PRIMARY};
}}
QLabel#status {{
    font-size: 12px;
    color: {FG_SECONDARY};
}}
"""

# Button base + pressed animation styles
BTN_BASE = (
    "QPushButton {{"
    "  border: none; border-radius: 4px; padding: 8px 18px;"
    "  font-weight: bold; font-size: 12px;"
    "  background-color: {bg}; color: {fg};"
    "}}"
    "QPushButton:hover {{ background-color: {hover}; }}"
    "QPushButton:pressed {{ background-color: {pressed}; padding-top: 10px; }}"
)


def _btn_style(bg: str, fg: str = BG_DARK) -> str:
    """Generate button stylesheet with hover/pressed states."""
    # Darken bg for pressed, lighten for hover
    c = QColor(bg)
    pressed = c.darker(140).name()
    hover = c.lighter(115).name()
    return BTN_BASE.format(bg=bg, fg=fg, hover=hover, pressed=pressed)


# --- Spotify / media control ---

def _media_pause():
    """Pause media players via D-Bus (works with Spotify, etc.)."""
    try:
        subprocess.Popen(
            ["dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify",
             "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.Pause"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def _media_play():
    """Resume media players via D-Bus."""
    try:
        subprocess.Popen(
            ["dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify",
             "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.Player.Play"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def _is_media_playing() -> bool:
    """Check if Spotify is currently playing."""
    try:
        result = subprocess.run(
            ["dbus-send", "--print-reply", "--dest=org.mpris.MediaPlayer2.spotify",
             "/org/mpris/MediaPlayer2",
             "org.freedesktop.DBus.Properties.Get",
             "string:org.mpris.MediaPlayer2.Player",
             "string:PlaybackStatus"],
            capture_output=True, text=True, timeout=2,
        )
        return "Playing" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# --- Waveform widget ---

class WaveformWidget(QWidget):
    """Animated audio waveform bars."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._num_bars = 7
        self._bar_w = 3
        self._gap = 3
        self._max_h = 22
        self._min_h = 3
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self.setFixedSize(
            self._num_bars * (self._bar_w + self._gap) - self._gap,
            self._max_h,
        )
        self.hide()

    def start(self):
        self._tick = 0
        self.show()
        self._timer.start(50)

    def stop(self):
        self._timer.stop()
        self.hide()

    def _animate(self):
        self._tick += 1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._tick * 0.15
        mid = self._max_h / 2

        for i in range(self._num_bars):
            phase = i * 0.9
            h_norm = (
                0.5 * math.sin(t + phase)
                + 0.3 * math.sin(t * 1.7 + phase * 0.6)
                + 0.2 * math.sin(t * 2.5 + phase * 1.3)
            )
            h = self._min_h + (h_norm + 1) / 2 * (self._max_h - self._min_h)
            x = i * (self._bar_w + self._gap)
            y_top = mid - h / 2

            brightness = int(
                80 + 175 * ((h - self._min_h) / (self._max_h - self._min_h))
            )
            color = QColor(brightness, int(brightness * 0.4), int(brightness * 0.4))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(
                int(x), int(y_top), self._bar_w, int(h), 1, 1,
            )

        painter.end()


# --- Polishing indicator (animated dots) ---

class PolishingWidget(QWidget):
    """Animated dots shown while the LLM polishes transcription."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._num_dots = 5
        self._dot_r = 3
        self._gap = 5
        self._tick = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self.setFixedSize(
            self._num_dots * (self._dot_r * 2 + self._gap) - self._gap,
            self._dot_r * 2 + 4,
        )
        self.hide()

    def start(self):
        self._tick = 0
        self.show()
        self._timer.start(60)

    def stop(self):
        self._timer.stop()
        self.hide()

    def _animate(self):
        self._tick += 1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t = self._tick * 0.18
        cy = self.height() / 2

        for i in range(self._num_dots):
            phase = i * 0.7
            # Each dot pulses in opacity and bounces vertically
            wave = (math.sin(t + phase) + 1) / 2  # 0..1
            alpha = int(80 + 175 * wave)
            y_off = -3 * math.sin(t + phase)
            x = i * (self._dot_r * 2 + self._gap) + self._dot_r
            color = QColor(ACCENT_BLUE)
            color.setAlpha(alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(
                int(x - self._dot_r), int(cy + y_off - self._dot_r),
                self._dot_r * 2, self._dot_r * 2,
            )

        painter.end()


# --- Animated border frame ---

class GlowFrame(QFrame):
    """Frame with animated border glow. Supports red (recording) and blue (polishing) modes."""

    # Color targets: (r, g, b) that the border pulses toward.
    _COLORS = {
        "red":  (0xff, 0x6b, 0x6b),
        "blue": (0x4d, 0xab, 0xf7),
    }
    _BASE = (0x45, 0x47, 0x5a)  # border color at rest

    def __init__(self, parent=None):
        super().__init__(parent)
        self._glow = 0.0
        self._color_mode = "red"
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._tick = 0
        self._update_border()

    def _get_glow(self):
        return self._glow

    def _set_glow(self, val):
        self._glow = val
        self._update_border()

    glow = pyqtProperty(float, _get_glow, _set_glow)

    def _update_border(self):
        p = self._glow
        tr, tg, tb = self._COLORS.get(self._color_mode, self._COLORS["red"])
        br, bg_, bb = self._BASE
        r = int(br + (tr - br) * p)
        g = int(bg_ + (tg - bg_) * p)
        b = int(bb + (tb - bb) * p)
        self.setStyleSheet(
            f"GlowFrame {{ border: 2px solid #{r:02x}{g:02x}{b:02x}; "
            f"border-radius: 4px; background-color: transparent; }}"
        )

    def start(self, color="red"):
        self._color_mode = color
        self._tick = 0
        self._timer.start(50)

    def stop(self):
        self._timer.stop()
        self._glow = 0.0
        self._update_border()

    def _animate(self):
        self._tick += 1
        t = self._tick * 0.04
        self._glow = math.sin(t) ** 2
        self._update_border()


# --- Transcription signal bridge ---

class TranscriptionBridge(QObject):
    """Thread-safe signal to deliver transcription text to the GUI."""
    text_ready = pyqtSignal(str)
    text_replaced = pyqtSignal(int, str)  # start_pos, polished_text
    polishing = pyqtSignal(bool)  # True = started, False = finished
    editor_text_requested = pyqtSignal()  # request current editor text from main thread
    error = pyqtSignal(str)
    download_progress = pyqtSignal(int, int)  # downloaded_bytes, total_bytes
    download_done = pyqtSignal()


class DownloadDialog(QDialog):
    """Modal progress dialog for model download."""

    def __init__(self, model_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Downloading model")
        self.setFixedSize(440, 150)
        self.setModal(True)
        self.setStyleSheet(
            f"QDialog {{ background-color: {BG_DARK}; }}"
            f"QLabel {{ color: {FG_PRIMARY}; font-size: 13px; }}"
            f"QProgressBar {{"
            f"  border: 1px solid {BORDER_COLOR}; border-radius: 4px;"
            f"  background-color: {BG_LIGHT}; text-align: center;"
            f"  color: {FG_PRIMARY}; font-size: 11px;"
            f"  min-height: 22px;"
            f"}}"
            f"QProgressBar::chunk {{"
            f"  background-color: {ACCENT_BLUE}; border-radius: 3px;"
            f"}}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        self._label = QLabel(f"Downloading Whisper model \"{model_name}\"...")
        layout.addWidget(self._label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # Indeterminate / pulsing
        layout.addWidget(self._progress)

        self._detail = QLabel("Connecting...")
        self._detail.setStyleSheet(
            f"color: {FG_SECONDARY}; font-size: 11px;"
        )
        layout.addWidget(self._detail)

    def update_progress(self, downloaded: int, total: int):
        if total > 0:
            # Switch to determinate mode
            total_mb = total // (1024 * 1024)
            dl_mb = downloaded // (1024 * 1024)
            self._progress.setRange(0, total_mb)
            self._progress.setValue(dl_mb)
            pct = int(downloaded * 100 / total) if total else 0
            self._detail.setText(f"{dl_mb} MB / {total_mb} MB  ({pct}%)")
        else:
            # Keep pulsing, show bytes downloaded
            dl_mb = downloaded // (1024 * 1024)
            self._detail.setText(f"Downloaded {dl_mb} MB...")


# --- Main window ---

class SpeechToTextWindow(QMainWindow):
    def __init__(self, model_name="large-v3", language="en", device=None):
        super().__init__()
        self.model_name = model_name
        self.language = language
        self.device_override = device
        self.model = None
        self.audio_queue: queue.Queue = queue.Queue()
        self.audio_buffer: list = []
        self.recording = False
        self.stream = None
        self.transcription_thread = None
        self._media_was_playing = False
        self._recorded_audio = None  # Full recording as numpy array
        self._playback_stream = None  # sounddevice OutputStream for playback
        self._playback_pos = 0  # Current playback position in samples

        self._bridge = TranscriptionBridge()
        self._bridge.text_ready.connect(self._insert_transcription)
        self._bridge.text_replaced.connect(self._replace_session_text)
        self._bridge.polishing.connect(self._on_polishing)
        self._bridge.editor_text_requested.connect(self._provide_editor_text)
        self._bridge.error.connect(
            lambda msg: self.status_label.setText(f"Error: {msg}")
        )
        # Accumulates raw transcription text during a recording session.
        # Used to polish the full text when recording stops.
        self._session_raw_parts: list[str] = []
        # Cursor position where the current session's text starts, so we can
        # replace raw text with the polished version.
        self._session_start_pos: int = 0
        # Thread-safe mechanism to read editor text from background thread.
        self._editor_text_response = ""
        self._editor_text_event = threading.Event()
        # Tracks whether we are currently polishing or have finished polishing.
        self._is_polishing = False
        self._polish_done = False

        self.setWindowTitle("voxtap")
        self.setMinimumSize(400, 300)
        self.resize(750, 520)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet(STYLESHEET)

        self._setup_gui()

        # IPC toggle: SIGUSR1 on POSIX, named event on Windows
        if sys.platform == "win32":
            self._setup_win32_toggle()
        else:
            signal.signal(signal.SIGUSR1, self._handle_sigusr1)

        # PID file
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))

        # Load model
        QTimer.singleShot(100, self._start_model_load)

    def _setup_gui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # --- Title bar ---
        title_bar = QWidget()
        title_bar.setFixedHeight(50)
        title_bar.setStyleSheet(f"background-color: {BG_MEDIUM};")
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(15, 0, 15, 0)

        title_label = QLabel("voxtap")
        title_label.setObjectName("title")
        tb_layout.addWidget(title_label)

        tb_layout.addStretch()

        self.waveform = WaveformWidget()
        tb_layout.addWidget(self.waveform)

        self.polish_indicator = PolishingWidget()
        tb_layout.addWidget(self.polish_indicator)

        self.status_label = QLabel("Loading model...")
        self.status_label.setObjectName("status")
        tb_layout.addWidget(self.status_label)

        layout.addWidget(title_bar)

        # --- Model info strip ---
        info_bar = QWidget()
        info_bar.setFixedHeight(22)
        info_bar.setStyleSheet(f"background-color: {BG_DARK};")
        info_layout = QHBoxLayout(info_bar)
        info_layout.setContentsMargins(15, 0, 15, 0)
        info_layout.setSpacing(12)

        style_info = f"color: {FG_SECONDARY}; font-size: 10px;"
        whisper_label = QLabel(f"STT: {self.model_name}")
        whisper_label.setStyleSheet(style_info)
        info_layout.addWidget(whisper_label)

        llm_label = QLabel(f"LLM: {OLLAMA_MODEL}")
        llm_label.setStyleSheet(style_info)
        info_layout.addWidget(llm_label)

        info_layout.addStretch()
        layout.addWidget(info_bar)

        # --- Formatting toolbar ---
        toolbar = QToolBar()
        toolbar.setMovable(False)

        # Bold
        self._bold_action = QAction("B", self)
        self._bold_action.setCheckable(True)
        self._bold_action.setFont(QFont("Sans", 11, QFont.Weight.Bold))
        self._bold_action.setShortcut(QKeySequence("Ctrl+B"))
        self._bold_action.triggered.connect(self._toggle_bold)
        toolbar.addAction(self._bold_action)

        # Italic
        self._italic_action = QAction("I", self)
        self._italic_action.setCheckable(True)
        f = QFont("Sans", 11)
        f.setItalic(True)
        self._italic_action.setFont(f)
        self._italic_action.setShortcut(QKeySequence("Ctrl+I"))
        self._italic_action.triggered.connect(self._toggle_italic)
        toolbar.addAction(self._italic_action)

        # Underline
        self._underline_action = QAction("U", self)
        self._underline_action.setCheckable(True)
        uf = QFont("Sans", 11)
        uf.setUnderline(True)
        self._underline_action.setFont(uf)
        self._underline_action.setShortcut(QKeySequence("Ctrl+U"))
        self._underline_action.triggered.connect(self._toggle_underline)
        toolbar.addAction(self._underline_action)

        # Strikethrough
        self._strike_action = QAction("S\u0336", self)
        self._strike_action.setCheckable(True)
        self._strike_action.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._strike_action.triggered.connect(self._toggle_strikethrough)
        toolbar.addAction(self._strike_action)

        toolbar.addSeparator()

        # Headings
        self._h1_action = QAction("H1", self)
        self._h1_action.setCheckable(True)
        self._h1_action.triggered.connect(lambda: self._set_heading(1))
        toolbar.addAction(self._h1_action)

        self._h2_action = QAction("H2", self)
        self._h2_action.setCheckable(True)
        self._h2_action.triggered.connect(lambda: self._set_heading(2))
        toolbar.addAction(self._h2_action)

        self._h3_action = QAction("H3", self)
        self._h3_action.setCheckable(True)
        self._h3_action.triggered.connect(lambda: self._set_heading(3))
        toolbar.addAction(self._h3_action)

        toolbar.addSeparator()

        # Lists
        self._bullet_action = QAction("\u2022 List", self)
        self._bullet_action.setCheckable(True)
        self._bullet_action.triggered.connect(self._toggle_bullet)
        toolbar.addAction(self._bullet_action)

        self._numbered_action = QAction("1. List", self)
        self._numbered_action.setCheckable(True)
        self._numbered_action.triggered.connect(self._toggle_numbered)
        toolbar.addAction(self._numbered_action)

        toolbar.addSeparator()

        # Alignment
        self._align_left = QAction("\u2261 Left", self)
        self._align_left.triggered.connect(
            lambda: self._set_alignment(Qt.AlignmentFlag.AlignLeft)
        )
        toolbar.addAction(self._align_left)

        self._align_center = QAction("\u2261 Center", self)
        self._align_center.triggered.connect(
            lambda: self._set_alignment(Qt.AlignmentFlag.AlignCenter)
        )
        toolbar.addAction(self._align_center)

        self._align_right = QAction("\u2261 Right", self)
        self._align_right.triggered.connect(
            lambda: self._set_alignment(Qt.AlignmentFlag.AlignRight)
        )
        toolbar.addAction(self._align_right)

        layout.addWidget(toolbar)

        # --- Editor with glow border ---
        editor_container = QWidget()
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(15, 6, 15, 10)

        self.glow_frame = GlowFrame()
        glow_inner = QVBoxLayout(self.glow_frame)
        glow_inner.setContentsMargins(0, 0, 0, 0)

        self.editor = QTextEdit()
        self.editor.setAcceptRichText(True)
        self.editor.setFont(QFont("Sans", 13))
        self.editor.cursorPositionChanged.connect(self._update_format_buttons)
        self.editor.installEventFilter(self)
        glow_inner.addWidget(self.editor)

        editor_layout.addWidget(self.glow_frame)
        layout.addWidget(editor_container, stretch=1)

        # --- Button bar ---
        btn_bar = QWidget()
        btn_bar.setFixedHeight(55)
        btn_bar.setStyleSheet(f"background-color: {BG_MEDIUM};")
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.addStretch()

        self.record_btn = QPushButton("Record")
        self.record_btn.setStyleSheet(_btn_style(ACCENT_RED))
        self.record_btn.setEnabled(False)
        self.record_btn.clicked.connect(self._toggle_recording)
        btn_layout.addWidget(self.record_btn)

        self.play_btn = QPushButton("Play")
        self.play_btn.setStyleSheet(_btn_style(ACCENT_GREEN))
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self._toggle_playback)
        btn_layout.addWidget(self.play_btn)

        self.transcribe_btn = QPushButton("Transcribe")
        self.transcribe_btn.setStyleSheet(_btn_style(ACCENT_PURPLE))
        self.transcribe_btn.setEnabled(False)
        self.transcribe_btn.clicked.connect(self._transcribe_full_recording)
        btn_layout.addWidget(self.transcribe_btn)

        self.polish_btn = QPushButton("Polish")
        self.polish_btn.setStyleSheet(_btn_style(ACCENT_BLUE))
        self.polish_btn.setEnabled(False)
        self.polish_btn.clicked.connect(self._polish_current_text)
        btn_layout.addWidget(self.polish_btn)

        copy_btn = QPushButton("Copy as Markdown")
        copy_btn.setStyleSheet(_btn_style(ACCENT_YELLOW))
        copy_btn.clicked.connect(self.copy_to_clipboard)
        btn_layout.addWidget(copy_btn)

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(_btn_style(FG_SECONDARY))
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)

        btn_layout.addStretch()
        layout.addWidget(btn_bar)

    def eventFilter(self, obj, event):
        """Handle Ctrl+V: fast text paste, only check images if no text."""
        from PyQt6.QtCore import QEvent
        if (
            obj is self.editor
            and event.type() == QEvent.Type.KeyPress
            and event.modifiers() == Qt.KeyboardModifier.ControlModifier
            and event.key() == Qt.Key.Key_V
        ):
            # Fast path: if clipboard has text, let Qt handle it natively
            cb = QApplication.clipboard()
            if cb.text():
                return False  # Default paste — instant

            # No text in clipboard — check for image (slower, uses xclip)
            path = self._try_get_clipboard_image_path()
            if path:
                self.editor.insertPlainText(path)
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        # Don't intercept shortcuts when editor has focus (user is typing)
        if self.editor.hasFocus():
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_Escape:
            if self.recording:
                self.stop_recording()
            elif self._playback_stream is not None:
                self._stop_playback()
            elif self._is_polishing:
                pass
            elif self._polish_done:
                self.copy_to_clipboard()
                self.close()
            else:
                self.close()
        elif event.key() == Qt.Key.Key_R:
            self._toggle_recording()
        elif event.key() == Qt.Key.Key_P:
            self._toggle_playback()
        elif event.key() == Qt.Key.Key_T:
            self._transcribe_full_recording()
        elif event.key() == Qt.Key.Key_L:
            self._polish_current_text()
        else:
            super().keyPressEvent(event)

    # --- Formatting ---

    def _toggle_bold(self):
        fmt = QTextCharFormat()
        cursor = self.editor.textCursor()
        if cursor.charFormat().fontWeight() == QFont.Weight.Bold:
            fmt.setFontWeight(QFont.Weight.Normal)
        else:
            fmt.setFontWeight(QFont.Weight.Bold)
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)
        self._update_format_buttons()

    def _toggle_italic(self):
        fmt = QTextCharFormat()
        cursor = self.editor.textCursor()
        fmt.setFontItalic(not cursor.charFormat().fontItalic())
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)
        self._update_format_buttons()

    def _toggle_underline(self):
        fmt = QTextCharFormat()
        cursor = self.editor.textCursor()
        fmt.setFontUnderline(not cursor.charFormat().fontUnderline())
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)
        self._update_format_buttons()

    def _toggle_strikethrough(self):
        fmt = QTextCharFormat()
        cursor = self.editor.textCursor()
        fmt.setFontStrikeOut(not cursor.charFormat().fontStrikeOut())
        cursor.mergeCharFormat(fmt)
        self.editor.mergeCurrentCharFormat(fmt)
        self._update_format_buttons()

    def _set_heading(self, level: int):
        cursor = self.editor.textCursor()
        block_fmt = cursor.blockFormat()
        char_fmt = QTextCharFormat()

        # Toggle: if already this heading level, remove it
        current_level = block_fmt.headingLevel()
        if current_level == level:
            block_fmt.setHeadingLevel(0)
            char_fmt.setFontWeight(QFont.Weight.Normal)
            char_fmt.setProperty(QTextCharFormat.Property.FontSizeAdjustment, 0)
        else:
            block_fmt.setHeadingLevel(level)
            char_fmt.setFontWeight(QFont.Weight.Bold)
            size_adj = {1: 4, 2: 2, 3: 1}[level]
            char_fmt.setProperty(
                QTextCharFormat.Property.FontSizeAdjustment, size_adj,
            )

        cursor.setBlockFormat(block_fmt)
        cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
        cursor.mergeCharFormat(char_fmt)
        self._update_format_buttons()

    def _toggle_bullet(self):
        cursor = self.editor.textCursor()
        lst = cursor.currentList()
        if lst and lst.format().style() == QTextListFormat.Style.ListDisc:
            block = cursor.block()
            lst.remove(block)
            fmt = block.blockFormat()
            fmt.setIndent(0)
            cursor.setBlockFormat(fmt)
        else:
            list_fmt = QTextListFormat()
            list_fmt.setStyle(QTextListFormat.Style.ListDisc)
            cursor.createList(list_fmt)
        self._update_format_buttons()

    def _toggle_numbered(self):
        cursor = self.editor.textCursor()
        lst = cursor.currentList()
        if lst and lst.format().style() == QTextListFormat.Style.ListDecimal:
            block = cursor.block()
            lst.remove(block)
            fmt = block.blockFormat()
            fmt.setIndent(0)
            cursor.setBlockFormat(fmt)
        else:
            list_fmt = QTextListFormat()
            list_fmt.setStyle(QTextListFormat.Style.ListDecimal)
            cursor.createList(list_fmt)
        self._update_format_buttons()

    def _set_alignment(self, alignment):
        cursor = self.editor.textCursor()
        fmt = cursor.blockFormat()
        fmt.setAlignment(alignment)
        cursor.mergeBlockFormat(fmt)

    def _update_format_buttons(self):
        cursor = self.editor.textCursor()
        fmt = cursor.charFormat()
        self._bold_action.setChecked(fmt.fontWeight() == QFont.Weight.Bold)
        self._italic_action.setChecked(fmt.fontItalic())
        self._underline_action.setChecked(fmt.fontUnderline())
        self._strike_action.setChecked(fmt.fontStrikeOut())

        block_fmt = cursor.blockFormat()
        hl = block_fmt.headingLevel()
        self._h1_action.setChecked(hl == 1)
        self._h2_action.setChecked(hl == 2)
        self._h3_action.setChecked(hl == 3)

        lst = cursor.currentList()
        if lst:
            style = lst.format().style()
            self._bullet_action.setChecked(
                style == QTextListFormat.Style.ListDisc
            )
            self._numbered_action.setChecked(
                style == QTextListFormat.Style.ListDecimal
            )
        else:
            self._bullet_action.setChecked(False)
            self._numbered_action.setChecked(False)

    # --- Clipboard image paste ---

    def _try_get_clipboard_image_path(self) -> str | None:
        if sys.platform == "win32":
            return self._get_clipboard_image_path_windows()
        if sys.platform == "darwin":
            return self._get_clipboard_image_path_macos()
        return self._get_clipboard_image_path_linux()

    def _get_clipboard_image_path_linux(self) -> str | None:
        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
                capture_output=True, text=True, timeout=2,
            )
            targets = [t.strip() for t in result.stdout.strip().splitlines()]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

        for target_type in ("text/uri-list", "x-special/gnome-copied-files"):
            if target_type in targets:
                try:
                    result = subprocess.run(
                        ["xclip", "-selection", "clipboard", "-t", target_type, "-o"],
                        capture_output=True, text=True, timeout=2,
                    )
                    for line in result.stdout.strip().splitlines():
                        line = line.strip()
                        if line.startswith("file://"):
                            from urllib.parse import unquote, urlparse
                            path = unquote(urlparse(line).path)
                            if os.path.isfile(path):
                                return path
                        elif line.startswith("/") and os.path.isfile(line):
                            return line
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

        has_image = any(
            t in ("image/png", "image/jpeg", "image/bmp") for t in targets
        )
        if not has_image:
            return None

        image_target = next(
            t for t in targets
            if t in ("image/png", "image/jpeg", "image/bmp")
        )
        ext = image_target.split("/")[1]
        if ext == "jpeg":
            ext = "jpg"

        images_dir = os.path.join(CACHE_DIR, "images")
        os.makedirs(images_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(images_dir, f"paste_{timestamp}.{ext}")

        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", image_target, "-o"],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout:
                with open(filepath, "wb") as f:
                    f.write(result.stdout)
                return filepath
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return None

    def _get_clipboard_image_path_windows(self) -> str | None:
        # Try file paths first (e.g. copied file in Explorer)
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Clipboard -Format FileDropList | Select-Object -ExpandProperty FullName"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    path = line.strip()
                    if os.path.isfile(path):
                        return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Try screenshot / image in clipboard
        images_dir = os.path.join(CACHE_DIR, "images")
        os.makedirs(images_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(images_dir, f"paste_{timestamp}.png")

        ps_script = (
            "$img = Get-Clipboard -Format Image; "
            f"if ($img) {{ $img.Save('{filepath}') }}"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0 and os.path.isfile(filepath):
                return filepath
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        return None

    def _get_clipboard_image_path_macos(self) -> str | None:
        try:
            result = subprocess.run(
                ["osascript", "-e", 'the clipboard as «class furl»'],
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                path = result.stdout.strip()
                if path.startswith("file://"):
                    from urllib.parse import unquote, urlparse
                    path = unquote(urlparse(path).path)
                if os.path.isfile(path):
                    return path
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

    # --- Copy as markdown ---

    def _to_markdown(self) -> str:
        doc = self.editor.document()
        md_lines = []
        block = doc.begin()

        while block.isValid():
            it = block.begin()
            md_parts = []

            while not it.atEnd():
                fragment = it.fragment()
                if fragment.isValid():
                    fmt = fragment.charFormat()
                    frag_text = fragment.text()

                    is_bold = fmt.fontWeight() == QFont.Weight.Bold
                    is_italic = fmt.fontItalic()
                    is_strike = fmt.fontStrikeOut()

                    # Don't wrap heading text in bold markers (already bold visually)
                    if block.blockFormat().headingLevel() > 0:
                        is_bold = False

                    if is_strike:
                        frag_text = f"~~{frag_text}~~"
                    if is_bold and is_italic:
                        frag_text = f"***{frag_text}***"
                    elif is_bold:
                        frag_text = f"**{frag_text}**"
                    elif is_italic:
                        frag_text = f"*{frag_text}*"

                    md_parts.append(frag_text)
                it += 1

            line = "".join(md_parts)

            # Headings
            hl = block.blockFormat().headingLevel()
            if hl > 0:
                line = "#" * hl + " " + line

            # Lists
            lst = block.textList()
            if lst:
                style = lst.format().style()
                if style == QTextListFormat.Style.ListDisc:
                    line = f"- {line}"
                elif style == QTextListFormat.Style.ListDecimal:
                    idx = lst.itemNumber(block) + 1
                    line = f"{idx}. {line}"

            md_lines.append(line)
            block = block.next()

        while md_lines and not md_lines[-1].strip():
            md_lines.pop()

        return "\n".join(md_lines)

    def copy_to_clipboard(self):
        md = self._to_markdown()
        if md:
            try:
                clipboard.copy(md)
            except RuntimeError:
                pass
        self.status_label.setText("Copied!")
        self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")
        QTimer.singleShot(2000, self._restore_status)

    # --- Transcription insertion ---

    def _on_polishing(self, active):
        self._is_polishing = active
        if active:
            self._polish_done = False
            self._pre_polish_status = self.status_label.text()
            self._pre_polish_style = self.status_label.styleSheet()
            self.status_label.setText("Polishing...")
            self.status_label.setStyleSheet(f"color: {ACCENT_BLUE};")
            self.polish_indicator.start()
            self.glow_frame.start("blue")
        else:
            self._polish_done = True
            self.polish_indicator.stop()
            self.glow_frame.stop()
            self.status_label.setText("Polished — Esc to copy & close")
            self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")

    def _insert_transcription(self, text):
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            cursor.removeSelectedText()
            cursor.insertText(text)
        else:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            current = self.editor.toPlainText()
            if current:
                cursor.insertText(" " + text)
            else:
                cursor.insertText(text)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()

    def _replace_session_text(self, start_pos, polished):
        """Replace raw session text (from start_pos to end) with polished text."""
        cursor = self.editor.textCursor()
        cursor.setPosition(start_pos)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        # Add leading space if session didn't start at beginning
        prefix = " " if start_pos > 0 else ""
        cursor.insertText(prefix + polished)
        self.editor.setTextCursor(cursor)
        self.editor.ensureCursorVisible()

    def _provide_editor_text(self):
        """Main-thread slot: read session text from editor and hand it to the background thread."""
        plain = self.editor.toPlainText()
        self._editor_text_response = plain[self._session_start_pos:]
        self._editor_text_event.set()

    # --- Model loading ---

    def _start_model_load(self):
        # Check if model is already cached
        from faster_whisper.utils import download_model

        needs_download = False
        try:
            download_model(self.model_name, local_files_only=True)
        except Exception:
            needs_download = True

        if needs_download:
            self._download_dialog = DownloadDialog(self.model_name, self)
            self._bridge.download_progress.connect(
                self._download_dialog.update_progress
            )
            self._bridge.download_done.connect(self._download_dialog.accept)
            self._download_dialog.show()

        def load():
            try:
                if needs_download:
                    self._download_with_progress()

                device, compute_type = self._resolve_device()
                self.model = WhisperModel(
                    self.model_name, device=device, compute_type=compute_type,
                )
                QTimer.singleShot(0, self._on_model_loaded)
            except Exception as e:
                self._bridge.error.emit(str(e))

        threading.Thread(target=load, daemon=True).start()

    def _download_with_progress(self):
        """Download model files with progress reporting."""
        import fnmatch

        import huggingface_hub
        from faster_whisper.utils import _MODELS

        repo_id = _MODELS.get(self.model_name, self.model_name)

        allow_patterns = [
            "config.json", "preprocessor_config.json", "model.bin",
            "tokenizer.json", "vocabulary.*",
        ]

        # Get total size from repo metadata
        total_size = 0
        try:
            info = huggingface_hub.model_info(repo_id, files_metadata=True)
            for f in (info.siblings or []):
                if f.size and any(
                    fnmatch.fnmatch(f.rfilename, p) for p in allow_patterns
                ):
                    total_size += f.size
        except Exception:
            pass  # Will use indeterminate progress

        downloaded_bytes = 0
        bridge = self._bridge

        from tqdm import tqdm as _tqdm_base

        class ProgressTqdm(_tqdm_base):
            """tqdm subclass that reports download progress via Qt signal."""
            def __init__(self_, *args, **kwargs):
                # Strip unknown kwargs that huggingface_hub may pass
                known = set(inspect.signature(_tqdm_base.__init__).parameters)
                clean = {k: v for k, v in kwargs.items() if k in known}
                clean["disable"] = False
                clean.setdefault("file", open(os.devnull, "w"))
                super().__init__(*args, **clean)

            def update(self_, n=1):
                nonlocal downloaded_bytes
                super().update(n)
                if isinstance(n, (int, float)):
                    downloaded_bytes += int(n)
                    bridge.download_progress.emit(
                        downloaded_bytes, total_size,
                    )

        huggingface_hub.snapshot_download(
            repo_id,
            allow_patterns=allow_patterns,
            tqdm_class=ProgressTqdm,
        )
        bridge.download_done.emit()

    def _resolve_device(self) -> tuple[str, str]:
        if self.device_override:
            if self.device_override == "cuda":
                return "cuda", "int8_float16"
            return self.device_override, "int8"

        try:
            import torch
            if torch.cuda.is_available():
                return "cuda", "int8_float16"
        except ImportError:
            pass

        return "cpu", "int8"

    def _on_model_loaded(self):
        self.record_btn.setEnabled(True)
        self.status_label.setText("Ready — press R to record")
        self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")

    # --- Recording ---

    def _toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.recording or self.model is None:
            return
        self._stop_playback()
        self.recording = True
        self.audio_buffer = []
        self._recorded_audio = None
        self._session_raw_parts = []
        self._session_start_pos = len(self.editor.toPlainText())
        self._polish_done = False
        self._is_polishing = False

        # Disable play/transcribe/polish while recording
        self.play_btn.setEnabled(False)
        self.transcribe_btn.setEnabled(False)
        self.polish_btn.setEnabled(False)

        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

        # Pause Spotify if playing
        self._media_was_playing = _is_media_playing()
        if self._media_was_playing:
            _media_pause()

        self.status_label.setText("Recording")
        self.status_label.setStyleSheet(f"color: {ACCENT_RED};")
        self.record_btn.setText("Stop")
        self.record_btn.setStyleSheet(_btn_style(ACCENT_YELLOW))
        self.waveform.start()
        self.glow_frame.start()

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1,
            dtype="float32", callback=self._audio_callback,
        )
        self.stream.start()

        self.transcription_thread = threading.Thread(
            target=self._recording_accumulate_loop, daemon=True,
        )
        self.transcription_thread.start()

    def _audio_callback(self, indata, frames, time_info, status):
        if self.recording:
            self.audio_queue.put(indata.copy())

    def _recording_accumulate_loop(self):
        """Background thread: drain audio queue into buffer while recording."""
        while self.recording:
            chunks = []
            try:
                while True:
                    chunks.append(self.audio_queue.get_nowait())
            except queue.Empty:
                pass
            if chunks:
                self.audio_buffer.extend(chunks)
            threading.Event().wait(0.1)

        # Drain any remaining chunks
        chunks = []
        try:
            while True:
                chunks.append(self.audio_queue.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            self.audio_buffer.extend(chunks)

        # Store the full recorded audio
        if self.audio_buffer:
            self._recorded_audio = np.concatenate(
                self.audio_buffer, axis=0,
            ).flatten()
            self.audio_buffer = []
            # Enable playback and transcription buttons from main thread
            QTimer.singleShot(0, self._on_recording_stored)

    def _postprocess_text(self, text):
        """Send text to local Ollama LLM for correction. Falls back to raw text."""
        payload = json.dumps({
            "model": OLLAMA_MODEL,
            "stream": False,
            "think": False,
            "options": {"num_predict": max(len(text) * 2, 256)},
            "messages": [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        }).encode()
        req = urllib.request.Request(
            OLLAMA_URL, data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read())
            corrected = result["message"]["content"].strip()
            return corrected if corrected else text
        except (urllib.error.URLError, KeyError, TimeoutError):
            return text

    def _run_transcription(self, audio):
        try:
            # Prepend 300ms of silence so VAD does not clip speech onset.
            pad = np.zeros(int(SAMPLE_RATE * 0.3), dtype=audio.dtype)
            audio = np.concatenate([pad, audio])

            segments, _ = self.model.transcribe(
                audio, language=self.language,
                vad_filter=True, beam_size=5,
                vad_parameters=dict(
                    threshold=0.3,
                    min_speech_duration_ms=100,
                    speech_pad_ms=300,
                ),
                initial_prompt=INITIAL_PROMPT,
                condition_on_previous_text=False,
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                compression_ratio_threshold=1.35,
                no_speech_threshold=0.45,
                log_prob_threshold=-0.5,
                repetition_penalty=1.1,
                no_repeat_ngram_size=3,
            )
            # Filter out hallucinated segments.
            prompt_lower = INITIAL_PROMPT.lower()
            text_parts = []
            for seg in segments:
                t = seg.text.strip()
                if not t:
                    continue
                t_lower = t.lower()
                # Drop known Whisper hallucination phrases
                if any(h in t_lower for h in _HALLUCINATIONS):
                    continue
                # Drop segments whose words all appear in the prompt
                words = [w.strip(".,;:!? ") for w in t_lower.split()]
                if words and all(w in prompt_lower for w in words):
                    continue
                # Drop very low confidence segments
                if seg.avg_logprob < -1.0:
                    continue
                # Drop segments that are just the same word repeated
                unique = set(words)
                if len(words) > 2 and len(unique) == 1:
                    continue
                text_parts.append(t)
            if text_parts:
                raw_text = " ".join(text_parts)
                self._session_raw_parts.append(raw_text)
                # Emit raw text immediately for live feedback
                self._bridge.text_ready.emit(raw_text)
        except Exception as e:
            self._bridge.error.emit(str(e))

    def _polish_session(self):
        """Replace the raw session text with LLM-polished version."""
        if not self._session_raw_parts:
            return
        # Read the actual editor text (includes any manual edits the user made).
        self._editor_text_event.clear()
        self._bridge.editor_text_requested.emit()
        if not self._editor_text_event.wait(timeout=5):
            # Fallback to raw parts if main thread didn't respond in time.
            full_text = " ".join(self._session_raw_parts)
        else:
            full_text = self._editor_text_response.strip()
        if not full_text:
            return
        self._bridge.polishing.emit(True)
        try:
            polished = self._postprocess_text(full_text)
        finally:
            self._bridge.polishing.emit(False)
        # Replace the session text in the editor with the polished version
        self._bridge.text_replaced.emit(self._session_start_pos, polished)

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        self.waveform.stop()
        self.glow_frame.stop()
        self.status_label.setText("Processing recording...")
        self.status_label.setStyleSheet(f"color: {ACCENT_YELLOW};")
        self.record_btn.setText("Record")
        self.record_btn.setStyleSheet(_btn_style(ACCENT_RED))

        # Resume Spotify if it was playing before
        if self._media_was_playing:
            _media_play()
            self._media_was_playing = False

    def _on_recording_stored(self):
        """Called from main thread after recording audio is stored."""
        if self._recorded_audio is not None and len(self._recorded_audio) > 0:
            duration = len(self._recorded_audio) / SAMPLE_RATE
            self.play_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(True)
            self.status_label.setText(
                f"Recorded {duration:.1f}s — Play, Transcribe, or Polish"
            )
            self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")
        else:
            self.status_label.setText("No audio recorded")
            self.status_label.setStyleSheet(f"color: {FG_SECONDARY};")

    # --- Playback ---

    def _toggle_playback(self):
        if self._playback_stream is not None:
            self._stop_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        if self._recorded_audio is None:
            return
        self._playback_pos = 0
        self.play_btn.setText("Stop")
        self.play_btn.setStyleSheet(_btn_style(ACCENT_YELLOW))
        self.record_btn.setEnabled(False)
        self.transcribe_btn.setEnabled(False)
        self.polish_btn.setEnabled(False)
        self.status_label.setText("Playing...")
        self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")

        def playback_callback(outdata, frames, time_info, status):
            end = self._playback_pos + frames
            audio = self._recorded_audio
            if self._playback_pos >= len(audio):
                outdata[:] = 0
                raise sd.CallbackStop()
            chunk = audio[self._playback_pos:end]
            if len(chunk) < frames:
                outdata[:len(chunk), 0] = chunk
                outdata[len(chunk):] = 0
                self._playback_pos = len(audio)
                raise sd.CallbackStop()
            else:
                outdata[:, 0] = chunk
                self._playback_pos = end

        self._playback_stream = sd.OutputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="float32",
            callback=playback_callback,
            finished_callback=lambda: QTimer.singleShot(0, self._on_playback_finished),
        )
        self._playback_stream.start()

    def _stop_playback(self):
        if self._playback_stream is not None:
            stream = self._playback_stream
            self._playback_stream = None
            stream.stop()
            stream.close()
            self._on_playback_finished()

    def _on_playback_finished(self):
        if self._playback_stream is not None:
            self._playback_stream.close()
        self._playback_stream = None
        self.play_btn.setText("Play")
        self.play_btn.setStyleSheet(_btn_style(ACCENT_GREEN))
        self.record_btn.setEnabled(True)
        self.polish_btn.setEnabled(True)
        if self._recorded_audio is not None:
            self.transcribe_btn.setEnabled(True)
            duration = len(self._recorded_audio) / SAMPLE_RATE
            self.status_label.setText(
                f"Recorded {duration:.1f}s — Play, Transcribe, or Polish"
            )
        self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")

    # --- Full transcription ---

    def _transcribe_full_recording(self):
        """Transcribe the entire recorded audio."""
        if self._recorded_audio is None or self.model is None:
            return
        self._stop_playback()
        self.play_btn.setEnabled(False)
        self.transcribe_btn.setEnabled(False)
        self.polish_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self._session_raw_parts = []
        self._session_start_pos = len(self.editor.toPlainText())
        self._polish_done = False
        self._is_polishing = False

        self.status_label.setText("Transcribing...")
        self.status_label.setStyleSheet(f"color: {ACCENT_BLUE};")
        self.glow_frame.start("blue")

        def run():
            self._run_transcription(self._recorded_audio)
            QTimer.singleShot(0, self._on_transcription_complete)

        threading.Thread(target=run, daemon=True).start()

    def _on_transcription_complete(self):
        self.glow_frame.stop()
        self.record_btn.setEnabled(True)
        if self._recorded_audio is not None:
            self.play_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(True)
        self.polish_btn.setEnabled(True)
        self.status_label.setText("Transcribed — Polish to refine, or edit manually")
        self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")

    def _polish_current_text(self):
        """Polish the current editor text with LLM."""
        text = self.editor.toPlainText().strip()
        if not text:
            return
        self.polish_btn.setEnabled(False)
        self.record_btn.setEnabled(False)
        self.play_btn.setEnabled(False)
        self.transcribe_btn.setEnabled(False)
        self._session_start_pos = 0
        self._session_raw_parts = [text]

        def run():
            self._polish_session()
            QTimer.singleShot(0, self._on_polish_complete)

        threading.Thread(target=run, daemon=True).start()

    def _on_polish_complete(self):
        self.record_btn.setEnabled(True)
        self.polish_btn.setEnabled(True)
        if self._recorded_audio is not None:
            self.play_btn.setEnabled(True)
            self.transcribe_btn.setEnabled(True)

    def _handle_sigusr1(self, signum, frame):
        QTimer.singleShot(0, self._toggle_recording)

    def _setup_win32_toggle(self):
        """Create a Win32 named event and poll it with a QTimer."""
        import ctypes
        kernel32 = ctypes.windll.kernel32
        self._win32_event = kernel32.CreateEventW(None, True, False,
                                                   "voxtap_toggle_event")
        self._win32_timer = QTimer(self)
        self._win32_timer.timeout.connect(self._poll_win32_event)
        self._win32_timer.start(200)

    def _poll_win32_event(self):
        import ctypes
        kernel32 = ctypes.windll.kernel32
        WAIT_OBJECT_0 = 0
        result = kernel32.WaitForSingleObject(self._win32_event, 0)
        if result == WAIT_OBJECT_0:
            kernel32.ResetEvent(self._win32_event)
            self._toggle_recording()

    def _restore_status(self):
        if self.recording:
            self.status_label.setText("Recording")
            self.status_label.setStyleSheet(f"color: {ACCENT_RED};")
        elif self._recorded_audio is not None:
            duration = len(self._recorded_audio) / SAMPLE_RATE
            self.status_label.setText(
                f"Recorded {duration:.1f}s — Play, Transcribe, or Polish"
            )
            self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")
        else:
            self.status_label.setText("Ready — press R to record")
            self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")

    def closeEvent(self, event):
        self._stop_playback()
        self.stop_recording()
        # Copy text as markdown to clipboard on exit
        md = self._to_markdown()
        if md:
            try:
                clipboard.copy(md)
            except RuntimeError:
                pass
        try:
            os.remove(PIDFILE)
        except OSError:
            pass
        # Clean up Win32 named event
        if sys.platform == "win32" and hasattr(self, "_win32_event"):
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self._win32_event)
        event.accept()


def main():
    parser = argparse.ArgumentParser(
        prog="voxtap",
        description="Speech-to-text with Whisper. Tap a key, get voice transcribed.",
    )
    parser.add_argument(
        "--model", default="large-v3",
        help="Whisper model name (default: large-v3)",
    )
    parser.add_argument(
        "--language", default="en",
        help="Language code (default: en)",
    )
    parser.add_argument(
        "--device", choices=["cpu", "cuda", "auto"], default=None,
        help="Force device (default: auto-detect CUDA, fall back to CPU)",
    )
    args = parser.parse_args()

    device = args.device if args.device != "auto" else None

    app = QApplication(sys.argv)
    window = SpeechToTextWindow(
        model_name=args.model, language=args.language, device=device,
    )
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
