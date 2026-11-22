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
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from simple_montage.model import Clip, Overlay, Segment
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


def _cut_args(file_start: float, file_end: float, media: MediaInfo) -> list[str]:
    """Input options that read only ``[file_start, file_end)`` of a file.

    Seeking on the input keeps FFmpeg from decoding (and buffering) parts it
    does not need, which matters once pieces are reordered.
    """
    args = []
    start = boundary(file_start, media.fps)
    if start > 0:
        args += ["-ss", _num(start)]
    if file_end < media.duration - 1e-6:  # up to the end of the file: no limit needed
        args += ["-t", _num(boundary(file_end, media.fps) - start)]
    return args


class ExportMode(str, Enum):
    VIDEO_AND_AUDIO = "video+audio"
    VIDEO_ONLY = "video"
    AUDIO_ONLY = "audio"


AUDIO_FORMATS = {  # file extension -> FFmpeg audio encoder arguments
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
}


def zoom_filter(seg: Segment, info: MediaInfo) -> str:
    """Crop to the zoomed area and scale back to the source size ("" if not zoomed)."""
    if seg.zoom <= 1.0 + 1e-9:
        return ""
    z = _num(seg.zoom)
    crop = _crop_zoom(seg.zoom, seg.pan_x, seg.pan_y)
    size = f"{info.width}:{info.height}" if info.width and info.height else f"iw*{z}:ih*{z}"
    # keep the source pixel aspect so zoomed and normal segments can be joined
    return f",{crop},scale={size}:flags=lanczos,setsar={info.sar}"


def light_filter(seg: Segment) -> str:
    """FFmpeg ``eq`` filter for the segment's lighting ("" when unchanged)."""
    if not seg.has_light:
        return ""
    return (f",eq=brightness={_num(seg.brightness)}:contrast={_num(seg.contrast)}"
            f":saturation={_num(seg.saturation)}:gamma={_num(seg.gamma)}")


@dataclass
class _OverlayUse:
    """One overlay shown during part of one segment."""

    overlay: Overlay
    file_start: float    # where to start reading the overlay file
    file_length: float   # how much of it to read (source seconds)
    out_start: float     # when it appears, in the segment's output time
    out_end: float
    input_index: int = -1


def _overlay_uses(segments: Sequence[Segment], overlays: Sequence[Overlay],
                  fps: float) -> list[list[_OverlayUse]]:
    """For each segment, the overlays that are visible during it."""
    min_len = 0.5 / fps
    result: list[list[_OverlayUse]] = []
    for seg in segments:
        uses = []
        for ov in overlays:
            a, b = max(seg.start, ov.start), min(seg.end, ov.end)
            if b - a < min_len:
                continue
            uses.append(_OverlayUse(
                ov, file_start=ov.file_time(a), file_length=b - a,
                out_start=(a - seg.start) / seg.speed, out_end=(b - seg.start) / seg.speed,
            ))
        result.append(uses)
    return result


def _as_clips(sources) -> list[Clip]:
    """Accept one MediaInfo, MediaInfos (whole files) or Clips."""
    if isinstance(sources, (MediaInfo, Clip)):
        sources = [sources]
    return [s if isinstance(s, Clip) else Clip(s) for s in sources]


def _split_at_clips(segments: Sequence[Segment], clips: list[Clip]) -> list[tuple[int, Segment]]:
    """Pair each segment with its clip index, splitting any that cross clips."""
    bounds, t = [], 0.0
    for clip in clips:
        bounds.append((t, t + clip.length))
        t += clip.length
    result = []
    for seg in segments:
        for k, (a, b) in enumerate(bounds):
            start, end = max(seg.start, a), min(seg.end, b)
            if end - start > 1e-6:
                result.append((k, replace(seg, start=start, end=end)))
    return result


def _fit_filter(out: MediaInfo | Clip) -> str:
    """Letterbox a clip to the output size (used when combined clips differ)."""
    w, h = out.width, out.height
    return (f",scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")


def _crop_zoom(zoom: float, pan_x: float, pan_y: float) -> str:
    z = _num(zoom)
    return f"crop=w=iw/{z}:h=ih/{z}:x=(iw-iw/{z})*{_num(pan_x)}:y=(ih-ih/{z})*{_num(pan_y)}"


