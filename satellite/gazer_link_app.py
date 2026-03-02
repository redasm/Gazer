
import sys
import os
import json
import time
import socket
import logging
import base64
from io import BytesIO

import requests
import pyautogui
import websocket
from PIL import Image

from PySide6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QWidget, QVBoxLayout, 
    QLabel, QLineEdit, QPushButton, QCheckBox, QMessageBox,
    QStyle
)
from PySide6.QtGui import QIcon, QAction
from PySide6.QtCore import QThread, Signal, QObject, QTimer, Qt, QSettings, QSharedMemory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("GazerLink")


class Config:
    """Persistent configuration backed by QSettings."""

    def __init__(self):
        self._settings = QSettings("Gazer", "GazerLink")
        self.server_url: str = self._settings.value("server_url", "http://127.0.0.1:8080")
        self.device_name: str = self._settings.value("device_name", socket.gethostname())
        self.node_id: str = self._settings.value("node_id", self.device_name)
        self.node_token: str = self._settings.value("node_token", "")
        self.capture_interval: int = int(self._settings.value("capture_interval", 5))

    def save(self):
        self._settings.setValue("server_url", self.server_url)
        self._settings.setValue("device_name", self.device_name)
        self._settings.setValue("node_id", self.node_id)
        self._settings.setValue("node_token", self.node_token)
        self._settings.setValue("capture_interval", self.capture_interval)
        self._settings.sync()


