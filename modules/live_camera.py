import time

import zxingcpp
from PySide6.QtCore import Qt, Signal
from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QMediaDevices
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class LiveCameraScanner(QWidget):
    barcode_scanned = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera = None
        self.capture_session = QMediaCaptureSession(self)
        self.video_widget = QVideoWidget()
        self.capture_session.setVideoOutput(self.video_widget)
        self.video_widget.videoSink().videoFrameChanged.connect(self._scan_frame)
        self.camera_selector = QComboBox()
        self.status = QLabel("Kamera ist ausgeschaltet.")
        self._next_scan_at = 0.0
        self._last_seen = {}

        controls = QHBoxLayout()
        controls.addWidget(self.camera_selector, 1)
        self.start_button = QPushButton("Live-Kamera starten")
        self.start_button.clicked.connect(self.start_camera)
        controls.addWidget(self.start_button)
        self.stop_button = QPushButton("Kamera stoppen")
        self.stop_button.clicked.connect(self.stop_camera)
        self.stop_button.setEnabled(False)
        controls.addWidget(self.stop_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(controls)
        self.video_widget.setMinimumHeight(190)
        self.video_widget.setMaximumHeight(300)
        layout.addWidget(self.video_widget)
        layout.addWidget(self.status)
        self.refresh_cameras()

    def refresh_cameras(self):
        selected_description = self.camera_selector.currentText()
        self.camera_selector.clear()
        devices = QMediaDevices.videoInputs()
        for device in devices:
            self.camera_selector.addItem(device.description(), device)
        selected_index = self.camera_selector.findText(selected_description)
        if selected_index >= 0:
            self.camera_selector.setCurrentIndex(selected_index)
        self.start_button.setEnabled(bool(devices))
        if not devices:
            self.status.setText("Keine Kamera gefunden.")

    def start_camera(self):
        device = self.camera_selector.currentData()
        if device is None:
            self.status.setText("Keine Kamera verfügbar.")
            return
        self.stop_camera(update_status=False)
        self.camera = QCamera(device)
        self.camera.setParent(self)
        self.camera.errorOccurred.connect(self._camera_error)
        self.capture_session.setCamera(self.camera)
        self.camera.start()
        self._last_seen.clear()
        self._next_scan_at = 0.0
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.camera_selector.setEnabled(False)
        self.status.setText(f"Live-Kamera aktiv: {device.description()}")

    def stop_camera(self, update_status=True):
        if self.camera is not None:
            self.camera.stop()
            self.camera.deleteLater()
            self.camera = None
        self.start_button.setEnabled(self.camera_selector.count() > 0)
        self.stop_button.setEnabled(False)
        self.camera_selector.setEnabled(True)
        self._last_seen.clear()
        if update_status:
            self.status.setText("Kamera ist ausgeschaltet.")

    def _camera_error(self, _error, message):
        self.status.setText(f"Kamerafehler: {message}")
        self.stop_camera(update_status=False)

    def _scan_frame(self, frame):
        if self.camera is None:
            return
        now = time.monotonic()
        if now < self._next_scan_at:
            return
        self._next_scan_at = now + 0.25
        image = frame.toImage()
        if image.isNull():
            return
        try:
            results = zxingcpp.read_barcodes(image)
        except (TypeError, ValueError):
            return
        detected = set()
        for result in results:
            code = result.text.strip()
            if not code:
                continue
            detected.add(code)
            last_seen = self._last_seen.get(code)
            if last_seen is None or now - last_seen > 1.5:
                self.barcode_scanned.emit(code)
            self._last_seen[code] = now
        self._last_seen = {
            code: seen_at for code, seen_at in self._last_seen.items()
            if now - seen_at <= 1.5 or code in detected
        }
