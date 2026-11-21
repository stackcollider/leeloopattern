"""The Vertical sheet: the search for its markers and what the screen says about them (docs/DECISIONS.md 2026-11-15).

The Vertical sheet is the white sheet with the markers 42-49 (tools/make_camera_blank_pdf.py; the old name was "set-up blank").
The four corner markers 46-49 (clockwise from the top left) must all be found. The work sheets (A3, 50x70, 61x86) are the green
sheets with the grid; the Focus sheet is the standard green sheet.
"""
import cv2
import numpy as np

CORNER_IDS = (46, 47, 48, 49)                                   # top left, top right, bottom right, bottom left
SHEETS = (("Vertical sheet", range(42, 50)), ("A3", range(0, 4)), ("50x70", range(4, 10)), ("61x86", range(10, 16)))
SIDE_NAMES = ("top", "right", "bottom", "left")                  # the sides 46-47, 47-48, 48-49, 49-46
TURN_TOL_DEG = 10.0                                              # the sheet may lie turned this much (decision 2026-11-15)
# The detector reads every bit of a marker from a bigger picture than its default (4 px per cell): with the default a marker
# whose pattern has a lone dark cell inside white is lost on a small picture. Measured 2026-11-15 (docs/ACCURACY.md): 8 finds all
# 8 markers of the sheet already at 1280x960 (20 px per marker), 4 does not; on 437 stored pictures 8 loses no real marker.
PIXELS_PER_CELL = 8

NEUTRAL, BAD, GOOD = "neutral", "bad", "good"                    # the kinds of the plate


def make_detector():
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.perspectiveRemovePixelPerCell = PIXELS_PER_CELL
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), params)


def search(detector, gray):
    """The markers found in a grey picture: {id: 4 corners (camera px)}."""
    corners, ids, _ = detector.detectMarkers(gray)
    return {} if ids is None else {int(i): c[0].astype(np.float64) for c, i in zip(corners, ids.ravel())}


def sheet_turn(found):
    """How the Vertical sheet is turned in the picture, degrees (0 = the top row 46 -> 47 runs to the right, 180 = upside down).
    Needs the four corner markers."""
    c = [found[i].mean(axis=0) for i in CORNER_IDS]
    dx, dy = (c[1] - c[0]) + (c[2] - c[3])
    return float(np.degrees(np.arctan2(dy, dx)))


def plate(found):
    """(text, kind) of the plate that says what the search found."""
    missing = [i for i in CORNER_IDS if i not in found]
    work = [name for name, ids in SHEETS[1:] if any(i in found for i in ids)]
    if not found:
        return "No markers in view", NEUTRAL
    if not any(i in found for i in SHEETS[0][1]):
        if work:
            return f"Sheet {', '.join(work)} in view. The Vertical sheet is not on it", NEUTRAL
        return "No known markers in view", NEUTRAL
    if missing:
        return f"Vertical sheet: corner marker{'s' if len(missing) > 1 else ''} {', '.join(str(i) for i in missing)} not found", BAD
    turn = sheet_turn(found)
    if abs(turn) > TURN_TOL_DEG:
        advice = "marker 46 must be top left" if abs(turn) >= 45 else f"straighten it (limit {TURN_TOL_DEG:.0f} deg)"
        return f"Vertical sheet is turned {turn:+.0f} deg: {advice}", BAD
    return "Vertical sheet: all 4 corner markers found", GOOD


def info_lines(found):
    """The lines of the Markers block that depend on the markers: how many, their size, each sheet n of m."""
    sides = [float(np.linalg.norm(c[(k + 1) % 4] - c[k])) for c in found.values() for k in range(4)]
    lines = [f"Found:    {len(found)}", f"Side:     {np.mean(sides):.1f} px" if sides else "Side:     -"]
    for name, ids in SHEETS:
        hit = sorted(i for i in found if i in ids)
        if hit:
            lines.append(f"{name}: {len(hit)} of {len(ids)}")
            missing = [i for i in ids if i not in hit]
            if missing and name == SHEETS[0][0]:
                lines.append(f"  missing: {', '.join(str(i) for i in missing)}")
    return lines


# ----- the tilt of the camera (step 2 of the transfer from tools/camera_setup2.py)
WARN = "warn"                                                    # a kind of the plate: close to the limit
MARKER_MM = 20.0
# the centres of the markers in mm from the sheet centre (x right, y down), 20 mm markers (tools/make_camera_blank_pdf.py)
MARKERS_MM = {46: (-180.0, -118.5), 47: (180.0, -118.5), 48: (180.0, 118.5), 49: (-180.0, 118.5),       # corners
              42: (0.0, -128.5), 43: (180.0, 0.0), 44: (0.0, 128.5), 45: (-180.0, 0.0)}                # middles: top, right, bottom, left
