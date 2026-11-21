"""Fewer nodes for the DXF: the measured contour (a chain of points every ~0.1 mm) -> a short polygon.

plain      Douglas-Peucker with a tolerance (what was done before). A corner of the piece is rounded by the optics, the
           blur and the 50 % edge level (about 0.5-1 mm), so it comes out as two or three nodes lying inside the true corner.
corners    the corners are found (a turn of more than CORNER_DEG over an arm of ARM_MM), the straight edges on both sides of a
           corner are fitted by lines and the node is put at their intersection: one node at the true corner. Between the
           corners (arcs, long edges) Douglas-Peucker with the tolerance, the rounded zone round every corner is left out.
straight   as corners, and an edge between two corners that is straight (STRAIGHT_MM) is one line fitted to the whole
           edge: no nodes on it, the corner is the intersection of the lines of its two edges (2026-11-20).
Everything in sheet mm; the polygon is open (the last node is not a repeat of the first).
"""
import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, maximum_filter1d

STEP_MM = 0.2                    # the contour is resampled at this step before everything else
ARM_MM = 2.0                     # the turn at a point is measured between points this far before and after it
CORNER_DEG = 25.0                # a turn above this is a corner
SMOOTH_MM = 0.5                  # the contour is smoothed along itself (Gauss, sigma) before the nodes between the corners are chosen:
                                 # the grid lines crossing the edge make it tremble by +-0.2 mm
ROUND_MM = 1.5                   # round a corner the contour is rounded by the blur and the smoothing: these points are not used between corners
FIT_FROM_MM, FIT_TO_MM = 1.5, 6.0    # the edge points used to fit the line on each side of a corner (distance from the corner)
MAX_SHIFT_MM = 3.0               # the intersection further than this from the rounded corner is not trusted
MAX_FIT_RMS_MM = 0.25            # an "edge" that does not look like a line (an arc) is not used for the intersection
MIN_SIN = 0.15                   # the two lines are too parallel to intersect

STRAIGHT_MM = 0.3                # straight: an edge between two corners whose smoothed points stay this close to one line is
                                 # one straight segment (the trembling of the grid lines, +-0.2 mm, does not make nodes on it)
STRAIGHT_PCT = 98                # ... "stay close": this percentile of the distances (a single bad point does not count)

MODES = {"plain": ("Plain", 0.1), "corners": ("Corners", 0.1), "corners2": ("Corners+", 0.2),
         "straight": ("Straight", 0.1)}                                         # key -> (button text, tolerance mm)


def _resample(c, h):
    d = np.linalg.norm(np.roll(c, -1, axis=0) - c, axis=1)
    s = np.concatenate([[0.0], np.cumsum(d)])
    ring = np.vstack([c, c[:1]])
    t = np.arange(0.0, s[-1], h)
    return np.column_stack([np.interp(t, s, ring[:, 0]), np.interp(t, s, ring[:, 1])])


def _find_corners(r, k, corner_deg=CORNER_DEG):
    a, b = r - np.roll(r, k, axis=0), np.roll(r, -k, axis=0) - r
    ang = np.abs(np.degrees(np.arctan2(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0], a[:, 0] * b[:, 0] + a[:, 1] * b[:, 1])))
    peak = (ang == maximum_filter1d(ang, size=2 * k + 1, mode="wrap")) & (ang > corner_deg)
    corners = []
    for i in np.where(peak)[0]:                                  # a flat top gives several neighbours: keep the first
        if not corners or i - corners[-1] > k:
            corners.append(int(i))
    if len(corners) > 1 and len(r) - corners[-1] + corners[0] <= k:
        corners.pop()
    return corners


def _fit_line(p):
    mean = p.mean(axis=0)
    _, _, vt = np.linalg.svd(p - mean, full_matrices=False)
    d = vt[0]
    off = (p - mean) @ np.array([-d[1], d[0]])
    return mean, d, float(np.sqrt(np.mean(off ** 2)))


