# PyInstaller recipe of the installed LeeLooPattern (one folder: LeeLooPattern.exe + _internal). Built by installer/build.ps1.
import os

root = os.path.abspath(os.path.join(SPECPATH, ".."))
build_info = os.environ.get("LEELOO_BUILD_INFO") or os.path.join(root, "build", "build_info.json")

a = Analysis(
    [os.path.join(root, "leeloopattern.py")],
    pathex=[root],
    datas=[(os.path.join(root, "VERSION"), "."), (build_info, "."),
           (os.path.join(root, "patterndxf", "ui", "icons"), os.path.join("patterndxf", "ui", "icons")),    # theme.ICON_PATH
           (os.path.join(root, "patterndxf", "sheet", "sheets"), os.path.join("patterndxf", "sheet", "sheets"))],   # the work sheets
    excludes=["tkinter", "matplotlib", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LeeLooPattern", console=False,
          icon=os.path.join(root, "patterndxf", "ui", "icons", "app.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="LeeLooPattern")
