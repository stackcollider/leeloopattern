"""The Focus sheet: the standard green sheet with the grid (A3, 50x70, 61x86), found by the IDs of its markers
(docs/DECISIONS.md 2026-11-15). The search for the markers, the plate that says what was found, where the sheet lies and the sharpness of the corner markers
(the Focus screen: patterndxf/ui/focus_window.py)."""
import json
from pathlib import Path

import cv2
import numpy as np

from patterndxf.sheet import vertical

NEUTRAL, BAD, GOOD = vertical.NEUTRAL, vertical.BAD, vertical.GOOD
WORK_SHEETS = vertical.SHEETS[1:]                                # (name, marker IDs): A3, 50x70, 61x86
DETECT_WIDTH = 1600                                              # the search runs on a picture not wider than this (a 16 MP frame
                                                                 # is too slow, and the markers are big: 73 px at 4656)


def search_scaled(detector, bgr, max_width=DETECT_WIDTH):
    """The markers found in a colour picture, searched on a reduced copy; the corners are returned in the pixels of the picture."""
    h, w = bgr.shape[:2]
    k = min(1.0, max_width / w)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if k < 1.0:
        gray = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    return {i: c / k for i, c in vertical.search(detector, gray).items()}


def plate(found):
    """(text, kind) of the plate that says what the search found."""
    if not found:
        return "No markers in view", NEUTRAL
    hits = [(name, [i for i in ids if i in found], ids) for name, ids in WORK_SHEETS if any(i in found for i in ids)]
    if not hits:
        if any(i in found for i in vertical.SHEETS[0][1]):
            return "The Vertical sheet is in view, not the Focus sheet", NEUTRAL
        return "No known markers in view", NEUTRAL
    name, got, ids = max(hits, key=lambda h: len(h[1]))
    if any(i in found for i in vertical.SHEETS[0][1]):                # the Vertical sheet lies on the Focus sheet: it spoils the measuring
        return "Remove the Vertical sheet from the Focus sheet", BAD
    missing = [i for i in ids if i not in got]
    if missing:
        return f"Focus sheet {name}: marker{'s' if len(missing) > 1 else ''} {', '.join(str(i) for i in missing)} not found", BAD
    return f"Focus sheet {name}: all {len(ids)} markers found", GOOD


# ----- where the work sheet lies (docs/DECISIONS.md 2026-11-19), transferred from tools/focus_test3.py
# the marker that must be seen top left: the sheet lies turned 90 deg, the page x axis runs up the frame (decision 2026-11-19;
# A3 and 61x86 by analogy, not checked on the stand)
TOP_LEFT = {"A3": 1, "50x70": 5, "61x86": 11}
SHEETS_DIR = Path(__file__).resolve().parent / "sheets"            # the descriptions of the work sheets: the one source (also for nodecal)


