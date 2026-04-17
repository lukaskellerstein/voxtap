"""TCP control server for MCP-driven UI testing of voxtap.

Embedded in the Qt app (gated behind the VOXTAP_CONTROL_PORT env var). Exposes
a newline-delimited JSON protocol on localhost for an external MCP server to
inspect and interact with the UI.

Adapted from FinanceApp's control_server.py, with three voxtap-specific
commands added: trigger_toggle, set_transcript, get_recording_state.
"""

import base64
import json
import socketserver
import threading
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import QBuffer, QIODevice, QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QWidget,
)


class CommandHandler(QObject):
    """Dispatches control-server commands onto the Qt main thread."""

    command_received = pyqtSignal(dict, object)

    def __init__(self, main_window: QWidget):
        super().__init__()
        self.app = main_window
        self.command_received.connect(self._handle_command)

    def _handle_command(self, command: dict, response_callback):
        try:
            result = self._execute_command(command)
            response_callback({"status": "success", "result": result})
        except Exception as e:
            response_callback({"status": "error", "message": str(e)})

    def _execute_command(self, command: dict) -> Any:
        cmd_type = command.get("type")

        if cmd_type == "get_snapshot":
            return self._get_snapshot()
        elif cmd_type == "click":
            return self._click(command["object_name"])
        elif cmd_type == "fill":
            return self._fill(command["object_name"], command["text"])
        elif cmd_type == "clear":
            return self._clear(command["object_name"])
        elif cmd_type == "get_text":
            return self._get_text(command["object_name"])
        elif cmd_type == "trigger_action":
            return self._trigger_action(command["object_name"])
        elif cmd_type == "get_window_info":
            return self._get_window_info()
        elif cmd_type == "take_screenshot":
            return self._take_screenshot(
                command.get("window_name"),
                command.get("widget_name"),
            )
        elif cmd_type == "trigger_toggle":
            return self._trigger_toggle()
        elif cmd_type == "set_transcript":
            return self._set_transcript(command["text"])
        elif cmd_type == "get_recording_state":
            return self._get_recording_state()
        elif cmd_type == "close":
            QTimer.singleShot(100, self.app.close)
            return True
        else:
            raise ValueError(f"Unknown command type: {cmd_type}")

    # --- Snapshot ---------------------------------------------------------

    def _get_snapshot(self) -> Dict:
        widgets: List[Dict] = []
        windows: List[Dict] = []

        def collect(widget: QWidget, parent_path: str = ""):
            name = widget.objectName()
            path = f"{parent_path}/{name}" if parent_path else name

            if name and not name.startswith("qt_"):
                info: Dict[str, Any] = {
                    "object_name": name,
                    "path": path,
                    "type": widget.__class__.__name__,
                    "visible": widget.isVisible(),
                    "enabled": widget.isEnabled(),
                }
                if isinstance(widget, QLineEdit):
                    info["text"] = widget.text()
                    info["placeholder"] = widget.placeholderText()
                    info["readonly"] = widget.isReadOnly()
                elif isinstance(widget, QPushButton):
                    info["text"] = widget.text()
                    info["checkable"] = widget.isCheckable()
                    if widget.isCheckable():
                        info["checked"] = widget.isChecked()
                elif isinstance(widget, QLabel):
                    info["text"] = widget.text()
                elif isinstance(widget, (QCheckBox, QRadioButton)):
                    info["checked"] = widget.isChecked()
                    info["text"] = widget.text()
                elif isinstance(widget, QComboBox):
                    info["current_text"] = widget.currentText()
                    info["current_index"] = widget.currentIndex()
                    info["count"] = widget.count()
                elif isinstance(widget, QTextEdit):
                    info["text"] = widget.toPlainText()
                elif isinstance(widget, QPlainTextEdit):
                    info["text"] = widget.toPlainText()
                elif isinstance(widget, QProgressBar):
                    info["value"] = widget.value()
                    info["minimum"] = widget.minimum()
                    info["maximum"] = widget.maximum()

                widgets.append(info)

            for child in widget.children():
                if isinstance(child, QWidget):
                    collect(child, path)

        collect(self.app)
        windows.append({
            "title": self.app.windowTitle(),
            "size": {"width": self.app.width(), "height": self.app.height()},
            "is_main": True,
        })

        qapp = QApplication.instance()
        if qapp:
            for window in qapp.topLevelWidgets():
                if window is not self.app and window.isVisible():
                    if isinstance(window, (QMainWindow, QDialog)):
                        collect(window)
                        windows.append({
                            "title": window.windowTitle(),
                            "size": {"width": window.width(), "height": window.height()},
                            "is_main": False,
                        })

        return {
            "window_title": self.app.windowTitle(),
            "window_size": {"width": self.app.width(), "height": self.app.height()},
            "windows": windows,
            "widgets": widgets,
            "menu_actions": self._collect_actions(),
        }

    def _collect_actions(self) -> List[Dict]:
        actions: List[Dict] = []

        def walk(window: QWidget):
            # Menu bar actions
            menu_bar = window.findChild(QMenuBar)
            if menu_bar:
                for menu_action in menu_bar.actions():
                    menu = menu_action.menu()
                    if menu:
                        for action in menu.actions():
                            if action.objectName() and not action.isSeparator():
                                actions.append({
                                    "object_name": action.objectName(),
                                    "text": action.text().replace("&", ""),
                                    "enabled": action.isEnabled(),
                                    "checked": action.isChecked() if action.isCheckable() else None,
                                    "source": f"menu:{menu.title()}",
                                    "window": window.windowTitle(),
                                })
            # Toolbar actions (voxtap uses a QToolBar, no menu bar)
            for action in window.findChildren(QAction):
                if action.objectName() and not action.isSeparator():
                    if any(a["object_name"] == action.objectName() for a in actions):
                        continue
                    actions.append({
                        "object_name": action.objectName(),
                        "text": action.text().replace("&", ""),
                        "enabled": action.isEnabled(),
                        "checked": action.isChecked() if action.isCheckable() else None,
                        "source": "toolbar",
                        "window": window.windowTitle(),
                    })

        walk(self.app)
        qapp = QApplication.instance()
        if qapp:
            for window in qapp.topLevelWidgets():
                if window is not self.app and window.isVisible():
                    if isinstance(window, (QMainWindow, QDialog)):
                        walk(window)
        return actions

    # --- Widget lookup ----------------------------------------------------

    def _find_widget(self, name: str) -> Optional[QWidget]:
        widget = self.app.findChild(QWidget, name)
        if widget:
            return widget
        qapp = QApplication.instance()
        if qapp:
            for window in qapp.topLevelWidgets():
                if window is not self.app:
                    widget = window.findChild(QWidget, name)
                    if widget:
                        return widget
        return None

    def _find_action(self, name: str) -> Optional[QAction]:
        action = self.app.findChild(QAction, name)
        if action:
            return action
        qapp = QApplication.instance()
        if qapp:
            for window in qapp.topLevelWidgets():
                if window is not self.app:
                    action = window.findChild(QAction, name)
                    if action:
                        return action
        return None

    # --- Interactions -----------------------------------------------------

    def _click(self, name: str) -> bool:
        widget = self._find_widget(name)
        if not widget:
            raise ValueError(f"Widget not found: {name}")
        if isinstance(widget, (QPushButton, QCheckBox, QRadioButton)):
            widget.click()
            return True
        raise ValueError(f"Widget {name} is not clickable")

    def _fill(self, name: str, text: str) -> bool:
        widget = self._find_widget(name)
        if not widget:
            raise ValueError(f"Widget not found: {name}")
        if isinstance(widget, QLineEdit):
            widget.setText(text)
            return True
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            widget.setPlainText(text)
            return True
        raise ValueError(f"Widget {name} is not a text input")

    def _clear(self, name: str) -> bool:
        widget = self._find_widget(name)
        if not widget:
            raise ValueError(f"Widget not found: {name}")
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
            widget.clear()
            return True
        raise ValueError(f"Widget {name} is not a text input")

    def _get_text(self, name: str) -> str:
        widget = self._find_widget(name)
        if not widget:
            raise ValueError(f"Widget not found: {name}")
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, (QLabel, QPushButton, QCheckBox, QRadioButton)):
            return widget.text()
        if isinstance(widget, (QTextEdit, QPlainTextEdit)):
            return widget.toPlainText()
        if isinstance(widget, QComboBox):
            return widget.currentText()
        raise ValueError(f"Widget {name} does not have text")

    def _trigger_action(self, name: str) -> bool:
        action = self._find_action(name)
        if not action:
            raise ValueError(f"Action not found: {name}")
        if not action.isEnabled():
            raise ValueError(f"Action {name} is not enabled")
        action.trigger()
        return True

    def _get_window_info(self) -> Dict:
        return {
            "title": self.app.windowTitle(),
            "width": self.app.width(),
            "height": self.app.height(),
            "x": self.app.x(),
            "y": self.app.y(),
            "minimized": self.app.isMinimized(),
            "maximized": self.app.isMaximized(),
            "visible": self.app.isVisible(),
        }

    def _take_screenshot(
        self,
        window_name: Optional[str] = None,
        widget_name: Optional[str] = None,
    ) -> Dict:
        target: Optional[QWidget] = None
        target_name: str

        if widget_name:
            target = self._find_widget(widget_name)
            if not target:
                raise ValueError(f"Widget not found: {widget_name}")
            target_name = widget_name
        elif window_name:
            qapp = QApplication.instance()
            if qapp:
                for window in qapp.topLevelWidgets():
                    if window.windowTitle() == window_name and window.isVisible():
                        target = window
                        target_name = window_name
                        break
            if not target:
                raise ValueError(f"Window not found: {window_name}")
        else:
            target = self.app
            target_name = self.app.windowTitle()

        pixmap = target.grab()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        buffer.close()
        image_b64 = base64.b64encode(buffer.data().data()).decode("utf-8")

        return {
            "image_base64": image_b64,
            "width": pixmap.width(),
            "height": pixmap.height(),
            "target": target_name,
            "format": "png",
        }

    # --- Voxtap-specific --------------------------------------------------

    def _trigger_toggle(self) -> bool:
        """Fire the same path the global hotkey / Record button would."""
        toggle = getattr(self.app, "_toggle_recording", None)
        if not callable(toggle):
            raise RuntimeError("Main window has no _toggle_recording slot")
        QTimer.singleShot(0, toggle)
        return True

    def _set_transcript(self, text: str) -> bool:
        """Write text directly into the transcript editor, bypassing Whisper."""
        editor = getattr(self.app, "editor", None)
        if editor is None:
            raise RuntimeError("Main window has no editor attribute")
        editor.setPlainText(text)
        return True

    def _get_recording_state(self) -> Dict:
        """Return the app's current state in machine-readable form."""
        app = self.app
        if getattr(app, "model", True) is None:
            state = "loading_model"
        elif getattr(app, "recording", False):
            state = "recording"
        elif getattr(app, "_is_polishing", False):
            state = "polishing"
        elif getattr(app, "_transcribing", False):
            state = "transcribing"
        elif getattr(app, "_playback_stream", None) is not None:
            state = "playing"
        else:
            state = "idle"

        recorded = getattr(app, "_recorded_audio", None)
        has_recording = recorded is not None and len(recorded) > 0
        return {
            "state": state,
            "has_recording": has_recording,
            "transcript": app.editor.toPlainText() if hasattr(app, "editor") else "",
        }


class _Handler(socketserver.StreamRequestHandler):
    """Per-connection newline-delimited JSON request handler."""

    def handle(self):
        while True:
            try:
                line = self.rfile.readline()
                if not line:
                    break

                command = json.loads(line.decode("utf-8"))
                response: Dict[str, Any] = {}
                event = threading.Event()

                def callback(result):
                    response.update(result)
                    event.set()

                self.server.command_handler.command_received.emit(command, callback)
                event.wait(timeout=30)

                self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))
                self.wfile.flush()
            except json.JSONDecodeError as e:
                err = json.dumps({"status": "error", "message": f"Invalid JSON: {e}"}) + "\n"
                self.wfile.write(err.encode("utf-8"))
                self.wfile.flush()
            except Exception as e:
                err = json.dumps({"status": "error", "message": str(e)}) + "\n"
                self.wfile.write(err.encode("utf-8"))
                self.wfile.flush()
                break


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_control_server(
    command_handler: CommandHandler,
    host: str = "127.0.0.1",
    port: int = 29998,
) -> _ThreadedTCPServer:
    """Start the control server on a background thread. Returns the server instance."""
    server = _ThreadedTCPServer((host, port), _Handler)
    server.command_handler = command_handler  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
