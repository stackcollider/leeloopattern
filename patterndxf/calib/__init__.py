"""Calibration by the nodes of the printed grid (docs/DECISIONS.md 2026-11-19).

nodecal.py is a copy of calib/nodecal.py of the research project Nodes WITHOUT ANY CHANGE (a reference test, not
published, proves it gives the same results as there); when it is fixed in Nodes, it is copied again. The names of the program live in a thin layer over it.
"""
from pathlib import Path

SHEETS_DIR = Path(__file__).resolve().parent.parent / "sheet" / "sheets"      # sheet_A3.json, sheet_50x70.json, sheet_61x86.json


def load_sheets():
    """The descriptions of every work sheet (nodecal.Sheet)."""
    from patterndxf.calib.nodecal import Sheet
    return [Sheet.load(str(p)) for p in sorted(SHEETS_DIR.glob("sheet_*.json"))]
