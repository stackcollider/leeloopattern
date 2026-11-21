"""A snapshot instead of the camera (the developer's --sample <folder>, DECISIONS.md 2026-11-20): the screens that open the
camera (patterndxf/ui/sheet_screen.py) get the frames of a folder made by tools/grab_frames.py, so Measure can be tried
without the camera and without the light.

The folder: color.png (the colour frame, shown and measured); its size is the video mode. The frame is given again and
again, FPS times a second, as a camera would. The same signals and fields as CameraSource that the screens use.
"""
from pathlib import Path

import cv2
from PySide6.QtCore import QObject, QTimer, Signal

from patterndxf.camera.source import VideoMode

FPS = 10
FOLDER = None                                  # set by leeloopattern.py --sample: then make_source gives a SampleSource


class SampleSource(QObject):
    frame = Signal()
    opened = Signal(str)
    failed = Signal(str)
    modes = Signal(list, list)

    def __init__(self, folder):
        super().__init__()
        self.folder = Path(folder)
        self.latest, self.size, self.frames = None, None, 0
        self.video_mode = self.start_mode = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

    def open(self):
        bgr = cv2.imread(str(self.folder / "color.png"))
        if bgr is None:
            self.failed.emit(f"No snapshot in {self.folder} (color.png)")
            return
        self.latest = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
        self.size = bgr.shape[1], bgr.shape[0]
        self.video_mode = self.start_mode = VideoMode(*self.size, FPS)
        self.opened.emit(f"Snapshot {self.folder.name}")
        self.modes.emit([self.video_mode], [])
        self.timer.start(1000 // FPS)

    def _tick(self):
        self.frames += 1
        self.frame.emit()

    def choose_video(self, _mode):
        """One mode only: the size of the snapshot."""

    def close(self):
        self.timer.stop()


def make_source(**camera_args):
    """The camera of the profile, or the snapshot when --sample was given."""
    if FOLDER is not None:
        return SampleSource(FOLDER)
    from patterndxf.camera.source import CameraSource
    return CameraSource(**camera_args)
