"""DXF output of a piece contour (mm, y up) in a few flavours, to find the one AccuMark imports.

plain      R2010, closed LWPOLYLINE on layer 0 (what the Save DXF button writes now)
layer1     R12, closed POLYLINE on layer "1" (AAMA/ASTM: layer 1 = piece boundary)
aama       layer1 + notches as POINT entities on layer "4" + the name as TEXT on layer "8";
           the contour itself has no notch geometry
In 'plain' and 'layer1' the notches, if any, are part of the contour geometry (add_notches).
"""
import ezdxf
import numpy as np


def write_dxf(path, outer_mm, flavour="plain", notch_points=None, name=""):
    outer = np.asarray(outer_mm, float)
    if flavour == "plain":
        doc = ezdxf.new("R2010", setup=True)
        doc.units = ezdxf.units.MM
        doc.modelspace().add_lwpolyline(outer.tolist(), close=True)
    else:
        doc = ezdxf.new("R12")
        msp = doc.modelspace()
        msp.add_polyline2d(outer.tolist(), close=True, dxfattribs={"layer": "1"})
        if flavour == "aama":
            for x, y in (notch_points or []):
                msp.add_point((float(x), float(y)), dxfattribs={"layer": "4"})
            if name:
                centre = outer.mean(axis=0)
                msp.add_text(name, dxfattribs={"layer": "8", "height": 3.5,
                                               "insert": (float(centre[0]), float(centre[1]))})
    doc.saveas(path)


STANDARD_LAYER = "T001L001"


def write_standard_dxf(path, outer_mm, layer=STANDARD_LAYER):
    """The format that AccuMark's Standard DXF import was verified with (import_tests T1, T3): AC1009,
    one closed POLYLINE (first vertex repeated), numbers in mm (AccuMark reads them as mm), clockwise with
    y up, origin at the bottom-left corner of the bounding box. The piece and model are named after the
    file, upper-cased by AccuMark."""
    pts = np.asarray(outer_mm, float)
    pts = pts - pts.min(axis=0)
    if float(np.dot(pts[:, 0], np.roll(pts[:, 1], -1)) - np.dot(pts[:, 1], np.roll(pts[:, 0], -1))) > 0:
        pts = pts[::-1]                                   # counter-clockwise: reverse
    ring = np.vstack([pts, pts[:1]])
    lines = ["999", "Version 1.0, Gerber Technology.", "  9", "$ACADVER", "  1", "AC1009", "  0", "SECTION", "  2",
             "HEADER", "  9", "$INSUNITS", " 70", "4", "  0", "ENDSEC", "  0", "SECTION", "  2", "ENTITIES", "  0",
             "POLYLINE", "  8", layer, " 66", "1"]
    for x, y in ring:
        lines += ["  0", "VERTEX", "  8", layer, " 10", f"{x:.6f}", " 20", f"{y:.6f}"]
    lines += ["  0", "SEQEND", "  8", layer, "  0", "ENDSEC", "  0", "EOF"]
    crlf = chr(13) + chr(10)
    with open(path, "w", newline="") as f:
        f.write(crlf.join(lines) + crlf)
