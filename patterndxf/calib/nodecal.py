"""nodecal: camera calibration by the nodes of a printed 10 mm grid sheet - the module to bring into the program.

Self-contained (numpy, OpenCV with aruco, SciPy). No printing, no files except save()/load(). The algorithms are the ones
proven in the scripts of the research project Nodes (markers.py, nodes.py, accuracy.py, normalize.py) - copied, not changed.

    sheets = [Sheet.load(p) for p in glob("data/sheets/*.json")]
    cal = calibrate(gray, sheets)                # gray: the median of N frames of the empty sheet, uint8
    mm = cal.to_mm(px)                           # image points (N, 2) -> sheet mm (N, 2)
    px = cal.to_px(mm)                           # sheet mm -> image points
    top = cal.redraw(gray, ppm)                  # the sheet seen from above, ppm pixels per mm (box: cal.box())
    mapx, mapy = cal.remap_tables(ppm)           # the same as tables: cv2.remap(frame, mapx, mapy, INTER_CUBIC)
    cal.holdout()                                # accuracy: 1/5 of the nodes hidden in turn, mm (median, p95, max)
    cal.check_redraw(gray, ppm)                  # nodes looked for again in the redrawn picture, mm
    cal.save("calibration.json"); Calibration.load("calibration.json")

Steps of calibrate():
  1 markers (ArUco) -> which sheet (by the IDs), whole sheet in the frame?
  2 coarse model from the marker corners: homography, or one-view lens model with k1 when that fits better (fisheye-like)
  3 nodes ring by ring from the marker blocks; each node predicted from its found neighbours; detector "cross":
    profiles across both lines (pieces on both sides, the crossing left out), line centre = darkness centroid near the
    dark peak nearest the prediction, straight line per side pair (total least squares), node = intersection; 2 passes
  4 model by the nodes (inner nodes only): one-view lens model (K, k1 k2 p1 p2 k3) + thin-plate field of the residuals,
    both directions (mm -> px for redrawing, px -> mm for measuring points)
"""
import json

import cv2
import numpy as np
from scipy.interpolate import RBFInterpolator

# detector constants (cells = grid steps in the image)
BLOB = 0.15        # crossing blob half size, cells (no centres taken there)
REACH = 0.5        # pieces reach this far from the node, cells
ACROSS = 0.3       # profile half length across the line, cells
MAX_JUMP = 0.2     # a found node may differ from its prediction by this much, cells
MAX_SCATTER = 0.5  # px: line centres around their straight line
MIN_PTS = 8        # centres per line
RBF_NEIGHBOURS = 40
MARGIN_MM = 15.0   # border around the grid in box()
FIELD_STEP_MM = 2.0
FORMAT = "nodecal 1"


class CalibrationError(Exception):
    pass


