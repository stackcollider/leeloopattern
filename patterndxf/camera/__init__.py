"""The camera layer of LeeLooPattern: live frames and full-size photos through the Windows camera API (MediaCapture).

match.py   which camera is the one of the profile (name and VID:PID; no WinRT needed, can be tested anywhere)
source.py  CameraSource: opens it, gives live frames, video and photo modes, takes a photo (needs WinRT)

source.py is not imported here: the Microsoft Store Python gets "Access is denied" from the camera API and has no
winrt packages, so only the code that really needs the camera imports it (docs/DECISIONS.md 2026-11-14).
"""
