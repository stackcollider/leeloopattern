"""Writes the description of a printed work sheet (the JSON that patterndxf.calib.nodecal reads) by the same rules
tools/make_grid_pdf.py draws the PDF with, and checks it against the PDF itself.

Run: .venv-cam\\Scripts\\python tools\\make_sheet_json.py --page 61x86 --first-id 10 --mids --pdf out\\sheet_61x86.pdf
     .venv-cam\\Scripts\\python tools\\make_sheet_json.py --page 50x70 --first-id 4 --mids --compare <sheet_50x70.json from Nodes>
Writes patterndxf/sheet/sheets/sheet_<page>.json (mm, page coordinates, origin top-left of the PDF page, y down).
--compare: only says whether the made JSON equals a given one (the proof that the rules are the same).
"""
import argparse
import json
import re
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_grid_pdf as g  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "patterndxf" / "sheet" / "sheets"
PITCH, WIDTH, MARGIN, TRIM, MARKER_MM = 10.0, 0.4, 6.0, 1, 20.0     # the values the work sheets were printed with


def num(v):
    return int(v) if float(v).is_integer() else round(float(v), 4)


def make(page, first_id, mids):
    name = g.page_name(page)
    pw, ph = g.parse_page(page)
    x0, y0, x1, y1, nx, ny = g.grid_lines(pw, ph, PITCH, MARGIN, TRIM)           # y from the bottom of the page (PDF)
    box = 3 * PITCH
    spots = [(x0, y1 - box), (x1 - box, y1 - box), (x1 - box, y0), (x0, y0)]       # the same order as build_pdf
    if mids:
        ym = y0 + (ny // 2 - 1) * PITCH
        spots += [(x1 - box, ym), (x0, ym)]
    markers = {}
    for n, (bx, by) in enumerate(spots):
        top = ph - (by + box)                                                    # the box in page coordinates, y down
        cx, cy = bx + box / 2, top + box / 2
        h = MARKER_MM / 2
        markers[str(first_id + n)] = {
            "center": [num(cx), num(cy)],
            "corners": [[num(cx - h), num(cy - h)], [num(cx + h), num(cy - h)], [num(cx + h), num(cy + h)], [num(cx - h), num(cy + h)]],
            "block": [num(bx), num(top), num(bx + box), num(top + box)]}
    return {"name": name, "pdf": f"sheet_{name}.pdf", "page_mm": [num(pw), num(ph)],
            "units": "mm, page coordinates, origin top-left of the PDF page, y down", "dictionary": "DICT_4X4_100",
            "marker_mm": num(MARKER_MM), "cell_mm": num(PITCH), "line_mm": WIDTH,
            "grid": {"x0": num(x0), "y0": num(ph - y1), "nx": nx, "ny": ny,
                     "note": "lines at x0 + i*cell (i=0..nx), y0 + j*cell (j=0..ny); no lines inside marker blocks"},
            "markers": markers}


def check_pdf(spec, pdf):
    """The grid lines and the white marker boxes of the PDF must be where the JSON says. -> list of problems."""
    data = Path(pdf).read_bytes()
    stream = re.search(rb"stream\n(.*?)\nendstream", data, re.S).group(1)
    try:
        stream = zlib.decompress(stream)
    except zlib.error:
        pass
    text = stream.decode("latin-1")
    k = g.PT_PER_MM
    ph = spec["page_mm"][1]
    xs, ys = set(), set()
    for a, b, c, d in re.findall(r"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", text):
        a, b, c, d = (float(v) / k for v in (a, b, c, d))
        if abs(a - c) < 1e-3:
            xs.add(round(a, 2))
        elif abs(b - d) < 1e-3:
            ys.add(round(ph - b, 2))
    gr, cell = spec["grid"], spec["cell_mm"]
    want_x = {round(gr["x0"] + i * cell, 2) for i in range(gr["nx"] + 1)}
    want_y = {round(gr["y0"] + j * cell, 2) for j in range(gr["ny"] + 1)}
    problems = []
    if not want_x <= xs or not want_y <= ys:
        problems.append(f"grid lines: {len(want_x - xs)} vertical and {len(want_y - ys)} horizontal lines of the JSON are not in the PDF")
    boxes = re.findall(r"1 1 1 rg\n([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+) re f", text)
    centres = sorted((round(float(x) / k + float(w) / k / 2, 2), round(ph - (float(y) / k + float(h) / k / 2), 2)) for x, y, w, h in boxes)
    want = sorted(tuple(round(v, 2) for v in m["center"]) for m in spec["markers"].values())
    if centres != want:
        problems.append(f"marker boxes: PDF {centres} vs JSON {want}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", required=True)
    ap.add_argument("--first-id", type=int, required=True)
    ap.add_argument("--mids", action="store_true")
    ap.add_argument("--pdf", help="check the JSON against this PDF")
    ap.add_argument("--compare", help="only compare with this JSON")
    a = ap.parse_args()
    spec = make(a.page, a.first_id, a.mids)
    if a.compare:
        other = json.loads(Path(a.compare).read_text(encoding="utf-8"))
        same = {k: v for k, v in spec.items() if k != "pdf"} == {k: v for k, v in other.items() if k != "pdf"}
        print("the same" if same else "DIFFERENT")
        if not same:
            for key in spec:
                if spec[key] != other.get(key):
                    print(f"  {key}: made {spec[key]}\n  {' ' * len(key)}  given {other.get(key)}")
        sys.exit(0 if same else 1)
    if a.pdf:
        problems = check_pdf(spec, a.pdf)
        for p in problems:
            print("PDF CHECK:", p)
        if problems:
            sys.exit(1)
        print("PDF check: grid lines and marker boxes as in the JSON")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"sheet_{spec['name']}.json"
    path.write_text(json.dumps(spec, indent=1), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