# ======================================================================== sheet
class Sheet:
    """A printed sheet: the JSON of data/sheets (grid, markers, blocks without lines), mm, page coordinates, y down."""

    def __init__(self, spec):
        self.spec = spec
        self.name = spec["name"]
        self.dictionary = spec["dictionary"]
        self.cell = float(spec["cell_mm"])
        g = spec["grid"]
        self.x0, self.y0, self.nx, self.ny = g["x0"], g["y0"], g["nx"], g["ny"]
        self.markers = {int(k): v for k, v in spec["markers"].items()}
        self.blocks = [m["block"] for m in self.markers.values()]

    @staticmethod
    def load(path):
        return Sheet(json.load(open(path, encoding="utf-8")))

    def mm(self, i, j):
        return np.array([self.x0 + i * self.cell, self.y0 + j * self.cell])

    def inside_block(self, x, y):
        return any(b[0] < x < b[2] and b[1] < y < b[3] for b in self.blocks)

    def sides(self, i, j):
        """Which of the 4 pieces of the cross exist: ((+x, -x), (+y, -y)) - in the grid and not into a marker block."""
        def ok(di, dj):
            x, y = self.mm(i, j) + np.array([di, dj]) * self.cell * 0.3
            return (self.x0 <= x <= self.x0 + self.nx * self.cell and self.y0 <= y <= self.y0 + self.ny * self.cell
                    and not self.inside_block(x, y))
        return (ok(1, 0), ok(-1, 0)), (ok(0, 1), ok(0, -1))

    def kind(self, i, j):
        """inner: a full cross; edge: on the outer frame; block: on / at a marker block. Only inner nodes are used."""
        x, y = self.mm(i, j)
        if x in (self.x0, self.x0 + self.nx * self.cell) or y in (self.y0, self.y0 + self.ny * self.cell):
            return "edge"
        on_block = any(b[0] <= x <= b[2] and b[1] <= y <= b[3] for b in self.blocks)
        s = self.sides(i, j)
        return "block" if on_block or not (all(s[0]) and all(s[1])) else "inner"

    def nodes(self):
        """(i, j) of every node: not inside a block, both lines present."""
        out = []
        for j in range(self.ny + 1):
            for i in range(self.nx + 1):
                if self.inside_block(*self.mm(i, j)):
                    continue
                s = self.sides(i, j)
                if any(s[0]) and any(s[1]):
                    out.append((i, j))
        return out

    def box(self, margin=MARGIN_MM):
        return (self.x0 - margin, self.y0 - margin, self.x0 + self.nx * self.cell + margin,
                self.y0 + self.ny * self.cell + margin)


# ======================================================================== markers, coarse model
def find_markers(gray, sheets):
    """-> (sheet, {id: 4x2 corners}) of the sheet with most markers found, or (None, {})."""
    best, sheet, cache = {}, None, {}
    for s in sheets:
        if s.dictionary not in cache:
            p = cv2.aruco.DetectorParameters()
            p.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
            p.cornerRefinementWinSize = max(3, int(min(gray.shape) / 400))
            det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, s.dictionary)), p)
            c, ids, _ = det.detectMarkers(gray)
            cache[s.dictionary] = {} if ids is None else {int(i): q.reshape(4, 2) for i, q in zip(ids.ravel(), c)}
        got = {i: q for i, q in cache[s.dictionary].items() if i in s.markers}
        if len(got) > len(best):
            best, sheet = got, s
    return sheet, best


class Coarse:
    """sheet mm -> image px from the marker corners: homography, or a one-view lens with k1 (whichever fits better)."""

    def __init__(self, sheet, found, size):
        w, h = size
        ids = sorted(found)
        src = np.array([sheet.markers[i]["corners"][k] for i in ids for k in range(4)], np.float64)
        dst = np.array([found[i][k] for i in ids for k in range(4)], np.float64)
        self.H, _ = cv2.findHomography(src, dst, 0)
        e_h = np.linalg.norm(cv2.perspectiveTransform(src.reshape(-1, 1, 2), self.H).reshape(-1, 2) - dst, axis=1)
        obj = np.hstack([src, np.zeros((len(src), 1))]).astype(np.float32)
        K0 = np.array([[w, 0, w / 2], [0, w, h / 2], [0, 0, 1]], np.float64)
        flags = (cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_PRINCIPAL_POINT | cv2.CALIB_FIX_ASPECT_RATIO |
                 cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3)
        _, self.K, self.d, rv, tv = cv2.calibrateCamera([obj], [dst.astype(np.float32)], (w, h), K0, np.zeros(5),
                                                        flags=flags)
        self.rv, self.tv = rv[0], tv[0]
        e_l = np.linalg.norm(cv2.projectPoints(obj, self.rv, self.tv, self.K, self.d)[0].reshape(-1, 2) - dst, axis=1)
        self.use_lens = np.sqrt((e_l ** 2).mean()) < np.sqrt((e_h ** 2).mean())
        self.residual_px = float(np.sqrt(((e_l if self.use_lens else e_h) ** 2).mean()))

    def __call__(self, mm):
        mm = np.atleast_2d(np.asarray(mm, np.float64))
        if self.use_lens:
            return cv2.projectPoints(np.hstack([mm, np.zeros((len(mm), 1))]), self.rv, self.tv, self.K,
                                     self.d)[0].reshape(-1, 2)
        return cv2.perspectiveTransform(mm.reshape(-1, 1, 2), self.H).reshape(-1, 2)


