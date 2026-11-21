"""Measure: the piece on the work sheet -> its outline in mm -> DXF (docs/DECISIONS.md 2026-11-19 "Measure").

The main window of the program (DECISIONS.md 2026-11-20): opened by the start when every check passed, or by Go to measurement
of the Camera preview; one window with the three window buttons, maximized. The gear in the title row (Setup) asks for the
Camera preview (the setup); closing the window ends the program ("Exit without saving?" when the last result is not saved).
Capture is shown in three phases (DECISIONS.md 2026-11-20 "Capture animation"): the badge fills violet frame by frame
(CAPTURING n / 15); a light band sweeps down while the outline is counted (MEASURING); the outline draws itself clockwise
from its top left point, the nodes come, the area comes (RESULT).
Two positions (DECISIONS.md 2026-11-20 "Measure: two positions"), told by the badge at the bottom middle of the picture:
    LIVE    the live video (button Capture). The switch Normalised in the title row (on by default): the video redrawn as the
            sheet lies, from above (marker 5 top left), PPM px/mm, as the result; off: the camera picture with the markers;
    RESULT  the straight picture of the last Capture with the outline, its nodes and the area (buttons Capture and Save;
            Save writes and returns to LIVE). The live video goes on in a small picture at the top left: the operator
            changes the piece, sees it there and presses Capture again (the result not saved is dropped); a click on the
            small picture returns to LIVE without writing (DECISIONS.md 2026-11-21).
Capture takes FRAMES frames, their median gives the outline (way A, patterndxf/measure.py). Save opens Save piece (the
name in the work folder, patterndxf/ui/save_piece.py) and writes <work folder>/<name>.dxf (Standard DXF for AccuMark, mm,
as the piece lies), then the log folder:
    <log folder>/measurements/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/  color.png (the snapshot), median.png, straight.png (with the
                                             outline), piece.dxf (the same DXF), result.json (piece_name, dxf_path, ...)
"""
import datetime
import json
import logging
import os
import time
import traceback
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QElapsedTimer, QEvent, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from patterndxf import measure, report, startup
from patterndxf.calib import nodecal
from patterndxf.core import dxf_out, simplify
from patterndxf.sheet import empty, focus
from patterndxf.ui import theme
from patterndxf.ui.sheet_screen import AXES_BGR, NO_CAMERA, SheetScreen, hex_bgr
from patterndxf.ui import work_folder
from patterndxf.ui.widgets import Badge, ScanOverlay, Snackbar, Switch, card
from patterndxf.ui.save_piece import SavePieceDialog
from patterndxf.ui.work_folder import WorkFolderDialog

FRAMES = 15                                     # as the calibration: the median of 15 frames
PPM = 5.0                                       # the straight picture (live and result): px per mm; the live redraw takes ~8 ms
OUTLINE_BGR, NODE_BGR = AXES_BGR, (60, 60, 255)   # sky blue outline, red nodes
# Simplify: the mode straight with three sliders (DECISIONS.md 2026-11-20): settings key, title, low, high, step, format, tip
SLIDERS = (("simplify_tolerance_mm", "Tolerance", 0.05, 0.5, 0.01, "{:.2f} mm",
            "How far the DXF may leave the outline: more = fewer nodes"),
           ("simplify_corner_deg", "Corner", 10.0, 60.0, 1.0, "{:.0f}°",
            "A turn above this is a corner: less = more corners"),
           ("simplify_straight_mm", "Straight", 0.1, 1.0, 0.01, "{:.2f} mm",
            "An edge this close to a line is one straight segment: more = more straight edges"))
DEFAULT_MODE = "straight"                     # 2026-11-20: 10 nodes on the star instead of 54-103 (docs/ACCURACY.md)
BLINK_MS = 600                                  # the red point of the LIVE badge
# the Piece card before a result: the same table with dashes, so the card keeps its height (DECISIONS.md 2026-11-20)
# the Piece card: big figures (DECISIONS.md 2026-11-20); before a result dashes, so the card keeps its height
FIGURES = (("width", "width_mm", "{:.1f}", "mm"), ("height", "height_mm", "{:.1f}", "mm"),
           ("area", "area_cm2", "{:.1f}", "cm²"), ("nodes", "nodes", "{:d}", ""))
LOW_FPS = 5.0                                   # below this the frame line on the picture is orange
OUTLINE_S, NODES_S, ANIM_MS = 0.8, 0.4, 40      # Capture, phase 3: the outline draws itself, then the nodes come, then the size
SAVED_ROWS = 10                                 # Saved pieces: rows seen at once (more: scroll)
INSET_PART = 0.16                             # RESULT: the small live picture, this part of the width of the picture


def clockwise_from_top_left(pts):
    """The order of drawing on the screen (y down): clockwise, from the leftmost of the topmost points (only the look)."""
    x, y = pts[:, 0], pts[:, 1]
    if np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) < 0:     # anticlockwise on the screen: turn round
        pts = pts[::-1]
    top = np.flatnonzero(pts[:, 1] <= pts[:, 1].min() + 2)
    return np.roll(pts, -top[np.argmin(pts[top, 0])], axis=0)
DOT_DIM = "#5a2030"                             # the LIVE point between the blinks: dim, not gone (the plate does not jump)
LAYERS = (("outline", "Outline", False, "The measured edge of the piece (sky blue)"),
          ("nodes", "Nodes", True, "The nodes that go into piece.dxf (red points)"),
          ("dxf", "DXF", True, "The nodes joined by straight lines: what goes into piece.dxf (red lines)"),
          ("area", "Area", True, "The area of the piece, in the middle of its size"))
FIGURE_LAYERS = {"area_cm2": "area", "nodes": "nodes"}   # a figure of the Piece card -> its layer: the switch in its row (1.15.0)
AREA_SCALE = 1.2                                # the area figure on the piece: this times the base scale of the drawing
AREA_BGR = hex_bgr(theme.BG)                    # dark: it is read on the white paper (DECISIONS.md 2026-11-21)
# each on its own switch; the third value is the state at every start (not remembered, DECISIONS.md 2026-11-20)


