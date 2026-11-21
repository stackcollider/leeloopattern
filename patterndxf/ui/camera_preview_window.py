"""Camera preview: the window after the splash (docs/DECISIONS.md 2026-11-14).

The live video of the camera of the profile and the facts about it: the camera, the state, the video settings of the
profile (only shown: the operator does not change them, Set Camera does), one status line, Set camera, App settings, Commissioning and Go to measurement.
Go to measurement opens only for an accepted stand with a calibration (calibration.json next to the profile).
"""
import logging
import platform
import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from patterndxf import appinfo
from patterndxf.camera.source import CameraSource, PhotoMode, VideoMode
from patterndxf.ui import theme
from patterndxf.ui.widgets import LiveView, card

INFO_EVERY_MS = 500


class CameraPreviewWindow(QMainWindow):
    open_set_camera = Signal()
    open_set_settings = Signal()
    open_commissioning = Signal()
    go_measure = Signal()

    def __init__(self, profile, problem=""):
        """profile: the Profile in force; problem: why the camera cannot be opened ('' = try to open it)."""
        super().__init__()
        self.setWindowTitle(f"{appinfo.NAME} {appinfo.version()}: Camera preview")
        self.profile, self.problem = profile, problem
        self.cam = None
        self.state = "-"
        self._n0, self._t0, self.fps = 0, time.perf_counter(), 0.0

        self.view = LiveView("Camera preview")
        self.info = QLabel("")
        self.info.setStyleSheet(f"font-family: {theme.MONO}; font-size: 13px")
        self.info.setWordWrap(True)
        self.video_label = QLabel(VideoMode(*profile.video).label)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.go_btn = QPushButton("Go to measurement")
        self.go_btn.setObjectName("primary")
        self.camera_btn = QPushButton("Set camera")
        self.settings_btn = QPushButton("App settings")
        self.comm_btn = QPushButton("Commissioning")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(110)

        side = QVBoxLayout()
        side.addWidget(card("Camera", self.info))
        side.addWidget(card("Video settings", self.video_label))
        side.addStretch(1)
        side.addWidget(self.camera_btn)
        side.addWidget(self.settings_btn)
        side.addWidget(self.comm_btn)
        side.addWidget(self.go_btn)
        right = QWidget()
        right.setFixedWidth(380)
        right.setLayout(side)
        top = QHBoxLayout()
        top.addWidget(self.view, 1)
        top.addWidget(right)
        title = QLabel("Camera preview")
        title.setStyleSheet(f"color: {theme.KEY}; font-size: 26px")
        lay = QVBoxLayout()
        lay.addWidget(title)
        lay.addWidget(self.status)                              # the state under the title: what is wrong or what to do
        lay.addLayout(top, 1)
        lay.addWidget(self.log)
        box = QWidget()
        box.setLayout(lay)
        self.setCentralWidget(box)
        self.resize(1500, 900)

        self.camera_btn.clicked.connect(self.open_set_camera)
        self.settings_btn.clicked.connect(self.open_set_settings)
        self.comm_btn.clicked.connect(self.open_commissioning)
        self.go_btn.clicked.connect(self.go_measure)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_info)
        self.timer.start(INFO_EVERY_MS)
        self.say(f"{appinfo.NAME} {appinfo.version()} on {platform.node()}, camera {profile.camera_text}")   # head: computer, version, camera
        self.update_status()
        self.update_info()

    # ----- the camera (released for Set Camera and reopened afterwards)
    def start_camera(self):
        if self.cam is not None or self.problem:
            return
        p = self.profile
        self.state = "opening ..."
        self.cam = cam = CameraSource(name=p.camera_name, vid_pid=p.camera_vid_pid, video=VideoMode(*p.video))
        cam.frame.connect(lambda c=cam: self.on_frame(c))
        cam.opened.connect(lambda name, c=cam: self.on_opened(c, name))
        cam.modes.connect(lambda v, ph, c=cam: self.on_modes(c, v, ph))
        cam.failed.connect(lambda m, c=cam: self.on_failed(c, m))
        cam.open()

    def stop_camera(self):
        cam, self.cam = self.cam, None
        if cam is not None:
            cam.close()
        self.state = "released"
        self.view.clear_frame("Camera released")

    def on_frame(self, cam):
        if cam is self.cam and cam.latest is not None:
            self.view.show_frame(cam.latest)

    def on_opened(self, cam, name):
        if cam is self.cam:
            self.state = "open"
            self.say(f"Camera open: {name} ({cam.camera.vid_pid})")

    def on_modes(self, cam, video, photo):
        if cam is not self.cam:
            return
        if VideoMode(*self.profile.video) not in video:
            self.problem = f"Video mode of the profile is not available: {VideoMode(*self.profile.video).label}"
            self.update_status()
        if PhotoMode(*self.profile.photo) not in photo:
            self.problem = f"Photo size of the profile is not available: {PhotoMode(*self.profile.photo).label}"
            self.update_status()

    def on_failed(self, cam, text):
        if cam is self.cam:
            self.state = "failed"
            self.problem = text
            self.say(f"ERROR {text}")
            self.update_status()

    # ----- what is shown
    def update_status(self):
        """One line, the most important reason first: a problem with the camera, else the acceptance."""
        if self.problem:
            text, color = self.problem, theme.ERR
        elif self.profile.accepted is None:
            text, color = "Not accepted: run commissioning", theme.WARN
        else:
            text, color = "Accepted", theme.OK
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}; font-size: 14px")
        from patterndxf.measure import calibration_path
        ready = not self.problem and self.profile.accepted is not None and calibration_path().exists()
        self.go_btn.setEnabled(ready)                           # an accepted stand with a calibration
        self.go_btn.setToolTip("" if ready else "Available for an accepted stand with a calibration (Commissioning)")
        if self.problem and self.cam is None:
            self.view.clear_frame(self.problem)

    def update_info(self):
        now = time.perf_counter()
        if self.cam is not None:
            self.fps = (self.cam.frames - self._n0) / (now - self._t0)
            self._n0, self._t0 = self.cam.frames, now
        size = self.cam.size if self.cam is not None else None
        p = self.profile
        self.info.setText("\n".join((
            f"Computer: {platform.node()}",
            f"Camera:   {p.camera_name}",
            f"VID:PID:  {p.camera_vid_pid or '-'}",
            f"State:    {'not found' if self.problem and self.cam is None else self.state}",
            f"Live:     {f'{size[0]}x{size[1]} at {self.fps:.1f} fps' if size else '-'}")))

    def say(self, text):
        import datetime
        self.log.appendPlainText(f"{datetime.datetime.now():%H:%M:%S}  {text}")
        logging.info(text)

    def closeEvent(self, event):
        self.timer.stop()
        self.stop_camera()
        super().closeEvent(event)
