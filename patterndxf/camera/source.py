"""CameraSource: the camera of the profile through the Windows camera API (MediaCapture), no window.

Live frames arrive on the asyncio thread of this object; the owner reads them from `latest` (a BGRA numpy picture, a copy
the owner may keep) after the `frame` signal. A full-size photo is taken while the live video goes on (the Camera app does the
same), the live video stops for about a second and is set back afterwards.

Python: not the Microsoft Store one (it gets "Access is denied" from MediaCapture) and with the winrt packages
(requirements-focus.txt, to be renamed when the old camera code is gone). Only one program can hold the camera at a time.
"""
import asyncio
import datetime
import threading
import time
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PySide6.QtCore import QObject, Signal
from winrt.windows.devices.enumeration import DeviceClass, DeviceInformation
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.capture import (MediaCapture, MediaCaptureInitializationSettings, MediaCaptureMemoryPreference,
                                         MediaStreamType, StreamingCaptureMode)
from winrt.windows.media.capture.frames import MediaFrameSourceKind
from winrt.windows.media.mediaproperties import ImageEncodingProperties, VideoEncodingProperties
from winrt.windows.storage.streams import Buffer, DataReader, InMemoryRandomAccessStream

from patterndxf.camera.match import CameraInfo, choose

MIN_FPS = 5                                    # slower video modes are not offered


class VideoMode(NamedTuple):
    width: int
    height: int
    fps: int

    @property
    def label(self):
        return f"{self.height}p {_aspect(self.width, self.height)} {self.fps}fps  ({self.width}x{self.height})"


class PhotoMode(NamedTuple):
    width: int
    height: int

    @property
    def label(self):
        return f"{self.width * self.height / 1e6:.1f}MP {_aspect(self.width, self.height)} ({self.width}x{self.height})"


def _aspect(w, h):
    r = w / h
    return "4:3" if abs(r - 4 / 3) < 0.02 else "16:9" if abs(r - 16 / 9) < 0.02 else f"{r:.2f}"


def _mode_of(props):
    """(width, height, fps, subtype) of a video encoding property of the camera."""
    q = props.as_(VideoEncodingProperties)                      # the list gives the base interface; ask for the video one
    fps = round(q.frame_rate.numerator / q.frame_rate.denominator) if q.frame_rate.denominator else 0
    return q.width, q.height, fps, q.subtype


def list_cameras():
    """The video cameras of the computer now: [CameraInfo]. Blocks for a moment (about 0.1 s); call it from any thread."""
    async def find():
        found = await DeviceInformation.find_all_async(DeviceClass.VIDEO_CAPTURE)
        return [CameraInfo(d.name, d.id) for d in found]
    return asyncio.run(find())