class WorkerThread(QThread):
    """Background worker: captures screenshots and streams to the Gazer server."""

    status_signal = Signal(str)

    def __init__(self, config: Config):
        super().__init__()
        self.config = config
        self._running = False
        self._paused = False
        self._ws = None
        self._last_capture_ts = 0.0
        self._last_heartbeat_ts = 0.0

    def _send_hello(self):
        hello = {
            "type": "hello",
            "node_id": self.config.node_id,
            "token": self.config.node_token,
            "version": "1",
        }
        self._ws.send(json.dumps(hello, ensure_ascii=False))

    def _send_heartbeat_if_due(self):
        now = time.time()
        if now - self._last_heartbeat_ts < 10:
            return
        self._ws.send(json.dumps({"type": "heartbeat", "ts": now}, ensure_ascii=False))
        self._last_heartbeat_ts = now

    def _send_frame_if_due(self):
        now = time.time()
        if now - self._last_capture_ts < max(self.config.capture_interval, 1):
            return
        screenshot = pyautogui.screenshot()
        buf = BytesIO()
        screenshot.save(buf, format="JPEG", quality=60)
        payload = base64.b64encode(buf.getvalue()).decode("utf-8")
        frame = {
            "type": "frame",
            "format": "jpeg",
            "payload": payload,
            "ts": now,
        }
        self._ws.send(json.dumps(frame, ensure_ascii=False))
        self._last_capture_ts = now

    def _execute_invoke(self, action: str, args: dict) -> tuple[bool, str, dict]:
        try:
            if action == "input.mouse.click":
                x = int(args.get("x"))
                y = int(args.get("y"))
                button = str(args.get("button") or "left")
                pyautogui.click(x, y, button=button)
                return True, f"Clicked ({x}, {y}) with {button}.", {}

            if action == "input.keyboard.type":
                text = str(args.get("text") or "")
                if not text:
                    return False, "Parameter 'text' is required.", {}
                pyautogui.write(text, interval=0.02)
                return True, "Typed text.", {}

            if action == "input.keyboard.hotkey":
                keys_raw = args.get("keys") or []
                if not isinstance(keys_raw, list) or not keys_raw:
                    return False, "Parameter 'keys' must be a non-empty array.", {}
                keys = [str(item) for item in keys_raw]
                pyautogui.hotkey(*keys)
                return True, f"Pressed {'+'.join(keys)}.", {}

            if action == "screen.screenshot":
                screenshot = pyautogui.screenshot()
                buf = BytesIO()
                screenshot.save(buf, format="PNG")
                media_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                return True, "Screenshot captured.", {"media_b64": media_b64, "media_format": "png"}

            if action == "file.send":
                path = str(args.get("path") or "").strip()
                if not path:
                    return False, "Parameter 'path' is required.", {}
                if not os.path.isfile(path):
                    return False, f"File not found: {path}", {}
                with open(path, "rb") as handle:
                    payload = base64.b64encode(handle.read()).decode("utf-8")
                ext = os.path.splitext(path)[1].lstrip(".") or "bin"
                return True, "File prepared.", {"media_b64": payload, "media_format": ext}

            return False, f"Unsupported action: {action}", {}
        except Exception as exc:
            return False, f"Invoke error: {exc}", {}

    def _handle_server_message(self, message: str):
        try:
            frame = json.loads(message)
        except Exception:
            return
        frame_type = str(frame.get("type", ""))
        if frame_type != "invoke":
            return
        request_id = str(frame.get("request_id", "")).strip()
        action = str(frame.get("action", "")).strip()
        args = frame.get("args") if isinstance(frame.get("args"), dict) else {}
        ok, msg, data = self._execute_invoke(action, args)
        result = {
            "type": "invoke_result",
            "request_id": request_id,
            "ok": ok,
            "message": msg,
            "data": data,
        }
        self._ws.send(json.dumps(result, ensure_ascii=False))

    def run(self):
        self._running = True
        self.status_signal.emit("Connecting...")

        while self._running:
            try:
                ws_url = self.config.server_url.replace("http", "ws", 1) + "/ws/satellite"
                self._ws = websocket.WebSocket()
                self._ws.connect(ws_url, timeout=10)
                self._ws.settimeout(0.2)
                self._send_hello()
                self.status_signal.emit("Connected")
                self._last_capture_ts = 0.0
                self._last_heartbeat_ts = 0.0

                while self._running:
                    if self._paused:
                        time.sleep(1)
                        continue

                    try:
                        self._send_heartbeat_if_due()
                        self._send_frame_if_due()
                        try:
                            incoming = self._ws.recv()
                            if isinstance(incoming, str):
                                self._handle_server_message(incoming)
                        except websocket.WebSocketTimeoutException:
                            pass
                    except Exception as exc:
                        logger.warning(f"Capture error: {exc}")
                        time.sleep(1)

            except (websocket.WebSocketException, ConnectionError, OSError) as exc:
                self.status_signal.emit(f"Disconnected: {exc}")
                logger.warning(f"Connection lost: {exc}")
                if self._running:
                    time.sleep(5)  # retry backoff
            except Exception as exc:
                self.status_signal.emit(f"Error: {exc}")
                logger.error(f"Worker error: {exc}", exc_info=True)
                if self._running:
                    time.sleep(5)

        self._close_ws()
        self.status_signal.emit("Stopped")

    def stop(self):
        self._running = False
        self._close_ws()
        self.wait(3000)

    def set_paused(self, paused: bool):
        self._paused = paused

    def _close_ws(self):
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None


