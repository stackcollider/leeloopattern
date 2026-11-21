"""The LeeLoo look in one place (DECISIONS.md 2026-10-19: the MudBlazor palette; 2026-11-12: Segoe UI, one theme).

Every window takes its colours and its Qt style sheet from here, so a colour is changed once.
The camera picture keeps its own functional colours (red cross, green point): they are for contrast, not for design."""

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QPushButton

# the dark theme
BG, SURFACE, DARK, LINE, DIVIDER = "#1a1a27", "#1e1e2d", "#151521", "#33323e", "#292838"
PRIMARY, TEXT, TEXT2, KEY = "#7e6fff", "#b2b0bf", "#92929f", "#f1f5f0"
OK, WARN, ERR, INFO, TERTIARY = "#3dcb6c", "#ffb545", "#ff3f5f", "#4a86ff", "#1dbb9d"
NEUTRAL = "#74718e"                         # ActionDefault: a skipped or unknown state
DISABLED_BG, DISABLED_TEXT = "#605f6d4d", "#ffffff33"
# the buttons: the MudBlazor "Filled" look (colours taken from the MudBlazor documentation picture, DECISIONS.md 2026-11-15).
# Default, Primary (object name "primary"), Disabled. Secondary (pink #ff4081) only on request.
BTN, BTN_HOVER, BTN_PRESSED = "#232332", "#2c2c3f", "#353549"
PRIMARY_HOVER, PRIMARY_PRESSED = "#9186ff", "#6a5bf0"
BTN_DISABLED, BTN_DISABLED_TEXT = "#323240", "#51515b"

FONT = '"Segoe UI", Arial'                  # the decision of 2026-11-12 (it is on every Windows, nothing to ship)
MONO = "Consolas, monospace"                # tables of numbers keep their columns

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: {FONT}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QFrame#card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 12px; }}
QFrame#outlined {{ background: transparent; }}
QWidget#cardrow {{ background: transparent; }}
QFrame#thumb {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 10px; }}
QFrame#thumb[selected="true"] {{ border: 2px solid {PRIMARY}; }}
QPushButton {{ background: {BTN}; color: {TEXT}; border: 0; border-radius: 4px; padding: 6px 16px; font-size: 16px; font-weight: 500; }}
QPushButton:hover {{ background: {BTN_HOVER}; }}
QPushButton:pressed {{ background: {BTN_PRESSED}; }}
QPushButton:checked {{ background: {BTN_PRESSED}; color: {PRIMARY_HOVER}; border: 1px solid {PRIMARY}; }}
QPushButton#primary {{ background: {PRIMARY}; color: #ffffff; }}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton#primary:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton:disabled, QPushButton#primary:disabled {{ background: {BTN_DISABLED}; color: {BTN_DISABLED_TEXT}; }}
QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; background: {PRIMARY}; }}
QSlider::sub-page:horizontal:disabled {{ background: #605f6d; }}      /* the state after the sub-control, or Qt applies it always */
QSlider::handle:horizontal:disabled {{ background: #605f6d; }}
QGraphicsView {{ background: {DARK}; border: 0; border-radius: 8px; }}
QPlainTextEdit {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {LINE}; border-radius: 8px;
                  font-family: {MONO}; font-size: 12px; }}
"""


class _UpperButtons(QObject):
    """The labels of all push buttons are shown in capitals (the MudBlazor look); the text itself stays as written."""

    def eventFilter(self, obj, event):
        if isinstance(obj, QPushButton) and event.type() in (QEvent.Polish, QEvent.Show):
            font = obj.font()
            if font.capitalization() != QFont.AllUppercase:
                font.setCapitalization(QFont.AllUppercase)
                obj.setFont(font)
        return False


_upper = None


def apply(app):
    """The style sheet and the capitals of the buttons for the whole application (call once, after QApplication is made)."""
    global _upper
    app.setStyleSheet(STYLE)
    if _upper is None:
        _upper = _UpperButtons(app)
        app.installEventFilter(_upper)


ICON_PATH = Path(__file__).resolve().parent / "icons" / "app.ico"     # the icon of the program (variant C, 2026-11-19)
APP_ID = "StackCollider.LeeLooPattern"                                 # who the windows belong to, for the Windows taskbar


def set_app_icon(app):
    """The icon of every window and of the taskbar button. Without its own AppUserModelID a program run by python.exe shows
    the icon of Python on the taskbar. Call once, right after QApplication is made."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (AttributeError, OSError):
            pass                                                   # an old Windows: the window icon is still set
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))
