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


def app_dir() -> Path:
    """Folder the app runs from: the frozen exe's folder, or the project root."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def bin_dir() -> Path:
    return app_dir() / "bin"


def find_executable(name: str) -> str | None:
    """Find ``name`` (e.g. ``"ffmpeg"``) in ``bin/`` first, then on PATH."""
    exe = name + (".exe" if os.name == "nt" else "")
    bundled = bin_dir() / exe
    if bundled.is_file():
        return str(bundled)
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
    folder = str(bin_dir())
    if os.path.isdir(folder):
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(folder)
