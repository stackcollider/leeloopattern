"""Vertical: the step of Commissioning that puts the camera vertical (docs/DECISIONS.md 2026-11-15), transferred from
tools/camera_setup2.py step by step. Done: the live picture of the camera of the profile, the search for the markers of the
Vertical sheet, the plates, the Markers block, the lines of the frame centre, the zoom (step 1); Centre, Check, the green point,
Show measured sides, the plate with the result of the Check (step 2); Resolution (this screen only), Snap, Fit (step 3); Cancel and
Finish, the result for the hub, the line in the log (step 4).

The way it is used: Check (the sheet in the middle) -> the green point appears (where the camera should look to be vertical) ->
move the sheet, aim the camera (the dim ring is where it looks now) -> Check again, until the plate is green (tilt below 3 deg).

Finish opens only for a green Check (tilt below 3 deg): the dialog ends with Accepted and `result` (the figures of the Check) and the
line for the journal goes to the log; Cancel ends it with Rejected and no result.
A modal dialog over the Commissioning hub (the hub hides meanwhile). The camera is opened here and released when the dialog ends.
The live video starts in a working mode (not wider than WORKING_WIDTH): the video mode of the profile may be the biggest one,
too slow for a live set-up; the mode of the profile is shown for information.
"""
import datetime
import logging
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from patterndxf import appinfo
from patterndxf.camera.source import CameraSource, VideoMode
from patterndxf.sheet import vertical
from patterndxf.ui import theme
from patterndxf.ui.splash import center_on
from patterndxf.ui.widgets import ZoomPicture, card

WORKING_WIDTH = 1280                            # the live video of this step: the biggest mode not wider than this (1280x960 at 30 fps)
DETECT_EVERY = 3                                # the markers are searched on every 3rd frame
SWITCH_WAIT_S, SWITCH_TRIES = 2.5, 3         # a requested mode is asked again after this many seconds without frames of that size
MEASURE_FRAMES, MEASURE_TRIES = 10, 40          # Check averages this many good searches and gives up after this many
LIVE_AVG, LIVE_MISSES = 8, 5                    # the live ring: the mean of the last 8 poses, forgotten after 5 searches without one
LINE_BGR = (0, 0, 255)                          # the lines of the frame centre: red, 1 px at any zoom
BLANK_BGR, SHEET_BGR = (80, 220, 100), (0, 200, 255)       # outlines: the Vertical sheet green, the markers of the work sheets yellow
BLANK_ID_BGR = (0, 110, 0)                      # the IDs of the Vertical sheet markers: dark green (readable on white and on green)
SIDE_BGR, LABEL_SCALE = (110, 110, 110), 0.6    # the measured sides and their labels: grey
GREEN_BGR, DIM_GREEN_BGR = (0, 255, 0), (0, 120, 0)
PLATE_COLOURS = {vertical.NEUTRAL: theme.TEXT2, vertical.BAD: theme.ERR, vertical.GOOD: theme.OK, vertical.WARN: theme.WARN}
GREY = "#6b6b82"


def draw_lines(img, x, y):
    """A vertical line at x and a horizontal line at y of the screen picture (float pixel coordinates), 1 px, sub-pixel position."""
    h, w = img.shape[:2]
    k = 16                                              # sub-pixel precision: shift 4 bits
    if 0 <= x <= w - 1:
        cv2.line(img, (round(x * k), 0), (round(x * k), (h - 1) * k), LINE_BGR, 1, cv2.LINE_AA, 4)
    if 0 <= y <= h - 1:
        cv2.line(img, (0, round(y * k)), ((w - 1) * k, round(y * k)), LINE_BGR, 1, cv2.LINE_AA, 4)


def draw_point(out, point, to_view, radius, colour, dot):
    """A ring (and a dot) at a camera point, drawn on the screen picture."""
    vh, vw = out.shape[:2]
    gx, gy = to_view(*point)
    if -20 <= gx <= vw + 20 and -20 <= gy <= vh + 20:
        cv2.circle(out, (round(gx), round(gy)), radius, colour, 2 if dot else 1, cv2.LINE_AA)
        if dot:
            cv2.circle(out, (round(gx), round(gy)), 2, colour, -1, cv2.LINE_AA)


