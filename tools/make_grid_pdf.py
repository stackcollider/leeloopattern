"""Make a PDF with a plain black grid for printing on coloured paper.

Usage: python tools/make_grid_pdf.py [--page A3|A4|WxH] [--pitch 10] [--width 0.4] [--margin 6] [--trim 1] [--out out/grid_A3.pdf]

Page size in mm, portrait. The grid is a whole number of cells, centred on the page, no numbers, no
diagonals. Four ArUco 4x4 markers (DICT_4X4_50, IDs 0-3 clockwise from top-left, 20 mm, in a white 3 x 3 cell
box) stand in the grid corners; --no-markers leaves them out. Print at 100% ("Actual size"), then measure the
distance between marker centres with a ruler. Needs opencv (marker bit patterns only).
"""
import argparse
import os

PAGES = {"A3": (297.0, 420.0), "A4": (210.0, 297.0), "50x70": (500.0, 700.0), "61x86": (610.0, 860.0)}
PT_PER_MM = 72.0 / 25.4


def page_name(text):
    """The sheet format name as printed (A3, 50x70, 61x86), or the text as given for a WxH size."""
    for name in PAGES:
        if name.lower() == text.lower():
            return name
    return text


def parse_page(text):
    name = page_name(text)
    if name in PAGES:
        return PAGES[name]
    w, h = text.lower().split("x")
    return float(w), float(h)


def grid_lines(page_w, page_h, pitch, margin, trim=0):
    """(x0, y0, x1, y1, nx, ny): grid rectangle in mm from the page's bottom-left, nx x ny cells."""
    nx = int((page_w - 2 * margin) // pitch)
    ny = int((page_h - 2 * margin) // pitch)
    nx, ny = nx - trim, ny - trim
    gw, gh = nx * pitch, ny * pitch
    x0, y0 = (page_w - gw) / 2, (page_h - gh) / 2
    return x0, y0, x0 + gw, y0 + gh, nx, ny


def marker_modules(marker_id):
    """6 x 6 bool matrix of an ArUco 4x4 marker (border included), True = black."""
    import cv2
    img = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), marker_id, 6)
    return img == 0


def marker_ops(x, y, box, size, marker_id, k, line_width):
    """PDF operators for one marker: white box (x, y = its bottom-left, mm) with the black marker centred in it.
    The box is pulled in by half a line width on every side: the grid lines run along its edges and must stay whole."""
    g = line_width / 2
    ops = ["1 1 1 rg", f"{(x + g) * k:.4f} {(y + g) * k:.4f} {(box - 2 * g) * k:.4f} {(box - 2 * g) * k:.4f} re f", "0 0 0 rg"]
    mx, my, mod = x + (box - size) / 2, y + (box - size) / 2, size / 6
    mat = marker_modules(marker_id)
    for row in range(6):
        for col in range(6):
            if mat[row, col]:       # row 0 is the top of the marker
                ops.append(f"{(mx + col * mod) * k:.4f} {(my + (5 - row) * mod) * k:.4f} {mod * k:.4f} {mod * k:.4f} re f")
    return ops


def sign_ops(cx, cy, k, dot=5.0, length=14.0, head=4.0, half=2.0, stem=1.0):
    """PDF operators for the origin sign: a black dot at (cx, cy) mm and two arrows from it.
    The page is portrait, the sheet is laid landscape: the landscape X axis (right, towards marker 3)
    is down the page, the landscape Y axis (up, towards marker 1) is to the right."""
    r, c = dot / 2, 0.5523 * dot / 2
    ops = ["0 0 0 rg", "0 0 0 RG", f"{stem * k:.4f} w", "0 J",
           f"{(cx + r) * k:.4f} {cy * k:.4f} m "
           f"{(cx + r) * k:.4f} {(cy + c) * k:.4f} {(cx + c) * k:.4f} {(cy + r) * k:.4f} {cx * k:.4f} {(cy + r) * k:.4f} c "
           f"{(cx - c) * k:.4f} {(cy + r) * k:.4f} {(cx - r) * k:.4f} {(cy + c) * k:.4f} {(cx - r) * k:.4f} {cy * k:.4f} c "
           f"{(cx - r) * k:.4f} {(cy - c) * k:.4f} {(cx - c) * k:.4f} {(cy - r) * k:.4f} {cx * k:.4f} {(cy - r) * k:.4f} c "
           f"{(cx + c) * k:.4f} {(cy - r) * k:.4f} {(cx + r) * k:.4f} {(cy - c) * k:.4f} {(cx + r) * k:.4f} {cy * k:.4f} c f"]
    ops.append(f"{cx * k:.4f} {cy * k:.4f} m {cx * k:.4f} {(cy - length + head) * k:.4f} l S")          # down the page
    ops.append(f"{cx * k:.4f} {(cy - length) * k:.4f} m {(cx - half) * k:.4f} {(cy - length + head) * k:.4f} l "
               f"{(cx + half) * k:.4f} {(cy - length + head) * k:.4f} l f")
    ops.append(f"{cx * k:.4f} {cy * k:.4f} m {(cx + length - head) * k:.4f} {cy * k:.4f} l S")          # to the right
    ops.append(f"{(cx + length) * k:.4f} {cy * k:.4f} m {(cx + length - head) * k:.4f} {(cy + half) * k:.4f} l "
               f"{(cx + length - head) * k:.4f} {(cy - half) * k:.4f} l f")
    return ops


