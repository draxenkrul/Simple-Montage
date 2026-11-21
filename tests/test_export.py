import pytest

from simple_montage.export import (
    atempo_chain,
    boundary,
    build_ffmpeg_args,
    build_filter_graph,
    parse_progress_line,
)
from simple_montage.model import Segment
from simple_montage.probe import MediaInfo


def info(has_audio=True, duration=10.0):
    return MediaInfo("in.mp4", duration, 25.0, "25/1", 1920, 1080, has_audio)


@pytest.mark.parametrize("speed, expected", [
    (1.0, []), (2.0, [2.0]), (4.0, [2.0, 2.0]), (0.5, [0.5]),
    (0.25, [0.5, 0.5]), (3.0, [2.0, 1.5]), (0.1, [0.5, 0.5, 0.5, 0.8]),
])
def test_atempo_chain(speed, expected):
    chain = atempo_chain(speed)
    assert chain == pytest.approx(expected)
    product = 1.0
    for f in chain:
        assert 0.5 <= f <= 2.0
        product *= f
    assert product == pytest.approx(speed)


def test_boundary_sits_half_a_frame_early():
    assert boundary(4.0, 25) == pytest.approx(3.98)
    assert boundary(0.0, 25) == 0.0


def test_filter_graph_with_audio():
    graph = build_filter_graph([Segment(0, 4, 2.0), Segment(6, 10, 1.0)], info())
    assert graph == (
        "[0:v:0]trim=end=3.98,setpts=(PTS-STARTPTS)/2[v0];"
        "[0:a:0]atrim=end=3.98,asetpts=PTS-STARTPTS,atempo=2[a0];"
        "[0:v:0]trim=start=5.98,setpts=PTS-STARTPTS[v1];"
        "[0:a:0]atrim=start=5.98,asetpts=PTS-STARTPTS[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[cv][aout];"
        "[cv]fps=25/1,format=yuv420p[vout]"
    )


def test_filter_graph_without_audio():
    graph = build_filter_graph([Segment(1, 3, 0.5)], info(has_audio=False))
    assert "[0:a" not in graph
    assert "concat=n=1:v=1:a=0[cv]" in graph


def test_empty_edit_raises():
    with pytest.raises(ValueError):
        build_filter_graph([], info())


def test_ffmpeg_args():
    args = build_ffmpeg_args("ffmpeg", info(), [Segment(0, 10)], "out.mp4")
    assert args[0] == "ffmpeg" and args[-1] == "out.mp4"
    assert args[args.index("-i") + 1] == "in.mp4"
    assert "[aout]" in args and "libx264" in args


def test_parse_progress_line():
    assert parse_progress_line("out_time_us=1500000") == 1.5
    assert parse_progress_line("out_time_us=N/A") is None
    assert parse_progress_line("frame=12") is None


def test_filter_graph_audio_only():
    from simple_montage.export import ExportMode
    graph = build_filter_graph([Segment(5, 10, 1.0)], info(duration=20.0), ExportMode.AUDIO_ONLY)
    assert graph == "[0:a:0]atrim=start=4.98:end=9.98,asetpts=PTS-STARTPTS[a0];[a0]concat=n=1:v=0:a=1[aout]"


def test_filter_graph_video_only_has_no_audio():
    from simple_montage.export import ExportMode
    graph = build_filter_graph([Segment(5, 10)], info(), ExportMode.VIDEO_ONLY)
    assert "[0:a" not in graph and "[vout]" in graph


def test_audio_only_without_sound_raises():
    from simple_montage.export import ExportMode
    with pytest.raises(ValueError):
        build_filter_graph([Segment(0, 1)], info(has_audio=False), ExportMode.AUDIO_ONLY)


@pytest.mark.parametrize("ext, codec", [(".mp3", "libmp3lame"), (".wav", "pcm_s16le"), (".m4a", "aac")])
def test_audio_only_args_pick_codec_from_extension(ext, codec):
    from simple_montage.export import ExportMode
    args = build_ffmpeg_args("ffmpeg", info(), [Segment(0, 10)], "out" + ext, mode=ExportMode.AUDIO_ONLY)
    assert codec in args and "-vn" in args and "libx264" not in args and "[vout]" not in args


def test_zoom_filter():
    seg = Segment(0, 10, 1.0, zoom=2.0, pan_x=0.25, pan_y=1.0)
    graph = build_filter_graph([seg], info())
    assert ("setpts=PTS-STARTPTS,crop=w=iw/2:h=ih/2:x=(iw-iw/2)*0.25:y=(ih-ih/2)*1,"
            "scale=1920:1080:flags=lanczos,setsar=1[v0]") in graph


def test_no_zoom_filter_at_1x():
    assert "crop" not in build_filter_graph([Segment(0, 10)], info())
