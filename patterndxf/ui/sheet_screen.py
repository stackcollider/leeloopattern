"""The common screen of the Commissioning steps that work on the work sheet (decision 2026-11-19, variant A): Focus and Calibration.

What every such screen has: the live picture of the camera of the profile in the video mode of the profile; the search for the
markers in a thread on a reduced copy; the markers, the blue cross of the sheet and the red cross of the frame over the picture;
the plate of the sheet and the line of where it lies (centre, turn, what is still needed); Resolution (this screen only);
the Camera and Markers blocks; a status line; Snap, Fit; Cancel, Finish.
A screen built on it adds its own buttons (into `self.actions`), its own drawing (draw_extra), its own condition for Finish
(can_finish) and its finish(). A modal dialog over the Commissioning hub; the camera is opened here and released at the end.
"""
import datetime
import logging
import threading
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from patterndxf import appinfo
from patterndxf.camera.sample import make_source
from patterndxf.camera.source import VideoMode
from patterndxf.sheet import focus, vertical
from patterndxf.ui import theme
from patterndxf.ui.splash import center_on
from patterndxf.ui.vertical_window import draw_lines
from patterndxf.ui.widgets import ZoomPicture, card

DETECT_EVERY_S = 0.3                            # the markers are searched at most this often (in a thread: the window never waits for it)
SWITCH_WAIT_S, SWITCH_TRIES = 2.5, 3            # a requested mode is asked again after this many seconds without frames of that size
STALL_S, REOPEN_S = 4.0, 3.0                    # no frame this long: the camera is lost; then it is opened again this often
NO_CAMERA = "No picture from the camera: check the cable"
BLANK_BGR, SHEET_BGR = (80, 220, 100), (0, 200, 255)       # outlines: the Vertical sheet green, the markers of the work sheets yellow
BLANK_ID_BGR = (0, 110, 0)                      # the IDs of the Vertical sheet markers: dark green
AXES_BGR, AXES_PX = (255, 191, 0), 3            # the cross of the sheet: sky blue, 3 px (chosen 2026-11-19)
PLATE_COLOURS = {focus.NEUTRAL: theme.TEXT2, focus.BAD: theme.ERR, focus.GOOD: theme.OK}
GREY = "#6b6b82"


class MarkerThread(QThread):
    """Searches the markers away from the window thread: it takes the newest picture it was given (the older ones are dropped),
    so a slow search (0.1-0.5 s on a 16 MP picture) never makes the window wait."""
    found = Signal(object, float, object)           # the markers {id: corners}, the time of the search (ms), the size (w, h) of the picture

    def __init__(self, detector):
        super().__init__()
        self.detector = detector
        self._pending, self._lock, self._wake, self._stop = None, threading.Lock(), threading.Event(), False
        self.busy = False

    def submit(self, bgr):
        with self._lock:
            self._pending = bgr
        self._wake.set()

    def stop(self):
        self._stop = True
        self._wake.set()
        self.wait(5000)

    def run(self):
        while True:
            self._wake.wait()
            self._wake.clear()
            if self._stop:
                return
            with self._lock:
                bgr, self._pending = self._pending, None
            if bgr is None:
                continue
            self.busy = True
            t0 = time.perf_counter()
            try:
                found = focus.search_scaled(self.detector, bgr)
            except Exception:                       # a plain trace, never a silent death of the thread
                traceback.print_exc()
                found = {}
            self.busy = False
            self.found.emit(found, (time.perf_counter() - t0) * 1000, (bgr.shape[1], bgr.shape[0]))


def hex_bgr(colour):
    return (int(colour[5:7], 16), int(colour[3:5], 16), int(colour[1:3], 16))


