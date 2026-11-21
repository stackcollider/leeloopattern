"""Makes patterndxf/ui/icons/app.ico from app.svg (the icon of the program, variant C chosen 2026-11-19).

Run: .venv-cam\\Scripts\\python tools\\make_app_icon.py
The .ico holds PNG pictures of every size Windows asks for (16 ... 256 px), drawn from the SVG by Qt.
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

ICONS = Path(__file__).resolve().parent.parent / "patterndxf" / "ui" / "icons"
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def png_of(renderer, size):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p, QRectF(0, 0, size, size))
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def main():
    app = QApplication(sys.argv)                                  # Qt draws the SVG only with an application
    renderer = QSvgRenderer(str(ICONS / "app.svg"))
    pngs = [png_of(renderer, s) for s in SIZES]
    head = struct.pack("<HHH", 0, 1, len(SIZES))                  # an icon file with this many pictures
    offset = 6 + 16 * len(SIZES)
    entries = b""
    for size, png in zip(SIZES, pngs):
        w = 0 if size >= 256 else size                            # 0 means 256 in an icon directory
        entries += struct.pack("<BBBBHHII", w, w, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
    (ICONS / "app.ico").write_bytes(head + entries + b"".join(pngs))
    print(ICONS / "app.ico", sum(len(p) for p in pngs), "bytes of pictures")
    app.quit()


if __name__ == "__main__":
    main()
