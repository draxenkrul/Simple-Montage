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


def test_light_filter_in_graph():
    seg = Segment(0, 10, brightness=0.1, contrast=1.2)
    assert ",eq=brightness=0.1:contrast=1.2:saturation=1:gamma=1[v0]" in build_filter_graph([seg], info())
    assert "eq=" not in build_filter_graph([Segment(0, 10)], info())


def test_overlay_inputs_and_graph():
    from simple_montage.export import build_export
    from simple_montage.model import Overlay
    ov = Overlay("pip.mp4", 8.0, 640, 360, True, start=3.0, in_point=1.0, length=4.0,
                 scale=0.25, pos_x=1.0, pos_y=0.0, sound=True)
    segs = [Segment(0, 5), Segment(5, 10, speed=2.0)]
    inputs, graph = build_export(segs, info(), overlays=[ov])
    # overlay main 3-7: 3-5 in segment 0, 5-7 in segment 1 (file 1-3 and 3-5)
    assert inputs == [["-ss", "1", "-t", "2", "-i", "pip.mp4"], ["-ss", "3", "-t", "2", "-i", "pip.mp4"]]
    assert "[1:v:0]setpts=(PTS-STARTPTS)/1+3/TB,scale=480:-2:flags=lanczos[ov0_0]" in graph
    assert "[2:v:0]setpts=(PTS-STARTPTS)/2+0/TB" in graph
    assert "enable='between(t,0,1)'" in graph            # 2 s at 2x -> first second of segment 1
    assert "overlay=x=(main_w-overlay_w)*1:y=(main_h-overlay_h)*0" in graph
    assert "adelay=3000:all=1" in graph and "amix=inputs=2:duration=first:normalize=0[a0]" in graph


def test_overlay_args_put_inputs_after_main():
    from simple_montage.model import Overlay
    ov = Overlay("pip.mp4", 8.0, 640, 360, False, start=0.0, length=2.0)
    args = build_ffmpeg_args("ffmpeg", info(), [Segment(0, 10)], "out.mp4", overlays=[ov])
    i = args.index("-i")
    assert args[i + 1] == "in.mp4" and args[i + 2: i + 8] == ["-ss", "0", "-t", "2", "-i", "pip.mp4"]
    assert "amix" not in args[args.index("-filter_complex") + 1]


def test_combined_clips_use_their_own_inputs_and_local_times():
    from simple_montage.export import build_export
    a = MediaInfo("a.mp4", 10.0, 25.0, "25/1", 1920, 1080, True)
    b = MediaInfo("b.mp4", 4.0, 25.0, "25/1", 640, 480, False)
    # one segment crossing the clip boundary (8-12) is split into 8-10 (a) and 0-2 (b)
    inputs, graph = build_export([Segment(8, 12)], [a, b])
    assert inputs == [["-i", "b.mp4"]]
    assert "[0:v:0]trim=start=7.98,setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease" in graph
    assert "[1:v:0]trim=end=1.98,setpts=PTS-STARTPTS,scale=1920:1080" in graph and "pad=1920:1080" in graph
    assert "anullsrc=r=48000:cl=stereo,atrim=duration=2[a1]" in graph     # b has no sound
    assert "concat=n=2:v=1:a=1" in graph


def test_same_size_clips_are_not_rescaled():
    a = MediaInfo("a.mp4", 10.0, 25.0, "25/1", 1920, 1080, True)
    b = MediaInfo("b.mp4", 4.0, 25.0, "25/1", 1920, 1080, True)
    assert "pad=" not in build_filter_graph([Segment(0, 14)], [a, b])