# ======================================================================== the cross detector
def _centroids(P, core):
    """Line centre in every profile (row of P) across the line: darkness centroid around the dark peak nearest the
    middle (= the predicted line; print near the sheet edge can be darker than the line), +-core samples."""
    if len(P) == 0:
        return np.zeros(0), np.zeros(0)
    ns = P.shape[1]
    d = P.max(1, keepdims=True) - P
    peak = d.max(1)
    cand = np.zeros_like(d, bool)
    cand[:, 1:-1] = (d[:, 1:-1] >= d[:, :-2]) & (d[:, 1:-1] >= d[:, 2:]) & (d[:, 1:-1] > 0.5 * peak[:, None])
    dist = np.where(cand, np.abs(np.arange(ns) - (ns - 1) / 2), np.inf)
    k0 = np.argmin(dist, 1)
    none = ~cand.any(1)
    k0[none] = np.argmax(d[none], 1)
    r = np.arange(len(P))
    pk = d[r, k0]
    idx = k0[:, None] + np.arange(-core, core + 1)
    valid = (idx >= 0) & (idx < ns)
    idxc = np.clip(idx, 0, ns - 1)
    dd = np.clip(d[r[:, None], idxc] - 0.3 * pk[:, None], 0, None) * valid
    sm = dd.sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        k = (dd * idxc).sum(1) / sm
    k[sm <= 0] = np.nan
    return k, np.where(sm > 0, pk, 0.0)


def _group_fit(p, gid, ng, keep):
    """Total least squares line per group: mean, direction, scatter (rms across), count, distances across."""
    w = keep.astype(np.float64)
    n = np.bincount(gid, w, ng)
    nn = np.maximum(n, 1)
    mx = np.bincount(gid, w * p[:, 0], ng) / nn
    my = np.bincount(gid, w * p[:, 1], ng) / nn
    dx, dy = p[:, 0] - mx[gid], p[:, 1] - my[gid]
    th = 0.5 * np.arctan2(2 * np.bincount(gid, w * dx * dy, ng),
                          np.bincount(gid, w * dx * dx, ng) - np.bincount(gid, w * dy * dy, ng))
    d = np.stack([np.cos(th), np.sin(th)], 1)
    r = -dx * d[gid, 1] + dy * d[gid, 0]
    return np.stack([mx, my], 1), d, np.sqrt(np.bincount(gid, w * r * r, ng) / nn), n, r


