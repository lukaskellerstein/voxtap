"""Main voxtap GUI — speech-to-text with Whisper and Qt."""

import argparse
import datetime
import inspect
import math
import os
import queue
import signal
import subprocess
import sys
import threading

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
ACCENT_YELLOW = "#ffd43b"
BORDER_COLOR = "#45475a"

SAMPLE_RATE = 16000
CHUNK_INTERVAL = 1.5

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


# --- Animated border frame ---

class GlowFrame(QFrame):
    """Frame with animated border glow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._glow = 0.0
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
        r = int(0x45 + (0xff - 0x45) * p)
        g = int(0x47 + (0x6b - 0x47) * p)
        b = int(0x5a + (0x6b - 0x5a) * p)
        self.setStyleSheet(
            f"GlowFrame {{ border: 2px solid #{r:02x}{g:02x}{b:02x}; "
            f"border-radius: 4px; background-color: transparent; }}"
        )

    def start(self):
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
    def __init__(self, model_name="distil-large-v3", language="en", device=None):
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

        self._bridge = TranscriptionBridge()
        self._bridge.text_ready.connect(self._insert_transcription)
        self._bridge.error.connect(
            lambda msg: self.status_label.setText(f"Error: {msg}")
        )

        self.setWindowTitle("voxtap")
        self.setMinimumSize(400, 300)
        self.resize(750, 520)
        self.setWindowFlags(
            self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setStyleSheet(STYLESHEET)

        self._setup_gui()

        # SIGUSR1 toggle (POSIX)
        if sys.platform != "win32":
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

        self.status_label = QLabel("Loading model...")
        self.status_label.setObjectName("status")
        tb_layout.addWidget(self.status_label)

        layout.addWidget(title_bar)

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

        copy_btn = QPushButton("Copy as Markdown")
        copy_btn.setStyleSheet(_btn_style(ACCENT_BLUE))
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
        if event.key() == Qt.Key.Key_Escape:
            self.close()
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
                return "cuda", "float16"
            return self.device_override, "int8"

        try:
            import torch
            if torch.cuda.is_available():
                return "cuda", "float16"
        except ImportError:
            pass

        return "cpu", "int8"

    def _on_model_loaded(self):
        self.record_btn.setEnabled(True)
        self.start_recording()

    # --- Recording ---

    def _toggle_recording(self):
        if self.recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self):
        if self.recording or self.model is None:
            return
        self.recording = True
        self.audio_buffer = []

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
        self.record_btn.setText("Pause")
        self.record_btn.setStyleSheet(_btn_style(ACCENT_YELLOW))
        self.waveform.start()
        self.glow_frame.start()

        self.stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1,
            dtype="float32", callback=self._audio_callback,
        )
        self.stream.start()

        self.transcription_thread = threading.Thread(
            target=self._transcription_loop, daemon=True,
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
                    audio = np.concatenate(
                        self.audio_buffer, axis=0,
                    ).flatten()
                    self.audio_buffer = []
                    self._run_transcription(audio)

            threading.Event().wait(0.5)

        # Final transcription
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
            self.audio_buffer = []
            self._run_transcription(audio)

    def _run_transcription(self, audio):
        try:
            segments, _ = self.model.transcribe(
                audio, language=self.language,
                vad_filter=True, beam_size=5,
            )
            text_parts = [segment.text.strip() for segment in segments]
            if text_parts:
                self._bridge.text_ready.emit(" ".join(text_parts))
        except Exception as e:
            self._bridge.error.emit(str(e))

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
        self.status_label.setText("Paused — edit text freely")
        self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")
        self.record_btn.setText("Record")
        self.record_btn.setStyleSheet(_btn_style(ACCENT_RED))

        # Resume Spotify if it was playing before
        if self._media_was_playing:
            _media_play()
            self._media_was_playing = False

    def _handle_sigusr1(self, signum, frame):
        QTimer.singleShot(0, self._toggle_recording)

    def _restore_status(self):
        if self.recording:
            self.status_label.setText("Recording")
            self.status_label.setStyleSheet(f"color: {ACCENT_RED};")
        else:
            self.status_label.setText("Paused — edit text freely")
            self.status_label.setStyleSheet(f"color: {ACCENT_GREEN};")

    def closeEvent(self, event):
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
        event.accept()


def main():
    parser = argparse.ArgumentParser(
        prog="voxtap",
        description="Speech-to-text with Whisper. Tap a key, get voice transcribed.",
    )
    parser.add_argument(
        "--model", default="distil-large-v3",
        help="Whisper model name (default: distil-large-v3)",
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
