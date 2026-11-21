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