def detect_crosses(gray_f32, C, DX, DY, CELL, SIDES):
    """Crosses at the points C (N, 2) with the image directions DX, DY (unit, N x 2) of the two lines, cell size CELL
    (px, N or a number) and the existing pieces SIDES (N x ((+x, -x), (+y, -y))).
    -> nodes (N, 2) with NaN where not found, [info dict or reason string]."""
    N = len(C)
    CELL = np.broadcast_to(np.asarray(CELL, np.float64), (N,))
    rows_p, rows_gid, rows_depth, groups = [], [], [], {}
    for i, cl in enumerate(CELL):                                   # equal profile geometry -> one remap
        groups.setdefault((len(np.arange(-ACROSS * cl, ACROSS * cl + 0.25, 0.5)),
                           len(np.arange(BLOB * cl, REACH * cl, 1.0)), max(3, int(round(0.12 * cl / 0.5)))), []).append(i)
    for (ns, nT, core), idx in groups.items():
        idx = np.array(idx)
        cl = CELL[idx]
        for L, D in ((0, DX), (1, DY)):
            d = D[idx]
            nv = np.stack([-d[:, 1], d[:, 0]], 1)
            tt = np.repeat(BLOB * cl[:, None] + np.arange(nT)[None, :], 2, axis=1)       # along: t, both sides
            ss = np.tile(np.array([1.0, -1.0]), nT)[None, :].repeat(len(idx), 0)
            ex = np.tile(np.array([[SIDES[i][L][0], SIDES[i][L][1]] for i in idx], bool), (1, nT))
            bases = C[idx][:, None, :] + (ss * tt)[..., None] * d[:, None, :]
            s0 = -ACROSS * cl
            sv = s0[:, None] + 0.5 * np.arange(ns)[None, :]                              # across, 0.5 px step
            pts = (bases[:, :, None, :] + sv[:, None, :, None] * nv[:, None, None, :]).reshape(-1, ns, 2)
            pts = pts.astype(np.float32)
            P = np.empty(pts.shape[:2], np.float32)
            for a0 in range(0, len(pts), 30000):                    # remap maps stay below 32767 rows
                q = pts[a0:a0 + 30000]
                P[a0:a0 + 30000] = cv2.remap(gray_f32, np.ascontiguousarray(q[..., 0]), np.ascontiguousarray(q[..., 1]),
                                             cv2.INTER_LINEAR, borderValue=float("nan"))
            gid = np.repeat(idx * 2 + L, 2 * nT)
            ok = ex.ravel() & np.all(np.isfinite(P), axis=1)
            k, pk = _centroids(P[ok], core)
            good = np.isfinite(k)
            rows_p.append(bases.reshape(-1, 2)[ok][good] +
                          (np.repeat(s0, 2 * nT)[ok][good] + k[good] * 0.5)[:, None] * np.repeat(nv, 2 * nT, 0)[ok][good])
            rows_gid.append(gid[ok][good])
            rows_depth.append(pk[good])
    p = np.concatenate(rows_p) if rows_p else np.zeros((0, 2))
    gid = np.concatenate(rows_gid).astype(np.int64) if rows_gid else np.zeros(0, np.int64)
    depth = np.concatenate(rows_depth) if rows_depth else np.zeros(0)
    ng = 2 * N
    cnt = np.bincount(gid, minlength=ng)
    order = np.lexsort((depth, gid))
    starts = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    med, has, ds = np.zeros(ng), cnt > 0, depth[order]
    med[has] = 0.5 * (ds[starts[has] + (cnt[has] - 1) // 2] + ds[starts[has] + cnt[has] // 2])
    keep = depth > 0.5 * med[gid]                                    # a missing side (T / L) has no depth
    cnt2 = np.bincount(gid, keep.astype(np.float64), ng)
    m, d, sc, _, r = _group_fit(p, gid, ng, keep)
    keep2 = keep & (np.abs(r) < np.maximum(3 * sc, 0.3)[gid])         # stray centres out, refit
    m, d, sc, n2, _ = _group_fit(p, gid, ng, keep2)
    few = (cnt < MIN_PTS) | (cnt2 < MIN_PTS)
    m1, d1, m2, d2 = m[0::2], d[0::2], m[1::2], d[1::2]
    det = -d1[:, 0] * d2[:, 1] + d2[:, 0] * d1[:, 1]
    with np.errstate(invalid="ignore", divide="ignore"):
        t1 = ((m2 - m1)[:, 0] * -d2[:, 1] + d2[:, 0] * (m2 - m1)[:, 1]) / det
    nodes, out = np.full((N, 2), np.nan), []
    for i in range(N):
        a, b = 2 * i, 2 * i + 1
        if few[a] or few[b]:
            out.append("few centres")
        elif sc[a] > MAX_SCATTER or sc[b] > MAX_SCATTER:
            out.append(f"scatter {max(sc[a], sc[b]):.2f}")
        elif abs(det[i]) < 1e-12:
            out.append("parallel")
        else:
            nodes[i] = m1[i] + t1[i] * d1[i]
            out.append({"scatter": [round(float(sc[a]), 4), round(float(sc[b]), 4)], "n": [int(n2[a]), int(n2[b])]})
    return nodes, out


def find_nodes(gray, sheet, coarse, found_markers):
    """All nodes, ring by ring from the marker blocks. -> {(i, j): (px (2,), kind)}, {(i, j): reason}."""
    g = gray.astype(np.float32)
    allowed = set(sheet.nodes())
    found, rejected = {}, {}

    def predict(i, j):
        preds = []
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = (i + di, j + dj), (i + 2 * di, j + 2 * dj)
            if a in found and b in found:
                preds.append(2 * found[a] - found[b])                # the line continued
            elif a in found:
                preds.append(found[a] + (coarse(sheet.mm(i, j))[0] - coarse(sheet.mm(*a))[0]))
        return np.mean(preds, 0) if preds else coarse(sheet.mm(i, j))[0]

    seeds = []
    for b in sheet.blocks:                                          # the corners of every marker block

        for x, y in ((b[0], b[1]), (b[2], b[1]), (b[0], b[3]), (b[2], b[3])):
            ij = (int(round((x - sheet.x0) / sheet.cell)), int(round((y - sheet.y0) / sheet.cell)))
            if ij in allowed:
                seeds.append(ij)
    frontier, queued = list(dict.fromkeys(seeds)), set(seeds)
    while frontier:
        P0 = np.array([predict(*ij) for ij in frontier])
        A = coarse(np.concatenate([np.array([sheet.mm(i, j), sheet.mm(i + 1, j), sheet.mm(i, j + 1)])
                                   for i, j in frontier])).reshape(-1, 3, 2)
        DX, DY = A[:, 1] - A[:, 0], A[:, 2] - A[:, 0]
        LX, LY = np.linalg.norm(DX, axis=1), np.linalg.norm(DY, axis=1)
        DX, DY, CELL = DX / LX[:, None], DY / LY[:, None], (LX + LY) / 2
        SD = [sheet.sides(*ij) for ij in frontier]
        n1, r1 = detect_crosses(g, P0, DX, DY, CELL, SD)
        ok1 = np.isfinite(n1[:, 0])
        n2, _ = detect_crosses(g, np.where(ok1[:, None], n1, P0), DX, DY, CELL, SD)      # centred on the node
        nxt = []
        for k, ij in enumerate(frontier):
            if not ok1[k]:
                rejected[ij] = r1[k]
                continue
            node = n2[k] if np.isfinite(n2[k, 0]) else n1[k]
            if np.linalg.norm(node - P0[k]) > MAX_JUMP * CELL[k]:
                rejected[ij] = f"jump {np.linalg.norm(node - P0[k]) / CELL[k]:.2f} cell"
                continue
            found[ij] = node
            for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (ij[0] + di, ij[1] + dj)
                if nb in allowed and nb not in queued:
                    queued.add(nb)
                    nxt.append(nb)
        frontier = nxt
    return {ij: (p, sheet.kind(*ij)) for ij, p in found.items()}, rejected


# ======================================================================== the model by the nodes
class _Lens:
    """One-view camera model sheet (mm, z=0) <-> image (px): K, k1 k2 p1 p2 k3, pose."""

    def __init__(self, mm, px, size, K=None, dist=None, rvec=None, tvec=None):
        if K is None:
            w, h = size
            obj = np.hstack([mm, np.zeros((len(mm), 1))]).astype(np.float32)
            K0 = np.array([[w, 0, w / 2], [0, w, h / 2], [0, 0, 1]], np.float64)
            _, K, dist, rv, tv = cv2.calibrateCamera([obj], [px.astype(np.float32)], (w, h), K0, np.zeros(5),
                                                     flags=cv2.CALIB_USE_INTRINSIC_GUESS)
            rvec, tvec = rv[0], tv[0]
        self.K, self.dist = np.asarray(K, np.float64), np.asarray(dist, np.float64).ravel()
        self.rvec, self.tvec = np.asarray(rvec, np.float64).reshape(3, 1), np.asarray(tvec, np.float64).reshape(3, 1)
        R, _ = cv2.Rodrigues(self.rvec)
        self.Hinv = np.linalg.inv(np.column_stack([R[:, 0], R[:, 1], self.tvec.ravel()]))

    def to_px(self, mm):
        return cv2.projectPoints(np.hstack([mm, np.zeros((len(mm), 1))]), self.rvec, self.tvec, self.K,
                                 self.dist)[0].reshape(-1, 2)

    def to_mm(self, px):
        n = cv2.undistortPoints(np.asarray(px, np.float64).reshape(-1, 1, 2), self.K, self.dist).reshape(-1, 2)
        q = (self.Hinv @ np.column_stack([n, np.ones(len(n))]).T).T
        return q[:, :2] / q[:, 2:3]


def _stats(err):
    d = np.linalg.norm(np.asarray(err), axis=1)
    d = d[np.isfinite(d)]
    return {"median": float(np.median(d)), "p95": float(np.percentile(d, 95)), "max": float(d.max()), "n": int(len(d))}


class Calibration:
    """The model by the nodes. Built from the inner nodes (sheet mm <-> image px); everything else is derived."""

    def __init__(self, mm, px, size, sheet, meta=None, lens=None):
        self.mm, self.px = np.asarray(mm, np.float64), np.asarray(px, np.float64)
        self.size, self.sheet, self.meta = tuple(size), sheet, dict(meta or {})
        self.lens = _Lens(self.mm, self.px, self.size, **(lens or {}))
        m = self.lens.to_mm(self.px)                                 # px -> mm: lens, then the field in mm
        self._inv = RBFInterpolator(m, self.mm - m, kernel="thin_plate_spline", neighbors=RBF_NEIGHBOURS)
        self._fwd = RBFInterpolator(self.mm, self.px - self.lens.to_px(self.mm), kernel="thin_plate_spline",
                                    neighbors=RBF_NEIGHBOURS)                # mm -> px: lens, then the field in px

    # ----- points
    def to_mm(self, px):
        m = self.lens.to_mm(np.atleast_2d(px))
        return m + self._inv(m)

    def to_px(self, mm):
        mm = np.atleast_2d(np.asarray(mm, np.float64))
        return self.lens.to_px(mm) + self._fwd(mm)

    def px_per_mm(self):
        """The camera's own resolution on the sheet: median distance of neighbouring nodes / cell."""
        key = {tuple(np.round(p, 6)): q for p, q in zip(self.mm, self.px)}
        c = self.sheet.cell
        d = [np.linalg.norm(key[k2] - q) for k, q in key.items()
             for k2 in ((k[0] + c, k[1]), (k[0], k[1] + c)) if k2 in key]
        return float(np.median(d)) / c

    # ----- pictures
    def box(self):
        return self.sheet.box()

    def remap_tables(self, ppm):
        """For cv2.remap: output pixel (u, v) = sheet mm (box x0 + u / ppm, box y0 + v / ppm)."""
        x0, y0, x1, y1 = self.box()
        W, H = int((x1 - x0) * ppm), int((y1 - y0) * ppm)
        gx = np.arange(x0, x1 + FIELD_STEP_MM, FIELD_STEP_MM)
        gy = np.arange(y0, y1 + FIELD_STEP_MM, FIELD_STEP_MM)
        X, Y = np.meshgrid(gx, gy)
        P = self.to_px(np.column_stack([X.ravel(), Y.ravel()])).reshape(len(gy), len(gx), 2).astype(np.float32)
        iu = (np.arange(W, dtype=np.float32) / (ppm * FIELD_STEP_MM))[None, :].repeat(H, 0)
        iv = (np.arange(H, dtype=np.float32) / (ppm * FIELD_STEP_MM))[:, None].repeat(W, 1)
        return (cv2.remap(np.ascontiguousarray(P[..., 0]), iu, iv, cv2.INTER_LINEAR),
                cv2.remap(np.ascontiguousarray(P[..., 1]), iu, iv, cv2.INTER_LINEAR))

    def redraw(self, img, ppm, tables=None):
        mx, my = tables or self.remap_tables(ppm)
        return cv2.remap(img, mx, my, cv2.INTER_CUBIC, borderValue=0)

    # ----- checks
    def holdout(self, folds=5, seed=0):
        """1/folds of the nodes hidden in turn; the model from the rest measures them: error in mm."""
        fold = np.random.default_rng(seed).integers(0, folds, len(self.mm))
        err = np.zeros_like(self.mm)
        for k in range(folds):
            tr, te = fold != k, fold == k
            lens = _Lens(self.mm[tr], self.px[tr], self.size)
            m_tr = lens.to_mm(self.px[tr])
            field = RBFInterpolator(m_tr, self.mm[tr] - m_tr, kernel="thin_plate_spline", neighbors=RBF_NEIGHBOURS)
            m_te = lens.to_mm(self.px[te])
            err[te] = m_te + field(m_te) - self.mm[te]
        return _stats(err)

    def check_redraw(self, gray, ppm):
        """The nodes looked for again in the redrawn picture: deviation from the ideal lattice, mm ("by the nodes")."""
        top = self.redraw(gray, ppm).astype(np.float32)
        x0, y0 = self.box()[:2]
        C = np.column_stack([(self.mm[:, 0] - x0) * ppm, (self.mm[:, 1] - y0) * ppm])
        DX, DY = np.tile([1.0, 0.0], (len(C), 1)), np.tile([0.0, 1.0], (len(C), 1))
        full = [((True, True), (True, True))] * len(C)
        n1, _ = detect_crosses(top, C, DX, DY, self.sheet.cell * ppm, full)
        ok = np.isfinite(n1[:, 0])
        n2, _ = detect_crosses(top, np.where(ok[:, None], n1, C), DX, DY, self.sheet.cell * ppm, full)
        node = np.where(np.isfinite(n2[:, :1]), n2, n1)
        node[~ok] = np.nan
        return _stats((node - C) / ppm)

    # ----- save / load
    def to_json(self):
        L = self.lens
        return {"format": FORMAT, "sheet": self.sheet.spec, "size": list(self.size), "meta": self.meta,
                "lens": {"K": L.K.tolist(), "dist": L.dist.tolist(), "rvec": L.rvec.ravel().tolist(),
                         "tvec": L.tvec.ravel().tolist()},
                "nodes": [{"mm": [float(a), float(b)], "px": [float(c), float(d)]}     # full precision: rounding
                          for (a, b), (c, d) in zip(self.mm, self.px)]}               # to 4 digits moved to_mm 3e-5 mm

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_json(), f, indent=0)

    @staticmethod
    def load(path):
        j = json.load(open(path, encoding="utf-8"))
        if j.get("format") != FORMAT:
            raise CalibrationError(f"{path}: not a {FORMAT} file")
        return Calibration([n["mm"] for n in j["nodes"]], [n["px"] for n in j["nodes"]], j["size"], Sheet(j["sheet"]),
                           j.get("meta"), lens=j["lens"])


# ======================================================================== all of it
def calibrate(gray, sheets, meta=None):
    """The median picture of the empty sheet -> Calibration. Raises CalibrationError when it cannot be done."""
    if gray.ndim != 2:
        raise CalibrationError("a gray picture is needed")
    sheet, found = find_markers(gray, sheets)
    if sheet is None:
        raise CalibrationError("no marker of a known sheet")
    coarse = Coarse(sheet, found, gray.shape[::-1])
    nodes, rejected = find_nodes(gray, sheet, coarse, found)
    inner = [(ij, p) for ij, (p, k) in sorted(nodes.items()) if k == "inner"]
    n_inner = sum(1 for ij in sheet.nodes() if sheet.kind(*ij) == "inner")
    if len(inner) < 0.5 * n_inner:
        raise CalibrationError(f"only {len(inner)} of {n_inner} inner nodes found")
    info = {"sheet": sheet.name, "markers_found": sorted(found), "markers_expected": sorted(sheet.markers),
            "whole_sheet": sorted(found) == sorted(sheet.markers), "coarse_model": "lens_k1" if coarse.use_lens else
            "homography", "coarse_residual_px": round(coarse.residual_px, 3), "inner_found": len(inner),
            "inner_expected": n_inner, "nodes_found": len(nodes), "rejected": len(rejected)}
    cal = Calibration([sheet.mm(*ij) for ij, _ in inner], [p for _, p in inner], gray.shape[::-1], sheet,
                      {**(meta or {}), **info})
    cal.all_nodes = nodes                                            # every node with its kind (not saved)
    return cal
