"""Measure test: the outline of a pattern piece in mm, two ways, against its DXF (2026-11-19).

Run: .venv-cam\\Scripts\\python tools\\measure_test.py [calibration.json] [reference.dxf]
     (defaults: %APPDATA%\\LeeLooPattern\\calibration.json, out/star5_200mm.dxf; the samples: out/samples/*/median.png)

A  the outline is found on the median picture of the camera (to 1/4 px) and every point is turned into sheet mm by the model;
B  the sheet is redrawn from above first (B5: 5 px/mm as the program does now, B10: 10 px/mm) and the outline is found there.
Each outline is laid on the reference (shift + turn, mirror allowed) and the distance of every outline point to the reference
outline is measured (mean, p95, max, mm); a second fit with a free scale tells whether the model is wrong in size.
The window: the list of samples x ways with the figures; the picture of the chosen one (the normalised sheet with the reference
in red, A in blue, B in orange).
"""
import os
import sys
import time
from pathlib import Path

import cv2
import ezdxf
import numpy as np
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)
from scipy.optimize import minimize
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from patterndxf.calib import nodecal                                          # noqa: E402
from patterndxf.ui import theme                                               # noqa: E402
from patterndxf.ui.widgets import ZoomPicture, card                           # noqa: E402

LEVEL = 205                 # gray level of the edge: paper ~252, the green sheet ~150-170 between its lines
UP = 4                      # A: the picture around the piece is enlarged this much before the outline is taken (1/4 px)
REF_STEP_MM = 0.1           # the reference outline is sampled this densely
VIEW_PPM = 10.0             # the picture in the window
COLOURS = {"ref": (60, 60, 255), "A": (255, 160, 40), "B5": (0, 150, 255), "B10": (0, 200, 255)}   # BGR: red, blue, orange, yellow


# ----- the outline
def outline_px(gray, level=LEVEL, up=UP):
    """The outline of the biggest bright piece of a gray picture, sub-pixel: [(x, y)] in the pixels of the picture, or None."""
    _, mask = cv2.threshold(gray, level, 255, cv2.THRESH_BINARY)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n < 2:
        return None
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = stats[big, :4]
    pad = 8
    x0, y0 = max(x - pad, 0), max(y - pad, 0)
    x1, y1 = min(x + w + pad, gray.shape[1]), min(y + h + pad, gray.shape[0])
    roi = cv2.GaussianBlur(gray[y0:y1, x0:x1].astype(np.float32), (0, 0), 0.8)
    big_roi = cv2.resize(roi, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    m = np.uint8(big_roi > level) * 255
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    piece = np.uint8(labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))) * 255
    contours, _ = cv2.findContours(piece, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)    # the outer border only: holes do not matter
    c = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
    return (c + 0.5) / up - 0.5 + np.array([x0, y0])        # the centre of an enlarged pixel back to the picture


# ----- the reference
def read_reference(path):
    """The first closed polyline of a DXF, mm: (N, 2)."""
    doc = ezdxf.readfile(path)
    for e in doc.modelspace():
        if e.dxftype() == "LWPOLYLINE":
            return np.array([(p[0], p[1]) for p in e.get_points()], np.float64)
        if e.dxftype() == "POLYLINE":
            return np.array([(v.dxf.location[0], v.dxf.location[1]) for v in e.vertices], np.float64)
    raise ValueError(f"{path}: no polyline")


def sample(poly, step):
    pts = []
    for a, b in zip(poly, np.roll(poly, -1, axis=0)):
        n = max(1, int(np.ceil(np.linalg.norm(b - a) / step)))
        pts.append(a + (b - a) * np.arange(n)[:, None] / n)
    return np.vstack(pts)


def area(poly):
    x, y = poly[:, 0], poly[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def fit(pts, ref_tree, ref_centre, free_scale=False):
    """Lays the outline on the reference: turn, shift (and scale): -> (distances mm, the moved points, scale, mirrored)."""
    best = None
    c = pts.mean(axis=0)
    for mirror in (False, True):
        p0 = (pts - c) * ([-1.0, 1.0] if mirror else [1.0, 1.0])
        for ang in np.radians(np.arange(0, 360, 3)):                     # a rough search of the turn first
            r = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]])
            d = ref_tree.query(p0 @ r.T + ref_centre)[0]
            if best is None or d.mean() < best[0]:
                best = (d.mean(), mirror, ang)
    _, mirror, ang0 = best
    p0 = (pts - c) * ([-1.0, 1.0] if mirror else [1.0, 1.0])

    def move(v):
        a, tx, ty = v[:3]
        s = v[3] if free_scale else 1.0
        r = np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
        return s * (p0 @ r.T) + ref_centre + [tx, ty]

    v0 = [ang0, 0.0, 0.0] + ([1.0] if free_scale else [])
    res = minimize(lambda v: np.mean(ref_tree.query(move(v))[0] ** 2), v0, method="Nelder-Mead",
                   options={"xatol": 1e-7, "fatol": 1e-10, "maxiter": 4000})
    moved = move(res.x)
    return ref_tree.query(moved)[0], moved, (res.x[3] if free_scale else 1.0), mirror


