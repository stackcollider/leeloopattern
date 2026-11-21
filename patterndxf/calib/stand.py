"""The layer of the program over nodecal (docs/DECISIONS.md 2026-11-19): the names of the program and the calibration
step by step, so that a window can show every step as it is done.

calibrate_steps() does exactly what nodecal.calibrate() does, with the same functions of nodecal in the same order, and tells
`report(key, text)` after every step; tests/test_reference.py --steps checks that both give the same calibration.
`report` may raise Cancelled to stop between the steps.
"""
from patterndxf.calib import nodecal

STEPS = (("markers", "Markers on the median"), ("coarse", "Coarse model"), ("nodes", "Grid nodes"),
         ("model", "Lens model and field"))


class Cancelled(Exception):
    """The person stopped the calibration (raised by a `report` between the steps)."""


def calibrate_steps(gray, sheets, meta=None, report=None):
    """The median picture of the empty sheet -> nodecal.Calibration, step by step. Raises nodecal.CalibrationError as
    nodecal.calibrate() does, and Cancelled when `report` raises it."""
    report = report or (lambda key, text: None)
    if gray.ndim != 2:
        raise nodecal.CalibrationError("a gray picture is needed")
    sheet, found = nodecal.find_markers(gray, sheets)
    if sheet is None:
        raise nodecal.CalibrationError("no marker of a known sheet")
    whole = sorted(found) == sorted(sheet.markers)
    report("markers", f"{sheet.name}: {len(found)} of {len(sheet.markers)}{', whole sheet' if whole else ', NOT whole'}")
    coarse = nodecal.Coarse(sheet, found, gray.shape[::-1])
    report("coarse", f"{'lens k1' if coarse.use_lens else 'homography'}, markers off by {coarse.residual_px:.2f} px")
    nodes, rejected = nodecal.find_nodes(gray, sheet, coarse, found)
    inner = [(ij, p) for ij, (p, k) in sorted(nodes.items()) if k == "inner"]
    n_inner = sum(1 for ij in sheet.nodes() if sheet.kind(*ij) == "inner")
    if len(inner) < 0.5 * n_inner:
        raise nodecal.CalibrationError(f"only {len(inner)} of {n_inner} inner nodes found")
    report("nodes", f"{len(inner)} of {n_inner} inner")
    info = {"sheet": sheet.name, "markers_found": sorted(found), "markers_expected": sorted(sheet.markers),
            "whole_sheet": whole, "coarse_model": "lens_k1" if coarse.use_lens else
            "homography", "coarse_residual_px": round(coarse.residual_px, 3), "inner_found": len(inner),
            "inner_expected": n_inner, "nodes_found": len(nodes), "rejected": len(rejected)}
    cal = nodecal.Calibration([sheet.mm(*ij) for ij, _ in inner], [p for _, p in inner], gray.shape[::-1], sheet,
                              {**(meta or {}), **info})
    cal.all_nodes = nodes                                            # every node with its kind (not saved)
    report("model", f"{cal.px_per_mm():.2f} px/mm")
    return cal
