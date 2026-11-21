"""Calibration: the step of Commissioning that builds the model of the sheet by the nodes of its grid (docs/DECISIONS.md 2026-11-19),
tried first as tools/calibration_test.py.

The way it is used: the work sheet clean (nothing on it), the blue cross on the red one; Capture opens the steps dialog
(the live picture on the left, what is wrong in red): the sheet in view, the sheet empty, N frames, their median, the markers,
the coarse model, the grid nodes, the lens model and field, the hold-out check, the check by the nodes, the normalised sheet,
saved. Show normalised: the sheet from above as it lies, with the ideal grid on request.
Finish opens after a good capture (hold-out p95 not more than FAIL_P95_MM): the calibration is written next to the profile
(calibration.json) and the hold-out p95 goes to the hub.
At the start of the program (at_start=True, DECISIONS.md 2026-11-20): this screen is never shown, only the steps dialog;
Capture runs by itself as soon as the whole sheet is in view. Green p95: the result is seen for a moment, the calibration is
written, start_outcome "measure". Yellow: Continue (the Close button) does the same. Red: the button Commissioning ->
"commissioning". Cancel, or Close after another failure -> "exit" (the start asks "Exit the program?").
NOT here yet: the camera layer of the TZ (locked exposure / white balance / focus, the light check).
"""
import datetime
import json
import logging
import time
import traceback

import cv2
import numpy as np
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMainWindow, QPushButton, QSpinBox, QVBoxLayout, QWidget

from patterndxf import profile
from patterndxf.calib import load_sheets, nodecal, stand
from patterndxf.sheet import empty, focus
from patterndxf.ui import theme
from patterndxf.ui.sheet_screen import AXES_BGR, GREY, SheetScreen
from patterndxf.ui.splash import center_on
from patterndxf.ui.steps_dialog import StepsDialog
from patterndxf.ui.widgets import ZoomPicture, card

FRAMES_DEFAULT = 15                             # the TZ: 15 frames (1 frame gives p95 0.038 instead of 0.033 mm on the 16MP)
MIN_PPM = 5.0                                   # the TZ (to revisit): output px/mm = max(camera px/mm, 5)
WARN_P95_MM, FAIL_P95_MM = 0.06, 0.1            # the plate: green up to the first (the worst profile mode gave 0.057), red above 0.1 (refusal)
MIN_COVERAGE = 0.99                             # the inner nodes found must be at least this part (the reference sets: all of them)
EMPTY_EVERY_S = 0.3                             # the live check "the sheet is empty" while it fails
PROBLEM_BGR = (60, 60, 255)                     # what is wrong, on the live picture of the dialog


# the rows of the Capture dialog, in their order
STEP_ROWS = (("sheet", "Sheet in view"), ("empty", "Sheet is empty"), ("frames", "Frames"), ("median", "Median picture")) + stand.STEPS + (
    ("holdout", "Hold-out check"), ("check", "Check by the nodes"), ("normalised", "Normalised sheet"), ("saved", "Saved"))


