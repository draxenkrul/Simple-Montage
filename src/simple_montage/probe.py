"""Reading basic facts about a video file with ffprobe."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str
    duration: float
    fps: float
    fps_text: str  # exact rational, e.g. "30000/1001", used for FFmpeg filters
    width: int
    height: int
    has_audio: bool
    sar: str = "1"  # pixel (sample) aspect ratio, e.g. "1" or "4/3"


def _parse_rate(text: str | None) -> Fraction | None:
    if not text:
        return None
    try:
        rate = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def parse_ffprobe_json(path: str, data: dict) -> MediaInfo:
    """Build :class:`MediaInfo` from ``ffprobe -print_format json`` output."""
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("The file has no video stream.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    rate = (
        _parse_rate(video.get("avg_frame_rate"))
        or _parse_rate(video.get("r_frame_rate"))
        or Fraction(30)
    )
    duration = data.get("format", {}).get("duration") or video.get("duration")
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        raise ProbeError("Could not read the video duration.") from None

    sar = Fraction(1)
    num, _, den = str(video.get("sample_aspect_ratio") or "").partition(":")
    if num.isdigit() and den.isdigit() and int(num) > 0 and int(den) > 0:
        sar = Fraction(int(num), int(den))

    return MediaInfo(
        path=path,
        duration=duration,
        fps=float(rate),
        fps_text=f"{rate.numerator}/{rate.denominator}",
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        has_audio=has_audio,
        sar=f"{sar.numerator}/{sar.denominator}",
    )


def probe(ffprobe: str, path: str) -> MediaInfo:
    """Run ffprobe on ``path``."""
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):  # don't flash a console on Windows
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", **kwargs)
    if result.returncode != 0:
        raise ProbeError(result.stderr.strip() or "ffprobe failed.")
    return parse_ffprobe_json(path, json.loads(result.stdout))
