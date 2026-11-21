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
