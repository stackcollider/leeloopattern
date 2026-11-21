"""Reports of DXF pieces: Excel and PDF (DECISIONS.md 2026-11-21; tried first in tools/report_test.py, report_pdf_test.py).

The figures are read from the DXF files themselves (the biggest closed outline), so a report shows what lies in the files.
    Excel  a row per piece: Name, Width mm, Height mm, Area cm², Perimeter mm, Nodes, Saved, Outline (a picture, each in
           its own scale, no nodes); on top the title and the time, at the bottom the number of pieces and the sum of areas.
    PDF    A4, like the model sheet of AccuMark: the title, the pieces in a grid of 6 columns in ONE round scale 1:N, under
           each piece its name and area; under the grid the table of the Excel report; more pieces: the next pages.
make() writes <out_dir>/<title>_report_<YYYYMMDD_HHMMSS>.<xlsx|pdf>. The PDF needs a running Qt application (its fonts).
"""
import datetime
import io
from pathlib import Path

import cv2
import ezdxf
import numpy as np
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Font
from PySide6.QtCore import QMarginsF, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen, QPolygonF

COLUMNS = (("Name", 22), ("Width mm", 11), ("Height mm", 11), ("Area cm²", 11), ("Perimeter mm", 13), ("Nodes", 8),
           ("Saved", 17), ("Outline", 20))
PIC_PX = 130                                    # Excel: the picture of the outline fitted into this square, px
ROW_PT = PIC_PX * 0.75 + 6                      # Excel: the row height in points (1 px = 0.75 pt)
DPI = 300                                       # PDF
PAGE_W, PAGE_H, MARGIN, TITLE_H = 210.0, 297.0, 15.0, 18.0        # mm
COLS, CELL_H, LABEL_H, PAD = 6, 34.0, 8.0, 2.0                     # the grid: columns, cell height, room for the name, gap (mm)
SCALES = (1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50)   # 1:N, the first that lets the biggest piece fit
ROW_H = 6.5                                                        # the table: one row, mm
TABLE_X = (0, 42, 64, 86, 108, 134, 150)                          # the table: the left of each column, mm from the margin
LINE, FILL, TEXT2 = QColor(30, 30, 40), QColor(232, 236, 240), QColor(90, 90, 105)


def folder_files(folder):
    """The DXF files of a folder, the oldest first."""
    return sorted(Path(folder).glob("*.dxf"), key=lambda p: p.stat().st_mtime)


def outline(path):
    """The biggest closed outline of a DXF as an (n, 2) array in mm (the closing point not repeated), or None."""
    try:
        doc = ezdxf.readfile(str(path))
    except (OSError, ezdxf.DXFError):
        return None
    best, best_a = None, 0.0
    for e in doc.modelspace().query("POLYLINE LWPOLYLINE"):
        pts = np.array([(p[0], p[1]) for p in (e.points() if e.dxftype() == "POLYLINE" else e.get_points("xy"))], float)
        if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        if len(pts) < 3:
            continue
        a = 0.5 * abs(float(np.dot(pts[:, 0], np.roll(pts[:, 1], -1)) - np.dot(pts[:, 1], np.roll(pts[:, 0], -1))))
        if a > best_a:
            best, best_a = pts, a
    return best


def picture(pts):
    """The outline fitted into a PIC_PX square (y up as in the DXF): a light fill, a dark line (no nodes)."""
    img = np.full((PIC_PX, PIC_PX, 3), 255, np.uint8)
    lo, size = pts.min(axis=0), np.ptp(pts, axis=0).max()
    k = (PIC_PX - 12) / size
    q = (pts - lo) * k
    q[:, 1] = q[:, 1].max() - q[:, 1]                     # y up -> the picture's y down
    q += (PIC_PX - q.max(axis=0)) / 2                     # in the middle
    poly = np.int32(np.round(q))
    cv2.fillPoly(img, [poly], (238, 232, 228), cv2.LINE_AA)
    cv2.polylines(img, [poly], True, (60, 40, 30), 1, cv2.LINE_AA)
    ok, png = cv2.imencode(".png", img)
    return io.BytesIO(png.tobytes())


