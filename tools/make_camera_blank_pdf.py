"""Make the camera set-up blank (A3 landscape PDF) for Camera Setup and Camera Lab: frame, diagonals, axes, circles,
grid, 8 ArUco markers.

Usage: python tools/make_camera_blank_pdf.py [--out out/camera_blank_A3.pdf]

Print on white paper at 100% ("Actual size", A3 landscape), lay it on a flat rigid board, then measure with a
calibrated ruler the distances between the marker centres (nominal values are printed by this script).
Content (mm, from the page centre): frame 310 x 232.5 (4:3 like the sensor), thin grid every 20 mm, diagonals
(broken at the corner circles), axes, big circle 200 mm, five sharpness circles 40 mm (centre + four corners),
eight ArUco markers 4x4 (DICT_4X4_50), all 20 mm: corners 46 top-left, 47 top-right, 48 bottom-right, 49 bottom-left
(clockwise), middles of the sides 42 top, 43 right, 44 bottom, 45 left (clockwise from the top). The middle markers
show the lens distortion: in a picture without it they lie exactly on the straight line between the two corner markers.
Corner and left/right markers keep 20 mm to the paper edge; the top/bottom middle markers only MID_EDGE_MM (10 mm),
because there is no room between the frame and the 20 mm margin (a printer may cut so close to the edge: check).
Needs opencv (marker bit patterns only).
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_grid_pdf import PT_PER_MM, marker_modules  # noqa: E402  (same marker drawing as the sheets)

PAGE_W, PAGE_H = 420.0, 297.0
FRAME_W, FRAME_H = 310.0, 232.5
MARKER_MM, EDGE_MM = 20.0, 20.0
MID_EDGE_MM = 10.0                  # paper edge to the outer edge of the top/bottom middle markers
FIRST_ID, LAST_ID = 42, 49        # DICT_4X4_50 has ids 0-49 only
GRID_MM = 20.0
BIG_CIRCLE_D, SMALL_CIRCLE_D, CORNER_INSET = 200.0, 40.0, 35.0
DIAGONAL_GAP = 3.0                  # mm of clear paper around a sharpness circle
W_FRAME, W_AXIS, W_THIN, W_GRID = 1.0, 0.6, 0.4, 0.25


def segment_outside_circles(p0, p1, circles, gap):
    """Pieces of the segment p0-p1 that lie outside every (cx, cy, r + gap) circle: list of (a, b) points."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    cuts = []
    for cx, cy, r in circles:
        r += gap
        fx, fy = p0[0] - cx, p0[1] - cy
        a, b, c = dx * dx + dy * dy, 2 * (fx * dx + fy * dy), fx * fx + fy * fy - r * r
        disc = b * b - 4 * a * c
        if disc > 0:
            s = math.sqrt(disc)
            cuts.append(((-b - s) / (2 * a), (-b + s) / (2 * a)))
    cuts.sort()
    pieces, t = [], 0.0
    for lo, hi in cuts:
        if lo > t:
            pieces.append((t, min(lo, 1.0)))
        t = max(t, hi)
    if t < 1.0:
        pieces.append((t, 1.0))
    return [((p0[0] + a * dx, p0[1] + a * dy), (p0[0] + b * dx, p0[1] + b * dy)) for a, b in pieces if b > a]