def draw_sides(out, centres, to_view):
    """The four corner marker centres, the sides between them and their lengths (px), drawn on the screen picture."""
    pts = [to_view(x, y) for x, y in centres]
    lengths = vertical.side_lengths(centres)
    vh, vw = out.shape[:2]
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        cv2.line(out, (round(a[0]), round(a[1])), (round(b[0]), round(b[1])), SIDE_BGR, 1, cv2.LINE_AA)
        cv2.circle(out, (round(a[0]), round(a[1])), 4, SIDE_BGR, 1, cv2.LINE_AA)
        text = f"{vertical.SIDE_NAMES[i]} {lengths[i]:.1f}"
        mx, my = 0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]      # a quarter along the side: the middle is taken by the middle marker
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, LABEL_SCALE, 1)
        # the label outside the quadrangle: above the top side, below the bottom, left of the left, right of the right
        pos = {0: (mx - tw / 2, my - 8), 2: (mx - tw / 2, my + th + 8), 1: (mx + 8, my + th / 2), 3: (mx - tw - 8, my + th / 2)}[i]
        x = min(max(pos[0], 4), vw - tw - 4)                              # a label near the border of the view is moved inside
        y = min(max(pos[1], th + 4), vh - 4)
        cv2.putText(out, text, (round(x), round(y)), cv2.FONT_HERSHEY_SIMPLEX, LABEL_SCALE, SIDE_BGR, 1, cv2.LINE_AA)