def figures(path):
    pts = outline(path)
    saved = datetime.datetime.fromtimestamp(path.stat().st_mtime)
    if pts is None:
        return [path.stem, None, None, None, None, None, saved]
    w, h = np.ptp(pts, axis=0)
    area = 0.5 * abs(float(np.dot(pts[:, 0], np.roll(pts[:, 1], -1)) - np.dot(pts[:, 1], np.roll(pts[:, 0], -1))))
    perimeter = float(np.sum(np.hypot(*np.diff(np.vstack([pts, pts[:1]]), axis=0).T)))
    return [path.stem, round(float(w), 1), round(float(h), 1), round(area / 100, 1), round(perimeter, 1), len(pts), saved]


def write_xlsx(files, title, path):
    files = [Path(f) for f in files]
    rows = [figures(p) for p in files]
    outlines = [outline(p) for p in files]
    wb = Workbook()
    ws = wb.active
    ws.title = "Pieces"
    ws.append(["Pieces", title])
    ws.append(["Report", f"{datetime.datetime.now():%d.%m.%Y %H:%M}"])     # text: it runs over the next cells (a date in the narrow B is ####)
    ws.append([])
    ws.append([name for name, _w in COLUMNS])
    for cell in ws[4]:
        cell.font = Font(bold=True)
    for row, pts in zip(rows, outlines):
        ws.append(row)
        r = ws.max_row
        ws.cell(r, 7).number_format = "dd.mm.yyyy hh:mm"
        for col in range(1, len(COLUMNS) + 1):
            ws.cell(r, col).alignment = Alignment(vertical="center")
        if pts is not None:
            ws.row_dimensions[r].height = ROW_PT
            pic = Image(picture(pts))
            pic.width = pic.height = PIC_PX
            ws.add_image(pic, f"H{r}")
    areas = [r[3] for r in rows if r[3] is not None]
    ws.append([f"{len(rows)} piece{'' if len(rows) == 1 else 's'}", None, None, round(sum(areas), 1)])
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for i, (_name, width) in enumerate(COLUMNS, start=1):
        ws.column_dimensions[chr(64 + i)].width = width
    ws.freeze_panes = "A5"
    wb.save(path)
    return rows


def mm(v):
    return v * DPI / 25.4


class Pages:
    """A QPdfWriter with a painter that works in mm."""

    def __init__(self, path):
        self.writer = QPdfWriter(str(path))
        self.writer.setResolution(DPI)
        self.writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
        self.writer.setTitle(Path(path).stem)
        self.p = QPainter(self.writer)
        self.first = True

    def new_page(self):
        if not self.first:
            self.writer.newPage()
        self.first = False

    def font(self, size_pt, bold=False):
        f = QFont("Segoe UI")
        f.setPointSizeF(size_pt)
        f.setBold(bold)
        self.p.setFont(f)

    def text(self, x, y, w, h, s, align=Qt.AlignLeft | Qt.AlignVCenter, colour=LINE):
        self.p.setPen(colour)
        self.p.drawText(QRectF(mm(x), mm(y), mm(w), mm(h)), int(align), s)

    def end(self):
        self.p.end()


def nice_scale(need):
    """The first round 1:N with N >= need."""
    return next((n for n in SCALES if n >= need), int(np.ceil(need)))


def head(pg, title, line):
    """The title and the line under it on a new page; gives the y where the page goes on."""
    pg.new_page()
    pg.font(16, True)
    pg.text(MARGIN, MARGIN, PAGE_W - 2 * MARGIN, 9, title, Qt.AlignHCenter | Qt.AlignVCenter)
    pg.font(8)
    pg.text(MARGIN, MARGIN + 9, PAGE_W - 2 * MARGIN, 6, line, Qt.AlignHCenter | Qt.AlignVCenter, TEXT2)
    return MARGIN + TITLE_H


