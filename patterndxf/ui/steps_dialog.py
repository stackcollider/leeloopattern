"""A dialog that shows a list of steps while they are done (first used by the Calibration capture, 2026-11-19).

Every row: an icon, the name, a figure on the right. The states: waiting (grey dot), running (a turning arc, the row lit),
done (a green tick that grows in), done with a warning (a yellow tick), failed (a red cross and the reason under the row).
At the end: everything fine -> the summary in green and the dialog closes by itself after AUTO_CLOSE_MS; a warning -> the
summary in yellow, Restart and Close; a failure -> it stops there, Restart and Close. Cancel works while the steps run.
live=True: a live picture on the left (the owner gives every frame by show_frame, with its own drawing: what is wrong in red).
The dialog only shows: the owner calls start / value / finish / fail / end / show_frame and listens to cancel_requested /
restart_requested.
"""
import time

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from patterndxf.ui import theme
from patterndxf.ui.widgets import ZoomPicture

AUTO_CLOSE_MS = 1500
ICON_PX = 24
WAIT, RUN, OK, WARN, FAIL, STOPPED = "wait", "run", "ok", "warn", "fail", "stopped"


class StepIcon(QWidget):
    """The icon of a row: a dot, a turning arc, a tick (green / yellow) or a cross that grows in."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(ICON_PX, ICON_PX)
        self.state, self.angle, self.grow = WAIT, 0, 1.0
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(320)
        self.anim.setEasingCurve(QEasingCurve.OutBack)
        self.anim.valueChanged.connect(self._grown)

    def _grown(self, v):
        self.grow = float(v)
        self.update()

    def set_state(self, state):
        self.state = state
        if state in (OK, WARN, FAIL):
            self.anim.stop()
            self.anim.setStartValue(0.0)
            self.anim.setEndValue(1.0)
            self.anim.start()
        else:
            self.grow = 1.0
        self.update()

    def turn(self):
        if self.state == RUN:
            self.angle = (self.angle + 12) % 360
            self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QPointF(ICON_PX / 2, ICON_PX / 2)
        if self.state in (WAIT, STOPPED):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.NEUTRAL))
            p.drawEllipse(c, 3.5, 3.5)
        elif self.state == RUN:
            pen = QPen(QColor(theme.PRIMARY), 2.6)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(QRectF(3, 3, ICON_PX - 6, ICON_PX - 6), -self.angle * 16, 270 * 16)
        else:
            colour = {OK: theme.OK, WARN: theme.WARN, FAIL: theme.ERR}[self.state]
            r = (ICON_PX / 2 - 1) * self.grow
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(colour))
            p.drawEllipse(c, r, r)
            pen = QPen(QColor("white"), 2.4)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.translate(c)
            p.scale(self.grow, self.grow)
            path = QPainterPath()
            if self.state == FAIL:
                path.moveTo(-4.5, -4.5)
                path.lineTo(4.5, 4.5)
                path.moveTo(4.5, -4.5)
                path.lineTo(-4.5, 4.5)
            else:
                path.moveTo(-5, 0.5)
                path.lineTo(-1.5, 4)
                path.lineTo(5.5, -4)
            p.drawPath(path)
        p.end()


class StepRow(QFrame):
    def __init__(self, name):
        super().__init__()
        self.icon = StepIcon()
        self.name = QLabel(name)
        self.value = QLabel("")
        self.value.setStyleSheet(f"color: {theme.TEXT2}; font-family: {theme.MONO}; font-size: 13px")
        self.reason = QLabel("")
        self.reason.setWordWrap(True)
        self.reason.setStyleSheet(f"color: {theme.ERR}; font-size: 13px")
        self.reason.hide()
        line = QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(12)
        line.addWidget(self.icon)
        line.addWidget(self.name, 1)
        line.addWidget(self.value)
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 6, 12, 6)
        box.setSpacing(2)
        box.addLayout(line)
        box.addWidget(self.reason)
        self.started = None
        self.set_state(WAIT)

    def set_state(self, state):
        self.icon.set_state(state)
        lit = state == RUN
        self.setStyleSheet(f"StepRow {{ background: {theme.BTN_HOVER if lit else 'transparent'}; border-radius: 8px; "
                           f"border-left: 3px solid {theme.PRIMARY if lit else 'transparent'}; }}")
        colour = theme.KEY if state in (RUN, OK, WARN, FAIL) else theme.TEXT2
        self.name.setStyleSheet(f"color: {colour}; font-size: 15px")


class StepsDialog(QDialog):
    cancel_requested = Signal()
    restart_requested = Signal()

    def __init__(self, title, steps, parent=None, live=False):
        """steps: [(key, name)] in their order; live: a live picture on the left."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.keys = [k for k, _ in steps]
        self.rows = {k: StepRow(name) for k, name in steps}
        head = QLabel(title)
        head.setStyleSheet(f"color: {theme.KEY}; font-size: 22px")
        self.bar = QProgressBar()
        self.bar.setRange(0, len(steps))
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        self.bar.setStyleSheet(f"QProgressBar {{ background: {theme.LINE}; border: none; border-radius: 3px; }}"
                               f"QProgressBar::chunk {{ background: {theme.PRIMARY}; border-radius: 3px; }}")
        self.summary = QLabel("")
        self.summary.setAlignment(Qt.AlignCenter)
        self.summary.setWordWrap(True)
        self.summary.hide()
        self.cancel_btn, self.retry_btn, self.close_btn = QPushButton("Cancel"), QPushButton("Restart"), QPushButton("Close")
        self.retry_btn.setObjectName("primary")
        for b in (self.cancel_btn, self.retry_btn, self.close_btn):
            b.setMinimumHeight(40)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.retry_btn)
        buttons.addWidget(self.close_btn)
        lay = QVBoxLayout()
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(head)
        lay.addSpacing(6)
        for k in self.keys:
            lay.addWidget(self.rows[k])
        lay.addSpacing(8)
        lay.addWidget(self.bar)
        lay.addWidget(self.summary)
        lay.addStretch(1)
        lay.addLayout(buttons)
        column = QWidget()
        column.setFixedWidth(560)
        column.setLayout(lay)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(18)
        self.picture = None
        if live:                                         # the picture on the left, the steps on the right
            self.picture = ZoomPicture("Waiting for the camera ...")
            self.picture.setMinimumSize(720, 540)
            outer.addWidget(self.picture, 1)
        outer.addWidget(column)
        self.spin = QTimer(self)
        self.spin.timeout.connect(self._turn)
        self.spin.start(30)
        self.closer = QTimer(self)
        self.closer.setSingleShot(True)
        self.closer.timeout.connect(self.accept)
        self.cancel_btn.clicked.connect(self._cancel)
        self.retry_btn.clicked.connect(self.restart_requested)
        self.close_btn.clicked.connect(self.reject)
        self.reset()

    def reset(self):
        """Every row waiting again (a new run)."""
        self.closer.stop()
        for row in self.rows.values():
            row.set_state(WAIT)
            row.value.setText("")
            row.reason.hide()
            row.started = None
        self.done_count = 0
        self.bar.setValue(0)
        self.summary.hide()
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setText("Cancel")
        self.cancel_btn.show()
        self.retry_btn.hide()
        self.close_btn.hide()
        self.adjustSize()

    def _turn(self):
        for row in self.rows.values():
            row.icon.turn()

    def _cancel(self):
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setText("Stopping ...")
        self.cancel_requested.emit()

    # ----- what the owner calls
    def start(self, key):
        row = self.rows[key]
        row.started = time.perf_counter()
        row.set_state(RUN)

    def value(self, key, text):
        self.rows[key].value.setText(text)

    def finish(self, key, text="", kind=OK):
        """The step is done: a tick (kind OK or WARN), the figure and the time on the right."""
        row = self.rows[key]
        seconds = time.perf_counter() - row.started if row.started else None
        row.value.setText(text + (f"   {seconds:.1f} s" if seconds is not None and seconds >= 0.05 else ""))
        row.set_state(kind)
        self.done_count += 1
        self.bar.setValue(self.done_count)

    def show_frame(self, bgr, overlay=None):
        """A new live picture (live=True only); overlay(out, to_view) draws on it, as for ZoomPicture."""
        if self.picture is not None:
            self.picture.set_frame(bgr, overlay)

    def reason(self, key, text, colour=None):
        """Changes the text under a failed row (a live check: what is still wrong, or that it is fine now)."""
        row = self.rows[key]
        row.reason.setText(text)
        row.reason.setStyleSheet(f"color: {colour or theme.ERR}; font-size: 13px")

    def fail(self, key, reason):
        """The step failed: the cross, the reason under it; it stops there (Restart, Close)."""
        row = self.rows[key]
        row.set_state(FAIL)
        self.reason(key, reason)
        row.reason.show()
        self.cancel_btn.hide()
        self.retry_btn.show()
        self.close_btn.show()
        self.adjustSize()

    def stopped(self, key):
        """Cancel has taken effect: the row that ran is greyed and the dialog closes."""
        if key in self.rows:
            self.rows[key].set_state(STOPPED)
        self.reject()

    def end(self, summary, kind=OK):
        """All steps done: the summary; OK closes by itself, WARN waits for Close."""
        colour = theme.OK if kind == OK else theme.WARN
        self.summary.setText(summary)
        self.summary.setStyleSheet(f"color: {colour}; font-size: 20px; font-weight: 600; padding: 6px")
        self.summary.show()
        self.cancel_btn.hide()
        if kind == OK:
            self.closer.start(AUTO_CLOSE_MS)
        else:
            self.retry_btn.show()
            self.close_btn.show()
        self.adjustSize()
