import pytest

from simple_montage.model import MAX_SPEED, MIN_SPEED, Segment, Timeline


def spans(tl):
    return [(round(s.start, 4), round(s.end, 4), s.speed) for s in tl.segments]


def test_reset_keeps_whole_video():
    tl = Timeline(10.0, 25)
    assert spans(tl) == [(0.0, 10.0, 1.0)]
    assert tl.output_duration == pytest.approx(10.0)


def test_split_snaps_to_frame():
    tl = Timeline(10.0, 25)
    assert tl.split(4.013)  # nearest frame start at 25 fps is 4.0
    assert spans(tl) == [(0.0, 4.0, 1.0), (4.0, 10.0, 1.0)]


def test_split_at_edges_or_in_gap_does_nothing():
    tl = Timeline(10.0, 25)
    assert not tl.split(0.0)
    assert not tl.split(10.0)
    tl.delete_range(2, 4)
    assert not tl.split(3.0)


def test_delete_segment():
    tl = Timeline(10.0, 25)
    tl.split(4.0)
    assert tl.delete(0)
    assert spans(tl) == [(4.0, 10.0, 1.0)]
    assert tl.index_at(1.0) is None
    assert tl.next_index_after(1.0) == 0


def test_delete_and_keep_range():
    tl = Timeline(10.0, 25)
    assert tl.delete_range(6, 3)  # order does not matter
    assert spans(tl) == [(0.0, 3.0, 1.0), (6.0, 10.0, 1.0)]
    assert tl.keep_range(1, 8)
    assert spans(tl) == [(1.0, 3.0, 1.0), (6.0, 8.0, 1.0)]


def test_speed_changes_output_duration_and_mapping():
    tl = Timeline(10.0, 25)
    tl.split(4.0)
    tl.set_speed(0, 2.0)   # 4 s -> 2 s
    tl.set_speed(1, 0.5)   # 6 s -> 12 s
    assert tl.output_duration == pytest.approx(14.0)
    assert tl.source_to_output(2.0) == pytest.approx(1.0)
    assert tl.source_to_output(5.0) == pytest.approx(4.0)


def test_speed_is_clamped():
    tl = Timeline(10.0, 25)
    tl.set_speed(0, 100)
    assert tl.segments[0].speed == MAX_SPEED
    tl.set_speed(0, 0)
    assert tl.segments[0].speed == MIN_SPEED


def test_undo_redo():
    tl = Timeline(10.0, 25)
    tl.split(5.0)
    tl.delete(1)
    assert spans(tl) == [(0.0, 5.0, 1.0)]
    assert tl.undo()
    assert spans(tl) == [(0.0, 5.0, 1.0), (5.0, 10.0, 1.0)]
    assert tl.undo()
    assert spans(tl) == [(0.0, 10.0, 1.0)]
    assert not tl.undo()
    assert tl.redo() and tl.redo()
    assert spans(tl) == [(0.0, 5.0, 1.0)]


def test_repeated_speed_edits_merge_into_one_undo_step():
    tl = Timeline(10.0, 25)
    for s in (1.1, 1.2, 1.3):
        tl.set_speed(0, s)
    tl.undo()
    assert tl.segments[0].speed == 1.0


def test_cut_points():
    tl = Timeline(10.0, 25)
    tl.delete_range(2, 4)
    assert tl.cut_points() == [0.0, 2.0, 4.0, 10.0]


def test_segment_contains_is_half_open():
    seg = Segment(1.0, 2.0)
    assert seg.contains(1.0) and not seg.contains(2.0)


def test_segments_between_clips_without_changing_the_edit():
    tl = Timeline(20.0, 25)
    tl.delete_range(7, 8)      # kept: 0-7, 8-20
    tl.split(12)
    tl.set_speed(2, 2.0)       # 12-20 at 2x
    part = tl.segments_between(5, 14)
    assert [(s.start, s.end, s.speed) for s in part] == [(5.0, 7.0, 1.0), (8.0, 12.0, 1.0), (12.0, 14.0, 2.0)]
    assert sum(s.output_duration for s in part) == pytest.approx(7.0)
    assert spans(tl) == [(0.0, 7.0, 1.0), (8.0, 12.0, 1.0), (12.0, 20.0, 2.0)]  # untouched