class VerticalDialog(QDialog):
    def __init__(self, profile, snap_folder=None, parent=None):
        """profile: the Profile in force (its camera is opened); snap_folder: where Snap saves the picture."""
        super().__init__(parent)
        self.setWindowTitle(f"{appinfo.NAME} {appinfo.version()}: Vertical")
        self.setModal(True)
        self.profile = profile
        self.snap_folder = Path(snap_folder) if snap_folder else None
        self.last_bgr, self.video_modes = None, []
        self.wanted, self.wanted_at, self.wanted_tries = None, 0.0, 0     # the mode requested in the box until frames of its size arrive
        self.result = None                               # after Finish: {tilt, lr, fb, sides, mode, journal} for the hub
        self.detector = vertical.make_detector()
        self.found, self.search_ms, self.shown_frames = {}, 0.0, 0
        self.frame_size = None
        self._n0, self._t0, self.fps = 0, time.perf_counter(), 0.0
        self.live, self.live_misses, self.live_pose = deque(maxlen=LIVE_AVG), 0, None      # the last poses and their mean
        self.centre_ok = False                           # the condition for Check: the sheet centre is on the cross
        self.collect, self.tries = None, 0               # the poses collected by Check (None = not measuring)
        self.centres, self.target, self.check_failed = None, None, False    # the result of the last Check
        self.cam = None

        self.picture = ZoomPicture("Opening the camera ...")
        self.plate = QLabel("")
        self.result_plate = QLabel("")                   # the result of the last Check: the tilt and the traffic light (does not jump)
        self.centre_line = QLabel("Centre: -")
        for label in (self.plate, self.result_plate):
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
        self.plate.setMinimumHeight(60)
        self.result_plate.setMinimumHeight(70)
        self.centre_line.setMinimumHeight(26)
        self.centre_line.setWordWrap(True)
        self.info = QLabel("")
        self.info.setStyleSheet(f"font-family: {theme.MONO}; font-size: 13px")
        self.mode_box = QComboBox()                      # the video mode of this screen only; the profile is not changed
        self.mode_box.setEnabled(False)
        self.settings_line = QLabel(f"Settings: {VideoMode(*profile.video).width}x{VideoMode(*profile.video).height} "
                                    f"at {profile.video[2]} fps\nThe choice here is for this screen only.")
        self.settings_line.setStyleSheet(f"color: {theme.TEXT2}; font-size: 13px")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.check_btn = QPushButton("Check")
        self.check_btn.setObjectName("primary")
        self.show_btn = QPushButton("Show measured sides")
        self.show_btn.setCheckable(True)
        self.show_btn.setEnabled(False)
        self.snap_btn, self.fit_btn = QPushButton("Snap"), QPushButton("Fit")
        self.cancel_btn, self.finish_btn = QPushButton("Cancel"), QPushButton("Finish")
        self.finish_btn.setEnabled(False)                # opens only for a green Check
        for b in (self.check_btn, self.show_btn, self.snap_btn, self.fit_btn, self.cancel_btn, self.finish_btn):
            b.setMinimumHeight(44)

        side = QVBoxLayout()
        side.addWidget(card("Resolution", self.mode_box, self.settings_line))
        side.addWidget(self.plate)
        side.addWidget(self.result_plate)
        side.addWidget(card("Markers", self.info))
        side.addStretch(1)
        side.addWidget(self.status)
        side.addWidget(self.centre_line)
        side.addWidget(self.check_btn)
        side.addWidget(self.show_btn)
        side.addWidget(self.snap_btn)
        side.addWidget(self.fit_btn)
        end = QHBoxLayout()
        end.addWidget(self.cancel_btn)
        end.addWidget(self.finish_btn)
        side.addLayout(end)
        right = QWidget()
        right.setFixedWidth(380)
        right.setLayout(side)
        title = QLabel("Vertical")
        title.setStyleSheet(f"color: {theme.KEY}; font-size: 26px")
        hint = QLabel("Put the Vertical sheet flat in the middle of the table, marker 46 top left.")
        hint.setStyleSheet(f"color: {theme.TEXT2}; font-size: 15px")
        body = QHBoxLayout()
        body.addWidget(self.picture, 1)
        body.addWidget(right)
        lay = QVBoxLayout()
        lay.addWidget(title)
        lay.addWidget(hint)
        lay.addLayout(body, 1)
        self.setLayout(lay)
        self.resize(1500, 900)
        self.check_btn.clicked.connect(self.start_check)
        self.show_btn.toggled.connect(self.on_show_toggled)
        self.snap_btn.clicked.connect(self.snap)
        self.fit_btn.clicked.connect(self.picture.fit)
        self.mode_box.activated.connect(self.on_mode_chosen)
        self.cancel_btn.clicked.connect(self.reject)
        self.finish_btn.clicked.connect(self.finish)
        self.refresh_plate()
        self.refresh_info()
        self.refresh_centre(None)
        self.show_result()
        self.start_camera()

    # ----- the camera
    def start_camera(self):
        p = self.profile
        self.cam = cam = CameraSource(name=p.camera_name, vid_pid=p.camera_vid_pid, max_width=WORKING_WIDTH)
        cam.frame.connect(lambda c=cam: self.on_frame(c))
        cam.opened.connect(lambda name, c=cam: self.on_opened(c, name))
        cam.modes.connect(lambda video, photo, c=cam: self.on_modes(c, video))
        cam.failed.connect(lambda text, c=cam: self.on_failed(c, text))
        cam.open()

    def on_modes(self, cam, video):
        """The video modes of the camera go into the box; the one the video starts in is chosen."""
        if cam is not self.cam:
            return
        self.video_modes = list(video)
        self.mode_box.clear()
        self.mode_box.addItems([f"{m.width}x{m.height}  ({m.fps} fps)" for m in self.video_modes])
        if cam.start_mode in self.video_modes:
            self.mode_box.setCurrentIndex(self.video_modes.index(cam.start_mode))
        self.mode_box.setEnabled(True)

    def check_wanted_mode(self, cam, bgr):
        """The camera sometimes keeps another size after a switch: if the frames are not of the requested size, ask again,
        and say so when it does not help."""
        if self.wanted is None:
            return
        if (bgr.shape[1], bgr.shape[0]) == (self.wanted.width, self.wanted.height):
            self.wanted = None
        elif time.perf_counter() - self.wanted_at > SWITCH_WAIT_S:
            if self.wanted_tries >= SWITCH_TRIES:
                self.status.setText(f"The camera gives {bgr.shape[1]}x{bgr.shape[0]} instead of {self.wanted.width}x{self.wanted.height}")
                self.status.setStyleSheet(f"color: {theme.WARN}")
                logging.warning("Vertical: %s", self.status.text())
                self.wanted = None
            else:
                self.wanted_tries += 1
                self.wanted_at = time.perf_counter()
                logging.info("Vertical: asking for %dx%d again (%d)", self.wanted.width, self.wanted.height, self.wanted_tries)
                cam.choose_video(self.wanted)

    def on_mode_chosen(self, index):
        """Another resolution for this screen: the results that belong to the old picture are dropped."""
        if self.cam is None or not 0 <= index < len(self.video_modes) or self.video_modes[index] == self.cam.video_mode:
            return
        mode = self.video_modes[index]
        self.cam.choose_video(mode)
        self.wanted, self.wanted_at, self.wanted_tries = mode, time.perf_counter(), 1
        self.status.setText("")
        self.found, self.live_pose, self.frame_size, self.last_bgr = {}, None, None, None
        self.live.clear()
        self.live_misses, self.shown_frames = 0, self.cam.frames
        self.clear_check()
        self.picture.zoom, self.picture.centre = 0.0, None
        self.picture.clear_frame(f"Switching to {mode.width}x{mode.height} ...")
        self.refresh_centre(None)
        self.refresh_plate()
        self.refresh_info()
        logging.info("Vertical: video mode of this screen %dx%d at %d fps", mode.width, mode.height, mode.fps)

    def on_opened(self, cam, name):
        if cam is self.cam:
            logging.info("Vertical: camera open: %s", name)

    def on_failed(self, cam, text):
        if cam is self.cam:
            self.status.setText(f"Camera error: {text}")
            self.status.setStyleSheet(f"color: {theme.ERR}")
            self.picture.clear_frame(text)
            logging.warning("Vertical: camera: %s", text)

    def on_frame(self, cam):
        if cam is not self.cam or cam.latest is None or cam.frames == self.shown_frames:
            return                                       # queued signals of frames that are already shown or of a dropped camera
        self.shown_frames = cam.frames
        bgr = cv2.cvtColor(cam.latest, cv2.COLOR_BGRA2BGR)
        self.frame_size = bgr.shape[1], bgr.shape[0]
        self.last_bgr = bgr
        self.check_wanted_mode(cam, bgr)
        if cam.frames % DETECT_EVERY == 1 or not self.found:
            self.search(bgr)
        self.picture.set_frame(bgr, self.draw_overlay)
        now = time.perf_counter()
        if now - self._t0 >= 1.0:
            self.fps, self._n0, self._t0 = (cam.frames - self._n0) / (now - self._t0), cam.frames, now
            self.refresh_info()

    # ----- the search for the markers and what follows from it
    def search(self, bgr):
        t0 = time.perf_counter()
        self.found = vertical.search(self.detector, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        self.search_ms = (time.perf_counter() - t0) * 1000
        self.after_search()

    def after_search(self):
        w, h = self.frame_size
        self.update_live(w, h)
        self.refresh_centre((w, h))
        self.collect_poses(w, h)
        self.refresh_plate()
        self.refresh_info()

    def update_live(self, w, h):
        """The live ring: the mean of the last poses; forgotten after LIVE_MISSES searches without a pose."""
        pose = vertical.estimate_pose(self.found, w, h)
        if pose is None:
            self.live_misses += 1
            if self.live_misses >= LIVE_MISSES:
                self.live.clear()
        else:
            self.live.append(pose)
            self.live_misses = 0
        self.live_pose = ({k: np.mean([p[k] for p in self.live], axis=0) for k in ("nadir", "tilt", "lr", "fb")}
                          if self.live else None)

    def refresh_plate(self):
        text, kind = vertical.plate(self.found)
        self._set_plate(self.plate, text, PLATE_COLOURS[kind])

    @staticmethod
    def _set_plate(label, text, colour):
        label.setText(text)
        label.setStyleSheet(f"background: {colour}; color: white; font-size: 14px; font-weight: bold; border-radius: 8px; padding: 8px")

    def refresh_info(self):
        w, h = self.frame_size or (0, 0)
        lines = [f"Frame:    {w}x{h}", f"FPS:      {self.fps:.1f}", f"Search:   {self.search_ms:.0f} ms"] + vertical.info_lines(self.found)
        self.info.setText("\n".join(lines))

    # ----- Centre: the condition for Check
    def refresh_centre(self, size):
        if size is None:
            ok, text, kind = False, "Centre: -", vertical.NEUTRAL
        else:
            ok, text, kind = vertical.centre_state(self.found, size[0], size[1], self.centre_ok)
        self.centre_ok = ok
        colour = GREY if kind == vertical.NEUTRAL else PLATE_COLOURS[kind]
        self.centre_line.setText(text)
        self.centre_line.setStyleSheet(f"background: {colour}; color: white; border-radius: 6px; padding: 4px 8px")
        self.refresh_check_button()

    def refresh_check_button(self):
        """Check is open only when the sheet centre is on the cross and no measurement is running."""
        self.check_btn.setEnabled(self.collect is None and self.centre_ok)

    # ----- Check: the pose averaged over MEASURE_FRAMES searches
    def start_check(self):
        if self.frame_size is None or not self.centre_ok:
            return
        self.collect, self.tries = [], 0
        self.centres, self.target, self.check_failed = None, None, False
        self.show_btn.setChecked(False)
        self.show_btn.setEnabled(False)
        self.status.setText("")
        self.show_result()
        self.refresh_check_button()

    def collect_poses(self, w, h):
        if self.collect is None:
            return
        self.tries += 1
        pose = vertical.estimate_pose(self.found, w, h)
        if pose is not None:
            self.collect.append((np.array([self.found[i].mean(axis=0) for i in vertical.CORNER_IDS]), pose))
        if len(self.collect) >= MEASURE_FRAMES:
            self.centres = np.mean([c for c, _ in self.collect], axis=0)
            self.target = {k: np.mean([p[k] for _, p in self.collect], axis=0) for k in ("nadir", "tilt", "lr", "fb")}
            self.collect = None
            dx, dy = self.target["nadir"][0] - (w - 1) / 2, self.target["nadir"][1] - (h - 1) / 2
            logging.info("Vertical: Check: tilt %.1f deg (lr %+.1f, fb %+.1f), green point %+.0f, %+.0f px from the cross, sides %s",
                         self.target["tilt"], self.target["lr"], self.target["fb"], dx, dy,
                         ", ".join(f"{n} {v:.1f}" for n, v in zip(vertical.SIDE_NAMES, vertical.side_lengths(self.centres))))
            self.show_btn.setEnabled(True)
            self.show_btn.setChecked(True)               # the lines show at once
        elif self.tries >= MEASURE_TRIES:
            self.collect, self.check_failed = None, True
            self.status.setText("Check failed: the four corner markers of the Vertical sheet were not found (or the sheet is turned) "
                                "in enough searches")
            logging.warning("Vertical: %s", self.status.text())
        else:
            return
        self.show_result()
        self.refresh_check_button()

    def show_result(self):
        """The plate of the last Check: not done / measuring / failed / the tilt with the traffic light. It is set by Check only."""
        if self.collect is not None:
            text, colour = "Check: measuring ...", GREY
        elif self.target is not None:
            text, kind = vertical.level_plate(float(self.target["tilt"]), float(self.target["lr"]), float(self.target["fb"]))
            colour = PLATE_COLOURS[kind]
        elif self.check_failed:
            text, colour = "Check failed: no result", theme.ERR
        else:
            text, colour = "Check: not done", GREY
        self._set_plate(self.result_plate, text, colour)
        self.finish_btn.setEnabled(self.collect is None and self.target is not None
                                   and float(self.target["tilt"]) < vertical.LEVEL_GOOD_DEG)

    def clear_check(self):
        """No result: the measurement belongs to one picture (one mode)."""
        self.collect, self.centres, self.target, self.check_failed = None, None, None, False
        self.show_btn.setChecked(False)
        self.show_btn.setEnabled(False)
        self.show_result()
        self.refresh_check_button()

    def snap(self):
        """Saves the picture on the screen (the whole frame, no drawing) for a look later."""
        if self.last_bgr is None:
            return
        if self.snap_folder is None:
            self.status.setText("Snap: no folder is set")
            return
        try:
            self.snap_folder.mkdir(parents=True, exist_ok=True)
            path = self.snap_folder / f"vertical_{datetime.datetime.now():%Y%m%d_%H%M%S}.png"
            ok = cv2.imwrite(str(path), self.last_bgr)
        except OSError as err:
            ok, path = False, err
        self.status.setText(f"Saved {path.name}" if ok else f"Snap failed: {path}")
        self.status.setStyleSheet(f"color: {theme.TEXT2 if ok else theme.ERR}")
        logging.info("Vertical: snap %s (%s)", path, "saved" if ok else "failed")

    def journal_line(self):
        """One line for the log: when, the tilt, the sides, the mode, the height, the camera."""
        t = self.target
        sides = ", ".join(f"{n} {v:.1f}" for n, v in zip(vertical.SIDE_NAMES, vertical.side_lengths(self.centres)))
        return (f"Vertical: tilt {float(t['tilt']):.1f} deg (left-right {float(t['lr']):+.1f}, front-back {float(t['fb']):+.1f}); "
                f"sides px {sides}; mode {self.mode_box.currentText().split('  ')[0]}; "
                f"reading {vertical.PIXELS_PER_CELL} px per cell; height {vertical.HEIGHT_MM:g} mm; camera {self.profile.camera_text}")

    def finish(self):
        """The end of the step: the figures go to the hub (Accepted), the line goes to the log. Only for a green Check."""
        if not self.finish_btn.isEnabled():
            return
        line = self.journal_line()
        logging.info("%s", line)
        self.result = {"tilt": float(self.target["tilt"]), "lr": float(self.target["lr"]), "fb": float(self.target["fb"]),
                       "sides": vertical.side_lengths(self.centres), "journal": line}
        self.accept()

    def on_show_toggled(self, shown):
        self.show_btn.setText("Hide measured sides" if shown else "Show measured sides")
        self.picture.render()

    # ----- what is drawn on the picture
    def draw_overlay(self, out, to_view):
        """On the screen picture: the lines through the frame centre, the markers, the measured sides, the green point."""
        if self.frame_size is None:
            return
        w, h = self.frame_size
        cx, cy = to_view((w - 1) / 2, (h - 1) / 2)
        draw_lines(out, cx, cy)
        for marker_id, quad in self.found.items():
            pts = np.int32([to_view(x, y) for x, y in quad])
            on_vertical = marker_id in vertical.SHEETS[0][1]
            cv2.polylines(out, [pts], True, BLANK_BGR if on_vertical else SHEET_BGR, 1, cv2.LINE_AA)
            cv2.putText(out, str(marker_id), (int(pts[:, 0].max()) + 4, int(pts[:, 1].min()) - 4), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, BLANK_ID_BGR if on_vertical else SHEET_BGR, 1, cv2.LINE_AA)
        if self.centres is not None and self.show_btn.isChecked():
            draw_sides(out, self.centres, to_view)
        if self.target is not None:                      # the green point: where the camera should look to be vertical
            draw_point(out, self.target["nadir"], to_view, 9, GREEN_BGR, True)
            if self.live_pose is not None:               # the dim ring: where it looks now (the camera's reaction)
                draw_point(out, self.live_pose["nadir"], to_view, 5, DIM_GREEN_BGR, False)

    # ----- the end: the camera is released
    def showEvent(self, event):
        super().showEvent(event)
        center_on(self, self.screen())

    def done(self, result):
        cam, self.cam = self.cam, None
        if cam is not None:
            cam.close()                                  # the camera is free for the next window
        super().done(result)
