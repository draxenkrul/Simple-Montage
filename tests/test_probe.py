import pytest

from simple_montage.probe import ProbeError, parse_ffprobe_json
from simple_montage.timecode import format_speed, format_time


def test_parse_ffprobe_json():
    data = {
        "streams": [
            {"codec_type": "video", "avg_frame_rate": "30000/1001", "width": 1280, "height": 720},
            {"codec_type": "audio"},
        ],
        "format": {"duration": "12.5"},
    }
    info = parse_ffprobe_json("a.mp4", data)
    assert info.duration == 12.5
    assert info.fps == pytest.approx(29.97, abs=0.01)
    assert info.fps_text == "30000/1001"
    assert info.has_audio and (info.width, info.height) == (1280, 720)


def test_parse_falls_back_when_avg_rate_unknown():
    data = {"streams": [{"codec_type": "video", "avg_frame_rate": "0/0", "r_frame_rate": "25/1"}],
            "format": {"duration": "3"}}
    info = parse_ffprobe_json("a.mp4", data)
    assert info.fps == 25 and not info.has_audio


def test_parse_requires_video():
    with pytest.raises(ProbeError):
        parse_ffprobe_json("a.mp3", {"streams": [{"codec_type": "audio"}], "format": {"duration": "3"}})


def test_format_time():
    assert format_time(0) == "0:00:00.000"
    assert format_time(3723.4567) == "1:02:03.457"
    assert format_time(59.9996, millis=False) == "0:01:00"
    assert format_speed(2.0) == "2×" and format_speed(0.25) == "0.25×"


def test_parse_sample_aspect_ratio():
    base = {"codec_type": "video", "avg_frame_rate": "25/1"}
    fmt = {"duration": "3"}
    assert parse_ffprobe_json("a", {"streams": [dict(base, sample_aspect_ratio="4:3")], "format": fmt}).sar == "4/3"
    assert parse_ffprobe_json("a", {"streams": [dict(base, sample_aspect_ratio="0:1")], "format": fmt}).sar == "1/1"
    assert parse_ffprobe_json("a", {"streams": [base], "format": fmt}).sar == "1/1"



def test_duration_prefers_the_video_stream():
    data = {"streams": [{"codec_type": "video", "avg_frame_rate": "25/1", "duration": "3.000"},
                        {"codec_type": "audio", "duration": "3.021"}],
            "format": {"duration": "3.021"}}
    assert parse_ffprobe_json("a.mp4", data).duration == 3.0
