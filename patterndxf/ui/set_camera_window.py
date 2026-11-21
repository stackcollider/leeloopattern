"""Set Camera: the screen where the adjuster chooses the camera, the video mode and the photo size (docs/DECISIONS.md 2026-11-14).

Left: the live video of the chosen camera. Right: the cameras found (Refresh looks again), Video quality, Photo quality, Save, Cancel.
Save writes profile.json. Cancel on the very first start (no profile yet) asks "Camera is not set. Exit the program?";
opened later (a profile exists) Cancel just goes back. A change of the camera or of a mode takes the acceptance of the stand away.
"""
import logging

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget, QMainWindow, QMessageBox, QPushButton, QVBoxLayout,
                               QWidget)

from patterndxf import appinfo, profile
from patterndxf.camera.match import CameraInfo
from patterndxf.camera.source import CameraSource, PhotoMode, VideoMode, list_cameras
from patterndxf.ui import theme
from patterndxf.ui.widgets import LiveView, card

NO_CAMERA_TEXT = "No camera found: connect the camera and press Refresh"


class SetCameraWindow(QMainWindow):
    saved = Signal(object)             # the Profile that was written
    cancelled = Signal()               # closed without saving (a profile existed)
    exit_requested = Signal()          # Cancel on the first start, confirmed: the program ends

    def __init__(self, first_run, current=None):
        """first_run: there is no profile yet; current: the Profile in force (preselected), or None."""
        super().__init__()
        self.setWindowTitle(f"{appinfo.NAME} {appinfo.version()}: Set Camera")
        self.first_run, self.current = first_run, current
        self.cameras, self._source, self._ready, self._done = [], None, False, False
        self.video_modes, self.photo_modes = [], []

        self.view = LiveView("Choose a camera")
        self.list = QListWidget()
        self.refresh_btn = QPushButton("Refresh")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.video_box, self.photo_box = QComboBox(), QComboBox()
        self.video_box.setEnabled(False)
        self.photo_box.setEnabled(False)
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.save_btn.setEnabled(False)
        self.cancel_btn = QPushButton("Cancel")

        side = QVBoxLayout()
        side.addWidget(card("Camera", self.list, self.refresh_btn, self.status))
        side.addWidget(card("Video settings", QLabel("Video quality"), self.video_box))
        side.addWidget(card("Photo settings", QLabel("Photo quality"), self.photo_box))
        side.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.save_btn)
        side.addLayout(buttons)
        right = QWidget()
        right.setFixedWidth(380)
        right.setLayout(side)
        title = QLabel("Set camera")
        title.setStyleSheet(f"color: {theme.KEY}; font-size: 26px")
        hint = QLabel("Choose the camera, the video quality and the photo quality, then press Save.")
        hint.setStyleSheet(f"color: {theme.TEXT2}; font-size: 13px")
        body = QHBoxLayout()
        body.addWidget(self.view, 1)
        body.addWidget(right)
        lay = QVBoxLayout()
        lay.addWidget(title)
        lay.addWidget(hint)
        lay.addLayout(body, 1)
        box = QWidget()
        box.setLayout(lay)
        self.setCentralWidget(box)
        self.resize(1400, 820)

        self.refresh_btn.clicked.connect(self.refresh)
        self.list.currentRowChanged.connect(self.select)
        self.video_box.activated.connect(self.on_video_chosen)
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn.clicked.connect(self.cancel)
        self.refresh()

    # ----- the cameras
    def set_status(self, text, color=theme.TEXT2):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color}")

    def refresh(self):
        """Looks for the cameras again; the one of the profile (or the only one) is chosen."""
        self._stop_source()
        try:
            self.cameras = list_cameras()
        except Exception as err:                                 # never a crash: a plain line
            self.cameras = []
            self.set_status(f"Camera search failed: {err}", theme.ERR)
            logging.warning("camera search failed: %s", err)
            return
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([f"{c.name}  ({c.vid_pid or 'no VID:PID'})" for c in self.cameras])
        self.list.blockSignals(False)
        if not self.cameras:
            self.view.clear_frame("No camera")
            self.set_status(NO_CAMERA_TEXT, theme.WARN)
            return
        self.set_status(f"{len(self.cameras)} camera(s) found", theme.TEXT2)
        row = next((i for i, c in enumerate(self.cameras) if self.current and c.vid_pid == self.current.camera_vid_pid
                    and c.name == self.current.camera_name), -1)
        if row < 0 and len(self.cameras) == 1:
            row = 0
        if row >= 0:
            self.list.setCurrentRow(row)                         # starts select()

    def select(self, row):
        self._stop_source()
        if not 0 <= row < len(self.cameras):
            return
        cam = self.cameras[row]
        self.set_status(f"Opening {cam.name} ...", theme.TEXT2)
        cur = self.current
        mode = VideoMode(*cur.video) if cur and (cur.camera_name, cur.camera_vid_pid) == (cam.name, cam.vid_pid) else None
        self._source = source = CameraSource(name=cam.name, vid_pid=cam.vid_pid, video=mode)        # None: the biggest mode
        source.frame.connect(lambda s=source: self.on_frame(s))
        source.modes.connect(lambda v, p, s=source: self.on_modes(s, v, p))
        source.failed.connect(lambda m, s=source: self.on_failed(s, m))
        source.open()

    def _stop_source(self):
        """Releases the camera (only one program can hold it), the picture and the choice of the modes are cleared."""
        source, self._source, self._ready = self._source, None, False
        if source is not None:
            source.close()
        self.video_box.clear()
        self.photo_box.clear()
        self.video_box.setEnabled(False)
        self.photo_box.setEnabled(False)
        self.save_btn.setEnabled(False)

    # ----- the signals of the camera (ignored when they come from a source that was dropped)
    def on_frame(self, source):
        if source is self._source and source.latest is not None:
            self.view.show_frame(source.latest)

    def on_modes(self, source, video, photo):
        if source is not self._source:
            return
        self.video_modes, self.photo_modes = list(video), list(photo)
        for box, rows in ((self.video_box, self.video_modes), (self.photo_box, self.photo_modes)):
            box.clear()
            box.addItems([mode.label for mode in rows])
            box.setEnabled(True)
        cur, cam = self.current, self.cameras[self.list.currentRow()]
        same = cur is not None and cur.camera_vid_pid == cam.vid_pid and cur.camera_name == cam.name
        if same and VideoMode(*cur.video) in self.video_modes:                  # the profile's choice, else the biggest (the first)
            self.video_box.setCurrentIndex(self.video_modes.index(VideoMode(*cur.video)))
        if same and PhotoMode(*cur.photo) in self.photo_modes:
            self.photo_box.setCurrentIndex(self.photo_modes.index(PhotoMode(*cur.photo)))
        self._ready = True
        self.save_btn.setEnabled(True)
        self.set_status(f"{cam.name}: {len(video)} video modes, {len(photo)} photo modes", theme.OK)

    def on_failed(self, source, text):
        if source is self._source:
            self.set_status(text, theme.ERR)
            logging.warning("camera: %s", text)

    def on_video_chosen(self, index):
        if self._source is not None:
            self._source.choose_video(self.video_modes[index])

    # ----- Save and Cancel
    def save(self):
        if not self._ready:
            return
        cam = self.cameras[self.list.currentRow()]
        video = self.video_modes[self.video_box.currentIndex()]
        photo = self.photo_modes[self.photo_box.currentIndex()]
        cur = self.current
        unchanged = (cur is not None and (cur.camera_name, cur.camera_vid_pid) == (cam.name, cam.vid_pid)
                     and tuple(cur.video) == tuple(video) and tuple(cur.photo) == tuple(photo))
        new = profile.Profile(cam.name, cam.vid_pid, tuple(video), tuple(photo),
                              cur.accepted if unchanged else None)   # a change takes the acceptance away
        try:
            profile.save(new)
        except OSError as err:
            self.set_status(f"Could not save the profile: {err}", theme.ERR)
            logging.error("profile not saved: %s", err)
            return
        logging.info("profile saved: %s, video %s, photo %s", new.camera_text, video, photo)
        self._release()                                          # the camera is free for the next window before it opens
        self.saved.emit(new)
        self.close()                                             # after the next window is up: the program never sees "no windows"

    def cancel(self):
        if self.first_run:
            ask = QMessageBox.question(self, "Set Camera", "Camera is not set. Exit the program?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ask != QMessageBox.Yes:
                return False
            self._release()
            self.exit_requested.emit()
        else:
            self._release()
            self.cancelled.emit()
        self.close()
        return True

    def _release(self):
        self._done = True
        self._stop_source()                                      # the camera is released for the next window

    def closeEvent(self, event):
        if self._done:
            super().closeEvent(event)
        elif self.cancel():                                      # the cross is a Cancel
            super().closeEvent(event)
        else:
            event.ignore()