class CalibrateThread(QThread):
    """median -> the calibration step by step -> hold-out -> by the nodes -> the redrawn sheet, away from the window thread.
    Every step is told by `started` and `finished`; `cancel` (set from the window) stops it between two steps."""
    started_step = Signal(str)                      # the key of the step that begins
    finished_step = Signal(str, str, str)           # the key, the figure, the kind (ok / warn)
    failed = Signal(str, str)                       # the key of the step that failed, the reason ("" key: cancelled)
    done = Signal(object, object)                   # the result dict, the redrawn colour picture

    def __init__(self, grays, color, sheets):
        super().__init__()
        self.grays, self.color, self.sheets = grays, color, sheets
        self.cancel, self.current = False, ""
        self.holes = None                               # the inner nodes that were not found (camera px), when too many

    def begin(self, key):
        if self.cancel:
            raise stand.Cancelled()
        self.current = key
        self.started_step.emit(key)

    def run(self):
        order = [k for k, _ in stand.STEPS]
        try:
            res, t = {}, time.perf_counter()
            self.begin("median")
            median = np.median(np.stack(self.grays), axis=0).astype(np.uint8)
            res["median_s"] = time.perf_counter() - t
            self.finished_step.emit("median", f"{median.shape[1]}x{median.shape[0]}", "ok")

            def report(key, text):                      # the steps of the calibration, one after the other
                self.finished_step.emit(key, text, "ok")
                nxt = order.index(key) + 1
                if nxt < len(order):
                    self.begin(order[nxt])

            t = time.perf_counter()
            self.begin(order[0])
            cal = stand.calibrate_steps(median, self.sheets, report=report)
            res["calibrate_s"] = time.perf_counter() - t
            m = cal.meta
            if m["inner_found"] < MIN_COVERAGE * m["inner_expected"]:   # a part of the sheet is covered: no model on a hole
                found = {ij for ij, (_p, kind) in cal.all_nodes.items() if kind == "inner"}
                missing = [cal.sheet.mm(*ij) for ij in cal.sheet.nodes() if cal.sheet.kind(*ij) == "inner" and ij not in found]
                self.holes = cal.to_px(missing) if missing else None
                self.failed.emit("nodes", f"{m['inner_expected'] - m['inner_found']} of {m['inner_expected']} inner nodes are "
                                          "missing (red on the picture): something covers the sheet there")
                return

            t = time.perf_counter()
            self.begin("holdout")
            h = res["holdout"] = cal.holdout()
            res["holdout_s"] = time.perf_counter() - t
            figure = f"p95 {h['p95']:.3f} mm"
            if h["p95"] > FAIL_P95_MM:
                self.failed.emit("holdout", f"hold-out p95 {h['p95']:.3f} mm is more than {FAIL_P95_MM:g} mm: the model is not good enough")
                return
            self.finished_step.emit("holdout", figure, "ok" if h["p95"] <= WARN_P95_MM else "warn")

            res["camera_ppm"] = cal.px_per_mm()
            ppm = res["ppm"] = max(res["camera_ppm"], MIN_PPM)
            t = time.perf_counter()
            self.begin("check")
            b = res["by_nodes"] = cal.check_redraw(median, ppm)
            res["check_s"] = time.perf_counter() - t
            self.finished_step.emit("check", f"p95 {b['p95']:.3f} mm", "ok")

            t = time.perf_counter()
            self.begin("normalised")
            top = cv2.rotate(cal.redraw(self.color, ppm), cv2.ROTATE_90_COUNTERCLOCKWISE)   # as the sheet lies (A)
            res["tables_s"] = time.perf_counter() - t
            self.finished_step.emit("normalised", f"{top.shape[1]}x{top.shape[0]} at {ppm:.1f} px/mm", "ok")
            res["meta"], res["frames"] = cal.meta, len(self.grays)
            res["cal"], res["median"] = cal, median
            self.done.emit(res, top)
        except stand.Cancelled:
            self.failed.emit("", self.current)
        except nodecal.CalibrationError as err:
            self.failed.emit(self.current, str(err))
        except Exception as err:                      # a plain line, never a silent death of the thread
            self.failed.emit(self.current, f"{type(err).__name__}: {err}")
            traceback.print_exc()


IDEAL_BGR = AXES_BGR                            # the ideal grid over the normalised sheet: sky blue, 1 px


