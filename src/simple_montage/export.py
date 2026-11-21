"""Turning the edit (a list of segments) into an FFmpeg command.

Each kept segment is trimmed out of the source, re-timed to its speed and then
all segments are concatenated:

    [0:v]trim=start=S:end=E,setpts=(PTS-STARTPTS)/SPEED[v0];
    [0:a]atrim=start=S:end=E,asetpts=PTS-STARTPTS,atempo=SPEED[a0];
    ...
    [v0][a0][v1][a1]concat=n=2:v=1:a=1[cv][ca];
    [cv]fps=FPS,format=yuv420p[vout]

Pure functions only: running the process is the GUI's job.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from pathlib import Path

from simple_montage.model import Segment
from simple_montage.probe import MediaInfo


def _num(x: float) -> str:
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


def atempo_chain(speed: float) -> list[float]:
    """Split a speed factor into atempo steps within 0.5..2.0.

    Newer FFmpeg accepts a wider range per filter, but chaining keeps older
    builds working and sounds the same.
    """
    factors: list[float] = []
    remaining = speed
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    if abs(remaining - 1.0) > 1e-9 or not factors:
        factors.append(remaining)
    return [f for f in factors if abs(f - 1.0) > 1e-9]


def boundary(t: float, fps: float) -> float:
    """Convert a cut time (on a frame start) to a safe trim timestamp.

    Trimming exactly at a frame's timestamp is fragile with floating point, so
    cuts are placed half a frame before the frame they start at.
    """
    frame = round(t * fps)
    return max(0.0, (frame - 0.5) / fps)


def _trim_args(seg: Segment, info: MediaInfo) -> str:
    args = []
    start = boundary(seg.start, info.fps)
    if start > 0:
        args.append(f"start={_num(start)}")
    if seg.end < info.duration - 1e-6:  # last segment: keep everything to the end
        args.append(f"end={_num(boundary(seg.end, info.fps))}")
    return ":".join(args)


class ExportMode(str, Enum):
    VIDEO_AND_AUDIO = "video+audio"
    VIDEO_ONLY = "video"
    AUDIO_ONLY = "audio"


AUDIO_FORMATS = {  # file extension -> FFmpeg audio encoder arguments
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
}


def build_filter_graph(
    segments: Sequence[Segment],
    info: MediaInfo,
    mode: ExportMode = ExportMode.VIDEO_AND_AUDIO,
) -> str:
    """Build the ``-filter_complex`` string.

    Outputs ``[vout]`` when the mode includes video and ``[aout]`` when it
    includes audio (and the source has audio).
    """
    if not segments:
        raise ValueError("Nothing to export: every part of the chosen range is cut out.")
    want_video = mode != ExportMode.AUDIO_ONLY
    want_audio = mode != ExportMode.VIDEO_ONLY and info.has_audio
    if not want_video and not want_audio:
        raise ValueError("This video has no sound to export.")

    chains: list[str] = []
    concat_inputs: list[str] = []
    for i, seg in enumerate(segments):
        trim = _trim_args(seg, info)
        if want_video:
            trim_v = f"trim={trim}," if trim else ""
            vpts = "PTS-STARTPTS" if seg.speed == 1 else f"(PTS-STARTPTS)/{_num(seg.speed)}"
            chains.append(f"[0:v:0]{trim_v}setpts={vpts}[v{i}]")
            concat_inputs.append(f"[v{i}]")
        if want_audio:
            trim_a = f"atrim={trim}," if trim else ""
            tempo = "".join(f",atempo={_num(f)}" for f in atempo_chain(seg.speed))
            chains.append(f"[0:a:0]{trim_a}asetpts=PTS-STARTPTS{tempo}[a{i}]")
            concat_inputs.append(f"[a{i}]")

    outputs = ("[cv]" if want_video else "") + ("[aout]" if want_audio else "")
    chains.append(
        f"{''.join(concat_inputs)}concat=n={len(segments)}"
        f":v={int(want_video)}:a={int(want_audio)}{outputs}"
    )
    if want_video:
        chains.append(f"[cv]fps={info.fps_text},format=yuv420p[vout]")
    return ";".join(chains)


def build_ffmpeg_args(
    ffmpeg: str,
    info: MediaInfo,
    segments: Sequence[Segment],
    output_path: str,
    *,
    mode: ExportMode = ExportMode.VIDEO_AND_AUDIO,
    crf: int = 18,
    preset: str = "medium",
) -> list[str]:
    """Full FFmpeg command line with machine-readable progress.

    Video exports are H.264 (+ AAC sound) in MP4. Sound-only exports pick the
    format from the file extension: .mp3, .wav or .m4a.
    """
    args = [
        ffmpeg, "-hide_banner", "-y",
        "-i", info.path,
        "-filter_complex", build_filter_graph(segments, info, mode),
    ]
    if mode == ExportMode.AUDIO_ONLY:
        ext = Path(output_path).suffix.lower()
        args += ["-map", "[aout]", "-vn", *AUDIO_FORMATS.get(ext, AUDIO_FORMATS[".m4a"])]
    else:
        args += ["-map", "[vout]"]
        if mode == ExportMode.VIDEO_AND_AUDIO and info.has_audio:
            args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
        args += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-movflags", "+faststart"]
    args += ["-progress", "pipe:1", "-nostats", output_path]
    return args


def parse_progress_line(line: str) -> float | None:
    """Return the output position in seconds from a ``-progress`` line, if any."""
    key, _, value = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms"):  # both are microseconds in FFmpeg
        try:
            return int(value) / 1_000_000
        except ValueError:
            return None
    return None