# ----- the work, away from the window
class Worker(QThread):
    row = Signal(dict)
    done = Signal(str)

    def __init__(self, cal_path, ref_path, samples):
        super().__init__()
        self.cal_path, self.ref_path, self.samples = cal_path, ref_path, samples

    def run(self):
        try:
            cal = nodecal.Calibration.load(str(self.cal_path))
            ref = read_reference(str(self.ref_path))
            dense = sample(ref, REF_STEP_MM)
            tree, centre = cKDTree(dense), dense.mean(axis=0)
            bx0, by0 = cal.box()[:2]
            tables = {}
            for folder in self.samples:
                gray = cv2.imread(str(folder / "median.png"), cv2.IMREAD_GRAYSCALE)
                if gray.shape[::-1] != tuple(cal.size):
                    self.row.emit({"sample": folder.name, "way": "-", "error": f"picture {gray.shape[::-1]} is not of the model {cal.size}"})
                    continue
                view = None
                for way in ("A", "B5", "B10"):
                    t = time.perf_counter()
                    if way == "A":
                        px = outline_px(gray)
                        mm = None if px is None else cal.to_mm(px)
                    else:
                        ppm = 5.0 if way == "B5" else 10.0
                        if ppm not in tables:
                            tables[ppm] = cal.remap_tables(ppm)
                        top = cal.redraw(gray, ppm, tables[ppm])
                        px = outline_px(top)
                        mm = None if px is None else px / ppm + [bx0, by0]
                        if way == "B10":
                            view = top
                    seconds = time.perf_counter() - t
                    if mm is None:
                        self.row.emit({"sample": folder.name, "way": way, "error": "no outline"})
                        continue
                    d, _moved, _, mirror = fit(mm, tree, centre)
                    _, _, scale, _ = fit(mm, tree, centre, free_scale=True)
                    self.row.emit({"sample": folder.name, "way": way, "mean": float(d.mean()), "p95": float(np.percentile(d, 95)),
                                   "max": float(d.max()), "scale": float(scale), "area": area(mm) / 100.0,
                                   "ref_area": area(ref) / 100.0, "mirror": mirror, "points": len(mm), "seconds": seconds,
                                   "mm": mm, "view": view if way == "B10" else None, "box0": (bx0, by0)})
            self.done.emit("")
        except Exception as err:                       # a plain line, never a silent death of the thread
            import traceback
            traceback.print_exc()
            self.done.emit(f"{type(err).__name__}: {err}")


class MainWindow(QMainWindow):
    COLUMNS = ("Sample", "Way", "mean mm", "p95 mm", "max mm", "scale", "area cm²", "s")

    def __init__(self, cal_path, ref_path, samples):
        super().__init__()
        self.setWindowTitle("Measure test")
        self.rows, self.views = [], {}
        self.picture = ZoomPicture("Measuring ...")
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setStyleSheet(f"font-family: {theme.MONO}; font-size: 13px")
        info = QLabel(f"model: {cal_path}\nreference: {ref_path}\n"
                      "A: outline on the camera picture, points to mm by the model\n"
                      "B5 / B10: outline on the sheet redrawn at 5 / 10 px/mm\n"
                      "distances: outline points to the reference after shift + turn; scale: free-scale fit\n"
                      "picture: the sheet at 10 px/mm; reference red, A blue, B5 orange, B10 yellow")
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {theme.TEXT2}; font-size: 12px")
        self.status = QLabel("Measuring ...")
        side = QVBoxLayout()
        side.addWidget(card("Results", self.table))
        side.addWidget(info)
        side.addWidget(self.status)
        right = QWidget()
        right.setFixedWidth(700)
        right.setLayout(side)
        lay = QHBoxLayout()
        lay.addWidget(self.picture, 1)
        lay.addWidget(right)
        box = QWidget()
        box.setLayout(lay)
        self.setCentralWidget(box)
        self.resize(1700, 950)
        self.table.itemSelectionChanged.connect(self.show_selected)
        self.worker = Worker(cal_path, ref_path, samples)
        self.worker.row.connect(self.add_row)
        self.worker.done.connect(self.on_done)
        self.worker.start()

    def add_row(self, r):
        self.rows.append(r)
        if r.get("view") is not None:
            self.views[r["sample"]] = (r["view"], r["box0"])
        i = self.table.rowCount()
        self.table.insertRow(i)
        if "error" in r:
            cells = (r["sample"], r["way"], r["error"], "", "", "", "", "")
        else:
            cells = (r["sample"], r["way"], f"{r['mean']:.3f}", f"{r['p95']:.3f}", f"{r['max']:.3f}", f"{r['scale']:.4f}",
                     f"{r['area']:.1f} / {r['ref_area']:.1f}", f"{r['seconds']:.1f}")
        for j, text in enumerate(cells):
            self.table.setItem(i, j, QTableWidgetItem(text))
        if self.table.rowCount() == 1:
            self.table.selectRow(0)

    def on_done(self, error):
        self.status.setText(f"Failed: {error}" if error else f"Done: {len(self.rows)} rows. Pick a row to see its sample.")
        self.show_selected()

    def show_selected(self):
        sel = self.table.selectedItems()
        if not sel:
            return
        name = self.table.item(sel[0].row(), 0).text()
        if name not in self.views:
            return
        view, (bx0, by0) = self.views[name]
        pic = cv2.cvtColor(view, cv2.COLOR_GRAY2BGR)
        lines = []
        for r in self.rows:
            if r["sample"] == name and "mm" in r:
                lines.append((r["way"], (r["mm"] - [bx0, by0]) * VIEW_PPM))

        def overlay(out, to_view):
            for way, pts in lines:
                p = np.int32([to_view(x, y) for x, y in pts[::2]])
                cv2.polylines(out, [p], True, COLOURS.get(way, (255, 255, 255)), 1, cv2.LINE_AA)
        self.picture.set_frame(pic, overlay)


def main():
    appdata = os.environ.get("APPDATA", "")
    cal_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(appdata) / "LeeLooPattern" / "calibration.json"
    ref_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "out" / "star5_200mm.dxf"
    samples = sorted(p.parent for p in (ROOT / "out" / "samples").glob("*/median.png"))
    app = QApplication(sys.argv)
    theme.apply(app)
    theme.set_app_icon(app)
    win = MainWindow(cal_path, ref_path, samples)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