def _refine(r, i, prev_gap, next_gap):
    """The corner at index i of the resampled contour moved to the intersection of the edges on both sides, or None."""
    m = len(r)
    j0, j1 = int(round(FIT_FROM_MM / STEP_MM)), int(round(FIT_TO_MM / STEP_MM))
    after_to, before_to = min(j1, next_gap // 2), min(j1, prev_gap // 2)
    if after_to - j0 < 5 or before_to - j0 < 5:
        return None
    after = r[[(i + j) % m for j in range(j0, after_to)]]
    before = r[[(i - j) % m for j in range(j0, before_to)]]
    pb, db, rb = _fit_line(before)
    pa, da, ra = _fit_line(after)
    det = db[0] * (-da[1]) - (-da[0]) * db[1]
    if max(rb, ra) > MAX_FIT_RMS_MM or abs(det) < MIN_SIN:
        return None
    s, _ = np.linalg.solve(np.array([[db[0], -da[0]], [db[1], -da[1]]]), pa - pb)
    x = pb + s * db
    return x if np.linalg.norm(x - r[i]) <= MAX_SHIFT_MM else None


def _line_of(points, straight_mm=STRAIGHT_MM):
    """The line of an edge, fitted without the pull of a few bad points (Huber), and whether the edge is straight."""
    if len(points) < 10:
        return None
    vx, vy, x0, y0 = cv2.fitLine(points.astype(np.float32).reshape(-1, 1, 2), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
    p, d = np.array([x0, y0], np.float64), np.array([vx, vy], np.float64)
    off = np.abs((points - p) @ np.array([-d[1], d[0]]))
    return (p, d) if np.percentile(off, STRAIGHT_PCT) <= straight_mm else None


def _cross(a, b):
    (pa, da), (pb, db) = a, b
    det = da[0] * (-db[1]) - (-db[0]) * da[1]
    if abs(det) < MIN_SIN:
        return None
    s, _ = np.linalg.solve(np.array([[da[0], -db[0]], [da[1], -db[1]]]), pb - pa)
    return pa + s * da


def _straight(c, tol, corner_deg=CORNER_DEG, straight_mm=STRAIGHT_MM):
    """The mode straight (see the head of the file)."""
    r = _resample(c, STEP_MM)
    m = len(r)
    smooth = gaussian_filter1d(r, SMOOTH_MM / STEP_MM, axis=0, mode="wrap")
    corners = _find_corners(r, int(round(ARM_MM / STEP_MM)), corner_deg)
    if len(corners) < 2:
        return None
    e = int(round(ROUND_MM / STEP_MM))
    n = len(corners)
    gaps = [((corners[(q + 1) % n] - corners[q]) % m or m) for q in range(n)]
    mids = [smooth[[(corners[q] + j) % m for j in range(e, gaps[q] - e)]] if gaps[q] > 2 * e else np.empty((0, 2)) for q in range(n)]
    lines = [_line_of(mid, straight_mm) for mid in mids]                      # the edge q runs from the corner q to the corner q + 1
    nodes = []
    for q, i in enumerate(corners):
        before, after = lines[q - 1], lines[q]
        x = _cross(before, after) if before is not None and after is not None else None
        if x is not None and np.linalg.norm(x - r[i]) > MAX_SHIFT_MM:
            x = None
        if x is None:
            x = _refine(r, i, gaps[q - 1], gaps[q])
        nodes.append(smooth[i] if x is None else x)
    out = []
    for q in range(n):
        if lines[q] is not None:                                 # a straight edge: the corner node only
            out.append(nodes[q][None])
            continue
        seq = np.vstack([nodes[q][None], mids[q], nodes[(q + 1) % n][None]]).astype(np.float32)
        poly = cv2.approxPolyDP(seq.reshape(-1, 1, 2), tol, False)[:, 0, :].astype(np.float64)
        out.append(poly[:-1])
    return np.vstack(out)


def simplify_contour(contour_mm, mode, tol=None, corner_deg=CORNER_DEG, straight_mm=STRAIGHT_MM):
    """contour_mm: N x 2 closed chain in sheet mm; mode: a key of MODES. Returns the nodes, M x 2, in sheet mm.
    tol, corner_deg, straight_mm: the sliders of Measure (None tol: the tolerance of the mode)."""
    c = np.asarray(contour_mm, np.float64)
    tol = MODES[mode][1] if tol is None else tol
    if mode == "plain":
        return cv2.approxPolyDP(c.astype(np.float32).reshape(-1, 1, 2), tol, True)[:, 0, :].astype(np.float64)
    if mode == "straight":
        nodes = _straight(c, tol, corner_deg, straight_mm)
        if nodes is not None:
            return nodes
    r = _resample(c, STEP_MM)
    m = len(r)
    smooth = gaussian_filter1d(r, SMOOTH_MM / STEP_MM, axis=0, mode="wrap")        # the trembling of the edge out
    k = int(round(ARM_MM / STEP_MM))
    corners = _find_corners(r, k, corner_deg)
    if not corners:
        return cv2.approxPolyDP(smooth.astype(np.float32).reshape(-1, 1, 2), tol, True)[:, 0, :].astype(np.float64)
    nodes = []
    for n, i in enumerate(corners):
        prev_gap = (i - corners[n - 1]) % m or m
        next_gap = (corners[(n + 1) % len(corners)] - i) % m or m
        x = _refine(r, i, prev_gap, next_gap)
        nodes.append(smooth[i] if x is None else x)
    out = []
    e = int(round(ROUND_MM / STEP_MM))
    for n, i in enumerate(corners):
        nxt = corners[(n + 1) % len(corners)]
        gap = (nxt - i) % m or m
        mid = smooth[[(i + j) % m for j in range(e, gap - e)]] if gap > 2 * e else np.empty((0, 2))
        seq = np.vstack([nodes[n][None], mid, nodes[(n + 1) % len(corners)][None]]).astype(np.float32)
        poly = cv2.approxPolyDP(seq.reshape(-1, 1, 2), tol, False)[:, 0, :].astype(np.float64)
        out.append(poly[:-1])
    return np.vstack(out)


def to_lying(poly):
    """Sheet mm (x right, y down, upright sheet) -> DXF mm as the piece lies (x = sheet y, y = sheet x), as analyze_sheets.dxf_points."""
    return np.column_stack([poly[:, 1], poly[:, 0]])


def polygon_area(p):
    return 0.5 * abs(float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1))))
