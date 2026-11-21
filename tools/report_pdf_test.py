"""Report, test: the DXF pieces of a folder -> a PDF like the model sheet of AccuMark (2026-11-21). The report itself: patterndxf/report.py (since 1.19.0).

Run: .venv-cam\\Scripts\\python tools\\report_pdf_test.py [--folder <folder with DXF>] [--no-open]
     (default: the work folder of settings.json; the report: out/reports/<folder>_report_<YYYYMMDD_HHMMSS>.pdf, opened)
"""
import os
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from patterndxf import report, startup                                          # noqa: E402

OUT = ROOT / "out" / "reports"


def main():
    app = QGuiApplication(sys.argv)                       # the PDF painter needs the fonts of a Qt application
    if "--folder" in sys.argv:
        folder = Path(sys.argv[sys.argv.index("--folder") + 1]).resolve()
    else:
        settings, _note = startup.load_settings()
        folder = Path(settings["dxf_dir"]) / settings.get("work_folder", "")
    files = report.folder_files(folder)
    path = report.make("pdf", files, folder.name, OUT)
    print(f"{len(files)} pieces -> {path}")
    if "--no-open" not in sys.argv:
        os.startfile(str(path))
    app.quit()


if __name__ == "__main__":
    main()
