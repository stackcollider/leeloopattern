"""App settings (was Set Settings): everything except the camera (docs/DECISIONS.md 2026-11-15).

Card: Output (the DXF and log folders, checked while typing). Defaults fills the fields with the defaults (nothing is
written until Save). The cards Checks and Measurement view of 0.x are gone (1.19.4: nothing read those settings).
Save writes settings.json (the dialog returns Accepted, `new` holds the settings); Cancel changes nothing (Rejected).
A modal dialog over the Camera preview; no camera is needed here, so there is no live video.
"""
import logging
from pathlib import Path

from PySide6.QtWidgets import QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from patterndxf import appinfo, startup
from patterndxf.ui import theme
from patterndxf.ui.widgets import card

class SetSettingsWindow(QDialog):
    def __init__(self, settings, parent=None):
        """settings: the settings in force (all keys; the keys this screen does not show are kept as they are)."""
        super().__init__(parent)
        self.setWindowTitle(f"{appinfo.NAME} {appinfo.version()}: App settings")
        self.setModal(True)
        self.setStyleSheet("QLabel, QLineEdit, QPushButton { font-size: 16px; }")
        self.settings = dict(settings)
        self.new = None                                          # the settings that were written (after Save)

        self.edits, self.statuses = {}, {}
        folders = QWidget()
        grid = QGridLayout(folders)
        grid.setContentsMargins(0, 0, 0, 0)
        for row, (key, title) in enumerate((("dxf_dir", "DXF folder"), ("log_dir", "Log folder"))):
            edit, status, browse = QLineEdit(self.settings[key]), QLabel(""), QPushButton("Browse...")
            self.edits[key], self.statuses[key] = edit, status
            browse.clicked.connect(lambda _=False, e=edit: self.browse(e))
            edit.textChanged.connect(self.check_folders)
            grid.addWidget(QLabel(title), row * 2, 0)
            grid.addWidget(edit, row * 2, 1)
            grid.addWidget(browse, row * 2, 2)
            grid.addWidget(status, row * 2 + 1, 1, 1, 2)
        grid.setColumnStretch(1, 1)

        self.save_btn = QPushButton("Save")
        self.save_btn.setObjectName("primary")
        self.cancel_btn = QPushButton("Cancel")
        self.defaults_btn = QPushButton("Defaults")
        self.defaults_btn.setToolTip("Fill the fields with the defaults (nothing is written until Save)")
        self.message = QLabel("")
        self.message.setWordWrap(True)

        title = QLabel("App settings")
        title.setStyleSheet(f"color: {theme.KEY}; font-size: 26px")
        hint = QLabel("Defaults are fine for most stands. Change only what you need, then press Save.")
        hint.setStyleSheet(f"color: {theme.TEXT2}; font-size: 15px")
        buttons = QHBoxLayout()
        buttons.addWidget(self.defaults_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.save_btn)
        lay = QVBoxLayout()
        lay.addWidget(title)
        lay.addWidget(hint)
        lay.addWidget(card("Output", folders))
        lay.addStretch(1)
        lay.addWidget(self.message)
        lay.addLayout(buttons)
        self.setLayout(lay)
        self.resize(720, 380)

        self.save_btn.clicked.connect(self.save)
        self.cancel_btn.clicked.connect(self.reject)
        self.defaults_btn.clicked.connect(self.fill_defaults)
        self.fill(self.settings)

    # ----- the fields
    def fill(self, values):
        for key, edit in self.edits.items():
            edit.setText(str(values[key]))
        self.check_folders()

    def fill_defaults(self):
        self.fill(startup.DEFAULT_SETTINGS)
        self.message.setText("Defaults filled in: press Save to keep them.")
        self.message.setStyleSheet(f"color: {theme.TEXT2}")

    def browse(self, edit):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder", edit.text() or str(Path.home()))
        if folder:
            edit.setText(str(Path(folder)))

    def check_folders(self):
        """The folders are checked while typing: writable, free space. Save is open only when both are fine."""
        ok = True
        for key, edit in self.edits.items():
            folder = edit.text().strip()
            problem = startup.folder_problem(folder)
            status = self.statuses[key]
            if problem:
                status.setText(f"✗ {problem}")
                status.setStyleSheet(f"color: {theme.ERR}")
                ok = False
            else:
                free = startup.free_gb(folder)
                status.setText("✓ writable" + (f", {free:.0f} GB free" if free is not None else ""))
                status.setStyleSheet(f"color: {theme.OK}")
        self.save_btn.setEnabled(ok)
        return ok

    # ----- Save and Cancel
    def save(self):
        if not self.check_folders():
            self.message.setText("Fix the folders first.")
            self.message.setStyleSheet(f"color: {theme.ERR}")
            return
        new = dict(self.settings)
        for key, edit in self.edits.items():
            new[key] = edit.text().strip()
        try:
            startup.save_settings(new)
        except OSError as err:
            self.message.setText(f"Could not save the settings: {err}")
            self.message.setStyleSheet(f"color: {theme.ERR}")
            logging.error("settings not saved: %s", err)
            return
        logging.info("settings saved: %s", {k: new[k] for k in self.edits})
        self.new = new
        self.accept()
