# PyInstaller build recipe. Run via packaging/build_windows.ps1.
# Produces dist/SimpleMontage/SimpleMontage.exe (one folder, starts fast).
from pathlib import Path

root = Path(SPECPATH).parent

a = Analysis(
    [str(root / "packaging" / "launch.py")],
    pathex=[str(root / "src")],
    datas=[(str(root / "src" / "simple_montage" / "resources"), "simple_montage/resources")],
    excludes=[
        "tkinter", "unittest", "pytest", "PIL",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
        "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtMultimedia",
        "PySide6.QtPdf", "PySide6.QtNetwork",
    ],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SimpleMontage",
    console=False,
    icon=str(root / "packaging" / "icon.ico"),
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="SimpleMontage", upx=False)
