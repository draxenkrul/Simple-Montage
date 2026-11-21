"""Locating the external binaries the app depends on (FFmpeg, ffprobe, libmpv).

Search order:
1. The ``bin`` folder next to the app (project root in development, or the
   folder of the packaged ``.exe``). This is how the app ships on Windows.
2. The system ``PATH``.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def bin_dirs() -> list[Path]:
    """Folders that may hold the bundled binaries, in search order.

    Packaged app: ``bin`` next to ``SimpleMontage.exe`` (and inside PyInstaller's
    ``_internal`` folder as a fallback). Development: ``bin`` in the project root.
    """
    if getattr(sys, "frozen", False):
        dirs = [Path(sys.executable).resolve().parent / "bin"]
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass) / "bin")
        return dirs
    return [Path(__file__).resolve().parents[2] / "bin"]


def find_executable(name: str) -> str | None:
    """Find ``name`` (e.g. ``"ffmpeg"``) in the bundled ``bin`` folder, then on PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    for folder in bin_dirs():
        if (folder / exe).is_file():
            return str(folder / exe)
    return shutil.which(name)


def find_ffmpeg() -> str | None:
    return find_executable("ffmpeg")


def find_ffprobe() -> str | None:
    return find_executable("ffprobe")


def prepare_mpv_library() -> None:
    """Make a bundled ``libmpv-2.dll`` loadable before ``import mpv``.

    python-mpv looks for the DLL on ``PATH`` on Windows, so the ``bin`` folder
    is put in front of it.
    """
    for folder in reversed(bin_dirs()):  # first entry ends up first on PATH
        if folder.is_dir():
            os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(folder))