HEIGHT_MM = 780.0                      # lens to sheet (the focal length in px comes from it); will become a setting
LEVEL_GOOD_DEG, LEVEL_OK_DEG = 3.0, 6.0                          # the traffic light of the tilt (docs/DECISIONS.md 2026-11-10)
# the sheet centre must be this close to the cross (a part of the frame width: 25 px at 1280); once good it stays good up to
# the second value (a gap against flicker). Near the border of the frame the lens distortion fakes a tilt.
CENTRE_TOL_FRAC, CENTRE_KEEP_FRAC = 25 / 1280, 30 / 1280


def sheet_centre_px(found):
    """Where the centre of the Vertical sheet is seen (camera px), from the four corner markers; None without them."""
    if not all(i in found for i in CORNER_IDS):
        return None
    model = np.float32([MARKERS_MM[i] for i in CORNER_IDS])
    seen = np.float32([found[i].mean(axis=0) for i in CORNER_IDS])
    return cv2.perspectiveTransform(np.float32([[[0.0, 0.0]]]), cv2.getPerspectiveTransform(model, seen))[0, 0].astype(np.float64)


def centre_state(found, w, h, was_ok):
    """The condition for Check: the centre of the sheet is on the cross. -> (ok, text, kind)."""
    centre = sheet_centre_px(found)
    if centre is None:
        return False, "Centre: unknown (the four corner markers are needed)", NEUTRAL
    dx, dy = float(centre[0] - (w - 1) / 2), float(centre[1] - (h - 1) / 2)
    ok = bool(np.hypot(dx, dy) <= (CENTRE_KEEP_FRAC if was_ok else CENTRE_TOL_FRAC) * w)
    if ok:
        return True, "Centre: OK", GOOD
    return False, (f"Centre: move the sheet {'left' if dx >= 0 else 'right'} {abs(dx):.0f}, "
                   f"{'up' if dy >= 0 else 'down'} {abs(dy):.0f}"), BAD


def estimate_pose(found, w, h, height_mm=HEIGHT_MM):
    """Where the camera would look if it stood vertical, from the markers of the Vertical sheet.
    None: the four corner markers are not all found, or the sheet lies turned more than TURN_TOL_DEG (a pose fitted to a turned
    sheet is nonsense). Else a dict: nadir (x, y px: where the point straight below the lens is seen; the frame centre when the
    camera looks exactly down), tilt, lr, fb (degrees; positive = the point lies to the right / below the frame centre)."""
    if not all(i in found for i in CORNER_IDS) or abs(sheet_turn(found)) > TURN_TOL_DEG:
        return None
    cen = [found[i].mean(axis=0) for i in CORNER_IDS]
    half = MARKER_MM / 2
    obj, pts = [], []
    for marker_id, (mx, my) in MARKERS_MM.items():
        if marker_id in found:                                            # a middle marker may be missing
            for k, (ox, oy) in enumerate(((-half, -half), (half, -half), (half, half), (-half, half))):
                obj.append((mx + ox, my + oy, 0.0))
                pts.append(found[marker_id][k])
    obj, pts = np.array(obj), np.array(pts)
    width_px = (np.linalg.norm(cen[1] - cen[0]) + np.linalg.norm(cen[2] - cen[3])) / 2
    f = width_px / (2 * MARKERS_MM[47][0]) * height_mm
    centre = np.array([(w - 1) / 2, (h - 1) / 2])
    cam = np.array([[f, 0, centre[0]], [0, f, centre[1]], [0, 0, 1.0]])
    # iterative fit from "the camera looks straight down" (the planar solver gave the mirror tilt on small angles)
    ok, rvec, tvec = cv2.solvePnP(obj, pts, cam, None, np.zeros((3, 1)), np.array([[0.0], [0.0], [float(height_mm)]]), True,
                                  cv2.SOLVEPNP_ITERATIVE)
    if not ok:
        return None
    n = cv2.Rodrigues(rvec)[0][:, 2]
    if n[2] < 0:
        n = -n
    return {"nadir": centre + f * n[:2] / n[2], "tilt": float(np.degrees(np.arccos(n[2]))),
            "lr": float(np.degrees(np.arctan2(n[0], n[2]))), "fb": float(np.degrees(np.arctan2(n[1], n[2])))}


def side_lengths(centres):
    """Lengths (px) of the four sides between the corner marker centres: top, right, bottom, left."""
    return [float(np.linalg.norm(centres[(i + 1) % 4] - centres[i])) for i in range(4)]


def level_plate(tilt, lr, fb):
    """(text, kind) for the result of a Check: the tilt with the traffic light (green below 3 deg, yellow below 6, red above)."""
    state, kind = ("OK", GOOD) if tilt < LEVEL_GOOD_DEG else ("close", WARN) if tilt < LEVEL_OK_DEG else ("tilted", BAD)
    return f"Check {state}: tilt {tilt:.1f} deg\nleft-right {lr:+.1f}, front-back {fb:+.1f}", kind