def build_pdf():
    k = PT_PER_MM
    cx, cy = PAGE_W / 2, PAGE_H / 2
    x0, y0 = cx - FRAME_W / 2, cy - FRAME_H / 2
    x1, y1 = cx + FRAME_W / 2, cy + FRAME_H / 2
    ops = ["0 0 0 RG", "0 J", "1 j"]

    def stroke(width, body):
        ops.append(f"{width * k:.4f} w")
        ops.append(body)

    def seg(a, b):
        return f"{a[0] * k:.4f} {a[1] * k:.4f} m {b[0] * k:.4f} {b[1] * k:.4f} l S"

    def circle(c, d, width):
        r, h = d / 2, 0.5522847498 * d / 2          # four Bezier arcs
        px = lambda x, y: f"{(c[0] + x) * k:.4f} {(c[1] + y) * k:.4f}"
        stroke(width, f"{px(r, 0)} m {px(r, h)} {px(h, r)} {px(0, r)} c {px(-h, r)} {px(-r, h)} {px(-r, 0)} c "
                      f"{px(-r, -h)} {px(-h, -r)} {px(0, -r)} c {px(h, -r)} {px(r, -h)} {px(r, 0)} c S")

    # thin grid through the centre every 20 mm (the centre lines are the thick axes, drawn later)
    for i in range(1, int(FRAME_W / 2 // GRID_MM) + 1):
        for s in (-1, 1):
            gx = cx + s * i * GRID_MM
            if x0 < gx < x1:
                stroke(W_GRID, seg((gx, y0), (gx, y1)))
    for j in range(1, int(FRAME_H / 2 // GRID_MM) + 1):
        for s in (-1, 1):
            gy = cy + s * j * GRID_MM
            if y0 < gy < y1:
                stroke(W_GRID, seg((x0, gy), (x1, gy)))

    corner_circles = [(cx + sx * (FRAME_W / 2 - CORNER_INSET), cy + sy * (FRAME_H / 2 - CORNER_INSET))
                      for sx in (-1, 1) for sy in (-1, 1)]
    # white disc behind every sharpness circle so that grid lines do not run through the measured edge
    ops.append("1 1 1 rg")
    for c in corner_circles + [(cx, cy)]:
        r = SMALL_CIRCLE_D / 2 + 1.5
        ops.append(f"{(c[0] - r) * k:.4f} {(c[1] - r) * k:.4f} {2 * r * k:.4f} {2 * r * k:.4f} re f")
    ops.append("0 0 0 rg")

    for a, b in (((x0, y0), (x1, y1)), ((x0, y1), (x1, y0))):                    # diagonals, broken at the corner circles
        for p, q in segment_outside_circles(a, b, [(c[0], c[1], SMALL_CIRCLE_D / 2) for c in corner_circles], DIAGONAL_GAP):
            stroke(W_THIN, seg(p, q))
    stroke(W_AXIS, seg((cx, y0), (cx, y1)))
    stroke(W_AXIS, seg((x0, cy), (x1, cy)))
    stroke(W_FRAME, f"{x0 * k:.4f} {y0 * k:.4f} {FRAME_W * k:.4f} {FRAME_H * k:.4f} re S")
    circle((cx, cy), BIG_CIRCLE_D, W_THIN)
    circle((cx, cy), SMALL_CIRCLE_D, W_THIN)
    for c in corner_circles:
        circle(c, SMALL_CIRCLE_D, W_THIN)

    # markers: corners 46-49 clockwise from the top-left, then the middles of the sides 50-53 clockwise from the top
    half = MARKER_MM / 2
    left, right = EDGE_MM + half, PAGE_W - EDGE_MM - half
    bottom, top = EDGE_MM + half, PAGE_H - EDGE_MM - half
    mid_top, mid_bottom = PAGE_H - MID_EDGE_MM - half, MID_EDGE_MM + half
    assert mid_top - half - y1 >= 2.0, "the top/bottom middle markers touch the frame"
    centres = {46: (left, top), 47: (right, top), 48: (right, bottom), 49: (left, bottom),
               42: (cx, mid_top), 43: (right, cy), 44: (cx, mid_bottom), 45: (left, cy)}
    mod = MARKER_MM / 6
    for n, (mx, my) in centres.items():
        mat = marker_modules(n)
        ops.append("0 0 0 rg")
        for row in range(6):
            for col in range(6):
                if mat[row, col]:
                    ops.append(f"{(mx - half + col * mod) * k:.4f} {(my + half - (row + 1) * mod) * k:.4f} "
                               f"{mod * k:.4f} {mod * k:.4f} re f")

    # caption inside the frame, bottom left of the vertical axis, on a white strip
    text = f"Camera blank A3 landscape | IDs {FIRST_ID}-{LAST_ID} | print 100% | frame {FRAME_W:g} x {FRAME_H:g} mm"
    ops.append("1 1 1 rg")
    ops.append(f"{(x0 + 6) * k:.4f} {(y0 + 2.5) * k:.4f} {(cx - x0 - 12) * k:.4f} {6.0 * k:.4f} re f")
    ops += ["0 0 0 rg", f"BT /F1 {3.2 * k:.4f} Tf {(x0 + 8) * k:.4f} {(y0 + 4.5) * k:.4f} Td ({text}) Tj ET"]
    stream = "\n".join(ops).encode("latin-1")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W * k:.4f} {PAGE_H * k:.4f}] /Contents 4 0 R "
        f"/Resources << /Font << /F1 5 0 R >> >> >>".encode(),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out), centres


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=os.path.join("out", "camera_blank_A3.pdf"))
    a = ap.parse_args()
    pdf, c = build_pdf()
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "wb") as f:
        f.write(pdf)
    dx, dy = c[47][0] - c[46][0], c[46][1] - c[49][1]
    print(f"{a.out}: A3 landscape, frame {FRAME_W:g} x {FRAME_H:g} mm, markers {FIRST_ID}-{LAST_ID} "
          f"(corners 46-49 clockwise from top-left, middles 42 top, 43 right, 44 bottom, 45 left)")
    print("Nominal distances between marker centres, mm (measure after printing at 100%):")
    print(f"  top {dx:g}, bottom {dx:g}, left {dy:g}, right {dy:g}, each diagonal {math.hypot(dx, dy):.2f}")
    print(f"  top middle (42) to bottom middle (44) {c[42][1] - c[44][1]:g}, left middle (45) to right middle (43) {c[43][0] - c[45][0]:g}")


if __name__ == "__main__":
    main()
