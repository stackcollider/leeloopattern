"""Save piece, test: the window of Save tried apart from the program (2026-11-21, DECISIONS.md "Save piece").

Run: .venv-cam\\Scripts\\python tools\\save_piece_test.py [--folder <work folder>]
     (default: out/save_test/DXF/Customer A/6275610, made when missing; never the real DXF folder of the program)

New piece makes a fake piece (a rectangle of a random size); Save opens the window: the work folder on top, the pieces of
the folder (name, area read from the DXF, time), the sum, the name field (by default the last name with the part after the
model number selected), "exists" when the name is taken, the area of this piece; Save writes a real Standard DXF
(patterndxf.core.dxf_out, as the program does), a taken name asks Replace / Cancel.
SavePieceDialog lives in patterndxf/ui/save_piece.py (in the program since 1.17.0); this tool tries it apart.
"""
import random
import sys
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from patterndxf.core import dxf_out                                             # noqa: E402
from patterndxf.ui import theme                                                 # noqa: E402
from patterndxf.ui.save_piece import SavePieceDialog                            # noqa: E402

TEST_DXF = ROOT / "out" / "save_test" / "DXF"


class TestWindow(QMainWindow):
    def __init__(self, folder):
        super().__init__()
        self.setWindowTitle("Save piece test")
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.piece = None
        self.info = QLabel("")
        self.info.setStyleSheet(f"color: {theme.KEY}; font-size: 16px")
        self.log = QLabel("")
        self.log.setStyleSheet(f"color: {theme.TEXT2}")
        self.log.setWordWrap(True)
        new_btn, save_btn = QPushButton("New piece"), QPushButton("Save")
        save_btn.setObjectName("primary")
        new_btn.clicked.connect(self.new_piece)
        save_btn.clicked.connect(self.save)
        row = QHBoxLayout()
        row.addWidget(new_btn)
        row.addWidget(save_btn)
        box = QVBoxLayout()
        box.addWidget(QLabel(f"Work folder: {self.folder}"))
        box.addWidget(self.info)
        box.addLayout(row)
        box.addWidget(self.log)
        box.addStretch(1)
        w = QWidget()
        w.setLayout(box)
        self.setCentralWidget(w)
        self.resize(560, 260)
        self.new_piece()

    def new_piece(self):
        w, h = random.randint(40, 300), random.randint(40, 300)
        self.piece = np.array([(0, 0), (w, 0), (w, h), (0, h)], float)
        self.info.setText(f"Fake piece: {w} x {h} mm, {w * h / 100:.1f} cm²")

    def shown_folder(self):
        try:
            return " › ".join(("DXF",) + self.folder.resolve().relative_to(TEST_DXF.resolve()).parts)
        except ValueError:
            return str(self.folder)

    def save(self):
        w, h = np.ptp(self.piece, axis=0)
        dialog = SavePieceDialog(self.folder, w * h / 100, self.shown_folder(), self)
        if dialog.exec() != QDialog.Accepted or dialog.path is None:
            return
        try:
            dxf_out.write_standard_dxf(str(dialog.path), self.piece)
        except OSError as err:
            self.log.setText(f"Save failed: {err}")
            return
        self.log.setText(f"Saved {dialog.path}")
        self.new_piece()


def main():
    folder = TEST_DXF / "Customer A" / "6275610"
    if "--folder" in sys.argv:
        folder = Path(sys.argv[sys.argv.index("--folder") + 1])
    app = QApplication(sys.argv)
    theme.apply(app)
    win = TestWindow(folder)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