def test_segments_between_cut_out_range_is_empty():
    tl = Timeline(20.0, 25)
    tl.delete_range(5, 10)
    assert tl.segments_between(6, 9) == []


def test_zoom_is_kept_by_split_delete_and_ranges():
    tl = Timeline(10.0, 25)
    tl.set_zoom(0, 2.0, 0.0, 1.0)
    tl.split(5.0)
    tl.delete_range(7, 8)
    for seg in tl.segments + tl.segments_between(2, 9):
        assert (seg.zoom, seg.pan_x, seg.pan_y) == (2.0, 0.0, 1.0)


def test_zoom_is_clamped_and_merges_into_one_undo_step():
    from simple_montage.model import MAX_ZOOM
    tl = Timeline(10.0, 25)
    tl.set_zoom(0, 50, 7, -3)
    assert (tl.segments[0].zoom, tl.segments[0].pan_x, tl.segments[0].pan_y) == (MAX_ZOOM, 1.0, 0.0)
    tl.set_zoom(0, 2.0, 0.3)   # pan_y unchanged when not given
    assert tl.segments[0].pan_y == 0.0
    tl.undo()
    assert tl.segments[0].zoom == 1.0 and tl.segments[0].pan_x == 0.5


def test_light_set_clamp_merge_and_split():
    tl = Timeline(10.0, 25)
    assert not tl.segments[0].has_light
    tl.set_light(0, brightness=0.1)
    tl.set_light(0, brightness=0.2, contrast=9)      # merged, clamped
    seg = tl.segments[0]
    assert (seg.brightness, seg.contrast) == (0.2, 2.0) and seg.has_light
    tl.split(5.0)
    assert all(s.brightness == 0.2 for s in tl.segments)
    tl.undo(); tl.undo()
    assert not tl.segments[0].has_light


def test_reset_light():
    tl = Timeline(10.0, 25)
    tl.set_light(0, saturation=0.0, gamma=1.5)
    assert tl.reset_light(0) and not tl.segments[0].has_light


def _overlay(**kw):
    from simple_montage.model import Overlay
    return Overlay("pip.mp4", file_duration=6.0, width=640, height=360, has_audio=True, **kw)


def test_overlay_is_clamped_to_main_and_file():
    tl = Timeline(10.0, 25)
    i = tl.add_overlay(_overlay(start=8.0, length=100))
    assert tl.overlays[i].length == pytest.approx(2.0)          # main ends at 10
    tl.update_overlay(i, start=1.0, in_point=5.0, length=100)
    assert tl.overlays[i].length == pytest.approx(1.0)          # file has 1 s left
    tl.update_overlay(i, scale=5, pos_x=-1)
    assert (tl.overlays[i].scale, tl.overlays[i].pos_x) == (1.0, 0.0)


def test_overlay_at_and_file_time():
    tl = Timeline(10.0, 25)
    tl.add_overlay(_overlay(start=2.0, length=3.0, in_point=1.0))
    assert tl.overlay_at(1.0) is None and tl.overlay_at(2.0) == 0 and tl.overlay_at(5.0) is None
    assert tl.overlays[0].file_time(3.5) == pytest.approx(2.5)


def test_overlay_undo_redo_and_reset():
    tl = Timeline(10.0, 25)
    tl.add_overlay(_overlay(start=1.0, length=2.0))
    tl.update_overlay(0, start=2.0)
    tl.update_overlay(0, start=3.0)          # merged with the previous move
    tl.undo()
    assert tl.overlays[0].start == 1.0
    tl.undo()
    assert tl.overlays == []
    tl.redo()
    assert len(tl.overlays) == 1
    tl.remove_overlay(0)
    assert tl.overlays == []
    tl.reset(10.0, 25)
    assert tl.overlays == [] and not tl.can_undo


def test_overlay_without_audio_cannot_have_sound():
    from simple_montage.model import Overlay
    tl = Timeline(10.0, 25)
    tl.add_overlay(Overlay("a.mp4", 5.0, 100, 100, has_audio=False, sound=True, length=2))
    assert tl.overlays[0].sound is False
