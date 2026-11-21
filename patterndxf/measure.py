"""The outline of a piece on the work sheet, in sheet mm (docs/DECISIONS.md 2026-11-19 "Measure", way A).

The median picture of the camera -> the biggest bright piece inside the grid of the sheet (the marker blocks and everything
off the sheet are left out) -> its outline at 1/4 px -> sheet mm by the calibration model (nodecal).
Tried first in tools/measure_test.py (the star: perimeter and area as the reference DXF, docs/ACCURACY.md 2026-11-19).
"""
import cv2
import numpy as np

LEVEL = 205                 # gray level of the edge: paper ~252, the green sheet ~150-170 between its lines
UP = 4                      # the picture around the piece is enlarged this much before the outline is taken (1/4 px)
MIN_PIECE_MM2 = 300.0       # the smallest piece (the TZ: 20 x 20 mm, the bust middle 30 x 20)
BLOCK_PAD_MM = 1.0          # the marker blocks are left out with this much around them
EDGE_STEPS = 40             # a side of a rectangle is followed by this many points (the lens bends it a little)


def calibration_path():
    """Where the calibration of the stand lives: next to the profile (written by the Calibration step)."""
    from patterndxf import profile
    return profile.profile_path().parent / "calibration.json"


def _ring(x0, y0, x1, y1):
    t = np.linspace(0.0, 1.0, EDGE_STEPS, endpoint=False)
    return np.vstack([np.column_stack([x0 + (x1 - x0) * t, np.full_like(t, y0)]),
                      np.column_stack([np.full_like(t, x1), y0 + (y1 - y0) * t]),
                      np.column_stack([x1 - (x1 - x0) * t, np.full_like(t, y1)]),
                      np.column_stack([np.full_like(t, x0), y1 - (y1 - y0) * t])])


def sheet_mask(cal, shape):
    """255 where a piece may be: inside the grid of the sheet, not on a marker block; camera pixels of a picture of `shape`."""
    s = cal.sheet
    mask = np.zeros(shape[:2], np.uint8)
    grid = (s.x0, s.y0, s.x0 + s.nx * s.cell, s.y0 + s.ny * s.cell)
    cv2.fillPoly(mask, [np.int32(np.round(cal.to_px(_ring(*grid))))], 255)
    p = BLOCK_PAD_MM
    for b in s.blocks:
        cv2.fillPoly(mask, [np.int32(np.round(cal.to_px(_ring(b[0] - p, b[1] - p, b[2] + p, b[3] + p))))], 0)
    return mask


def outline_px(gray, level=LEVEL, up=UP, mask=None):
    """The outline of the biggest bright piece of a gray picture, sub-pixel: [(x, y)] in the pixels of the picture, or None.
    mask: where to look (255), the rest is taken as dark."""
    if mask is not None:
        gray = np.where(mask > 0, gray, 0).astype(np.uint8)
    _, bright = cv2.threshold(gray, level, 255, cv2.THRESH_BINARY)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bright, connectivity=8)
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
    if n < 2:
        return None
    piece = np.uint8(labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))) * 255
    contours, _ = cv2.findContours(piece, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)    # the outer border only: holes do not matter
    c = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
    return (c + 0.5) / up - 0.5 + np.array([x0, y0])        # the centre of an enlarged pixel back to the picture


def area(p):
    return 0.5 * abs(float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1))))


def perimeter(p):
    return float(np.linalg.norm(np.roll(p, -1, axis=0) - p, axis=1).sum())


def outline_mm(gray, cal):
    """The outline of the piece on the median picture in sheet mm (page coordinates, y down), or (None, reason)."""
    if gray.shape[::-1] != tuple(cal.size):
        return None, f"the picture is {gray.shape[1]}x{gray.shape[0]}, the calibration is for {cal.size[0]}x{cal.size[1]}"
    px = outline_px(gray, mask=sheet_mask(cal, gray.shape))
    if px is None:
        return None, "no piece on the sheet"
    mm = cal.to_mm(px)
    if area(mm) < MIN_PIECE_MM2:
        return None, f"no piece on the sheet (the biggest bright spot is {area(mm):.0f} mm²)"
    return mm, ""
