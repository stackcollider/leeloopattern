"""Name, version and build year of LeeLooPattern: one place for the window, the splash, the records.

The version is the text of the VERSION file next to the package. The build year is read from build_info.json (written
by the build, {"built": "YYYY-MM-DD"}); running from the sources it is the current year.
"""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "LeeLooPattern"
TAGLINE = "Pattern outline to DXF"
COMPANY = "StackCollider"


def version():
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def build_year():
    try:
        return int(json.loads((ROOT / "build_info.json").read_text(encoding="utf-8"))["built"][:4])
    except (OSError, ValueError, KeyError, TypeError):
        return date.today().year


def copyright_line():
    return f"© {build_year()} {COMPANY}"
