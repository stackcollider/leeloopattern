"""Measure screen, test: the Measure step tried on saved snapshots instead of the camera (2026-11-19).

Run: .venv-cam\\Scripts\\python tools\\measure_screen.py [calibration.json]
     (default: %APPDATA%\\LeeLooPattern\\calibration.json; the snapshots: out/samples/*/median.png + color.png, made by
     tools/grab_frames.py)

The way (docs/DECISIONS.md 2026-11-19 "Measure"): the outline of the piece is found on the median picture of the camera
(way A, 1/4 px, tools/measure_test.py) and its points are turned into sheet mm by the model; the person sees the STRAIGHT
picture: the sheet redrawn from above as it lies (marker 5 top left), with the outline, its nodes and its size on it.
Simplify: the modes of patterndxf/core/simplify.py. Save DXF: the Standard DXF that AccuMark imports (core/dxf_out.py),
as the piece lies (x = sheet y, y = sheet x), mm, into out/dxf/<sample>.dxf.
"""
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QHBoxLayout, QLabel, QListWidget, QMainWindow, QPushButton, QVBoxLayout,
                               QWidget)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_test import outline_px                                            # noqa: E402
from patterndxf.calib import nodecal                                           # noqa: E402
from patterndxf.core import dxf_out, simplify                                  # noqa: E402
from patterndxf.ui import theme                                                # noqa: E402
from patterndxf.ui.widgets import ZoomPicture, card                            # noqa: E402

PPM = 5.0                                       # the straight picture: px per mm
OUTLINE_BGR, NODE_BGR, SIZE_BGR = (255, 191, 0), (60, 60, 255), (220, 220, 220)    # sky blue outline, red nodes, light size lines
OUT_DXF = ROOT / "out" / "dxf"


def area(p):
    return 0.5 * abs(float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1))))


def perimeter(p):
    return float(np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1).sum())


class Worker(QThread):
    """Loads the model, makes the tables of the straight picture once, then measures every snapshot."""
    ready = Signal(str)                             # "" or the reason it failed
    measured = Signal(str, object)                  # the sample, {top, outline_mm, seconds} or {error}

    def __init__(self, cal_path):
        super().__init__()
        self.cal_path, self.queue, self.cal, self.tables = cal_path, [], None, None

    def run(self):
        try:
            self.cal = nodecal.Calibration.load(str(self.cal_path))
            self.tables = self.cal.remap_tables(PPM)
            self.ready.emit("")
        except Exception as err:                    # a plain line, never a silent death of the thread
            self.ready.emit(f"{type(err).__name__}: {err}")
            return
        while self.queue:
            self.measure(self.queue.pop(0))

    def measure(self, folder):
        try:
            gray = cv2.imread(str(folder / "median.png"), cv2.IMREAD_GRAYSCALE)
            color = cv2.imread(str(folder / "color.png"))
            if gray is None or gray.shape[::-1] != tuple(self.cal.size):
                self.measured.emit(folder.name, {"error": "the picture is missing or not of the size of the model"})
                return
            t = time.perf_counter()
            px = outline_px(gray)
            if px is None:
                self.measured.emit(folder.name, {"error": "no piece found"})
                return
            mm = self.cal.to_mm(px)
            seconds = time.perf_counter() - t
            top = cv2.rotate(self.cal.redraw(color if color is not None else gray, PPM, self.tables), cv2.ROTATE_90_COUNTERCLOCKWISE)
            self.measured.emit(folder.name, {"top": top, "outline_mm": mm, "seconds": seconds, "box0": self.cal.box()[:2]})
        except Exception as err:
            self.measured.emit(folder.name, {"error": f"{type(err).__name__}: {err}"})


