"""LeeLooPattern: the entry point (the splash, Set Camera on the first start, Camera preview: patterndxf/flow.py).

Run: python leeloopattern.py               the program
     python leeloopattern.py --reset       (the developer) settings.json and profile.json are deleted first: a first start
     python leeloopattern.py --selftest [report file]    everything the program needs is there (to check an installed program)
     python leeloopattern.py --splash-demo only the splash: a click switches the state, a right click closes it
     python leeloopattern.py --sample <folder>   (the developer) a snapshot of tools/grab_frames.py instead of the camera:
                                                  no camera check, no calibration at the start, straight to Measure
Needs a Python with the winrt packages that is not the Microsoft Store one (requirements.txt).
"""
import os
import sys
import tempfile
from pathlib import Path

if sys.stdout is None:                        # the installed program has no console: a stray print must not kill it
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
from patterndxf.ui import theme                                          # noqa: E402
from patterndxf.ui.splash import SplashWindow, pick_screen               # noqa: E402


def splash_demo(app):
    """Only the splash: a left click switches between the normal and the failed state, a right click (or 3 minutes) closes it."""
    splash = SplashWindow(pick_screen())
    splash.show()
    state = {"error": False}
    splash.set_step(2, "Reading profile")

    def click(event):
        if event.button() == Qt.RightButton:
            app.quit()
            return
        state["error"] = not state["error"]
        if state["error"]:
            splash.set_error("Camera not found: opening the settings")
        else:
            splash.set_step(2, "Reading profile")

    splash.mousePressEvent = click
    QTimer.singleShot(180000, app.quit)
    return splash


def selftest(report_path):
    """Everything the program needs is there: writes the report and returns 0 if all fine."""
    import cv2
    import numpy as np
    lines, ok = [], True

    def check(name, fn):
        nonlocal ok
        try:
            lines.append(f"ok    {name}: {fn()}")
        except Exception as err:
            ok = False
            lines.append(f"FAIL  {name}: {type(err).__name__}: {err}")

    from patterndxf import appinfo, startup
    from patterndxf.calib import load_sheets
    from patterndxf.core import simplify
    from patterndxf.core.dxf_out import write_standard_dxf
    from patterndxf.sheet import vertical
    check("version", lambda: f"{appinfo.NAME} {appinfo.version()} (c) {appinfo.build_year()}, frozen={startup.FROZEN}")
    check("default folders", lambda: f"DXF {startup.DEFAULT_SETTINGS['dxf_dir']} | logs {startup.DEFAULT_SETTINGS['log_dir']}")
    check("opencv + aruco", lambda: f"{cv2.__version__}, markers on a blank frame: "
                                    f"{len(vertical.make_detector().detectMarkers(np.zeros((200, 300), np.uint8))[0])}")
    check("work sheets", lambda: ", ".join(s.name for s in load_sheets()))

    def contour():
        t = np.linspace(0, 2 * np.pi, 800, endpoint=False)
        circle = np.column_stack([50 + 40 * np.cos(t), 50 + 30 * np.sin(t)])
        return f"{len(simplify.simplify_contour(circle, 'straight'))} nodes of an ellipse"
    check("contour simplification (scipy)", contour)

    def dxf():
        path = os.path.join(tempfile.gettempdir(), "leeloopattern_selftest.dxf")
        write_standard_dxf(path, np.array([[0, 0], [10, 0], [10, 5], [0, 5]], float))
        import ezdxf
        return f"{len(list(ezdxf.readfile(path).modelspace()))} entity written and read back"
    check("DXF (ezdxf)", dxf)

    def camera():
        from patterndxf.camera.source import list_cameras
        return f"{len(list_cameras())} camera(s) (Windows camera API)"
    check("camera API (winrt)", camera)

    def windows():
        from patterndxf.ui import (calibration_dialog, camera_preview_window, commissioning_window, focus_window,  # noqa: F401
                                   measure_screen, set_camera_window, set_settings_window, vertical_window)
        return "every window module loads"
    check("windows", windows)
    lines.append("SELFTEST OK" if ok else "SELFTEST FAILED")
    Path(report_path).write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    return 0 if ok else 1


def main():
    app = QApplication(sys.argv)
    theme.set_app_icon(app)          # the icon of the windows and of the taskbar button (not the one of Python)
    if "--selftest" in sys.argv:
        i = sys.argv.index("--selftest")
        sys.exit(selftest(sys.argv[i + 1] if i + 1 < len(sys.argv) else os.path.join(tempfile.gettempdir(), "leeloopattern_selftest.txt")))
    if "--splash-demo" in sys.argv:
        demo = splash_demo(app)                                          # noqa: F841 (kept alive while the loop runs)
        sys.exit(app.exec())
    if "--sample" in sys.argv:
        i = sys.argv.index("--sample")
        if i + 1 >= len(sys.argv) or not Path(sys.argv[i + 1]).is_dir():
            print("--sample needs a folder made by tools/grab_frames.py")
            sys.exit(2)
        from patterndxf.camera import sample
        sample.FOLDER = Path(sys.argv[i + 1]).resolve()
    from patterndxf import flow
    sys.exit(flow.run(app, reset="--reset" in sys.argv))


if __name__ == "__main__":
    main()