class NormalisedWindow(QMainWindow):
    """The sheet redrawn from above AS IT LIES on the stand (the page turned 90 deg counter-clockwise: marker 5 top left,
    the page x axis runs up), with a zoom, the ideal grid on request and Save."""

    def __init__(self, top, ppm, cal, folder, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Normalised sheet {folder.name}")
        self.top, self.ppm, self.cal, self.folder = top, ppm, cal, folder
        self.lines = None                                # the ideal grid in sheet mm, made on the first show
        self.picture = ZoomPicture("")
        info = QLabel(f"{top.shape[1]}x{top.shape[0]} px at {ppm:.2f} px/mm\nas it lies: marker "
                      f"{focus.TOP_LEFT.get(cal.sheet.name, '?')} top left")
        info.setStyleSheet(f"font-family: {theme.MONO}; font-size: 13px")
        hint = QLabel("Ideal grid: lines every 10 mm. Zoom in: where the printed lines leave the blue ones, the model is wrong there.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT2}; font-size: 12px")
        self.status = QLabel("")
        self.ideal_btn = QPushButton("Show ideal grid")
        self.ideal_btn.setCheckable(True)
        fit, save = QPushButton("Fit"), QPushButton("Save")
        for b in (self.ideal_btn, fit, save):
            b.setMinimumHeight(44)
        side = QVBoxLayout()
        side.addWidget(card("Normalised sheet", info))
        side.addWidget(hint)
        side.addStretch(1)
        side.addWidget(self.status)
        side.addWidget(self.ideal_btn)
        side.addWidget(fit)
        side.addWidget(save)
        right = QWidget()
        right.setFixedWidth(300)
        right.setLayout(side)
        lay = QHBoxLayout()
        lay.addWidget(self.picture, 1)
        lay.addWidget(right)
        box = QWidget()
        box.setLayout(lay)
        self.setCentralWidget(box)
        self.resize(1400, 900)
        self.picture.set_frame(top, self.draw_overlay)
        self.ideal_btn.toggled.connect(self.on_ideal)
        fit.clicked.connect(self.picture.fit)
        save.clicked.connect(self.save)

    def on_ideal(self, shown):
        self.ideal_btn.setText("Hide ideal grid" if shown else "Show ideal grid")
        self.picture.render()

    def to_pic(self, x, y):
        """Sheet mm (page coordinates) -> a pixel of the turned picture: the redrawn page pixel (u, v) goes to (v, W - 1 - u)."""
        bx0, by0 = self.cal.box()[:2]
        u, v = (x - bx0) * self.ppm, (y - by0) * self.ppm
        return v, self.top.shape[0] - 1 - u

    def draw_overlay(self, out, to_view):
        if not self.ideal_btn.isChecked():
            return
        if self.lines is None:                           # every cell side of the grid, except inside the marker blocks (not printed)
            s = self.cal.sheet
            self.lines = []
            for i in range(s.nx + 1):
                for j in range(s.ny + 1):
                    a = s.mm(i, j)
                    for b in ((s.mm(i + 1, j) if i < s.nx else None), (s.mm(i, j + 1) if j < s.ny else None)):
                        if b is not None and not s.inside_block((a[0] + b[0]) / 2, (a[1] + b[1]) / 2):
                            self.lines.append((a, b))
        for a, b in self.lines:
            p, q = to_view(*self.to_pic(*a)), to_view(*self.to_pic(*b))
            cv2.line(out, (round(p[0]), round(p[1])), (round(q[0]), round(q[1])), IDEAL_BGR, 1, cv2.LINE_AA)

    def save(self):
        path = self.folder / "normalised.png"
        ok = cv2.imwrite(str(path), self.top)
        self.status.setText(f"Saved {path.name}" if ok else "Save failed")




class CalibrationDialog(SheetScreen):
    TITLE = "Calibration"
    HINT = "Clear the work sheet (nothing on it), the blue cross on the red one, then press Capture."
    start_outcome = Signal(str)                                    # at_start: "measure", "commissioning" or "exit"

    def __init__(self, profile_, snap_folder=None, parent=None, at_start=False, screen=None):
        super().__init__(profile_, snap_folder, parent)
        self.at_start = at_start
        self.auto_started, self.red = False, False                 # at_start: Capture has run by itself; the p95 was red
        self.sheets = load_sheets()
        self.collect, self.worker, self.steps = None, None, None   # the frames being taken; the calibration thread; the dialog
        self.result_cal, self.top, self.folder = None, None, None   # the last good capture
        self.problem_spots, self.problem_holes = [], None         # what is wrong, red on the live picture of the steps dialog
        self.empty_live, self._empty_at = False, 0.0              # the check "the sheet is empty" runs live while it fails
        self.norm_windows = []

        self.result_plate = QLabel("")
        self.result_plate.setWordWrap(True)
        self.info_area.addWidget(self.result_plate)
        self.frames_box = QSpinBox()
        self.frames_box.setRange(1, 60)
        self.frames_box.setValue(FRAMES_DEFAULT)
        self.frames_box.setSuffix(" frames")
        self.capture_btn = QPushButton("Capture")
        self.capture_btn.setObjectName("primary")
        self.norm_btn = QPushButton("Show normalised")
        self.norm_btn.setEnabled(False)
        for b in (self.capture_btn, self.norm_btn):
            b.setMinimumHeight(44)
        row = QHBoxLayout()
        row.addWidget(self.frames_box)
        row.addWidget(self.capture_btn, 1)
        self.actions.addLayout(row)
        self.actions.addWidget(self.norm_btn)
        self.capture_btn.clicked.connect(self.start_capture)
        self.norm_btn.clicked.connect(self.show_normalised)
        self.show_result()
        self.begin()
        if at_start:                                               # only the steps dialog: waiting for the sheet
            self.make_steps()
            self.steps.start("sheet")
            self.steps.show()
            if screen is not None:
                center_on(self.steps, screen)

    # ----- the start of the program: Capture by itself, the end decides where to go
    def on_markers(self, found, ms, size):
        super().on_markers(found, ms, size)
        if self.at_start and not self.auto_started and self.pose is not None and focus.plate(self.found)[1] == focus.GOOD:
            self.auto_started = True
            logging.info("Calibration at the start: the sheet is in view, Capture by itself")
            self.start_capture()

    def make_steps(self):
        if self.steps is None:
            self.steps = StepsDialog("Calibration", STEP_ROWS, self, live=True)
            self.steps.cancel_requested.connect(self.cancel_capture)
            self.steps.restart_requested.connect(self.start_capture)
            self.steps.finished.connect(self.on_steps_closed)

    def save_calibration(self):
        """The calibration of the last good capture is written next to the profile; True if it was."""
        res = self.result_cal
        h = res["holdout"]
        target = profile.profile_path().parent / "calibration.json"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            res["cal"].save(str(target))
        except OSError as err:
            self.status.setText(f"The calibration could not be saved: {err}")
            self.status.setStyleSheet(f"color: {theme.ERR}")
            logging.error("Calibration: saving %s failed: %s", target, err)
            return False
        line = (f"Calibration: hold-out median {h['median']:.4f} p95 {h['p95']:.4f} max {h['max']:.4f} mm; by the nodes p95 "
                f"{res['by_nodes']['p95']:.4f} mm at {res['ppm']:.2f} px/mm; sheet {res['meta']['sheet']}, inner nodes "
                f"{res['meta']['inner_found']} of {res['meta']['inner_expected']}; {res['frames']} frames; mode "
                f"{self.mode_box.currentText().split('  ')[0]}; camera {self.profile.camera_text}; saved {target}")
        logging.info("%s", line)
        self.result = {"p95": h["p95"], "holdout": h, "by_nodes": res["by_nodes"], "warn": h["p95"] > WARN_P95_MM,
                       "file": str(target), "journal": line}
        return True

    # ----- what SheetScreen asks
    def can_finish(self):
        return self.result_cal is not None and self.worker is None and self.collect is None

    def on_mode_changed(self):
        self.result_cal, self.top = None, None                     # the model belongs to one mode
        self.norm_btn.setEnabled(False)
        self.show_result()

    def on_new_frame(self, bgr):
        if self.collect is not None:                               # Capture: every new frame is taken
            self.collect.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
            self.steps.value("frames", f"{len(self.collect)} of {self.frames_box.value()}")
            if len(self.collect) >= self.frames_box.value():
                self.start_calibration()
        if self.steps is not None and self.steps.isVisible():
            now = time.perf_counter()
            if self.empty_live and self.pose is not None and now - self._empty_at >= EMPTY_EVERY_S:
                self._empty_at = now
                self.check_empty_live(bgr)
            self.steps.show_frame(bgr, self.draw_problems)

    def finish(self):
        """The calibration of the last good capture is kept next to the profile; the hold-out p95 goes to the hub."""
        if self.save_calibration():
            self.accept()

    # ----- Capture: the steps dialog shows every step
    def start_capture(self):
        if self.last_bgr is None or self.collect is not None or self.worker is not None:
            return
        self.make_steps()
        self.steps.reset()
        self.steps.close_btn.setText("Close")
        self.problem_spots, self.problem_holes, self.empty_live, self.red = [], None, False, False
        self.steps.show()
        self.capture_btn.setEnabled(False)
        self.norm_btn.setEnabled(False)
        self.result_cal, self.top = None, None
        self.refresh_finish()
        self.show_result("Calibration: running ...")
        self.steps.start("sheet")
        text, kind = focus.plate(self.found)
        if kind != focus.GOOD or self.pose is None:              # the whole sheet must be in view before the frames are taken
            self.steps.fail("sheet", text)
            self.capture_btn.setEnabled(True)
            self.show_result(f"Calibration failed: {text}", theme.ERR)
            return
        self.steps.finish("sheet", text.replace("Focus sheet ", "").replace(": all", ","))
        self.steps.start("empty")
        spots = empty.foreign_spots(self.last_bgr, self.pose)
        if spots:                                                  # something lies on the sheet: red, checked live until Restart
            self.problem_spots = [o for o, _ in spots]
            self.steps.fail("empty", self.spots_text("something lies on the sheet", spots))
            self.empty_live = True
            self.capture_btn.setEnabled(True)
            self.show_result("Calibration failed: the sheet is not empty", theme.ERR)
            return
        self.steps.finish("empty", "nothing on it")
        self.steps.start("frames")
        self.search_paused = True                                  # the GIL: no marker search while the frames are taken
        self.collect, self.capture_t0 = [], time.perf_counter()
        logging.info("Calibration: capture of %d frames", self.frames_box.value())

    def spots_text(self, what, spots):
        size = self.frame_size or (1, 1)
        return f"{what}: " + ", ".join(f"{empty.where(o, size)} {a:.0f} cm²" for o, a in spots) + " (red on the picture)"

    def check_empty_live(self, bgr):
        """While "Sheet is empty" fails: the red spots follow what is still there; when nothing is left, say so."""
        spots = empty.foreign_spots(bgr, self.pose)
        self.problem_spots = [o for o, _ in spots]
        if spots:
            self.steps.reason("empty", self.spots_text("something lies on the sheet", spots))
        else:
            self.steps.reason("empty", "The sheet is empty now: press Restart", theme.OK)

    def draw_problems(self, out, to_view):
        """On the live picture of the steps dialog: what is wrong, in red (spots on the sheet, missing nodes)."""
        for outline in self.problem_spots:
            cv2.polylines(out, [np.int32([to_view(x, y) for x, y in outline])], True, PROBLEM_BGR, 3, cv2.LINE_AA)
        if self.problem_holes is not None:
            for x, y in self.problem_holes:
                sx, sy = to_view(x, y)
                cv2.line(out, (round(sx) - 4, round(sy) - 4), (round(sx) + 4, round(sy) + 4), PROBLEM_BGR, 2, cv2.LINE_AA)
                cv2.line(out, (round(sx) - 4, round(sy) + 4), (round(sx) + 4, round(sy) - 4), PROBLEM_BGR, 2, cv2.LINE_AA)

    def cancel_capture(self):
        if self.collect is not None:                               # the frames: stopped at once
            self.collect = None
            self.search_paused = False
            self.steps.stopped("frames")
        elif self.worker is not None:                              # the counting: stopped after the current step
            self.worker.cancel = True
        elif self.steps is not None:                               # nothing runs yet (at the start: waiting for the sheet)
            self.steps.reject()

    def on_steps_closed(self, _result):
        """The steps dialog closed (by itself, by Close, by Cancel): the screen is free again.
        At the start: where to go (a good or yellow capture: written, Measure; red: Commissioning; else the exit)."""
        self.empty_live = False
        if self.at_start:
            if self.result_cal is not None and self.collect is None and self.worker is None:
                self.start_outcome.emit("measure" if self.save_calibration() else "exit")
            elif self.red:
                self.start_outcome.emit("commissioning")
            else:                                                  # a running capture is stopped by done() of the start
                self.start_outcome.emit("exit")
            return
        if self.collect is None and self.worker is None:
            self.capture_btn.setEnabled(True)
            self.refresh_finish()
            if self.result_cal is None:
                self.show_result()

    def start_calibration(self):
        grays, self.collect = self.collect, None
        self.capture_s = time.perf_counter() - self.capture_t0
        moved = empty.moved_spots(grays[0], grays[-1], self.pose) if self.pose is not None else []
        if moved:                                                  # a hand or a shadow went through the capture
            self.search_paused = False
            self.problem_spots = [o for o, _ in moved]
            self.steps.fail("frames", self.spots_text("something moved during the capture", moved))
            self.capture_btn.setEnabled(True)
            self.show_result("Calibration failed: something moved", theme.ERR)
            return
        self.steps.finish("frames", f"{len(grays)} frames")
        self.worker = CalibrateThread(grays, self.last_bgr.copy(), self.sheets)
        self.worker.started_step.connect(self.steps.start)
        self.worker.finished_step.connect(lambda key, text, kind: self.steps.finish(key, text, kind))
        self.worker.failed.connect(self.on_failed_step)
        self.worker.done.connect(self.on_calibrated)
        self.worker.start()

    def end_worker(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.wait()
        self.search_paused = False
        self.capture_btn.setEnabled(True)
        return worker

    def on_failed_step(self, key, reason):
        worker = self.end_worker()
        if key == "":                                              # cancelled: the reason is the step that was running
            logging.info("Calibration: cancelled")
            self.steps.stopped(reason)
            self.show_result()
            return
        if key == "nodes" and worker is not None and worker.holes is not None:
            self.problem_holes = worker.holes
        logging.warning("Calibration failed at %s: %s", key, reason)
        self.steps.fail(key, reason)
        if self.at_start and key == "holdout":                     # red p95: the stand needs the commissioning
            self.red = True
            self.steps.close_btn.setText("Commissioning")
        self.show_result(f"Calibration failed: {reason}", theme.ERR)

    def on_calibrated(self, res, top):
        self.end_worker()
        res["capture_s"] = self.capture_s
        self.steps.start("saved")
        self.folder = (self.snap_folder or profile.profile_path().parent) / f"calib_{datetime.datetime.now():%Y%m%d_%H%M%S}"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(self.folder / "median.png"), res["median"])
            cv2.imwrite(str(self.folder / "color.png"), self.last_bgr)
            res["cal"].save(str(self.folder / "calibration.json"))
            keep = {k: v for k, v in res.items() if k not in ("cal", "median")}
            keep.update(mode=self.mode_box.currentText().split("  ")[0], camera=self.profile.camera_text)
            (self.folder / "result.json").write_text(json.dumps(keep, indent=1, default=str), encoding="utf-8")
            self.steps.finish("saved", self.folder.name)
        except OSError as err:
            logging.warning("Calibration: saving the capture failed: %s", err)
            self.steps.finish("saved", "NOT saved", "warn")
        self.result_cal, self.top = res, top
        h = res["holdout"]
        logging.info("Calibration: capture done, hold-out p95 %.4f mm -> %s", h["p95"], self.folder)
        self.norm_btn.setEnabled(True)
        self.show_result()
        self.refresh_finish()
        self.steps.end(f"Hold-out p95 {h['p95']:.3f} mm", "ok" if h["p95"] <= WARN_P95_MM else "warn")
        if self.at_start:
            self.steps.close_btn.setText("Continue")              # yellow: the operator goes on (green closes by itself)

    def show_result(self, text=None, colour=GREY):
        """The plate of the last capture: the figures and the traffic light of the hold-out p95."""
        r = self.result_cal
        if text is None and r is not None:
            h, b, m = r["holdout"], r["by_nodes"], r["meta"]
            colour = theme.OK if h["p95"] <= WARN_P95_MM else theme.WARN
            text = (f"Sheet {m['sheet']}, inner nodes {m['inner_found']} of {m['inner_expected']}\n"
                    f"camera       {r['camera_ppm']:.2f} px/mm\n"
                    f"hold-out     median {h['median']:.3f}  p95 {h['p95']:.3f} mm\n"
                    f"by the nodes median {b['median']:.3f}  p95 {b['p95']:.3f} mm")
        elif text is None:
            text = "Calibration: not done"
        self.result_plate.setText(text)
        self.result_plate.setStyleSheet(f"background: {colour}; color: white; font-family: {theme.MONO}; font-size: 12px; "
                                        f"border-radius: 8px; padding: 8px")

    def show_normalised(self):
        if self.top is None:
            return
        window = NormalisedWindow(self.top, self.result_cal["ppm"], self.result_cal["cal"], self.folder)
        window.setWindowModality(Qt.ApplicationModal)
        self.norm_windows.append(window)
        window.show()

    def done(self, result):
        if self.worker is not None:
            self.worker.cancel = True
            self.worker.wait(30000)
        for window in self.norm_windows:
            window.close()
        super().done(result)
