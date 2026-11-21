"""Grabs N frames of the camera of the profile (its video mode) and saves them for offline work: frames, their median, colour.

Run: .venv-cam\\Scripts\\python tools\\grab_frames.py [name] [N]
Saves out/samples/<time>_<name>/: f00.png ... (gray), median.png (gray), color.png (the last frame), info.json.
The camera must be free (close the program). The first SETTLE_S seconds are skipped: the exposure settles after the start.
NOT here: the locked exposure / white balance / focus of the TZ (the camera runs as it is, as in Calibration now).
"""
import datetime
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from patterndxf import profile                                                # noqa: E402
from patterndxf.camera.source import CameraSource, VideoMode                  # noqa: E402

SETTLE_S, TIMEOUT_S = 3.0, 40.0


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "sample"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    prof, note = profile.load()
    if prof is None:
        sys.exit(f"No profile ({note})")
    app = QApplication(sys.argv)
    cam = CameraSource(name=prof.camera_name, vid_pid=prof.camera_vid_pid, video=VideoMode(*prof.video))
    state = {"t0": time.perf_counter(), "frames": [], "seen": 0, "color": None, "error": ""}

    def on_frame():
        if cam.latest is None or cam.frames == state["seen"]:
            return
        state["seen"] = cam.frames
        if time.perf_counter() - state["t0"] < SETTLE_S:
            return
        bgr = cv2.cvtColor(cam.latest, cv2.COLOR_BGRA2BGR)
        state["frames"].append(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
        state["color"] = bgr
        if len(state["frames"]) >= n:
            app.quit()

    def on_failed(text):
        state["error"] = text
        app.quit()

    cam.frame.connect(on_frame)
    cam.failed.connect(on_failed)
    cam.open()
    QTimer.singleShot(int(TIMEOUT_S * 1000), app.quit)
    app.exec()
    cam.close()
    if state["error"] or len(state["frames"]) < n:
        sys.exit(f"failed: {state['error'] or f'only {len(state['frames'])} frames'}")
    out = ROOT / "out" / "samples" / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{name}"
    out.mkdir(parents=True, exist_ok=True)
    for i, g in enumerate(state["frames"]):
        cv2.imwrite(str(out / f"f{i:02d}.png"), g)
    cv2.imwrite(str(out / "median.png"), np.median(np.stack(state["frames"]), axis=0).astype(np.uint8))
    cv2.imwrite(str(out / "color.png"), state["color"])
    h, w = state["color"].shape[:2]
    (out / "info.json").write_text(json.dumps({"camera": prof.camera_text, "size": [w, h], "frames": n,
                                               "time": f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"}, indent=1), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