class SettingsWindow(QWidget):
    def __init__(self, config, on_save):
        super().__init__()
        self.config = config
        self.on_save = on_save
        self.setWindowTitle("Gazer Link Settings")
        self.setWindowIcon(QApplication.style().standardIcon(QStyle.SP_ComputerIcon))
        self.resize(300, 200)
        
        layout = QVBoxLayout()
        # ... (Layout remains same, omitted for brevity in instruction but I must include it if I am replacing the whole class? No, I can use context)
        # Actually, let's just rewrite the relevant methods to be safe.
        
        layout.addWidget(QLabel("Server URL:"))
        self.txt_server = QLineEdit(self.config.server_url)
        layout.addWidget(self.txt_server)
        
        layout.addWidget(QLabel("Device Name:"))
        self.txt_device = QLineEdit(self.config.device_name)
        layout.addWidget(self.txt_device)

        layout.addWidget(QLabel("Node ID:"))
        self.txt_node_id = QLineEdit(self.config.node_id)
        layout.addWidget(self.txt_node_id)

        layout.addWidget(QLabel("Node Token:"))
        self.txt_node_token = QLineEdit(self.config.node_token)
        self.txt_node_token.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.txt_node_token)
        
        btn_save = QPushButton("Save & Restart")
        btn_save.clicked.connect(self.save)
        layout.addWidget(btn_save)
        
        self.setLayout(layout)
        
    def save(self):
        self.config.server_url = self.txt_server.text()
        self.config.device_name = self.txt_device.text()
        self.config.node_id = self.txt_node_id.text().strip() or self.config.device_name
        self.config.node_token = self.txt_node_token.text().strip()
        self.on_save()
        self.hide()

    def closeEvent(self, event):
        """Override close event to minimize to tray instead of quitting."""
        event.ignore()
        self.hide()

class GazerLinkApp(QObject):
    def __init__(self):
        super().__init__()
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        
        # Single Instance Check
        self.shared_memory = QSharedMemory("GazerLinkInstanceLock")
        if not self.shared_memory.create(1):
             # Already running
             error_box = QMessageBox()
             error_box.setIcon(QMessageBox.Warning)
             error_box.setWindowTitle("Gazer Link")
             error_box.setText("Gazer Link is already running in the system tray.")
             error_box.exec()
             sys.exit(0)

        self.config = Config()
        
        # Worker
        self.worker = WorkerThread(self.config)
        self.worker.status_signal.connect(self.update_status)
        
        # Tray Icon
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.app.style().standardIcon(QStyle.SP_ComputerIcon))
        self.tray_icon.activated.connect(self.on_tray_activated)
        
        # Menu
        self.menu = QMenu()
        
        self.status_action = QAction("Status: Disconnected", self)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()
        
        self.action_pause = QAction("Pause Watching", self)
        self.action_pause.setCheckable(True)
        self.action_pause.triggered.connect(self.toggle_pause)
        self.menu.addAction(self.action_pause)
        
        self.action_settings = QAction("Settings", self)
        self.action_settings.triggered.connect(self.show_settings)
        self.menu.addAction(self.action_settings)
        
        self.menu.addSeparator()
        
        self.action_quit = QAction("Quit", self)
        self.action_quit.triggered.connect(self.quit_app)
        self.menu.addAction(self.action_quit)
        
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.show()
        
        # Settings Window
        self.settings_window = SettingsWindow(self.config, self.restart_worker)
        
        # Start
        self.worker.start()
        
        # Note: Do NOT show settings window on startup, start silently in tray.
        self.tray_icon.showMessage("Gazer Link", "Running in background...", QSystemTrayIcon.Information, 2000)
        
    def show_settings(self):
        self.settings_window.show()
        self.settings_window.activateWindow()
        
    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.show_settings()
        
    def restart_worker(self):
        self.worker.stop()
        self.worker.start()
        
    def toggle_pause(self, checked):
        self.worker.set_paused(checked)
        self.tray_icon.showMessage(
            "Gazer Link", 
            "Monitoring Paused" if checked else "Monitoring Resumed",
            QSystemTrayIcon.Information, 
            2000
        )
        
    def update_status(self, status):
        self.status_action.setText(f"Status: {status[:20]}")
        
        # Change Icon color/style based on status (Mock logic)
        if "Connected" in status:
            self.tray_icon.setToolTip(f"Gazer Link: Connected to {self.config.server_url}")
        else:
            self.tray_icon.setToolTip(f"Gazer Link: {status}")

    def quit_app(self):
        self.worker.stop()
        self.app.quit()
        
    def run(self):
        sys.exit(self.app.exec())

if __name__ == "__main__":
    app = GazerLinkApp()
    app.run()
