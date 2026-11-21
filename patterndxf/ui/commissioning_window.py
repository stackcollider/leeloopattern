"""Commissioning: the hub of the stand acceptance (docs/DECISIONS.md 2026-11-15).

A modal dialog over the Camera preview. Three steps, each in its own screen: Vertical (the tilt of the camera, on the
Vertical sheet), Focus (the sheet position and the corner sharpness) and Calibration (the accuracy of the sheet model). A step shows its last result of this session
("not checked", or a tick/cross with the figure). Accept stand opens when every step is fine; the acceptance is the only thing
that is kept (profile.json), the results of the steps live in the session.
Made: Vertical (vertical_window.py), Focus (focus_window.py), Calibration (calibration_dialog.py).
"""
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from patterndxf import appinfo
from patterndxf.ui import theme
from patterndxf.ui.widgets import card

OPEN_STEPS = ("vertical", "focus", "calibration")                    # the steps whose screens are made
STEPS = (("vertical", "Vertical", "The tilt of the camera"),          # (key and icon name, caption, what it is)
         ("focus", "Focus", "Sheet position and corner sharpness"),
         ("calibration", "Calibration", "Accuracy of the sheet model"))


class CommissioningDialog(QDialog):
    def __init__(self, profile, snap_folder=None, parent=None):
        """profile: the Profile in force (its `accepted` is shown); snap_folder: where the steps save their snapshots."""
        super().__init__(parent)
        self.setWindowTitle(f"{appinfo.NAME} {appinfo.version()}: Commissioning")
        self.setModal(True)
        self.setStyleSheet("QLabel, QPushButton { font-size: 16px; }")
        self.profile, self.snap_folder = profile, snap_folder
        self.results = {key: None for key, _, _ in STEPS}        # None = not checked; else (ok, "the figure")
        self.status_labels, self.open_buttons = {}, {}

        title = QLabel("Commissioning")
        title.setStyleSheet(f"color: {theme.KEY}; font-size: 26px")
        hint = QLabel("Check the stand: do Vertical, Focus and Calibration, then press Accept stand.")
        hint.setStyleSheet(f"color: {theme.TEXT2}; font-size: 15px")
        self.stand = QLabel("")

        lay = QVBoxLayout()
        lay.addWidget(title)
        lay.addWidget(hint)
        lay.addWidget(self.stand)
        for key, caption, what in STEPS:
            status, button = QLabel(""), QPushButton("Open")
            button.setEnabled(key in OPEN_STEPS)                  # the screens that exist; the others are "not made yet"
            button.clicked.connect(lambda _=False, k=key: self.open_step(k))
            self.status_labels[key], self.open_buttons[key] = status, button
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(status, 1)
            line.addWidget(button)
            lay.addWidget(card(caption, QLabel(what), row, icon=key))
        lay.addStretch(1)
        self.back_btn = QPushButton("Cancel")
        self.accept_btn = QPushButton("Accept stand")
        self.accept_btn.setObjectName("primary")
        buttons = QHBoxLayout()
        buttons.addWidget(self.back_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.accept_btn)
        lay.addLayout(buttons)
        self.setLayout(lay)
        self.resize(640, 640)
        self.back_btn.clicked.connect(self.reject)
        self.accept_btn.clicked.connect(self.accept)
        self.refresh()

    def open_step(self, key):
        """A step screen opens modally over the hub (the hub stays where it is, locked meanwhile)."""
        if key == "vertical":
            from patterndxf.ui.vertical_window import VerticalDialog
            dialog = VerticalDialog(self.profile, self.snap_folder)
            if dialog.exec() and dialog.result is not None:               # Finish: the figures come to the hub
                self.set_result("vertical", True, f"{dialog.result['tilt']:.1f} deg")
                self.reset_step("focus")                                  # the tilt moves the focus: it is to be checked again
                self.reset_step("calibration")
        elif key == "focus":
            from patterndxf.ui.focus_window import FocusDialog
            dialog = FocusDialog(self.profile, self.snap_folder)
            if dialog.exec() and dialog.result is not None:               # Finish: the worst corner comes to the hub
                self.set_result("focus", True, f"worst corner {dialog.result['worst']:.1f} px")
                self.reset_step("calibration")                            # the focus changes the picture: calibrate again
        elif key == "calibration":
            from patterndxf.ui.calibration_dialog import CalibrationDialog
            dialog = CalibrationDialog(self.profile, self.snap_folder)
            if dialog.exec() and dialog.result is not None:               # Finish: the hold-out p95 comes to the hub
                self.set_result("calibration", True, f"hold-out p95 {dialog.result['p95']:.3f} mm")

    def reset_step(self, key):
        """A step goes back to "not checked" (something it depends on has changed)."""
        self.results[key] = None
        self.refresh()

    def set_result(self, key, ok, figure):
        """A step reports its result (the step screens call this)."""
        self.results[key] = (bool(ok), figure)
        self.refresh()

    def refresh(self):
        acc = self.profile.accepted
        if acc:
            self.stand.setText(f"Stand: accepted {acc.get('date', '')}".rstrip())
            self.stand.setStyleSheet(f"color: {theme.OK}")
        else:
            self.stand.setText("Stand: not accepted")
            self.stand.setStyleSheet(f"color: {theme.WARN}")
        for key, _, _ in STEPS:
            label, res = self.status_labels[key], self.results[key]
            if not self.open_buttons[key].isEnabled() and res is None:
                text, color = "not made yet", theme.TEXT2
            elif res is None:
                text, color = "not checked", theme.TEXT2
            else:
                text, color = (f"✓ {res[1]}", theme.OK) if res[0] else (f"✗ {res[1]}", theme.ERR)
            label.setText(text)
            label.setStyleSheet(f"color: {color}")
        self.accept_btn.setEnabled(all(r is not None and r[0] for r in self.results.values()))