def _layout():
    """{name: {page: (w, h) mm, top_left: id, markers: {id: centre (x, y) mm}, grid: (x0, y0, x1, y1) mm,
    blocks: [(x0, y0, x1, y1) mm of the marker blocks]}}: page coordinates, y down."""
    out = {}
    for path in sorted(SHEETS_DIR.glob("sheet_*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        g, cell = spec["grid"], float(spec["cell_mm"])
        out[spec["name"]] = {"page": tuple(float(v) for v in spec["page_mm"]), "top_left": TOP_LEFT.get(spec["name"]),
                             "markers": {int(k): tuple(float(v) for v in m["center"]) for k, m in spec["markers"].items()},
                             "grid": (g["x0"], g["y0"], g["x0"] + g["nx"] * cell, g["y0"] + g["ny"] * cell),
                             "blocks": [tuple(m["block"]) for m in spec["markers"].values()]}
    return out


WORK_LAYOUT = _layout()
AXIS_MM = 80.0                                                   # each arm of the cross of the sheet, mm from its centre
TURN_TOL_DEG = 10.0                                              # the sheet may lie turned this much (decision 2026-11-19)
CENTRE_TOL_FRAC, CENTRE_KEEP_FRAC = vertical.CENTRE_TOL_FRAC, vertical.CENTRE_KEEP_FRAC     # as Vertical: 25 px at 1280, kept up to 30


def sheet_pose(found):
    """The work sheet in view as (name, 3x3 matrix sheet mm -> camera px) or None. Four or more known markers give a homography,
    three an affine map (a little rougher), fewer nothing."""
    best = None
    for name, lay in WORK_LAYOUT.items():
        ids = [i for i in lay["markers"] if i in found]
        if len(ids) >= 3 and (best is None or len(ids) > len(best[1])):
            best = (name, ids)
    if best is None:
        return None
    name, ids = best
    model = np.float32([WORK_LAYOUT[name]["markers"][i] for i in ids])
    seen = np.float32([found[i].mean(axis=0) for i in ids])
    if len(ids) >= 4:
        m, _ = cv2.findHomography(model, seen, 0)
    else:
        m = np.vstack([cv2.getAffineTransform(model, seen), [0.0, 0.0, 1.0]])
    return (name, m) if m is not None else None


def to_px(m, pts_mm):
    """Sheet mm -> camera px by the 3x3 matrix of sheet_pose."""
    return cv2.perspectiveTransform(np.float64(pts_mm).reshape(-1, 1, 2), m).reshape(-1, 2)


def sheet_cross(pose):
    """The cross of the sheet in camera px: (centre, the ends of the arm along the page x, the ends of the arm along the page y)."""
    name, m = pose
    cx, cy = WORK_LAYOUT[name]["page"][0] / 2, WORK_LAYOUT[name]["page"][1] / 2
    p = to_px(m, [(cx, cy), (cx - AXIS_MM, cy), (cx + AXIS_MM, cy), (cx, cy - AXIS_MM), (cx, cy + AXIS_MM)])
    return p[0], (p[1], p[2]), (p[3], p[4])


def place(pose, w, h):
    """Where the sheet lies: {dx, dy: its centre from the frame centre, px (plus = right, down); turn: degrees (0 = the page x axis
    runs straight up the frame, plus = turned clockwise)}."""
    centre, (x0, x1), _ = sheet_cross(pose)
    d = x1 - x0
    return {"dx": float(centre[0] - (w - 1) / 2), "dy": float(centre[1] - (h - 1) / 2),
            "turn": float(np.degrees(np.arctan2(d[0], -d[1])))}


def place_state(found, w, h, was_ok):
    """The condition for Finish about the sheet: whole in view (every marker of it), its centre on the cross, turned not more
    than TURN_TOL_DEG. -> (ok, text, kind). Once good, the centre stays good up to CENTRE_KEEP_FRAC (a gap against flicker)."""
    pose = sheet_pose(found)
    if pose is None:
        return False, "Sheet centre: unknown (3 markers of a work sheet are needed)", NEUTRAL
    p = place(pose, w, h)
    name = pose[0]
    text = (f"Sheet centre: {abs(p['dx']):.0f} px {'right' if p['dx'] >= 0 else 'left'}, "
            f"{abs(p['dy']):.0f} px {'down' if p['dy'] >= 0 else 'up'}\nturn {p['turn']:+.1f} deg")
    if abs(p["turn"]) > 45:
        tl = WORK_LAYOUT[name]["top_left"]
        return False, text + (f": marker {tl} must be top left" if tl is not None else ": turn the sheet"), BAD
    whole = all(i in found for i in WORK_LAYOUT[name]["markers"])
    centred = bool(np.hypot(p["dx"], p["dy"]) <= (CENTRE_KEEP_FRAC if was_ok else CENTRE_TOL_FRAC) * w)
    straight = abs(p["turn"]) <= TURN_TOL_DEG
    if whole and centred and straight:
        return True, text, GOOD
    need = [m for m, ok in (("the whole sheet in view", whole), ("the centre on the cross", centred),
                            (f"turn within {TURN_TOL_DEG:.0f} deg", straight)) if not ok]
    return False, text + "\nneeded: " + ", ".join(need), BAD


# ----- the corner tiles: how sharp the extreme markers are
def edge_px(gray):
    """How wide the edges of a picture are: the contrast divided by the steepest slope, px (smaller = sharper)."""
    g = gray.astype(np.float32)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    slope = float(np.percentile(np.sqrt(gx * gx + gy * gy) / 8.0, 99))      # a ramp of m per pixel gives 8 m
    contrast = float(np.percentile(g, 98) - np.percentile(g, 2))
    return contrast / slope if slope > 1e-3 and contrast > 20 else float("nan")


def pick_extremes(found, size):
    """The four work-sheet markers nearest to the corners of the frame: [(place, id, centre (x, y px), side px)].
    The distances are taken in a 4:3 frame, so that x and y weigh as they do on the picture."""
    work = set()
    for _name, ids in WORK_SHEETS:
        work |= set(ids)
    seen = {i: (c.mean(axis=0), float(np.mean([np.linalg.norm(c[(k + 1) % 4] - c[k]) for k in range(4)])))
            for i, c in found.items() if i in work}
    w, h = size
    picks, used = [], set()
    for corner, (fx, fy) in (("top left", (0, 0)), ("top right", (1, 0)), ("bottom right", (1, 1)), ("bottom left", (0, 1))):
        best = min(((i, v) for i, v in seen.items() if i not in used),
                   key=lambda it: ((it[1][0][0] / w - fx) * 4) ** 2 + ((it[1][0][1] / h - fy) * 3) ** 2, default=None)
        if best is not None:
            used.add(best[0])
            picks.append((corner, best[0], best[1][0], best[1][1]))
    return picks