def build_export(
    segments: Sequence[Segment],
    sources,
    mode: ExportMode = ExportMode.VIDEO_AND_AUDIO,
    overlays: Sequence[Overlay] = (),
) -> tuple[list[list[str]], str]:
    """Build the FFmpeg inputs and the ``-filter_complex`` string.

    ``sources`` are the timeline's clips in order (Clips, or MediaInfos for
    whole files, or a single MediaInfo); segment times are on the combined
    timeline. Every piece of a segment and every overlay shown during it
    gets its own input, pre-cut with ``-ss``/``-t``. The first clip sets the
    output size and frame rate; clips of another size are letterboxed to it.
    The graph outputs ``[vout]`` when the mode includes video and ``[aout]``
    when it includes audio.
    """
    clips = _as_clips(sources)
    first = clips[0]
    if not segments:
        raise ValueError("Nothing to export: every part of the chosen range is cut out.")
    want_video = mode != ExportMode.AUDIO_ONLY
    want_audio = mode != ExportMode.VIDEO_ONLY and any(c.has_audio for c in clips)
    if not want_video and not want_audio:
        raise ValueError("This video has no sound to export.")

    offsets = [sum(c.length for c in clips[:k]) for k in range(len(clips))]
    mixed_sizes = len({(c.width, c.height, c.sar) for c in clips}) > 1
    pieces = _split_at_clips(segments, clips)
    uses_per_piece = _overlay_uses([seg for _k, seg in pieces], overlays, first.fps)

    inputs: list[list[str]] = []
    chains: list[str] = []
    concat_inputs: list[str] = []
    for i, ((k, seg), uses) in enumerate(zip(pieces, uses_per_piece)):
        clip = clips[k]
        file_start = seg.start - offsets[k] + clip.in_point
        file_end = seg.end - offsets[k] + clip.in_point
        src = len(inputs)
        inputs.append([*_cut_args(file_start, file_end, clip.media), "-i", clip.path])
        for use in uses:
            use.input_index = len(inputs)
            inputs.append(["-ss", _num(use.file_start), "-t", _num(use.file_length), "-i", use.overlay.path])
        tempo = "".join(f",atempo={_num(f)}" for f in atempo_chain(seg.speed))
        vpts = "PTS-STARTPTS" if seg.speed == 1 else f"(PTS-STARTPTS)/{_num(seg.speed)}"

        if want_video:
            fit = _fit_filter(first) if mixed_sizes else ""
            label = f"v{i}" if not uses else f"v{i}_0"
            chains.append(f"[{src}:v:0]setpts={vpts}{zoom_filter(seg, clip.media)}{fit}"
                          f"{light_filter(seg)}[{label}]")
            for n_use, use in enumerate(uses):
                ov, n = use.overlay, use.input_index
                width = max(2, int(round(first.width * ov.scale / 2)) * 2) if first.width else 2
                t0, t1 = _num(use.out_start), _num(use.out_end)
                crop = _crop_zoom(ov.zoom, ov.pan_x, ov.pan_y) + "," if ov.zoom > 1 + 1e-9 else ""
                chains.append(
                    f"[{n}:v:0]setpts=(PTS-STARTPTS)/{_num(seg.speed)}+{t0}/TB,"
                    f"{crop}scale={width}:-2:flags=lanczos[ov{i}_{n_use}]"
                )
                out = f"v{i}" if n_use == len(uses) - 1 else f"v{i}_{n_use + 1}"
                chains.append(
                    f"[{label}][ov{i}_{n_use}]overlay=x=(main_w-overlay_w)*{_num(ov.pos_x)}"
                    f":y=(main_h-overlay_h)*{_num(ov.pos_y)}:eof_action=pass"
                    f":enable='between(t,{t0},{t1})'[{out}]"
                )
                label = out
            concat_inputs.append(f"[v{i}]")

        if want_audio:
            sounding = [u for u in uses if u.overlay.sound and u.overlay.has_audio]
            label = f"a{i}" if not sounding else f"a{i}_main"
            if clip.has_audio:
                # lock the sound to the picture's length: files often have slightly longer
                # audio, which would otherwise leave a gap when this piece is not the last
                length = _num(seg.output_duration)
                chains.append(f"[{src}:a:0]asetpts=PTS-STARTPTS{tempo},"
                              f"atrim=duration={length},apad=whole_dur={length}[{label}]")
            else:  # this clip has no sound: silence of the right length
                chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={_num(seg.output_duration)}[{label}]")
            if sounding:
                mix = [f"[{label}]"]
                for n_use, use in enumerate(sounding):
                    delay = int(round(use.out_start * 1000))
                    chains.append(f"[{use.input_index}:a:0]asetpts=PTS-STARTPTS{tempo},"
                                  f"adelay={delay}:all=1[oa{i}_{n_use}]")
                    mix.append(f"[oa{i}_{n_use}]")
                chains.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:normalize=0[a{i}]")
            concat_inputs.append(f"[a{i}]")

    outputs = ("[cv]" if want_video else "") + ("[aout]" if want_audio else "")
    chains.append(
        f"{''.join(concat_inputs)}concat=n={len(pieces)}"
        f":v={int(want_video)}:a={int(want_audio)}{outputs}"
    )
    if want_video:
        chains.append(f"[cv]fps={first.fps_text},format=yuv420p[vout]")
    return inputs, ";".join(chains)


def build_filter_graph(
    segments: Sequence[Segment],
    info,
    mode: ExportMode = ExportMode.VIDEO_AND_AUDIO,
    overlays: Sequence[Overlay] = (),
) -> str:
    """Just the ``-filter_complex`` string (see :func:`build_export`)."""
    return build_export(segments, info, mode, overlays)[1]


def build_ffmpeg_args(
    ffmpeg: str,
    info,
    segments: Sequence[Segment],
    output_path: str,
    *,
    mode: ExportMode = ExportMode.VIDEO_AND_AUDIO,
    overlays: Sequence[Overlay] = (),
    crf: int = 18,
    preset: str = "medium",
) -> list[str]:
    """Full FFmpeg command line with machine-readable progress.

    Video exports are H.264 (+ AAC sound) in MP4. Sound-only exports pick the
    format from the file extension: .mp3, .wav or .m4a.
    """
    clips = _as_clips(info)
    inputs, graph = build_export(segments, clips, mode, overlays)
    args = [ffmpeg, "-hide_banner", "-y"]
    for extra in inputs:
        args += extra
    args += ["-filter_complex", graph]
    if mode == ExportMode.AUDIO_ONLY:
        ext = Path(output_path).suffix.lower()
        args += ["-map", "[aout]", "-vn", *AUDIO_FORMATS.get(ext, AUDIO_FORMATS[".m4a"])]
    else:
        args += ["-map", "[vout]"]
        if mode == ExportMode.VIDEO_AND_AUDIO and any(c.has_audio for c in clips):
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
