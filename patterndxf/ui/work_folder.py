"""Work folder: the folder inside the DXF folder where the pieces are written (DECISIONS.md 2026-11-21).

The program keeps two rules only: the folder is inside the DXF folder, and it is not the DXF folder itself. The order inside
(one folder per model, the pieces only in the model folder, not deeper than 3 levels) is kept by the people: the
recommendations are in the tooltip of the folder icon (RULES). settings.json keeps the folder relative to the DXF folder
(`work_folder`), so it survives a change of the DXF folder in App settings.
"""
import os
from pathlib import Path

from PySide6.QtCore import QDir, QSortFilterProxyModel, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (QDialog, QFileSystemModel, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
                               QTreeView, QVBoxLayout)

from patterndxf.ui import theme

RULES = ("Choose the folder the pieces are written to.\n"
         "Keep the order in DXF:\n"
         "  • one folder per model: DXF › Customer › 6275610\n"
         "  • pieces only in the model folder, not next to other folders\n"
         "  • not deeper than 3 levels")
BAD_CHARS = '<>:"/\\|?*'                          # not allowed in a Windows file or folder name
SEP = " › "


def resolve(dxf_dir, rel):
    """The work folder as a path, or None when it is not set, gone, the DXF folder itself or outside it."""
    if not rel:
        return None
    root = Path(dxf_dir).resolve()
    path = (root / rel).resolve()
    if path == root or root not in path.parents or not path.is_dir():
        return None
    return path


def relative(dxf_dir, path):
    """The folder relative to the DXF folder, as kept in settings.json; None when it may not be the work folder."""
    root, path = Path(dxf_dir).resolve(), Path(path).resolve()
    if path == root or root not in path.parents:
        return None
    return str(path.relative_to(root))


def display(rel, metrics: QFontMetrics, width):
    """'DXF › Customer A › 6275610'; too long: the start is cut ('DXF › … › Constructor › 6275610'), the end stays."""
    parts = Path(rel).parts if rel else ()
    text = SEP.join(("DXF",) + parts)
    for k in range(1, len(parts)):
        if metrics.horizontalAdvance(text) <= width:
            break
        text = SEP.join(("DXF", "…") + parts[k:])
    return metrics.elidedText(text, Qt.ElideLeft, width) if metrics.horizontalAdvance(text) > width else text


def name_problem(name):
    if not name.strip():
        return "The name is empty"
    if any(c in BAD_CHARS for c in name):
        return f"These characters are not allowed: {BAD_CHARS}"
    if name.strip() != name.rstrip(". "):
        return "The name may not end with a dot or a space"
    return ""


class InsideDxf(QSortFilterProxyModel):
    """Only the DXF folder, its folders and the folders on the way to it: DXF is the top node, nothing beside it."""

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.root = root

    def filterAcceptsRow(self, row, parent):
        path = Path(self.sourceModel().filePath(self.sourceModel().index(row, 0, parent))).resolve()
        return path == self.root or self.root in path.parents or path in self.root.parents


class WorkFolderDialog(QDialog):
    """The folders of the DXF folder as a tree with DXF as the top node (no way above it); New folder; Open in Explorer;
    Select (not on DXF itself)."""

    def __init__(self, dxf_dir, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Work folder")
        self.setModal(True)
        self.resize(520, 560)
        self.root = Path(dxf_dir).resolve()
        self.chosen, self.pending = None, current
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Dirs | QDir.NoDotAndDotDot)
        self.model.setRootPath(str(self.root.parent))
        self.proxy = InsideDxf(self.root, self)
        self.proxy.setSourceModel(self.model)
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setRootIndex(self.proxy.mapFromSource(self.model.index(str(self.root.parent))))
        for col in (1, 2, 3):                            # size, type, date: not needed
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.selectionModel().currentChanged.connect(self.refresh)
        self.tree.doubleClicked.connect(lambda _i: self.select())
        self.model.directoryLoaded.connect(self.on_loaded)
        head = QLabel(f"DXF: {self.root}")
        head.setStyleSheet(f"color: {theme.TEXT2}")
        head.setWordWrap(True)
        self.new_btn, self.open_btn = QPushButton("New folder"), QPushButton("Open in Explorer")
        self.select_btn, cancel = QPushButton("Select"), QPushButton("Cancel")
        self.new_btn.setToolTip("A new folder inside the selected one (select DXF for a folder of the top level)")
        self.open_btn.setToolTip("The selected folder in Windows Explorer (to rename or delete folders there)")
        self.select_btn.setToolTip("Write the pieces into the selected folder (not into DXF itself)")
        self.select_btn.setObjectName("primary")
        self.new_btn.clicked.connect(self.new_folder)
        self.open_btn.clicked.connect(self.open_explorer)
        self.select_btn.clicked.connect(self.select)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.new_btn)
        buttons.addWidget(self.open_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.select_btn)
        buttons.addWidget(cancel)
        box = QVBoxLayout(self)
        box.addWidget(head)
        box.addWidget(self.tree, 1)
        box.addLayout(buttons)
        self.show_folder(current or self.root)
        self.tree.expand(self.index_of(self.root))
        self.refresh()

    def index_of(self, path):
        return self.proxy.mapFromSource(self.model.index(str(path)))

    def on_loaded(self, _path):
        """The folders come in the background: the folder to show is selected once it is there."""
        if self.pending is not None and self.show_folder(self.pending):
            self.pending = None
        self.tree.expand(self.index_of(self.root))

    def show_folder(self, path):
        index = self.index_of(path)
        if not index.isValid():
            return False
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        return True

    def current(self):
        """The folder of the current row (DXF included), or None."""
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        path = Path(self.model.filePath(self.proxy.mapToSource(index))).resolve()
        return path if path == self.root or self.root in path.parents else None

    def selected(self):
        """The folder that may be the work folder: inside DXF, not DXF itself."""
        path = self.current()
        return path if path is not None and path != self.root else None

    def refresh(self, *_args):
        self.select_btn.setEnabled(self.selected() is not None)

    def new_folder(self):
        base = self.current() or self.root
        name, ok = QInputDialog.getText(self, "New folder", f"A new folder in {base.name if base != self.root else 'DXF'}:")
        if not ok:
            return
        problem = name_problem(name)
        if problem:
            QMessageBox.warning(self, "New folder", problem)
            return
        path = base / name.strip()
        try:
            path.mkdir(exist_ok=True)
        except OSError as err:
            QMessageBox.warning(self, "New folder", f"The folder could not be made: {err}")
            return
        self.tree.expand(self.index_of(base))
        if not self.show_folder(path):
            self.pending = path                          # it comes with the next load of the folder

    def open_explorer(self):
        os.startfile(str(self.current() or self.root))

    def select(self):
        path = self.selected()
        if path is None:
            return
        self.chosen = path
        self.accept()
