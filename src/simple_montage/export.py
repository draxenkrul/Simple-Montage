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


def build_filter_graph(segments: Sequence[Segment], info: MediaInfo) -> str:
    """Build the ``-filter_complex`` string. Outputs ``[vout]`` (and ``[aout]``)."""
    if not segments:
        raise ValueError("Nothing to export: every part of the video was cut.")

    chains: list[str] = []
    concat_inputs: list[str] = []
    for i, seg in enumerate(segments):
        trim = _trim_args(seg, info)
        trim_v = f"trim={trim}," if trim else ""
        trim_a = f"atrim={trim}," if trim else ""

        vpts = "PTS-STARTPTS" if seg.speed == 1 else f"(PTS-STARTPTS)/{_num(seg.speed)}"
        chains.append(f"[0:v:0]{trim_v}setpts={vpts}[v{i}]")
        concat_inputs.append(f"[v{i}]")

        if info.has_audio:
            tempo = "".join(f",atempo={_num(f)}" for f in atempo_chain(seg.speed))
            chains.append(f"[0:a:0]{trim_a}asetpts=PTS-STARTPTS{tempo}[a{i}]")
            concat_inputs.append(f"[a{i}]")

    n = len(segments)
    a = 1 if info.has_audio else 0
    outputs = "[cv][aout]" if info.has_audio else "[cv]"
    chains.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a={a}{outputs}")
    chains.append(f"[cv]fps={info.fps_text},format=yuv420p[vout]")
    return ";".join(chains)


def build_ffmpeg_args(
    ffmpeg: str,
    info: MediaInfo,
    segments: Sequence[Segment],
    output_path: str,
    *,
    crf: int = 18,
    preset: str = "medium",
) -> list[str]:
    """Full FFmpeg command line (H.264 + AAC in MP4) with machine-readable progress."""
    args = [
        ffmpeg, "-hide_banner", "-y",
        "-i", info.path,
        "-filter_complex", build_filter_graph(segments, info),
        "-map", "[vout]",
    ]
    if info.has_audio:
        args += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    args += [
        "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        output_path,
    ]
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
