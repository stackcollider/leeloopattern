"""Small widgets shared by the windows of the new core: the live picture, a titled card, the on/off switch, the Snackbar."""
import cv2
import numpy as np
from PySide6.QtCore import (QElapsedTimer, QEvent, QObject, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QTimer,
                            QVariantAnimation)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon, QImage, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import (QAbstractButton, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from patterndxf.ui import theme


class LiveView(QLabel):
    """The live picture (a BGRA numpy frame), fitted into the window; reduced by area averaging, so no moire on the grid lines."""

    def __init__(self, waiting="Waiting for the camera..."):
        super().__init__(waiting)
        self.setMinimumSize(480, 360)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"background: {theme.DARK}; color: {theme.TEXT2}; border-radius: 8px")
        self._frame = None

    def show_frame(self, bgra):
        self._frame = bgra
        self._paint()

    def clear_frame(self, text):
        self._frame = None
        self.clear()
        self.setText(text)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._paint()

    def _paint(self):
        if self._frame is None:
            return
        h, w = self._frame.shape[:2]
        k = min(self.width() / w, self.height() / h)
        small = np.ascontiguousarray(cv2.resize(self._frame, (max(int(w * k), 1), max(int(h * k), 1)), interpolation=cv2.INTER_AREA))
        img = QImage(small.data, small.shape[1], small.shape[0], 4 * small.shape[1], QImage.Format_RGB32)
        self.setPixmap(QPixmap.fromImage(img))                  # fromImage copies: `small` may go


ICON_PX = 48                                    # the icon on the left of a card


