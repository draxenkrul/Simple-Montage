"""End-to-end: run the real FFmpeg on a generated clip and check the result."""

import subprocess

import pytest

from simple_montage.export import build_ffmpeg_args
from simple_montage.model import Timeline
from simple_montage.probe import probe
from simple_montage.tools import find_ffmpeg, find_ffprobe

FFMPEG, FFPROBE = find_ffmpeg(), find_ffprobe()
pytestmark = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not installed")


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "source.mp4"
    subprocess.run([
        FFMPEG, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], check=True)
    return str(path)


def test_cut_and_speed_change(source, tmp_path):
    info = probe(FFPROBE, source)
    tl = Timeline(info.duration, info.fps)
    tl.delete_range(1.0, 2.0)   # keep 0-1, 2-6
    tl.split(4.0)               # 0-1, 2-4, 4-6
    tl.set_speed(1, 2.0)        # 2-4 at 2x -> 1 s
    tl.set_speed(2, 0.5)        # 4-6 at 0.5x -> 4 s
    expected = 1 + 1 + 4

    out = tmp_path / "out.mp4"
    args = build_ffmpeg_args(FFMPEG, info, tl.segments, str(out), preset="ultrafast")
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]

    exported = probe(FFPROBE, str(out))
    assert exported.duration == pytest.approx(expected, abs=0.15)
    assert exported.has_audio
    frames = subprocess.run(
        [FFPROBE, "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert int(frames) == expected * 25


def _export(info, segments, out, mode):
    from simple_montage.export import build_ffmpeg_args
    args = build_ffmpeg_args(FFMPEG, info, segments, str(out), mode=mode, preset="ultrafast")
    result = subprocess.run(args, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]


def _streams(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True).stdout
    return sorted(out.split())


def test_export_part_of_timeline(source, tmp_path):
    from simple_montage.export import ExportMode
    info = probe(FFPROBE, source)
    tl = Timeline(info.duration, info.fps)
    out = tmp_path / "part.mp4"
    _export(info, tl.segments_between(2.0, 5.0), out, ExportMode.VIDEO_AND_AUDIO)
    assert probe(FFPROBE, str(out)).duration == pytest.approx(3.0, abs=0.1)
    assert _streams(out) == ["audio", "video"]


@pytest.mark.parametrize("ext", [".mp3", ".wav", ".m4a"])
def test_export_sound_only(source, tmp_path, ext):
    from simple_montage.export import ExportMode
    info = probe(FFPROBE, source)
    tl = Timeline(info.duration, info.fps)
    out = tmp_path / ("sound" + ext)
    _export(info, tl.segments_between(1.0, 4.0), out, ExportMode.AUDIO_ONLY)
    assert _streams(out) == ["audio"]
    dur = float(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    assert dur == pytest.approx(3.0, abs=0.1)


def test_export_video_without_sound(source, tmp_path):
    from simple_montage.export import ExportMode
    info = probe(FFPROBE, source)
    tl = Timeline(info.duration, info.fps)
    out = tmp_path / "silent.mp4"
    _export(info, tl.segments, out, ExportMode.VIDEO_ONLY)
    assert _streams(out) == ["video"]


@pytest.fixture(scope="module")
def quadrants(tmp_path_factory):
    """4 s clip: red top-left, blue top-right, green bottom-left, yellow bottom-right."""
    path = tmp_path_factory.mktemp("media") / "quadrants.mp4"
    colors = ["red", "blue", "green", "yellow"]
    cmd = [FFMPEG, "-v", "error", "-y"]
    for c in colors:
        cmd += ["-f", "lavfi", "-i", f"color={c}:s=160x120:r=25:d=4"]
    cmd += ["-f", "lavfi", "-i", "sine=duration=4",
            "-filter_complex", "[0][1]hstack[t];[2][3]hstack[b];[t][b]vstack[v]",
            "-map", "[v]", "-map", "4:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True)
    return str(path)


def _average_rgb(path, t):
    raw = subprocess.run([FFMPEG, "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return tuple(raw[:3])


def test_zoom_shows_the_chosen_part_of_the_frame(quadrants, tmp_path):
    from simple_montage.export import ExportMode
    info = probe(FFPROBE, quadrants)
    tl = Timeline(info.duration, info.fps)
    for t in (1, 2, 3):
        tl.split(t)
    tl.set_zoom(1, 2.0, 0.0, 0.0)   # top-left -> red
    tl.set_zoom(2, 2.0, 1.0, 1.0)   # bottom-right -> yellow
    tl.set_zoom(3, 2.0, 1.0, 0.0)   # top-right -> blue
    out = tmp_path / "zoom.mp4"
    _export(info, tl.segments, out, ExportMode.VIDEO_AND_AUDIO)

    exported = probe(FFPROBE, str(out))
    assert (exported.width, exported.height) == (320, 240)
    assert exported.duration == pytest.approx(4.0, abs=0.1)

    def dominant(rgb):
        r, g, b = rgb
        if r > 200 and g > 200 and b < 80:
            return "yellow"
        return {0: "red", 1: "green", 2: "blue"}[max(range(3), key=lambda i: rgb[i])]

    mixed = _average_rgb(out, 0.5)          # unzoomed: all four colours averaged
    assert max(mixed) - min(mixed) < 140
    assert dominant(_average_rgb(out, 1.5)) == "red"
    assert dominant(_average_rgb(out, 2.5)) == "yellow"
    assert dominant(_average_rgb(out, 3.5)) == "blue"
