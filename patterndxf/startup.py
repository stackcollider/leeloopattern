"""The settings of LeeLooPattern and the real steps of its start-up (no interface here); each step returns a StepResult.

1. read_settings: the settings file (defaults if there is none or it is damaged), the log file and its header.
2. check_folders: the DXF and log folders can be written.
A failed step opens the window where it is put right (patterndxf/flow.py).
"""
import json
import logging
import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from patterndxf import appinfo

ROOT = appinfo.ROOT
FROZEN = bool(getattr(sys, "frozen", False))          # the installed program (PyInstaller), not the sources


def _default_folder(frozen_name, source_path):
    """DXF files: the installed program keeps them in Documents/LeeLooPattern (the program folder is not for data); from the sources: out/."""
    return str(Path.home() / "Documents" / "LeeLooPattern" / frozen_name) if FROZEN else str(source_path)


def _default_log_folder():
    """The logs live next to settings.json and profile.json: %APPDATA%/LeeLooPattern/logs (out/logs if there is no APPDATA)."""
    base = os.environ.get("APPDATA")
    return str(Path(base) / "LeeLooPattern" / "logs") if base else str(ROOT / "out" / "logs")


DEFAULT_SETTINGS = {
    "dxf_dir": _default_folder("DXF", ROOT / "out" / "dxf"),            # Output DXF Folder
    "log_dir": _default_log_folder(),                                    # Log Folder (the journal, the measurement logs, camera profiles)
    "simplify_tolerance_mm": 0.1,                     # Measure, Simplify: how far the DXF may leave the outline, mm
    "simplify_corner_deg": 25.0,                      # Measure, Simplify: a turn above this is a corner, deg
    "simplify_straight_mm": 0.3,                      # Measure, Simplify: an edge this close to a line is one straight segment, mm
    "work_folder": "",                                # Measure, Work folder: where the pieces go, relative to dxf_dir (2026-11-21)
}


@dataclass
class StepResult:
    ok: bool
    text: str                 # one plain line for the operator (shown on error)
    data: dict = field(default_factory=dict)


def settings_path():
    base = os.environ.get("APPDATA")
    return Path(base) / "LeeLooPattern" / "settings.json" if base else ROOT / "out" / "settings.json"


def load_settings():
    """(settings with the defaults filled in, a note if the file was damaged)."""
    settings, note = dict(DEFAULT_SETTINGS), ""
    path = settings_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            settings.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        except (OSError, ValueError) as err:
            note = f"settings file damaged, defaults used ({err})"
    return settings, note


def save_settings(settings):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: settings[k] for k in DEFAULT_SETTINGS if k in settings}, indent=2, ensure_ascii=False), encoding="utf-8")


def log_file(settings):
    return Path(settings["log_dir"]) / "leeloopattern.log"


def read_settings():
    """Settings (defaults filled in) and the log. A damaged file gives the defaults and a warning, not an error."""
    settings, note = load_settings()
    damaged = bool(note)
    try:
        log_file(settings).parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=str(log_file(settings)), level=logging.INFO, encoding="utf-8",
                            format="%(asctime)s %(levelname)s %(message)s", force=True)
    except OSError:
        logging.basicConfig(level=logging.INFO, force=True)          # the log folder is not usable: keep running
        note = note or "log folder not available"
    logging.getLogger("ezdxf").setLevel(logging.WARNING)            # not 15 lines "creating ACAD_... dictionary" on every Save
    logging.info("%s %s started on %s, Windows %s", appinfo.NAME, appinfo.version(), platform.node(), platform.version())
    if note:
        logging.warning(note)
    logging.info("settings: %s", settings)
    return StepResult(True, note or "settings read", {"settings": settings, "warning": note, "damaged": damaged})


def folder_problem(folder):
    """'' if the folder can be written (it is created if needed), else a short reason."""
    if not str(folder).strip():
        return "no folder chosen"
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)
        probe = Path(folder) / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return ""
    except OSError as err:
        return err.strerror or "cannot write"


def free_gb(folder):
    try:
        return shutil.disk_usage(folder).free / 1e9
    except OSError:
        return None


def check_folders(settings):
    problems = [label for key, label in (("dxf_dir", "Output DXF Folder"), ("log_dir", "Log Folder"))
                if folder_problem(settings[key])]
    logging.info("folders: problems=%s", problems)
    if problems:
        return StepResult(False, "Folder not available: " + ", ".join(problems))
    return StepResult(True, "folders ok")

