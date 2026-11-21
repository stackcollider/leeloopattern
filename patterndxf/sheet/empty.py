"""Is the work sheet empty? (2026-11-19, before the calibration: the model must not be built on a covered sheet.)

foreign_spots: on a reduced copy of the picture, inside the grid of the sheet (the marker blocks left out), everything that is
neither the green of the sheet nor a thin printed line: paper, a pattern piece, a hand, a tool (a big dark thing counts too).
moved_spots: the places where two pictures differ (something moved during the capture).
Both return spots as outlines in the camera pixels of the full picture and their areas in cm2. The thresholds are STARTING
values, to be checked on the stand.
"""
import cv2
import numpy as np

from patterndxf.sheet import focus

WORK_W = 1200                                   # the checks run on a copy not wider than this
INSET_MM, BLOCK_PAD_MM = 6.0, 3.0               # the grid area is taken this much inside (the marker model is rough at the border);
                                                # the marker blocks this much wider
MIN_SPOT_CM2 = 1.0                              # a smaller spot is noise (decision 2026-11-19: strict, the sheet must be clean)
MERGE_MM = 5.0                                  # pieces closer than this are one thing
GREEN_H, GREEN_S, GREEN_V = (45, 95), 60, 60    # the sheet (measured on the stand: hue 62-74, saturation 100-170)
DARK_V = 110                                    # darker: a printed line, or a dark thing when it is wide
DARK_WIDE_PX = 5                                # at WORK_W a printed line is under 1 px wide: a dark spot wider than this is a thing
MOVED_DIFF = 30                                 # two pictures differ here when the gray levels differ by more


def _work(img):
    k = min(1.0, WORK_W / img.shape[1])
    small = cv2.resize(img, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1.0 else img
    return small, k


def _sheet_mask(shape, pose, k):
    """The grid area of the sheet, a little inside, without the marker blocks, on the reduced picture."""
    name, m = pose
    lay = focus.WORK_LAYOUT[name]
    x0, y0, x1, y1 = lay["grid"]
    x0, y0, x1, y1 = x0 + INSET_MM, y0 + INSET_MM, x1 - INSET_MM, y1 - INSET_MM
    edge = []
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        n = max(2, int(np.hypot(b[0] - a[0], b[1] - a[1]) // 10))
        edge += [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) for t in np.linspace(0, 1, n, endpoint=False)]
    mask = np.zeros(shape[:2], np.uint8)
    cv2.fillPoly(mask, [np.int32(focus.to_px(m, edge) * k)], 255)
    p = BLOCK_PAD_MM
    for bx0, by0, bx1, by1 in lay["blocks"]:
        quad = [(bx0 - p, by0 - p), (bx1 + p, by0 - p), (bx1 + p, by1 + p), (bx0 - p, by1 + p)]
        cv2.fillPoly(mask, [np.int32(focus.to_px(m, quad) * k)], 0)
    return mask


def _px_per_mm(pose, k):
    """Pixels per mm of the reduced picture near the sheet centre."""
    name, m = pose
    cx, cy = (v / 2 for v in focus.WORK_LAYOUT[name]["page"])
    a, b = focus.to_px(m, [(cx, cy), (cx + 10.0, cy)])
    return float(np.linalg.norm(b - a)) / 10.0 * k


def _spots(bad, mask, pose, k):
    """Big enough spots of `bad` inside the mask -> [(outline in full camera px, area cm2)]. Pieces closer than MERGE_MM are one
    thing (a pen is light and dark parts: one outline, one area); the area counts only the pixels that are really foreign."""
    bad = cv2.morphologyEx(bad & mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    ppm = _px_per_mm(pose, k)
    min_px = MIN_SPOT_CM2 * 100.0 * ppm * ppm
    reach = max(3, int(round(MERGE_MM * ppm)) | 1)
    merged = cv2.dilate(bad, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (reach, reach)))
    out = []
    n, labels = cv2.connectedComponents(merged, connectivity=8)
    for i in range(1, n):
        part = labels == i
        area_px = int(np.count_nonzero(bad[part]))
        if area_px >= min_px:
            contours, _ = cv2.findContours(np.uint8(part) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            outline = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64) / k
            out.append((outline, area_px / (ppm * ppm) / 100.0))
    return out


def foreign_spots(bgr, pose):
    """What lies on the sheet: [(outline in camera px, area cm2)], [] when the sheet is empty. pose: focus.sheet_pose."""
    small, k = _work(bgr)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    green = (h >= GREEN_H[0]) & (h <= GREEN_H[1]) & (s >= GREEN_S) & (v >= GREEN_V)
    dark = np.uint8(v < DARK_V) * 255
    wide = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (DARK_WIDE_PX, DARK_WIDE_PX))
    wide_dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, wide)
    bad = (np.uint8(~green & (dark == 0)) * 255) | wide_dark
    return _spots(bad, _sheet_mask(small.shape, pose, k), pose, k)


def moved_spots(gray_a, gray_b, pose):
    """Where two gray pictures of the capture differ: [(outline in camera px, area cm2)]."""
    a, k = _work(gray_a)
    b, _ = _work(gray_b)
    diff = cv2.absdiff(cv2.GaussianBlur(a, (5, 5), 0), cv2.GaussianBlur(b, (5, 5), 0))
    bad = np.uint8(diff > MOVED_DIFF) * 255
    return _spots(bad, _sheet_mask(a.shape, pose, k), pose, k)


def where(outline, size):
    """A few words for the person: in which part of the picture a spot is ("upper left", "middle", ...)."""
    cx, cy = outline.mean(axis=0)
    w, h = size
    col = "left" if cx < w / 3 else "right" if cx > 2 * w / 3 else ""
    row = "upper" if cy < h / 3 else "lower" if cy > 2 * h / 3 else ""
    return f"{row} {col}".strip() or "middle"
