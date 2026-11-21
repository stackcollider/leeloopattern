"""The start of the new core: the splash checks, a failed check opens the window where it is put right (DECISIONS.md 2026-11-20).

    splash: 1 Checking settings         settings.json missing or damaged -> the defaults are written (a line in the log)
            2 Checking folders          the DXF or log folder cannot be written -> App settings (the red line at the folder)
            3 Checking camera profile   profile.json missing or damaged -> Set Camera (first start)
            4 Looking for the camera    the camera of the profile is not there -> "Retry / Set camera / Exit"
            5 Checking the stand        not accepted or no calibration.json -> Commissioning
      The splash is closed before the window (the camera question too); after Save the start begins again from
      check 1 with a new splash (everything is read from the files again). Cancel asks "Exit the program?".
      All passed -> the calibration (only its steps dialog: Capture by itself; green -> Measure, yellow -> Continue -> Measure,
      red -> Commissioning, Cancel or Close -> "Exit the program?", No -> the start again) -> Measure, the main window (maximized). Its gear -> Camera preview (the setup); closing it ends the program.
    Camera preview: Settings -> Set Camera (Save -> a new Camera preview, Cancel -> back)
                              -> App settings (a modal dialog over the preview, which keeps the camera)
                              -> Commissioning (a modal dialog over the preview, which releases the camera and takes it back).
                 Go to measurement -> Measure instead of the preview.
"""
import logging
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from patterndxf import appinfo, profile, startup
from patterndxf.camera.match import choose
from patterndxf.ui.splash import STEPS, SplashWindow, center_on, pick_screen

MIN_SPLASH_S = 2.5
ERROR_HOLD_S = 2.5
SETTINGS, FOLDERS, PROFILE, CAMERA, STAND = range(1, len(STEPS) + 1)      # the numbers of the checks (splash.STEPS)


def calibration_path():
    from patterndxf.measure import calibration_path as path
    return path()


class StartWorker(QThread):
    """The checks from the first on, until the first one that fails; the splash is told before each of them.
    state: the dict of the start ("settings", "profile"); the checks fill it (the window thread waits meanwhile)."""
    step = Signal(int, str)
    finished_all = Signal(int, str)                  # the failed check (0 = all passed) and why

    def __init__(self, state):
        super().__init__()
        self.state = state

    def run(self):
        failed, reason = 0, ""
        for n in range(1, len(STEPS) + 1):
            self.step.emit(n, STEPS[n - 1])
            try:
                reason = self.check(n)
            except Exception as err:                 # a check never kills the start: it fails with the reason
                traceback.print_exc()
                reason = f"{STEPS[n - 1]} failed: {type(err).__name__}: {err}"
            if reason:
                failed = n
                break
        self.finished_all.emit(failed, reason)

    def check(self, n):
        """'' if the check passed, else the plain reason."""
        state = self.state
        if n == SETTINGS:
            res = startup.read_settings()                            # the log is opened here, with its header
            state["settings"] = res.data["settings"]
            if state.pop("reset", None) is not None:
                logging.warning("--reset: deleted %s", state["reset_gone"] or "nothing (there were no files)")
            if not startup.settings_path().exists() or res.data["damaged"]:     # the defaults are written, no window
                startup.save_settings(state["settings"])
                logging.warning("settings.json %s: the defaults are written",
                                "damaged" if res.data["damaged"] else "missing")
        elif n == FOLDERS:
            res = startup.check_folders(state["settings"])
            if not res.ok:
                return res.text
        elif n == PROFILE:
            state["profile"], note = profile.load()
            if state["profile"] is None:
                return "Camera profile damaged: Set camera opens" if note else "No camera profile yet: Set camera opens"
        elif n == CAMERA:
            from patterndxf.camera import sample
            if sample.FOLDER is not None:                        # --sample: the snapshot is the camera
                logging.info("camera: --sample %s (no camera)", sample.FOLDER)
                return ""
            from patterndxf.camera.source import list_cameras
            p = state["profile"]
            camera, problem = choose(list_cameras(), p.camera_name, p.camera_vid_pid)
            if problem:
                return problem
            logging.info("camera: %s (profile %s)", camera.name, p.camera_text)
        elif n == STAND:
            p = state["profile"]
            if p.accepted is None:
                return "The stand is not accepted: Commissioning opens"
            if not calibration_path().exists():
                return "No calibration of the stand: Commissioning opens"
        return ""


def reset_files():
    """For the developer (--reset): deletes settings.json and profile.json, so the next start is a first start.
    Returns the paths that were deleted."""
    gone = []
    for path in (startup.settings_path(), profile.profile_path()):
        if path.exists():
            path.unlink()
            gone.append(str(path))
    return gone


