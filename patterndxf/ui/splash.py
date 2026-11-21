"""The splash window of LeeLooPattern (design: docs/design/splash, from Claude Design) (the start-up steps: patterndxf.flow).

The look follows the hand-off exactly: 640 x 360, borderless, background #1a1a27, the mark (a pattern piece on a faint grid),
name, tagline, version, one status line with the step counter, a thin progress bar, the copyright. Two states: normal and a
failed step (amber, warning triangle, bar full). The steps are the real ones of patterndxf.flow.
"""

from PySide6.QtCore import QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from patterndxf import appinfo

W, H = 640, 360
from patterndxf.ui.theme import BG, LINE as TRACK, PRIMARY as VIOLET, TEXT, TEXT2 as SECOND, KEY, WARN as AMBER   # the shared look
STEPS = ["Checking settings", "Checking folders", "Checking camera profile", "Looking for the camera",
         "Checking the stand"]                         # the checks of patterndxf.flow.StartWorker


def pick_screen():
    """The screen the operator is looking at: the one under the mouse cursor (else the primary). The splash, the main window
    and the dialogs all go to this one screen, so nothing opens on the other monitor."""
    return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()


def center_on(widget, screen):
    """Put a window in the middle of the screen, made smaller if it does not fit."""
    area = screen.availableGeometry()
    width, height = min(widget.width(), int(area.width() * 0.96)), min(widget.height(), int(area.height() * 0.96))
    if (width, height) != (widget.width(), widget.height()):
        widget.resize(width, height)
    widget.move(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2)


class SplashWindow(QWidget):
    def __init__(self, screen=None):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setFixedSize(W, H)
        self.step, self.count, self.text, self.error = 0, len(STEPS), "Starting", False
        self._frac = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.valueChanged.connect(self._on_frac)
        center_on(self, screen or pick_screen())

    def _on_frac(self, value):
        self._frac = float(value)
        self.update()

    def set_step(self, step, text):
        self.step, self.text, self.error = step, text, False
        self._animate(step / self.count)

    def set_error(self, text):
        self.step, self.text, self.error = self.count, text, True
        self._animate(1.0)

    def _animate(self, target):
        self._anim.stop()
        self._anim.setStartValue(self._frac)
        self._anim.setEndValue(target)
        self._anim.start()
        self.update()

    @staticmethod
    def _font(size, weight=QFont.Normal, mono=False):
        f = QFont()
        f.setFamilies(["Roboto Mono", "Consolas"] if mono else ["Roboto", "Segoe UI", "Arial"])
        f.setPixelSize(size)
        f.setWeight(weight)
        return f

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(BG))
        # the mark: 112 x 80, faint grid and the outline of a pattern piece with one small notch
        x0, y0 = (W - 112) / 2, 44
        p.save()
        p.translate(x0, y0)
        grid = QColor(VIOLET)
        grid.setAlphaF(0.16)
        p.setPen(QPen(grid, 1))
        for y in (8, 24, 40, 56, 72):
            p.drawLine(QPointF(0, y), QPointF(112, y))
        for x in (8, 24, 40, 56, 72, 88, 104):
            p.drawLine(QPointF(x, 0), QPointF(x, 80))
        outline = QPainterPath(QPointF(22, 70))
        outline.cubicTo(18, 48, 26, 26, 44, 16)
        outline.cubicTo(56, 9, 72, 8, 84, 15)
        outline.cubicTo(96, 23, 98, 46, 92, 70)
        outline.closeSubpath()
        pen = QPen(QColor(VIOLET), 2.5)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawPath(outline)
        notch = QPainterPath(QPointF(60, 9.5))
        notch.lineTo(63, 16)
        notch.lineTo(66, 10)
        p.drawPath(notch)
        p.restore()
        # name, tagline, version
        y = y0 + 80 + 18
        for text, font, color, gap in ((appinfo.NAME, self._font(36, QFont.Light), KEY, 4),
                                       (appinfo.TAGLINE, self._font(13), SECOND, 4),
                                       (f"v{appinfo.version()}", self._font(11), SECOND, 0)):
            if text == appinfo.NAME:
                font.setLetterSpacing(QFont.AbsoluteSpacing, 0.36)
            p.setFont(font)
            p.setPen(QColor(color))
            h = QFontMetricsF(font).height()
            p.drawText(QRectF(0, y, W, h), Qt.AlignHCenter | Qt.AlignVCenter, text)
            y += h + gap
        # copyright at the bottom, the status block 28 px above it
        cf = self._font(11)
        ch = QFontMetricsF(cf).height()
        p.setFont(cf)
        p.setPen(QColor(SECOND))
        p.drawText(QRectF(0, H - 16 - ch, W, ch), Qt.AlignHCenter | Qt.AlignVCenter, appinfo.copyright_line())
        bar_bottom = H - 16 - ch - 28
        color = AMBER if self.error else VIOLET
        track = QRectF((W - 400) / 2, bar_bottom - 3, 400, 3)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(TRACK))
        p.drawRoundedRect(track, 2, 2)
        p.setBrush(QColor(color))
        p.drawRoundedRect(QRectF(track.x(), track.y(), track.width() * max(self._frac, 0.0), 3), 2, 2)
        # status line: text on the left (with the warning triangle on error), the counter on the right
        sf = self._font(13)
        sh = QFontMetricsF(sf).height()
        row_y = track.y() - 8 - sh
        left = track.x()
        if self.error:
            p.save()
            p.translate(left, row_y + (sh - 16) / 2)
            p.scale(16 / 24, 16 / 24)
            pen = QPen(QColor(AMBER), 2.2)
            pen.setJoinStyle(Qt.RoundJoin)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            tri = QPainterPath(QPointF(12, 3.5))
            tri.lineTo(21.5, 20)
            tri.lineTo(2.5, 20)
            tri.closeSubpath()
            p.drawPath(tri)
            p.drawLine(QPointF(12, 10), QPointF(12, 14.5))
            p.drawPoint(QPointF(12, 17.3))
            p.restore()
            left += 16 + 8
        p.setFont(sf)
        p.setPen(QColor(AMBER if self.error else TEXT))
        p.drawText(QRectF(left, row_y, track.width() - (left - track.x()) - 50, sh), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        mf = self._font(11, mono=True)
        p.setFont(mf)
        p.setPen(QColor(AMBER if self.error else SECOND))
        p.drawText(QRectF(track.right() - 50, row_y, 50, sh), Qt.AlignRight | Qt.AlignVCenter, f"{self.step} / {self.count}")
        p.end()

