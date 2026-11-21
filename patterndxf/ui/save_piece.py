"""Save piece: the window of Save in Measure (DECISIONS.md 2026-11-21 "Save piece"; tried first in tools/save_piece_test.py).

On top the work folder, then the pieces of the folder (name, area read from the DXF, time; the last at the bottom) and their
sum, the name field (+ .dxf), a state line, Open in Explorer, Save / Cancel (Enter / Esc). A click on a piece puts
its name into the field (to take it again and replace it), a double click saves at once; a taken name asks Replace.
By default the field holds the model number (the folder name), the cursor at its end.
The window gives the path to write; the writing is done by the caller.
"""
import datetime
import os
from pathlib import Path

import ezdxf
import numpy as np
from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from patterndxf.ui import theme
from patterndxf.ui.work_folder import BAD_CHARS


def dxf_area_cm2(path):
    """The area of the biggest closed outline of a DXF, cm² (None when it cannot be read)."""
    try:
        doc = ezdxf.readfile(str(path))
    except (OSError, ezdxf.DXFError):
        return None
    best = 0.0
    for e in doc.modelspace().query("POLYLINE LWPOLYLINE"):
        pts = np.array([(p[0], p[1]) for p in (e.points() if e.dxftype() == "POLYLINE" else e.get_points("xy"))], float)
        if len(pts) >= 3:
            a = 0.5 * abs(float(np.dot(pts[:, 0], np.roll(pts[:, 1], -1)) - np.dot(pts[:, 1], np.roll(pts[:, 0], -1))))
            best = max(best, a)
    return best / 100 if best else None


class SavePieceDialog(QDialog):
    """Save piece: the work folder, its pieces, the name; returns the path to write (the replace already agreed)."""

    def __init__(self, folder, area_cm2, shown_folder="", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Save piece")
        self.setModal(True)
        self.resize(600, 620)
        self.folder, self.area_cm2, self.path = Path(folder), area_cm2, None
        head = QLabel(shown_folder or str(self.folder))
        head.setStyleSheet(f"color: {theme.KEY}; font-size: 16px")
        head.setToolTip(str(self.folder))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Area cm²", "Saved"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.itemClicked.connect(self.on_clicked)
        self.table.itemDoubleClicked.connect(lambda item: (self.on_clicked(item), self.save()))
        self.total = QLabel("")
        self.total.setStyleSheet(f"color: {theme.TEXT2}")
        self.name = QLineEdit()
        self.name.setValidator(QRegularExpressionValidator(QRegularExpression(f"[^{QRegularExpression.escape(BAD_CHARS)}]*")))
        self.name.setStyleSheet(f"font-family: {theme.MONO}; font-size: 16px")
        self.name.textChanged.connect(self.refresh)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        name_row.addWidget(self.name, 1)
        name_row.addWidget(QLabel(".dxf"))
        self.state = QLabel("")
        self.open_btn = QPushButton("Open in Explorer")
        self.open_btn.setToolTip("The work folder in Windows Explorer")
        self.open_btn.clicked.connect(lambda: os.startfile(str(self.folder)))
        self.save_btn, cancel = QPushButton("Save"), QPushButton("Cancel")
        self.save_btn.setObjectName("primary")
        self.save_btn.setDefault(True)                  # Enter
        self.save_btn.clicked.connect(self.save)
        cancel.clicked.connect(self.reject)             # Esc too (QDialog)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(cancel)
        box = QVBoxLayout(self)
        box.addWidget(head)
        box.addWidget(self.table, 1)
        box.addWidget(self.total)
        box.addSpacing(8)
        box.addLayout(name_row)
        box.addWidget(self.state)
        box.addLayout(buttons)
        self.pieces = self.read_folder()
        self.fill_table()
        self.prefill()
        self.name.setFocus()

    def read_folder(self):
        """[(name, area cm² or None, time)], the oldest first."""
        files = sorted(self.folder.glob("*.dxf"), key=lambda p: p.stat().st_mtime)
        return [(p.stem, dxf_area_cm2(p), datetime.datetime.fromtimestamp(p.stat().st_mtime)) for p in files]

    def fill_table(self):
        self.table.setRowCount(len(self.pieces))
        for row, (name, area, when) in enumerate(self.pieces):
            area_item = QTableWidgetItem("?" if area is None else f"{area:.1f}")
            area_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            for col, item in enumerate((QTableWidgetItem(name), area_item, QTableWidgetItem(f"{when:%d.%m %H:%M}"))):
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.scrollToBottom()
        known = [a for _n, a, _w in self.pieces if a is not None]
        self.total.setText(f"{len(self.pieces)} pieces · {sum(known):.1f} cm²")

    def prefill(self):
        """The model number (the folder name), the cursor at its end: the operator types the rest (1.17.1)."""
        self.name.setText(self.folder.name)
        self.name.end(False)

    def row_of(self, name):
        return next((i for i, (n, _a, _w) in enumerate(self.pieces) if n.lower() == name.lower()), None)

    def on_clicked(self, item):
        self.name.setText(self.pieces[item.row()][0])

    def refresh(self, *_args):
        name = self.name.text().strip()
        row = self.row_of(name) if name else None
        self.table.blockSignals(True)
        if row is None:
            self.table.clearSelection()
        else:
            self.table.selectRow(row)
            self.table.scrollToItem(self.table.item(row, 0))
        self.table.blockSignals(False)
        if not name:
            self.state.setText("Enter the piece name")
            self.state.setStyleSheet(f"color: {theme.TEXT2}")
        elif name.endswith("."):
            self.state.setText("The name may not end with a dot")
            self.state.setStyleSheet(f"color: {theme.WARN}")
        elif row is not None:
            self.state.setText("⚠ exists: Save replaces it")
            self.state.setStyleSheet(f"color: {theme.WARN}")
        else:
            self.state.setText(f"✓ new file {name}.dxf")
            self.state.setStyleSheet(f"color: {theme.OK}")
        self.save_btn.setEnabled(bool(name) and not name.endswith("."))

    def save(self):
        name = self.name.text().strip()
        if not name or name.endswith("."):
            return
        path = self.folder / f"{name}.dxf"
        if path.exists():
            ask = QMessageBox.question(self, "Save piece", f"{name}.dxf exists. Replace it?",
                                       QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
            if ask != QMessageBox.Yes:
                return
        self.path = path
        self.accept()