class CameraSource(QObject):
    frame = Signal()                   # a new live frame is in `latest`
    opened = Signal(str)               # the camera is open: its name
    failed = Signal(str)               # a plain line for the log or the operator
    modes = Signal(list, list)         # the video modes (VideoMode) and the photo modes (PhotoMode), best first
    photo_done = Signal(str, float, int, int, int)      # path, seconds, width, height, bytes

    def __init__(self, name="", vid_pid="", video=None, max_width=None):
        """name, vid_pid: the camera of the profile ('' = no restriction, then exactly one camera must exist);
        video: the VideoMode to start with (default: the biggest one); max_width: without `video`, the biggest mode
        that is not wider than this (a screen that needs a fast live picture asks for a small one)."""
        super().__init__()
        self.name, self.vid_pid, self.start_video, self.max_width = name, vid_pid, video, max_width
        self.camera = None             # CameraInfo of the open camera
        self.latest = None             # the newest live frame: BGRA numpy picture (h, w, 4), or None
        self.size = None               # (width, height) of `latest`
        self.frames = 0                # live frames since the start (the owner counts the speed)
        self.busy = False              # a photo is being taken
        self.video_mode = None         # the VideoMode of the live video now
        self.start_mode = None         # the VideoMode the live video starts in (known when `modes` is emitted)
        self._mc = self._src = self._reader = None
        self._open_future = None       # the opening in progress (close() waits for it: a camera opened after close would stay held)
        self._closing = False
        self._video = {}               # VideoMode -> frame format of the frame source
        self._photo = {}               # PhotoMode -> media encoding properties of the PHOTO stream
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    # ----- running coroutines on the camera thread
    def _run(self, coro):
        return asyncio.run_coroutine_threadsafe(self._guard(coro), self.loop)

    async def _guard(self, coro):
        try:
            await coro
        except Exception as e:                                  # a plain line, never a silent death of the thread
            self.busy = False
            self.failed.emit(f"{type(e).__name__}: {e}")

    # ----- open
    def open(self):
        self._open_future = self._run(self._open())

    async def _open(self):
        found = await DeviceInformation.find_all_async(DeviceClass.VIDEO_CAPTURE)
        cameras = [CameraInfo(d.name, d.id) for d in found]
        camera, problem = choose(cameras, self.name, self.vid_pid)
        if camera is None:
            self.failed.emit(problem)
            return
        self.camera = camera
        self._mc = MediaCapture()
        st = MediaCaptureInitializationSettings()
        st.video_device_id = camera.device_id
        st.streaming_capture_mode = StreamingCaptureMode.VIDEO
        st.memory_preference = MediaCaptureMemoryPreference.CPU
        await self._mc.initialize_async(st)
        if self._closing:                                       # closed while opening: let the camera go at once
            self._mc.close()
            self._mc = None
            return
        self._src = next(s for s in self._mc.frame_sources.values() if s.info.source_kind == MediaFrameSourceKind.COLOR)
        for fmt in self._src.supported_formats:                 # the video modes (MJPG preferred over NV12 and YUY2)
            v, fr = fmt.video_format, fmt.frame_rate
            if not fr.denominator:
                continue
            mode = VideoMode(v.width, v.height, round(fr.numerator / fr.denominator))
            if mode.fps >= MIN_FPS and (mode not in self._video or fmt.subtype == "MJPG"):
                self._video[mode] = fmt
        for p in self._mc.video_device_controller.get_available_media_stream_properties(MediaStreamType.PHOTO):
            w, h, _, sub = _mode_of(p)
            if PhotoMode(w, h) not in self._photo or sub == "MJPG":
                self._photo[PhotoMode(w, h)] = p
        video = sorted(self._video, key=lambda m: (-m.width * m.height, -m.fps))
        photo = sorted(self._photo, key=lambda m: -m.width * m.height)
        first = video[0]
        if self.max_width is not None:
            first = next((m for m in video if m.width <= self.max_width), video[-1])
        self.start_mode = self.start_video if self.start_video in self._video else first
        self.modes.emit(video, photo)
        self.opened.emit(camera.name)
        await self._set_video(self.start_mode)
        if self._closing:
            self._mc.close()
            self._mc = None
            return
        self._reader = await self._mc.create_frame_reader_async(self._src)
        self._reader.add_frame_arrived(self._on_frame)
        await self._reader.start_async()

    def _on_frame(self, reader, args):
        f = reader.try_acquire_latest_frame()
        if f is None:
            return
        try:
            sb = f.video_media_frame.software_bitmap
            if sb.bitmap_pixel_format != BitmapPixelFormat.BGRA8:
                sb = SoftwareBitmap.convert(sb, BitmapPixelFormat.BGRA8)
            w, h = sb.pixel_width, sb.pixel_height
            buf = Buffer(w * h * 4)
            sb.copy_to_buffer(buf)
            self.latest = np.frombuffer(memoryview(buf), np.uint8).reshape(h, w, 4).copy()
            self.size = (w, h)
            self.frames += 1
            self.frame.emit()
        except Exception as e:
            self.failed.emit(f"frame: {type(e).__name__}: {e}")
        finally:
            f.close()

    # ----- the video mode
    async def _set_video(self, mode):
        self.video_mode = mode
        await self._src.set_format_async(self._video[mode])

    def choose_video(self, mode):
        self._run(self._set_video(mode))

    # ----- a photo (the live video stops for about a second and is set back afterwards)
    def snap(self, mode, folder):
        self.busy = True
        self._run(self._snap(mode, Path(folder)))

    async def _snap(self, mode, folder):
        t0 = time.perf_counter()
        await self._mc.video_device_controller.set_media_stream_properties_async(MediaStreamType.PHOTO, self._photo[mode])
        stream = InMemoryRandomAccessStream()
        await self._mc.capture_photo_to_stream_async(ImageEncodingProperties.create_jpeg(), stream)
        size = stream.size
        stream.seek(0)
        reader = DataReader(stream)
        await reader.load_async(size)
        data = bytearray(size)
        reader.read_bytes(data)
        seconds = time.perf_counter() - t0
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"photo_{datetime.datetime.now():%Y%m%d_%H%M%S}.jpg"
        path.write_bytes(bytes(data))
        await self._set_video(self.video_mode)          # the photo leaves the live video at 10 fps: set the mode again
        self.busy = False
        self.photo_done.emit(str(path), seconds, mode.width, mode.height, size)

    # ----- close
    def close(self):
        self._closing = True
        if self._open_future is not None:                       # the opening ends first (it lets go of the camera itself when closing)
            try:
                self._open_future.result(5)
            except Exception:
                pass

        async def stop():
            if self._reader is not None:
                await self._reader.stop_async()
            if self._mc is not None:
                self._mc.close()
        try:
            asyncio.run_coroutine_threadsafe(stop(), self.loop).result(5)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
