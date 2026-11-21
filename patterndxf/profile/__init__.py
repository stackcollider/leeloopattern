"""profile.json: the profile of the stand (docs/DECISIONS.md 2026-11-14).

The camera (name and VID:PID, not the device path), the video mode, the photo size, later the sheet format, the tolerances in mm
and the result of the commissioning. It is written by Set Camera / Set Settings (the adjuster), never by hand and never made
automatically. Rules: a `version` field, an atomic write, a damaged file counts as "no profile", unknown keys are dropped.
Lives next to settings.json in %APPDATA%\\LeeLooPattern\\.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from patterndxf import appinfo

VERSION = 1


@dataclass
class Profile:
    camera_name: str
    camera_vid_pid: str
    video: tuple                       # (width, height, fps) of the live video
    photo: tuple                       # (width, height) of the photo
    accepted: Optional[dict] = None    # None = the stand is not accepted; {"date": ..., "result": ...} after the commissioning

    @property
    def camera_text(self):
        return f"{self.camera_name} ({self.camera_vid_pid})" if self.camera_vid_pid else self.camera_name


def profile_path():
    base = os.environ.get("APPDATA")
    return Path(base) / "LeeLooPattern" / "profile.json" if base else appinfo.ROOT / "out" / "profile.json"


def to_dict(p):
    return {"version": VERSION,
            "camera": {"name": p.camera_name, "vid_pid": p.camera_vid_pid},
            "video": {"width": p.video[0], "height": p.video[1], "fps": p.video[2]},
            "photo": {"width": p.photo[0], "height": p.photo[1]},
            "accepted": p.accepted}


def _positive(d, *keys):
    values = tuple(d[k] for k in keys)
    if not all(isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in values):
        raise ValueError(f"{'/'.join(keys)} must be positive whole numbers")
    return values


def from_dict(d):
    """A Profile from the content of the file; ValueError says what is wrong."""
    try:
        if d["version"] != VERSION:
            raise ValueError(f"unknown profile version {d['version']}")
        name, vid_pid = d["camera"]["name"], d["camera"].get("vid_pid", "")
        if not isinstance(name, str) or not name or not isinstance(vid_pid, str):
            raise ValueError("camera name is missing")
        accepted = d.get("accepted")
        if accepted is not None and not isinstance(accepted, dict):
            raise ValueError("accepted must be empty or a record")
        return Profile(name, vid_pid, _positive(d["video"], "width", "height", "fps"), _positive(d["photo"], "width", "height"), accepted)
    except (KeyError, TypeError) as err:
        raise ValueError(f"profile is incomplete ({err!r})") from err


def load():
    """(the Profile or None, a note). No file: (None, ''). A damaged file: (None, why): it counts as no profile."""
    path = profile_path()
    if not path.exists():
        return None, ""
    try:
        return from_dict(json.loads(path.read_text(encoding="utf-8"))), ""
    except (OSError, ValueError) as err:
        return None, f"profile file damaged, treated as not set ({err})"


def save(p):
    """Writes the profile atomically (a temporary file, then a rename), so a power cut never leaves half a file."""
    path = profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(to_dict(p), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
