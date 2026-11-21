"""Which camera is the one of the profile. Pure Python: the list of the cameras comes from the caller.

The camera is known by its name and VID:PID (docs/DECISIONS.md 2026-11-14), not by the device path: the path of a camera
without a serial number holds the USB port, so it changes when the camera is plugged into another port.
"""
import re
from dataclasses import dataclass

_VID_PID = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")


@dataclass(frozen=True)
class CameraInfo:
    name: str
    device_id: str                 # the device path of Windows (opens the camera; may change with the port)

    @property
    def vid_pid(self):
        """'VID_32E4&PID_0298' or '' when the path has none."""
        found = _VID_PID.search(self.device_id)
        return f"VID_{found.group(1).upper()}&PID_{found.group(2).upper()}" if found else ""


def choose(cameras, name="", vid_pid=""):
    """(the camera, '') or (None, a plain line for the operator).

    name: the camera name of the profile (a part of it is enough); vid_pid: 'VID_32E4&PID_0298'. An empty value does not
    restrict. Other cameras of the computer (a laptop camera, a virtual one) are ignored."""
    cameras = list(cameras)
    if not name and not vid_pid:
        if not cameras:
            return None, "Camera not found: no camera in the system"
        if len(cameras) > 1:
            return None, "Several cameras found and no profile to choose by"
        return cameras[0], ""
    vid_pid = vid_pid.upper()
    hits = [c for c in cameras if (not vid_pid or c.vid_pid == vid_pid) and (not name or name.casefold() in c.name.casefold())]
    if not hits:
        return None, f"Camera not found: {name or vid_pid}"
    if len(hits) > 1:
        return None, "Two cameras match the profile"
    return hits[0], ""