def caption_ops(text, x, y, size_mm, k):
    """One line of Helvetica (Latin-1 text) starting at (x, y) mm and running DOWN the page, so that it reads
    left to right along the bottom edge when the sheet is laid landscape."""
    text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return ["0 0 0 rg", f"BT /F1 {size_mm * k:.4f} Tf 0 -1 1 0 {x * k:.4f} {y * k:.4f} Tm ({text}) Tj ET"]


def build_pdf(page_w, page_h, pitch, width, margin, trim=0, markers=True, marker_mm=20.0, sign=False, caption="",
              first_id=0, mids=False):
    x0, y0, x1, y1, nx, ny = grid_lines(page_w, page_h, pitch, margin, trim)
    k = PT_PER_MM
    ops = [f"{width * k:.4f} w", "0 0 0 RG", "0 J"]        # butt caps: lines end exactly at the grid edge
    for i in range(nx + 1):
        x = (x0 + i * pitch) * k
        ops.append(f"{x:.4f} {y0 * k:.4f} m {x:.4f} {y1 * k:.4f} l S")
    for j in range(ny + 1):
        y = (y0 + j * pitch) * k
        ops.append(f"{x0 * k:.4f} {y:.4f} m {x1 * k:.4f} {y:.4f} l S")
    if markers:
        # one marker in each corner of the grid, in a white box of 3 x 3 cells (aligned to the grid lines).
        # IDs go clockwise seen from the printed side: 0 top-left, 1 top-right, 2 bottom-right, 3 bottom-left.
        box = 3 * pitch
        spots = [(x0, y1 - box), (x1 - box, y1 - box), (x1 - box, y0), (x0, y0)]
        if mids:
            # two more in the middle of the long sides: right edge, then left edge (IDs first_id + 4 and + 5);
            # the box starts one cell below the middle line so that it stays aligned to the grid lines
            ym = y0 + (ny // 2 - 1) * pitch
            spots += [(x1 - box, ym), (x0, ym)]
        for n, (cx, cy) in enumerate(spots):
            ops += marker_ops(cx, cy, box, marker_mm, first_id + n, k, width)
    if sign:
        # in the margins outside the grid, at the top-left page corner (the bottom-left of the landscape sheet)
        ops += sign_ops(x0 / 2, y1 + (page_h - y1) / 2, k)
    if caption:
        size = f"{max(x1 - x0, y1 - y0):g} × {min(x1 - x0, y1 - y0):g} mm"      # the grid as the landscape sheet reads
        ids = f"IDs {first_id}-{first_id + (6 if mids else 4) - 1}"
        ops += caption_ops(caption.replace("{size}", size).replace("{ids}", ids), x0 - 5.4, y1 - 3 * pitch - 8, 4.0, k)
    stream = "\n".join(ops).encode("latin-1")

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w * k:.4f} {page_h * k:.4f}] /Contents 4 0 R "
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
    return bytes(out), (nx, ny, x1 - x0, y1 - y0)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--page", default="A3", help="A3, A4, 50x70, 61x86 or WxH in mm (portrait)")
    ap.add_argument("--pitch", type=float, default=10.0, help="cell size, mm")
    ap.add_argument("--width", type=float, default=0.4, help="line width, mm")
    ap.add_argument("--margin", type=float, default=6.0, help="minimum free border, mm (printers cannot print to the edge)")
    ap.add_argument("--trim", type=int, default=1, help="cells removed from the grid in each direction (one column and one row)")
    ap.add_argument("--no-markers", action="store_true", help="plain grid, without the four ArUco corner markers")
    ap.add_argument("--marker", type=float, default=20.0, help="ArUco marker size, mm (must be < 3 x pitch)")
    ap.add_argument("--first-id", type=int, default=0, help="ArUco ID of the first corner marker (A3 0, 50x70 4, 61x86 10)")
    ap.add_argument("--mids", action="store_true", help="two more markers in the middle of the long sides (50x70, 61x86)")
    ap.add_argument("--sign", action="store_true", help="origin sign (dot and two arrows) in the margin at the marker 0 corner")
    ap.add_argument("--caption", default="", help="one line of text in the margin along the bottom edge of the landscape sheet; {page}, {size} and {ids} are filled in")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    w, h = parse_page(a.page)
    pdf, (nx, ny, gw, gh) = build_pdf(w, h, a.pitch, a.width, a.margin, a.trim, not a.no_markers, a.marker, a.sign,
                                            a.caption.replace("{page}", page_name(a.page)), a.first_id, a.mids)
    tag = "" if a.no_markers else f"_aruco{a.marker:g}"
    out = a.out or os.path.join("out", f"grid_{page_name(a.page)}_{a.pitch:g}mm_{a.width:g}{tag}.pdf")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(pdf)
    print(f"{out}: page {w:g} x {h:g} mm, grid {nx} x {ny} cells = {gw:g} x {gh:g} mm, pitch {a.pitch:g} mm, line {a.width:g} mm")


if __name__ == "__main__":
    main()