class LiveInset(QLabel):
    """RESULT: the live video, small, at the top left of the picture; a click asks for LIVE (DECISIONS.md 2026-11-21)."""
    clicked = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Back to the live video")
        self.hover(False)
        self.hide()

    def hover(self, on):
        self.setStyleSheet(f"background: {theme.DARK}; border: 2px solid {theme.PRIMARY if on else theme.TEXT2}; border-radius: 4px")

    def show_frame(self, bgr, width):
        h, w = bgr.shape[:2]
        k = width / w
        small = np.ascontiguousarray(cv2.cvtColor(cv2.resize(bgr, (max(int(w * k), 1), max(int(h * k), 1)),
                                                             interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB))
        self.setPixmap(QPixmap.fromImage(QImage(small.data, small.shape[1], small.shape[0], 3 * small.shape[1],
                                                QImage.Format_RGB888).copy()))
        self.setFixedSize(small.shape[1] + 4, small.shape[0] + 4)

    def enterEvent(self, event):
        self.hover(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover(False)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class TablesThread(QThread):
    """The tables of the straight picture (~2 s), once per screen, away from the window thread."""
    ready = Signal(object)                          # (mx, my) or the error text

    def __init__(self, cal):
        super().__init__()
        self.cal = cal

    def run(self):
        try:
            self.ready.emit(self.cal.remap_tables(PPM))
        except Exception as err:
            traceback.print_exc()
            self.ready.emit(f"{type(err).__name__}: {err}")


class MeasureThread(QThread):
    """median -> outline in mm -> the straight picture, away from the window thread."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, grays, color, cal, tables):
        super().__init__()
        self.grays, self.color, self.cal, self.tables = grays, color, cal, tables

    def run(self):
        try:
            t = time.perf_counter()
            median = np.median(np.stack(self.grays), axis=0).astype(np.uint8)
            mm, reason = measure.outline_mm(median, self.cal)
            if mm is None:
                self.failed.emit(reason)
                return
            seconds = time.perf_counter() - t
            top = cv2.rotate(self.cal.redraw(self.color, PPM, self.tables), cv2.ROTATE_90_COUNTERCLOCKWISE)
            self.done.emit({"outline_mm": mm, "median": median, "color": self.color, "top": top, "seconds": seconds,
                            "frames": len(self.grays)})
        except Exception as err:                        # a plain line, never a silent death of the thread
            traceback.print_exc()
            self.failed.emit(f"{type(err).__name__}: {err}")


class MeasureScreen(SheetScreen):
    TITLE = "Measure"
    HINT = "Put the piece on the sheet, keep the markers clear, then press Capture."
    setup_requested = Signal()                          # the gear: the Camera preview (the setup) instead of this window

    def __init__(self, profile_, snap_folder=None, result_folder=None, parent=None):
        super().__init__(profile_, snap_folder, parent)
        self.setModal(False)                             # the main window of the work, not a dialog over another one
        self.setWindowFlags(Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.result_folder = Path(result_folder) if result_folder else measure.calibration_path().parent / "measurements"
        self.cal, self.cal_problem, self.tables = None, "", None
        self.cal_path = measure.calibration_path()
        try:
            self.cal = nodecal.Calibration.load(str(self.cal_path))
        except Exception as err:
            self.cal_problem = f"No calibration ({self.cal_path}): {type(err).__name__}: {err}"
        self.collect, self.worker, self.res, self.nodes_mm, self.figures_now = None, None, None, None, None
        self.view = "live"                               # "live" or "result"
        self.saved, self.leaving = True, False           # the last result is saved; the window closes for the setup

        self.cancel_btn.hide()                           # the main window: closing it ends the program
        self.finish_btn.hide()                           # not a step of the commissioning: nothing goes to a hub
        self.setup_btn = QPushButton()
        self.setup_btn.setIcon(QIcon(str(theme.ICON_PATH.parent / "gear.svg")))
        self.setup_btn.setIconSize(QSize(32, 32))
        self.setup_btn.setFixedSize(48, 48)
        self.setup_btn.setFlat(True)
        self.setup_btn.setToolTip("Setup")
        self.setup_btn.clicked.connect(self.go_setup)
        norm_label = QLabel("Normalised")
        norm_label.setStyleSheet(f"color: {theme.TEXT}; font-size: 15px")
        self.norm_switch = Switch(True)
        self.norm_switch.setToolTip("On: the sheet from above as it lies. Off: the camera picture with the markers")
        self.norm_switch.toggled.connect(self.on_norm_switch)
        self.header.addWidget(norm_label)
        self.header.addWidget(self.norm_switch)
        self.header.addSpacing(20)
        self.header.addWidget(self.setup_btn)
        self.badge = Badge(self.picture)                 # LIVE / RESULT at the bottom middle of the picture, one size
        self.picture.margins = (14 + 40 + 8, 14 + self.badge.height() + 8)   # the icons above, the badge and lines below: off the sheet
        self.picture.installEventFilter(self)            # the badge follows the size of the picture
        self.scan = ScanOverlay(self.picture)            # phase 2 of Capture
        self.snackbar = Snackbar(self.picture, avoid=self.badge)   # the messages, bottom left of the picture
        self.inset = LiveInset(self.picture)             # RESULT: the live video, small, top left; a click: LIVE
        self.inset.clicked.connect(self.back_to_live)
        self.res_card.hide()                             # the resolution is the one of the calibration (DECISIONS.md 2026-11-20)
        self.cam_card.hide()                             # frame and fps: a line on the picture, bottom right
        self.frame_line = QLabel(self.picture)
        self.frame_line.hide()
        self.markers_card.hide()                         # the markers: a line on the picture, bottom left (DECISIONS.md 2026-11-20)
        self.plate.hide()                                # the plate and the sheet centre: for the commissioning; why Capture
        self.place_line.hide()                           # cannot measure is said by the Snackbar
        self.markers_line = QLabel(self.picture)
        self.markers_line.hide()
        self.snap_btn.hide()                             # Snap and Fit are icons on the picture (DECISIONS.md 2026-11-20)
        self.fit_btn.hide()
        self.snap_icon = self.picture_icon("camera.svg", "Save the frame (Snap)", self.snap)
        self.home_icon = self.picture_icon("home.svg", "Fit the picture", self.picture.fit)
        self.phase = None                                # None, "capture", "measure", "draw" (the phases of Capture)
        self.busy_on = False                             # the pointer "Working in background" is on
        self.outline_pic = self.nodes_pic = self.outline_len = None   # the result on the straight picture, in drawing order
        self.anim_clock = QElapsedTimer()
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.on_anim)
        self.blink_on = True
        self.blink = QTimer(self)
        self.blink.timeout.connect(self.on_blink)
        self.blink.start(BLINK_MS)
        settings, _note = startup.load_settings()
        self.simplify_saved = {key: float(settings[key]) for key, *_rest in SLIDERS}
        self.sliders, self.slider_values = {}, {}
        simplify_box = QWidget()
        sgrid = QGridLayout(simplify_box)
        sgrid.setContentsMargins(0, 0, 0, 0)
        sgrid.setHorizontalSpacing(10)
        for row, (key, title, low, high, step, fmt, tip) in enumerate(SLIDERS):
            name, sl, value = QLabel(title), QSlider(Qt.Horizontal), QLabel("")
            sl.setRange(0, round((high - low) / step))
            sl.setToolTip(tip)
            name.setToolTip(tip)
            value.setStyleSheet(f"font-family: {theme.MONO}")
            value.setMinimumWidth(70)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            sgrid.addWidget(name, row, 0)
            sgrid.addWidget(sl, row, 1)
            sgrid.addWidget(value, row, 2)
            self.sliders[key], self.slider_values[key] = sl, value
            sl.valueChanged.connect(self.on_slider)
        sgrid.setColumnStretch(1, 1)
        self.reset_btn, self.default_btn, self.save_simplify_btn = QPushButton("Reset"), QPushButton("Default"), QPushButton("Save")
        self.reset_btn.setToolTip("Back to the saved values")
        self.default_btn.setToolTip("Back to the values of the program")
        self.save_simplify_btn.setToolTip("Keep these values for the next start (settings.json)")
        brow = QHBoxLayout()
        brow.setContentsMargins(0, 6, 0, 0)
        brow.setSpacing(8)
        for b in (self.reset_btn, self.default_btn, self.save_simplify_btn):
            b.setStyleSheet("font-size: 13px; padding: 4px 12px")
            brow.addWidget(b)
        brow.addStretch(1)
        sgrid.addLayout(brow, len(SLIDERS), 0, 1, 3)
        self.reset_btn.clicked.connect(lambda: self.set_simplify(self.simplify_saved))
        self.default_btn.clicked.connect(lambda: self.set_simplify({k: startup.DEFAULT_SETTINGS[k] for k, *_r in SLIDERS}))
        self.save_simplify_btn.clicked.connect(self.save_simplify)
        self.set_simplify(self.simplify_saved)
        self.layer_switches = {}
        for key, _text, on, tip in LAYERS:               # each layer on and off by itself
            sw = Switch(on)
            sw.setToolTip(tip)
            sw.toggled.connect(self.redraw_result)
            self.layer_switches[key] = sw
        self.figures = QWidget()
        grid = QGridLayout(self.figures)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(2)
        self.figure_values = {}
        big = QFont(theme.MONO.split(",")[0])
        big.setPixelSize(24)
        big_w = QFontMetrics(big).horizontalAdvance("0000.0") + 6
        for row, (name, key, _fmt, unit) in enumerate(FIGURES):
            label, value, units = QLabel(name), QLabel("–"), QLabel(unit)
            label.setStyleSheet(f"color: {theme.TEXT2}; font-size: 14px")
            value.setStyleSheet(f"color: {theme.KEY}; font-family: {theme.MONO}; font-size: 24px")
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setMinimumWidth(big_w)                 # dashes or figures: one width, the units do not move
            units.setStyleSheet(f"color: {theme.TEXT2}; font-size: 14px")
            grid.addWidget(label, row, 0)
            grid.addWidget(value, row, 1)
            grid.addWidget(units, row, 2)
            if key in FIGURE_LAYERS:                     # the switch of the figure on the picture, on the right of its row
                grid.addWidget(self.layer_switches[FIGURE_LAYERS[key]], row, 4, Qt.AlignVCenter)
            self.figure_values[key] = value
        grid.setColumnStretch(3, 1)
        draws = QHBoxLayout()                            # the layers that are not a figure: one row, in the middle
        draws.addStretch(1)
        for key, text, _on, tip in LAYERS:
            if key in FIGURE_LAYERS.values():
                continue
            label = QLabel(text)
            label.setToolTip(tip)
            draws.addWidget(self.layer_switches[key])
            draws.addWidget(label)
            draws.addSpacing(36)
        draws.takeAt(draws.count() - 1)                  # no gap after the last one
        draws.addStretch(1)
        draws_box = QWidget()
        draws_box.setLayout(draws)
        draws.setContentsMargins(0, 0, 0, 0)
        self.info_area.addWidget(card("Layers", draws_box))           # what is shown on the picture (not the file)
        self.info_area.addWidget(card("Piece", self.figures))         # the result of the measuring
        self.info_area.addWidget(card("Simplify", simplify_box))   # how the nodes of the DXF are made (DECISIONS.md 2026-11-20)
        self.dxf_dir, self.work_rel = settings["dxf_dir"], settings.get("work_folder", "")   # Work folder (DECISIONS.md 2026-11-21)
        if work_folder.resolve(self.dxf_dir, self.work_rel) is None:
            self.work_rel = ""                           # gone, or not inside DXF: the line shows DXF
        self.work_line = QLabel()
        self.work_line.setStyleSheet(f"color: {theme.KEY}; font-size: 15px")
        self.work_line.setMinimumWidth(60)
        self.work_btn = self.tile_icon("folder.svg", work_folder.RULES, self.choose_work_folder)
        work_row = QWidget()
        wrow = QHBoxLayout(work_row)
        wrow.setContentsMargins(0, 0, 0, 0)
        wrow.addWidget(self.work_line, 1)
        wrow.addWidget(self.work_btn)
        for kind, svg in (("pdf", "pdf.svg"), ("xlsx", "xls.svg")):   # the reports of the work folder (DECISIONS.md 2026-11-21)
            wrow.addWidget(self.tile_icon(svg, f"Report of the work folder ({'PDF' if kind == 'pdf' else 'Excel'})",
                                          lambda _c=False, k=kind: self.report_folder(k)))
        self.info_area.addWidget(card("Work folder", work_row))      # under Simplify (1.16.1)
        self.saved_pieces = []                           # Saved pieces: written in this session, the last first (1.18.0)
        self.saved_table = QTableWidget(0, 4)               # name, area, time, × (remove from the list)
        self.saved_table.horizontalHeader().hide()
        self.saved_table.verticalHeader().hide()
        self.saved_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.saved_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.saved_table.setFocusPolicy(Qt.NoFocus)
        self.saved_table.setShowGrid(False)
        self.saved_table.setStyleSheet(f"QTableWidget {{ background: transparent; border: none; font-family: {theme.MONO}; }}")
        self.saved_table.verticalHeader().setDefaultSectionSize(24)
        self.saved_table.setFixedHeight(24 * SAVED_ROWS + 4)
        self.saved_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.saved_total = QLabel("")
        self.saved_total.setStyleSheet(f"color: {theme.TEXT2}")
        saved_box = QWidget()
        sbox = QVBoxLayout(saved_box)
        sbox.setContentsMargins(0, 0, 0, 0)
        sbox.setSpacing(4)
        sbox.addWidget(self.saved_table)
        total_row = QHBoxLayout()
        total_row.setContentsMargins(0, 0, 0, 0)
        total_row.addWidget(self.saved_total, 1)
        for kind, svg in (("pdf", "pdf.svg"), ("xlsx", "xls.svg")):   # the reports of the saved pieces
            total_row.addWidget(self.tile_icon(svg, f"Report of the saved pieces ({'PDF' if kind == 'pdf' else 'Excel'})",
                                               lambda _c=False, k=kind: self.report_saved(k)))
        sbox.addLayout(total_row)
        self.info_area.addWidget(card("Saved pieces", saved_box))
        self.show_saved()
        self.capture_btn = QPushButton("Capture")
        self.capture_btn.setObjectName("primary")
        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        for b in (self.capture_btn, self.save_btn):
            b.setMinimumHeight(52)
        row = QHBoxLayout()
        row.addWidget(self.capture_btn, 1)
        row.addWidget(self.save_btn, 1)
        self.actions.addLayout(row)
        self.capture_btn.clicked.connect(self.start_capture)
        self.save_btn.clicked.connect(self.save_result)
        self.capture_btn.setEnabled(False)               # until the tables of the straight picture are made
        self.show_position()
        self.tables_thread = None
        if self.cal_problem:
            self.say(self.cal_problem, theme.ERR)
        else:
            self.say("Preparing the normalised view ...")
            self.tables_thread = TablesThread(self.cal)
            self.tables_thread.ready.connect(self.on_tables)
            self.tables_thread.start()
        self.begin()

    # ----- Work folder: where the pieces go (DECISIONS.md 2026-11-21)
    def show_work_folder(self):
        full = work_folder.resolve(self.dxf_dir, self.work_rel)
        self.work_line.setText(work_folder.display(self.work_rel, self.work_line.fontMetrics(), max(self.work_line.width(), 60)))
        self.work_line.setToolTip(str(full) if full else f"{self.dxf_dir}\nNo work folder yet: choose one with the folder icon")
        self.work_line.setStyleSheet(f"color: {theme.KEY if full else theme.TEXT2}; font-size: 15px")

    def choose_work_folder(self):
        dialog = WorkFolderDialog(self.dxf_dir, work_folder.resolve(self.dxf_dir, self.work_rel), self)
        if dialog.exec() != QDialog.Accepted or dialog.chosen is None:
            return
        rel = work_folder.relative(self.dxf_dir, dialog.chosen)
        if rel is None:
            return
        settings, _note = startup.load_settings()
        settings["work_folder"] = rel
        try:
            startup.save_settings(settings)
        except OSError as err:
            self.snackbar.show_message("error", f"Settings not saved: {err}")
        self.work_rel = rel
        self.show_work_folder()
        logging.info("Measure: work folder %s", rel)

    # ----- Saved pieces: what this session wrote, the last first (DECISIONS.md 2026-11-21)
    def add_saved(self, path, area_cm2):
        """A replaced piece moves to the top, it is not listed twice."""
        self.saved_pieces = [x for x in self.saved_pieces if x[3] != path]
        self.saved_pieces.insert(0, (path.stem, area_cm2, datetime.datetime.now(), path))
        self.show_saved()

    # ----- the reports: PDF and Excel of the work folder or of the saved pieces (patterndxf/report.py)
    def tile_icon(self, svg, tip, action):
        """A flat 32 px icon button of a tile (the folder, PDF, XLS)."""
        b = QPushButton()
        b.setIcon(QIcon(str(theme.ICON_PATH.parent / svg)))
        b.setIconSize(QSize(32, 32))
        b.setFixedSize(42, 42)
        b.setFlat(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tip)
        b.clicked.connect(action)
        return b

    def no_work_folder(self):
        """The work folder is not set, or it is set but gone (deleted or renamed in Explorer)."""
        text = f"Work folder {Path(self.work_rel).name} not found: choose another" if self.work_rel else "Choose the work folder"
        self.snackbar.show_message("warning", text)

    def report_folder(self, kind):
        folder = work_folder.resolve(self.dxf_dir, self.work_rel)
        if folder is None:
            self.no_work_folder()
            return
        self.make_report(kind, report.folder_files(folder), folder.name)

    def report_saved(self, kind):
        files = [x[3] for x in reversed(self.saved_pieces) if x[3].exists()]    # in the order of saving
        self.make_report(kind, files, f"saved_pieces_{datetime.datetime.now():%Y%m%d}")

    def make_report(self, kind, files, title):
        """The report goes to <log folder>/reports/ and opens (the DXF folders keep only DXF)."""
        if not files:
            self.snackbar.show_message("warning", "No pieces for the report")
            return
        QApplication.setOverrideCursor(Qt.BusyCursor)
        try:
            path = report.make(kind, files, title, self.result_folder.parent / "reports")
        except Exception as err:                         # a plain line, never a crash of the window
            self.snackbar.show_message("error", f"Report not made: {type(err).__name__}: {err}")
            logging.exception("Measure: report %s", kind)
            return
        finally:
            QApplication.restoreOverrideCursor()
        logging.info("Measure: report %s", path)
        self.snackbar.show_message("success", f"Report {path.name}")
        os.startfile(str(path))

    def remove_saved(self, path):
        self.saved_pieces = [x for x in self.saved_pieces if x[3] != path]
        self.show_saved()

    def show_saved(self):
        t = self.saved_table
        t.setRowCount(len(self.saved_pieces))
        for row, (name, area, when, path) in enumerate(self.saved_pieces):
            area_item = QTableWidgetItem(f"{area:.1f}")
            area_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            time_item = QTableWidgetItem(f"{when:%H:%M}")
            time_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            for col, item in enumerate((QTableWidgetItem(name), area_item, time_item)):
                item.setToolTip(str(path))
                t.setItem(row, col, item)
            x = QPushButton("×")                          # the row leaves the list; the file and the log stay (1.19.1)
            x.setFlat(True)
            x.setCursor(Qt.PointingHandCursor)
            x.setToolTip("Remove from the list (the file stays)")
            x.setStyleSheet(f"QPushButton {{ color: {theme.TEXT2}; font-size: 16px; border: none; padding: 0 4px; }}"
                            f"QPushButton:hover {{ color: {theme.ERR}; }}")
            x.clicked.connect(lambda _c=False, pth=path: self.remove_saved(pth))
            t.setCellWidget(row, 3, x)
        t.resizeColumnToContents(1)
        t.resizeColumnToContents(2)
        t.setColumnWidth(2, t.columnWidth(2) + 18)       # room between the area and the time
        t.setColumnWidth(3, 28)
        n = len(self.saved_pieces)
        self.saved_total.setText(f"{n} piece{'' if n == 1 else 's'} · {sum(a for _n, a, _w, _p in self.saved_pieces):.1f} cm²"
                                 if n else "Nothing saved yet")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "work_line"):
            self.show_work_folder()                      # the line is cut to its width

    def say(self, text, colour=None):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour or theme.TEXT2}")

    def on_tables(self, tables):
        if isinstance(tables, str):
            self.say(f"The normalised view could not be made: {tables}", theme.ERR)
            return
        self.tables = tables
        self.capture_btn.setEnabled(self.collect is None and self.worker is None)
        self.say("")

    # ----- the two positions: LIVE and RESULT
    def set_view(self, key):
        self.view = key
        self.picture.zoom, self.picture.centre = 0.0, None          # another picture: fit
        if key == "result":
            self.show_result()
        elif self.last_bgr is not None:
            self.present(self.last_bgr)
        self.show_position()

    def show_position(self):
        """The badge and the buttons of the position."""
        live = self.view == "live"
        self.save_btn.setVisible(not live)               # Capture is there in both positions (DECISIONS.md 2026-11-21)
        if live:
            self.inset.hide()
        elif self.last_bgr is not None:
            self.show_inset(self.last_bgr)
        self.norm_switch.setEnabled(live)
        self.show_frame_line()
        for sw in self.layer_switches.values():          # the layers work on the result only
            sw.setEnabled(not live)
        self.paint_badge()

    def paint_badge(self):
        if self.phase == "capture":
            n = len(self.collect or [])
            self.badge.show_state(f"CAPTURING  {n} / {FRAMES}", theme.KEY, fill=n / FRAMES, ticks=FRAMES)
        elif self.phase in ("measure", "draw"):
            self.badge.show_state("MEASURING", theme.KEY)
        elif self.view == "live" and self.cam_lost:
            self.badge.show_state("NO CAMERA", theme.ERR)
        elif self.view == "live":
            self.badge.show_state("LIVE", theme.KEY, theme.ERR if self.blink_on else DOT_DIM)
        else:
            self.badge.show_state("RESULT", theme.PRIMARY)
        self.place_badge()
        self.badge.raise_()

    def picture_icon(self, svg, tip, action):
        """A small icon button on the top right of the picture, on a dark plate."""
        b = QPushButton(self.picture)
        b.setIcon(QIcon(str(theme.ICON_PATH.parent / svg)))
        b.setIconSize(QSize(28, 28))
        b.setFixedSize(40, 40)
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("QPushButton { background: rgba(21, 21, 33, 200); border: none; border-radius: 6px; padding: 0; }"
                        "QPushButton:hover { background: rgba(60, 60, 85, 220); }"
                        "QPushButton:pressed { background: rgba(126, 111, 255, 120); }")
        b.clicked.connect(action)
        return b

    def place_tools(self):
        x = self.picture.width() - 14 - 40
        self.home_icon.move(x, 14)
        self.snap_icon.move(x - 48, 14)

    def snap_prefix(self):
        return "live" if self.view == "live" else "result"

    def snap_image(self):
        """LIVE: the camera frame as it is; RESULT: the whole straight picture with the layers that are on."""
        if self.view == "live":
            return self.last_bgr
        if self.res is None:
            return None
        self.stop_drawing()
        return self.result_picture()

    def result_picture(self):
        """The straight picture at full size with the layers that are on (straight.png, the result_ snap)."""
        straight = self.res["top"].copy()
        self.draw_result(straight, lambda x, y: (x, y), 1.0)
        return straight

    def snapped(self, ok, text):
        self.snackbar.show_message("success" if ok else "error", text)

    def refresh_info(self):
        super().refresh_info()
        if hasattr(self, "frame_line"):              # SheetScreen.__init__ calls this before Measure has made it
            self.show_frame_line()
            self.show_markers_line()

    def show_frame_line(self):
        """LIVE: '4656x3496 · 9.1 fps' at the bottom right of the picture; orange when the frame is not the size of the
        calibration or the camera gives fewer than LOW_FPS frames a second."""
        if self.view != "live" or self.frame_size is None:
            self.frame_line.hide()
            return
        w, h = self.frame_size
        bad = (self.cal is not None and self.frame_size != tuple(self.cal.size)) or 0 < self.fps < LOW_FPS or self.cam_lost
        self.frame_line.setText(f"{w}x{h} · {self.fps:.1f} fps")
        self.frame_line.setToolTip(f"The calibration is for {self.cal.size[0]}x{self.cal.size[1]}" if self.cal is not None else "")
        self.paint_line(self.frame_line, bad)
        self.place_lines()
        self.frame_line.show()

    def show_markers_line(self):
        """LIVE and RESULT: '50x70 · markers 6/6 · 576 ms · 85 px' at the bottom left of the picture (the Snackbar lies over it);
        orange when not all the markers of the sheet are found."""
        found = self.found or {}
        ids = dict(focus.WORK_SHEETS).get(self.cal.sheet.name) if self.cal is not None else None
        if ids is None:                                  # no calibration: the work sheet with the most markers in view
            hits = [s_ids for _name, s_ids in focus.WORK_SHEETS if any(i in found for i in s_ids)]
            ids = max(hits, key=lambda s_ids: sum(i in found for i in s_ids)) if hits else []
        got = sum(i in found for i in ids)
        sides = [float(np.linalg.norm(c[(k + 1) % 4] - c[k])) for c in found.values() for k in range(4)]
        side = f"{np.mean(sides):.0f} px" if sides else "- px"
        name = self.cal.sheet.name if self.cal is not None else next(
            (n for n, s_ids in focus.WORK_SHEETS if s_ids is ids), "?")
        self.markers_line.setText(f"{name} · markers {got}/{len(ids)} · {self.search_ms:.0f} ms · {side}")
        missing = [str(i) for i in ids if i not in found]
        self.markers_line.setToolTip(f"Not found: {', '.join(missing)}" if missing else "")
        self.paint_line(self.markers_line, not ids or got < len(ids))
        self.place_lines()
        self.markers_line.show()

    @staticmethod
    def paint_line(label, bad):
        label.setStyleSheet(f"background: rgba(21, 21, 33, 215); color: {theme.WARN if bad else theme.TEXT};"
                            f" border-radius: 6px; padding: 4px 10px; font-size: 12px; font-family: {theme.MONO}")
        label.adjustSize()

    def place_lines(self):
        """The frame line bottom right, the markers line bottom left, both on the line of the badge."""
        for label, right in ((getattr(self, "frame_line", None), True), (getattr(self, "markers_line", None), False)):
            if label is None:
                continue
            y = self.picture.height() - 14 - (self.badge.height() + label.height()) // 2
            label.move(self.picture.width() - 14 - label.width() if right else 14, y)

    def place_badge(self):
        self.badge.move((self.picture.width() - self.badge.width()) // 2, self.picture.height() - self.badge.height() - 14)

    def eventFilter(self, obj, event):
        if obj is self.picture and event.type() == QEvent.Resize:
            self.place_badge()
            self.place_tools()
            self.place_lines()
            self.scan.setGeometry(self.picture.rect())
        return super().eventFilter(obj, event)

    def on_blink(self):
        self.blink_on = not self.blink_on
        if self.view == "live" and self.phase is None:
            self.paint_badge()

    def on_norm_switch(self, _on):
        self.picture.zoom, self.picture.centre = 0.0, None          # another picture: fit
        if self.view == "live" and self.last_bgr is not None:
            self.present(self.last_bgr)

    def back_to_live(self):
        """The small picture (or Capture in RESULT): the result is dropped (nothing written), the live video again."""
        self.stop_drawing()
        self.res, self.nodes_mm, self.figures_now, self.saved = None, None, None, True
        self.show_figures(None)
        self.say("")
        self.set_view("live")

    def normalised_ok(self):
        return self.tables is not None and self.frame_size == tuple(self.cal.size)

    def live_top(self, bgr):
        """The frame redrawn as the sheet lies (the straight picture of the live video)."""
        return cv2.rotate(cv2.remap(bgr, *self.tables, cv2.INTER_LINEAR), cv2.ROTATE_90_COUNTERCLOCKWISE)

    def present(self, bgr):
        if self.view == "result":
            self.show_inset(bgr)                         # the result stays; the live video goes into the small picture
            return
        if self.norm_switch.isChecked() and self.normalised_ok():
            self.picture.set_frame(self.live_top(bgr))
        else:
            super().present(bgr)

    def show_inset(self, bgr):
        """RESULT: the live frame in the small picture at the top left; normalised when it can be, else as the camera gives it."""
        self.inset.show_frame(self.live_top(bgr) if self.normalised_ok() else bgr, max(int(self.picture.width() * INSET_PART), 80))
        self.inset.move(14, 14)
        self.inset.show()
        self.inset.raise_()

    def on_mode_changed(self):
        if self.cal is not None:
            self.say("The calibration is for one resolution: Normalised and Capture need "
                     f"{self.cal.size[0]}x{self.cal.size[1]}", theme.WARN)

    def on_camera_lost(self):
        """No frames: a capture in progress is stopped (it would wait for ever), the operator is told until the camera is back."""
        if self.collect is not None:
            self.collect = None
            self.end_capture()
            logging.warning("Measure: capture stopped, the camera is lost")
        self.lost_snack = self.snackbar.show_message("error", NO_CAMERA)
        self.paint_badge()

    def on_camera_back(self):
        snack, self.lost_snack = getattr(self, "lost_snack", None), None
        if snack is not None:
            self.snackbar.remove(snack)
        self.snackbar.show_message("success", "The camera is back")
        self.paint_badge()

    # ----- Capture
    def start_capture(self):
        if self.cam_lost:                                # the red Snackbar of the lost camera is already there
            return
        if self.cal is None or self.tables is None or self.last_bgr is None or self.collect is not None or self.worker is not None:
            return
        if self.view == "result":                        # Capture in RESULT: the result not saved is dropped, the capture
            self.back_to_live()                          # goes on the live video (DECISIONS.md 2026-11-21)
        if self.frame_size != tuple(self.cal.size):     # why it cannot measure: an orange Snackbar (DECISIONS.md 2026-11-20)
            self.snackbar.show_message("warning", f"The camera gives {self.frame_size[0]}x{self.frame_size[1]}, "
                                                  f"the calibration is for {self.cal.size[0]}x{self.cal.size[1]}")
            return
        text, kind = focus.plate(self.found)
        if kind != focus.GOOD or self.pose is None:
            why = text.split(": ", 1)[-1] if kind != focus.GOOD else "The place of the sheet is not found yet"
            self.snackbar.show_message("warning", why[:1].upper() + why[1:])    # "Marker 7 not found", "Remove the Vertical sheet ..."
            return
        self.capture_btn.setEnabled(False)
        self.search_paused = True                        # the GIL: no marker search while the frames are taken
        self.collect = []
        self.phase = "capture"
        self.busy(True)
        self.say("")
        self.paint_badge()
        logging.info("Measure: capture of %d frames", FRAMES)

    def on_new_frame(self, bgr):
        if self.collect is None:
            return
        self.collect.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        self.paint_badge()
        if len(self.collect) >= FRAMES:
            self.start_measure()

    def start_measure(self):
        grays, self.collect = self.collect, None
        moved = empty.moved_spots(grays[0], grays[-1], self.pose) if self.pose is not None else []
        if moved:                                        # a hand or a shadow went through the capture
            self.end_capture()
            self.say("Something moved during the capture: Capture again", theme.ERR)
            return
        self.phase = "measure"
        self.scan.setGeometry(self.picture.rect())
        self.scan.start()
        self.paint_badge()
        self.worker = MeasureThread(grays, self.last_bgr.copy(), self.cal, self.tables)
        self.worker.done.connect(self.on_measured)
        self.worker.failed.connect(self.on_measure_failed)
        self.worker.start()

    def end_capture(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.wait()
        self.search_paused = False
        self.capture_btn.setEnabled(True)
        self.scan.stop()
        self.phase = None
        self.busy(False)
        self.paint_badge()

    def busy(self, on):
        """The Windows pointer "Working in background" (an arrow with a circle) while the frames are taken and counted:
        the window still answers (DECISIONS.md 2026-11-20)."""
        if on and not self.busy_on:
            QApplication.setOverrideCursor(Qt.BusyCursor)
        elif not on and self.busy_on:
            QApplication.restoreOverrideCursor()
        self.busy_on = on

    def on_measure_failed(self, reason):
        self.end_capture()
        self.say(f"Measure failed: {reason}", theme.ERR)
        logging.warning("Measure failed: %s", reason)

    def on_measured(self, res):
        """The outline is counted; the band finishes its first sweep, then the result draws itself."""
        QTimer.singleShot(self.scan.left_ms(), lambda: self.show_measured(res))

    def show_measured(self, res):
        if self.worker is None or not self.isVisible():  # the window is closing meanwhile
            return
        self.res, self.saved = res, False
        self.end_capture()
        self.say(f"Measured: {len(res['outline_mm'])} points of the outline in {res['seconds']:.2f} s", theme.OK)
        self.phase = "draw"
        self.anim_clock.start()
        self.set_view("result")
        self.anim_timer.start(ANIM_MS)

    def on_anim(self):
        if self.anim_clock.elapsed() / 1000 >= OUTLINE_S + NODES_S:
            self.stop_drawing()
        else:
            self.picture.render()

    def stop_drawing(self):
        """The end of phase 3 (or Save, LIVE again): the whole result, the badge RESULT."""
        self.anim_timer.stop()
        if self.phase == "draw":
            self.phase = None
            self.paint_badge()
            if self.view == "result":
                self.picture.render()

    # ----- the result on the straight picture
    def to_pic(self, pts_mm):
        """Sheet mm -> pixels of the straight picture: the redrawn page pixel (u, v) goes to (v, W - 1 - u)."""
        bx0, by0 = self.cal.box()[:2]
        u, v = (pts_mm[:, 0] - bx0) * PPM, (pts_mm[:, 1] - by0) * PPM
        return np.column_stack([v, self.res["top"].shape[0] - 1 - u])

    def show_result(self):
        r = self.res
        if r is None:
            return
        v = self.simplify_now()
        self.nodes_mm = simplify.simplify_contour(r["outline_mm"], DEFAULT_MODE, v["simplify_tolerance_mm"],
                                                  v["simplify_corner_deg"], v["simplify_straight_mm"])
        lying = simplify.to_lying(self.nodes_mm)
        w, h = np.ptp(lying, axis=0)
        self.figures_now = {"width_mm": round(float(w), 2), "height_mm": round(float(h), 2),
                            "area_cm2": round(measure.area(lying) / 100, 2), "perimeter_mm": round(measure.perimeter(lying), 2),
                            "nodes": len(lying), "outline_points": len(r["outline_mm"])}
        self.outline_pic = clockwise_from_top_left(self.to_pic(r["outline_mm"]))
        self.nodes_pic = clockwise_from_top_left(self.to_pic(self.nodes_mm))
        closed = np.vstack([self.outline_pic, self.outline_pic[:1]])
        self.outline_len = np.r_[0, np.cumsum(np.hypot(*np.diff(closed, axis=0).T))]
        f = self.figures_now
        self.show_figures(f)
        if self.view == "result":
            self.picture.set_frame(r["top"], self.draw_result)

    # ----- Simplify: three sliders, Reset / Default / Save
    def slider_to_value(self, key):
        _k, _t, low, _high, step, _f, _tip = next(x for x in SLIDERS if x[0] == key)
        return round(low + self.sliders[key].value() * step, 4)

    def simplify_now(self):
        return {key: self.slider_to_value(key) for key, *_rest in SLIDERS}

    def set_simplify(self, values):
        for key, _t, low, _high, step, _f, _tip in SLIDERS:
            sl = self.sliders[key]
            sl.blockSignals(True)
            sl.setValue(round((float(values[key]) - low) / step))
            sl.blockSignals(False)
        self.on_slider()

    def on_slider(self, _value=None):
        now = self.simplify_now()
        for key, _t, _l, _h, _s, fmt, _tip in SLIDERS:
            self.slider_values[key].setText(fmt.format(now[key]))
        defaults = {k: float(startup.DEFAULT_SETTINGS[k]) for k, *_r in SLIDERS}
        same = lambda a: all(abs(now[k] - a[k]) < 1e-6 for k in now)
        self.reset_btn.setEnabled(not same(self.simplify_saved))
        self.save_simplify_btn.setEnabled(not same(self.simplify_saved))
        self.default_btn.setEnabled(not same(defaults))
        if getattr(self, "res", None) is not None and getattr(self, "phase", None) is None:
            self.show_result()                           # the nodes again, at once

    def save_simplify(self):
        settings, _note = startup.load_settings()
        settings.update(self.simplify_now())
        try:
            startup.save_settings(settings)
        except OSError as err:
            self.snackbar.show_message("error", f"Settings not saved: {err}")
            return
        self.simplify_saved = self.simplify_now()
        self.on_slider()
        logging.info("Measure: simplify saved %s", self.simplify_saved)
        self.snackbar.show_message("success", "Simplify saved")

    def show_figures(self, f):
        """The big figures of the Piece card; None: dashes."""
        for _name, key, fmt, _unit in FIGURES:
            self.figure_values[key].setText(fmt.format(f[key]) if f else "–")

    def layer(self, key):
        return self.layer_switches[key].isChecked()

    def redraw_result(self, _on=None):
        if self.view == "result":
            self.picture.render()

    def draw_result(self, out, to_view, scale=0.6):
        outline_pic, nodes_pic = self.outline_pic, self.nodes_pic
        t = self.anim_clock.elapsed() / 1000 if self.phase == "draw" else None
        if t is not None and t < OUTLINE_S:              # phase 3: the outline draws itself, a white pen at its end
            m = max(2, int(np.searchsorted(self.outline_len, self.outline_len[-1] * t / OUTLINE_S)))
            p = np.int32([to_view(x, y) for x, y in outline_pic[:m:2]])
            cv2.polylines(out, [p], False, OUTLINE_BGR, 2, cv2.LINE_AA)
            cv2.circle(out, tuple(int(v) for v in p[-1]), 6, (255, 255, 255), -1, cv2.LINE_AA)
            return
        if self.layer("outline") or t is not None:      # the animation always shows the outline it has drawn
            p = np.int32([to_view(x, y) for x, y in outline_pic[::2]])
            cv2.polylines(out, [p], True, OUTLINE_BGR, 1, cv2.LINE_AA)
        if t is not None:                                # the nodes and the DXF come one node after another, as switched
            shown = int(len(nodes_pic) * min(1.0, (t - OUTLINE_S) / NODES_S))
            if self.layer("dxf") and shown >= 2:
                q = np.int32([to_view(x, y) for x, y in nodes_pic[:shown]])
                cv2.polylines(out, [q], False, NODE_BGR, 2, cv2.LINE_AA)
            if self.layer("nodes"):
                for x, y in nodes_pic[:shown]:
                    sx, sy = to_view(x, y)
                    cv2.circle(out, (round(sx), round(sy)), 4, NODE_BGR, -1, cv2.LINE_AA)
            return
        if self.layer("dxf"):                            # what goes into piece.dxf: the nodes joined by straight lines
            q = np.int32([to_view(x, y) for x, y in nodes_pic])
            cv2.polylines(out, [q], True, NODE_BGR, 2, cv2.LINE_AA)
        a, b = to_view(*nodes_pic.min(axis=0)), to_view(*nodes_pic.max(axis=0))   # no size figures on the picture (1.14.1)
        if self.layer("nodes"):
            for x, y in nodes_pic:
                sx, sy = to_view(x, y)
                cv2.circle(out, (round(sx), round(sy)), 4, NODE_BGR, -1, cv2.LINE_AA)
        if self.layer("area"):                           # the area in the middle of the size frame (DECISIONS.md 2026-11-21)
            cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            self.put_area(out, cx, cy, self.figures_now["area_cm2"], scale * AREA_SCALE, AREA_BGR)

    @staticmethod
    def put_area(out, cx, cy, area_cm2, scale, colour):
        """'118.9 cm²' centred on (cx, cy); OpenCV has no '²', so the 2 is drawn small and raised."""
        font, thick = cv2.FONT_HERSHEY_SIMPLEX, max(1, round(scale * 1.5))
        text, small = f"{area_cm2:.1f} cm", scale * 0.55
        (tw, th), _base = cv2.getTextSize(text, font, scale, thick)
        (sw, _sh), _b = cv2.getTextSize("2", font, small, thick)
        x, y = round(cx - (tw + sw) / 2), round(cy + th / 2)
        cv2.putText(out, text, (x, y), font, scale, colour, thick, cv2.LINE_AA)
        cv2.putText(out, "2", (x + tw + 1, round(y - th * 0.5)), font, small, colour, thick, cv2.LINE_AA)

    # ----- Save result: one folder per piece
    def save_result(self):
        r = self.res
        if r is None or self.nodes_mm is None:
            return
        self.stop_drawing()                              # the whole result goes into straight.png
        target = work_folder.resolve(self.dxf_dir, self.work_rel)   # the DXF: Save piece into the work folder (DECISIONS.md 2026-11-21)
        if target is None:
            self.no_work_folder()
            return
        shown = work_folder.SEP.join(("DXF",) + Path(self.work_rel).parts)
        dialog = SavePieceDialog(target, self.figures_now["area_cm2"], shown, self)
        if dialog.exec() != QDialog.Accepted or dialog.path is None:
            return
        lying = simplify.to_lying(self.nodes_mm)
        try:
            dxf_out.write_standard_dxf(str(dialog.path), lying)
        except OSError as err:
            self.snackbar.show_message("error", f"DXF not saved: {err}")
            logging.error("Measure: writing %s failed: %s", dialog.path, err)
            return
        now = datetime.datetime.now()                    # <measurements>/<YYYYMMDD>/<YYYYMMDD_HHMMSS>/ (DECISIONS.md 2026-11-20)
        folder = self.result_folder / f"{now:%Y%m%d}" / f"{now:%Y%m%d_%H%M%S}"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            straight = self.result_picture()
            for name, img in (("color.png", r["color"]), ("median.png", r["median"]), ("straight.png", straight)):
                if not cv2.imwrite(str(folder / name), img):
                    raise OSError(f"{name} could not be written")
            dxf_out.write_standard_dxf(str(folder / "piece.dxf"), lying)
            info = {"piece_name": dialog.path.stem, "dxf_path": str(dialog.path), **self.figures_now, "simplify": {k[len("simplify_"):]: v for k, v in self.simplify_now().items()}, "frames": r["frames"],
                    "outline_s": round(r["seconds"], 3), "straight_ppm": PPM, "calibration": str(self.cal_path),
                    "sheet": self.cal.sheet.name, "camera": self.profile.camera_text,
                    "frame": f"{self.frame_size[0]}x{self.frame_size[1]}" if self.frame_size else ""}
            (folder / "result.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
        except OSError as err:                           # the DXF is written; only the log is missing
            self.snackbar.show_message("warning", f"{dialog.path.name} saved, the log not: {err}")
            logging.error("Measure: saving the log %s failed: %s", folder, err)
        self.saved = True
        self.add_saved(dialog.path, self.figures_now["area_cm2"])
        logging.info("Measure: saved %s (log %s): %s", dialog.path, folder, self.figures_now)
        self.back_to_live()                              # written: the live video for the next piece
        self.snackbar.show_message("success", f"Saved {dialog.path.name}")

    # ----- the window: maximized, not centred
    def showEvent(self, event):
        QDialog.showEvent(self, event)

    def go_setup(self):
        """The gear: this window closes (the camera is released) and the Camera preview opens."""
        self.leaving = True
        self.setup_requested.emit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:                 # Esc must not end the program
            event.ignore()
            return
        super().keyPressEvent(event)

    def done(self, result):
        if not self.leaving and self.res is not None and not self.saved:
            ask = QMessageBox.question(self, self.windowTitle(), "The result is not saved. Exit without saving?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ask != QMessageBox.Yes:
                return                                   # the window stays
        self.busy(False)
        self.collect = None
        for thread in (self.worker, self.tables_thread):
            if thread is not None:
                thread.wait(30000)
        super().done(result)