class OutlinedCard(QFrame):
    """A card as the MudBlazor Outlined field (DECISIONS.md 2026-11-20): a thin rounded frame, the title in a gap of its top
    line, the inside a little lighter than the window. Painted by hand (no style sheet background)."""
    RADIUS, TITLE_X, TITLE_PAD = 8, 12, 5

    def __init__(self, title):
        super().__init__()
        self.setObjectName("outlined")
        self.title = title
        f = self.font()
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        self.title_font = f
        fm = QFontMetrics(f)
        self.title_h, self.title_w = fm.height(), fm.horizontalAdvance(title)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        top = self.title_h / 2
        r = QRectF(0.5, top + 0.5, self.width() - 1, self.height() - top - 1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE))
        p.drawRoundedRect(r, self.RADIUS, self.RADIUS)
        gap = QRectF(self.TITLE_X, 0, self.title_w + 2 * self.TITLE_PAD, self.title_h)
        p.save()
        p.setClipRegion(QRegion(self.rect()).subtracted(QRegion(gap.toAlignedRect())))
        p.setPen(QPen(QColor(theme.NEUTRAL), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, self.RADIUS, self.RADIUS)
        p.restore()
        p.setFont(self.title_font)
        p.setPen(QColor(theme.KEY))
        p.drawText(gap, Qt.AlignCenter, self.title)


def card(title, *widgets, icon=None):
    """A titled card. Without an icon: the Outlined card (the title in the frame line). icon: the name of an SVG in
    patterndxf/ui/icons, shown on the left (the tiles of the Commissioning hub keep the filled card with the title inside)."""
    if icon is None:
        frame = OutlinedCard(title)
        box = QVBoxLayout(frame)
        box.setContentsMargins(14, frame.title_h + 6, 14, 12)
        for w in widgets:
            if type(w) is QWidget:                     # a plain row of widgets: no window background inside the card
                w.setObjectName("cardrow")
            box.addWidget(w)
        return frame
    frame = QFrame()
    frame.setObjectName("card")
    box = QVBoxLayout()
    head = QLabel(title)
    head.setStyleSheet(f"color: {theme.KEY}; font-size: 14px; font-weight: 600")
    box.addWidget(head)
    for w in widgets:
        box.addWidget(w)
    picture = QLabel()
    picture.setPixmap(QIcon(str(theme.ICON_PATH.parent / f"{icon}.svg")).pixmap(ICON_PX, ICON_PX))
    picture.setFixedSize(ICON_PX, ICON_PX)
    outer = QHBoxLayout(frame)
    outer.setContentsMargins(14, 10, 12, 10)
    outer.setSpacing(14)
    outer.addWidget(picture, 0, Qt.AlignVCenter)
    box.setContentsMargins(0, 0, 0, 0)
    outer.addLayout(box, 1)
    return frame


class ZoomPicture(QLabel):
    """A live picture (BGR numpy) with a zoom: the wheel zooms at the cursor, a drag moves, a double click fits it.

    The picture is drawn at screen resolution (lines and labels drawn by the owner stay sharp at any zoom). The owner passes an
    `overlay(out, to_view)` to set_frame: to_view(x, y) turns a camera point into a point of the view (float).
    The view shows the camera point `centre` in its middle at `zoom` screen px per camera px (0 = fit the frame)."""
    MAX_ZOOM, WHEEL_STEP = 16.0, 1.25

    def __init__(self, waiting="Waiting for the camera..."):
        super().__init__(waiting)
        self.setMinimumSize(640, 480)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(f"background: {theme.DARK}; color: {theme.TEXT2}; border-radius: 8px")
        self.frame, self.overlay = None, None
        self.zoom, self.centre, self._drag = 0.0, None, None
        self.margins = (0, 0)                           # (top, bottom) px kept free when the frame is fitted: the owner's plates lie there

    def set_frame(self, bgr, overlay=None):
        self.frame, self.overlay = bgr, overlay
        self.render()

    def clear_frame(self, text):
        self.frame = None
        self.clear()
        self.setText(text)

    def fit(self):
        self.zoom, self.centre = 0.0, None
        self.render()

    def view_state(self):
        """(s, cx, cy, fit): the zoom actually shown and the camera point in the middle (kept so that the view stays inside)."""
        h, w = self.frame.shape[:2]
        vw, vh = max(self.width(), 1), max(self.height(), 1)
        fit = min(vw / w, max(vh - sum(self.margins), 1) / h)
        s = self.zoom if self.zoom > fit * 1.0001 else fit
        cx, cy = self.centre if (self.centre and s > fit) else ((w - 1) / 2, (h - 1) / 2)
        hw, hh = vw / (2 * s), vh / (2 * s)
        cx = (w - 1) / 2 if 2 * hw >= w else min(max(cx, hw), w - hw)
        cy = (h - 1) / 2 if 2 * hh >= h else min(max(cy, hh), h - hh)
        return s, cx, cy, fit

    def view_mid(self, s, fit):
        """The point of the view where the camera point `centre` is shown: the middle, or the middle between the margins when fitted."""
        vw, vh = max(self.width(), 1), max(self.height(), 1)
        top, bottom = self.margins if s <= fit * 1.0001 else (0, 0)
        return (vw - 1) / 2, top + (vh - top - bottom - 1) / 2

    def render(self):
        if self.frame is None:
            return
        img = self.frame
        h, w = img.shape[:2]
        s, cx, cy, fit = self.view_state()
        src, k = img, 1.0
        if s < 1:                                       # zoom out: honest averaging, no aliasing
            src, k = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), s
        vw, vh = max(self.width(), 1), max(self.height(), 1)
        mid_x, mid_y = self.view_mid(s, fit)
        m = np.float32([[s / k, 0, mid_x - cx * s], [0, s / k, mid_y - cy * s]])
        interp = cv2.INTER_NEAREST if s >= 2 else cv2.INTER_LINEAR      # zoomed in: the pixels are visible
        out = cv2.warpAffine(src, m, (vw, vh), flags=interp, borderMode=cv2.BORDER_CONSTANT, borderValue=(33, 21, 21))
        if self.overlay is not None:
            self.overlay(out, lambda x, y: (mid_x + (x - cx) * s, mid_y + (y - cy) * s))
        show = cv2.cvtColor(out, cv2.COLOR_BGR2RGB)
        self.setPixmap(QPixmap.fromImage(QImage(show.data, vw, vh, 3 * vw, QImage.Format_RGB888).copy()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.render()

    def wheelEvent(self, e):
        if self.frame is None:
            return
        p = e.position()
        s, cx, cy, fit = self.view_state()
        mx, my = self.view_mid(s, fit)
        fx, fy = cx + (p.x() - mx) / s, cy + (p.y() - my) / s     # the camera point under the cursor stays there
        ns = min(max(s * self.WHEEL_STEP ** (1 if e.angleDelta().y() > 0 else -1), fit), self.MAX_ZOOM)
        nx, ny = self.view_mid(ns, fit)
        self.zoom, self.centre = ns, (fx - (p.x() - nx) / ns, fy - (p.y() - ny) / ns)
        self.render()

    def mousePressEvent(self, e):
        self._drag = e.position()

    def mouseMoveEvent(self, e):
        if self._drag is not None and self.frame is not None and e.buttons() & Qt.LeftButton:
            p = e.position()
            s, cx, cy, fit = self.view_state()
            if s > fit:
                self.centre = (cx - (p.x() - self._drag.x()) / s, cy - (p.y() - self._drag.y()) / s)
                self.render()
            self._drag = p

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.fit()


class Switch(QAbstractButton):
    """An on/off switch as MudSwitch of MudBlazor (DECISIONS.md 2026-11-20): a track and a knob that slides, LeeLoo colours.
    On: a violet knob on a dim violet track; off: a light knob on a grey track. Clicked or toggled() as any checkable button."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 28)
        self._pos = 1.0 if checked else 0.0                   # 0 = off (left) .. 1 = on (right)
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(120)
        self._anim.valueChanged.connect(self._moved)
        self.toggled.connect(self._slide)

    def _moved(self, value):
        self._pos = float(value)
        self.update()

    def _slide(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self):
        return QSize(46, 28)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        w, h, knob = self.width(), self.height(), 20
        on = QColor(theme.PRIMARY)
        track_on, track_off = QColor(on.red(), on.green(), on.blue(), 110), QColor("#4a4a5c")
        t = self._pos
        track = QColor(round(track_off.red() + (track_on.red() - track_off.red()) * t),
                       round(track_off.green() + (track_on.green() - track_off.green()) * t),
                       round(track_off.blue() + (track_on.blue() - track_off.blue()) * t),
                       round(track_off.alpha() + (track_on.alpha() - track_off.alpha()) * t))
        p.setBrush(track if self.isEnabled() else QColor(theme.BTN_DISABLED))
        p.drawRoundedRect(QRectF(4, (h - 14) / 2, w - 8, 14), 7, 7)
        x = 2 + t * (w - knob - 4)
        p.setBrush((on if t >= 0.5 else QColor("#f4f4f8")) if self.isEnabled() else QColor("#8a8a96"))
        p.drawEllipse(QRectF(x, (h - knob) / 2, knob, knob))


class Badge(QWidget):
    """A small plate over a picture: text, and on request a round point before it (LIVE with a blinking red point).
    Drawn by hand, so the point sits exactly in the middle of the height and the whole is centred."""

    def __init__(self, parent=None, width=230, height=46):
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.text, self.colour, self.dot = "", QColor(theme.KEY), None     # dot: None, or the QColor of the point
        self.fill, self.ticks = None, 0                                    # a progress: 0..1 of the plate in violet, the marks

    def show_state(self, text, colour, dot=None, fill=None, ticks=0):
        self.text, self.colour, self.dot = text, QColor(colour), (QColor(dot) if dot else None)
        self.fill, self.ticks = fill, ticks
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(21, 21, 33, 200))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        if self.fill is not None:                                         # the progress: the plate fills violet
            p.setBrush(QColor(126, 111, 255, 170))
            p.drawRoundedRect(QRectF(0, 0, self.width() * min(max(self.fill, 0.0), 1.0), self.height()), 8, 8)
            p.setPen(QColor(255, 255, 255, 60))
            for i in range(1, self.ticks):
                x = self.width() * i / self.ticks
                p.drawLine(QPointF(x, self.height() - 7), QPointF(x, self.height() - 3))
            p.setPen(Qt.NoPen)
        font = p.font()
        font.setPixelSize(17)
        font.setBold(True)
        p.setFont(font)
        text_w = p.fontMetrics().horizontalAdvance(self.text)
        d, gap = 14, 9
        total = text_w + (d + gap if self.dot is not None else 0)
        x = (self.width() - total) / 2
        if self.dot is not None:
            if self.dot.alpha():
                p.setBrush(self.dot)
                p.drawEllipse(QRectF(x, (self.height() - d) / 2, d, d))
            x += d + gap
        p.setPen(self.colour)
        p.drawText(QRectF(x, 0, text_w + 2, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text)


class ScanOverlay(QWidget):
    """A light band that sweeps down over a picture again and again (the outline is being counted); transparent for the mouse.
    start() / stop(); swept() tells whether one whole sweep has been shown."""
    SWEEP_MS = 600

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)
        self.hide()

    def start(self):
        self.clock.start()
        self.timer.start(30)
        self.show()
        self.raise_()

    def stop(self):
        self.timer.stop()
        self.hide()

    def left_ms(self):
        """How long until the first sweep is whole (0 when it is)."""
        return max(0, self.SWEEP_MS - self.clock.elapsed()) if self.clock.isValid() else 0

    def paintEvent(self, _event):
        p = QPainter(self)
        t = (self.clock.elapsed() % self.SWEEP_MS) / self.SWEEP_MS
        y = -80 + (self.height() + 160) * t
        p.setPen(Qt.NoPen)
        for j in range(40):
            p.setBrush(QColor(255, 255, 255, int(70 * (1 - j / 40))))
            p.drawRect(QRectF(0, y - j * 2, self.width(), 2))
        p.setPen(QColor(255, 255, 255, 200))
        p.drawLine(QPointF(0, y), QPointF(self.width(), y))


class _Snack(QWidget):
    """One message of the Snackbar: a coloured plate, an icon, the text, a close cross. Painted by hand."""
    H, PAD, ICON, CLOSE = 48, 14, 22, 40

    def __init__(self, parent, kind, text, on_close):
        super().__init__(parent)
        self.kind, self.text, self.on_close = kind, text, on_close
        self.colour = QColor(Snackbar.KINDS[kind][0])
        self.hover_x = False
        self.closing = False
        self.setMouseTracking(True)
        f = self.font()
        f.setPixelSize(15)
        f.setWeight(QFont.Medium)
        self.setFont(f)
        self.text_w = QFontMetrics(f).horizontalAdvance(text)
        self.fade = QGraphicsOpacityEffect(self)
        self.fade.setOpacity(0.0)
        self.setGraphicsEffect(self.fade)
        self.anim = QPropertyAnimation(self.fade, b"opacity", self)
        self.anim.finished.connect(self.on_anim_end)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.fade_out)

    def natural_width(self):
        return self.PAD + self.ICON + 12 + self.text_w + 12 + self.CLOSE

    def go_to(self, opacity, ms):
        self.anim.stop()
        self.anim.setDuration(ms)
        self.anim.setStartValue(self.fade.opacity())
        self.anim.setEndValue(opacity)
        self.anim.start()

    def fade_in(self):
        self.closing = False
        self.go_to(1.0, Snackbar.FADE_IN_MS)

    def start_timer(self):
        ms = Snackbar.KINDS[self.kind][1]
        if ms:                                                 # an Error stays until it is closed
            self.timer.start(ms)

    def fade_out(self):
        self.timer.stop()
        self.closing = True
        self.go_to(0.0, Snackbar.FADE_OUT_MS)

    def on_anim_end(self):
        if self.closing:
            self.on_close(self)
        elif not self.underMouse():
            self.start_timer()

    def enterEvent(self, event):                               # the pointer on it: it stays, to be read to the end
        self.timer.stop()
        if self.closing:
            self.fade_in()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_x = False
        self.update()
        if not self.closing:
            self.start_timer()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event):
        x = event.position().x() >= self.width() - self.CLOSE
        if x != self.hover_x:
            self.hover_x = x
            self.setCursor(Qt.PointingHandCursor if x else Qt.ArrowCursor)
            self.update()

    def mousePressEvent(self, event):
        if event.position().x() >= self.width() - self.CLOSE:
            self.fade_out()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.colour)
        p.drawRoundedRect(QRectF(self.rect()), 6, 6)
        white = QColor("#ffffff")
        pen = QPen(white, 2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        s, x0, y0 = self.ICON, self.PAD, (self.H - self.ICON) / 2
        cx, cy, r = x0 + s / 2, y0 + s / 2, s / 2 - 1
        if self.kind == "warning":
            p.drawPolygon([QPointF(cx, y0 + 1), QPointF(x0 + s - 1, y0 + s - 2), QPointF(x0 + 1, y0 + s - 2)])
            p.drawLine(QPointF(cx, cy - 3), QPointF(cx, cy + 3))
            p.drawPoint(QPointF(cx, cy + 7))
        else:
            p.drawEllipse(QPointF(cx, cy), r, r)
            if self.kind == "success":
                p.drawPolyline([QPointF(cx - 5, cy), QPointF(cx - 1.5, cy + 4), QPointF(cx + 5.5, cy - 4)])
            elif self.kind == "info":
                p.drawLine(QPointF(cx, cy - 1), QPointF(cx, cy + 5))
                p.drawPoint(QPointF(cx, cy - 5))
            else:
                p.drawLine(QPointF(cx, cy - 5), QPointF(cx, cy + 1))
                p.drawPoint(QPointF(cx, cy + 5))
        tx = x0 + s + 12
        tw = self.width() - tx - self.CLOSE - 4
        p.setPen(white)
        p.drawText(QRectF(tx, 0, tw, self.H), Qt.AlignVCenter | Qt.AlignLeft,
                   QFontMetrics(self.font()).elidedText(self.text, Qt.ElideRight, int(tw)))
        c = QPointF(self.width() - self.CLOSE / 2, self.H / 2)
        if self.hover_x:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 50))
            p.drawEllipse(c, 14, 14)
        p.setPen(pen)
        p.drawLine(c + QPointF(-5, -5), c + QPointF(5, 5))
        p.drawLine(c + QPointF(-5, 5), c + QPointF(5, -5))


class Snackbar(QObject):
    """Short messages on a widget, as the MudBlazor Snackbar (DECISIONS.md 2026-11-20): bottom left, the newest at the
    bottom, at most 3. Info and Success go after 2 s, Warning after 4 s, an Error stays until its cross is clicked;
    the pointer on a message keeps it. avoid: a widget at the bottom of the same parent that the messages must not cover."""
    KINDS = {"info": (theme.INFO, 2000), "success": (theme.OK, 2000), "warning": (theme.WARN, 4000), "error": (theme.ERR, 0)}
    FADE_IN_MS, FADE_OUT_MS, MAX, MARGIN, GAP, MAX_W = 200, 500, 3, 14, 8, 460

    def __init__(self, parent, avoid=None):
        super().__init__(parent)
        self.host, self.avoid, self.items = parent, avoid, []
        parent.installEventFilter(self)

    def show_message(self, kind, text):
        while len(self.items) >= self.MAX:                     # the oldest goes at once
            self.remove(self.items[0])
        item = _Snack(self.host, kind, text, self.remove)
        self.items.append(item)
        self.place()
        item.show()
        item.raise_()
        item.fade_in()
        return item

    def remove(self, item):
        if item in self.items:
            self.items.remove(item)
            item.hide()
            item.deleteLater()
            self.place()

    def place(self):
        bottom = self.host.height() - self.MARGIN
        room = self.host.width() - 2 * self.MARGIN
        w = max([260] + [item.natural_width() for item in self.items])   # one width for all, as the widest
        w = min(w, self.MAX_W, room)
        for item in reversed(self.items):
            if self.avoid is not None and self.avoid.isVisible() and self.MARGIN + w > self.avoid.x() - self.GAP:
                bottom = min(bottom, self.avoid.y() - self.GAP)   # it would cover the badge: it stands above it
            item.setGeometry(self.MARGIN, bottom - _Snack.H, w, _Snack.H)
            bottom -= _Snack.H + self.GAP

    def eventFilter(self, obj, event):
        if obj is self.host and event.type() == QEvent.Resize:
            self.place()
        return False