def sheet(pg, title, items, when):
    """The pieces in a grid of COLS columns, one scale for all; the table goes on under the grid. Gives the last y."""
    cell_w = (PAGE_W - 2 * MARGIN) / COLS
    box_w, box_h = cell_w - 2 * PAD, CELL_H - LABEL_H - 2 * PAD
    sizes = [np.ptp(pts, axis=0) for _n, pts, _a in items if pts is not None]
    need = max(max(w / box_w, h / box_h) for w, h in sizes) if sizes else 1.0
    n = nice_scale(need)
    line = f"Scale 1:{n:g}  ·  {len(items)} piece{'' if len(items) == 1 else 's'}  ·  {when:%d.%m.%Y %H:%M}"
    y = head(pg, title, line)
    for i, (name, pts, area) in enumerate(items):
        if i % COLS == 0 and i:
            y += CELL_H
        if y + CELL_H > PAGE_H - MARGIN:                 # the page is full: on the next one
            y = head(pg, title, line)
        cx = MARGIN + (i % COLS) * cell_w
        if pts is not None:
            q = (pts - pts.min(axis=0)) / n                  # mm on the paper
            q[:, 1] = q[:, 1].max() - q[:, 1]                # y up (DXF) -> y down (page)
            w, h = q.max(axis=0)
            ox, oy = cx + PAD + (box_w - w) / 2, y + PAD + (box_h - h) / 2
            pg.p.setPen(QPen(LINE, mm(0.2)))
            pg.p.setBrush(FILL)
            pg.p.drawPolygon(QPolygonF([QPointF(mm(ox + x), mm(oy + v)) for x, v in q]))
            pg.p.setBrush(Qt.NoBrush)
        pg.font(6.5, True)
        pg.text(cx, y + CELL_H - LABEL_H, cell_w, 4, name, Qt.AlignHCenter | Qt.AlignVCenter)
        pg.font(6)
        pg.text(cx, y + CELL_H - LABEL_H + 3.6, cell_w, 3.6, "" if area is None else f"{area:.1f} cm²",
                Qt.AlignHCenter | Qt.AlignVCenter, TEXT2)
    return (y + CELL_H if items else y), line


def table(pg, title, rows, y, line):
    """The table of the Excel report under the grid; a new page with the head row when the page is full."""
    widths = [b - a for a, b in zip(TABLE_X, TABLE_X[1:] + (PAGE_W - 2 * MARGIN,))]

    def put(values, bold=False):
        pg.font(8, bold)
        for col, (x, w, v) in enumerate(zip(TABLE_X, widths, values)):
            align = (Qt.AlignLeft if col in (0, 6) else Qt.AlignRight) | Qt.AlignVCenter
            pg.text(MARGIN + x + (0 if col in (0, 6) else -2), y, w - 1, ROW_H, v, align)

    def rule(at):
        pg.p.setPen(QPen(LINE, mm(0.2)))
        pg.p.drawLine(QPointF(mm(MARGIN), mm(at)), QPointF(mm(PAGE_W - MARGIN), mm(at)))

    def head_row():
        put([name for name, _w in COLUMNS[:7]], True)
        rule(y + ROW_H)

    y += 6
    if y + 3 * ROW_H > PAGE_H - MARGIN:
        y = head(pg, title, line)
    head_row()
    for r in rows:
        y += ROW_H
        if y + ROW_H > PAGE_H - MARGIN:
            y = head(pg, title, line)
            head_row()
            y += ROW_H
        put([r[0]] + ["" if v is None else f"{v:g}" if isinstance(v, int) else f"{v:.1f}" for v in r[1:6]]
            + [f"{r[6]:%d.%m.%Y %H:%M}"])
    y += ROW_H
    if y + ROW_H > PAGE_H - MARGIN:
        y = head(pg, title, line)
    rule(y)
    areas = [r[3] for r in rows if r[3] is not None]
    put([f"{len(rows)} piece{'' if len(rows) == 1 else 's'}", "", "", f"{sum(areas):.1f}", "", "", ""], True)


def write_pdf(files, title, path):
    files = [Path(f) for f in files]
    rows = [figures(p) for p in files]
    items = [(r[0], outline(p), r[3]) for r, p in zip(rows, files)]
    pg = Pages(path)
    y, line = sheet(pg, title, items, datetime.datetime.now())
    table(pg, title, rows, y, line)
    pg.end()
    return rows


def make(kind, files, title, out_dir):
    """kind 'xlsx' or 'pdf': writes the report, gives its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{title}_report_{datetime.datetime.now():%Y%m%d_%H%M%S}.{kind}"
    (write_xlsx if kind == "xlsx" else write_pdf)(files, title, path)
    return path