class SheetScreen(QDialog):
    TITLE = "Sheet"                                  # the name of the step (window title, heading, log, snapshot files)
    HINT = ""                                        # one line under the heading

    def __init__(self, profile, snap_folder=None, parent=None):
        """profile: the Profile in force (its camera is opened); snap_folder: where Snap saves the picture."""
        super().__init__(parent)
        self.setWindowTitle(f"{appinfo.NAME} {appinfo.version()}: {self.TITLE}")
        self.setModal(True)
        self.profile = profile
        self.snap_folder = Path(snap_folder) if snap_folder else None
        self.result = None                               # after Finish: what goes to the hub
        self.markers = MarkerThread(vertical.make_detector())
        self.markers.found.connect(self.on_markers)
        self.markers.start()
        self._submitted = 0.0                            # when the last picture was given to the search
        self.search_paused = False                       # a screen pauses the search while it works hard (the GIL)
        self.found, self.search_ms, self.shown_frames = {}, 0.0, 0
        self.frame_size, self.last_bgr, self.video_modes = None, None, []
        self.wanted, self.wanted_at, self.wanted_tries = None, 0.0, 0
        self.pose = None                                 # the work sheet in view (focus.sheet_pose), for the cross of the sheet
        self.place_ok = False                            # the sheet whole, centred and straight
        self._n0, self._t0, self.fps = 0, time.perf_counter(), 0.0
        self.cam = None
        self.last_frame_at = None                        # when the last frame came (the watchdog of the camera)
        self.cam_lost, self.lost_at = False, 0.0         # no frames for STALL_S: the camera is opened again every REOPEN_S
        self.watchdog = QTimer(self)
        self.watchdog.timeout.connect(self.check_camera)

        self.picture = ZoomPicture("Opening the camera ...")
        self.plate = QLabel("")
        self.plate.setAlignment(Qt.AlignCenter)
        self.plate.setWordWrap(True)
        self.plate.setMinimumHeight(60)
        self.place_line = QLabel("")                     # where the sheet centre lies from the cross, the turn, what is still needed
        self.place_line.setWordWrap(True)
        self.mode_box = QComboBox()                      # the video mode of this screen only; the profile is not changed
        self.mode_box.setEnabled(False)
        self.settings_line = QLabel(f"Settings: {profile.video[0]}x{profile.video[1]} at {profile.video[2]} fps\n"
                                    "The choice here is for this screen only.")
        self.settings_line.setStyleSheet(f"color: {theme.TEXT2}; font-size: 13px")
        self.cam_info, self.info = QLabel(""), QLabel("")          # the blocks Camera (frame, fps) and Markers
        for label in (self.cam_info, self.info):
            label.setStyleSheet(f"font-family: {theme.MONO}; font-size: 13px")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.snap_btn, self.fit_btn = QPushButton("Snap"), QPushButton("Fit")
        self.cancel_btn, self.finish_btn = QPushButton("Cancel"), QPushButton("Finish")
        self.finish_btn.setObjectName("primary")
        self.finish_btn.setEnabled(False)
        for b in (self.snap_btn, self.fit_btn, self.cancel_btn, self.finish_btn):
            b.setMinimumHeight(44)

        self.info_area = QVBoxLayout()                   # a screen adds its own blocks here (under the place line)
        self.actions = QVBoxLayout()                     # a screen adds its own buttons here (under Snap and Fit)
        side = QVBoxLayout()
        self.res_card = card("Resolution", self.mode_box, self.settings_line)
        self.cam_card = card("Camera", self.cam_info)
        self.markers_card = card("Markers", self.info)
        side.addWidget(self.res_card)
        side.addWidget(self.plate)
        side.addWidget(self.place_line)
        side.addLayout(self.info_area)
        side.addWidget(self.cam_card)
        side.addWidget(self.markers_card)
        side.addStretch(1)
        side.addWidget(self.status)
        row = QHBoxLayout()
        row.addWidget(self.snap_btn)
        row.addWidget(self.fit_btn)
        side.addLayout(row)
        side.addLayout(self.actions)
        end = QHBoxLayout()
        end.addWidget(self.cancel_btn)
        end.addWidget(self.finish_btn)
        side.addLayout(end)
        right = QWidget()
        right.setFixedWidth(380)
        right.setLayout(side)
        title = QLabel(self.TITLE)
        title.setStyleSheet(f"color: {theme.KEY}; font-size: 26px")
        hint = QLabel(self.HINT)
        hint.setStyleSheet(f"color: {theme.TEXT2}; font-size: 15px")
        body = QHBoxLayout()
        body.addWidget(self.picture, 1)
        body.addWidget(right)
        self.header = QHBoxLayout()                      # the title; a screen adds its own icons on the right
        self.header.addWidget(title)
        self.header.addStretch(1)
        lay = QVBoxLayout()
        lay.addLayout(self.header)
        lay.addWidget(hint)
        lay.addLayout(body, 1)
        self.setLayout(lay)
        self.resize(1500, 900)

        self.snap_btn.clicked.connect(self.snap)
        self.fit_btn.clicked.connect(self.picture.fit)
        self.mode_box.activated.connect(self.on_mode_chosen)
        self.cancel_btn.clicked.connect(self.reject)
        self.finish_btn.clicked.connect(self.on_finish_clicked)

    def begin(self):
        """Called by the screen at the end of its __init__ (its own widgets exist by then): the first texts, the camera."""
        self.refresh_plate()
        self.refresh_place()
        self.refresh_info()
        self.start_camera()
        self.watchdog.start(1000)

    # ----- what a screen gives (the defaults do nothing)
    def can_finish(self):
        """The screen's own condition for Finish, on top of the place of the sheet."""
        return True

    def draw_extra(self, out, to_view):
        """The screen's own drawing, over everything else."""

    def present(self, bgr):
        """Shows the new picture: the camera picture with the markers and the crosses (a screen may show another view)."""
        self.picture.set_frame(bgr, self.draw_overlay)

    def on_new_frame(self, bgr):
        """Every new picture (after it is shown)."""

    def on_mode_changed(self):
        """Another resolution: what belongs to the old picture is to be dropped."""

    def finish(self):
        """The end of the step: set self.result, log, accept()."""
        self.accept()

    def on_camera_lost(self):
        """The camera gives no frames (the cable is out): a screen stops what needs frames."""

    def on_camera_back(self):
        """The frames come again after the camera was lost."""

    # ----- the camera (the video mode of the profile)
    def start_camera(self):
        p = self.profile
        self.cam = cam = make_source(name=p.camera_name, vid_pid=p.camera_vid_pid, video=VideoMode(*p.video))
        cam.frame.connect(lambda c=cam: self.on_frame(c))
        cam.opened.connect(lambda name, c=cam: self.on_opened(c, name))
        cam.modes.connect(lambda video, photo, c=cam: self.on_modes(c, video))
        cam.failed.connect(lambda text, c=cam: self.on_failed(c, text))
        cam.open()

    # ----- the watchdog: a camera that gives no frames is lost and opened again (the cable out and in)
    def check_camera(self):
        now = time.perf_counter()
        if self.cam_lost:
            if now - self.lost_at >= REOPEN_S:
                self.lost_at = now
                self.reopen_camera()
            return
        if self.last_frame_at is None or self.cam is None or getattr(self.cam, "busy", False):
            return                                       # not open yet, or a photo is being taken
        if now - self.last_frame_at > STALL_S:
            self.cam_lost, self.lost_at = True, now
            self.fps, self.last_bgr = 0.0, None
            self.found, self.pose = {}, None
            self.status.setText(NO_CAMERA)
            self.status.setStyleSheet(f"color: {theme.ERR}")
            self.picture.clear_frame(NO_CAMERA)
            logging.warning("%s: no frame for %.0f s: the camera is lost, it is opened again every %.0f s",
                            self.TITLE, now - self.last_frame_at, REOPEN_S)
            self.on_camera_lost()
            self.refresh_plate()
            self.refresh_place()
            self.refresh_info()

    def reopen_camera(self):
        old, self.cam = self.cam, None
        if old is not None:                              # closing a lost camera may wait: not in the window thread
            threading.Thread(target=old.close, daemon=True).start()
        self.shown_frames, self._n0, self._t0 = 0, 0, time.perf_counter()
        self.start_camera()

    def on_opened(self, cam, name):
        if cam is self.cam:
            logging.info("%s: camera open: %s", self.TITLE, name)

    def on_failed(self, cam, text):
        if cam is self.cam:
            self.status.setText(f"Camera error: {text}")
            self.status.setStyleSheet(f"color: {theme.ERR}")
            self.picture.clear_frame(text)
            logging.warning("%s: camera: %s", self.TITLE, text)

    def on_modes(self, cam, video):
        if cam is not self.cam:
            return
        self.video_modes = list(video)
        self.mode_box.clear()
        self.mode_box.addItems([f"{m.width}x{m.height}  ({m.fps} fps)" for m in self.video_modes])
        if cam.start_mode in self.video_modes:
            self.mode_box.setCurrentIndex(self.video_modes.index(cam.start_mode))
        self.mode_box.setEnabled(True)

    def on_mode_chosen(self, index):
        """Another resolution for this screen only: what belongs to the old picture is dropped."""
        if self.cam is None or not 0 <= index < len(self.video_modes) or self.video_modes[index] == self.cam.video_mode:
            return
        mode = self.video_modes[index]
        self.cam.choose_video(mode)
        self.wanted, self.wanted_at, self.wanted_tries = mode, time.perf_counter(), 1
        self.found, self.frame_size, self.last_bgr, self.pose = {}, None, None, None
        self.shown_frames = self.cam.frames
        self.status.setText("")
        self.picture.zoom, self.picture.centre = 0.0, None
        self.picture.clear_frame(f"Switching to {mode.width}x{mode.height} ...")
        self.on_mode_changed()
        self.refresh_plate()
        self.refresh_place()
        self.refresh_info()
        logging.info("%s: video mode of this screen %dx%d at %d fps", self.TITLE, mode.width, mode.height, mode.fps)

    def check_wanted_mode(self, cam, bgr):
        """The camera sometimes keeps another size after a switch: ask again, and say so when it does not help."""
        if self.wanted is None:
            return
        if (bgr.shape[1], bgr.shape[0]) == (self.wanted.width, self.wanted.height):
            self.wanted = None
        elif time.perf_counter() - self.wanted_at > SWITCH_WAIT_S:
            if self.wanted_tries >= SWITCH_TRIES:
                self.status.setText(f"The camera gives {bgr.shape[1]}x{bgr.shape[0]} instead of {self.wanted.width}x{self.wanted.height}")
                self.status.setStyleSheet(f"color: {theme.WARN}")
                logging.warning("%s: %s", self.TITLE, self.status.text())
                self.wanted = None
            else:
                self.wanted_tries += 1
                self.wanted_at = time.perf_counter()
                cam.choose_video(self.wanted)

    def on_frame(self, cam):
        if cam is not self.cam or cam.latest is None or cam.frames == self.shown_frames:
            return                                       # queued signals of frames that are already shown or of a dropped camera
        self.shown_frames = cam.frames
        self.last_frame_at = time.perf_counter()
        if self.cam_lost:
            self.cam_lost = False
            self.status.setText("")
            logging.info("%s: the camera is back", self.TITLE)
            self.on_camera_back()
        bgr = cv2.cvtColor(cam.latest, cv2.COLOR_BGRA2BGR)
        self.frame_size = bgr.shape[1], bgr.shape[0]
        self.last_bgr = bgr
        self.check_wanted_mode(cam, bgr)
        now = time.perf_counter()
        if not self.search_paused and not self.markers.busy and now - self._submitted >= DETECT_EVERY_S:
            self._submitted = now
            self.markers.submit(bgr)                     # the answer comes by on_markers
        self.present(bgr)
        self.on_new_frame(bgr)
        if now - self._t0 >= 1.0:
            self.fps, self._n0, self._t0 = (cam.frames - self._n0) / (now - self._t0), cam.frames, now
            self.refresh_info()

    def on_markers(self, found, ms, size):
        """The answer of the search: the plate, the place of the sheet, the Markers block. An answer for a picture of another size
        (the mode was switched meanwhile) is dropped."""
        if size != self.frame_size:
            return
        self.found, self.search_ms = found, ms
        self.pose = focus.sheet_pose(found)
        self.refresh_plate()
        self.refresh_place()
        self.refresh_info()

    # ----- what is said
    def plate_text(self):
        """(text, kind) of the plate of the sheet."""
        return focus.plate(self.found)

    def refresh_plate(self):
        text, kind = self.plate_text()
        self.plate.setText(text)
        self.plate.setStyleSheet(f"background: {PLATE_COLOURS[kind]}; color: white; font-size: 14px; font-weight: bold; "
                                 f"border-radius: 8px; padding: 8px")

    def sheet_whole(self):
        """Every marker of a work sheet is in view and the Vertical sheet is not on it."""
        return focus.plate(self.found)[1] == focus.GOOD

    def refresh_place(self):
        """The line of the place of the sheet (green when it is right) and the Finish button that depends on it."""
        if self.frame_size is None:
            ok, text, kind = False, "Sheet centre: -", focus.NEUTRAL
        else:
            ok, text, kind = focus.place_state(self.found, *self.frame_size, self.place_ok)
        self.place_ok = ok
        colour = GREY if kind == focus.NEUTRAL else PLATE_COLOURS[kind]
        self.place_line.setText(text)
        self.place_line.setStyleSheet(f"background: {colour}; color: white; font-family: {theme.MONO}; font-size: 13px; "
                                      f"border-radius: 6px; padding: 4px 8px")
        self.refresh_finish()

    def refresh_finish(self):
        self.finish_btn.setEnabled(self.place_ok and self.sheet_whole() and self.can_finish())

    def refresh_info(self):
        w, h = self.frame_size or (0, 0)
        self.cam_info.setText(f"Frame:    {w}x{h}\nFPS:      {self.fps:.1f}")
        self.info.setText("\n".join([f"Search:   {self.search_ms:.0f} ms"] + vertical.info_lines(self.found)))

    # ----- what is drawn on the picture
    def draw_overlay(self, out, to_view):
        """The markers, the blue cross of the sheet, the red cross of the frame over it, then the screen's own drawing."""
        for marker_id, quad in self.found.items():
            pts = np.int32([to_view(x, y) for x, y in quad])
            on_vertical = marker_id in vertical.SHEETS[0][1]
            cv2.polylines(out, [pts], True, BLANK_BGR if on_vertical else SHEET_BGR, 1, cv2.LINE_AA)
            cv2.putText(out, str(marker_id), (int(pts[:, 0].max()) + 4, int(pts[:, 1].min()) - 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, BLANK_ID_BGR if on_vertical else SHEET_BGR, 1, cv2.LINE_AA)
        if self.pose is not None:                                      # the cross of the sheet: where its centre and its axes are now
            centre, arm_x, arm_y = focus.sheet_cross(self.pose)
            for a, b in (arm_x, arm_y):
                p, q = to_view(*a), to_view(*b)
                cv2.line(out, (round(p[0]), round(p[1])), (round(q[0]), round(q[1])), AXES_BGR, AXES_PX, cv2.LINE_AA)
            c = to_view(*centre)
            cv2.circle(out, (round(c[0]), round(c[1])), 5, AXES_BGR, AXES_PX, cv2.LINE_AA)
        if self.frame_size is not None:                                # the red cross over the blue one: where to put the sheet centre
            w, h = self.frame_size
            draw_lines(out, *to_view((w - 1) / 2, (h - 1) / 2))
        self.draw_extra(out, to_view)

    # ----- Snap, Finish
    def snap(self):
        """Saves a picture for a look later: <snaps>/<YYYYMMDD>/<prefix>_<YYYYMMDD_HHMMSS>.png (DECISIONS.md 2026-11-20)."""
        image = self.snap_image()
        if image is None:
            return
        if self.snap_folder is None:
            self.snapped(False, "Snap: no folder is set")
            return
        try:
            now = datetime.datetime.now()
            day = self.snap_folder / f"{now:%Y%m%d}"
            day.mkdir(parents=True, exist_ok=True)
            path = day / f"{self.snap_prefix()}_{now:%Y%m%d_%H%M%S}.png"
            ok = cv2.imwrite(str(path), image)
        except OSError as err:
            ok, path = False, err
        self.snapped(ok, f"Saved {path.name}" if ok else f"Snap failed: {path}")
        logging.info("%s: snap %s (%s)", self.TITLE, path, "saved" if ok else "failed")

    def snap_prefix(self):
        return self.TITLE.lower()

    def snap_image(self):
        """The whole camera frame, no drawing."""
        return self.last_bgr

    def snapped(self, ok, text):
        """Tells the result of Snap; Measure shows it in its Snackbar."""
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {theme.TEXT2 if ok else theme.ERR}")

    def on_finish_clicked(self):
        if self.finish_btn.isEnabled():
            self.finish()

    # ----- the end: the search and the camera are stopped
    def showEvent(self, event):
        super().showEvent(event)
        center_on(self, self.screen())

    def done(self, result):
        self.watchdog.stop()
        self.markers.stop()
        cam, self.cam = self.cam, None
        if cam is not None:
            cam.close()                                  # the camera is free for the next window
        super().done(result)