class MainWindow(QMainWindow):
    def __init__(self, cal_path, samples):
        super().__init__()
        self.setWindowTitle("Measure (test on snapshots)")
        self.samples = {s.name: s for s in samples}
        self.results, self.current, self.nodes_mm = {}, None, None
        self.picture = ZoomPicture("Loading the model ...")
        self.list = QListWidget()
        self.list.addItems(list(self.samples))
        self.list.setFixedHeight(150)
        self.mode_box = QComboBox()
        for key, (text, tol) in simplify.MODES.items():
            self.mode_box.addItem(f"{text} ({tol:g} mm)", key)
        self.mode_box.setCurrentIndex(list(simplify.MODES).index("corners"))
        self.figures = QLabel("")
        self.figures.setStyleSheet(f"font-family: {theme.MONO}; font-size: 13px")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.nodes_btn = QPushButton("Hide nodes")
        self.nodes_btn.setCheckable(True)
        self.fit_btn, self.save_btn = QPushButton("Fit"), QPushButton("Save DXF")
        self.save_btn.setObjectName("primary")
        self.save_btn.setEnabled(False)
        for b in (self.nodes_btn, self.fit_btn, self.save_btn):
            b.setMinimumHeight(44)
        hint = QLabel(f"model: {cal_path}\nThe straight picture: the sheet from above as it lies, {PPM:g} px/mm. "
                      "The outline: way A (on the camera picture, points to mm by the model).")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT2}; font-size: 12px")
        side = QVBoxLayout()
        side.addWidget(card("Snapshot", self.list))
        side.addWidget(card("Simplify", self.mode_box))
        side.addWidget(card("Piece", self.figures))
        side.addWidget(hint)
        side.addStretch(1)
        side.addWidget(self.status)
        side.addWidget(self.nodes_btn)
        side.addWidget(self.fit_btn)
        side.addWidget(self.save_btn)
        right = QWidget()
        right.setFixedWidth(400)
        right.setLayout(side)
        lay = QHBoxLayout()
        lay.addWidget(self.picture, 1)
        lay.addWidget(right)
        box = QWidget()
        box.setLayout(lay)
        self.setCentralWidget(box)
        self.resize(1600, 950)
        self.list.currentTextChanged.connect(self.pick)
        self.mode_box.activated.connect(lambda _i: self.show_current())
        self.nodes_btn.toggled.connect(self.on_nodes)
        self.fit_btn.clicked.connect(self.picture.fit)
        self.save_btn.clicked.connect(self.save)
        self.worker = Worker(cal_path)
        self.worker.queue = list(samples)
        self.worker.ready.connect(self.on_ready)
        self.worker.measured.connect(self.on_measured)
        self.worker.start()

    def on_ready(self, error):
        if error:
            self.status.setText(f"The model could not be loaded: {error}")
            self.status.setStyleSheet(f"color: {theme.ERR}")
            self.picture.clear_frame("No model")
        else:
            self.status.setText("Measuring the snapshots ...")

    def on_measured(self, name, res):
        self.results[name] = res
        if self.current is None:
            self.list.setCurrentRow(0)
        elif name == self.current:
            self.show_current()
        if len(self.results) == len(self.samples):
            self.status.setText(f"{len(self.results)} snapshots measured.")

    def pick(self, name):
        self.current = name
        self.show_current()

    def on_nodes(self, hidden):
        self.nodes_btn.setText("Show nodes" if hidden else "Hide nodes")
        self.picture.render()

    # ----- the piece as it lies: sheet mm (page, y down) -> DXF mm (x = sheet y, y = sheet x) and -> the straight picture
    def to_pic(self, pts_mm, res):
        """Sheet mm -> pixels of the straight picture: the redrawn page pixel (u, v) goes to (v, W - 1 - u)."""
        bx0, by0 = res["box0"]
        u, v = (pts_mm[:, 0] - bx0) * PPM, (pts_mm[:, 1] - by0) * PPM
        return np.column_stack([v, res["top"].shape[0] - 1 - u])

    def show_current(self):
        res = self.results.get(self.current)
        self.save_btn.setEnabled(False)
        if res is None:
            self.picture.clear_frame("Measuring ...")
            self.figures.setText("")
            return
        if "error" in res:
            self.picture.clear_frame(res["error"])
            self.figures.setText(res["error"])
            return
        mm = res["outline_mm"]
        self.nodes_mm = simplify.simplify_contour(mm, self.mode_box.currentData())
        lying = simplify.to_lying(self.nodes_mm)
        w, h = np.ptp(lying, axis=0)
        self.figures.setText(f"width       {w:8.1f} mm\nheight      {h:8.1f} mm\narea        {area(lying) / 100:8.1f} cm²\n"
                             f"perimeter   {perimeter(lying):8.1f} mm\nnodes       {len(lying):8d}\n"
                             f"outline     {len(mm):8d} points, {res['seconds']:.2f} s")
        outline_pic, nodes_pic = self.to_pic(mm, res), self.to_pic(self.nodes_mm, res)
        lo, hi = nodes_pic.min(axis=0), nodes_pic.max(axis=0)

        def overlay(out, to_view):
            p = np.int32([to_view(x, y) for x, y in outline_pic[::2]])
            cv2.polylines(out, [p], True, OUTLINE_BGR, 2, cv2.LINE_AA)
            a, b = to_view(*lo), to_view(*hi)
            cv2.rectangle(out, (round(a[0]), round(a[1])), (round(b[0]), round(b[1])), SIZE_BGR, 1, cv2.LINE_AA)
            cv2.putText(out, f"{w:.1f} mm", (round((a[0] + b[0]) / 2) - 40, round(a[1]) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        SIZE_BGR, 1, cv2.LINE_AA)
            cv2.putText(out, f"{h:.1f} mm", (round(b[0]) + 8, round((a[1] + b[1]) / 2)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        SIZE_BGR, 1, cv2.LINE_AA)
            if not self.nodes_btn.isChecked():
                for x, y in nodes_pic:
                    sx, sy = to_view(x, y)
                    cv2.circle(out, (round(sx), round(sy)), 4, NODE_BGR, -1, cv2.LINE_AA)
        self.picture.set_frame(res["top"], overlay)
        self.save_btn.setEnabled(True)

    def save(self):
        if self.nodes_mm is None:
            return
        OUT_DXF.mkdir(parents=True, exist_ok=True)
        path = OUT_DXF / f"{self.current}.dxf"
        try:
            dxf_out.write_standard_dxf(str(path), simplify.to_lying(self.nodes_mm))
            self.status.setText(f"Saved {path}")
            self.status.setStyleSheet(f"color: {theme.OK}")
        except OSError as err:
            self.status.setText(f"Save failed: {err}")
            self.status.setStyleSheet(f"color: {theme.ERR}")

    def closeEvent(self, event):
        self.worker.queue = []
        self.worker.wait(30000)
        super().closeEvent(event)


def main():
    cal_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(os.environ.get("APPDATA", "")) / "LeeLooPattern" / "calibration.json"
    samples = sorted(p.parent for p in (ROOT / "out" / "samples").glob("*/median.png"))
    app = QApplication(sys.argv)
    theme.apply(app)
    theme.set_app_icon(app)
    win = MainWindow(cal_path, samples)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