def accept_stand(prof, dialog):
    """Accept stand of the Commissioning: the acceptance is kept in the profile."""
    prof.accepted = {"date": time.strftime("%Y-%m-%d %H:%M"),
                     "result": {key: res[1] for key, res in dialog.results.items() if res is not None}}
    try:
        profile.save(prof)
        logging.info("stand accepted: %s", prof.accepted)
    except OSError as err:
        prof.accepted = None
        logging.error("the acceptance could not be saved: %s", err)


def run(app, reset=False):
    """Runs the start; returns when the program ends. reset: the developer's --reset, the files are deleted first."""
    from patterndxf.ui import theme
    theme.apply(app)
    app.setQuitOnLastWindowClosed(False)                         # while the checks run there may be no window for a moment
    screen = pick_screen()                                       # one screen for everything
    state = {"settings": dict(startup.DEFAULT_SETTINGS), "profile": None}
    if reset:
        state["reset"], state["reset_gone"] = True, reset_files()      # logged by the first check (the log opens there)

    def show(window):
        window.resize(1500, 900)
        center_on(window, screen)
        window.show()
        state["window"] = window

    def open_preview(prof, problem=""):
        from patterndxf.ui.camera_preview_window import CameraPreviewWindow
        window = CameraPreviewWindow(prof, problem)
        window.open_set_camera.connect(lambda: open_set_camera(window, False))
        window.open_set_settings.connect(lambda: open_set_settings(window))
        window.open_commissioning.connect(lambda: open_commissioning(window))
        window.go_measure.connect(lambda: open_measure(window.profile, window))
        show(window)
        app.setQuitOnLastWindowClosed(True)                      # from here closing the last window ends the program
        window.start_camera()
        state["preview"] = window

    def run_dialog(preview, dialog):
        """A modal dialog over the preview (the preview stays where it is, locked meanwhile), in the middle of the same screen."""
        center_on(dialog, screen)
        return dialog.exec()

    def open_set_settings(preview):
        from patterndxf.ui.set_settings_window import SetSettingsWindow
        dialog = SetSettingsWindow(state["settings"])
        if run_dialog(preview, dialog) and dialog.new is not None:       # the preview keeps the camera meanwhile
            state["settings"] = dialog.new

    def open_commissioning(preview):
        from patterndxf.ui.commissioning_window import CommissioningDialog
        preview.stop_camera()                                    # the steps open the camera themselves; only one program can hold it
        snaps = Path(state["settings"]["log_dir"]) / "snaps"        # the snapshots of the steps go next to the log
        dialog = CommissioningDialog(preview.profile, snaps)
        if run_dialog(preview, dialog):                          # Accept stand: the acceptance is kept in the profile
            accept_stand(preview.profile, dialog)
            preview.update_status()
        preview.start_camera()

    def open_measure(prof, preview=None):
        """Measure, the main window: opened by the start or instead of the preview (which is closed). Its gear closes it and
        opens the preview; closing it otherwise ends the program."""
        from patterndxf.ui.measure_screen import MeasureScreen
        if preview is not None:
            preview.stop_camera()                                # the Measure screen opens the camera itself
        log_dir = Path(state["settings"]["log_dir"])
        window = MeasureScreen(prof, log_dir / "snaps", log_dir / "measurements")
        window.setup_requested.connect(lambda: measure_to_setup(window))
        state["measure"] = window
        window.showMaximized()                                   # shown before the preview closes: the program never has no window
        state["window"] = window
        app.setQuitOnLastWindowClosed(True)                      # closing Measure ends the program
        if preview is not None:
            preview.close()

    def measure_to_setup(window):
        """The gear of Measure: Measure closes (the camera is released), then the preview opens."""
        app.setQuitOnLastWindowClosed(False)                     # for a moment there is no window
        window.reject()
        open_preview(window.profile)

    def open_set_camera(preview, first_run):
        from patterndxf.ui.set_camera_window import SetCameraWindow
        current = None if preview is None else preview.profile
        if preview is not None:
            preview.stop_camera()                                # only one program can hold the camera
            preview.hide()
        window = SetCameraWindow(first_run, current)
        window.saved.connect(lambda prof: after_saved(preview, prof))
        window.cancelled.connect(lambda: back(preview))
        window.exit_requested.connect(app.quit)
        state["set_camera"] = window
        show(window)

    def after_saved(old_preview, prof):
        if old_preview is not None:
            old_preview.close()
        open_preview(prof)

    def back(preview):
        if preview is not None:
            preview.show()
            preview.start_camera()
            state["window"] = preview

    # ----- the checks of the start and the windows that put a failed one right
    def start_checks():
        """A new splash and all the checks from the first (also after a window of the start was saved)."""
        splash = SplashWindow(screen)
        splash.show()
        state["splash"], state["started"] = splash, time.time()
        worker = StartWorker(state)
        worker.step.connect(splash.set_step)
        worker.finished_all.connect(checked)
        state["worker"] = worker
        worker.start()

    def checked(failed, reason):
        splash = state["splash"]
        wait = max(0.0, MIN_SPLASH_S - (time.time() - state["started"]))
        if failed:
            state["reason"] = reason                             # the question of the camera check shows it
            splash.set_error(reason)
            logging.warning("start-up: %s", reason)
            wait = max(wait, ERROR_HOLD_S)

        def go():
            splash.close()                                       # every window of the start opens without the splash
            if failed == CAMERA:
                camera_question()
            elif failed:
                fix(failed)
            else:
                logging.info("start-up: all checks passed")
                from patterndxf.camera import sample
                if sample.FOLDER is not None:                    # --sample: no calibration at the start (a piece is on it)
                    open_measure(state["profile"])
                else:
                    start_calibration()
        QTimer.singleShot(int(wait * 1000), go)

    def start_calibration():
        """The calibration of every start (only its steps dialog is seen): Measure, Commissioning or the exit."""
        from patterndxf.ui.calibration_dialog import CalibrationDialog
        dialog = CalibrationDialog(state["profile"], Path(state["settings"]["log_dir"]) / "snaps", at_start=True, screen=screen)
        dialog.start_outcome.connect(lambda outcome: calibrated(dialog, outcome))
        state["calibration"] = dialog

    def calibrated(dialog, outcome):
        logging.info("start-up: calibration -> %s", outcome)
        dialog.done(0)                                           # the camera is released for the next window
        if outcome == "measure":
            open_measure(state["profile"])
        elif outcome == "commissioning":
            fix(STAND)
        else:
            ask = QMessageBox.question(None, appinfo.NAME, "The calibration did not pass. Exit the program?",
                                       QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ask == QMessageBox.Yes:
                logging.info("start-up: exit at the calibration")
                app.quit()
            else:
                start_checks()

    def ask_exit(failed):
        """Cancel in a window of the start: Yes ends the program, No opens the window again."""
        ask = QMessageBox.question(None, appinfo.NAME, f"{STEPS[failed - 1]} did not pass. Exit the program?",
                                   QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ask == QMessageBox.Yes:
            logging.info("start-up: exit at %s", STEPS[failed - 1])
            app.quit()
        else:
            fix(failed)

    def fix(failed):
        """The window that puts the failed check right (the splash is closed): Save -> the start again."""
        if failed in (SETTINGS, FOLDERS):                        # SETTINGS fails only on an exception
            from patterndxf.ui.set_settings_window import SetSettingsWindow
            dialog = SetSettingsWindow(state["settings"])
            center_on(dialog, screen)
            if dialog.exec() and dialog.new is not None:
                start_checks()
            else:
                ask_exit(failed)
        elif failed == PROFILE:
            set_camera(True)
        elif failed == STAND:
            from patterndxf.ui.commissioning_window import CommissioningDialog
            prof = state["profile"]
            dialog = CommissioningDialog(prof, Path(state["settings"]["log_dir"]) / "snaps")
            center_on(dialog, screen)
            if dialog.exec():
                accept_stand(prof, dialog)
                start_checks()
            else:
                ask_exit(failed)

    def camera_question():
        """The camera of the profile is not there: Retry / Set camera / Exit (the splash is closed)."""
        box = QMessageBox(QMessageBox.Warning, appinfo.NAME, f"{state.get('reason') or 'Camera not found'}.\n"
                          "Plug the camera in and press Retry, or choose another camera.")
        retry = box.addButton("Retry", QMessageBox.AcceptRole)
        choose_btn = box.addButton("Set camera", QMessageBox.ActionRole)
        box.addButton("Exit", QMessageBox.RejectRole)
        box.setDefaultButton(retry)
        box.exec()
        if box.clickedButton() is retry:
            start_checks()
        elif box.clickedButton() is choose_btn:
            set_camera(False)
        else:
            logging.info("start-up: exit, no camera")
            app.quit()

    def set_camera(first_run):
        """Set Camera of the start: Save or Cancel -> the start again (Cancel on the first start asks for the exit itself)."""
        from patterndxf.ui.set_camera_window import SetCameraWindow
        window = SetCameraWindow(first_run, None if first_run else state["profile"])
        window.saved.connect(lambda _prof: start_checks())
        window.cancelled.connect(start_checks)
        window.exit_requested.connect(app.quit)
        state["set_camera"] = window
        show(window)

    start_checks()
    return app.exec()
